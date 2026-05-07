# 03 · 运行时流程：接取 / 执行 / 完成

## 五阶段生命周期

```
┌─────────────────────────────────────────────────────────────┐
│  阶段 0: 启动期                                                │
│  JSON → 反序列化 → 倒排索引 (beginCondQuestMap)               │
└────────────────────────────────────────────┬────────────────┘
                                              ↓
┌─────────────────────────────────────────────────────────────┐
│  阶段 1: 接取期 (条件驱动，自动)                              │
│  外部事件 → queueEvent(QuestCond)                            │
│  → 倒排索引找候选 → 验证 acceptCond → addQuest                │
│  state: NONE → UNSTARTED                                     │
└────────────────────────────────────────────┬────────────────┘
                                              ↓
┌─────────────────────────────────────────────────────────────┐
│  阶段 2: 启动期                                               │
│  GameQuest.start()                                           │
│  - 注册 trigger（如有）                                       │
│  - 执行 beginExec[]                                          │
│  - 推送任务列表给客户端                                       │
│  state: UNSTARTED → UNFINISHED                               │
└────────────────────────────────────────────┬────────────────┘
                                              ↓
┌─────────────────────────────────────────────────────────────┐
│  阶段 3: 进行期 (事件驱动，订阅式)                             │
│  玩家行为 → queueEvent(QuestContent)                         │
│  → 遍历活跃 SubQuest → checkAndUpdateContent                  │
│  → 命中条件: progress[i]++                                    │
│  → LogicType.calculate(progress) → 决定是否完成               │
└────────────────────────────────────────────┬────────────────┘
                                              ↓
┌─────────────────────────────────────────────────────────────┐
│  阶段 4: 完成期 / 失败期                                      │
│  GameQuest.finish() or fail()                                │
│  - state: UNFINISHED → FINISHED/FAILED                       │
│  - 执行 finishExec[] / failExec[] (给奖励、解锁、改变量)       │
│  - triggerStateEvents() ← 链式触发其他 SubQuest               │
│  - if finishParent: 关闭 MainQuest 整体                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 阶段 0：启动期 — 加载 + 建索引

```
ResourceLoader.loadQuests()             ← ResourceLoader.java:589
  ↓
读取 Generated/Quest/<id>.json
JsonUtils.loadToClass(path, MainQuestData.class)    ← Gson 反序列化
  ↓
GameData.mainQuestDataMap.put(id, mainQuest)
  ↓
对每个 SubQuest 调用 addToCache(...)    ← ResourceLoader.java:629
   ├─ GameData.questDataMap.put(subId, subQuest)
   └─ 构建倒排索引 beginCondQuestMap
```

倒排索引按 acceptCond 的每个条件建：

```java
// ResourceLoader.java:629
private static void addToCache(SubQuestData questData) {
    questData.getAcceptCond().forEach(cond -> {
        val key = cond.asKey();   // type + 首参数 + 字符串参数
        cacheMap.computeIfAbsent(key, e -> new ArrayList<>())
                .add(questData);
    });
}
```

---

## 阶段 1：接取期

### 触发源：任何业务都能 fire QuestCond

```
[业务 X] → queueEvent(QuestCond.X, params)
   ↓ 异步 eventExecutor (4 线程池)
QuestManager.triggerEvent(QuestCond, ...)    ← QuestManager.java:367
```

### 调度路径

```java
public void triggerEvent(QuestCond condType, String paramStr, int... params) {
    // 1. 倒排索引查候选
    val potentialQuests = GameData.getQuestDataByConditions(condType, params[0], paramStr);
    
    // 2. 对每个候选验证 acceptCond
    potentialQuests.forEach(questData -> {
        if (wasSubQuestStarted(questData)) return;
        
        val acceptCond = questData.getAcceptCond();
        int[] accept = new int[acceptCond.size()];
        for (int i = 0; i < acceptCond.size(); i++) {
            accept[i] = questSystem.triggerCondition(...) ? 1 : 0;
        }
        
        // 3. 用 LogicType 组合验证结果
        boolean shouldAccept = LogicType.calculate(questData.getAcceptCondComb(), accept);
        
        // 4. 通过则 addQuest
        if (shouldAccept) {
            GameQuest quest = owner.getQuestManager().addQuest(questData);
        }
    });
}
```

`addQuest` → `new GameQuest(...)` → state = `UNSTARTED`。

### 关键洞察

**所有原神 SubQuest 都是被动接取**——没有"接取按钮"。条件满足时自动接取。这是为什么剧情可以"丝滑"推进。

---

## 阶段 2：启动期

```java
// GameQuest.start() (GameQuest.java:79)
public void start() {
    this.startTime = this.acceptTime;
    this.state = QuestState.QUEST_STATE_UNFINISHED;
    
    // 1. 注册 Lua trigger（如有 QUEST_CONTENT_TRIGGER_FIRE 类型的 finishCond）
    val triggerCond = questData.getFinishCond().stream()
        .filter(p -> p.getType() == QUEST_CONTENT_TRIGGER_FIRE).toList();
    for (val cond : triggerCond) {
        TriggerExcelConfigData newTrigger = ...;
        triggerData.put(newTrigger.getTriggerName(), newTrigger);
        // 通过 SceneScriptManager 注册到场景 Lua
    }
    
    // 2. 通知客户端
    getOwner().sendPacket(new PacketQuestListUpdateNotify(this));
    
    // 3. 执行 beginExec[]（投放 NPC、解锁地区、改变量等）
    if (getQuestData().getBeginExec() != null) {
        getQuestData().getBeginExec().forEach(e -> 
            getOwner().getServer().getQuestSystem().triggerExec(this, e, e.getParam()));
    }
    
    // 4. 检查是否启动时就已满足完成条件（特殊情况）
    getOwner().getQuestManager().checkQuestAlreadyFulfilled(this, true);
    
    // 5. 联动其他系统
    getOwner().getDungeonEntryManager().checkQuestForDungeonEntryUpdate(this);
    getOwner().getCoopHandler().checkNextCoopPointAccept(this.getSubQuestId());
}
```

### checkQuestAlreadyFulfilled 的存在原因

某些 finishCond 类型是**主动检查型**（如"持有道具 X"），不是事件订阅型。如果接取时玩家已满足，需要立即完成，不能等下一次事件。

---

## 阶段 3：进行期

### 业务事件触发

以"和 NPC 说话"为例（`HandlerNpcTalkReq.java:13`）：

```java
public void handle(GameSession session, byte[] header, NpcTalkReq req) {
    int talkId = req.getTalkId();
    int mainQuestId = ...;
    
    // 1. 改玩家状态
    val mainQuest = questManager.getMainQuestByTalkId(talkId);
    if (mainQuest != null) {
        mainQuest.getTalks().put(talkId, talkForQuest);
    }
    
    // 2. fire 三个事件（接取检查 + 推进活跃任务）
    questManager.queueEvent(QuestContent.QUEST_CONTENT_COMPLETE_ANY_TALK, talkId, 0, 0);
    questManager.queueEvent(QuestContent.QUEST_CONTENT_COMPLETE_TALK, talkId, 0);
    questManager.queueEvent(QuestCond.QUEST_COND_COMPLETE_TALK, talkId, 0);
    
    // 3. 回响应
    session.send(new PacketNpcTalkRsp(...));
}
```

### 进度推进

```java
// QuestSystem.checkAndUpdateContent (QuestSystem.java:128)
public boolean checkAndUpdateContent(GameQuest quest, int[] curProgress, ...) {
    int[] finished = new int[conditions.size()];
    for (int i = 0; i < conditions.size(); i++) {
        val condition = conditions.get(i);
        BaseContent handler = getContentHandler(condition.getType(), quest.getQuestData());
        
        // handler 决定这个事件是否影响这个条件
        if (handler.isEvent(quest.getQuestData(), condition, condType, paramStr, params)) {
            int result = handler.updateProgress(quest, curProgress[i], condition, paramStr, params);
            curProgress[i] = result;
        }
        finished[i] = handler.checkProgress(quest, condition, curProgress[i]) ? 1 : 0;
    }
    
    // 用 LogicType 组合
    return LogicType.calculate(logicType, finished);
}
```

### 进度持久化

每个 SubQuest 有两个数组：

```java
// GameQuest.java:48
private int[] finishProgressList;
private int[] failProgressList;
```

数组长度 = 条件个数。每个槽位的语义由 handler 决定：
- **杀怪任务**：累加击杀数
- **状态等于任务**：0 或 1
- **物品收集**：累加获取数

最终判定（`BaseContent.java:24`）：

```java
public boolean checkProgress(quest, condition, currentProgress) {
    val target = condition.getCount() > 0 ? condition.getCount() : 1;
    return currentProgress >= target;
}
```

---

## 阶段 4：完成期 / 失败期

```java
// GameQuest.finish() (GameQuest.java:187)
public void finish(boolean isManualFinish) {
    this.state = QuestState.QUEST_STATE_FINISHED;
    this.finishTime = Utils.getCurrentSeconds();
    
    // 1. 通知客户端
    getOwner().sendPacket(new PacketQuestListUpdateNotify(this));
    
    // 2. 如果标记 finishParent，关闭整个 MainQuest
    if (getQuestData().isFinishParent()) {
        getMainQuest().finish(isManualFinish);
    }
    
    // 3. 执行 finishExec[]
    if (getQuestData().getFinishExec() != null) {
        getQuestData().getFinishExec().forEach(e -> 
            getOwner().getServer().getQuestSystem().triggerExec(this, e, e.getParam()));
    }
    
    // 4. fire 状态变化事件 → 链式触发其他 SubQuest 接取
    triggerStateEvents();
    
    // 5. 联动副本/合作模式/解锁状态
    getOwner().getScene().triggerDungeonEvent(...);
    getOwner().getProgressManager().tryUnlockOpenStates();
    
    // 6. 给奖励道具
    val gainItems = questData.getGainItems();
    if (gainItems != null) {
        gainItems.forEach(item -> 
            getOwner().getInventory().addItem(item.getItemId(), item.getCount(), ActionReason.QuestItem));
    }
    
    // 7. 持久化
    save();
}
```

### triggerStateEvents — 链式触发

```java
// GameQuest.java:248
public void triggerStateEvents() {
    val questId = this.subQuestId;
    val state = this.state.getValue();
    
    questManager.queueEvent(QuestCond.QUEST_COND_STATE_EQUAL, questId, state);
    questManager.queueEvent(QuestCond.QUEST_COND_STATE_NOT_EQUAL, questId, state);
    questManager.queueEvent(QuestContent.QUEST_CONTENT_QUEST_STATE_EQUAL, questId, state);
    questManager.queueEvent(QuestContent.QUEST_CONTENT_QUEST_STATE_NOT_EQUAL, questId, state);
}
```

→ 这就是"完成步骤 A → 自动接取步骤 B"的实现：B 的 acceptCond 订阅了 A 的状态变化。

### finishExec 反向操作其他任务

`finishExec` 不只是给奖励，还能**主动改变其他系统**：

| Exec 类型 | 效果 |
|---|---|
| `QUEST_EXEC_ROLLBACK_QUEST` | 把指定 SubQuest 重置回 UNSTARTED |
| `QUEST_EXEC_SET_QUEST_VAR` | 改任务变量 → 触发更多事件 |
| `QUEST_EXEC_SET_OPEN_STATE` | 解锁系统功能（如锻造、深境） |
| `QUEST_EXEC_UNLOCK_AREA` | 解锁地图区域 |
| `QUEST_EXEC_REFRESH_GROUP_SUITE` | 切换场景配置（NPC 出现/消失） |
| `QUEST_EXEC_GRANT_TRIAL_AVATAR` | 发试用角色 |

→ **任务系统是整个游戏 progression 的中央总线**。地图、NPC、商店、剧情、技能解锁，全部由 finishExec 驱动。

---

## 启动 / 登录的 rewind 机制

玩家死亡或掉线时不应丢任务进度。机制：

```java
// GameQuest.rewind (GameQuest.java:261)
public boolean rewind(boolean notifyDelete) {
    // 同 MainQuest 内 order > 当前 的所有 SubQuest 全部清空
    getMainQuest().getChildQuests().values().stream()
        .filter(p -> p.getQuestData().getOrder() > this.getQuestData().getOrder())
        .forEach(q -> q.clearProgress(notifyDelete));
    clearProgress(notifyDelete);
    this.start();
    return true;
}
```

登录时自动触发（`QuestManager.onLogin` `QuestManager.java:115-135`）：

```java
public void onLogin() {
    List<GameMainQuest> activeQuests = getActiveMainQuests();
    for (GameMainQuest quest : activeQuests) {
        var rewindTarget = quest.getRewindTarget();
        if (rewindTarget == null) rewindTarget = quest.getHighestActiveQuest();
        List<Position> rewindPos = quest.rewind();
        if (rewindPos != null) {
            getPlayer().getPosition().set(rewindPos.get(0));  // 把玩家送回安全点
        }
    }
}
```

---

## onTick：不是定时器，是懒检查

任务系统的"周期性逻辑"（每日重置、时间变量、游戏时间）通过玩家的 `onTick` 触发：

```java
// QuestManager.onTick (QuestManager.java:137)
public void onTick() {
    val world = player.getWorld();
    if (world == null) return;
    
    checkTimeVars(world);
    
    queueEvent(QuestContent.QUEST_CONTENT_GAME_TIME_TICK, world.getGameTimeHours(), 0);
}
```

**好处**：
- 不需要全局定时器
- 不在线的玩家不消耗资源
- 跨天 / 时区切换都自然处理

---

## 参考代码位（核心运行时）

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/GameQuest.java` — SubQuest 运行实例
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/GameMainQuest.java` — MainQuest 运行实例
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/QuestManager.java` — 玩家任务管理器
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/QuestSystem.java` — 全局任务系统
- `Grasscutter-Quests/src/main/java/emu/grasscutter/data/ResourceLoader.java:589` — 数据加载入口
