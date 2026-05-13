# Quest 引擎运行时深度剖析

> 第 43 篇：notes/02 讲了任务"设计图", notes/42 讲了"演出层"——这一篇填补**运行时引擎**：QuestSystem / QuestManager / GameMainQuest / GameQuest 4 层对象的协作机制。

---

## 0. 为什么这一篇重要

前面笔记里**任务运行时反复出现**但没单独解剖：
- notes/02 任务系统设计：讲了 SubQuestData / cond / content / exec 配表结构
- notes/05/07 真实任务实例：讲了任务的实际跑流
- notes/41 事件总线：讲了 4 套事件类型
- notes/42 表演系统：讲了 talk/cutscene 怎么走

但**Quest 内部怎么调度？事件怎么路由？谁触发 finish？谁存 questVar？**——这一篇打开"任务发动机"。

---

## 1. 4 层对象架构

```
┌───────────────────────────────────────────────────────────┐
│  QuestSystem (单例, GameServer 级)                          │
│  - 反射注册 3 类 handler (cond / content / exec)             │
│  - triggerCondition / initialCheckContent / triggerExec     │
│  - 174 行                                                    │
└────────────────────────┬──────────────────────────────────┘
                         │ 注入到所有 Player
                         ↓
┌───────────────────────────────────────────────────────────┐
│  QuestManager (per Player)                                  │
│  - 协调玩家的所有 mainQuest                                  │
│  - eventExecutor (4 线程异步池)                              │
│  - queueEvent / triggerEvent / onTick / onLogin             │
│  - 483 行                                                    │
└────────────────────────┬──────────────────────────────────┘
                         │ 1 player N mainQuest
                         ↓
┌───────────────────────────────────────────────────────────┐
│  GameMainQuest (per parentQuest, 持久化)                    │
│  - @Entity "quests" collection                              │
│  - 持有 N 个 GameQuest (childQuests Map)                    │
│  - questVars[5] + timeVar[10]                               │
│  - tryFinishSubQuests / rewind                              │
│  - 507 行                                                    │
└────────────────────────┬──────────────────────────────────┘
                         │ embedded 1 mainQuest N subQuest
                         ↓
┌───────────────────────────────────────────────────────────┐
│  GameQuest (per subQuest, embedded)                         │
│  - state (UNFINISHED / FINISHED / FAILED)                   │
│  - finishProgressList / failProgressList                    │
│  - accept / finish / fail                                   │
│  - 294 行                                                    │
└───────────────────────────────────────────────────────────┘
```

→ **总计 1458 行**支撑整个任务运行时。

---

## 2. QuestSystem：单例 + 反射注册

### 2.1 3 类 handler 的反射注册

```java
public QuestSystem(GameServer server) {
    super(server);
    this.condHandlers = new Int2ObjectOpenHashMap<>();   // 80+ Cond handler
    this.contHandlers = new Int2ObjectOpenHashMap<>();   // 80+ Content handler
    this.execHandlers = new Int2ObjectOpenHashMap<>();   // 30+ Exec handler
    this.registerHandlers();
}

public void registerHandlers() {
    this.registerHandlers(condHandlers, "conditions", BaseCondition.class);
    this.registerHandlers(contHandlers, "content", BaseContent.class);
    this.registerHandlers(execHandlers, "exec", QuestExecHandler.class);
}

public <T> void registerHandlers(Int2ObjectMap<T> map, String packageName, Class<T> clazz) {
    var handlerClasses = Grasscutter.reflector.getSubTypesOf(clazz);
    for (var obj : handlerClasses) {
        this.registerPacketHandler(map, obj);
    }
}
```

### 2.2 注解驱动注册

```java
public <T> void registerPacketHandler(Int2ObjectMap<T> map, Class<? extends T> handlerClass) {
    int value;
    if (handlerClass.isAnnotationPresent(QuestValueExec.class)) {
        QuestValueExec opcode = handlerClass.getAnnotation(QuestValueExec.class);
        value = opcode.value().getValue();
    } else if (handlerClass.isAnnotationPresent(QuestValueContent.class)) {
        QuestValueContent opcode = handlerClass.getAnnotation(QuestValueContent.class);
        value = opcode.value().getValue();
    } else if (handlerClass.isAnnotationPresent(QuestValueCond.class)) {
        QuestValueCond opcode = handlerClass.getAnnotation(QuestValueCond.class);
        value = opcode.value().getValue();
    } else {
        return;
    }
    map.put(value, handlerClass.getDeclaredConstructor().newInstance());
}
```

→ **第 11 次"注解 + 反射 + 自动注册"模式**（参见 [[project_grasscutter_pattern]]）：
- `@QuestValueCond(QUEST_COND_xxx)` 标注 Cond 类
- `@QuestValueContent(QUEST_CONTENT_xxx)` 标注 Content 类
- `@QuestValueExec(QUEST_EXEC_xxx)` 标注 Exec 类

加新条件/内容/执行器：**只写一个 class + 加注解，不改 QuestSystem**。

### 2.3 3 类 handler 数量

| 类 | 数量 | 用途 |
|---|---|---|
| BaseCondition (cond) | 80+ | 任务**接受/激活**条件 |
| BaseContent (cont) | 80+ | 任务**进度/完成**目标 |
| QuestExecHandler (exec) | 30+ | 任务**完成时执行**动作 |

→ 总共 **190+ handler**——是 grasscutter 中第二大的 handler 群（仅次于 PacketHandler 600+）。

---

## 3. QuestSystem 的 3 个核心 API

### 3.1 triggerCondition（检查条件）

```java
public boolean triggerCondition(Player owner, SubQuestData questData, 
                                 QuestAcceptCondition condition, String paramStr, int... params) {
    BaseCondition handler = condHandlers.get(condition.getType().getValue());
    if (handler == null) return false;
    return handler.execute(owner, questData, condition, paramStr, params);
}
```

→ 用于"任务能否接？" 的判断。

### 3.2 initialCheckContent / checkAndUpdateContent（检查进度）

```java
public boolean initialCheckContent(GameQuest quest, int[] curProgress,
        List<QuestContentCondition> conditions, LogicType logicType, boolean shouldReset) {
    int[] finished = new int[conditions.size()];
    for (int i = 0; i < conditions.size(); i++) {
        val condition = conditions.get(i);
        BaseContent handler = getContentHandler(condition.getType(), quest.getQuestData());
        
        int result = handler.initialCheck(quest, quest.getQuestData(), condition);
        if (shouldReset) curProgress[i] = result;
        finished[i] = handler.checkProgress(quest, condition, result) ? 1 : 0;
    }
    return LogicType.calculate(logicType, finished);   // ★ AND / OR 逻辑组合
}
```

→ 用于"任务进度满足了吗？" 的判断。
→ **LogicType.calculate** 处理 AND/OR/NOR 等组合逻辑——5 个 cond 的"全满足"或"任一满足"。

### 3.3 triggerExec（执行动作）

```java
public void triggerExec(GameQuest quest, QuestExecParam execParam, String... params) {
    QuestExecHandler handler = execHandlers.get(execParam.getType().getValue());
    if (handler == null) return;
    
    QuestManager.eventExecutor.execute(() -> {   // ★ 异步执行
        if (!handler.execute(quest, execParam, params)) {
            getLogger().debug("exec trigger failed");
        }
    });
}
```

→ 用于"任务完成 → 给奖励 / 切场景 / 通知 Lua"。
→ **异步执行**（4 线程池）—— exec 可能很慢（DB save, 跨服 RPC 等）。

---

## 4. QuestManager：协调中心

### 4.1 字段

```java
public class QuestManager extends BasePlayerManager {
    @Getter private final Player player;
    @Getter private final Int2ObjectMap<GameMainQuest> mainQuests;   // 玩家的所有主任务
    private long lastHourCheck = 0;
    private long lastDayCheck = 0;
    public static final ExecutorService eventExecutor;
}
```

### 4.2 4 线程异步池

```java
public static final ExecutorService eventExecutor;
static {
    eventExecutor = new ThreadPoolExecutor(4, 4,
        60, TimeUnit.SECONDS, new LinkedBlockingDeque<>(1000),
        r -> {
            Thread thread = new FastThreadLocalThread(r);
            thread.setUncaughtExceptionHandler((t, e) ->
                QuestSystem.getLogger().error("Uncaught exception", e));
            return thread;
        },
        new ThreadPoolExecutor.AbortPolicy());
}
```

→ **第 12 次"4 线程异步池"** —— Quest / Scene Script / Ability / Activity / Network logicThread / Database / ... 全是 4 线程。

→ 队列 1000 容量；超过 → `AbortPolicy` 抛异常（不堆积）。

### 4.3 queueEvent vs triggerEvent

```java
public void queueEvent(QuestContent condType, int... params) {
    queueEvent(condType, "", params);
}

public void queueEvent(QuestContent condType, String paramStr, int... params) {
    eventExecutor.execute(() -> triggerEvent(condType, paramStr, params));   // ★ 异步
}

public void triggerEvent(QuestContent condType, String paramStr, int... params) {
    // ↑ 同步执行
    List<GameMainQuest> checkMainQuests = this.getMainQuests().values().stream()
        .filter(i -> i.getState() != ParentQuestState.PARENT_QUEST_STATE_FINISHED)
        .toList();
    for (GameMainQuest mainQuest : checkMainQuests) {
        mainQuest.tryFailSubQuests(condType, paramStr, params);
        mainQuest.tryFinishSubQuests(condType, paramStr, params);
    }
}
```

**用法分离**：
- `queueEvent(...)` —— **提交到异步队列**（业务代码用）
- `triggerEvent(...)` —— **同步执行**（事件处理器内部用）

→ 业务代码 `inventory.addItem` 触发 `queueEvent` → 4 线程池消费 → 调 `triggerEvent` → 遍历所有 mainQuest 检查。

### 4.4 双重触发：Cond vs Content

```java
public void triggerEvent(QuestCond condType, String paramStr, int... params) {
    // ★ Cond 路径: 看能否激活新任务
    val potentialQuests = GameData.getQuestDataByConditions(condType, param, paramStr);
    potentialQuests.forEach(questData -> {
        if (wasSubQuestStarted(questData)) return;
        // 检查所有 acceptCond 满足
        boolean shouldAccept = LogicType.calculate(...);
        if (shouldAccept) {
            owner.getQuestManager().addQuest(questData);   // ★ 接任务
        }
    });
}

public void triggerEvent(QuestContent condType, String paramStr, int... params) {
    // ★ Content 路径: 看 active 任务是否能完成
    for (GameMainQuest mainQuest : checkMainQuests) {
        mainQuest.tryFailSubQuests(condType, paramStr, params);
        mainQuest.tryFinishSubQuests(condType, paramStr, params);
    }
}
```

**关键区别**：
- **QuestCond → 激活**：看玩家是否满足某任务的接受条件 → 是 → 自动接受
- **QuestContent → 进度**：看 active 任务的 finishCond 是否满足 → 是 → 完成

→ **同一事件可能既触发 Cond 又触发 Content**（如完成 talk → 解锁下一任务 + 推进当前任务）。

### 4.5 GameData.getQuestDataByConditions 索引

```java
val potentialQuests = GameData.getQuestDataByConditions(condType, param, paramStr);
```

→ **倒排索引**：按 (condType, param, paramStr) → List\<SubQuestData\>。
→ 不是遍历全部 20893 个 SubQuest——只看可能命中的。
→ 这就是 notes/02 提到的"beginCondQuestMap"——任务触发器的核心数据结构。

---

## 5. QuestManager.onLogin：玩家上线后做什么

```java
public void onLogin() {
    List<GameMainQuest> activeQuests = getActiveMainQuests();
    for (GameMainQuest quest : activeQuests) {
        var rewindTarget = quest.getRewindTarget();
        if (rewindTarget == null) rewindTarget = quest.getHighestActiveQuest();
        val finalRewindTarget = rewindTarget;
        
        // ★ 把玩家传送到 rewind 点
        List<Position> rewindPos = quest.rewind();
        if (rewindPos != null) {
            getPlayer().getPosition().set(rewindPos.get(0));
            getPlayer().getRotation().set(rewindPos.get(1));
        }
        
        // ★ 重放所有 rewind 之前的 beginExec
        quest.getChildQuests().values().stream()
            .filter(p -> p.getQuestData().getOrder() < finalRewindTarget.getQuestData().getOrder()
                && p.getState().getValue() == QuestState.QUEST_STATE_UNFINISHED.getValue())
            .forEach(q -> {
                if (q.getQuestData().getBeginExec() == null) return;
                q.getQuestData().getBeginExec().forEach(e -> 
                    getPlayer().getServer().getQuestSystem().triggerExec(q, e, e.getParam()));
            });
        
        quest.checkProgress(false);
    }
    player.getActivityManager().triggerActivityConditions();
}
```

### 5.1 rewind 机制

`rewindTarget` 是"任务的 save-point"——通常是某个剧情节点。

玩家**重登时**：
- 找到所有 active mainQuest
- 找出每个 mainQuest 的 rewindTarget（剧情 save-point）
- **传送玩家到 rewind 位置**
- **重放所有"早于 rewind 点"的 beginExec**（恢复世界状态）

→ 这是 notes/07 提到的"save-point 模式"的代码实现。
→ 例：剧情到一半下线 → 重登传回剧情起点，怪物/NPC/机关全部重置。

### 5.2 为什么重放 beginExec

beginExec 包括：
- ExecRefreshGroupSuite（切场景配置）
- ExecLockPoint（锁锚点）
- ExecAddSceneTag（加场景 tag）
- ExecModifyWeatherArea（改天气）

**世界状态不持久化**——重登必须**通过重放 exec 重建**。

---

## 6. QuestManager.onTick：每秒触发

```java
public void onTick() {
    val world = player.getWorld();
    if (world == null) return;
    checkTimeVars(world);
    
    // ★ 每 tick 触发"游戏时间 tick"事件
    queueEvent(QuestContent.QUEST_CONTENT_GAME_TIME_TICK,
        world.getGameTimeHours(), 0);
}

private void checkTimeVars(World world) {
    val currentDays = world.getTotalGameTimeDays();
    val currentHours = world.getTotalGameTimeHours();
    boolean checkDays = currentDays != lastDayCheck;
    boolean checkHours = currentHours != lastHourCheck;
    
    if (!checkDays && !checkHours) return;
    
    this.lastDayCheck = currentDays;
    this.lastHourCheck = currentHours;
    
    // ★ 触发 time-var 相关事件
    player.getActiveQuestTimers().forEach(mainQuestId -> {
        queueEvent(QuestCond.QUEST_COND_IS_DAYTIME);
        if (checkHours) {
            queueEvent(QuestCond.QUEST_COND_TIME_VAR_GT_EQ, mainQuestId);
            queueEvent(QuestContent.QUEST_CONTENT_TIME_VAR_GT_EQ, mainQuestId);
        }
        if (checkDays) {
            queueEvent(QuestCond.QUEST_COND_TIME_VAR_PASS_DAY, mainQuestId);
            queueEvent(QuestContent.QUEST_CONTENT_TIME_VAR_PASS_DAY, mainQuestId);
        }
    });
}
```

### 6.1 时间任务触发

`QUEST_CONTENT_GAME_TIME_TICK` 每 tick 触发 —— 让"剧情中等到日落 / 等到次日"类任务能监听。

→ "等到下午 6 点见 Klee" 这类剧情的实现机制。

### 6.2 timeVar 系统

`GameMainQuest.timeVar[10]` 存 10 个时间变量。
- 任务接受时 `ExecInitTimeVar` 记录当前时间
- 玩家移动时 onTick 检查"是否过了 N 小时/N 天"
- 触发 `QUEST_CONTENT_TIME_VAR_GT_EQ` / `QUEST_CONTENT_TIME_VAR_PASS_DAY`

→ "3 天后回来找我"机制。

---

## 7. addQuest / acceptQuest 完整流程

```java
public GameQuest addQuest(SubQuestData questConfig) {
    // 1. 找/创建 mainQuest
    GameMainQuest mainQuest = this.getMainQuestById(questConfig.getMainId());
    if (mainQuest == null) {
        mainQuest = addMainQuest(questConfig);
    }
    
    // 2. 拿到 child quest
    GameQuest quest = mainQuest.getChildQuestById(questConfig.getSubId());
    
    // 3. 强制接受
    quest.acceptQuest(true);
    
    // 4. 立即检查是否已经完成 (如玩家已有需要的物品)
    checkQuestAlreadyFulfilled(quest, true);
    
    return quest;
}

public void checkQuestAlreadyFulfilled(GameQuest quest, boolean shouldReset) {
    Grasscutter.getGameServer().getScheduler().scheduleDelayedTask(() -> {
        // ★ 延迟 1 tick 检查
        val shouldFinish = questSystem.initialCheckContent(quest, ...);
        if (shouldFinish) {
            quest.finish(false);
            return;
        }
        if (questData.getFailCond() != null && !questData.getFailCond().isEmpty()) {
            val shouldFail = questSystem.initialCheckContent(quest, ...);
            if (shouldFail) quest.fail();
        }
    }, 1);
}
```

### 7.1 即时检查 "is already done"

接到任务**立即检查 finishCond** —— 这是为什么"你已经有 10 个白萝卜了，直接完成"：
- 任务接受时检查 `QUEST_CONTENT_PACK_HAVE_ITEM` 已满足
- 自动 finish

### 7.2 延迟 1 tick

```java
scheduleDelayedTask(() -> {...}, 1);
```

→ 不立即检查，**延迟 1 tick** —— 避免事件链中的竞态。

---

## 8. enableQuests：新玩家初始化

```java
public void enableQuests() {
    if (!player.isQuestsEnabled()) {
        val startQuest = getMainQuestById(351);   // ★ 蒙德剧情起点
        if (startQuest == null || !startQuest.isFinished()) {
            // 锁住游戏时间到 540 (中午 9:00)
            player.getWorld().setGameTimeLocked(true);
            player.getWorld().changeTime(540, true);
        }
        player.setQuestsEnabled(true);
    }
    // ★ 触发 2 个起始事件
    triggerEvent(QuestCond.QUEST_COND_NONE, null, 0);
    triggerEvent(QuestCond.QUEST_COND_PLAYER_LEVEL_EQUAL_GREATER, null, 1);
}
```

→ "QuestCond.QUEST_COND_NONE" + "PLAYER_LEVEL >= 1" 这两个**铺底事件**会激活：
- 一切"无条件"开始的任务
- 一切"玩家等级 >= 1"接受的任务

→ 然后**链式反应**：新任务接受 → 触发更多事件 → 接更多任务 → 形成"任务网络"。

---

## 9. GameMainQuest：持久化主任务

### 9.1 字段

```java
@Entity(value = "quests", useDiscriminator = false)
public class GameMainQuest {
    @Id private ObjectId id;
    @Indexed @Getter private int ownerUid;
    @Transient @Getter private Player owner;
    @Transient @Getter private QuestManager questManager;
    @Getter private Map<Integer, GameQuest> childQuests;     // ★ 子任务
    @Getter private int parentQuestId;
    @Getter private int[] questVars;                          // ★ 5 个变量
    @Getter private long[] timeVar;                           // ★ 10 个时间变量
    @Getter private ParentQuestState state;
    @Getter private boolean isFinished;
    @Getter List<QuestGroupSuite> questGroupSuites;
    @Getter private int rewardIndex = 0;
    @Getter int[] suggestTrackMainQuestList;
    @Getter private Map<Integer, TalkData> talks;
}
```

### 9.2 5 个 questVars 初始 0

```java
this.questVars = new int[] {0,0,0,0,0};
```

→ 官方服 mihoyo 也是固定 5 个——**每个 mainQuest 最多 5 个状态变量**。

→ 剧情分支：`questVar[0] == 1` 表示选了 A 路线，==2 表示选了 B。

### 9.3 10 个 timeVars 初始 -1

```java
this.timeVar = new long[] {-1,-1,-1,-1,-1,-1,-1,-1,-1,-1};
```

→ -1 = 未初始化。
→ `ExecInitTimeVar` 设为当前游戏时间 → onTick 监控。

### 9.4 questVar 改变触发 6 个事件

```java
private void triggerQuestVarAction(int index, int value) {
    questManager.queueEvent(QuestCond.QUEST_COND_QUEST_VAR_EQUAL, index, value);
    questManager.queueEvent(QuestCond.QUEST_COND_QUEST_VAR_GREATER, index, value);
    questManager.queueEvent(QuestCond.QUEST_COND_QUEST_VAR_LESS, index, value);
    questManager.queueEvent(QuestContent.QUEST_CONTENT_QUEST_VAR_EQUAL, index, value);
    questManager.queueEvent(QuestContent.QUEST_CONTENT_QUEST_VAR_GREATER, index, value);
    questManager.queueEvent(QuestContent.QUEST_CONTENT_QUEST_VAR_LESS, index, value);
    this.getOwner().sendPacket(new PacketQuestUpdateQuestVarNotify(...));
}
```

→ **一次 questVar 改变 → 6 个事件触发**：
- 3 个 Cond（EQ / GT / LT）→ 检查其他任务是否能接
- 3 个 Content（EQ / GT / LT）→ 检查 active 任务是否能完成

→ 这就是为什么"questVar = 1"能触发剧情分支——它通过 6 个事件传播。

### 9.5 3 类变量对比

| 变量 | 范围 | 触发事件 |
|---|---|---|
| questVar[5] | 单 mainQuest | 6 个事件（QUEST_VAR_* × 2 × EQ/GT/LT）|
| timeVar[10] | 单 mainQuest | 2-3 个事件（TIME_VAR_GT_EQ / PASS_DAY / IS_DAYTIME）|
| questGlobalVar | 跨 mainQuest | 4 个事件（GLOBAL_VAR_* × EQ/GT/LT + Notify）|

→ **三层变量系统**：局部 / 时间 / 全局。

---

## 10. 完整事件流：玩家拾取物品 → 任务完成

把所有组件串起来——一个**经典链路**：

```
[阶段 1: 玩家拾取]
EntityItem → Inventory.addItem(item)
    ↓
inventory.triggerAddItemEvents:
    queueEvent(QuestContent.QUEST_CONTENT_OBTAIN_ITEM, itemId, count);
    queueEvent(QuestContent.QUEST_CONTENT_OBTAIN_VARIOUS_ITEM, itemId, count);
    queueEvent(QuestCond.QUEST_COND_PACK_HAVE_ITEM, itemId, count);
    
[阶段 2: 4 线程池消费]
eventExecutor.execute(() -> triggerEvent(QUEST_CONTENT_OBTAIN_ITEM, "", [itemId, count]))
    
[阶段 3: triggerEvent 路由]
QuestManager.triggerEvent(QuestContent):
    for (GameMainQuest mainQuest : checkMainQuests):
        mainQuest.tryFailSubQuests(...)
        mainQuest.tryFinishSubQuests(...)
    
[阶段 4: GameMainQuest.tryFinishSubQuests]
    foreach SubQuest in childQuests:
        if state == UNFINISHED:
            QuestSystem.checkAndUpdateContent(...):
                for cond in finishCond:
                    handler = contHandlers[cond.type]
                    if handler.isEvent(...):
                        result = handler.updateProgress(...)
                        curProgress[i] = result
                        finished[i] = handler.checkProgress(...)
                LogicType.calculate(logicType, finished) → all done?
    
[阶段 5: 完成 SubQuest]
    if shouldFinish:
        quest.finish(false):
            遍历 finishExec:
                QuestSystem.triggerExec(quest, exec):
                    handler.execute(quest, exec, params)  // 4 线程异步
            发奖励:
                rewardData.getRewardItemList().forEach:
                    inventory.addItem(item, ActionReason.QuestReward)
                        ↓ 再次触发事件 (递归!)
            遍历 successExec:
                推进下一个 SubQuest 到 ACCEPTED
            packet:
                PacketQuestProgressUpdateNotify (Quest 进度)
                PacketItemAddHintNotify (右上角浮动)
                PacketQuestListUpdateNotify
                
[阶段 6: 客户端更新]
    客户端任务面板更新
    任务追踪标记移动到下一目标
```

→ **一次 addItem 触发完整任务链路 6 阶段 + 多次递归触发**。

---

## 11. 加密钥 (QuestEncryptionKey)

```java
public static long getQuestKey(int mainQuestId) {
    QuestEncryptionKey questEncryptionKey = GameData.getMainQuestEncryptionMap().get(mainQuestId);
    return questEncryptionKey != null ? questEncryptionKey.getEncryptionKey() : 0L;
}
```

→ **每个 mainQuest 有专属加密钥**：
- 任务剧情台词在客户端**加密存储**
- 接到任务 → 服务器下发解密钥
- 没接到 → 看不到剧情（防剧透）

→ 这就是为什么"mihoyo 解包工具需要 quest_key"——配表是加密的。grasscutter 用社区维护的 key 表。

---

## 12. 设计模式总结

### 12.1 4 层职责分离

```
QuestSystem  (单例)  → handler 注册 + 算法
QuestManager (per player) → 异步队列 + 事件路由
GameMainQuest (per parent) → 持久化 + questVar 管理
GameQuest (per sub)  → state 转换 + 单元任务
```

→ **每层职责清晰**——单元测试时可独立 mock。

### 12.2 同步 + 异步双 API

```java
queueEvent(...)     // 异步 (业务用)
triggerEvent(...)   // 同步 (内部用)
```

→ 业务**不用关心线程**——丢到队列就行。

### 12.3 倒排索引加速

```java
GameData.getQuestDataByConditions(condType, param, paramStr)
```

→ O(1) 查找匹配任务——不遍历 20893 个 SubQuest。

### 12.4 LogicType 组合

```java
LogicType.calculate(AND, [1, 1, 0]) → false
LogicType.calculate(OR, [1, 0, 0]) → true
```

→ 多个 cond/content **AND/OR/NOR** 组合——表达"5 个条件全满足 OR 任 1 满足"。

### 12.5 11 次 "注解+反射" 模式

参见 [[project_grasscutter_pattern]]——QuestSystem 是这个模式的**最经典应用**：
- 3 类 handler 共享同一注册逻辑
- 加新 handler 零代码改动
- 80+ Cond + 80+ Content + 30+ Exec 共 190+ 子类

---

## 13. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 客户端发"完成任务" packet | ✗ 没这种 packet |
| 篡改 questVar 数字 | ✗ 服务器存 |
| 直接发 `QUEST_CONTENT_xxx` 事件 | ✗ 客户端只能 talk/plot |
| 通过 talk/plot 间接刷事件 | ✓ 部分 |
| 用 GM 命令 startQuest | ✓ 但需要 GM 权限 |

→ Quest 引擎**反作弊较强**——核心检查在服务器，客户端只能通过特定 packet（talk/plot）间接触发。

---

## 14. 关键收获

1. **4 层对象架构**：QuestSystem (单例) → QuestManager (per player) → GameMainQuest (持久化) → GameQuest (embedded)
2. **总计 1458 行**支撑整个任务运行时
3. **第 11 次"注解+反射"模式**：QuestSystem 反射注册 190+ handler (80 Cond + 80 Content + 30 Exec)
4. **第 12 次"4 线程异步池"**：eventExecutor 处理事件分发
5. **queueEvent (异步) vs triggerEvent (同步)** 双 API
6. **双重路由**：QuestCond → 激活新任务 / QuestContent → 推进现有任务
7. **倒排索引 `getQuestDataByConditions`**：O(1) 找匹配任务而非遍历 20893
8. **LogicType.calculate**：AND/OR/NOR 组合多个条件
9. **rewind 机制**：重登传送回剧情 save-point + 重放 beginExec
10. **onTick 每秒触发 QUEST_CONTENT_GAME_TIME_TICK** 支持"等 N 小时"剧情
11. **questVars[5] + timeVar[10] + questGlobalVar**：三层变量系统
12. **questVar 改一次触发 6 个事件**（Cond/Content × EQ/GT/LT）
13. **enableQuests 用 QUEST_COND_NONE + PLAYER_LEVEL >= 1 铺底激活**全新任务
14. **checkQuestAlreadyFulfilled 延迟 1 tick**：避免竞态
15. **QuestEncryptionKey 每 mainQuest 一钥**：剧情台词客户端加密
16. **反作弊较强**：核心检查在服务器，客户端只能通过 talk/plot 间接触发

---

## 15. 一句话总结

> **Quest 运行时引擎 = 4 层对象 (QuestSystem 单例 → QuestManager 每玩家 → GameMainQuest 持久化 → GameQuest 嵌入) + 11 次注解反射注册的 190+ handler + 12 次 4 线程异步池 + queueEvent/triggerEvent 双 API + 倒排索引加速; QuestCond 激活 / QuestContent 推进 / QuestExec 输出三路驱动; questVar[5] + timeVar[10] + questGlobalVar 三层变量; rewind 机制实现剧情 save-point 重登恢复世界状态.**
> 
> **设计哲学: 单层薄、多层组合、handler 数据驱动 (注解注册) + 事件异步 (4 线程) + 倒排索引避免全表扫——这是 grasscutter 中最成熟的子系统设计，能跑 2000+ 主任务 / 20000+ 子任务的根本.**

---

**前置笔记**：
- notes/02-09 任务系统设计 - 数据结构 + 配表
- notes/27 架构模式 - 注解反射注册 + 4 线程异步池
- notes/40 Player Manager 横切 - QuestManager 是 25+ Manager 之一
- notes/41 事件总线 - QuestContent/QuestCond 是其中 2 套
- notes/42 表演系统 - HandlerNpcTalkReq 触发 Quest 事件

**关联文件**：
- `QuestSystem.java`(174) - 单例 + 反射注册
- `QuestManager.java`(483) - 每玩家协调
- `GameMainQuest.java`(507) - 持久化主任务 + questVars
- `GameQuest.java`(294) - 嵌入式子任务 + state 转换
- `QuestValueCond.java` / `QuestValueContent.java` / `QuestValueExec.java` - 3 个注解
- `conditions/Base*.java` × 80+
- `content/Base*.java` × 80+
- `exec/Exec*.java` × 30+
- `QuestEncryptionKey.java` - 加密钥表

**研究的源代码**: 1458 行任务运行时核心代码。
