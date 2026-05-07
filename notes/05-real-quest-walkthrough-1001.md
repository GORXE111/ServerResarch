# 05 · 真实任务拆解：MainQuest 1001

把前四篇的概念在一个真实任务上贯穿一遍。

数据来源：[Sycamore0/GenshinData/BinOutput/Quest/1001.json](https://github.com/Sycamore0/GenshinData/blob/master/BinOutput/Quest/1001.json)（章节 1101 / 蒙德序章相关，早期/教学性质）

---

## 完整配表

```jsonc
{
    "id": 1001,
    "ICLLDPJFIMA": 1004,                      // 字段名未还原
    "series": 1101,
    "titleTextMapHash": 2046717777,
    "descTextMapHash": 2302617031,
    "luaPath": "Actor/Quest/MQ1001",          // Lua 脚本钩子
    "showType": "QUEST_HIDDEN",
    "chapterId": 1101,
    "subQuests": [
        {  // ① 起手步
            "subId": 100101, "mainId": 1001, "order": 1,
            "acceptCond": [
                { "type": "QUEST_COND_STATE_EQUAL", "param": [100006, 3, 0, 0, 0] }
            ],
            "finishCondComb": "LOGIC_OR",
            "finishCond": [
                { "type": "QUEST_CONTENT_COMPLETE_TALK", "param": [100101, 0] },
                { "type": "QUEST_CONTENT_COMPLETE_TALK", "param": [100102, 0] }
            ],
            "failCond": [
                { "type": "QUEST_CONTENT_ADD_QUEST_PROGRESS", "param": [100101, 0], "count": 1 }
            ],
            "isRewind": true
        },
        {  // ② "正常"分支
            "subId": 100102, "mainId": 1001, "order": 2,
            "acceptCond": [
                { "type": "QUEST_COND_STATE_EQUAL", "param": [100101, 3, 0, 0, 0] }   // 上一步 FINISHED
            ],
            "finishCond": [
                { "type": "QUEST_CONTENT_FINISH_PLOT", "param": [100102, 0] }
            ],
            "finishExec": [
                { "type": "QUEST_EXEC_ROLLBACK_QUEST", "param": ["100101"] }            // 把 ① 回滚到 UNSTARTED
            ],
            "isRewind": true
        },
        {  // ③ "失败"分支
            "subId": 100103, "mainId": 1001, "order": 3,
            "acceptCond": [
                { "type": "QUEST_COND_STATE_EQUAL", "param": [100101, 4, 0, 0, 0] }   // 上一步 FAILED
            ],
            "finishCond": [
                { "type": "QUEST_CONTENT_FINISH_PLOT", "param": [100103, 0] }
            ],
            "finishParent": true,                                                       // 完成此步 → 整个 MainQuest 完成
            "isRewind": true
        }
    ]
}
```

**结构概要**：① 是入口，根据玩家走向分到 ② 或 ③，最终由 ③ 关闭整个 MainQuest。

---

## QuestState 枚举值（解读 `[questId, state]` 必备）

```
0 = NONE           3 = FINISHED
1 = UNSTARTED      4 = FAILED
2 = UNFINISHED     5 = CANCELED   6 = REWARDED
```

---

## 阶段 0：服务器启动时——加载 + 建索引

```
ResourceLoader.loadQuests()
  → 读取 1001.json
  → JsonUtils.loadToClass(path, MainQuestData.class)
  → GameData.mainQuestDataMap.put(1001, mainQuest)
  → 对每个 SubQuest 调用 addToCache()
       ├─ GameData.questDataMap.put(100101, subQuest)
       ├─ GameData.questDataMap.put(100102, subQuest)
       └─ GameData.questDataMap.put(100103, subQuest)

构建倒排索引 beginCondQuestMap：
  key="QUEST_COND_STATE_EQUAL100006"  → [100101]   (① 订阅外部 100006 的状态)
  key="QUEST_COND_STATE_EQUAL100101"  → [100102, 100103]  (②③ 都订阅 ① 的状态)
```

**注意倒排索引的 key**：是 `type + param[0]`。所以 100102（要 state==3）和 100103（要 state==4）会**共享同一个 key** —— 第二个参数（具体状态值）由 handler 自己验证。

---

## 阶段 1：玩家完成外部 quest 100006 触发本任务接取

```
[某个外部业务] 完成 quest 100006
   ↓
GameQuest.finish() (100006)               ← GameQuest.java:188
  this.state = QUEST_STATE_FINISHED (=3)
  triggerStateEvents()                    ← GameQuest.java:248
   ↓
queueEvent(QUEST_COND_STATE_EQUAL, questId=100006, state=3)
   ↓ (异步 eventExecutor)
QuestManager.triggerEvent(QuestCond, ...)
   ↓
GameData.getQuestDataByConditions(QUEST_COND_STATE_EQUAL, 100006, "")
  → 查 beginCondQuestMap.get("QUEST_COND_STATE_EQUAL100006")
  → 返回 [SubQuest 100101]
   ↓
对每个候选 SubQuest 验证 acceptCond：
  questSystem.triggerCondition(...)
   → ConditionStateEqual.execute(...)     ← ConditionStateEqual.java:18
       int questId = condition.getParam()[0]    // 100006
       int wantedState = condition.getParam()[1] // 3
       int curState = ... 查这个 quest 的当前状态
       return curState == wantedState     // ✅ 通过
   ↓
LogicType.calculate(LOGIC_AND, [1])  → true  (只有一个条件)
   ↓
addQuest(100101)
   ↓
new GameQuest(mainQuest, subQuestData)
this.state = QUEST_STATE_UNSTARTED (=1)
```

**关键洞察**：100101 是**被动接取**，不是玩家点击"接取按钮"。

---

## 阶段 2：SubQuest 启动 + 玩家做事

```
GameQuest.start() (100101)                ← GameQuest.java:79
  this.state = QUEST_STATE_UNFINISHED (=2)
  ↓
扫描 finishCond 中的 QUEST_CONTENT_TRIGGER_FIRE：
  本 quest 没有 → 跳过 trigger 注册
  ↓
sendPacket(PacketQuestListUpdateNotify)   ← 客户端任务列表加上这条
  ↓
执行 beginExec[]：
  本 quest 没有 beginExec → 跳过
  ↓
checkQuestAlreadyFulfilled(...)
  延迟 1 tick，扫一遍 finishCond 看是否已经满足（通常不满足）
```

**玩家此时看到任务上线，去找 NPC 对话。**

```
玩家点 NPC，发 NpcTalkReq{ talkId=100101 }
   ↓
HandlerNpcTalkReq.handle()
  fire 三个事件：
    QUEST_CONTENT_COMPLETE_ANY_TALK [100101]
    QUEST_CONTENT_COMPLETE_TALK     [100101]   ← 本步关心这个
    QUEST_COND_COMPLETE_TALK        [100101]
   ↓
QuestManager.triggerEvent(QuestContent.QUEST_CONTENT_COMPLETE_TALK, ...)
   ↓
遍历活跃 MainQuest（这里就是 1001）
   mainQuest.tryFinishSubQuests(QUEST_CONTENT_COMPLETE_TALK, "", 100101)
   ↓
对每个 UNFINISHED SubQuest（这里 100101）调用：
   QuestSystem.checkAndUpdateContent(...)
   ↓
遍历 finishCond[]：
  [0] QUEST_CONTENT_COMPLETE_TALK [100101]
       isEvent? type 匹配 ✓ && param[0]==100101 ✓  → 命中
       updateProgress(curr=0) → 1
       checkProgress: 1 >= count(默认1) → ✅ 完成
  [1] QUEST_CONTENT_COMPLETE_TALK [100102]
       isEvent? type 匹配 ✓ && param[0]==100102 ✗ → 不命中
       progress 不变 = 0
       checkProgress: 0 < 1 → ❌
   ↓
LogicType.calculate(LOGIC_OR, [1, 0]) → true   (有一个就够了)
   ↓
GameQuest.finish() (100101)
```

---

## 阶段 3：完成 ① 触发链式接取 ②

```
GameQuest.finish() (100101)
  state = QUEST_STATE_FINISHED (=3)
  sendPacket(PacketQuestListUpdateNotify)
  ↓
finishParent? false → 不关闭 MainQuest
  ↓
执行 finishExec[] —— 100101 没有 → 跳过
  ↓
triggerStateEvents()
  queueEvent(QUEST_COND_STATE_EQUAL, 100101, 3)
  queueEvent(QUEST_CONTENT_QUEST_STATE_EQUAL, 100101, 3)
  ...
   ↓
QuestManager.triggerEvent(QuestCond.QUEST_COND_STATE_EQUAL, 100101)
   ↓
beginCondQuestMap.get("QUEST_COND_STATE_EQUAL100101")
   → 返回 [100102, 100103]
   ↓
对 100102 验证 acceptCond:
  ConditionStateEqual: curState(100101) == 3 ✓  → 通过
   → addQuest(100102) ✅
对 100103 验证 acceptCond:
  ConditionStateEqual: curState(100101) == 3, wanted = 4 ✗
   → 不接取
```

**关键洞察**：倒排索引 key 只用 `type + 首参数`，导致 100102 和 100103 都被取出来作为候选；**精确状态值靠 handler 内验证再筛**。这是性能/精度的折中。

---

## 阶段 4：执行 ② 完成剧情 + 回滚 ①

```
GameQuest.start() (100102)
  state = UNFINISHED
   ↓ (玩家进入剧情对话/CG)

[剧情系统] 完成 plot 100102
  fire QUEST_CONTENT_FINISH_PLOT [100102]
   ↓
QuestSystem.checkAndUpdateContent (100102)
  finishCond[0] QUEST_CONTENT_FINISH_PLOT[100102] → 命中 → 完成
   ↓
GameQuest.finish() (100102)
  state = FINISHED
   ↓
执行 finishExec[]:                        ← GameQuest.java:197
  QUEST_EXEC_ROLLBACK_QUEST ["100101"]
  ↓
QuestSystem.triggerExec()
  ↓ 异步线程池
ExecRollbackQuest.execute()
  ↓
GameQuest.rewind()  on 100101            ← GameQuest.java:261
  把 100101 之后所有 SubQuest 清空进度（但 100102 已 FINISHED 不会被清）
  100101 自己 clearProgress + start
  → 100101 state 又变回 UNFINISHED！
```

**这里有个微妙现象**：rollback 不是设置成 FAILED，而是 UNSTARTED → 重新 start → UNFINISHED。所以**这个真实任务里 100103 可能永远不会通过常规路径接取**——除非业务层另外触发了 100101 的 fail。

这印证了 1001 是个早期/测试性质的任务，但**机制本身展示完整**：
- "正常"分支用状态比对
- "失败"分支用状态比对
- finishExec 可以**反向操作其他 SubQuest**（rollback）

---

## 阶段 5：假设 ① 真的失败了——③ 接取 + 关闭 MainQuest

```
[业务层调用] addQuestProgress(100101, 0)
  fire QUEST_CONTENT_ADD_QUEST_PROGRESS [100101]
   ↓
QuestSystem.checkAndUpdateContent (100101)
  failCond[0]: type 匹配 + param[0]=100101 ✓ → 命中
  count=1, progress=1 ≥ 1 → ✅ 失败
   ↓
GameQuest.fail() (100101)
  state = QUEST_STATE_FAILED (=4)
  triggerStateEvents()
   ↓ 又走一遍倒排查询
queueEvent(QUEST_COND_STATE_EQUAL, 100101, 4)
   ↓
beginCondQuestMap.get("QUEST_COND_STATE_EQUAL100101")
  → [100102, 100103]
   ↓
对 100102: state==3 wanted, current==4 → ✗
对 100103: state==4 wanted, current==4 → ✓ → addQuest(100103)
   ↓
GameQuest.start() (100103)
[玩家做剧情]
   ↓
GameQuest.finish() (100103)
   ↓
finishParent == true ✓
   ↓
GameMainQuest.finish(1001)
  state = PARENT_QUEST_STATE_FINISHED
  发奖（rewardIdList，本任务为空）
  sendPacket(PacketFinishedParentQuestNotify)
```

---

## 这套设计的几个值得偷师的细节

### 1. 倒排索引的"宽匹配 + 二次验证"

```
key 只用 type + param[0]    →  缩小候选到 O(1)
handler 用完整 param 验证   →  保证精度
```

**好处**：索引体积小（只按首参数分桶），但表达力不丢失。

### 2. 状态作为"事件"而非"轮询"

每次 `state = X` 都 fire 一次 `QUEST_COND_STATE_EQUAL` 事件——任何订阅这个状态的 SubQuest 立刻被检查。**没有轮询**。

### 3. finishExec 反向操作其他任务

任务系统是整个游戏 progression 的中央总线：

| Exec 类型 | 效果 |
|---|---|
| `QUEST_EXEC_ROLLBACK_QUEST` | 重置某 SubQuest |
| `QUEST_EXEC_SET_QUEST_VAR` | 改任务变量 → 触发更多事件 |
| `QUEST_EXEC_SET_OPEN_STATE` | 解锁系统功能 |
| `QUEST_EXEC_UNLOCK_AREA` | 解锁地图区域 |
| `QUEST_EXEC_REFRESH_GROUP_SUITE` | 切换场景配置（NPC 出现/消失） |
| `QUEST_EXEC_GRANT_TRIAL_AVATAR` | 发试用角色 |

地图、NPC、商店、剧情、技能解锁，全部由 finishExec 驱动。

### 4. `isRewind: true` 的语义

每个 SubQuest 都标了 `isRewind: true`——意思是**玩家死亡/掉线时可以从这一步重做**。配合 rewind 机制，玩家不会因为掉线丢失任务进度。

### 5. LogicType 用枚举而非布尔表达式树

`finishCondComb: "LOGIC_OR"` 一个枚举搞定。**不需要解析表达式**，配表更简单，引擎更快。

代价：复杂逻辑要拆成多个 SubQuest 串联，不能在单个 SubQuest 里写 `(A AND B) OR (C AND D)`。但这反而**强迫策划把复杂度拆成线性步骤**——可读性提升。

---

## 后续可挖的方向

- 找一个有 **`beginExec` 投放 NPC**、**`finishExec` 解锁地图**的真实任务（更典型的剧情任务）
- 看 **`scripts/SceneScriptManager.java`** 是怎么把 Lua 触发桥接回任务系统的
- 拆 **协议层**（KCP + Protobuf）——任务消息怎么序列化下发
- 分析 **Talk 系统** —— `TalkExcelConfigData.json` 38 MB，是对话树结构
