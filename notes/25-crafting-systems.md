# 25 · 制作系统大全 · Combine / Cook / Compound / Forge / Decompose

游戏里的 5 种"输入材料 → 输出物品"机制——表面看相似，**实际架构差异非常大**。从同步瞬时合成到异步队列锻造，每种都有独立的工程取舍。

> 核心代码：4 个独立 Manager + 5 种 Excel 配表 + ~970 行  
> Combine（一般合成）/ Cook（烹饪）/ Compound（复合材料）/ Forge（锻造）/ Decompose（圣遗物分解）

---

## 1. 5 种制作机制概览

```
                     ┌───────────────────────────────────────────────┐
                     │           Inventory.payItems(ActionReason)     │
                     │                  ↓                              │
            ┌────────┼────────┬─────────┼────────┬────────────┐
            │        │        │         │        │            │
        Combine    Cook    Compound   Forge   Decompose
        (合成)     (烹饪)   (复合)    (锻造)   (分解)
        瞬时输出    瞬时+概率 异步等待   异步队列  瞬时随机
            │        │        │         │        │
            └────────┼────────┼─────────┼────────┘
                     ↓        ↓        ↓
                Inventory.addItem(ActionReason.Combine/Cook/...)
```

| 类型 | 输出方式 | 队列 | 特殊机制 | 主要 ActionReason |
|---|---|---|---|---|
| **Combine** 合成 | 瞬时 | 无 | 简单输入→输出 | `Combine` |
| **Cook** 烹饪 | 瞬时 | 无 | "完美烹饪"概率（看角色 fetter）| `Cook` |
| **Compound** 复合 | **异步** | 单队列 | 等数小时后取 | `Compound` |
| **Forge** 锻造 | **异步队列** | 4 队列 | 锻造点消耗 + 时间 | `ForgeOutput` / `ForgeReturn` |
| **Decompose** 分解 | 瞬时 | 无 | **概率随机产出** | (隐式 Combine) |

---

## 2. Combine 系统：最简单的同步合成

```java
// CombineManger.combineItem (132 行总文件)
public CombineResult combineItem(Player player, int cid, int count) {
    CombineData combineData = GameData.getCombineDataMap().get(cid);
    
    // 等级要求
    if (combineData.getPlayerLevel() > player.getLevel()) return null;
    
    // 扣材料 + 摩拉 (一步原子完成)
    List<ItemParamData> material = new ArrayList<>(combineData.getMaterialItems());
    material.add(new ItemParamData(202, combineData.getScoinCost()));   // 摩拉
    boolean success = player.getInventory().payItems(material, count, ActionReason.Combine);
    
    if (!success) {
        player.sendPacket(new PacketCombineRsp(RET_ITEM_COMBINE_COUNT_NOT_ENOUGH));
        return;
    }
    
    // 生成产物
    player.getInventory().addItem(combineData.getResultItemId(),
                                   combineData.getResultItemCount() * count);
    
    return result;
}
```

**典型用例**：
- 圣遗物经验素材合成（用 3 个圣遗物经验素材合成 1 个高级）
- 物以类聚副本入场券
- 材料"升档"（5 个低级 → 1 个中级）

→ **132 行最简单的 manager**——纯同步事务，没有队列、概率、时间。

---

## 3. Decompose 系统：圣遗物分解（瞬时随机）

CombineManger 同时管理圣遗物分解（"圣遗物收纳箱"功能）：

```java
public synchronized void decomposeReliquaries(Player player, int configId, int count, List<Long> input) {
    // 反作弊：configId 必须合法
    List<Integer> possibleDrops = reliquaryDecomposeData.get(configId);
    if (possibleDrops == null) return RET_RELIQUARY_DECOMPOSE_PARAM_ERROR;
    
    // 反作弊：输入数 = 输出 × 3
    if (input.size() != count * 3) return RET_RELIQUARY_DECOMPOSE_PARAM_ERROR;
    
    // 反作弊：所有输入圣遗物必须在玩家背包里
    for (long guid : input) {
        if (player.getInventory().getItemByGuid(guid) == null) return RET_xxx;
    }
    
    // 删除输入圣遗物
    for (long guid : input) {
        player.getInventory().removeItem(guid);
    }
    
    // 随机生成输出（按 ReliquaryDecompose.json 的可能列表）
    List<Long> resultItems = new ArrayList<>();
    for (int i = 0; i < count; i++) {
        int itemId = Utils.drawRandomListElement(possibleDrops);
        GameItem newReliquary = new GameItem(itemId, 1);
        player.getInventory().addItem(newReliquary);
        resultItems.add(newReliquary.getGuid());
    }
}
```

**3:1 比例**：3 件圣遗物 → 1 件随机圣遗物。`possibleDrops` 是按 `configId` 分类的池（不同套装/部位）。

→ **典型的"随机重组"机制**——给玩家"刷副词条"的捷径，但需要消耗已有圣遗物。

---

## 4. Cook 系统：烹饪 + "完美烹饪"概率

```java
// CookingManager.java (185 行)
public boolean handleCook(Player player, int recipeId, int qualityType, int count) {
    CookRecipeData recipe = GameData.getCookRecipeDataMap().get(recipeId);
    if (recipe == null) return false;
    
    // ★ 三种品质选择 (普通/熟练/完美)
    // qualityType 决定输出物品的品质
    
    // 扣材料
    if (!inventory.payItems(recipe.getInputVec(), count, ActionReason.Cook)) return false;
    
    // 配角色加成 (CookBonusData)
    int proficiencyBonus = getCookProficiencyBonus(player, recipeId);
    
    // ★ 完美烹饪概率 (基础 +角色 fetter level 加成)
    int perfectChance = recipe.getMaxProficiency() - getProficiency(...);
    
    int normalCount = 0, deliciousCount = 0;
    for (int i = 0; i < count; i++) {
        int roll = ThreadLocalRandom.current().nextInt(100);
        if (roll < perfectChance) {
            deliciousCount++;
        } else {
            normalCount++;
        }
    }
    
    // 加产物 (普通版 + 完美版分别加)
    if (normalCount > 0) inventory.addItem(recipe.getQualityOutputId(qualityType), normalCount);
    if (deliciousCount > 0) inventory.addItem(recipe.getDeliciousOutputId(qualityType), deliciousCount);
    
    // 触发战令 + 任务事件
    player.getBattlePassManager().triggerMission(WatcherTriggerType.TRIGGER_COOK_NUM, ...);
}
```

### 5 个核心机制

1. **三档品质** (`qualityType`)：普通 / 熟练 / 完美——玩家选档位
2. **CookBonusData 角色加成**：某些角色（如香菱）烹饪时有专属 buff（提升完美率/产出）
3. **熟练度 (proficiency)**：对每个食谱的熟练度积累（做得多越熟练，完美率提升）
4. **完美版独立 itemId**：例如"普通蒙德烤鱼" vs "完美蒙德烤鱼"是不同 itemId，效果不同
5. **WatcherTriggerType.TRIGGER_COOK_NUM**：触发战令进度（notes/22）

→ **"完美烹饪"是养成系统的小循环**——玩家有动机反复做同一道菜直到熟练度满。

---

## 5. Compound 系统：异步复合（"等几小时"）

```java
// CookingCompoundManager.java (142 行)
public boolean handleCompoundReq(Player player, int compoundId, int count) {
    CompoundData compoundData = GameData.getCompoundDataMap().get(compoundId);
    if (compoundData == null) return false;
    
    // 扣材料
    if (!inventory.payItems(compoundData.getInputItems(), count, ActionReason.Compound)) return false;
    
    // ★ 创建"等待中的复合任务"
    int currentTime = Utils.getCurrentSeconds();
    int finishTime = currentTime + compoundData.getCostTime() * count;   // 时间累加
    
    ActiveCookCompoundData active = ActiveCookCompoundData.of()
        .compoundId(compoundId)
        .totalCount(count)
        .finishTime(finishTime)
        .build();
    
    player.getActiveCookCompounds().add(active);
    save();
    
    // 不立即给输出！
}

// 玩家"领取"时
public void handleTakeCompoundOutput(Player player, List<Integer> compoundIds) {
    for (int cid : compoundIds) {
        ActiveCookCompoundData active = findById(cid);
        int currentTime = Utils.getCurrentSeconds();
        
        // 计算"已完成"数量（按时间比例）
        int finishedCount = Math.min(active.getTotalCount(), 
            (currentTime - active.getStartTime()) / compoundData.getCostTime());
        
        if (finishedCount > 0) {
            inventory.addItem(compoundData.getResultItem(), finishedCount, ActionReason.Compound);
            active.subtractCount(finishedCount);
        }
    }
}
```

**典型用例**：
- 调味花蜜（用蜂蜜 + 糖等慢慢复合，每个要 3 小时）
- 树脂复合（虚空之缕 + 月之核合成原粹树脂）
- 鬼斧神工（角色培养材料的复合）

→ **异步设计**：玩家提交后**离线时间也在跑**——这是为什么"睡一觉醒来材料就有了"。
**强制定期登录**——配合 BP 周积分上限，留存设计。

---

## 6. Forge 系统：异步锻造队列（最复杂）

```java
// ForgingManager.java (303 行 - 最大的 crafting manager)
private synchronized int determineNumberOfQueues() {
    int adventureRank = player.getLevel();
    return
        (adventureRank >= 15) ? 4 :
        (adventureRank >= 10) ? 3 :
        (adventureRank >= 5)  ? 2 :
                                 1;
}
```

**冒险等级解锁队列数**：
- AR < 5: 1 队列
- AR 5-9: 2 队列
- AR 10-14: 3 队列
- AR ≥ 15: **4 队列**（满）

```java
public synchronized void handleForgeStartReq(ForgeStartReq req) {
    // 1. 队列已满检查
    if (player.getActiveForges().size() >= determineNumberOfQueues()) {
        send(RET_FORGE_QUEUE_FULL);
        return;
    }
    
    ForgeData forgeData = GameData.getForgeDataMap().get(req.getForgeId());
    
    // 2. 锻造点检查（forge points = 玩家自带的"耐力"）
    int requiredPoints = forgeData.getForgePoint() * req.getForgeCount();
    if (player.getForgePoints() < requiredPoints) return;
    
    // 3. 扣材料 + 摩拉 + 锻造点
    inventory.payItems(forgeData.getMaterialItems(), req.getForgeCount(), ActionReason.ForgeOutput);
    player.setForgePoints(player.getForgePoints() - requiredPoints);
    
    // 4. 创建 ActiveForgeData (持久化)
    ActiveForgeData active = ActiveForgeData.of()
        .forgeId(req.getForgeId())
        .totalCount(req.getForgeCount())
        .startTime(Utils.getCurrentSeconds())
        .avatarId(req.getAvatarId())   // 用谁锻造（影响特殊 buff）
        .build();
    player.getActiveForges().add(active);
    save();
}
```

### 取消队列（返还材料）

```java
public synchronized void handleQueueManipulate(Player player, ForgeQueueManipulateReq req) {
    if (req.getManipulateType() == FORGE_QUEUE_MANIPULATE_TYPE_REMOVE) {
        ActiveForgeData active = activeForges.get(req.getForgeQueueId() - 1);
        
        // ★ 部分完成的不退材料
        int finishedCount = active.getFinishedCount(currentTime);
        int unfinishedCount = active.getUnfinishedCount(currentTime);
        
        // 已完成的发给玩家
        if (finishedCount > 0) {
            inventory.addItem(forgeData.getResultItem(), finishedCount, 
                ActionReason.ForgeOutput);
        }
        
        // 未完成的退材料 + 锻造点（按 unfinishedCount 比例）
        if (unfinishedCount > 0) {
            inventory.addItems(forgeData.getMaterialItems(), unfinishedCount, 
                ActionReason.ForgeReturn);   // ★ 独立 ActionReason
            player.addForgePoints(forgeData.getForgePoint() * unfinishedCount);
        }
        
        activeForges.remove(req.getForgeQueueId() - 1);
    }
}
```

**关键设计**：
1. **每队列可锻造多个同样物品**（按时间累加）
2. **取消时按"已/未完成"分别处理**：已完成发奖，未完成退还
3. **`ActionReason.ForgeReturn`** 独立——便于审计区分"产出"和"返还"
4. **锻造点（耐力系统）**：每天恢复，限制锻造频率

---

## 7. 解锁机制：用配方道具解锁

四种制作都通过**消耗"配方书"道具**解锁：

```java
// game/props/ItemUseAction/
ItemUseUnlockCombine        合成配方解锁
ItemUseUnlockCookRecipe     菜谱解锁
ItemUseUnlockForge          锻造蓝图解锁
ItemUseCombineItem          直接合成（特殊配方）
```

```java
// 例：使用一本"风味料理：蒙德烤鱼"
public class ItemUseUnlockCookRecipe extends ItemUseAction {
    public boolean useItem(UseItemParams params) {
        int recipeId = ...;
        params.player.getUnlockedRecipies().add(recipeId);
        params.player.sendPacket(new PacketCookRecipeDataNotify(recipeId));
        return true;
    }
}
```

→ **复用 notes/15 的 `ITEM_USE_*` 抽象**——配方书就是"使用后解锁配方"的物品。再次验证统一抽象的威力。

---

## 8. 共享的 ActionReason（审计追溯）

所有制作系统都通过 `Inventory.payItems` 扣材料 + `Inventory.addItem` 加产物，关键差异是 **ActionReason**：

```java
ActionReason.Combine       一般合成
ActionReason.Cook          烹饪产出
ActionReason.Compound      复合产出
ActionReason.ForgeOutput   锻造产出
ActionReason.ForgeReturn   锻造取消返还 ★ 单独
```

→ 这就是 **notes/15 提到的 100+ ActionReason** 的实战意义——客户端弹窗"通过烹饪获得"vs"通过锻造获得"，提示词不同；客服查日志能精确追溯每次物品来源。

---

## 9. WatcherTriggerType 触发（与 BP / Activity 联动）

```java
// 烹饪后触发
player.getBattlePassManager().triggerMission(WatcherTriggerType.TRIGGER_COOK_NUM, ...);

// 锻造后触发
player.getBattlePassManager().triggerMission(WatcherTriggerType.TRIGGER_FORGE_NUM, ...);
```

→ **同一个事件源**通知 BP（notes/22）+ Activity（notes/20）。"做菜 100 次"、"锻造 50 把武器" 这些任务自动推进。

---

## 10. 完整流程示例：玩家锻造 5 把祭礼武器

```
[玩家在锻造台点 "锻造祭礼之礼" × 5]
   ↓
HandlerForgeStartReq(forgeId=祭礼之礼锻造图, count=5, avatarId=雷神)
   ↓
ForgingManager.handleForgeStartReq:
   1. 检查队列是否满 (4 队列上限)
   2. 检查 forgePoints (5 把 × 单消耗)
   3. inventory.payItems(材料 × 5, 摩拉 × 5万, ActionReason.ForgeOutput)
   4. 创建 ActiveForgeData (start=now, total=5, avatarId=雷神)
   5. 持久化到 DB
   ↓ (玩家继续别的事，包括离线)
   
[1 小时后玩家上线]
HandlerForgeGetQueueDataReq → 计算"已完成 = (now-start)/perItemTime"
   返回 finishedCount=2, unfinishedCount=3
   ↓
[玩家点 "取出"]
HandlerForgeQueueManipulateReq(REMOVE, queueId=1)
   ↓
ForgingManager.handleQueueManipulate:
   active.finished = 2, unfinished = 3
   1. inventory.addItems(祭礼之礼 ×2, ActionReason.ForgeOutput)
   2. inventory.addItems(原始材料 ×3, ActionReason.ForgeReturn) ← 退材料
   3. player.addForgePoints(forgePoint × 3) ← 退耐力
   4. 移除 ActiveForgeData
   ↓
sendForgeQueueDataNotify
   ↓
[客户端 UI 显示新空队列 + 玩家收到锻造好的武器]
```

---

## 11. 4 manager 设计模式对比

| 维度 | Combine | Cook | Compound | Forge |
|---|---|---|---|---|
| 代码行数 | 132 | 185 | 142 | 303 |
| 同步/异步 | 同步 | 同步 | **异步** | **异步队列** |
| 队列数 | 无 | 无 | 单队列 | 4 队列（按 AR）|
| 概率因素 | 无 | **完美烹饪概率** | 无 | 无 |
| 角色加成 | 无 | **CookBonusData** | 无 | avatarId 影响特殊 buff |
| 取消机制 | N/A | N/A | N/A | **退材料 + ForgeReturn** |
| 限速机制 | 等级要求 | 熟练度 | 时间 | 锻造点 + 时间 + 队列 |
| 持久化 | 无 | 熟练度 | ActiveCookCompoundData | ActiveForgeData |

→ **4 个独立 Manager 是有意义的**——每种制作的"商业模型"和"操作模式"差异巨大，不能强行统一。

---

## 12. 关键设计经验

### 12.1 制作系统的"时间维度"

| 同步 | 适合"一键消耗" | Combine / Cook / Decompose |
|---|---|---|
| **异步** | 适合"每天定期登录" | Compound / Forge |

→ **异步制作 = 留存设计**。玩家"挂着锻造离线"再上线取——**强制每日登录习惯**。

### 12.2 配方解锁用 ItemUseAction 复用

`ItemUseUnlockCombine/CookRecipe/Forge` 都是 `ItemUseAction` 子类——**复用物品使用框架**（notes/15）。配方书就是"特殊的物品"。

### 12.3 ActionReason 细分到操作类型

`ForgeOutput` vs `ForgeReturn` 是同一锻造的两条 ActionReason——**同一系统也要细分审计路径**。这是 notes/15 提到的"100+ ActionReason"必要性的具体体现。

### 12.4 异步系统的"按时间计算完成数"模式

```java
int finishedCount = (currentTime - active.getStartTime()) / costTimePerItem;
```

→ **不需要后台 cron 检查每个锻造完成**！玩家上线时一次性算出"现在已经完成多少"。**懒计算**——节省服务器资源。

### 12.5 部分取消的优雅处理

锻造队列取消时按"已完成发奖 + 未完成退还"分两路。**没有一刀切退所有**——这是用户体验的细节。

---

## 13. 反作弊点（每个 manager 都有）

```java
// 通用
1. payItems 是原子操作（材料不够直接 fail，不会扣一半）
2. inventory.removeItem(guid) 检查 guid 必须存在

// Forge 特有
3. 队列上限严格检查 (AR 决定)
4. 锻造点不能为负
5. 取消队列只能退未完成的部分

// Cook 特有
6. 角色 fetter level 是 server-side 状态 (notes/24)，加成不能伪造
7. 完美烹饪是 server 算的概率

// Compound/Forge 特有
8. ActiveXxxData 时间戳是 server-side，客户端无法跳过等待
9. 完成数按 (now-start)/costTime 实时计算，不存"已完成"标志（防伪造）
```

→ **服务器持有所有"在跑的制作"状态**——客户端只是查询者。

---

## 14. 给做制作系统开发者的提炼

1. **不要强行统一所有制作类型**——同步合成和异步队列差异巨大
2. **异步制作是留存设计利器**——离线时间也在算
3. **"完美级别"输出是养成小循环**——熟练度 + 角色加成
4. **取消按"已完成 / 未完成"分别处理**——优雅的 UX
5. **`ActionReason` 细分**——`ForgeOutput` vs `ForgeReturn` 区分审计
6. **配方解锁用 ItemUseAction**——别另起炉灶
7. **WatcherTriggerType 触发联动**——做事自动推进 BP/Activity
8. **完成数按 (now-start)/cost 计算**——不需要 cron
9. **ActiveXxxData 持久化在 Player**——跨登录保留状态
10. **队列上限按 AR 解锁**——长期成长奖励

---

## 15. 数据规模感

* Combine 配方：~50 个
* Cook 食谱：~200 个（含活动限定）
* Compound 配方：~30 个
* Forge 蓝图：~100 个（武器 + 锻造材料）
* Decompose 池：~10 类（按套装/部位）

代码规模：
- `CombineManger.java`：132 行
- `CookingManager.java`：185 行
- `CookingCompoundManager.java`：142 行
- `ForgingManager.java`：303 行
- 各 Excel + Active*Data：~200 行
- 总核心：**~970 行 = 整个制作系统**

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/combine/CombineManger.java`（132 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/managers/cooking/CookingManager.java`（185 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/managers/cooking/CookingCompoundManager.java`（142 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/managers/forging/ForgingManager.java`（303 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/data/excels/CombineData.java` / `CookRecipeData.java` / `ForgeData.java` / `CompoundData.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/props/ItemUseAction/ItemUseUnlock*.java`（4 种解锁配方）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/Handler*` （6 个 handler）
