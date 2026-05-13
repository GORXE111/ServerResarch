# Activity 限时活动运行时引擎深度剖析

> 第 49 篇：notes/20 讲了"插件式架构"的设计，这一篇填补**运行时引擎**：ActivityManager 255 行 + 39 个 activity 文件 + **141 ActivityType** + 5 子目录 + 14+ Condition handler，是 grasscutter 中**最长尾**的子系统。

---

## 0. 为什么这一篇重要

前 48 篇里 Activity 反复出现但 runtime 没专门挖：
- notes/20 Activity 系统：讲了"插件式架构 + WatcherTriggerType 跨系统事件总线"
- notes/40 Player Manager：`activityManager` 是 25 之一
- notes/41 事件总线：WatcherTriggerType 299 类 (BattlePass + Activity 共用)
- notes/48 副本：`DUNGEON_ACTIVITY` 类型 + TrialAvatarActivityHandler

但**Activity 内部怎么跑？ActivityHandler 子类怎么注册？为什么 141 个 ActivityType？**——这一篇统一回答。

---

## 1. Activity 体系全图

```
┌─────────────────────────────────────────────────────────────────┐
│  ActivityManager (per Player, 255 行)                             │
│  - playerActivityDataMap (per activity)                           │
│  - conditionExecutor                                               │
│  - triggerWatcher / triggerActivityConditions                     │
│  - 4 种时间状态判断 (Active/Open/Ended/Closed)                     │
└────────────────────────────┬────────────────────────────────────┘
                             │ 静态共享
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│  activityConfigItemMap (static, JVM 级共享)                       │
│  - Map<activityId, ActivityConfigItem>                           │
│  - 反射加载 @GameActivity + @ActivityWatcherType                  │
└────────────────────────────┬────────────────────────────────────┘
                             │ 实例化
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│  ActivityHandler 子类树                                            │
│  - DefaultActivityHandler (默认)                                   │
│  - AsterActivityHandler (海岛 1.1)                                │
│  - DragonspineActivityHandler (雪山)                              │
│  - IrodoriActivityHandler (彩之祭典)                              │
│  - MusicGameActivityHandler (音游)                                │
│  - SummerTime28ActivityHandler (海岛 2.8)                         │
│  - TrialAvatarActivityHandler (试用)                              │
│  ... 按 @GameActivity 注解注册                                     │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│  PlayerActivityData (per Player + per Activity, 持久化)            │
│  - @Entity "activities" collection (notes/30)                      │
│  - activityId, uid, bannerCleared, ...                            │
│  - 各子活动自己的状态字段                                           │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│  Condition 系统 (condition/all/ × 14+)                            │
│  - DayLess / DaysGreatEqual                                       │
│  - FinishWatcher                                                  │
│  - PlayerLevelGreatEqual                                          │
│  - QuestFinishAllowQuickOpen                                      │
│  - NotFinishTalk                                                  │
│  ...                                                              │
└─────────────────────────────────────────────────────────────────┘
```

→ **39 个文件**支撑活动系统 —— 是 grasscutter 中**逻辑最分散**的子系统。

---

## 2. ActivityType：141 个枚举

`ActivityType.java` —— **141 个**活动类型枚举！

### 2.1 ID 段位划分

```java
NONE(0)
NEW_ACTIVITY_SEA_LAMP(1)          // 海灯节
NEW_ACTIVITY_CRUCIBLE(2)           // 试炼
NEW_ACTIVITY_SALESMAN(3)           // 商人
NEW_ACTIVITY_TRIAL_AVATAR(4)       // 试用角色
NEW_ACTIVITY_SIGNIN(5)             // 签到
NEW_ACTIVITY_BONUS(6)              // 奖励
NEW_ACTIVITY_NEWBEEBONUS(7)        // 新手奖励
NEW_ACTIVITY_PERSONAL_LIINE(8)     // 个人剧情线
NEW_ACTIVITY_DELIVERY(9)           // 派送
NEW_ACTIVITY_FLIGHT(10)            // 飞行
NEW_ACTIVITY_TEMP(99)              // 临时
NEW_ACTIVITY_ASTER(1100)           // 海岛 1.1 (Aster)
NEW_ACTIVITY_DRAGONSPINE(1200)     // 雪山
NEW_ACTIVITY_REUNION(1201)         // 回归奖励
NEW_ACTIVITY_EFFIGY(1202)          // 突破任务
// ... 共 141 个
```

### 2.2 命名规律

- `NEW_ACTIVITY_*` —— 旧版本前缀（早期所有活动都加 NEW）
- 1-100 段：基础活动机制
- 1100+ 段：版本特定活动（每版本一组）

→ **每版本几个新活动** × 5 年 = 141 个枚举。这是 mihoyo "每版本必加新玩法"的痕迹。

### 2.3 grasscutter 实现情况

实际有 ActivityHandler 实现的（仅 6-7 个）：
- Default (兜底)
- Aster (海岛 1.1)
- Dragonspine (雪山)
- Irodori (彩之祭典)
- MusicGame (音游)
- SummerTime28 (海岛 2.8)
- TrialAvatar (试用)

→ **141 类 vs 7 实现** = **5% 实现率** —— 大量活动**走 DefaultActivityHandler 兜底**。
→ 这是为什么 grasscutter 私服**活动玩不完整**——很多活动有数据但没逻辑代码。

---

## 3. ActivityManager 静态初始化（反射注册）

`ActivityManager.java:32-82`：
```java
static {
    activityConfigItemMap = new HashMap<>();
    scheduleActivityConfigMap = new HashMap<>();
    loadActivityConfigData();
}

private static void loadActivityConfigData() {
    // 1. 反射扫描所有 ActivityHandler 子类
    var activityHandlerTypeMap = new HashMap<ActivityType, ConstructorAccess<?>>();
    var activityWatcherTypeMap = new HashMap<WatcherTriggerType, ConstructorAccess<?>>();
    var reflections = new Reflections(ActivityManager.class.getPackage().getName());
    
    // @GameActivity 注解的 ActivityHandler
    reflections.getSubTypesOf(ActivityHandler.class).forEach(item -> {
        var typeName = item.getAnnotation(GameActivity.class);
        activityHandlerTypeMap.put(typeName.value(), ConstructorAccess.get(item));
        //                          ↑ reflectasm 优化的构造器访问
    });
    
    // @ActivityWatcherType 注解的 ActivityWatcher
    reflections.getSubTypesOf(ActivityWatcher.class).forEach(item -> {
        var typeName = item.getAnnotation(ActivityWatcherType.class);
        activityWatcherTypeMap.put(typeName.value(), ConstructorAccess.get(item));
    });
    
    // 2. 加载 ActivityConfig.json
    DataLoader.loadList("ActivityConfig.json", ActivityConfigItem.class).forEach(item -> {
        item.onLoad();
        var activityData = GameData.getActivityDataMap().get(item.getActivityId());
        if (activityData == null) {
            Grasscutter.getLogger().warn("activity {} not exist.", item.getActivityId());
            return;
        }
        
        // 3. 按 ActivityType 找 Handler (没有则用 Default)
        var activityHandlerType = activityHandlerTypeMap.get(
            ActivityType.getTypeByName(activityData.getActivityType()));
        
        ActivityHandler activityHandler;
        if (activityHandlerType != null) {
            activityHandler = (ActivityHandler) activityHandlerType.newInstance();
        } else {
            activityHandler = new DefaultActivityHandler();   // ★ Fallback
        }
        
        activityHandler.setActivityConfigItem(item);
        activityHandler.initWatchers(activityWatcherTypeMap);
        item.setActivityHandler(activityHandler);
        
        activityConfigItemMap.putIfAbsent(item.getActivityId(), item);
        scheduleActivityConfigMap.putIfAbsent(item.getScheduleId(), item);
    });
    
    Grasscutter.getLogger().info("Enable {} activities.", activityConfigItemMap.size());
}
```

### 3.1 第 15 次"注解+反射"模式

- `@GameActivity(ActivityType.NEW_ACTIVITY_TRIAL_AVATAR)` 标注 Handler
- `@ActivityWatcherType(TRIGGER_MONSTER_DIE)` 标注 Watcher
- 反射扫描自动注册

→ Activity 系统**自带 2 套反射注册**：Handler + Watcher。这是 grasscutter 第 15 次出现这种模式。

### 3.2 ConstructorAccess (reflectasm)

```java
ConstructorAccess.get(item)   // ← ★ reflectasm 库
```

→ **reflectasm 是高性能反射库**——比 JDK 反射快 5-10 倍。
→ Activity 加载时每个 handler 都通过它实例化——但**只在初始化时调一次**，反射性能不那么关键。

### 3.3 Fallback 到 DefaultActivityHandler

```java
if (activityHandlerType != null) {
    activityHandler = (ActivityHandler) activityHandlerType.newInstance();
} else {
    activityHandler = new DefaultActivityHandler();   // ★ 兜底
}
```

→ 这就是为什么"141 个 ActivityType 但只有 7 个 Handler"还能运行——**剩下的全用 DefaultActivityHandler**。

---

## 4. PlayerActivityData：持久化每玩家每活动

```java
@Entity("activities")
public class PlayerActivityData {
    @Id private ObjectId id;
    @Indexed private int uid;
    private int activityId;
    
    private Map<Integer, Boolean> bannerCleared;
    
    private transient Player player;
    private transient ActivityHandler activityHandler;
    
    // 各子活动用自己的 字段 (通过子类 / map 存)
}
```

### 4.1 复合索引

```java
@Indexed private int uid;
private int activityId;
```

→ 查找语句（DatabaseHelper.getPlayerActivityData）：
```java
.filter(Filters.and(Filters.eq("uid", uid), Filters.eq("activityId", activityId)))
```

→ `uid` 单字段索引足够（每玩家活动数 < 100，遍历过滤快）。

### 4.2 注入引用

```java
data.setPlayer(player);
data.setActivityHandler(activityHandler);
activityHandler.initCurrencyHandlers(data);
```

→ Player 反序列化后**注入运行时引用**——transient 字段补回。

---

## 5. ActivityHandler：抽象基类（146 行）

```java
public abstract class ActivityHandler<T extends PlayerActivityData> {
    @Getter @Setter protected ActivityConfigItem activityConfigItem;
    @Getter protected Map<WatcherTriggerType, List<ActivityWatcher>> watchersMap;
    
    // 模板方法
    public abstract PlayerActivityData initPlayerActivityData(Player player);
    
    public void initWatchers(Map<WatcherTriggerType, ConstructorAccess<?>> watcherTypeMap) {
        watchersMap = new HashMap<>();
        // 遍历配置 + 实例化 Watcher
        activityConfigItem.getWatcherDataList().forEach(watcherData -> {
            var triggerType = ActivityType.getWatcherTriggerType(watcherData);
            var watcherAccess = watcherTypeMap.get(triggerType);
            
            ActivityWatcher watcher = (watcherAccess != null) ? 
                (ActivityWatcher) watcherAccess.newInstance() : new DefaultWatcher();
            
            watcher.setActivityHandler(this);
            watcher.setMetadata(watcherData);
            watchersMap.computeIfAbsent(triggerType, k -> new ArrayList<>()).add(watcher);
        });
    }
    
    public void triggerCondEvents(Player player) { ... }
    public void initCurrencyHandlers(PlayerActivityData data) { ... }
    public abstract ActivityInfo toProto(T data, ActivityConditionExecutor executor);
    public boolean isBannerCondMeet(T data, int scheduleId) { return true; }
    public void onLoadScene(Scene scene, Player player, ActivityConfigItem item) { }
}
```

### 5.1 ActivityHandler vs ActivityWatcher 区别

```
ActivityHandler:
   - per Activity (一种活动一个 Handler 实例)
   - 持有: activityConfigItem + watchersMap
   - 生命周期方法 (initData / onLoadScene / triggerCondEvents)
   
ActivityWatcher:
   - per WatcherTriggerType per Activity
   - 监听 WatcherTriggerType (杀怪/获得物品/...)
   - trigger 方法 (从 ActivityManager 派发)
```

**类比**：Handler = Activity 控制器，Watcher = 监听器。

---

## 6. triggerWatcher：事件路由（核心）

```java
public void triggerWatcher(WatcherTriggerType watcherTriggerType, String... params) {
    var watchers = activityConfigItemMap.values().stream()
        .map(ActivityConfigItem::getActivityHandler)
        .filter(Objects::nonNull)
        .map(ActivityHandler::getWatchersMap)
        .map(map -> map.get(watcherTriggerType))      // 按事件类型过滤
        .filter(Objects::nonNull)
        .flatMap(Collection::stream)
        .toList();
    
    watchers.forEach(watcher -> watcher.trigger(
        playerActivityDataMap.get(watcher.getActivityHandler().getActivityConfigItem().getActivityId()),
        params));
}
```

### 6.1 流程

```
[业务代码] player.getActivityManager().triggerWatcher(TRIGGER_MONSTER_DIE, "21010101", "1")
    ↓
[ActivityManager] 
   遍历所有活动 → 找到监听 TRIGGER_MONSTER_DIE 的 Watcher
    ↓
[Watcher.trigger]
   按自己的逻辑累计进度
   保存 PlayerActivityData
```

→ 这是 **WatcherTriggerType 299 类**（notes/41）→ Activity 系统的具体路由实现。

### 6.2 与 BattlePass 共用 WatcherTriggerType

```java
// notes/22 BattlePass
player.getBattlePassManager().triggerMission(TRIGGER_MONSTER_DIE, monsterId, 1);

// notes/49 Activity
player.getActivityManager().triggerWatcher(TRIGGER_MONSTER_DIE, monsterId, 1);
```

→ **BattlePass 和 Activity 共享 WatcherTriggerType** —— 业务代码每次触发都要**双路通知**。

---

## 7. 4 种时间状态判断

```java
public boolean isActivityActive(int activityId) {
    var now = new Date();
    return now.after(activityConfig.getBeginTime()) && now.before(activityConfig.getEndTime());
}

public boolean hasActivityEnded(int activityId) {
    return new Date().after(activityConfig.getEndTime());
}

public boolean isActivityOpen(int activityId) {
    var now = new Date();
    return now.after(activityConfig.getOpenTime()) && now.before(activityConfig.getCloseTime());
}

public boolean isActivityClosed(int activityId) {
    var now = new Date();
    return now.after(activityConfig.getCloseTime());
}

public int getOpenDay(int activityId) {
    val now = new Date();
    return (int) TimeUnit.DAYS.convert(now.getTime() - activityConfig.getOpenTime().getTime(), TimeUnit.MILLISECONDS) + 1;
}
```

### 7.1 4 个时间点

```
beginTime ──── openTime ────────── closeTime ──── endTime
   ↑              ↑                    ↑              ↑
 活动激活       玩家可参与            玩家不可参与     活动结束领奖
   ↓              ↓                    ↓              ↓
 isActive=true  isOpen=true         isOpen=false   hasEnded=true
```

**4 个时间段语义**：
- **Active**：begin → end —— 活动在 banner 中显示
- **Open**：open → close —— 玩家可玩
- **Ended**：> end —— 活动入口消失
- **Closed**：> close —— 不能玩但可能还能领奖

→ "**banner 显示但已停止参与**"是常见场景（如海灯节展示倒计时但不让玩了）。

### 7.2 getOpenDay：第 N 天

```java
return (int) TimeUnit.DAYS.convert(now - openTime, MILLISECONDS) + 1;
```

→ "**活动第 N 天**" —— 用于"每日签到"、"每日新关卡"机制。

---

## 8. ActivityCondition 系统（14+ 类）

`condition/all/` 目录有 14+ 个 ActivityCondition 子类：
```
DayLess.java                          // 第 N 天之前
DaysGreatEqual.java                    // 第 N 天之后
FinishWatcher.java                     // 完成某 watcher
PlayerLevelGreatEqual.java             // 玩家等级 >=
QuestFinishAllowQuickOpen.java         // 完成任务允许快速开
NotFinishTalk.java                     // 未完成对话
ScheduleStart.java                     // 计划开始
... 等
```

### 8.1 ConditionExecutor

```java
conditionExecutor = new BasicActivityConditionExecutor(
    activityConfigItemMap,
    GameData.getActivityCondExcelConfigDataMap(),
    PlayerActivityDataMappingBuilder.buildPlayerActivityDataByActivityCondId(playerActivityDataMap),
    AllActivityConditionBuilder.buildActivityConditions());
```

**4 个输入**：
- 活动配置 map
- 活动条件 Excel 配置
- 活动条件 ID → PlayerActivityData 映射
- 所有 ActivityCondition 实现

→ 类似 Quest 的 cond/content 系统，但**独立的注册体系**。

### 8.2 meetsCondition

```java
public boolean meetsCondition(int activityCondId) {
    return conditionExecutor.meetsCondition(activityCondId);
}

public void triggerActivityConditions() {
    activityConfigItemMap.forEach((k, v) -> {
        v.getActivityHandler().triggerCondEvents(player);
    });
}
```

→ 玩家登录时 `triggerActivityConditions()` 批量检查所有活动条件——可能解锁新阶段。

---

## 9. 7 个子目录的 ActivityHandler 实现

### 9.1 TrialAvatarActivityHandler

```java
@GameActivity(ActivityType.NEW_ACTIVITY_TRIAL_AVATAR)
public class TrialAvatarActivityHandler extends ActivityHandler<TrialAvatarPlayerData> {
    public List<Integer> getBattleAvatarsList() { ... }
    public boolean canEnterTrialDungeon(int dungeonId) { ... }
    public void onSettleTrialDungeon(...) { ... }
}
```

→ notes/48 副本系统调用：
```java
player.getActivityManager()
    .getActivityHandlerAs(NEW_ACTIVITY_TRIAL_AVATAR, TrialAvatarActivityHandler.class)
    .map(TrialAvatarActivityHandler::getBattleAvatarsList)
    .ifPresent(battleAvatars -> player.addTrialAvatarsForDungeon(...));
```

### 9.2 MusicGameActivityHandler

```java
@GameActivity(ActivityType.NEW_ACTIVITY_MUSIC_GAME)
public class MusicGameActivityHandler extends ActivityHandler<MusicGamePlayerData> {
    public void onSettleMusicGame(int level, int score, ...) {
        // 记录最高分
        // UGC 谱面分享
    }
}
```

→ 音游有自己专属的 `MusicGameBeatmap` collection (notes/30)——玩家可创建谱面分享。

### 9.3 AsterActivityHandler / DragonspineActivityHandler / IrodoriActivityHandler / SummerTime28ActivityHandler

每个**版本主题活动**一个 Handler——内部包含**该活动的所有特定逻辑**：
- Aster：海岛 1.1（飞行/采集）
- Dragonspine：雪山（寒气/温泉）
- Irodori：彩之祭典（花朵组合）
- SummerTime28：海岛 2.8（浪船/章鱼）

→ 每个**重大活动版本**有自己的 Handler 子目录——这种**长尾分散**是 Activity 系统的特点。

### 9.4 grasscutter 的实现完整度

```bash
$ ls activity/*/  | wc -l
6  # aster + dragonspine + irodori + musicgame + summer_time_2_8 + trialavatar
```

**只有 6 个版本主题活动**有专门实现。
其余 100+ 活动 → DefaultActivityHandler 兜底（**只读配置，无逻辑**）。

→ 私服活动**玩不完整**的根源 —— 米哈游正服活动逻辑**没全开源**。

---

## 10. checkAndNotifyActivityBanner：横幅通知

```java
public void onLogin() {
    activityConfigItemMap.values().forEach(item ->
        checkAndNotifyActivityBanner(item.getActivityId(), item.getScheduleId())
    );
}

public void checkAndNotifyActivityBanner(int activityId, int scheduleId) {
    var activityHandler = activityConfigItemMap.get(activityId).getActivityHandler();
    var activityData = playerActivityDataMap.get(activityId);
    
    if (activityHandler.isBannerCondMeet(activityData, scheduleId) && 
        !activityData.isBannerCleared(scheduleId)) {
        player.sendPacket(new PacketActivityBannerNotify(activityId, scheduleId));
    }
}

public boolean setBannerCleared(int activityId, int scheduleId) {
    var activityData = playerActivityDataMap.get(activityId);
    if (activityData.isBannerCleared(scheduleId)) return false;
    
    activityData.setBannerCleared(scheduleId);
    activityData.save();
    return true;
}
```

→ 玩家登录看到的"**红点 / 新活动通知**"机制：
- 每个活动有 `bannerCleared` 状态
- 玩家点开活动 → setBannerCleared
- 下次登录不再提示

---

## 11. triggerSceneLoadForActiveActivity：场景级钩子

```java
public void triggerSceneLoadForActiveActivity(Scene scene) {
    getActiveActivityIds().forEach(activityId -> {
        val activityConfig = activityConfigItemMap.get(activityId);
        val activityHandler = activityConfig.getActivityHandler();
        activityHandler.onLoadScene(scene, player, activityConfig);
    });
}
```

`Scene.<init>` 调用（notes/35）：
```java
this.scriptManager = new SceneScriptManager(this);
getWorld().getHost().getActivityManager().triggerSceneLoadForActiveActivity(this);
```

→ **每次进场景**通知所有活跃活动 —— Handler 可注入场景特定逻辑（如海岛活动给玩家自动飞翔器）。

---

## 12. 完整 Activity 时序

```
[服务器启动]
ActivityManager 静态块:
   1. 反射扫描 @GameActivity / @ActivityWatcherType
   2. 加载 ActivityConfig.json
   3. 每个活动: 找对应 Handler (或 Default) + 初始化 Watchers
   4. 注册到 activityConfigItemMap

[玩家登录]
new ActivityManager(player):
   1. 遍历 activityConfigItemMap
   2. 每个活动:
      - PlayerActivityData.getByPlayer(uid, activityId) (从 DB 拿)
      - 没有? 调 activityHandler.initPlayerActivityData(player)
      - 注入 player + activityHandler 引用
      - initCurrencyHandlers
   3. 发 PacketActivityScheduleInfoNotify
   4. 创建 ConditionExecutor

[玩家进入场景]
Scene.<init>:
   triggerSceneLoadForActiveActivity:
      所有 active activity → onLoadScene 钩子

[玩家行为 (杀怪/获得物品/...)]
业务代码:
   player.getActivityManager().triggerWatcher(TRIGGER_XXX, params)
   ↓
ActivityManager.triggerWatcher:
   找到所有监听 TRIGGER_XXX 的 Watcher
   每个 Watcher.trigger(playerActivityData, params)
   ↓
Watcher 累计进度 + save PlayerActivityData

[玩家完成活动条件]
某 Handler.triggerCondEvents 或 检查 ConditionExecutor.meetsCondition:
   - 解锁下一阶段
   - 发奖励 (inventory.addItem)
   - 更新进度 packet

[活动结束]
endTime 后:
   - banner 消失
   - 入口关闭
   - 玩家可能还能领奖 (closeTime 之前)

[新版本上线]
旧活动数据保留 (PlayerActivityData 在 DB)
新活动加入 activityConfigItemMap
玩家登录时为新活动 initPlayerActivityData
```

→ **活动全生命周期**：服务器启动反射注册 → 玩家登录加载 → 进场景钩子 → 行为触发 watcher → 完成条件 → 结束。

---

## 13. 与 BattlePass / Quest 的对比

| 维度 | Activity | BattlePass | Quest |
|---|---|---|---|
| 数量 | 141 ActivityType | 1 个 BattlePass | 2360 MainQuest |
| 实现方式 | 反射 + 子类 | 单 Manager | 注解反射 handler |
| 持久化 | PlayerActivityData (per activity) | BattlePassManager (per player) | GameMainQuest (per parent) |
| 事件源 | WatcherTriggerType + ActivityCondition | WatcherTriggerType | QuestContent + QuestCond |
| 限时 | begin/end/open/close 4 时间 | scheduleId 阶段 | 永久 |
| 兜底 | DefaultActivityHandler | - | - |
| 实现完整度 | 6/141 (~4%) | 高 | 高 |

→ **Activity 是 3 者中实现率最低**——长尾且每个活动逻辑独特。

---

## 14. 设计模式总结

### 14.1 第 15+ 次"注解+反射"

```
@GameActivity(ActivityType.XXX)
@ActivityWatcherType(WatcherTriggerType.XXX)
```

→ 加新活动**写一个 Handler + 加注解** = 零改动注册逻辑。

### 14.2 Fallback 兜底

```
Handler 没实现? → DefaultActivityHandler
Watcher 没实现? → DefaultWatcher
```

→ 这就是 141 个 ActivityType 但**只 6 个实现**还能运行的原因。

### 14.3 ConstructorAccess (reflectasm) 优化

```java
ConstructorAccess.get(item).newInstance()
```

→ 比 `Class.newInstance()` 快 5-10 倍 —— 启动时实例化大量 Handler 用得着。

### 14.4 双索引（id + scheduleId）

```java
activityConfigItemMap.putIfAbsent(item.getActivityId(), item);
scheduleActivityConfigMap.putIfAbsent(item.getScheduleId(), item);
```

→ 按活动 ID 和计划 ID **双索引** —— 不同入口查询方便。

### 14.5 注入式 transient

```java
data.setPlayer(player);
data.setActivityHandler(activityHandler);
```

→ DB 反序列化后**注入运行时引用** —— 类似 notes/30 Player 的模式。

---

## 15. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 客户端发"我完成了活动 X" | ✗ 服务器检查时间 + 进度 |
| 篡改 PlayerActivityData | ✗ 服务器内存 |
| 用过期活动奖励 | ✗ closeTime 服务器算 |
| 加速时间 | ✗ Date() 是服务器时间 |
| 飞翔器没解锁去活动场景 | 部分有效 (位置在客户端) |

→ Activity 系统**时间和进度反作弊较强**——核心在服务器算时间。

---

## 16. 关键收获

1. **141 个 ActivityType 枚举** —— grasscutter 最大的活动类型枚举
2. **39 个 activity 文件**支撑 —— 长尾但分散
3. **第 15+ 次"注解+反射"模式**：`@GameActivity` + `@ActivityWatcherType`
4. **ActivityHandler 子类树**：DefaultActivityHandler + 6 个版本主题 (Aster/Dragonspine/Irodori/MusicGame/SummerTime28/TrialAvatar)
5. **实现率仅 5%**：141 ActivityType vs 7 Handler —— 大量走 Default 兜底
6. **PlayerActivityData per Player per Activity** 持久化到 `activities` collection
7. **4 种时间状态**：Active (begin/end) / Open (open/close) / Ended (>end) / Closed (>close)
8. **getOpenDay 第 N 天**：用于每日签到 / 每日新关卡
9. **14+ ActivityCondition 子类**：DayLess / DaysGreatEqual / FinishWatcher / PlayerLevelGreatEqual / QuestFinishAllowQuickOpen / NotFinishTalk 等
10. **triggerWatcher 共享 WatcherTriggerType**：与 BattlePass 共用，**业务代码双路通知**
11. **ConstructorAccess (reflectasm) 优化**：比 JDK 反射快 5-10 倍
12. **Fallback 兜底**：未实现的活动用 DefaultActivityHandler/DefaultWatcher
13. **checkAndNotifyActivityBanner**：banner 红点机制
14. **triggerSceneLoadForActiveActivity**：进场景给所有活跃活动钩子
15. **三大系统对比 (Activity/BattlePass/Quest)**：Activity 是 3 者中实现率最低（长尾分散）
16. **Activity 是 grasscutter 最长尾子系统**：每版本新活动堆积导致 141 类型 + 5% 实现

---

## 17. 一句话总结

> **Activity 系统 = grasscutter 最长尾的子系统; 39 文件 + 141 ActivityType + 6 版本主题 Handler 实现 + 14+ Condition; 反射注册 (@GameActivity + @ActivityWatcherType) + ConstructorAccess 性能优化 + DefaultActivityHandler 兜底; 4 种时间状态 (Active/Open/Ended/Closed) + 第 N 天计算; 共享 WatcherTriggerType 但独立 Condition 子系统; 与 BattlePass 双路通知; 实现率仅 5% 因每版本必加新活动堆积。**
> 
> **设计哲学: 长尾兼容性优先——加新活动写 Handler 子类 + 注解 = 零代码改动注册逻辑, DefaultActivityHandler 兜底让未实现的活动也能加载, 反射 + 数据驱动到极致——这是"开源私服跟不上正服更新节奏"的根本机制。**

---

**前置笔记**：
- notes/20 Activity 系统 (设计层)
- notes/27 架构模式 - 注解反射模式
- notes/30 持久化 - activities collection
- notes/40 Player Manager - activityManager 是 25 之一
- notes/41 事件总线 - WatcherTriggerType 共享
- notes/48 副本 - DUNGEON_ACTIVITY + TrialAvatar 集成

**关联文件**：
- `ActivityManager.java`(255) - per Player 协调
- `ActivityHandler.java`(146) - 抽象基类
- `GameActivity.java`(14) - 注解定义
- `ActivityWatcherType.java`(14) - 注解定义
- `ActivityWatcher.java`(26) - 监听器基类
- `PlayerActivityData.java`(154) - 持久化数据
- `ActivityType.java` - 141 枚举
- 6 子目录 (aster/dragonspine/irodori/musicgame/summer_time_2_8/trialavatar)
- `condition/` 14+ ActivityCondition

**研究的源代码**: 595 行 ActivityManager 核心 + 39 个文件结构 + 141 ActivityType 枚举梳理。
