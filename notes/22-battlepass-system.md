# 22 · BattlePass / 战令系统 · 长期成长的统计型框架

战令是这类游戏的**长期成长 / 留存设计核心**。前面 notes/04, /15, /16, /19, /20 都引用过它的 `triggerMission` 但没专门讲。本笔记完整还原 ~570 行代码：**任务三段状态机 + 积分系统 + 付费分轨 + 周/日刷新**。

> 核心代码：`game/battlepass/BattlePassSystem.java`（80 行）+ `BattlePassManager.java`（380 行）+ `BattlePassMission.java`（71 行）+ `BattlePassReward.java`（44 行）

---

## 1. 整体架构

```
BattlePassSystem (全局)
  └── cachedTriggers: Map<WatcherTriggerType, List<BattlePassMissionData>>
       倒排索引 - 按触发器类型预聚合 mission

BattlePassManager (per Player, 持久化到 MongoDB)
  ├── ownerUid
  ├── point          当前积分（升级到下一档需要）
  ├── cyclePoints    本周积分（有上限）
  ├── level          当前 BP 等级 (0..50)
  ├── viewed         是否查看过新内容
  ├── paid           是否付费版（高级战令）
  ├── missions       Map<id, BattlePassMission>  全部 mission 进度
  └── takenRewards   Map<rewardId, BattlePassReward>  已领奖励

BattlePassMission (per mission per Player)
  ├── id
  ├── progress       当前进度
  └── status         状态机三段: UNFINISHED → FINISHED → POINT_TAKEN

BattlePassReward (已领奖励记录)
  ├── level          这是哪个等级的奖励
  ├── rewardId
  └── isPaid         付费版还是免费版
```

→ 关键：**BP 是"统计型"的**——它不像 Quest 是流程剧情，更像"做事记次数 → 累计积分 → 解锁奖励"。

---

## 2. 三段任务状态机

```java
public enum BattlePassMissionStatus {
    MISSION_STATUS_UNFINISHED   (0),  // 未完成
    MISSION_STATUS_FINISHED     (1),  // 已完成（待领积分）
    MISSION_STATUS_POINT_TAKEN  (2);  // 积分已领（mission 真正消化完成）
}
```

**为什么有"完成"和"领积分"两步？**
1. 任务进度满 → 标 FINISHED → 客户端弹"任务完成"通知
2. 玩家手动点"领取积分"（或一键领取）→ 调 `takeMissionPoint`
3. 服务器扣减 mission，加 BP point → 标 POINT_TAKEN

→ **二段式领取的 UX 价值**：让玩家**主动看到**积分入账，强化成就感（vs 自动入账无感）。**留存运营手段**。

---

## 3. 触发器系统：跟 Activity 共享 WatcherTriggerType

```java
// BattlePassSystem.triggerMission
public void triggerMission(Player player, WatcherTriggerType triggerType, int param, int progress) {
    List<BattlePassMissionData> triggerList = getTriggers().get(triggerType);
    if (triggerList == null || triggerList.isEmpty()) return;
    
    for (BattlePassMissionData data : triggerList) {
        // 参数过滤（如指定怪物 id 类型的任务）
        if (param != 0 && !data.getMainParams().contains(param)) continue;
        
        // 加载/初始化玩家任务
        BattlePassMission mission = player.getBattlePassManager().loadMissionById(data.getId());
        if (mission.isFinshed()) continue;
        
        // 加进度
        mission.addProgress(progress, data.getProgress());
        
        // 满了标完成
        if (mission.getProgress() >= data.getProgress()) {
            mission.setStatus(BattlePassMissionStatus.MISSION_STATUS_FINISHED);
        }
        
        player.getBattlePassManager().save();
        player.sendPacket(new PacketBattlePassMissionUpdateNotify(mission));
    }
}
```

### 倒排索引（启动时构建）

```java
public BattlePassSystem(GameServer server) {
    this.cachedTriggers = new HashMap<>();
    for (BattlePassMissionData missionData : GameData.getBattlePassMissionDataMap().values()) {
        if (missionData.isValidRefreshType()) {
            getTriggers().computeIfAbsent(missionData.getTriggerType(), e -> new ArrayList<>())
                         .add(missionData);
        }
    }
}
```

→ **同一个 WatcherTriggerType → 多个 mission 监听**（如 "击杀怪物" 触发"杀 5 个史莱姆"和"杀 50 个怪物"两个不同 mission）。倒排索引避免 O(N) 全扫。

### 共享事件源

```java
// notes/15 Inventory.addItem 触发链
private void triggerAddItemEvents(GameItem result) {
    getPlayer().getBattlePassManager().triggerMission(    ← BP
        WatcherTriggerType.TRIGGER_OBTAIN_MATERIAL_NUM, ...);
    getPlayer().getQuestManager().queueEvent(              ← Quest
        QuestContent.QUEST_CONTENT_OBTAIN_ITEM, ...);
    // Activity 也会订阅同一个 WatcherTriggerType
}
```

→ **同一个动作通知 BP + Quest + Activity 三个独立系统**。这就是为什么 `WatcherTriggerType` 涨到 ~150 个（notes/20）。

---

## 4. 积分到等级的换算（BP point → BP level）

```java
public void addPointsDirectly(int points, boolean isWeekly) {
    int amount = points;
    
    // ★ 周积分上限（防爆肝刷）
    if (isWeekly) {
        amount = Math.min(amount, GameConstants.BATTLE_PASS_POINT_PER_WEEK - this.cyclePoints);
    }
    if (amount <= 0) return;
    
    this.point += amount;
    this.cyclePoints += amount;
    
    // ★ 升级
    if (this.point >= GameConstants.BATTLE_PASS_POINT_PER_LEVEL && this.getLevel() < BATTLE_PASS_MAX_LEVEL) {
        int levelups = Math.floorDiv(this.point, GameConstants.BATTLE_PASS_POINT_PER_LEVEL);
        levelups = Math.min(levelups, BATTLE_PASS_MAX_LEVEL - levelups);
        
        this.point = this.point - (levelups * GameConstants.BATTLE_PASS_POINT_PER_LEVEL);
        this.level += levelups;
    }
}
```

### 关键常量

```java
GameConstants.BATTLE_PASS_POINT_PER_LEVEL    // 1000 积分 / 等级
GameConstants.BATTLE_PASS_POINT_PER_WEEK     // 周积分上限（约 12000）
GameConstants.BATTLE_PASS_MAX_LEVEL          // 50（顶级）
GameConstants.BATTLE_PASS_LEVEL_PRICE        // 跳级单价（原石）
GameConstants.BATTLE_PASS_CURRENT_INDEX      // 当前 BP 期数
```

### 周积分上限的运营意义

```java
amount = Math.min(amount, BATTLE_PASS_POINT_PER_WEEK - this.cyclePoints);
```

→ **强制玩家持续上线**。即使你某周连刷 50 小时，超过周积分上限就不再加分。**反爆肝设计**——把"完成 BP 等级"分摊到 6 周（一期），保留每周登录习惯。

---

## 5. 双轨奖励（免费 vs 付费）

```java
public void takeReward(List<BattlePassRewardTakeOption> takeOptionList) {
    for (BattlePassRewardTakeOption option : takeOptionList) {
        // 防重领（已领过的 rewardId）
        if (getTakenRewards().containsKey(option.getTag().getRewardId())) continue;
        
        // 等级检查
        if (option.getTag().getLevel() > this.getLevel()) continue;
        
        BattlePassRewardData rewardData = GameData.getBattlePassRewardDataMap().get(
            BATTLE_PASS_CURRENT_INDEX * 100 + option.getTag().getLevel());
        
        // ★ 双轨判断
        if (rewardData.getFreeRewardIdList().contains(option.getTag().getRewardId())) {
            rewardList.add(option);   // 免费档可领
        } else if (this.isPaid() && rewardData.getPaidRewardIdList().contains(option.getTag().getRewardId())) {
            rewardList.add(option);   // 付费档（仅付费版）
        }
    }
    
    // 实际发奖
    for (var option : rewardList) {
        RewardData reward = GameData.getRewardDataMap().get(tag.getRewardId());
        for (var entry : reward.getRewardItemList()) {
            // 处理可选礼包 (MATERIAL_SELECTABLE_CHEST)
            if (rewardItemData.getMaterialType() == MaterialType.MATERIAL_SELECTABLE_CHEST) {
                this.takeRewardsFromSelectChest(rewardItemData, index, entry, rewardItems);
            } else {
                rewardItems.add(new GameItem(rewardItemData, entry.getItemCount()));
            }
        }
        getTakenRewards().put(rewardId, new BattlePassReward(...));
    }
    
    getPlayer().getInventory().addItems(rewardItems);
}
```

每个等级配置：
```java
class BattlePassRewardData {
    int level;
    List<Integer> freeRewardIdList;   // 免费版可领
    List<Integer> paidRewardIdList;   // 付费版独享
}
```

→ **付费版不是"多发更多奖励"，而是解锁额外档位**。商业上：
- 免费玩家也能感受到"每升级有奖励"——降低弃坑率
- 付费版多 50% 左右奖励 + 限定皮肤/角色——付费动机
- 同一系统两套奖励池——配表灵活控制力度

---

## 6. 可选礼包：玩家自选物品

```java
private void takeRewardsFromSelectChest(ItemData rewardItemData, int index, ItemParamData entry, List<GameItem> rewardItems) {
    // 礼包配表里 useParam[0] = "1001,1002,1003,1004,1005" 这种逗号分隔列表
    String[] choices = rewardItemData.getItemUse().get(0).getUseParam()[0].split(",");
    int chosenId = Integer.parseInt(choices[index - 1]);
    
    // 两种礼包类型：
    if (useOp == ItemUseOp.ITEM_USE_ADD_SELECT_ITEM) {
        // 直接给选中的物品
        rewardItems.add(new GameItem(GameData.getItemDataMap().get(chosenId), entry.getItemCount()));
    } else if (useOp == ItemUseOp.ITEM_USE_GRANT_SELECT_REWARD) {
        // 选中的是 rewardId，再展开成具体物品列表
        RewardData selectedReward = GameData.getRewardDataMap().get(chosenId);
        for (var r : selectedReward.getRewardItemList()) {
            rewardItems.add(new GameItem(GameData.getItemDataMap().get(r.getItemId()), r.getItemCount()));
        }
    }
}
```

**典型例子**：高级战令的 4 星武器自选礼包
```
useParam[0] = "11405,12405,13407,14409,15405"   // 5 把 4 星武器 id
玩家在客户端选 index=3 → chosenId = 13407 (匣里灭辰)
```

→ **`ITEM_USE_*` 是物品使用的统一抽象**（notes/15 看过）。礼包就是"使用后吐出别的物品"的物品。

---

## 7. 双周期刷新（Daily + Weekly）

```java
// 每日刷新（Player.doDailyReset 触发, notes/04）
public void resetDailyMissions() {
    for (var mission : this.missions.values()) {
        if (mission.getData().getRefreshType() == null 
            || mission.getData().getRefreshType() == BATTLE_PASS_MISSION_REFRESH_DAILY) {
            mission.setStatus(BattlePassMissionStatus.MISSION_STATUS_UNFINISHED);
            mission.setProgress(0);
        }
    }
}

// 每周刷新（周一）
public void resetWeeklyMissions() {
    for (var mission : this.missions.values()) {
        if (mission.getData().getRefreshType() == BATTLE_PASS_MISSION_REFRESH_CYCLE_CROSS_SCHEDULE) {
            mission.setStatus(BattlePassMissionStatus.MISSION_STATUS_UNFINISHED);
            mission.setProgress(0);
        }
    }
}
```

### 三种 RefreshType

```java
public enum BattlePassMissionRefreshType {
    BATTLE_PASS_MISSION_REFRESH_DAILY                  // 每天 0 点重置
    BATTLE_PASS_MISSION_REFRESH_CYCLE_CROSS_SCHEDULE   // 每周一重置（贯穿整期 BP）
    null (永久)                                         // 整期任务，做完就完
}
```

举例：
- 永久："收集 100 件圣遗物"（一期 BP 内累计）
- 每周："本周完成 4 次副本"（每周一清零）
- 每日："今日打开 5 个宝箱"（每天清零）

→ 配合 notes/04 的"懒检查 reset"机制——玩家上线时自动触发对应日/周重置，**不需要全局 cron**。

---

## 8. 付费跳级

```java
public int buyLevels(int buyLevel) {
    int boughtLevels = Math.min(buyLevel, BATTLE_PASS_MAX_LEVEL - buyLevel);
    
    if (boughtLevels > 0) {
        int price = BATTLE_PASS_LEVEL_PRICE * boughtLevels;
        
        if (getPlayer().getPrimogems() < price) return 0;
        
        this.level += boughtLevels;
        this.save();
        getPlayer().sendPacket(new PacketBattlePassCurScheduleUpdateNotify(getPlayer()));
    }
    
    return boughtLevels;
}
```

→ **直接花原石买等级**——氪金捷径，避免周积分上限。这是为什么 BP 期末"差几级满级"的玩家会大量花原石。**典型的商业转化路径**。

---

## 9. 触发流程示例：一次完整的 BP 进度推进

```
[玩家击杀一只丘丘人]
   ↓ Combat 系统
EntityMonster.onDeath()
   ↓
fire WatcherTriggerType.TRIGGER_KILL_MONSTER (param=monsterTypeId, progress=1)
   ↓
BattlePassSystem.triggerMission(player, TRIGGER_KILL_MONSTER, ...)
   ↓
查 cachedTriggers[TRIGGER_KILL_MONSTER] → [
    Mission "击杀任意 100 个怪物" (param=0, progress 0-100),
    Mission "击杀 30 只丘丘暴徒" (param=丘丘暴徒类型id, 0-30),
    ...
]
   ↓
对每个 mission:
   if param != 0 && !mission.mainParams.contains(param) continue;  // 类型不匹配跳过
   mission.addProgress(1, totalProgress);
   if (mission.progress >= total) mission.status = FINISHED;
   ↓
sendPacket(BattlePassMissionUpdateNotify)
   ↓ 客户端弹"任务进度更新"
玩家点 UI "领取积分"
   ↓ 客户端发 takeMissionPoint(missionIds)
BattlePassManager.takeMissionPoint()
   ↓
   for missionId:
       mission = loadMissionById(id)
       if mission.status == FINISHED:
           addPointsDirectly(mission.data.addPoint, isCycleRefresh)   ← 加分 + 升级判断
           mission.status = POINT_TAKEN
   ↓
sendPacket(BattlePassMissionUpdateNotify + CurScheduleUpdateNotify)
   ↓ 客户端弹"获得 200 BP 积分 + 升 1 级"动画
```

---

## 10. BP vs Quest vs Activity 三对比

| 维度 | Quest | Activity | BattlePass |
|---|---|---|---|
| 设计目标 | 剧情有序（流程型）| 限时玩法（统计型）| 长期成长（留存型）|
| 触发系统 | QuestCond/Content | WatcherTriggerType | WatcherTriggerType（共享）|
| 进度数据 | finishProgress[] | WatcherInfo | BattlePassMission |
| 重置周期 | 永久（除非 rewind）| 活动结束 | Daily / Weekly / 期内永久 |
| 状态机 | 4 段 (UNSTARTED→...→FINISHED/FAILED) | 简单 finished bool | **3 段**（FINISHED 后还要"领分"）|
| 持久化 | GameMainQuest entity | PlayerActivityData entity | BattlePassManager entity |
| 付费维度 | 无 | 无 | **有**（免费版 vs 付费版）|

→ BP 的特点：**3 段状态机 + 付费分轨 + 周积分上限**——为商业留存优化的设计。

---

## 11. 完整生命周期：一期 BP 6 周

```
Week 0 (期初):
  - GameConstants.BATTLE_PASS_CURRENT_INDEX += 1
  - 玩家 BattlePassManager.level 重置为 0
  - point/cyclePoints 重置为 0
  - 部分 mission 重置（永久 mission 也清零，因为是新期 BP）
  - 已领奖励重置（takenRewards 清空）

Week 1-6 (做任务积累):
  Day 1-7:
    Daily missions (每日 0 点重置)
    Weekly missions (周一重置)
    Permanent missions (期内永久)
    
  玩家做事 → fire WatcherTriggerType → mission progress
  完成 → 领积分 → BP level up
  到达指定 level → 解锁 reward 档位 → 玩家点领

Week 6 (期末):
  - 玩家可花原石跳级到 50
  - 期内未领奖励仍可领（直到下期开始）
  - 下期开始时清算
```

---

## 12. 关键设计经验

### 12.1 共享 WatcherTriggerType（不重复造轮子）

BattlePass 没有自己的事件枚举——直接复用 Activity 的 WatcherTriggerType。**任意业务系统 trigger 一个 WatcherTriggerType，自动通知 BP + Activity 双向**。这是大规模重用的典型。

### 12.2 倒排索引避免轮询

```java
cachedTriggers: Map<WatcherTriggerType, List<BattlePassMissionData>>
```

启动时按触发类型预聚合 mission，运行时 O(1) 查表。**和 Quest 的 beginCondQuestMap、Scene Script 的 triggersByEvent 同模式**。

### 12.3 三段状态机的 UX 价值

`FINISHED → POINT_TAKEN` 二段式让玩家主动操作，强化成就感。**"自动加分"会让玩家无感**——这是经验设计。

### 12.4 周积分上限 + 跳级

强制每周登录 + 期末紧急消费。**双重商业留存**：
- 平时：周上限保证 6 周登录习惯
- 期末：差几级满级触发"花原石跳级"

### 12.5 双轨奖励配表灵活

每等级配 `freeRewardIdList` 和 `paidRewardIdList`——**修改配表就能调整免费/付费力度**。运营无需改代码。

---

## 13. 反作弊点

```java
// 1. 防重领
if (getTakenRewards().containsKey(option.getTag().getRewardId())) continue;

// 2. 等级检查
if (option.getTag().getLevel() > this.getLevel()) continue;

// 3. 配表合法性校验
if (rewardData.getFreeRewardIdList().contains(rewardId) || 
    isPaid() && rewardData.getPaidRewardIdList().contains(rewardId)) { ... }
else logger.info("Not in rewards list: ...");   // 异常报告

// 4. 付费档位需 isPaid() 才能领

// 5. 跳级前检查货币足够
if (getPrimogems() < price) return 0;

// 6. 任务列表大小检查（防注入海量假 id）
if (missionIdList.size() > BattlePassMissionDataMap.size()) return;

// 7. mission 必须本地存在才能"领分"（不能给不存在的 mission 领分）
if (!hasMission(id)) continue;
```

→ **每个进度推进/领奖操作都有前置 sanity check**。失败请求会进 logger（运营可监测异常 IP/UID）。

---

## 14. 给做长期成长系统开发者的提炼

1. **共享触发器枚举**——别每个系统造一套，WatcherTriggerType 一套通吃
2. **倒排索引按触发类型预聚合**——避免 O(N) 全扫
3. **三段状态机优于二段**：完成 → 领取 → 已领。让玩家主动操作强化成就
4. **周积分上限 = 留存设计**——强制每周登录，反爆肝
5. **付费分轨配表**——同一系统两套奖励池，运营可调
6. **可选礼包用 `ITEM_USE_*` 抽象**——复用物品使用框架
7. **跳级支付通道**——商业转化必须
8. **配表驱动期数**：BATTLE_PASS_CURRENT_INDEX × 100 + level = 期内每等级唯一 id

---

## 15. 数据规模感

* BP 等级：0..50（一期）
* 每等级积分：1000
* 周积分上限：~12000
* Daily mission：每天 4 个
* Weekly mission：每周 4 个
* 永久 mission：~30 个/期
* 期长：~6 周
* 满级总积分：50 × 1000 = 50,000

代码规模：
- `BattlePassSystem.java`：80 行（触发器调度）
- `BattlePassManager.java`：380 行（核心逻辑）
- `BattlePassMission.java`：71 行（任务实体）
- `BattlePassReward.java`：44 行（奖励实体）
- 总核心：~570 行 = **整个长期成长系统**

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/battlepass/BattlePassSystem.java`（80 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/battlepass/BattlePassManager.java`（380 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/battlepass/BattlePassMission.java`（71 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/battlepass/BattlePassReward.java`（44 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/props/BattlePassMissionRefreshType.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/props/BattlePassMissionStatus.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/props/WatcherTriggerType.java`（337 行 enum）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerTakeBattlePassMissionPointReq.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerTakeBattlePassRewardReq.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerBuyBattlePassLevelReq.java`
