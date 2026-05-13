# SceneScriptManager / Lua 引擎深度剖析

> 第 44 篇：notes/14 讲了 SceneScript 的"**设计图**"——这一篇打开**运行时引擎**：SceneScriptManager 1075 行 + 28 个 Lua API 处理器 + 第 13 次"4 线程异步池"模式。

---

## 0. 为什么这一篇重要

前面笔记里 SceneScriptManager 反复出现但从未真正解剖：
- notes/14 SceneScript 系统：讲了 EVENT 类型 + Group/Suite 设计
- notes/32 怪物：Lua spawn / EVENT_ANY_MONSTER_DIE
- notes/33 Gadget：scriptManager.refreshGroup
- notes/42 表演系统：ExecNotifyGroupLua + EVENT_LUA_NOTIFY
- notes/43 Quest 引擎：getSceneGroupSuite + 上一回答 NPC 创建
- 用户问的"任务 NPC 怎么创建"：本质就是 refreshGroup / addGroupSuite

**关键问题**：
1. Lua 脚本怎么加载 / 何时执行？
2. Trigger 怎么注册 / 触发 / 反注册？
3. `callEvent` 异步执行细节？
4. Region 触发器（"走到圈里")怎么实现？
5. Java 给 Lua 暴露了多少 API？
6. Group / Suite 切换的完整代码路径？

---

## 1. SceneScriptManager 字段全图（15+ Map）

`SceneScriptManager.java`（**1075 行**, grasscutter 第 4 大类）：

```java
public class SceneScriptManager {
    private final Scene scene;                                    // 反向引用
    private final Map<String, Integer> variables;                  // 场景级变量
    @Getter private SceneMeta meta;                                // 场景元数据（Block + Group）
    private boolean isInit;
    
    // ★ Trigger 系统 4 个 Map
    private final Map<Integer, Set<SceneTrigger>> currentTriggers;       // eventId → triggers
    private final Map<String, Set<SceneTrigger>> triggersByGroupScene;   // "groupId_suiteId" → triggers
    private final Map<Integer, Set<Pair<String, Integer>>> activeGroupTimers;
    private final Map<String, AtomicInteger> triggerInvocations;          // 调用次数计数
    
    // ★ Region 系统
    private final Map<Integer, EntityRegion> regions;             // entityId → Region
    
    // ★ Group 系统 3 层
    private final Map<Integer, SceneGroup> sceneGroups;
    private final Map<Integer, SceneGroupInstance> sceneGroupsInstances;        // 当前激活的
    private final Map<Integer, SceneGroupInstance> cachedSceneGroupsInstances;  // 持久化的
    
    // ★ Monster 服务
    private ScriptMonsterTideService scriptMonsterTideService;
    private ScriptMonsterSpawnService scriptMonsterSpawnService;
    
    // ★ Block 加载追踪
    private final Map<Integer, Set<SceneGroup>> loadedGroupSetPerBlock;
    private static final Int2ObjectMap<List<Grid>> groupGridsCache;
    
    // ★ 异步事件池 (第 13 次 4 线程!)
    public static final ExecutorService eventExecutor;
}
```

→ **比 Player.java 还紧凑**——每个字段都是核心数据结构。

---

## 2. 第 13 次"4 线程异步池"模式

```java
public static final ExecutorService eventExecutor;
static {
    eventExecutor = new ThreadPoolExecutor(4, 4,
        60, TimeUnit.SECONDS, new LinkedBlockingDeque<>(10000),   // ★ 10000 队列 (比 Quest 大 10x)
        r -> {
            Thread thread = new FastThreadLocalThread(r);
            thread.setUncaughtExceptionHandler((t, e) ->
                Loggers.getScriptSystem().error("Uncaught exception", e));
            return thread;
        }, new ThreadPoolExecutor.AbortPolicy());
}
```

**累计 13 次** —— grasscutter 把"4 线程异步池"用到极致：Quest(1000) / SceneScript(10000) / Ability(1000) / Activity / Network logicThread / Database / Talk / Codex / Dungeon / Activity Watcher / ...

→ **SceneScript 队列 10000** 是全系统**最大**——因为场景脚本调用最频繁（每个 trigger / region / gadget 状态变化都过它）。

---

## 3. callEvent 流程：异步 + ThreadLocal

```java
public void callEvent(@Nonnull ScriptArgs params) {
    /**
     * We use ThreadLocal to trans SceneScriptManager context to ScriptLib...
     * But when callEvent is called in a ScriptLib func, it may cause NPE because the inner call cleans the ThreadLocal...
     * e.g. CallEvent -> set -> ScriptLib.xxx -> CallEvent -> set -> remove -> NPE -> (remove)
     * So we use thread pool to clean the stack to avoid this new issue.
     */
    eventExecutor.execute(() -> this.realCallEvent(params));
}

private void realCallEvent(@Nonnull ScriptArgs params) {
    int eventType = params.type;
    
    // 1. 拿到 event 关心的 trigger (按 groupId + source 过滤)
    Set<SceneTrigger> relevantTriggers = this.getTriggersByEvent(eventType).stream()
        .filter(t -> params.getGroupId() == 0 || t.getGroupId() == params.getGroupId())
        .filter(t -> (t.getSource().isEmpty() || t.getSource().equals(params.getEventSource())))
        .collect(Collectors.toSet());
    
    // 2. 逐个执行
    for (SceneTrigger trigger : relevantTriggers) {
        handleEventForTrigger(params, trigger);
    }
}
```

### 3.1 为什么必须异步

注释非常有趣：**ThreadLocal 嵌套调用会 NPE**。

```
[场景] callEvent(A) 在主线程跑
   → ThreadLocal.set(ctx_A)
   → callScriptFunc → Lua 调 ScriptLib.foo
      → ScriptLib.foo 内部又调 callEvent(B)   ← 嵌套!
         → ThreadLocal.set(ctx_B)
         → callScriptFunc 处理...
         → ThreadLocal.remove()  ← ★ 移除了 ctx_B
      → 返回 ScriptLib.foo
   → 此时 ThreadLocal 已被 remove
   → ctx_A 不见了 → NPE
```

**解决**：每次 callEvent **丢到 4 线程池** → 每个 task 独立线程 → 各自的 ThreadLocal 互不影响。

→ 这是**实战踩坑的设计**——经典的线程局部存储 + 嵌套调用陷阱。

### 3.2 trigger 过滤的两条规则

```java
.filter(t -> params.getGroupId() == 0 || t.getGroupId() == params.getGroupId())
.filter(t -> t.getSource().isEmpty() || t.getSource().equals(params.getEventSource()))
```

- **groupId 过滤**：trigger 只关心自己组的事件（除非 groupId=0 广播）
- **source 过滤**：trigger 可以指定"只关心来自特定配置 ID 的事件"

→ 类似 Pub/Sub 的**主题 + 子主题**订阅模式。

---

## 4. Trigger 系统：注册 → 触发 → 反注册

### 4.1 注册

```java
public void registerTrigger(SceneTrigger trigger) {
    triggerInvocations.put(trigger.getName(), new AtomicInteger(0));   // 调用次数
    getTriggersByEvent(trigger.getEvent()).add(trigger);
    logger.debug("Registered trigger {}", trigger.getName());
}
```

→ trigger 按 eventType 索引到 `currentTriggers` Map。

### 4.2 触发后处理

`callTrigger` 第 807-841 行：
```java
private void callTrigger(SceneTrigger trigger, SceneGroup group, ScriptArgs params) {
    // 1. 调 Lua action 函数
    val callResult = this.callScriptFunc(trigger.getAction(), group, params);
    
    // 2. 计数 +1
    val invocations = invocationsCounter.incrementAndGet();
    
    // 3. 通知挑战系统
    val activeChallenge = scene.getChallenge();
    if (activeChallenge != null) {
        activeChallenge.onGroupTriggerDeath(trigger);
    }
    
    // 4. ★ 通知 Quest 系统
    val triggerData = GameData.getQuestTriggerDataByName(params.getGroupId(), trigger.getName());
    if (triggerData != null && triggerData.getGroupId() == params.getGroupId()) {
        getScene().getPlayers().forEach(p -> {
            p.getQuestManager().queueEvent(QuestContent.QUEST_CONTENT_TRIGGER_FIRE,
                triggerData.getId(), 0);
        });
    }
    
    // 5. Timer 事件特殊处理
    if (trigger.getEvent() == EVENT_TIMER_EVENT) {
        cancelGroupTimerEvent(trigger.getGroupId(), trigger.getSource());
    }
    
    // 6. ★ 触发器自动反注册逻辑
    if (callResult.isBoolean() && !callResult.asBoolean()
        || callResult.isInteger() && callResult.asInteger() != 0
        || trigger.getTriggerCount() > INF_TRIGGERS && invocations >= trigger.getTriggerCount()) {
        deregisterTrigger(trigger);
    }
}
```

### 4.3 自动反注册的 3 种条件

```java
1. Lua 返回 false        → deregister (Lua 说"我处理完了")
2. Lua 返回非 0 整数      → deregister
3. 达到 triggerCount 上限 → deregister (有限次触发)
```

→ trigger 默认**一次性** —— 触发后自动消失。除非 `triggerCount == INF_TRIGGERS` 永久触发器。

---

## 5. Group / Suite 系统：3 层抽象

```
SceneGroup (配表, 不可变)
   ↓ 包含
SceneSuite[] (一个 group 的多个变体)
   ↓ 实例化时
SceneGroupInstance (运行时, 持久化部分状态)
   ↓ 缓存
cachedSceneGroupsInstances (DB persistent)
```

### 5.1 refreshGroup（核心方法）

`refreshGroup` 有 **5 个重载** —— 这是 grasscutter 中**最多重载**的方法之一：
```java
refreshGroup(int groupId, int suiteIndex, boolean excludePrevSuite)
refreshGroup(SceneGroupInstance groupInstance)
refreshGroup(SceneGroupInstance groupInstance, int suiteIndex, boolean excludePrevSuite)
refreshGroup(SceneGroupInstance groupInstance, int suiteIndex, boolean excludePrevSuite, boolean dontLoad)
refreshGroup(SceneGroupInstance groupInstance, int suiteIndex, boolean excludePrevSuite, List<GameEntity<?>> entitiesAdded, boolean dontLoad)
```

→ 因为切换 suite 的场景太多了：剧情推进 / 任务完成 / 玩家进区域 / 时间到 / 服务器重启恢复。

### 5.2 addGroupSuite：加载某 suite 的内容

```java
public void addGroupSuite(SceneGroupInstance groupInstance, SceneSuite suite, List<GameEntity<?>> entities) {
    // 1. 先注册 trigger (剧情节点要先存在才能响应)
    registerTrigger(suite.getSceneTriggers());
    
    // 2. 创建 gadget + monster (按 suite 配置)
    var toCreate = new ArrayList<GameEntity<?>>();
    toCreate.addAll(getGadgetsInGroupSuite(groupInstance, suite));
    toCreate.addAll(getMonstersInGroupSuite(groupInstance, suite));
    
    if (entities != null) {
        entities.addAll(toCreate);
    } else {
        addEntities(toCreate);   // ★ 添加到 Scene
    }
    
    // 3. 注册 Region (进入圈触发的)
    registerRegionInGroupSuite(group, suite);
}
```

**3 步**：trigger → entity → region。

→ Region 必须**最后注册** —— 否则玩家可能在 trigger 还没准备好时就触发 region。

### 5.3 removeGroupSuite

```java
public void removeGroupSuite(SceneGroup group, SceneSuite suite) {
    deregisterTrigger(suite.getSceneTriggers());
    removeMonstersInGroup(group, suite);
    removeGadgetsInGroup(group, suite);
}
```

→ **顺序反过来**：先 trigger 再 entity（避免移除 entity 时 trigger 还在响应）。

---

## 6. Region 触发器：每 tick 检查

`checkRegions()` 由 `Scene.onTick` 调用（每 tick = 每秒）：
```java
public void checkRegions() {
    if (this.regions.size() == 0) return;
    
    for (var region : this.regions.values()) {
        region.clearDeadEntities();
        
        // ★ 找出"进入区域"的玩家
        getScene().getEntities().values().stream()
            .filter(e -> e.getEntityType() == EntityType.Avatar)
            .filter(e -> region.isPosInRegion(e.getPosition()) && !region.getEntities().contains(e))
            .forEach(region::addEntity);
        
        // ★ 找出"离开区域"的玩家
        getScene().getEntities().values().stream()
            .filter(e -> e.getEntityType() == EntityType.Avatar)
            .filter(e -> !region.isPosInRegion(e.getPosition()) && !region.getNotContainEntities().contains(e))
            .forEach(region::removeEntity);
        
        // 触发 enter 事件
        region.getNewEntities().forEach(entity -> 
            callRegionEvent(region, EventType.EVENT_ENTER_REGION, entity));
        region.resetNewEntities();
        
        // 触发 leave 事件
        region.getLeftEntities().forEach(entity -> 
            callRegionEvent(region, EventType.EVENT_LEAVE_REGION, entity));
        region.resetEntityLeave();
    }
}
```

### 6.1 触发 region 事件 → 双路通知

```java
private void callRegionEvent(EntityRegion region, int eventType, GameEntity<?> entity) {
    // 路径 1: Lua trigger
    callEvent(new ScriptArgs(region.getGroupId(), eventType, region.getConfigId()) ...);
    
    // 路径 2: Quest 系统 (玩家进入区域 → quest cond)
    if (eventType == EventType.EVENT_ENTER_REGION && entity instanceof EntityAvatar avatar) {
        avatar.getPlayer().getQuestManager().queueEvent(
            QuestCond.QUEST_COND_PLAYER_ENTER_REGION, 
            region.getGroupId(), region.getConfigId());
    }
}
```

→ "**进入区域**"事件**同时触发 Lua + Quest**——这就是"走到这里剧情触发"的实现。

### 6.2 EntityRegion 模型

```
EntityRegion (球形/方形)
   - pos / size
   - getEntities() = 当前在内的
   - getNewEntities() = 这帧刚进的
   - getLeftEntities() = 这帧刚出的
   - getNotContainEntities() = 显式排除的
```

→ Region 内部维护**双缓冲**：新进 / 已在 / 刚出，避免重复触发。

---

## 7. ScriptLib 28 个 Handler：Lua API 暴露面

`scriptlib_handlers/` 目录有 **28 个 Java 类**——这是 Java 给 Lua 暴露的 API。

### 7.1 完整 28 个 Handler 分类

| 类别 | Handler | 暴露的 Lua API（部分）|
|---|---|---|
| **基础** | BaseHandler | createGadget, printTable 等通用 |
| **实体（怪/Gadget/Region/NPC）** | GroupMonsterHandler | createMonster / setMonsterHp / lockMonsterHp |
| | GroupGadgetHandler | createGadget / changeGadgetState |
| | GroupEntityHandler | getEntityXxx 系列 |
| | GroupRegionHandler | createRegion / removeRegion |
| **Gadget 子系统** | GadgetControllerHandler | gadget 控制器 |
| | PlatformHandler | startPlatform / stopPlatform |
| **场景** | SceneStateScriptHandler | setSceneState / getSceneState |
| | ScenePlayerHandler | playerPosition / playerSceneId |
| | WeatherHandler | setSceneWeather |
| **副本/挑战** | DungeonScriptHandler | dungeon 控制 |
| | ChallengeScriptHandler | startChallenge / endChallenge |
| | SealBattleScriptHandler | 封印之战控制 |
| **任务** | QuestScriptHandler | beginQuest / failQuest / addQuestProgress |
| **活动** | ActivityHandler | activity 状态 |
| | ChannelerSlabHandler | 须弥金字塔活动 |
| | SummerTimeHandler | 海岛 2.8 |
| | BlossomScriptHandler | 凋零之缘 |
| **怪物刷新** | AutoMonsterTideScriptHandler | 自动车轮战 |
| **Tower** | TowerHandler | 深境螺旋 |
| **Group 管理** | GroupManagementScriptHandler | refresh/load/unload group |
| **杂项** | TimeHandler | server time |
| | TimersScriptHandler | timer 创建/取消 |
| | VisionHandler | 视野管理 |
| | MiscNotifyHandler | 通用 notify |
| | LoggingHandler | 日志 |
| **供应器** | ScriptLibControllerHandlerProvider | controller 加载 |
| | ScriptLibGroupHandlerProvider | group 加载 |

### 7.2 GroupMonsterHandler 示例（13+ 方法）

```java
public int createMonster(GroupEventLuaContext context, CreateMonsterParameters parameters);
public int createMonsterWithGlobalValue(GroupEventLuaContext context, int configId, ...);
public int createMonsterByConfigIdByPos(...);
public int createMonsterFaceAvatar(...);
public int createMonstersFromMonsterPool(...);
public int getMonsterIdByEntityId(...);
public int getMonsterConfigId(...);
public int getMonsterHpPercent(...);
public List<Integer> getMonsterAffixListByConfigId(...);
public int lockMonsterHp(...);
public int unlockMonsterHp(...);
public int setMonsterHp(...);
public int setMonsterAIByGroup(...);
```

→ 1 个 Handler **13 个 API** —— 28 个 Handler **共暴露 200-300 个 Lua API**。

→ Lua 脚本可以调:
```lua
ScriptLib.createMonster(context, params)
ScriptLib.lockMonsterHp(context, configId)
ScriptLib.refreshGroup(context, groupId, suiteId)
ScriptLib.beginQuest(context, questId)
ScriptLib.createGadget(context, params)
ScriptLib.changeGroupVariable(context, key, value)
```

**这些就是 mihoyo 场景脚本里用的标准 API**——grasscutter 完全模仿。

### 7.3 注解+反射加载

这是**第 12 次"注解+反射"模式**（如果算上 Ability 是 13 次）：
- 28 个 Handler 通过 `ScriptLibControllerHandlerProvider` / `ScriptLibGroupHandlerProvider` 注册
- Lua 引擎按方法名查找对应 Java handler
- 加新 API：写一个新方法 + 标注解，无需改 SceneScriptManager

---

## 8. createGadget / createMonster 流程

```java
public EntityGadget createGadget(SceneGadget g, int state) {
    val gadgetData = GameData.getGadgetDataMap().get(g.getGadgetId());
    val createConfig = new CreateGadgetEntityConfig(g, gadgetData);
    val entity = new EntityGadget(scene, createConfig);
    
    entity.setState(state);
    entity.setOwnerEntity(...);
    // ... 各种字段初始化
    
    return entity;
}
```

→ Lua 调 `createGadget` → 这里实例化 → 返回 entity → addEntity 加到 Scene → 广播。

---

## 9. Quest Trigger 桥接

```java
// callTrigger 内部
val triggerData = GameData.getQuestTriggerDataByName(params.getGroupId(), trigger.getName());
if (triggerData != null) {
    getScene().getPlayers().forEach(p -> {
        p.getQuestManager().queueEvent(QuestContent.QUEST_CONTENT_TRIGGER_FIRE,
            triggerData.getId(), 0);
    });
}
```

→ **场景 Lua trigger** 与 **Quest 系统** 的桥：
- 任务表里有 `QuestTriggerData` 注册
- Lua trigger 触发时 → 检查是否有 quest 监听 → 触发 `QUEST_CONTENT_TRIGGER_FIRE`

**典型用例**：
- 任务"走到广场触发剧情"
- 场景 Lua 配 region trigger（"广场区域"）
- 任务表注册：`trigger_region_plaza → questContentTrigger_id_42`
- 玩家走进区域 → Lua trigger 触发 → QuestContent 触发 → 任务推进

→ Lua + Quest **双引擎协作**——这才是 mihoyo 任务剧情灵活性的根源。

---

## 10. callScriptFunc：实际调 Lua

```java
private LuaValue callScriptFunc(String funcName, SceneGroup group, ScriptArgs params) {
    val script = group.getScript();
    if (script == null) return BooleanLuaValue.FALSE;
    
    val context = new GroupEventLuaContext(script.getEngine(), group, params, this);
    try {
        return GIScriptHandler.callGroupFunction(script, funcName, context, params);
    } catch (RuntimeException | ScriptException | NoSuchMethodException error) {
        logger.error("[LUA] call trigger failed in group {} with {},{}",
            group.getGroupInfo().getId(), funcName, params, error);
        return BooleanLuaValue.FALSE;
    }
}
```

### 10.1 GroupEventLuaContext

每次 Lua 调用都创建一个 **per-call context**：
- `script.getEngine()` —— Lua 引擎实例
- `group` —— 当前 group
- `params` —— 事件参数
- `this` —— SceneScriptManager 反向引用

→ Lua 通过 context 反向调 Java API（"ScriptLib.xxx"）。

### 10.2 错误处理

```java
catch (RuntimeException | ScriptException | NoSuchMethodException error) {
    logger.error("[LUA] call trigger failed in group {} with {},{}", ...);
    return BooleanLuaValue.FALSE;
}
```

→ Lua 抛异常 **不影响主循环** —— 单个脚本错误不会卡死服务器。
→ 注释里有具体 group ID 提示："302001042" —— 已知有 lua 错误的脚本。

---

## 11. Timer 系统：Lua 可创建定时事件

```java
public int createGroupTimerEvent(int groupID, String source, double time) {
    val timer = new Pair<>(source, ...);
    
    // 用 Server scheduler 注册延迟任务
    Grasscutter.getGameServer().getScheduler().scheduleDelayedTask(() -> {
        callEvent(new ScriptArgs(groupID, EVENT_TIMER_EVENT)
            .setEventSource(source));
    }, (int)(time * 60));   // 60 ticks per second
    
    activeGroupTimers.computeIfAbsent(groupID, k -> ConcurrentHashMap.newKeySet()).add(timer);
    return 0;
}
```

→ Lua 可调 `createGroupTimerEvent` → N 秒后触发 `EVENT_TIMER_EVENT` → 自身脚本继续处理。

→ "等 5 秒后下一波刷怪" 这类**剧情节奏控制**的实现。

### 11.1 cancelGroupTimerEvent

```java
public int cancelGroupTimerEvent(int groupID, String source) {
    // 从 activeGroupTimers 移除
    // 注意: scheduler 内的延迟任务**仍会执行** -- 但 callEvent 时找不到 trigger 就跳过
}
```

→ 没有真正取消 scheduler 任务——靠"找不到 trigger 就 noop"实现。

---

## 12. isClearedGroupMonsters：组清空检测

```java
public boolean isClearedGroupMonsters(int groupId) {
    val groupInstance = getCachedGroupInstanceById(groupId);
    if (groupInstance == null) return false;
    val group = groupInstance.getLuaGroup();
    val groupMonsters = group.getMonsters();
    if (groupMonsters == null || groupMonsters.isEmpty()) return false;
    
    // 检查所有原配怪是否都死了
    return groupMonsters.values().stream()
        .allMatch(m -> groupInstance.getDeadEntities().contains(m.getConfigId()));
}
```

→ notes/32 §11 引用的"组清空"判断 —— 配怪全部死亡才返回 true。

→ "打完这一组怪开宝箱"的实现机制：
1. 玩家打死最后一只 → onDeath
2. Lua trigger 监听 EVENT_ANY_MONSTER_DIE
3. trigger.action: `if isClearedGroupMonsters(groupId) then refreshGroupSuite(groupId, 2) end`
4. suite 2 包含宝箱 → 玩家看到宝箱出现

---

## 13. ThreadLocal + ScriptLib 上下文传递

注释说明 ScriptLib 怎么拿到 SceneScriptManager 引用：

```
[Lua trigger 调用流程]
    callEvent → callScriptFunc
        → ThreadLocal.set(context)
        → Lua 引擎执行 trigger.action
            → Lua 调 ScriptLib.createMonster(...)
                → Java 内部: 从 ThreadLocal 取 context
                → context.getSceneScriptManager().createMonster(...)
        → Lua 返回
        → ThreadLocal.remove
```

**为什么用 ThreadLocal**：
- ✓ Lua 不需要传递 manager 引用（每次调用自动注入）
- ✓ 性能（避免每次重新 eval 脚本）

**踩坑**：
- ✗ Lua 内嵌套调 callEvent → 内层 remove 把外层 context 也清了
- → 解决：每次 callEvent 用新线程（4 池）—— 各自独立 ThreadLocal

---

## 14. 完整时序：玩家走进区域触发剧情

把所有部分串起来：

```
[阶段 1: 场景加载]
Scene.<init>:
  scriptManager = new SceneScriptManager(this)
    ↓ init
  加载 SceneMeta (block + group)
  注册默认 suite 的 triggers
  
[阶段 2: 玩家进入]
HandlerEnterSceneDoneReq:
  loadNpcForPlayerEnter
  loadGroupForQuest:
    scriptManager.refreshGroup(groupId, suiteId)
      ↓
    addGroupSuite:
      registerTrigger × N
      addEntities (gadget + monster)
      registerRegionInGroupSuite
  
[阶段 3: 玩家移动]
Scene.onTick (每秒):
  scriptManager.checkRegions:
    遍历所有 EntityRegion
      isPosInRegion(玩家) ? 进入 : 不在
      新进的 → callRegionEvent(EVENT_ENTER_REGION)
        ↓
      callEvent → eventExecutor.execute (异步)
        ↓
      realCallEvent:
        过滤 triggers (groupId + source)
        handleEventForTrigger:
          evaluateTriggerCondition → callScriptFunc("condition_xxx")
          true → callTrigger:
            callScriptFunc("action_xxx")  ← ★ Lua 执行剧情
            
[阶段 4: Lua 执行]
function action_enter_region(context, evt)
    -- 通过 ScriptLib 调用 Java API
    ScriptLib.refreshGroupSuite(context, 210101, 2)  ← 切场景到 suite 2
    ScriptLib.createMonster(context, params)         ← 召唤敌人
    ScriptLib.beginCutscene(context, 401012001)       ← 播放过场
end
    ↓
[阶段 5: 副作用]
refreshGroupSuite:
  removeGroupSuite (旧 suite 的 monster/gadget)
  addGroupSuite (新 suite)
  callEvent(EVENT_GROUP_REFRESH)
    ↓
[阶段 6: 客户端通知]
PacketGroupSuiteNotify({groupId → newSuiteId})
  ↓
客户端按新 suite 渲染 NPC / Gadget
```

→ **从"玩家走到这"到"剧情触发"涉及 6 阶段 + 10+ 次跨层调用**。

---

## 15. 设计模式总结

### 15.1 4 线程 + ThreadLocal + 异步

```
4 线程池 + 每 task 独立 ThreadLocal = 避免嵌套 ScriptLib 调用 NPE
```

### 15.2 倒排索引（按 eventType）

```java
Map<Integer, Set<SceneTrigger>> currentTriggers;
```

→ 事件触发 O(1) 查找—不遍历所有 trigger。

### 15.3 5 个 refreshGroup 重载

```
为切 suite 的不同上下文准备 5 套接口
```

→ 调用方不用关心内部实现差异。

### 15.4 Provider 模式注册 ScriptLib

```
ScriptLibControllerHandlerProvider / ScriptLibGroupHandlerProvider
   → 28 个 Handler
   → 200+ Lua API
```

→ **第 12+ 次"注解+反射+自动注册"**——加新 API 零代码改动。

### 15.5 自动反注册的 trigger

```
Lua 返回 false / count 到上限 → deregister
```

→ trigger 默认一次性——避免"重复触发剧情"。

---

## 16. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 客户端发"我进入区域" packet | ✗ 服务器算位置 |
| 篡改 group/suite | ✗ 服务器存 |
| Lua 注入 | ✗ Lua 在服务器跑 |
| 飞天遁地避开 region trigger | ✓ 有效 (位置 client) |
| 跳过剧情 trigger | ✓ 有效 (cutscene 客户端) |

→ Lua 引擎本身**反作弊较强** —— 客户端无法直接触发 trigger，只能通过位置/talk/plot 间接触发。

---

## 17. 关键收获

1. **SceneScriptManager 1075 行** —— grasscutter 第 4 大类
2. **15+ 核心数据结构**：4 个 trigger map + region map + 3 层 group map + 2 个 monster service + cache 等
3. **第 13 次"4 线程异步池"**：队列 10000 是全系统最大
4. **callEvent 必须异步**：避免 ThreadLocal 嵌套调用 NPE
5. **Trigger 过滤 2 维度**：groupId + source（类似 Pub/Sub 主题）
6. **trigger 自动反注册 3 种条件**：返回 false / 非 0 整数 / 达上限——默认一次性
7. **3 层 Group 抽象**：SceneGroup (配表) → SceneGroupInstance (运行时) → cached (持久化)
8. **refreshGroup 5 个重载**：覆盖 5 种切 suite 上下文
9. **addGroupSuite 3 步**：trigger → entity → region（顺序很关键）
10. **checkRegions 每 tick 检查**：双缓冲（新进/已在/刚出）避免重复触发
11. **Region 双路通知**：Lua trigger + Quest QUEST_COND_PLAYER_ENTER_REGION
12. **28 个 scriptlib_handlers** 暴露 **200+ Lua API**
13. **QuestTriggerData 桥接**：Lua trigger 名 → Quest content trigger ID
14. **ThreadLocal + ScriptLib**：Lua 通过 context 反向调 Java
15. **Timer 系统**：Lua createGroupTimerEvent → N 秒后 EVENT_TIMER_EVENT
16. **isClearedGroupMonsters**：组配怪全死才 true
17. **第 12 次"注解+反射"模式**：Provider 注册 ScriptLib

---

## 18. 一句话总结

> **SceneScriptManager = Lua 引擎运行时核心 (1075 行) + 28 个 scriptlib_handlers 暴露 200+ Lua API + 第 13 次"4 线程异步池" (10000 队列) + ThreadLocal 上下文传递 + Trigger 倒排索引 + Region 双缓冲 + 3 层 Group 抽象 + 5 个 refreshGroup 重载。**
> 
> **设计哲学: Lua 配表驱动 + Java 提供 API + 4 线程异步避免嵌套陷阱 + 自动反注册防重复触发——是 grasscutter 中"逻辑可配置化"做得最彻底的子系统。任何剧情节奏 (spawn 怪/切 suite/播 cutscene/触发任务) 都写在 Lua 里, 加新内容只改 Lua 不动 Java.**

---

**前置笔记**：
- notes/14 SceneScript 系统 - 配表设计层
- notes/27 架构模式 - 第 12 次注解反射 + 第 13 次 4 线程
- notes/32 怪物 - Lua spawn / Tide
- notes/33 Gadget - refreshGroup 切 gadget
- notes/41 事件总线 - EventType.EVENT_* 30+
- notes/42 表演系统 - ExecNotifyGroupLua
- notes/43 Quest 引擎 - QuestTriggerData 桥接

**关联文件**：
- `SceneScriptManager.java`(1075) - 运行时核心
- `scriptlib_handlers/`/*.java × 28 - Lua API 暴露
- `lua_engine/GroupEventLuaContext.java` - Lua 上下文
- `lua_engine/service/ScriptMonsterSpawnService.java` - 怪物 spawn
- `lua_engine/service/ScriptMonsterTideService.java` - 车轮战
- `SceneIndexManager.java` - R-Tree 空间索引

**研究的源代码**: 1075 行 SceneScriptManager + 28 个 handler 文件名 + 200+ Lua API 推断。
