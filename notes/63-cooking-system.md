# CookingManager 烹饪系统深度剖析

> 第 63 篇：notes/62 合成台的**孪生对照**。同在 `cooking` 包、同 `BasePlayerManager`、同 `payItems`，但**执行模型截然不同**——合成台是时间锁①Lazy，烹饪是**纯即时事务（无 startTime / 无 Timer / 无 lazy）**。本篇用它**划清资源执行模型三分法的边界**（什么资源**不在**三模型内），并抓到一个**可利用的"无料白嫖"bug**，以及与 notes/62 static 串号 bug 的**正反对照**。

---

## 0. 为什么这一篇重要：定义三分法的"边界"

[[grasscutter-resource-execution-models]] 三分法（Lazy / 事件累计 / 主动轮询）覆盖**时间相关**资源。但一个好分类法也要能说清**什么不属于它**。

> **事前预测**：烹饪是"投料 → 立即出餐"，**无任何时间维度**（不是产出随时间累积，是一次性转换）。它应**落在三分法之外**——属**第 0 类：同步请求-响应事务**（与 notes/58 ShopSystem 买东西同族）。

读码证实：**预测命中**。烹饪零时间状态，纯事务。这反向**强化**了三分法的判据——"先看资源有没有时间维度，没有就不在三模型内"。

---

## 1. 烹饪系统全图

```
┌── Handler ─────────────────────────────────────────────────┐
│ PlayerCookReq      → 烹饪 (投料→即时出餐)  ★ 同步事务      │
│ PlayerCookArgsReq  → 拉烹饪参数 (空 Rsp)                    │
│ (CookDataNotify / CookRecipeDataNotify 推配方解锁)         │
└────────────────────────┬───────────────────────────────────┘
                         ↓ CookingManager (186 行 BasePlayerManager)
┌── 无任何时间状态 ──────────────────────────────────────────┐
│ static defaultUnlockedRecipies: Set<recipeId> (仅默认表)    │
│ Player.unlockedRecipies: Map<recipeId, proficiency> ★per玩家│
│   ——熟练度即配方解锁状态, 持久化进 Player                   │
└────────────────────────┬───────────────────────────────────┘
                         ↓ handlePlayerCookReq 一次完成
  payItems 扣料 → 按 QTE quality 选产物档 → 掷骰特色料理替换
  → addItem 即时入库 → 完美烹饪(q=3) proficiency+1 → Rsp
```

→ **186 行，零 startTime / 零 Timer / 零 onTick** —— 与 notes/62 合成台（靠 startTime 懒算）形成最干净的对照。

---

## 2. 第 0 类执行模型：同步请求-响应事务

| 模型 | 时间维度 | 代表 | 笔记 |
|---|---|---|---|
| **第 0 类 同步事务** | **无** | **Cooking / Shop 买卖** | **本篇** / notes/58 |
| ① Lazy 懒算 | 状态查询型 | Resin/Mail/Shop刷新/Expedition/Compound | notes/50/57/58/59/62 |
| ② 事件累计 | 离散事件型 | Energy | notes/60 |
| ③ 主动轮询 | 连续积分型 | Stamina | notes/61 |

→ **判据精炼**：研究新系统先问"**这个资源有没有时间维度？**"
> 无 → 第 0 类同步事务（一个 handler 内 payItems→产出→addItem 闭环）
> 有 → 再按时间性质分①②③

→ Cooking 与 Shop 买卖虽业务不同，**架构同构**：校验→`payItems` 原子扣→产出→`addItem`→Rsp，全在一个 handler 内同步完成，无持久化时间态。这解释了为何 grasscutter 大量"制造/交易"系统代码极短——它们都是第 0 类。

---

## 3. handlePlayerCookReq：一次出餐的完整事务

```java
var recipeData = GameData.getCookRecipeDataMap().get(recipeId);
if (recipeData == null) { sendPacket(RET_FAIL); return; }

int proficiency = player.getUnlockedRecipies().getOrDefault(recipeId, 0);

// ★ 扣料
boolean success = player.getInventory().payItems(recipeData.getInputVec(), count, ActionReason.Cook);
if (!success) {
    player.sendPacket(new PacketPlayerCookRsp(Retcode.RET_FAIL));
    // ★★★ BUG: 没有 return! 见 §5
}

// QTE 品质 → 产物档 (0=普通默认中档, 1/2/3 → index quality-1)
int qualityIndex = (quality == 0) ? 2 : quality - 1;
ItemParamData resultParam = recipeData.getQualityOutputVec().get(qualityIndex);

// 助理角色"拿手菜"替换
var bonusData = GameData.getCookBonusDataMap().get(avatar);
if (bonusData != null && recipeId == bonusData.getRecipeId()) {
    for (int i = 0; i < count; i++)
        if (rng.nextDouble() <= specialtyChance) specialtyCount++;   // 按星级 25/20/15%
}

// 普通 + 特色 分别 addItem (即时入库)
player.getInventory().addItem(new GameItem(resultItemData, resultParam.getCount() * normalCount));
if (specialtyCount > 0) player.getInventory().addItem(specialtyItemData ...);

// ★ 完美烹饪 (quality==3) 熟练度+1
if (quality == MANUAL_PERFECT_COOK_QUALITY)   // = 3
    player.getUnlockedRecipies().put(recipeId, Math.min(proficiency+1, maxProficiency));

player.sendPacket(new PacketPlayerCookRsp(cookResults, quality, count, recipeId, proficiency));
```

→ **全程同步**：进 handler → 出 Rsp，无任何留存时间态。

---

## 4. 还原真实机制的细节

### 4.1 QTE 品质 → 产物档

`quality`：0=未做 QTE（默认中档 index 2），1/2/3=普通/优秀/完美（index quality-1）。`getQualityOutputVec()` 三档产物（同食谱不同品质给不同成品）。

### 4.2 完美烹饪刷熟练度（手动完美）

`MANUAL_PERFECT_COOK_QUALITY = 3`：只有**手动**完美烹饪（quality==3）才 `proficiency+1`，封顶 `maxProficiency`。还原原神"手动完美做够次数 → 解锁自动完美"机制。`unlockedRecipies` 的 value 就是熟练度，key 在即解锁。

### 4.3 助理角色拿手菜（特色料理替换）

`CookBonusData[avatar]`：该角色专属食谱命中时，按成品星级掷骰把普通成品**替换**为该角色"特色料理"：

| 成品星级 | 特色替换概率 |
|---|---|
| 1★ | 25% |
| 2★ | 20% |
| 3★ | 15% |

→ 逐个 `count` 掷骰（`rng.nextDouble() <= chance`），`specialtyCount` 个替换为 `bonusData.getReplacementItemId()`，其余正常。完美还原"莫娜/班尼特等专属料理"。

---

## 5. 抓到的真 Bug：payItems 失败未 return → 无料白嫖

```java
boolean success = player.getInventory().payItems(recipeData.getInputVec(), count, ActionReason.Cook);
if (!success) {
    player.sendPacket(new PacketPlayerCookRsp(Retcode.RET_FAIL));
    // ❌ 缺 return;  —— 继续往下执行, 照常 addItem 发成品!
}
... // 后面无条件 addItem(cookResultNormal) 等
```

→ **可利用漏洞**：原料不足时 `payItems` 返回 false、发了 `RET_FAIL`，但**没有 `return`**，代码继续走到 `addItem` —— **没扣料却照发成品**。
→ 与 notes/62 合成台 `payItems` 失败**有** `return`（`RET_ITEM_COUNT_NOT_ENOUGH; return;`）形成直接对比 —— 同包孪生类，一个写对一个写错。
→ 影响：客户端发 `PlayerCookReq` 且原料为 0 → 服务端回 RET_FAIL（客户端可能不显示成品）但**背包实际已加成品**（addItem 入库 + 持久化）。属**经济漏洞**（无中生有食物 → 可卖摩拉/喂养）。
→ 修复一行：`if (!success) { sendPacket(RET_FAIL); return; }`。这类"忘记 return"是 grasscutter "功能优先"风格的典型副作用（接 notes/61/62 同主题）。

---

## 6. 正反对照：per-player vs static（接 notes/62 bug）

notes/62 合成台 bug：`unlocked` 是 **static**（全玩家串号）。
本篇烹饪**写对了**：

```java
private static Set<Integer> defaultUnlockedRecipies;     // static 只存"默认表"(只读, 正确)
// 实际解锁状态:
this.player.getUnlockedRecipies()  // Map<recipeId, proficiency> —— per-player, 持久化
```

→ `addDefaultUnlocked()`：登录时把"默认应解锁但玩家还没有的"合并进 **player** 的 `unlockedRecipies`，static 只当**只读模板**。
→ **正反教材**：同一 `cooking` 包，CookingManager 正确区分"静态只读模板"vs"玩家态"，CompoundManager 却把玩家态错放 static。研究私服代码要**逐类核查 static 字段语义**，不能因同包就假设一致。

---

## 7. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| **原料为 0 发 PlayerCookReq** | **✓ 有效！payItems 失败未 return → 白嫖成品（真 bug §5）** |
| 伪造 quality=3 刷熟练度 | ✓ 部分有效（quality 来自客户端，无 QTE 真实性校验）|
| 伪造 recipeId | ✗ recipeData==null → RET_FAIL+return |
| 伪造 assistAvatar 蹭特色料理 | ⚠ 仅当 `recipeId==bonusData.getRecipeId()` 才生效，作用有限 |
| 篡改产出数量 | ✗ 服务端按 recipeData.getQualityOutputVec 算 |

→ 烹饪反作弊**比合成台更弱**，且有 §5 真实经济漏洞 + quality 完全信任客户端（可无限刷熟练度）。典型 grasscutter 私服取舍，但 §5 是明确应修的 bug。

---

## 8. 关键收获

1. **CookingManager 186 行 = 第 0 类执行模型（同步请求-响应事务）**，零时间态
2. **三分法边界确立**：先问"资源有无时间维度"，无→第 0 类同步事务，有→①②③
3. **Cooking 与 Shop 买卖架构同构**：校验→payItems→产出→addItem→Rsp 单 handler 闭环
4. **预测命中**：事前预测落三分法之外（第 0 类），代码证实——分类法边界判据有效
5. **熟练度即解锁态**：`Player.unlockedRecipies: Map<recipeId, proficiency>`，key 在即解锁
6. **完美烹饪(quality==3)手动刷熟练度**：proficiency+1 封顶 maxProficiency（还原官服）
7. **QTE 品质→产物档**：quality 0=默认中档，1/2/3→index quality-1，三档成品
8. **助理角色拿手菜替换**：按星级 25/20/15% 掷骰，replacementItemId 替换（还原专属料理）
9. **★ 真 Bug：payItems 失败未 return → 无料白嫖成品**（经济漏洞，一行可修）
10. **与 notes/62 合成台正反对照**：合成台 payItems 失败有 return，烹饪忘了 return
11. **per-player vs static 正反教材**：Cooking 正确（static 仅只读模板 + player 态），Compound 错误（static 串号）
12. **方法论**：同包不代表同质，须逐类核查 static 字段语义 + return 完整性
13. **ActionReason.Cook 入账**（notes/38 经济审计）
14. **quality 完全信任客户端**：可伪造完美无限刷熟练度（弱反作弊）
15. **第 0 类解释代码极短**：grasscutter 制造/交易系统都是单 handler 同步事务

---

## 9. 一句话总结

> **CookingManager (186 行 BasePlayerManager) = 纯即时同步事务 —— 单 handler 内 payItems 扣料 → 按 QTE quality 选产物档 → 助理角色拿手菜按星级 25/20/15% 掷骰替换 → addItem 即时入库 → 完美烹饪(q=3) proficiency+1，零 startTime/Timer/lazy；熟练度即解锁态存 Player.unlockedRecipies Map.**
>
> **方法论意义: 本篇为 [[grasscutter-resource-execution-models]] 三分法划清边界——确立"第 0 类同步请求-响应事务"（无时间维度的制造/交易，与 Shop 买卖同构），精炼研究判据为"先问资源有无时间维度"；并抓到 payItems 失败未 return 的可利用无料白嫖经济 bug，与 notes/62 合成台构成 return 完整性 + per-player/static 的正反双对照——同包孪生类不可假设同质，须逐类核查.**

---

**前置笔记**：
- notes/62 CookingCompound 合成台 - 孪生对照（时间锁①Lazy vs 本篇即时事务；payItems 有 return vs 缺 return；player 态 vs static 串号）
- notes/61 StaminaManager - 建立资源执行模型三分法（本篇划其边界）
- notes/58 ShopSystem - 第 0 类同步事务同构（买卖 = payItems→产出）
- notes/38 Inventory - payItems 原子扣料 + ActionReason.Cook 经济审计

**关联文件**：
- `CookingManager.java`(186) - 同步烹饪事务 + 配方解锁/熟练度
- `Player.getUnlockedRecipies()` - Map<recipeId, proficiency> per-player 持久化
- `CookRecipeData`/`CookBonusData`(excel) - 食谱(inputVec/qualityOutputVec/maxProficiency) / 助理拿手菜
- Handler：`HandlerPlayerCookReq` / `HandlerPlayerCookArgsReq`
- Bug 位点：`CookingManager.java:89-92`（payItems 失败缺 return）

**研究的源代码**: CookingManager 186 行全文 + 与 notes/62 合成台正反对照 + 三分法边界推导。
