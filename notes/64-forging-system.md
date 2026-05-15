# ForgingManager 锻造系统深度剖析

> 第 64 篇：notes/62 合成台、notes/63 烹饪之后，制造三件套收官。本篇是 [[grasscutter-resource-execution-models]] 三分法的**第三次预测性验证**（预测①Lazy，命中），并交叉确认两条横切线索：① **滑动 startTime 懒队列惯用法**（与 notes/62 同款，跨系统再现）；② **payItems 失败缺 return 经济 bug 第 3 次出现**——升格为**系统性代码异味**。

---

## 0. 为什么这一篇重要：三条线索的交汇点

锻造（矿石→武器突破素材/摩拉/角色经验书等）单独看平平，但放进已建立的研究框架它是**三条线索的交汇验证**：

> **事前预测**：锻造是"投料 → 等 N 秒/件 → 取件"的时间锁制造，**状态查询型**资源 → 应是**模型①Lazy**，公式应与 notes/62 合成台 `(now-startTime)/costTime` 同族。

读码：**预测三连命中**。且发现锻造与合成台是"**并行多队列 vs 串行单队列**"的同模型不同形态对照，又一次出现 notes/62 的滑动 startTime 技巧，又一次出现 notes/63 的 payItems 缺 return bug。

---

## 1. 锻造系统全图

```
┌── Handler ─────────────────────────────────────────────────┐
│ ForgeStartReq            → 投料开锻 (建 ActiveForgeData)    │
│ ForgeGetQueueDataReq     → 拉队列状态                       │
│ ForgeQueueManipulateReq  → RECEIVE_OUTPUT 取件 / STOP 取消  │
│ (ForgeDataNotify/FormulaDataNotify 推图纸解锁)             │
└────────────────────────┬───────────────────────────────────┘
                         ↓ ForgingManager (303 行 BasePlayerManager)
┌── 并行多队列 (按冒险等级开放 1~4) ─────────────────────────┐
│ AR>=15→4  AR>=10→3  AR>=5→2  else→1                         │
│ Player.activeForges: List<ActiveForgeData> ★ List 非 Map    │
│ ActiveForgeData{forgeId,avatarId,count,startTime,forgeTime, │
│                 lastUnfinishedCount, changed} @Entity       │
└────────────────────────┬───────────────────────────────────┘
                         ↓ 状态全靠懒算 (零 Timer)
  getFinishedCount(now) = (now - startTime) / forgeTime  ★①Lazy
                         ↓ 仅"红点通知"走 onTick
  Player.onTick (Player.java:1282) → sendPlayerForgingUpdate
    → updateChanged(now) 脏标记 → 变化才推 ForgeQueueDataNotify
```

→ **303 + 88 行**。状态懒算，但比 notes/62 多一层"onTick 仅通知"——与 notes/59 Expedition **同位同模式**（Player.onTick 内）。

---

## 2. 第三次预测性验证：ActiveForgeData（①Lazy 同族公式）

```java
@Entity
public class ActiveForgeData {
    private int forgeId, avatarId, count, startTime, forgeTime;
    private int lastUnfinishedCount; private boolean changed;

    public int getFinishedCount(int currentTime) {                 // ★ 纯函数懒算
        int finishedCount = (currentTime - this.startTime) / this.forgeTime;
        return Math.min(finishedCount, this.count);
    }
    public int getUnfinishedCount(int currentTime) { return count - getFinishedCount(currentTime); }
    public int getTotalFinishTimestamp() { return startTime + forgeTime * count; }
    public int getNextFinishTimestamp(int now) {
        return (now >= getTotalFinishTimestamp()) ? getTotalFinishTimestamp()
             : (getFinishedCount(now) * forgeTime + forgeTime + startTime);
    }
}
```

→ 公式 `(now - startTime) / forgeTime` —— 与 notes/62 合成台 `(now-startTime)/costTime` **逐字符同族**，notes/59 Expedition `now-startTime>=hourTime*3600` 同家族。
→ **三分法第三次预测命中**（notes/62 ①、notes/63 第 0 类、本篇 ①）。判据"状态查询型→①Lazy"稳定可靠，已成研究前预判工具。

---

## 3. 并行多队列 vs 合成台串行单队列（同模型不同形态）

| 维度 | 合成台 Compound (notes/62) | 锻造 Forge (本篇) |
|---|---|---|
| 三分法 | ①Lazy | ①Lazy |
| 容器 | `Map<compoundId, ActiveCookCompoundData>` | `List<ActiveForgeData>`（queueId = index+1）|
| 并发形态 | **每配方一条**串行队列 | **AR 决定 1~4 条**并行队列，任意配方占一格 |
| 队列上限 | 配方自带 queueSize | 冒险等级 determineNumberOfQueues |
| 取件粒度 | 按 groupId 展开整组 | 按 queueId 单队列 |

→ 同是①Lazy，**容器选型由并发形态决定**：合成台"每配方独立串行"用 `Map<key>`；锻造"有限格位任意配方"用 `List`（格位即索引）。
→ 这补充了三分法的工程细节：**模型①下还有"串行 Map / 并行 List"两种队列形态**，由业务并发语义决定。

---

## 4. 滑动 startTime 懒队列技巧（notes/62 跨系统再现）

`obtainItems` 取走已完成、保留未完成进度：

```java
int finished = forge.getFinishedCount(currentTime);
int unfinished = forge.getUnfinishedCount(currentTime);
// 发成品
player.getInventory().addItem(new GameItem(resultItemData, data.getResultItemCount() * finished));
player.getBattlePassManager().triggerMission(WatcherTriggerType.TRIGGER_DO_FORGE, 0, finished);  // 战令(notes/22/41)

if (unfinished > 0) {
    ActiveForgeData remaining = new ActiveForgeData();
    remaining.setCount(unfinished);
    remaining.setForgeTime(forge.getForgeTime());
    remaining.setStartTime(forge.getStartTime() + finished * forge.getForgeTime());  // ★ startTime 前移
    this.player.getActiveForges().set(queueId - 1, remaining);
} else {
    this.player.getActiveForges().remove(queueId - 1);
}
```

→ `startTime += finished * forgeTime` —— 与 notes/62 `takeCompound` 的 `startTime += costTime * count` **完全相同的滑动锚点技巧**。
→ **跨系统再现 = 这是 grasscutter lazy 队列的标准惯用法**，非偶然。值得作为"识别 lazy 串行队列实现"的特征签名：见到"取件后 startTime 前移"即可断定 lazy 队列。
→ 与 notes/62 略异：合成台原地改 `startTime` 字段，锻造**新建对象替换**（因 ActiveForgeData 字段更多 + List set），语义等价。

---

## 5. lazy 状态 + onTick 仅通知（notes/59 Expedition 同模式）

```java
// Player.java:1282 (onTick 块内, 与 Expedition lazy-notify 同位置)
this.getForgingManager().sendPlayerForgingUpdate();

public synchronized void sendPlayerForgingUpdate() {
    if (player.getActiveForges().size() <= 0) return;                  // 无队列直接跳过
    boolean hasChanges = player.getActiveForges().stream()
        .anyMatch(forge -> forge.updateChanged(currentTime));          // 懒重算+脏标记
    if (!hasChanges) return;                                            // 无变化不推
    this.sendForgeQueueDataNotify();
    player.getActiveForges().forEach(f -> f.setChanged(false));
}

// ActiveForgeData
public boolean updateChanged(int currentTime) {
    int currentUnfinished = getUnfinishedCount(currentTime);            // 仍是懒算
    if (currentUnfinished != this.lastUnfinishedCount) {               // 跨过整数边界
        this.changed = true; this.lastUnfinishedCount = currentUnfinished;
    }
    return this.changed;
}
```

要点：
1. **状态从不依赖 onTick**：`getFinishedCount(now)` 任何时刻都对。onTick **只负责"推红点通知"**，不拥有状态。
2. **updateChanged = 懒重算 + 脏标记**：每 tick 懒算 unfinished，与上次缓存比，跨整数边界才置 `changed`，推完清标记。
3. **这正是 notes/59 Expedition 的同款**（Player.onTick 内 lazy-state + notify-only poll）——**①Lazy 的子模式"懒状态 + 通知轮询"**：状态懒算解决正确性，轻量轮询解决"延迟感知"（红点及时）。
4. 三层防抖：无队列 return → 无变化 return → 跨边界才 changed。把 onTick 成本压到最低。

→ 修正 notes/50 对 lazy "代价是延迟感知"的论断：grasscutter 用 **notify-only onTick** 弥补，状态仍 lazy，仅通知周期化。这是比纯 lazy 更精确的工程实态。

---

## 6. payItems 失败缺 return：第 3 次出现 → 系统性代码异味

```java
boolean success = player.getInventory().payItems(material, req.getForgeCount(), ActionReason.ForgeCost);
if (!success) {
    this.player.sendPacket(new PacketForgeStartRsp(Retcode.RET_ITEM_COUNT_NOT_ENOUGH));
    // ❌ 缺 return; —— 继续往下: 扣锻造点 + 建 ActiveForgeData
}
this.player.setForgePoints(this.player.getForgePoints() - requiredPoints);   // 照扣点
ActiveForgeData activeForge = new ActiveForgeData(); ...                       // 照建队列
this.player.getActiveForges().add(activeForge);
```

→ **同 notes/63 烹饪一模一样的 bug 类**：`payItems` 失败发了错误码但**未 return**，继续创建锻造队列（原料没扣，却照常产出 + 扣锻造点）。

### 6.1 三次出现统计 → 升格系统性

| 笔记 | 系统 | payItems 失败处理 | 结论 |
|---|---|---|---|
| notes/62 | 合成台 | `sendPacket(...); return;` | ✅ 正确 |
| notes/63 | 烹饪 | `sendPacket(...);`（无 return）| ❌ 无料白嫖成品 |
| notes/64 | 锻造 | `sendPacket(...);`（无 return）| ❌ 无料白嫖 + 错扣锻造点 |

→ 3 个同包/同类系统，1 对 2 错 → **这是系统性代码异味，不是孤例**。已记入记忆 [[grasscutter-payitems-missing-return]]，作为审计 grasscutter 经济系统的固定检查项：**每个 `payItems(...)` 后必须紧跟 return on false**。
→ 根因推测：grasscutter "功能优先"风格 + 复制粘贴 handler 骨架时漏改（合成台写对的版本没被复用）。

---

## 7. 取消锻造：完整退款（正确实现）

```java
private void cancelForge(int queueId) {
    if (forge.getFinishedCount(currentTime) > 0) return;   // ★ 已有成品不许取消(防套利)
    // 退原料 + 退摩拉 + 退锻造点(封顶 300_000)
    for (var material : data.getMaterialItems()) { ... addItem(returnItem); }
    player.setMora(player.getMora() + data.getScoinCost() * forge.getCount());
    int newPoints = Math.min(player.getForgePoints() + requiredPoints, 300_000);
    player.setForgePoints(newPoints);
    player.getActiveForges().remove(queueId - 1);
}
```

→ **取消有"无成品"前置校验**（`getFinishedCount>0 → return`），防"锻一半取消既拿成品又退料"套利——这里反而**校验严谨**（与 §6 形成反差：同类作者，关键套利点防住了，普通失败路径漏 return）。
→ 锻造点是独立货币，封顶 300_000。退款三件套（料/摩拉/点）完整。

---

## 8. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| **原料为 0 发 ForgeStartReq** | **✓ 有效！payItems 缺 return → 无料建队列产出（§6 bug）** |
| 锻一半取消套利 | ✗ cancelForge 有 getFinishedCount>0 前置校验 |
| 篡改 startTime/完成数 | ✗ @Entity 账本 + currentTime 服务端取 |
| 伪造 forgeId | ✗ ForgeDataMap.containsKey 校验 + return |
| 超队列数开锻 | ✗ activeForges.size() >= determineNumberOfQueues 校验 |
| 篡改锻造点 | ✗ 服务端 player.forgePoints 账本 |

→ 关键套利点（队列上限/取消/forgeId）防得住，但**普通失败路径 §6 漏 return** 是真经济漏洞。整体反作弊**强于烹饪、与合成台同档**（除 §6）。

---

## 9. 关键收获

1. **ForgingManager 303 行 + ActiveForgeData 88 行 = 模型①Lazy**（第三次预测命中）
2. **公式 `(now-startTime)/forgeTime`** 与合成台/派遣**逐字符同族**——三分法判据稳定
3. **并行多队列 vs 合成台串行**：同①Lazy，容器选型(List vs Map)由并发形态决定
4. **模型①细分两形态**：串行 Map<key> / 并行 List<index>，由业务并发语义定
5. **滑动 startTime 技巧跨系统再现**（notes/62 同款）→ grasscutter lazy 队列**标准惯用法/特征签名**
6. **lazy 状态 + onTick 仅通知**（Player.java:1282，与 notes/59 Expedition 同位同模式）
7. **updateChanged = 懒重算 + 脏标记 + 跨整数边界判定**，三层防抖压低 onTick 成本
8. **修正 notes/50 论断**：lazy "延迟感知"代价被 notify-only onTick 弥补，状态仍 lazy
9. **★ payItems 缺 return 第 3 次出现**（合成台对/烹饪错/锻造错）→ 系统性代码异味，入记忆
10. **锻造该 bug 更重**：不仅白嫖成品，还错扣锻造点（双重账目错乱）
11. **cancelForge 正确**：getFinishedCount>0 前置校验防取消套利 + 三件套完整退款
12. **作者反差**：关键套利点防得严，普通失败路径漏 return（功能优先风格副作用）
13. **锻造点独立货币**，封顶 300_000，setForgePoints 服务端账本
14. **战令联动**：obtainItems → TRIGGER_DO_FORGE（notes/22/41 WatcherTriggerType）
15. **图纸解锁 per-player**：`Player.getUnlockedForgingBlueprints()`（Set，正确，非 notes/62 static bug）
16. **冒险等级门控产能**：AR 15/10/5 → 4/3/2/1 队列（还原官服）
17. **@Entity ActiveForgeData 嵌 Player.activeForges List 持久化**（notes/30）
18. **离线锻造**：startTime 已存，登录/取件懒算离线产出（同 notes/59/62 离线收益）
19. **制造三件套收官**：合成台(①串行)/烹饪(第0类)/锻造(①并行)——三分法全覆盖验证
20. **方法论闭环**：三分法 3 连预测命中 + 2 条横切线索(滑动 startTime / payItems return)跨系统坐实

---

## 10. 一句话总结

> **ForgingManager (303 行) + ActiveForgeData (88 行 @Entity) = 时间锁并行多队列制造 —— 状态全靠 `getFinishedCount(now)=(now-startTime)/forgeTime` 纯函数懒算（模型①，与合成台/派遣同族公式），AR 决定 1~4 并行队列存 Player.activeForges List；取件用 `startTime+=finished*forgeTime` 滑动锚点保留未完成进度（notes/62 同款惯用法）；Player.onTick 仅做"懒重算+脏标记"的红点通知（notes/59 同模式），状态从不依赖 tick.**
>
> **方法论意义: [[grasscutter-resource-execution-models]] 三分法第三次预测性验证（①命中）+ 制造三件套(合成台①串行/烹饪第0类/锻造①并行)全覆盖；两条横切线索跨系统坐实——"滑动 startTime"是 grasscutter lazy 队列标准惯用法/识别签名，"payItems 失败缺 return"第 3 次出现（1 对 2 错）升格系统性经济 bug 类（入记忆 [[grasscutter-payitems-missing-return]]）；并修正 notes/50 "lazy 代价是延迟感知"——实由 notify-only onTick 弥补.**

---

**前置笔记**：
- notes/62 CookingCompound 合成台 - 同①Lazy 串行队列；滑动 startTime 同款；payItems 写对的版本
- notes/63 CookingManager 烹饪 - payItems 缺 return 同 bug 类（本篇第 3 次坐实系统性）
- notes/61 StaminaManager - 建立资源执行模型三分法（本篇第三次预测验证）
- notes/59 ExpeditionSystem - lazy 状态 + onTick 仅通知 同模式（Player.onTick 同位）
- notes/50 Resin - 修正其"lazy 代价是延迟感知"论断
- notes/22/41 BattlePass/事件 - TRIGGER_DO_FORGE 战令联动

**关联文件**：
- `ForgingManager.java`(303) - 队列调度 + 取件/取消
- `ActiveForgeData.java`(88) - @Entity lazy 懒算 + updateChanged 脏标记
- `Player.java:151` activeForges List / `:1282` onTick → sendPlayerForgingUpdate
- `ForgeData`(excel) - forgeTime/forgePoint/materialItems/scoinCost/resultItemId
- Handler：`HandlerForgeStartReq` / `HandlerForgeGetQueueDataReq` / `HandlerForgeQueueManipulateReq`
- Bug 位点：`ForgingManager.java:142-145`（payItems 失败缺 return）

**研究的源代码**: ForgingManager 303 行 + ActiveForgeData 88 行全文 + Player 接线 + 与 notes/62/63 横向对照。
