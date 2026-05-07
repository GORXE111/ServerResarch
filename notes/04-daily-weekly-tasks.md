# 04 · 每日委托 / 每周任务

## TL;DR

- **每日委托不是独立系统**，是寄生在主任务引擎上的薄壳
- **原神严格意义上没有"每周任务"**，只有"每周计数器重置"

---

## 每日委托：薄壳设计

### 数据结构出奇地简单

```java
// data/excels/DailyTaskData.java
public class DailyTaskData {
    private int id;
    private int cityId;          // 璃月/蒙德/稻妻/...
    private int poolId;
    private int finishProgress;  // 完成阈值
    private int taskRewardId;    // 奖励 id
}
```

**只有 5 个字段**——对比 SubQuestData 的 30+ 字段。Daily 自己**没有状态机、没有 cond/exec、没有 acceptCond**。

那它怎么"完成"？答案：**复用主任务引擎**。

### 桥接机制

```
                    ┌─────────────────────────┐
                    │   DailyTaskManager      │
                    │   (currentTasks: 4 个)  │
                    └────────┬────────┬───────┘
                             │        │
              QUEST_COND_DAILY_TASK_START (前向桥)
                             ↓        ↑  EXEC_NOTIFY_DAILY_TASK (反向桥)
                    ┌────────────────────┐
                    │   QuestSystem      │   ← 复用主任务引擎
                    │   (跑常规 SubQuest) │
                    └────────────────────┘
```

### 前向桥：daily → quest

```java
// DailyTaskManager.randomizeTasks() (凌晨刷新时调用)
this.currentTasks.forEach(task ->
    this.player.getQuestManager().queueEvent(QuestCond.QUEST_COND_DAILY_TASK_START, task));
```

每个 daily task 在配表里有**对应的影子 SubQuest**，acceptCond 长这样：

```json
{ "type": "QUEST_COND_DAILY_TASK_START", "param": [<dailyTaskId>] }
```

`ConditionDailyTaskStart.java:11` 验证：

```java
return owner.getDailyTaskManager().getCurrentTasks().contains(taskId);
```

→ 任务接取后，**就是一个普通 SubQuest**——有自己的 finishCond、finishExec、进度数组。

### 反向桥：quest → daily

影子 SubQuest 的 `finishExec` 里包含一条：

```json
{ "type": "QUEST_EXEC_NOTIFY_DAILY_TASK", "param": ["<dailyTaskId>"] }
```

`ExecNotifyDailyTask.java:13`：

```java
quest.getOwner().getDailyTaskManager().finishTask(taskId);
```

→ daily 标记完成 + 加 legendaryKeyDailyTasks 计数 + 发 PacketDailyTaskProgressNotify。

### 这设计的天才之处

Daily 自己几乎什么都不写，所有"杀 5 只史莱姆"、"和某 NPC 说话"的逻辑全部用现成的 quest cond/content 实现。**新增一种 daily 不需要写任何代码**，只配一个新的影子 SubQuest 即可。

---

## Daily 随机化：4 个/天，按城市过滤

```java
// DailyTaskManager.randomizeTasks
public void randomizeTasks() {
    // 1. 清掉昨天的（包括从玩家 quest 列表里删除残留的影子 SubQuest）
    finishedCurrentTasks.clear();

    // 2. 过滤候选（城市过滤 + 只选已解锁城市的）
    var taskList = GameData.getDailyTaskDataMap().values().stream()
        .filter(task -> cityFilter == 0 || task.getCityId() == cityFilter)
        .filter(task -> unlockedCities.contains(task.getCityId()))
        .toList();

    // 3. shuffle
    Collections.shuffle(taskList);

    // 4. 取前 4 个
    this.currentTasks = taskList.subList(0, 4)...

    // 5. 通过桥事件把 4 个影子 SubQuest 激活
    this.currentTasks.forEach(task ->
        queueEvent(QUEST_COND_DAILY_TASK_START, task));
}
```

**城市解锁**靠 `checkForCityUnlock`——某些主线 SubQuest 完成时会解锁城市的 daily 池（`CityTaskOpenExcelConfigData.json`）。

---

## Daily Task Vars（独立于 Quest Vars）

Daily 自己也有变量系统，**和 MainQuest 的 questVars 是分开的**：

```java
// DailyTaskManager.java:40
private Map<Integer, List<Integer>> taskVars;  // taskId → 变量数组

private void triggerTaskVarAction(int taskId, int index, int value) {
    queueEvent(QuestCond.QUEST_COND_DAILY_TASK_VAR_EQ, taskId, index, value);
    queueEvent(QuestCond.QUEST_COND_DAILY_TASK_VAR_GT, taskId, index, value);
    queueEvent(QuestCond.QUEST_COND_DAILY_TASK_VAR_LT, taskId, index, value);
}
```

为什么独立？因为 daily 需要**每天清零变量**，混在 questVars 里会污染主线进度。

---

## 每日刷新：懒检查，不是 cron

这是另一个值得偷师的设计——**没有定时任务、没有 cron、没有 scheduled job**。

```java
// Player.java:1262 每个玩家 onTick 里
this.doDailyReset();

// Player.java:1293
private synchronized void doDailyReset() {
    var currentDate    = LocalDate.ofInstant(now, systemDefault());
    var lastResetDate  = LocalDate.ofInstant(lastDailyReset, systemDefault());

    if (!currentDate.isAfter(lastResetDate)) return;  // 同一天就跳过

    // 跨天才执行：
    setForgePoints(300_000);                       // 锻造点回满
    battlePassManager.resetDailyMissions();        // 战令日常重置
    battlePassManager.triggerMission(LOGIN);       // 补一个登录任务事件
    blossomManager.dailyReset();                   // 大赏遗物
    if (currentDate.getDayOfWeek() == MONDAY) {
        battlePassManager.resetWeeklyMissions();   // 周一重置周令
    }
    setResinBuyCount(0);                           // 树脂购买次数
    dailyTaskManager.randomizeTasks();             // 刷新今日 4 个委托
    setLastDailyReset(currentTime);
}
```

### 为什么这样设计？

| 痛点 | 懒检查的解法 |
|---|---|
| 玩家时区不同 | `LocalDate.ofInstant(..., systemDefault())` 用每个玩家所在区的本地日期 |
| 玩家离线时不应消耗 cron | 不上线就不检查，零成本 |
| 服务器重启 | 重启后下一次 onTick 自然补齐 |
| 跨天瞬间在线 | 玩家在 onTick 时被动触发，不需要广播 |

**比 cron 优雅 10 倍**——你为 5000 万玩家维护 5000 万个定时器是噩梦，但每个玩家的下一次 onTick 自检是 O(1)。

---

## 每周任务：根本不存在（严格意义上）

原神**没有"每周任务"这个系统**。所谓"每周"内容只有 4 类，都是**计数器周一重置**而已：

### 1. 战令周令任务（最接近"每周任务"）

```java
// BattlePassManager.resetWeeklyMissions (BattlePassManager.java:339)
for (var mission : this.missions.values()) {
    if (mission.getData().getRefreshType() == BATTLE_PASS_MISSION_REFRESH_CYCLE_CROSS_SCHEDULE) {
        mission.setStatus(MISSION_STATUS_UNFINISHED);
        mission.setProgress(0);
    }
}
```

**关键：战令任务和 Quest 系统完全独立**——战令用的是 `Watcher` 系统（`WatcherTriggerType` 枚举）：

- TRIGGER_LOGIN（登录）
- TRIGGER_FIGHT（战斗）
- TRIGGER_FINISH_DAILY_TASK（完成委托）
- TRIGGER_GAIN_AVATAR（获得角色）
- ...

为什么不复用 quest 系统？因为这些事件**跨业务系统**，混进 quest 触发器表会污染。

### 2. 周本（Trounce Domain）—— 只是计数器

```java
// WeeklyBossRecord.java:24
public class WeeklyBossRecord {
    private int discountNum;        // 本周已用半价次数
    private int discountNumLimit;   // 半价次数上限（3）
    private int takeNum;            // 本周已领奖次数
    private int maxTakeNumLimit;
    private String lastCycledTime;

    public void reset() {
        this.discountNum = 0;
        this.takeNum = 0;
        ...
    }
}
```

→ **不是任务，是带 cap 的计数器**。每周一凌晨清零，本周三次半价、N 次领奖上限。

### 3. 其他每周计数

- 深境螺旋：周日 4:00 重置（按祈愿期切换）
- 幽境危战 / 七圣召唤周本：每周计数
- 树脂购买次数：每天重置（不是周）

**这些都不走任务系统**，直接在各自子系统里做时间检查。

---

## 三个系统的对比

| 维度 | 主线/支线（Quest） | 每日委托（DailyTask）| 战令周令任务（BattlePass）|
|---|---|---|---|
| **是否独立状态机** | ✅ SubQuest 有 | ❌ 寄生在 SubQuest | ✅ Mission 自己的状态 |
| **触发系统** | QuestCond/Content | 同左（影子任务）| WatcherTriggerType |
| **数据规模** | 5000+ MainQuest | ~30 个池 | 数百 mission |
| **重置周期** | 永久（除非 rewind）| 每天 | 每天/每周 |
| **触发频率** | 低（剧情节奏）| 高（4个/天）| 极高（每次战斗都触发）|
| **持久化** | MongoDB 完整存档 | DailyTaskManager 字段 | BattlePassMission 字段 |

---

## 设计哲学：四档梯度

如果你要做一套类似系统，按这个梯度选方案：

### 梯度 1：永久剧情任务
**重型方案**：状态机 + 触发器系统 + 任务变量 + Lua 桥

### 梯度 2：日常重复任务
**桥接方案**：复用梯度 1 的引擎，加"启动桥事件" + "完成回调"两个钩子
- **省 90% 代码**
- 新增 daily 类型不写代码，只配数据

### 梯度 3：成就/统计型任务
**watcher 方案**：独立的事件订阅系统，不走任务引擎
- 因为这类任务的事件源是**跨业务**的
- 例子：登录、消费 X 个原石、累计行走 N 米

### 梯度 4：纯计数器
**就别搞任务系统了**：直接在子系统里加一个 `weeklyCounter` 字段 + 周一 reset
- 最简单的方案，不要硬上"任务"概念
- 例子：周本领奖次数、深渊重置、商店每周购买上限

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/managers/dailyquest/DailyTaskManager.java` — 委托管理
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/conditions/ConditionDailyTaskStart.java` — 前向桥
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/exec/ExecNotifyDailyTask.java` — 反向桥
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/battlepass/BattlePassManager.java` — 战令任务
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/dungeons/dungeon_entry/WeeklyBossRecord.java` — 周本计数器
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/player/Player.java:1293` — doDailyReset
