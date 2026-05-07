# 20 · Activity / 限时活动系统 · 插件式架构与"临时世界"

限时活动（海岛、风花节、试用角色、音游、龙脊雪山、五彩之旅...）是把任务系统、奖励系统、副本系统**临时拼接成一个独立小游戏**的特殊机制。每一种活动都是一个**插件**——共享底层但有独立数据/货币/规则。

> 核心代码：`game/activity/ActivityHandler.java`（抽象基类）+ `ActivityManager.java` + 各子目录（aster / dragonspine / irodori / musicgame / summer_time_2_8 / trialavatar）+ `condition/`

---

## 1. 整体架构

```
ActivityManager (per Player)
  ├── playerActivityDataMap<activityId, PlayerActivityData>
  │     每个玩家每个活动一份数据
  └── conditionExecutor   评估"我能参与哪些活动"

ActivityHandler<PLAYER_DETAIL_DATA>  (每个活动一个子类)
  ├── @GameActivity(ActivityType.NEW_ACTIVITY_ASTER)   注解
  ├── activityConfigItem (begin/end time, scheduleId)
  ├── activityData (来自 ActivityExcelConfigData)
  ├── watchersMap<WatcherTriggerType, List<ActivityWatcher>>
  ├── onInitPlayerActivityData(player)  → 初始化 player 数据
  ├── onProtoBuild(...)                  → 序列化给客户端
  └── initCurrencyHandlers(...)          → 注册活动专用货币

PlayerActivityData (持久化到 MongoDB)
  ├── activityId
  ├── uid
  ├── watcherInfoMap<watcherId, WatcherInfo>  进度记录
  └── detail: PLAYER_DETAIL_DATA  (活动特有数据，泛型)

ActivityWatcher (per 活动 per type 多个)
  ├── @ActivityWatcherType(WatcherTriggerType.X)  注解
  ├── trigger 条件 (杀怪/获得材料/完成任务...)
  └── 进度计数
```

→ **6 个活动 = 6 个独立的 Handler / DetailData 子类**，但共享同一套 watcher / condition / data 持久化骨架。**插件化设计的典型**。

---

## 2. ActivityHandler 注解 + 反射注册（第 6 次出现）

```java
// AsterActivityHandler.java:17
@GameActivity(ActivityType.NEW_ACTIVITY_ASTER)
public class AsterActivityHandler 
    extends ActivityHandler<AsterGamePlayerData> 
    implements Inventory.VirtualCurrencyHandler<PlayerActivityData> {
    ...
}
```

```java
// ActivityManager.loadActivityConfigData (ActivityManager.java:38)
private static void loadActivityConfigData() {
    var activityHandlerTypeMap = new HashMap<ActivityType, ConstructorAccess<?>>();
    var activityWatcherTypeMap = new HashMap<WatcherTriggerType, ConstructorAccess<?>>();
    var reflections = new Reflections(ActivityManager.class.getPackage().getName());
    
    // 反射扫描 + 按 @GameActivity 注解注册
    reflections.getSubTypesOf(ActivityHandler.class).forEach(item -> {
        var typeName = item.getAnnotation(GameActivity.class);
        activityHandlerTypeMap.put(typeName.value(), ConstructorAccess.get(item));
    });
    
    // 同样扫描 watcher
    reflections.getSubTypesOf(ActivityWatcher.class).forEach(item -> {
        var typeName = item.getAnnotation(ActivityWatcherType.class);
        activityWatcherTypeMap.put(typeName.value(), ConstructorAccess.get(item));
    });
    
    // 加载 ActivityConfig.json，按 activityType 分配 handler
    DataLoader.loadList("ActivityConfig.json", ActivityConfigItem.class).forEach(item -> {
        var activityHandlerType = activityHandlerTypeMap.get(...);
        ActivityHandler activityHandler = activityHandlerType != null 
            ? (ActivityHandler) activityHandlerType.newInstance()
            : new DefaultActivityHandler();   // ★ 没特定实现就用默认
        ...
    });
}
```

→ **第 6 次发现"注解 + 反射 + 自动注册" 模式**！前面 Quest / Scene Script / Talk / Ability / Dungeon 都用过。这是 Grasscutter 的统一架构语言。

`@GameActivity / @ActivityWatcherType` 注解驱动。新加一种活动只需：
1. 加 `ActivityType` 枚举值
2. 写 `XxxActivityHandler extends ActivityHandler` + `@GameActivity(NEW_ACTIVITY_XXX)`
3. 写 `XxxPlayerData` 持有特定状态
4. 写 `XxxActivityWatcher` 类（如有特殊触发器）
5. 在 `ActivityConfig.json` 配活动元数据

---

## 3. WatcherTriggerType：跨系统事件总线（337 行枚举）

`game/props/WatcherTriggerType.java` 是 337 行的 enum，定义了**几乎所有玩家行为**的事件类型。它是 BattlePass 和 Activity 共用的"watcher 事件总线"，区别于 Quest 的 cond/content 系统。

部分类型：

```
TRIGGER_LOGIN                       玩家登录
TRIGGER_FIGHT                       战斗
TRIGGER_KILL_MONSTER                击杀怪物
TRIGGER_OBTAIN_MATERIAL_NUM         获得材料 (notes/15 看过)
TRIGGER_COST_MATERIAL               消耗材料
TRIGGER_FINISH_QUEST                完成任务
TRIGGER_FINISH_DUNGEON              完成副本 (notes/19 看过)
TRIGGER_FINISH_DAILY_TASK           完成委托
TRIGGER_GAIN_AVATAR                 获得角色
TRIGGER_GAIN_AVATAR_NUM             获得角色总数
TRIGGER_OPEN_CHEST                  开宝箱
TRIGGER_COOK_NUM                    烹饪次数
TRIGGER_FORGE_NUM                   锻造次数
TRIGGER_TRIGGER_GADGET              触发机关
TRIGGER_RUN_DISTANCE                奔跑距离
TRIGGER_FORTEAR_RANGE_BUFF_TIME     某 buff 时长
... (~150+ 种)
```

→ **WatcherTriggerType 是"统计型成就/任务"的事件源**。每次玩家做相关动作，BattlePass 和 Activity 都收到通知。

### 用法：触发 watcher

```java
// Inventory.java:241 (notes/15 看过)
private void triggerAddItemEvents(GameItem result) {
    getPlayer().getBattlePassManager().triggerMission(
        WatcherTriggerType.TRIGGER_OBTAIN_MATERIAL_NUM, 
        result.getItemId(), result.getCount());
    ...
}
```

任何"加道具"动作都触发这个 watcher → Activity 和 BattlePass 都更新对应进度。

---

## 4. ActivityCondition 系统（"哪些活动可见"）

不是所有活动对所有玩家可见。每个活动配了 `condGroupId`，指向一组 `ActivityCondition`：

```java
// ActivityHandler.triggerCondEvents (ActivityHandler.java:74)
protected void triggerCondEvents(Player player) {
    val questManager = player.getQuestManager();
    activityData.getCondGroupId().forEach(condGroupId -> {
        val condGroup = GameData.getActivityCondGroupMap().get((int)condGroupId);
        if (condGroup != null)
            condGroup.getCondIds().forEach(condID -> 
                questManager.queueEvent(QuestCond.QUEST_COND_ACTIVITY_COND, condID));
    });
}
```

### Condition 类型（`condition/all/`）

```
DayLess                   时间窗内
DaysGreatEqual            时间窗外
FinishWatcher             某 watcher 已完成
NotFinishTalk             某 talk 未完成
PlayerLevelGreatEqual     玩家等级 ≥ N
QuestFinished             某任务已完成
QuestFinishAllowQuickOpen 某任务完成且可"快速开启"
SalesmanCanDeliver        商人可送货（特定活动逻辑）
UnknownActivityConditionHandler  未知 fallback
```

→ 每个 condition 是独立 handler，按命名约定动态加载。**和 Quest 系统的 acceptCond 几乎平行**。

### 评估流程

```java
// ActivityHandler.toProto
List<Integer> meetConditions = getMeetConditions(conditionExecutor);
proto.setMeetCondList(meetConditions);   // 哪些 condition 已满足
```

→ 客户端拿到列表后**自己决定 UI 显示**（解锁/未解锁/进度多少等）。**meta-data 在服务器，渲染在客户端**。

---

## 5. 案例：Aster（风花节）活动

### 5.1 注解 + 类型参数

```java
@GameActivity(ActivityType.NEW_ACTIVITY_ASTER)
public class AsterActivityHandler 
    extends ActivityHandler<AsterGamePlayerData>   // ★ 泛型指定 player data 类型
    implements Inventory.VirtualCurrencyHandler<PlayerActivityData> {  // 同时实现货币 handler
```

→ Aster 活动**同时是 ActivityHandler + 货币 handler**。这是因为风花节有**专用货币（Aster Credit / Aster Token）**。

### 5.2 活动专用货币注册

```java
@Override
public void initCurrencyHandlers(PlayerActivityData playerActivityData) {
    val inventory = playerActivityData.getPlayer().getInventory();
    inventory.registerVirtualCurrencyHandler(109, this, playerActivityData); // Aster Credit
    inventory.registerVirtualCurrencyHandler(110, this, playerActivityData); // Aster Token
}
```

`itemId 109/110` 通过 `VirtualCurrencyHandler` 接口接入 `Inventory.addVirtualItem`（notes/15 看过的 ITEM_VIRTUAL 机制）。

```java
// 当玩家"获得 50 风花徽印":
inventory.addItem(109, 50, ActionReason.ActivityXxx)
   ↓ ItemType = ITEM_VIRTUAL
   ↓ addVirtualItem(109, 50)
   ↓ default 分支 (notes/15)
   ↓ virtualCurrencyHandlers.get(109).modifyCurrency(50)
   ↓ AsterActivityHandler.modifyCurrency(playerData, 109, 50)
```

→ **活动货币是 ITEM_VIRTUAL 的"插件"**：复用经济系统的入口和审计，但走自己的存储。

### 5.3 三层难度结构（Little / Mid / Large）

```java
@Override
public void onProtoBuild(PlayerActivityData playerActivityData, ActivityInfo activityInfo) {
    // 小活动 (新手向)
    val asterLittle = new AsterLittleDetailInfo();
    asterLittle.setStageId(3);
    asterLittle.setStageState(ASTER_LITTLE_STAGE_STARTED);
    
    // 中等活动 (主活动)
    val asterMiddle = new AsterMidDetailInfo();
    val asterMiddleCamp = new AsterMidCampInfo(1, new Vector(1538.519f, 335.521f, -2113.576f));
    asterMiddle.setCampList(List.of(asterMiddleCamp));
    
    // 大活动 (高难度)
    val asterLarge = new AsterLargeDetailInfo();
    
    // 进度数据
    val asterProgressDetailInfo = new AsterProgressDetailInfo();
    asterProgressDetailInfo.setCount(1000);
    
    // 整合 + 货币
    val asterInfo = new AsterActivityDetailInfo(asterLittle, asterMiddle, asterLarge, asterProgressDetailInfo);
    asterInfo.setAsterToken(asterDetail.getAsterToken());
    asterInfo.setAsterCredit(asterDetail.getAsterCredit());
}
```

→ **每个活动有自己的 Proto 结构**，反映其独特玩法。`AsterLittleDetailInfo` / `AsterMidDetailInfo` / `AsterLargeDetailInfo` 都是 Proto 类型。

---

## 6. 活动配置（ActivityConfig.json）

```jsonc
[
    {
        "activityId": 2001,
        "scheduleId": 2200001,
        "activityType": "NEW_ACTIVITY_ASTER",
        "beginTime": "2021-09-09 00:00:00",
        "endTime":   "2021-09-30 23:59:59",
        ...
    },
    {
        "activityId": 2003,
        "activityType": "NEW_ACTIVITY_DRAGONSPINE",
        ...
    }
]
```

→ 活动**有明确的开始/结束时间**——activity 系统按时间窗自动启用/禁用。

ActivityConfig.json 是**社区维护的开放配表**——上游官方版本里活动一开始有效就过期；社区把它"永久启用"以便研究。

---

## 7. 各活动子类型概览（实测代码里有的）

| 子类型 | 类型枚举 | 玩法 |
|---|---|---|
| **Aster** (风花节) | `NEW_ACTIVITY_ASTER` | 三难度（小/中/大）+ 双货币 |
| **Dragonspine** (龙脊雪山) | `NEW_ACTIVITY_DRAGONSPINE` | 解谜 + 古代地图 |
| **Irodori** (五彩之旅) | `NEW_ACTIVITY_IRODORI` | 拼图 + 创作 |
| **Music Game** (音游) | `NEW_ACTIVITY_MUSIC_GAME` | 节奏游戏 |
| **Summer Time 2.8** (金苹果群岛) | `NEW_ACTIVITY_SUMMER_TIME_V2` | 大型沙盒探索 |
| **Trial Avatar** (试用角色) | `NEW_ACTIVITY_TRIAL_AVATAR` | 借给玩家试玩高级角色 |
| 其他 (无独立 handler) | 用 `DefaultActivityHandler` | 简单签到、限时商店等 |

→ **6 个独立实现的活动**（每个 50-300 行）+ **若干 default 处理的简单活动**。即使每年 N 个新活动，**核心架构不需要动**。

---

## 8. PlayerActivityData 的持久化

```java
@Entity
public class PlayerActivityData {
    private int activityId;
    private int uid;
    private Map<Integer, WatcherInfo> watcherInfoMap;   // watcherId → 进度
    private DetailObject detail;                         // 活动特有数据（多态）
    
    // 多态 detail 由 activity handler 决定具体类型
    public <T> T getDetail(Class<T> clazz) {
        return (T) detail;
    }
}
```

→ **每个玩家每个活动独立一份数据**。Aster 活动的 detail 是 `AsterGamePlayerData`，Dragonspine 是 `DragonspinePlayerData`。多态存储。

### 通用进度跟踪

```java
public class WatcherInfo {
    private int watcherId;
    private int progress;   // 当前进度（如杀怪数、获得物品数）
    private boolean finished;
}
```

→ **所有活动用同一种 WatcherInfo 跟踪进度**。变化时通过 watcher type 派发到对应活动 handler。

---

## 9. 整体生命周期

```
[服务器启动]
ActivityManager.loadActivityConfigData()
   ├── 反射扫描 ActivityHandler/ActivityWatcher 子类
   ├── 按 @GameActivity 注解建立 type → handler 映射
   └── 加载 ActivityConfig.json
       为每个活动找 handler（找不到就用 DefaultActivityHandler）

[玩家登录]
ActivityManager(player) 构造:
   for each enabled activity in activityConfigItemMap:
      data = PlayerActivityData.getByPlayer(player, activityId)
              ?? handler.initPlayerActivityData(player)   // 首次进入活动
      handler.initCurrencyHandlers(data)                  // 注册活动货币
      playerActivityDataMap.put(activityId, data)

[玩家上线后]
servelt push ActivityScheduleInfoNotify
   ↓ 客户端拿到所有可见活动列表 + 各活动 condition meet 状态
   ↓ 渲染活动入口 UI

[玩家做活动相关动作]
   击杀怪物 → fire WatcherTriggerType.TRIGGER_KILL_MONSTER
   获得物品 → fire TRIGGER_OBTAIN_MATERIAL_NUM
   完成任务 → fire TRIGGER_FINISH_QUEST
   ↓ 每个 watcher 按自己关心的 trigger 更新进度
   ↓ 进度满 → 标 finished
   ↓ ActivityHandler 自定义后续逻辑（领奖等）

[活动结束时]
   按 endTime 字段，server 不再接受新进度
   玩家进度仍保留（PlayerActivityData 继续存在）
   只是 condition 失效
```

---

## 10. Watcher 与 Quest cond 系统的对比

| 维度 | Quest Cond/Content | Activity Watcher | BattlePass Mission |
|---|---|---|---|
| 触发源 | queueEvent(QuestCond/Content, ...) | trigger(WatcherTriggerType, ...) | trigger(WatcherTriggerType, ...) |
| 类型枚举 | ~30 + 60 + 60 (cond/content/exec) | ~150 (WatcherTriggerType) | 同左 |
| 进度数据 | finishProgress[] in SubQuest | WatcherInfo per activity | BattlePassMission |
| 注解 | @QuestValueCond/Content/Exec | @ActivityWatcherType | (反射 + WatcherTriggerType) |
| 设计目标 | 任务流程 (剧情有序) | 限时活动 (统计) | 长期成长 (统计 + 等级) |

→ **Watcher 是统计型，Quest 是流程型**。同一个动作（如击杀怪物）会同时触发：
- Quest's `QUEST_CONTENT_KILL_MONSTER` (任务流推进)
- BattlePass's `TRIGGER_KILL_MONSTER` (战令进度)
- Activity's `TRIGGER_KILL_MONSTER` (活动进度)

**三个独立系统从同一个事件源接事件**——这就是为什么 `WatcherTriggerType` 能涨到 ~150 个。

---

## 11. 关键设计：插件式架构的取舍

### 11.1 优点

1. **新活动开发零侵入**：不动核心代码，只加新的 handler 子类
2. **每个活动独立持久化**：PlayerActivityData 多态 detail 各活动互不干扰
3. **活动专用货币复用经济系统**：通过 `VirtualCurrencyHandler` 接口注册到 `Inventory.addVirtualItem`
4. **活动跑完直接禁用**：endTime 一过就 condition 失效，不需要清理

### 11.2 代价

1. **每个活动都是独立 spike**：没有共享的"活动模板"——简单签到也要写 Handler
2. **DefaultActivityHandler 兜底但弱**：很多活动用默认实现就只是"显示活动入口"，没真实功能
3. **Proto 类型爆炸**：每个活动有 N 种 detail proto（`AsterLittleDetailInfo`、`AsterMidDetailInfo`...）

→ **典型的"灵活但难标准化"的架构权衡**。新活动质量取决于开发者的实现深度。

---

## 12. 给做"限时活动系统"开发者的提炼

1. **插件化是必须**——你不知道未来会出多少种活动玩法
2. **共享 watcher 系统而非 quest 系统**——活动是"统计型"，不是"剧情型"
3. **活动专用货币用 VirtualCurrency 接口**——别另起一套货币系统
4. **PlayerActivityData 用多态 detail**——避免每个活动都改 player schema
5. **condition 与 watcher 分离**——condition 决定"能不能做"，watcher 决定"做到哪了"
6. **DefaultHandler 兜底**——没特殊实现的活动也能至少显示入口
7. **配表里写 begin/end time**——活动自动启用禁用，不靠运维手动开关
8. **WatcherTriggerType 就是事件总线**——任何"统计型成就/任务/活动"都能 hook 进去

---

## 13. 数据规模感

* 活动子类型实现：6 个有专门 handler，N 个用 default
* WatcherTriggerType 枚举：~150 个值
* ActivityCondition handler：~10 个
* PlayerActivityData 一般每玩家 < 50 个活动数据
* Aster 活动专用货币 itemId：109、110

代码规模：
- `ActivityHandler.java`：147 行（基类）
- `ActivityManager.java`：200+ 行
- 各活动子类：50-300 行/个
- WatcherTriggerType 枚举：337 行
- 总核心：~2270 行（不含每个活动特定 proto）

---

## 14. 7 次架构同构汇总（截至 notes/20）

| 子系统 | Handler 注解 | 异步池 | 备注 |
|---|---|---|---|
| Quest | `@QuestValueCond/Content/Exec` | 4 线程 | notes/02 |
| Scene Script | (反射注册) | 4 线程 | notes/14 |
| Talk | (前端权威) | -- | notes/08 |
| Ability | `@AbilityAction` | 4 线程 | notes/16 |
| Codex | (寄生设计) | -- | notes/17 |
| Dungeon | `@DungeonValue` / `@ChallengeTypeValue` | -- | notes/19 |
| **Activity** | **`@GameActivity` / `@ActivityWatcherType`** | -- | **notes/20** |

→ "**注解 + 反射 + 自动注册**"在 7 个子系统里复用。**Grasscutter 工程师用同一种语言描述所有系统**——这是大型项目的最佳实践。

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/activity/ActivityHandler.java` (基类 147 行)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/activity/ActivityManager.java` (注册器 200+ 行)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/activity/ActivityWatcher.java` 与 `WatcherTriggerType.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/activity/condition/` (10+ condition handler)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/activity/aster/` (风花节实现)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/activity/dragonspine/` (龙脊雪山)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/activity/irodori/` (五彩之旅)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/activity/musicgame/` (音游)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/activity/summer_time_2_8/` (金苹果群岛)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/activity/trialavatar/` (试用角色)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/props/WatcherTriggerType.java` (337 行枚举)
