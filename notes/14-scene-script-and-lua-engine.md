# 14 · Scene Script 系统 · 任务系统的镜像兄弟

任务系统之外的另一半世界——**场景动态行为引擎**。它和任务系统**架构同构但语义独立**：同样的事件总线 + 倒排索引 + 异步线程池模式，跑的是怪物/机关/区域/天气/副本。

> **范畴**：`emu/grasscutter/scripts/` (核心) + `emu/grasscutter/game/world/` (运行时) + `org.anime_game_servers.gi_lua.*` (外部 Lua 接口)

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│ Scene (一张地图实例, 例如 "蒙德城" / "教令院图书馆")               │
│   └── SceneScriptManager                                         │
│         ├── Map<groupId, SceneGroup>          所有 Group 元数据    │
│         ├── Map<groupId, SceneGroupInstance>  当前活跃实例 (变量)  │
│         ├── Map<eventType, Set<SceneTrigger>> 倒排索引             │
│         └── 4 线程异步线程池 eventExecutor                          │
└─────────────────────────────────────────────────────────────────┘

每个 SceneGroup:
   ├── Monsters[]   配置 ID → 模板 (位置, 等级, 类型)
   ├── Gadgets[]    机关/物体 (宝箱、机关、风车...)
   ├── Regions[]    触发区域 (进入/离开事件)
   ├── Triggers[]   事件回调 (condition + action 函数名)
   ├── Suites[]     ★ 多套配置变体 ★
   └── Variables[]  group 级变量

每个 Suite (Group 的一个"快照"):
   ├── 包含哪些 monster/gadget/region (按 configId 引用)
   ├── 包含哪些 trigger
   └── banRefresh 标志 (不能被立刻替换)
```

**关键概念**：**Group 是容器，Suite 是它的多个状态变体**。任务/脚本可以让 Group 在不同 Suite 之间切换——这就是「做完任务后村子里的怪消失了」、「触发机关后场景配置变了」的实现。

---

## 2. 整套 EVENT_* 类型清单

从全代码扫描出场景脚本系统支持的事件（不完全列表，按主题分类）：

### 2.1 Group 生命周期
- `EVENT_GROUP_LOAD` - Group 首次加载到 Scene
- `EVENT_GROUP_REFRESH` - Group 切换 Suite

### 2.2 实体事件
- `EVENT_GADGET_CREATE` - 机关被创建
- `EVENT_GADGET_STATE_CHANGE` - 机关状态变化（开/关/激活）
- `EVENT_ANY_GADGET_DIE` - 任何机关被摧毁
- `EVENT_BLOSSOM_CHEST_DIE` - 大赏宝箱被打开
- `EVENT_SPECIFIC_GADGET_HP_CHANGE` - 特定机关 HP 变化
- `EVENT_ANY_MONSTER_LIVE` - 任何怪物存活（可能新生成）
- `EVENT_ANY_MONSTER_DIE` - 任何怪物死亡
- `EVENT_SPECIFIC_MONSTER_HP_CHANGE` - 特定怪 HP 变化
- `EVENT_MONSTER_BATTLE` - 怪物进入战斗
- `EVENT_GATHER` - 采集动作（采花/挖矿）

### 2.3 区域事件
- `EVENT_ENTER_REGION` - 玩家/实体进入区域
- `EVENT_LEAVE_REGION` - 玩家/实体离开区域

### 2.4 玩家行为
- `EVENT_SELECT_OPTION` - 选择 worktop 选项（机关菜单）
- `EVENT_AVATAR_NEAR_PLATFORM` - 角色靠近平台
- `EVENT_PLATFORM_REACH_POINT` - 平台到达路径点
- `EVENT_UNLOCK_TRANS_POINT` - 解锁传送点

### 2.5 任务联动
- `EVENT_QUEST_START` - 任务接取（来自 NOTIFY_GROUP_LUA）
- `EVENT_QUEST_FINISH` - 任务完成

### 2.6 副本/挑战
- `EVENT_DUNGEON_SETTLE` - 副本结算
- `EVENT_DUNGEON_REWARD_GET` - 副本奖励
- `EVENT_CHALLENGE_SUCCESS` - 挑战成功
- `EVENT_CHALLENGE_FAIL` - 挑战失败
- `EVENT_MONSTER_TIDE_DIE` - 怪潮中怪死亡（递增计数）
- `EVENT_BLOSSOM_PROGRESS_FINISH` - 大赏进度完成
- `EVENT_SEAL_BATTLE_BEGIN` / `EVENT_SEAL_BATTLE_END` / `EVENT_SEAL_BATTLE_PROGRESS_DECREASE` - 封印之战

### 2.7 时间/变量
- `EVENT_TIMER_EVENT` - 计时器到点
- `EVENT_VARIABLE_CHANGE` - Group 变量变化

### 2.8 通用通知
- `EVENT_LUA_NOTIFY` - 通用 Lua 通知（被 ability 系统调用）

→ **30+ 种事件类型**，覆盖整个开放世界的所有动态行为。每种事件都按 `(eventType, groupId, source)` 三元组路由到对应的 SceneTrigger。

---

## 3. SceneTrigger 注册 + 派发流程

### 3.1 注册（Suite 加载时）

```java
// SceneScriptManager.java:200
triggersByGroupScene.put(groupId+"_"+suiteIndex, groupSceneTriggers);
```

每个 SceneTrigger 含：

```
SceneTrigger {
    name: "Trigger_Q3022_OnEnterLibrary",
    event: EVENT_ENTER_REGION,
    source: "302207",           // 可空; 用于精确过滤
    groupId: 133003051,
    condition: "condition_can_enter",   // Lua 函数名
    action: "action_spawn_alhaitham",   // Lua 函数名
    triggerCount: 1                     // -1 表示无限
}
```

### 3.2 派发（事件来了）

```java
// SceneScriptManager.java:745
public void callEvent(@Nonnull ScriptArgs params) {
    eventExecutor.execute(() -> realCallEvent(params));   // 异步隔离
}

private void realCallEvent(ScriptArgs params) {
    Set<SceneTrigger> relevantTriggers = getTriggersByEvent(params.type).stream()
        .filter(t -> params.getGroupId() == 0 || t.getGroupId() == params.getGroupId())
        .filter(t -> t.getSource().isEmpty() || t.getSource().equals(params.getEventSource()))
        .collect(Collectors.toSet());

    for (SceneTrigger trigger : relevantTriggers) {
        // 1. 调 Lua condition 函数（必须返回 true 才继续）
        if (evaluateTriggerCondition(trigger, group, params)) {
            // 2. 调 Lua action 函数
            callTrigger(trigger, group, params);
        }
    }
}
```

### 3.3 自动反注册

`callTrigger` 末尾（`SceneScriptManager.java:838`）：

```java
val invocations = invocationsCounter.incrementAndGet();
// 单次触发器或达到次数 → 反注册
if (callResult is false 
    || trigger.getTriggerCount() > INF_TRIGGERS && invocations >= trigger.getTriggerCount()) {
    deregisterTrigger(trigger);
}
```

→ **trigger 自动管理生命周期**——一次性事件用完就清掉，避免 trigger 表无限增长。

---

## 4. Lua API：服务器暴露给脚本的能力（约 100+ 函数）

所有 `scriptlib_handlers/` 下的类都是**Java 方法暴露给 Lua 脚本**的 API。Lua 脚本（在客户端 / 配表里）通过这些方法操作场景。按主题分组：

### 4.1 Group / Suite 管理（GroupManagementScriptHandler）
```lua
GoToGroupSuite(groupId, suiteId)        -- 切换 group 到指定 suite
AddExtraGroupSuite(groupId, suiteId)    -- 叠加加载一个 suite
RemoveExtraGroupSuite(groupId, suiteId)
KillExtraGroupSuite(groupId, suiteId)   -- 移除 + kill 内部实体
RefreshGroup(params)                    -- 刷新 (kill+respawn) 一个 group
SetGroupReplaceable(groupId, value)
```

### 4.2 Group 变量（GroupManagementScriptHandler 后半段）
```lua
CreateGroupVariable(name, value)
GetGroupVariableValue(name)
GetGroupVariableValueByGroup(name, groupId)
SetGroupVariableValue(name, value)      -- fire EVENT_VARIABLE_CHANGE
ChangeGroupVariableValue(name, delta)
```

→ 与 Quest var 平行的另一套变量系统，**只在 group 内部生效**。变化时 fire `EVENT_VARIABLE_CHANGE`，本 group 内 trigger 可订阅。

### 4.3 机关控制（GroupGadgetHandler）
```lua
ChangeGroupGadget(configId, state)           -- 改机关状态
SetGadgetStateByConfigId(configId, state)
GetGadgetStateByConfigId(groupId, configId)
GetGadgetHpPercent(groupId, configId)
CreateGadget(configId)                       -- 创建机关
SetGadgetEnableInteract(groupId, configId, enable)
SetWorktopOptions(options)                   -- 设置 worktop 菜单选项
DelWorktopOption(option)
CheckRemainGadgetCountByGroupId(params)
```

→ 「读完笔记后机关激活」、「打完怪后宝箱出现」、「机关菜单选项动态增减」全靠这些 API。

### 4.4 怪物（GroupMonsterHandler）
```lua
GetGroupAliveMonsterList(groupId)
GetGroupMonsterCountByGroupId(groupId)
KillGroupMonster(...)
SetMonsterFightProperty(...)
```

### 4.5 区域（GroupRegionHandler）
```lua
GetGroupRegionByConfigId(...)
CheckIsInGroup(groupId, configId)
```

### 4.6 副本/挑战（DungeonScriptHandler / ChallengeScriptHandler）
```lua
ActiveChallenge(challengeId, ...)
StartDungeon(dungeonId)
SettleDungeon(success)
GetDungeonRoster(...)
```

### 4.7 大赏（BlossomScriptHandler）
```lua
SetBlossomMonsterProgress(...)
RefreshBlossomGroup(groupId)
SpawnBlossomChest(...)
```

### 4.8 怪潮（AutoMonsterTideScriptHandler）
```lua
StartMonsterTideTrigger(groupId, count, monsters)
StopMonsterTide(...)
```

### 4.9 计时器（TimersScriptHandler）
```lua
CreateGroupTimerEvent(groupId, source, duration)  -- N 秒后 fire EVENT_TIMER_EVENT
CancelGroupTimerEvent(groupId, source)
```

→ **场景级定时器**，独立于全局玩家 onTick，由 group 自管。

### 4.10 任务桥（QuestScriptHandler，已在 notes/08 看过）
```lua
AddQuestProgress(eventNotifyName)   -- fire QUEST_*_LUA_NOTIFY 给玩家
GetHostQuestState(questId)
GetQuestState(entityId, questId)
```

### 4.11 其他
- `WeatherHandler` - 天气切换
- `TowerHandler` - 深境螺旋
- `SealBattleScriptHandler` - 封印之战
- `ChannelerSlabHandler` - 须弥某个谜题机关
- `SummerTimeHandler` - 夏日活动
- `VisionHandler` - 视野/迷雾
- `MiscNotifyHandler` - 杂项通知
- `LoggingHandler` - 日志（Lua 调试用）

---

## 5. 真实流程示例：玩家进入区域 → 触发剧情怪物生成

```
[玩家走进区域]
   ↓
SceneScriptManager.checkRegions() (每帧/onTick)
   ↓
检测 region.getNewEntities() 含玩家  
   ↓
callRegionEvent(region, EventType.EVENT_ENTER_REGION, entity)
   ↓
   eventExecutor 异步线程池
   ↓
realCallEvent(ScriptArgs{
    groupId: 133003051,
    type: EVENT_ENTER_REGION,
    sourceEntityId: <player>,
    targetEntityId: <region>
})
   ↓
按 (EVENT_ENTER_REGION, groupId=133003051, source) 三元组找 trigger
   找到 trigger: { name="Trigger_Ambush_OnEnter", action="action_spawn_ambush" }
   ↓
evaluateTriggerCondition(trigger, ...)
   → 调 Lua: condition_check_quest_state("302207")
   → Lua 返回 true (玩家在剧情里)
   ↓
callTrigger(trigger, ...)
   → 调 Lua: action_spawn_ambush()
       Lua 内部调:
         CreateMonster(monsterCfgId)  ← 创建埋伏怪
         SetGadgetStateByConfigId(...) ← 启动陷阱
         AddQuestProgress("Q302207_AmbushTriggered")  ← 通知任务系统
   ↓
deregisterTrigger(trigger) (单次触发器，用完即弃)
   ↓
[副作用]
   - 怪物在场景中出现（通过 PacketSceneEntityAppearNotify 推给客户端）
   - 任务系统接到 QUEST_CONTENT_LUA_NOTIFY["Q302207_AmbushTriggered"] 事件
   - 关心这个事件的 SubQuest 推进进度
```

→ **场景脚本 + 任务系统 + 实体系统三方协作**——但每一方都不直接调用对方，全靠事件总线。**完美解耦**。

---

## 6. ThreadLocal 的踩坑记录（重要工程经验）

`SceneScriptManager.callEvent` 的注释里直接写了：

```java
/**
 * We use ThreadLocal to trans SceneScriptManager context to ScriptLib,
 * to avoid eval script for every groups' trigger in every scene instances.
 *
 * But when callEvent is called in a ScriptLib func, it may cause NPE because
 * the inner call cleans the ThreadLocal so that outer call could not get it.
 * e.g. CallEvent -> set -> ScriptLib.xxx -> CallEvent -> set -> remove -> NPE -> (remove)
 *
 * So we use thread pool to clean the stack to avoid this new issue.
 */
eventExecutor.execute(() -> this.realCallEvent(params));
```

→ Lua 调 ScriptLib 函数时，函数内可能再 fire 新事件——形成嵌套调用链。**ThreadLocal 在嵌套清理时会出 NPE**。解决方案：**强制每个事件投递走异步线程池**，物理隔离调用栈。

这是踩过坑后才有的设计决策，**不是早期设计**。这种"血泪经验代码"比文档更值得读。

---

## 7. Quest vs Scene Script 系统对比

| 维度 | Quest 系统 | Scene Script 系统 |
|---|---|---|
| **作用域** | 玩家级（每个玩家有独立 QuestManager）| 场景级（每个 Scene 一个 SceneScriptManager）|
| **数据来源** | `BinOutput/Quest/<id>.json` | `BinOutput/Scene/Lua/<sceneId>/...`（客户端，我们看不到）|
| **事件类型** | QuestCond / QuestContent / QuestExec | EventType (30+) |
| **倒排索引** | beginCondQuestMap (按 acceptCond) | triggersByEvent (按 eventType) |
| **状态机** | SubQuest 4 状态 (UNSTARTED/UNFINISHED/FINISHED/FAILED) | Group 通过 Suite 切换 |
| **变量** | questVar (5个/MainQuest)、globalVar | groupVariable (per group) |
| **副作用** | finishExec 数组 | Lua 脚本里调 ScriptLib API |
| **桥接对方** | NOTIFY_GROUP_LUA (→ Lua) <br> ADD_QUEST_PROGRESS (← Lua) | EVENT_QUEST_START/FINISH (← Quest) <br> AddQuestProgress (→ Quest) |
| **异步线程池** | 4 线程 ✓ | 4 线程 ✓ |
| **代码复杂度** | ~2500 行核心 + 80 handler | ~3000 行核心 + 100+ Lua API handler |

**架构同构 + 语义独立** —— 这是大型 MMORPG 的标准做法：**子系统之间用事件总线交互，不互相暴露内部状态**。

---

## 8. Group Suite 实例理解：3022 中的"潜入"

设想 3022「识藏日」的场景配置：

**Group 133003051 (教令院图书馆区域)**:
- `Suite 1 = 平时状态`：守卫巡逻、其他学者闲逛、艾尔海森还没来
- `Suite 2 = 任务接取后`：艾尔海森已在门口、其他学者数量减少（"识藏日"忙）
- `Suite 3 = 潜入失败状态`：警报响起、卫兵增援、玩家被关
- `Suite 4 = 任务完成`：纳西妲被救出、艾尔海森离开

**3022 的 finishExec 切换 Suite**：
```jsonc
"finishExec": [
    { "type": "QUEST_EXEC_REFRESH_GROUP_SUITE", "param": ["3", "133003051", "2"] }
]
```
→ scene 3, group 133003051, switch to suite 2

**Suite 切换的物理过程**（`SceneScriptManager.refreshGroup` 226 行）：

```java
1. 找到目标 suite 数据 (suiteIndex → SceneSuite)
2. 检查 banRefresh 冲突: 如前 suite 标 banRefresh, 等一帧再切
3. removeGroupSuite(group, prevSuiteData)   ← 物理上销毁旧 suite 实体
   - kill 怪物 (broadcast EntityFightProp + Disappear)
   - destroy gadget
   - deregister region/trigger
4. addGroupSuite(groupInstance, suiteData)  ← 物理上加载新 suite 实体
   - createMonster + spawnMonsters (broadcast EntityAppear)
   - createGadget
   - registerRegion
   - register triggers (本 suite 的 trigger 现在生效)
5. 重置 group 变量 (除标记 noRefresh 的)
6. 设置 activeSuiteId = new suite
7. fire EVENT_GROUP_REFRESH (本 group 自己也能响应)
8. broadcastPacket(PacketGroupSuiteNotify) 通知客户端
```

→ **一次 suite 切换 = 实体 diff + trigger 表 swap + 客户端通知**。整个过程对玩家来说就是"突然多了几个 NPC，少了几个怪"。

---

## 9. 为什么这套架构能 scale

原神世界有：
- 数百个 Scene
- 每个 Scene 几十到几百个 Group
- 每个 Group 多个 Suite
- 每个 Suite 数十个实体 + 数个 Trigger

不发疯的关键是：

1. **Scene 是隔离单元**——切换 Scene 就完全卸载所有 group/trigger
2. **Group 是按需加载**——`getGroupById` 只在被引用时才 init
3. **Suite 是状态压缩**——同一 group 的 N 种状态用 N 个 suite 表达，不需要 N 个独立 group
4. **Trigger 自动反注册**——单次/N次 trigger 用完即清，长期运行不堆积
5. **倒排索引**——eventType → Set<Trigger>，event 派发 O(1) 找候选
6. **异步线程池**——避免嵌套调用 NPE，提升吞吐
7. **Group Variable 隔离**——group 间不共享变量，scope 明确

这套设计能撑住开放世界的所有动态行为——从「点机关开门」到「BOSS 战阶段切换」到「副本掉落条件」。

---

## 10. 给做开放世界游戏开发者的总结

如果你做类似系统：

1. **场景动态行为必须有引擎层抽象**——不要写在每个怪物/机关的 hardcode 里
2. **Group + Suite 是"压缩状态空间"的关键**——别为每个状态做新 group
3. **事件总线 + 倒排索引可以复用**——任务系统怎么做，场景系统也怎么做
4. **Lua API 的设计要能用 1-2 个简单函数表达 99% 操作**——SetGadgetState、CreateMonster、AddQuestProgress 这种粒度刚好
5. **异步隔离调用栈是必需的**——嵌套触发器会引入各种诡异并发 bug
6. **Trigger 必须能自反注册**——长期运行的服务器会因 trigger 泄漏崩
7. **Variable 系统要有作用域**——quest var / group var / global var 三层

---

## 参考代码位

- 主调度：`Grasscutter-Quests/src/main/java/emu/grasscutter/scripts/SceneScriptManager.java`（1100+ 行）
- Group 实例：`Grasscutter-Quests/src/main/java/emu/grasscutter/game/world/SceneGroupInstance.java`
- Scene 主类：`Grasscutter-Quests/src/main/java/emu/grasscutter/game/world/Scene.java`（1400+ 行）
- Lua API 入口：`Grasscutter-Quests/src/main/java/emu/grasscutter/scripts/scriptlib_handlers/`（25 个 handler 文件）
- 怪物服务：`Grasscutter-Quests/src/main/java/emu/grasscutter/scripts/lua_engine/service/ScriptMonsterSpawnService.java`、`ScriptMonsterTideService.java`
- 实体源头（事件发出方）：`Grasscutter-Quests/src/main/java/emu/grasscutter/game/entity/EntityMonster.java`、`EntityGadget.java`
