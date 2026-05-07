# 08 · Talk 对话系统 与 Lua 脚本桥

两个相关的子系统。**Talk = 玩家叙事入口，Lua = 场景动态逻辑入口**——它们都通过事件总线和任务系统对接。

---

# Part A · Talk 对话系统

## A1. 三层数据架构

```
┌──────────────────────────────────────────────────────────┐
│ Layer 1: TalkExcelConfigData.json (38 MB, 全局 Talk 索引) │
│   talkId → { beginWay, npcId, initDialog, performCfg, ...}│
└──────────────────────────────────┬───────────────────────┘
                                   ↓ initDialog
┌──────────────────────────────────────────────────────────┐
│ Layer 2: BinOutput/Talk/<hash>.json (27 个对话包)        │
│   或者 MainQuest.talks 数组                               │
│   存放具体对话节点序列 (textMapHash + 角色 + 选项)         │
└──────────────────────────────────┬───────────────────────┘
                                   ↓ performCfg / luaPath
┌──────────────────────────────────────────────────────────┐
│ Layer 3: 客户端 Lua 对话脚本 (我们看不到)                  │
│   QuestDialogue/AQ/Sumeru3_3022/Q302201.lua               │
│   控制实际呈现：表情、动画、镜头、特效                     │
└──────────────────────────────────────────────────────────┘
```

## A2. Talk 在 ExcelBinOutput 里的元数据结构

```jsonc
{
    "id": 1,
    "beginWay": "TALK_BEGIN_MANUAL",         // MANUAL=玩家点 NPC, AUTO=进入区域自动
    "activeMode": "PLAY_MODE_SINGLE",
    "beginCond": [...],                      // 何时这条 Talk 可见
    "priority": 3,                           // 优先级（同时多条可用时决胜）
    "nextTalks": [],                         // 此 Talk 完成后能链到的下一条
    "initDialog": 101,                       // → 对话节点起始 ID
    "nextRandomTalks": [],                   // 随机分支
    "npcId": [9004],                         // 说话的 NPC
    "participantId": [],                     // 其他参与者
    "performCfg": "QuestDialogue/Test/InterContainer_Test1",   // 客户端 Lua 表演脚本
    "extraLoadMarkId": [],                   // 额外加载的标记
    "prePerformCfg": "",                     // 对话前的预表演（CG 等）
    "talkMarkHideList": [],                  // 隐藏哪些 NPC 头顶提示
    "crowdLOD0List": [],                     // 人群 LOD 配置
    "finishExec": [...]                      // 对话完成后的副作用
}
```

## A3. 对话节点的实际形态（BinOutput/Talk）

```jsonc
{
    "id": 4010313,
    "type": "FREE",                          // OMNDEBJIOCP（混淆 key）→ type
    "dialogList": [                          // KJNKFMPAGAA → dialogList
        {
            "id": 401038001,
            "talkRole": { "_type": "TALK_ROLE_NPC", "_id": "1064" },
            "textMapHash": null              // (本节点没文本，可能是动作占位)
        },
        {
            "id": 401038002,
            "talkRole": { "_type": "TALK_ROLE_NPC", "_id": "13151" },
            "textMapHash": 2253853730        // 这条有文本
        },
        {
            "id": 401038003,
            "talkRole": { "_type": "TALK_ROLE_NPC", "_id": "1064" },
            "textMapHash": null
        }
        // ... 后面继续 401038004, 401038005, ...
    ]
}
```

注意：
- 节点 ID 严格顺序 `401038001 → 002 → 003 → ...`——**线性叙事用数组顺序作为隐式 next 链**
- 选项分支会用显式 `nextDialogs` 字段（这个简单例子里没有）
- `_type` / `_id` 是字面 key（**不混淆**），跟 SubQuest 的混淆字段不同

## A4. 真正的"哑"服务器：Talk 实际怎么走

`HandlerNpcTalkReq.java` 全部源码（50 行）：

```java
public void handle(GameSession session, byte[] header, NpcTalkReq req) {
    int talkId = req.getTalkId();
    int mainQuestId = GameData.getQuestTalkMap().getOrDefault(talkId, talkId / 100);
    val mainQuestData = GameData.getMainQuestDataMap().get(mainQuestId);

    if (mainQuestData != null) {
        // 把 talk 加到玩家"已说"列表
        var talkForQuest = new TalkData(talkId, "");
        // ... 查找 talkForQuest ...
        mainQuest.getTalks().put(talkId, talkForQuest);
    }

    // 发 3 个事件给任务系统
    questManager.queueEvent(QuestContent.QUEST_CONTENT_COMPLETE_ANY_TALK, talkId, 0, 0);
    questManager.queueEvent(QuestContent.QUEST_CONTENT_COMPLETE_TALK, talkId, 0);
    questManager.queueEvent(QuestCond.QUEST_COND_COMPLETE_TALK, talkId, 0);

    // 回个空 Rsp
    session.send(new PacketNpcTalkRsp(req.getNpcEntityId(), req.getTalkId(), req.getEntityId()));
}
```

### 重磅认知：服务器不知道对话内容

整个 Grasscutter 仓库**没有 `Handler*Dialog*Req` / `DialogSelectReq` / `ShowDialogRsp`**。Talk 流程是这样的：

```
[玩家点 NPC]  
   ↓
[客户端]
   1. 自己加载 TalkExcelConfigData[talkId]
   2. 自己读 initDialog 和 performCfg 路径
   3. 自己跑客户端 Lua 脚本（QuestDialogue/.../Qxxx.lua）
   4. 自己渲染对话框、文本、选项、动画
   5. 玩家选择选项 → 客户端自己处理跳转
   6. 整段对话完整 plays out
   ↓
[客户端] 发 NpcTalkReq{talkId} 给服务器，告知"talk X 完成了"
   ↓
[服务器]
   1. 标记 talk 已完成
   2. fire QUEST_CONTENT_COMPLETE_TALK 事件
   3. 任务系统看是否有任务关心 → 推进进度
   4. 回空 Rsp
```

**服务器从头到尾不知道对话说了什么。** 它只是：
- "客户端告诉我 talk 1234567 完成了"
- "我看看哪些任务订阅了 talk 1234567"
- "推进它们"

这与之前 notes/01 修正的"混合权威"模型完全一致——**对话渲染是客户端权威**，服务器只做**完成事实的公证人**。

## A5. 但 TALK_EXEC_* 怎么办？

我们在 corpus 里看到 `TALK_EXEC_SET_QUEST_VAR` 71 次、`TALK_EXEC_INC_QUEST_VAR` 15 次。如果服务器不知道对话内容，怎么知道哪个选项要 SET_QUEST_VAR？

**答案**：客户端在玩家选完选项后，**单独发请求**告诉服务器"请改 questVar"。可能通过：
- `QuestUpdateQuestVarReq`（我们看到过的 packet）
- 或一个专门的 `DialogActionReq`（在 Grasscutter 里没找到，可能在新版协议里）

但**逻辑是相同的**：客户端处理对话流，遇到 TALK_EXEC 就发对应的 Req 上来。服务器对每个 Req 应用副作用，然后链式触发任务事件。

## A6. Talk 在原神架构中的定位

| 维度 | Quest 系统 | Talk 系统 |
|---|---|---|
| **谁管状态机** | 服务器 | 客户端 |
| **谁管渲染** | 客户端 | 客户端 |
| **谁管完成判定** | 服务器 | 客户端 → 上报 → 服务器 |
| **数据格式** | 严重混淆（per version key）| 半混淆（_type 字面 / 内容混淆）|
| **配表大小** | 8.3 MB QuestExcel | **38 MB TalkExcel + 168 MB BinOutput/Talk** |

→ Talk 数据量是 Quest 的 25 倍。**剧情体量 ≈ 文字 + 配音 + 演出，远大于结构性逻辑**。

---

# Part B · Lua 脚本桥

## B1. 整体架构

每个**场景** (Scene) 有一个 `SceneScriptManager`。Lua 脚本控制：
- **Groups**：场景里实体的分组（怪物组、机关组、区域组）
- **Suites**：Group 的具体配置（哪些活、哪些死）
- **Triggers**：事件回调（玩家进入区域、击杀怪物、机关触发）
- **Variables**：场景级变量

```java
// SceneScriptManager.java:63
public class SceneScriptManager {
    private final Scene scene;
    private final Map<String, Integer> variables;           // 场景变量
    private SceneMeta meta;
    private final Map<Integer, Set<SceneTrigger>> currentTriggers;
    private final Map<Integer, EntityRegion> regions;
    private final Map<Integer, SceneGroup> sceneGroups;
    private final Map<Integer, SceneGroupInstance> sceneGroupsInstances;
    ...
}
```

## B2. 双向桥 #1：Quest → Lua（服务器告诉脚本"任务发生了什么"）

通过 `QUEST_EXEC_NOTIFY_GROUP_LUA` 这个 exec 类型实现（corpus 里出现 1087 次）。

```java
// game/quest/exec/ExecNotifyGroupLua.java:18
@QuestValueExec(QuestExec.QUEST_EXEC_NOTIFY_GROUP_LUA)
public class ExecNotifyGroupLua extends QuestExecHandler {
    public boolean execute(GameQuest quest, QuestExecParam condition, String... paramStr) {
        val sceneId = Integer.parseInt(paramStr[0]);
        val groupId = Integer.parseInt(paramStr[1]);

        val scene = quest.getOwner().getScene();
        if (scene.getId() != sceneId) return false;

        scene.runWhenFinished(() -> {
            val questState = quest.getState();
            val args = switch (questState) {
                case QUEST_STATE_FINISHED, QUEST_STATE_FAILED ->
                    new ScriptArgs(groupId, EventType.EVENT_QUEST_FINISH, 
                                   quest.getSubQuestId(), 
                                   questState == QuestState.QUEST_STATE_FINISHED ? 1 : 0);
                default ->
                    new ScriptArgs(groupId, EventType.EVENT_QUEST_START, quest.getSubQuestId());
            };
            args.setEventSource(quest.getSubQuestId());

            scriptManager.callEvent(args);   // ← 把事件投给 Lua
        });
        return true;
    }
}
```

**配表里这样用**：

```jsonc
"finishExec": [
    {
        "type": "QUEST_EXEC_NOTIFY_GROUP_LUA",
        "param": ["3", "133003051"]   // sceneId=3, groupId=133003051
    }
]
```

→ SubQuest 完成时通知场景 3 的 group 133003051：「我（subQuestId）finished 了」。
Lua 脚本里有个 `EVENT_QUEST_FINISH` 监听器，会被触发。

### Lua 端的 Trigger 注册

每个 Group 在 Lua 里定义 triggers：

```lua
-- pseudo Lua, actual file is in game client
triggers = {
    { name = "Trigger_Q3022_OnFinish",
      event = EventType.EVENT_QUEST_FINISH,
      source = "302207",                  -- 关心 SubQuest 302207
      condition = "condition_when_finished",
      action = "action_spawn_dainsleif",
      trigger_count = 1 }
}
```

服务器端 `SceneScriptManager.callEvent` 流程：

```java
// SceneScriptManager.java:745
public void callEvent(@Nonnull ScriptArgs params) {
    eventExecutor.execute(() -> realCallEvent(params));   // 异步线程池
}

private void realCallEvent(ScriptArgs params) {
    Set<SceneTrigger> relevantTriggers = getTriggersByEvent(params.type).stream()
        .filter(t -> params.getGroupId() == 0 || t.getGroupId() == params.getGroupId())
        .filter(t -> t.getSource().isEmpty() || t.getSource().equals(params.getEventSource()))
        .collect(Collectors.toSet());

    for (SceneTrigger trigger : relevantTriggers) {
        // 1. 调 Lua condition 函数
        if (evaluateTriggerCondition(trigger, group, params)) {
            // 2. 调 Lua action 函数
            callTrigger(trigger, group, params);
        }
    }
}
```

triggers 走**反向倒排索引**——按 `(eventType, groupId, source)` 三元组找候选；condition 在 Lua 里二次校验；action 在 Lua 里跑实际脚本。

## B3. 双向桥 #2：Lua → Quest（脚本告诉服务器"我做了某事，请通知任务系统"）

入口：`scriptlib_handlers/player/QuestScriptHandler.java`：

```java
@Override
public int addQuestProgress(GroupEventLuaContext context, @NotNull String eventNotifyName) {
    for (var player : context.getSceneScriptManager().getScene().getPlayers()) {
        player.getQuestManager().queueEvent(QuestCond.QUEST_COND_LUA_NOTIFY, eventNotifyName);
        player.getQuestManager().queueEvent(QuestContent.QUEST_CONTENT_LUA_NOTIFY, eventNotifyName);
    }
    return 0;
}
```

Lua 脚本里这样调：

```lua
-- 玩家完成场景里的某个谜题，Lua 脚本里
ScriptLib.AddQuestProgress(context, "Q302207_PuzzleSolved")
```

服务器收到后：
1. 给场景里所有玩家的 questManager 投递 `QUEST_*_LUA_NOTIFY` 事件
2. 关心 `eventNotifyName="Q302207_PuzzleSolved"` 的 SubQuest 自动推进

**这是为什么 corpus 里 `QUEST_CONTENT_LUA_NOTIFY` 占第 2 位（3401 次）**——大量任务步骤通过 Lua 通知机制完成。

### SubQuest 怎么订阅 Lua 事件

```jsonc
"finishCond": [
    {
        "type": "QUEST_CONTENT_LUA_NOTIFY",
        "paramString": "Q302207_PuzzleSolved"
    }
]
```

`paramString` 字段（不在 param[] 里！）就是事件名。Handler `ContentLuaNotify.java`：

```java
public boolean isEvent(SubQuestData questData, QuestContentCondition condition, 
                      QuestContent type, String paramStr, int... params) {
    if (condition.getType() != type) return false;
    return condition.getParamString().equals(paramStr);   // 字符串等值比较
}
```

→ 字符串完全匹配作为路由——**松耦合**。任何 Lua 脚本可以喊任何名字，任何任务可以订阅任何名字。

## B4. 第三种桥：TriggerExcelConfigData（命名 trigger 的双向同步）

这是更精细的桥。`TriggerExcelConfigData.json` 里定义命名 trigger：

```jsonc
{
    "id": 5001,
    "triggerName": "Q3022_PuzzleTrigger",
    "groupId": 133003051,
    "sceneId": 3,
    ...
}
```

SubQuest 启动时，会**主动把 trigger 注入场景**（`GameQuest.start()` 看过）：

```java
val triggerCond = questData.getFinishCond().stream()
    .filter(p -> p.getType() == QUEST_CONTENT_TRIGGER_FIRE).toList();
for (val cond : triggerCond) {
    TriggerExcelConfigData newTrigger = GameData.getTriggerExcelConfigDataMap().get(cond.getParam()[0]);
    triggerData.put(newTrigger.getTriggerName(), newTrigger);
    // 通过 SceneScriptManager 注册到场景 Lua 引擎
}
```

**Lua trigger 触发后**（`SceneScriptManager.java:825`）：

```java
val triggerData = GameData.getQuestTriggerDataByName(params.getGroupId(), trigger.getName());
if (triggerData != null && triggerData.getGroupId() == params.getGroupId()) {
    getScene().getPlayers().forEach(p -> {
        p.getQuestManager().queueEvent(QuestContent.QUEST_CONTENT_TRIGGER_FIRE,
            triggerData.getId(), 0);
    });
}
```

→ Lua trigger 名字 → 反查 `TriggerExcelConfigData` → fire `QUEST_CONTENT_TRIGGER_FIRE` 事件。

`LUA_NOTIFY` vs `TRIGGER_FIRE` 的区别：
- `LUA_NOTIFY`：自由文本路由，Lua 主动喊名字
- `TRIGGER_FIRE`：基于 SceneTrigger 注册机制，事件类型 + 来源严格匹配

## B5. 关键概念：Group Suite 切换（剧情驱动场景变化的核心）

每个 SceneGroup 有多个 Suite（配置变体）。**任务可以切换当前 Suite**——这是怎么实现"任务推进 → 场景变了"的：

```jsonc
"finishExec": [
    {
        "type": "QUEST_EXEC_REFRESH_GROUP_SUITE",
        "param": ["133003051", "2"]   // groupId=133003051, switch to suite 2
    }
]
```

`REFRESH_GROUP_SUITE` 在 corpus 是出现 #1 (1553 次) 的 exec 类型。它做的事：

1. 卸载当前 Suite 里的实体（怪物、机关、NPC）
2. 加载目标 Suite 里的实体
3. 通知客户端实体变化

→ 配合 `REGISTER_DYNAMIC_GROUP` (99 次) 和 `UNREGISTER_DYNAMIC_GROUP` (1526 次)，整个**场景"按需加载"**机制就成立了。

### 实例理解

设想 Caribert 任务里的一段：
- Suite 1：村庄正常状态，村民闲逛
- Suite 2：村民失踪后，空旷村庄 + 怪物
- Suite 3：剧情解决后，村民回归 + 庆祝氛围

`REFRESH_GROUP_SUITE` 在不同 SubQuest 完成时切换——**整个游戏世界跟着任务推进而变化**。

## B6. 性能关键：场景脚本系统是异步的

```java
public static final ExecutorService eventExecutor = new ThreadPoolExecutor(4, 4, ...);

public void callEvent(@Nonnull ScriptArgs params) {
    eventExecutor.execute(() -> realCallEvent(params));   // 完全异步
}
```

跟 QuestManager 一样，**每个场景独立的 4 线程池**。Lua 调用不会阻塞主玩家循环。

注释里直接说明了为什么必须异步：

```
We use ThreadLocal to trans SceneScriptManager context to ScriptLib, to avoid eval script for every groups' trigger in every scene instances.
But when callEvent is called in a ScriptLib func, it may cause NPE because the inner call cleans the ThreadLocal so that outer call could not get it.
e.g. CallEvent -> set -> ScriptLib.xxx -> CallEvent -> set -> remove -> NPE -> (remove)
So we use thread pool to clean the stack to avoid this new issue.
```

→ ThreadLocal 在嵌套 callEvent 时会被错误清理，所以**强制异步隔离**。这是踩过坑后的设计。

---

# Part C · 三系统联动的端到端例子

设想一个场景："找到丢失的村民"。

```
[阶段 0：服务器/场景启动期]
   - SceneScriptManager 加载场景 Lua
   - 注册 trigger "Trigger_VillagerFound" (event=EVENT_GADGET_INTERACT, group=X)
   - QuestSystem 加载 MainQuest 1234，建立倒排索引

[阶段 1：玩家接 Quest]
   触发 QUEST_COND_NONE 事件 → SubQuest 123401 接取 (state=UNFINISHED)
   → beginExec[REFRESH_GROUP_SUITE 133003051 → suite_lookforVillager]
        Lua 加载怪物 + 村民躺尸

[阶段 2：玩家与 NPC 对话]
   玩家点 NPC → 客户端读 Talk[12340101] → 跑客户端 Lua → 显示对话
   玩家选了"我会去找的"
   → 客户端发 NpcTalkReq{12340101}
   → 服务器：fire QUEST_CONTENT_COMPLETE_TALK[12340101]
   → SubQuest 123401 finishCond 命中 → finish()
   → finishExec: SET_QUEST_VAR[0, 1]   叙事状态 +1
   → triggerStateEvents → 引出 SubQuest 123402

[阶段 3：玩家进入村庄]
   玩家走到坐标 (X, Y)
   → 客户端发 EnterRegionReq
   → 服务器实体系统检测进入 region 1075
   → SceneScriptManager.callEvent(EVENT_ENTER_REGION, region=1075)
   → Lua trigger "Trigger_OnEnterVillage" 命中
   → Lua action: ScriptLib.SpawnMonsters(...)
       同时: ScriptLib.AddQuestProgress(ctx, "Q123402_RegionEntered")
   → QuestScriptHandler.addQuestProgress 把事件投给 questManager
   → fire QUEST_CONTENT_LUA_NOTIFY["Q123402_RegionEntered"]
   → SubQuest 123402 finishCond 命中 → finish()
   
[阶段 4：玩家击杀怪物]
   击杀实体
   → 服务器实体系统：fire QUEST_CONTENT_KILL_MONSTER[monsterId]
   → 同时给 Lua callEvent(EVENT_ANY_MONSTER_DIE)
   → Lua trigger "Trigger_AllMonstersDead" 检测剩余怪 == 0
       → action: ScriptLib.AddQuestProgress(ctx, "Q123403_AllKilled")
   → SubQuest 123403 完成

[阶段 5：剧情完成]
   最后一个 SubQuest finishParent=true
   → MainQuest finish
   → finishExec[REFRESH_GROUP_SUITE 133003051 → suite_villageRecovered]
       Lua 移除怪物，村民正常活动
   → 玩家发奖励、解锁地区
```

这一路走完，**任务系统、Talk 系统、Lua 脚本系统协同**：
- Talk 把"剧情决策"变成 questVar 改变
- questVar / state 变化通知 Lua（NOTIFY_GROUP_LUA）
- Lua 监听场景事件并回调（addQuestProgress / TRIGGER_FIRE）
- 任务系统协调三者，但不关心具体内容

---

# 关键洞察总结

## 1. Talk 是"客户端权威 + 服务器公证"模型
- 90% 的对话逻辑在客户端
- 服务器只接收"talk X 完成"通知 + 保留对应 questVar 修改
- **数据规模上 Talk 是 Quest 的 25 倍**——剧情就是文字 + 配音

## 2. Lua 桥是"双向松耦合"设计
- Quest → Lua：通过 EventType（EVENT_QUEST_START/FINISH 等）通知场景脚本
- Lua → Quest：通过字符串路由（LUA_NOTIFY + 事件名）唤起任务进度
- 没有任何一方知道对方的内部结构——**只通过事件总线和命名字符串通信**

## 3. Group Suite 是"场景动态变形"的关键
- 1553 次 `REFRESH_GROUP_SUITE` 出现率，证明每个剧情节点都在切换场景配置
- Group + Suite 的二维度让"同一场景多个状态"变得可表达

## 4. 三系统都是"事件 + 异步线程池 + 倒排索引"的复刻
| 系统 | 事件总线 | 调度策略 |
|---|---|---|
| Quest | queueEvent(QuestCond/Content) | 4 线程池 + beginCondQuestMap 倒排 |
| Lua/Scene | callEvent(EventType) | 4 线程池 + getTriggersByEvent 反查 |
| Talk | 客户端处理，完成时 NpcTalkReq | 服务器只是 talk-id → questEvent 映射 |

**同一种架构在三个不同抽象层重复**——当一个模式能扩展到这种程度，证明它确实是好的设计。

## 5. 这套设计可复用到任何大型 RPG
- "对话渲染客户端化 + 完成事实通过窄通道上报"
- "场景脚本通过命名事件总线和任务系统通信"
- "GroupSuite 抽象解耦场景表现和叙事进度"

每一条都是付出过血泪代价才打磨出的工程经验。

---

## 参考代码位

| 概念 | 文件 |
|---|---|
| Talk 服务端处理 | `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerNpcTalkReq.java` |
| Quest → Lua 桥 | `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/exec/ExecNotifyGroupLua.java` |
| Lua → Quest 桥 | `Grasscutter-Quests/src/main/java/emu/grasscutter/scripts/scriptlib_handlers/player/QuestScriptHandler.java` |
| 场景脚本主循环 | `Grasscutter-Quests/src/main/java/emu/grasscutter/scripts/SceneScriptManager.java:745` (callEvent) |
| Trigger 注册 | `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/GameQuest.java:83-99` |
| LUA_NOTIFY 处理 | `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/content/ContentLuaNotify.java` |
| TRIGGER_FIRE 桥 | `Grasscutter-Quests/src/main/java/emu/grasscutter/scripts/SceneScriptManager.java:825` |
