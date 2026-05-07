# 09 · Talk 选项分支实例：MainQuest 11019（夜兰 LQ）

实测数据：找到一个**完美的"分支选项 + 汇合"对话结构**——使用 `TALK_EXEC_SET_QUEST_VAR` 把玩家选择持久化到任务变量。

> 数据源：`GenshinData/BinOutput/Quest/11019.json`  
> 任务标识：`QuestDialogue/LQ/Yelan1_11019/Q1101952..Q1101955` (LQ = Legendary Quest，夜兰传说任务"知人知面")  
> 触发 NPC：`12403 = 知易的规划书`（笔记本类道具）；夜兰本人 NPC id 是 1048
>
> **🔧 修正（基于 notes/12 NPC 翻译）**：本文最初称"NPC 12403 = 夜兰"是错误的。12403 实际是"知易的规划书"（一本笔记），是触发 Talk 的可交互对象。3 个分支选项实际是**翻阅笔记的"海/岩/路"三个章节**，"汇合 talk" 是「不看了」收尾。这不影响下文对位向量分支机制的分析（机制本身是通用的），但具体场景描述应理解为"读笔记"而非"对话夜兰"。

---

## 1. 分支结构总览

在 SubQuest 1101911 推进过程中（状态 `UNFINISHED`），玩家可以与 NPC 12403 对话，看到 **3 个备选问题**。每个选项后会"消失"（被选过），可以反复回来挑剩下的选项。**3 个全选完后，汇合 talk 才出现**，结束这一段对话。

```
                    [SubQuest 1101911 = UNFINISHED]
                              │
                              ↓
              玩家点 NPC 12403，可见 3 个选项之一：
                              │
        ┌─────────────────────┼─────────────────────┐
        ↓                     ↓                     ↓
  Talk 1101952           Talk 1101953           Talk 1101954
  问题选项 A            问题选项 B            问题选项 C
  precond:              precond:              precond:
    var[3]==0             var[0]==0             var[4]==0
                              
  完成后 SET var[3]=1   完成后 SET var[0]=1   完成后 SET var[4]=1
  → 此选项隐藏         → 此选项隐藏         → 此选项隐藏
                              │
                              ↓ (玩家可再次回来挑剩下的)
                              ↓ (3 个全选完后)
                              ↓
              ┌──────────────────────────┐
              │ Talk 1101955 (汇合 talk) │
              │ precond: AND             │
              │   var[0]==1              │
              │   var[3]==1              │
              │   var[4]==1              │
              └──────────────────────────┘
                              │
                              ↓
                  完成 1101955 → 推进 SubQuest 1101911
```

---

## 2. 三个选项的真实数据（已反混淆）

### 选项 A：Talk 1101952

```jsonc
{
    "talkId": 1101952,
    "logicComb": "LOGIC_AND",
    "beginCond": [
        { "_type": "QUEST_COND_STATE_EQUAL",     "_param": ["1101911", "2"] },   // SubQuest 1101911 进行中
        { "_type": "QUEST_COND_QUEST_VAR_EQUAL", "_param": ["3", "0", "11019"] } // var[3] == 0 (尚未选过本选项)
    ],
    "priority": 3,
    "siblingTalks": [1101952, 1101953, 1101954, 1101955],   // 同组对话
    "perfId": 110191103,
    "npcId": [12403],                                        // 夜兰
    "performCfg": "QuestDialogue/LQ/Yelan1_11019/Q1101952",  // 客户端 Lua
    "talkRole": "TALK_HERO_MAIN",
    "mainQuestId": 11019,
    "textHash": 12266219804591188210,

    "finishExec": [                                          // ← 选这个选项后的副作用
        {
            "type": "TALK_EXEC_SET_QUEST_VAR",
            "param": ["3", "1", "11019"]                     // 设 questVar[3] = 1 (本任务)
        }
    ]
}
```

### 选项 B：Talk 1101953

```jsonc
{
    "talkId": 1101953,
    "beginCond": [
        STATE_EQUAL[1101911, 2],
        QUEST_VAR_EQUAL[0, 0, 11019]                    // var[0] == 0
    ],
    "siblingTalks": [1101952, 1101953, 1101954, 1101955],
    "performCfg": "QuestDialogue/LQ/Yelan1_11019/Q1101953",
    "finishExec": [
        TALK_EXEC_SET_QUEST_VAR[0, 1, 11019]            // 设 var[0] = 1
    ]
}
```

### 选项 C：Talk 1101954

```jsonc
{
    "talkId": 1101954,
    "beginCond": [
        STATE_EQUAL[1101911, 2],
        QUEST_VAR_EQUAL[4, 0, 11019]                    // var[4] == 0
    ],
    "siblingTalks": [1101952, 1101953, 1101954, 1101955],
    "performCfg": "QuestDialogue/LQ/Yelan1_11019/Q1101954",
    "finishExec": [
        TALK_EXEC_SET_QUEST_VAR[4, 1, 11019]            // 设 var[4] = 1
    ]
}
```

### 汇合 Talk：1101955

```jsonc
{
    "talkId": 1101955,
    "logicComb": "LOGIC_AND",
    "beginCond": [
        STATE_EQUAL[1101911, 2],
        QUEST_VAR_EQUAL[0, 1, 11019],                   // var[0] == 1
        QUEST_VAR_EQUAL[3, 1, 11019],                   // var[3] == 1
        QUEST_VAR_EQUAL[4, 1, 11019]                    // var[4] == 1
    ],
    "performCfg": "QuestDialogue/LQ/Yelan1_11019/Q1101955",
    // 没有 TALK_EXEC —— 只是普通完成，让 NpcTalkReq 推进 SubQuest 1101911
}
```

---

## 3. 完整端到端协议流程

### 阶段 A：客户端渲染对话

玩家走近 NPC 12403：

```
[客户端]
  ① 读取 TalkExcelConfigData 找出 NPC 12403 关联的所有 Talk
  ② 检查每个 Talk 的 beginCond:
     - Talk 1101952: STATE[1101911]==2 ✓ AND var[3]==0 ✓  →  可见
     - Talk 1101953: STATE[1101911]==2 ✓ AND var[0]==0 ✓  →  可见
     - Talk 1101954: STATE[1101911]==2 ✓ AND var[4]==0 ✓  →  可见
     - Talk 1101955: 缺 var[0]==1                          →  隐藏
  ③ 把 3 个选项渲染成对话选项列表
  ④ 等待玩家点击
```

⚠️ **重要**：客户端**自己读取 questVar 状态**（服务器先用 `PacketQuestUpdateQuestVarNotify` 同步过来），然后**自己判断哪些选项可见**。服务器不参与 visibility 决策。

### 阶段 B：玩家选择"选项 A"（Talk 1101952）

```
[客户端]
  ① 跑 Q1101952 的 Lua 表演脚本（角色对话、镜头、特效）
  ② 玩家阅读完毕，对话结束
  ③ 准备发两个包：
     a) NpcTalkReq{ talkId=1101952 }       // 通用"talk 完成"通知
     b) QuestUpdateQuestVarReq{
            questId=1101911,                  // SubQuest id
            parentQuestId=11019,              // MainQuest id
            questVarOpList=[
                { isAdd=false, index=3, value=1 }  // SET var[3] = 1
            ]
        }
```

**观察**：选项触发的 `TALK_EXEC_SET_QUEST_VAR` 在客户端被翻译成 `QuestUpdateQuestVarReq` 包。**服务器从未读取 TALK_EXEC_* 字段本身**——它只接收"客户端要求改 var"的请求。

### 阶段 C：服务器处理两个包

**包 1：`NpcTalkReq{1101952}`**（`HandlerNpcTalkReq.java:13`）

```java
// 上 notes/08 看过
questManager.queueEvent(QuestContent.QUEST_CONTENT_COMPLETE_TALK, 1101952, 0);
questManager.queueEvent(QuestCond.QUEST_COND_COMPLETE_TALK, 1101952, 0);
sendPacket(new PacketNpcTalkRsp(...));
```

事件投递后，无 SubQuest 在 finishCond 订阅 talk 1101952 → 没有任务推进。**这个 talk 仅用于触发 var 改动，不是完成条件**。

**包 2：`QuestUpdateQuestVarReq`**（`HandlerQuestUpdateQuestVarReq.java:14`）

```java
public void handle(GameSession session, byte[] header, QuestUpdateQuestVarReq req) {
    val questManager = session.getPlayer().getQuestManager();
    val subQuest = questManager.getQuestById(req.getQuestId());      // 1101911
    var mainQuest = questManager.getMainQuestById(req.getParentQuestId());  // 11019

    List<QuestVarOp> questVars = req.getQuestVarOpList();
    for (QuestVarOp op : questVars) {
        if (op.isAdd()) {
            mainQuest.incQuestVar(op.getIndex(), op.getValue());
        } else {
            mainQuest.setQuestVar(op.getIndex(), op.getValue());     // ← var[3] = 1
        }
    }
    session.send(new PacketQuestUpdateQuestVarRsp(req));
}
```

注意有个有趣的细节：客户端实际发**两个独立的 `QuestUpdateQuestVarReq` 包**（注释里写明了：「One with the value, and one with the index and the new value to set/inc/dec」）。这是为什么 Java 端有 `questVarsUpdate` 缓冲队列——第一个包进队列，第二个包配对处理。

### 阶段 D：服务器 setQuestVar 触发链式事件

`GameMainQuest.setQuestVar` (`GameMainQuest.java:109`)：

```java
public void setQuestVar(int i, int value) {
    int previousValue = this.questVars[i];
    this.questVars[i] = value;
    triggerQuestVarAction(i, this.questVars[i]);
}

private void triggerQuestVarAction(int index, int value) {
    questManager.queueEvent(QuestCond.QUEST_COND_QUEST_VAR_EQUAL, index, value);
    questManager.queueEvent(QuestCond.QUEST_COND_QUEST_VAR_GREATER, index, value);
    questManager.queueEvent(QuestCond.QUEST_COND_QUEST_VAR_LESS, index, value);
    questManager.queueEvent(QuestContent.QUEST_CONTENT_QUEST_VAR_EQUAL, index, value);
    questManager.queueEvent(QuestContent.QUEST_CONTENT_QUEST_VAR_GREATER, index, value);
    questManager.queueEvent(QuestContent.QUEST_CONTENT_QUEST_VAR_LESS, index, value);
    sendPacket(new PacketQuestUpdateQuestVarNotify(parentQuestId, questVars));  // 同步给客户端
}
```

→ **6 个事件同时投递**——任何 SubQuest / Talk 订阅 questVar[3]==1 的都会收到。

具体到我们的例子：
- `QUEST_COND_QUEST_VAR_EQUAL[3, 1, 11019]` 在 Talk 1101955 的 beginCond 里
- 但 1101955 还需要 var[0]==1 和 var[4]==1，目前只满足了 var[3]==1，所以**客户端下次开对话框时 1101955 仍不可见**

### 阶段 E：服务器 Notify 客户端同步 var 状态

```java
sendPacket(new PacketQuestUpdateQuestVarNotify(parentQuestId, questVars));
```

→ 客户端更新本地状态 var[3]=1。**下次玩家开同一个对话框，Talk 1101952 的 beginCond `var[3]==0` 不再满足，所以选项 A 消失了**。玩家只能看到选项 B 和选项 C。

### 阶段 F：循环——直到所有选项都选完

玩家点 NPC 12403 → 看到选项 B 和 C
- 选 B：→ var[0]=1，下次只剩 C
- 选 C：→ var[4]=1，下次没有选项 ABC 了

此时 var[0]==1 AND var[3]==1 AND var[4]==1 全部满足 → **Talk 1101955 显示**。

玩家点 NPC：客户端只看到 Talk 1101955（汇合 talk）→ 自动开始 → 完成后发 `NpcTalkReq{1101955}`。

### 阶段 G：汇合 talk 完成 → 推进 SubQuest

服务器收到 `NpcTalkReq{1101955}` → fire `QUEST_CONTENT_COMPLETE_TALK[1101955]`。

SubQuest 1101911 的 finishCond 必然包含：

```jsonc
"finishCond": [
    { "type": "QUEST_CONTENT_COMPLETE_TALK", "param": [1101955, 0] }
]
```

→ SubQuest 1101911 完成 → triggerStateEvents → 后续 SubQuest 自动接取 → 任务继续。

---

## 4. 关键观察与设计精髓

### 4.1 "客户端权威"的具体体现

**服务器从未直接处理 TALK_EXEC**：
- TALK_EXEC_SET_QUEST_VAR 是**客户端配表里的指令**
- 客户端解析它 → **翻译成对应的 Req 包**（QuestUpdateQuestVarReq）
- 服务器只看到通用的"改 var"请求，不知道这是某个对话选项触发的

→ 这是混合权威架构的一致体现：**叙事/UI 层在客户端，状态/数据层在服务器**。中间用通用的 Req/Rsp 协议沟通。

### 4.2 用 questVar 的位向量表达"已选项"

把 5 个 `questVars[5]` 当成位向量：
- var[0] = 选过选项 B 吗？
- var[3] = 选过选项 A 吗？
- var[4] = 选过选项 C 吗？

每个选项的 beginCond 检查"自己的位是否还是 0"——还没选过就显示，选过就隐藏。**完全自然的"消除已选选项"逻辑，不需要任何特殊机制**。

### 4.3 "convergence talk" 是匹配 AND 全集的查询

汇合 talk 1101955 的 beginCond 用 `LOGIC_AND` 检查所有 var 都为 1。只要任何一个还是 0，它就不可见。

→ **客户端的 visibility 检查 = SQL WHERE clause**，配置表就是声明式查询。

### 4.4 选项必须互斥怎么做？

我们这个例子是"3 选都要选"。如果是"3 选只能选 1 个"呢？—— 改 beginCond 的逻辑：

```jsonc
// 选项 A：要求 var[0]==0 AND var[3]==0 AND var[4]==0 (没选过任何选项)
"beginCond": [
    QUEST_VAR_EQUAL[0, 0, 11019],
    QUEST_VAR_EQUAL[3, 0, 11019],
    QUEST_VAR_EQUAL[4, 0, 11019]
],
"finishExec": [
    TALK_EXEC_SET_QUEST_VAR[3, 1, 11019]  // 选过 A
]
```

选了 A 之后 var[3]=1，其他选项的 beginCond 不满足 → 都消失。

→ **同一种"位向量 + AND 检查"模式可以表达"全选 / 单选 / N 选 K"等任意组合**。

### 4.5 客户端发两个 Req 的"一致性"问题

客户端先发 `NpcTalkReq` 再发 `QuestUpdateQuestVarReq`。如果中间网络断了怎么办？
- talk 已记录完成
- var 没改

下次进对话：选项 A 还显示（var[3] 还是 0）但完成进度已记录。

**怎么处理？** 看似有 bug，实际上没问题——**TALK_EXEC_SET_QUEST_VAR 是幂等的**：再选一次只是 `var[3] = 1` 第二次，结果相同。客户端再次发 var 改动包补齐即可。

→ 用 `setQuestVar`（而非 `incQuestVar`）就是为了幂等性！这是**对网络丢包的天然鲁棒性**。

---

## 5. 一图总结

```
   客户端                                  服务器
   ────────                                ────────

   读 TalkExcelConfigData                  
   读 questVars (本地缓存)                  
   显示 Talk 1101952/53/54 三个选项         
                                           
   玩家选 1101952                          
   跑客户端 Lua (Q1101952.lua)             
                                           
   ┌──────────────────────┐                
   │ 1. NpcTalkReq{1101952}│ ─────────────→ HandlerNpcTalkReq
   │                       │                  fire QUEST_CONTENT_COMPLETE_TALK
   │                       │                  (无人订阅，无效果)
   │                       │ ←───────────── PacketNpcTalkRsp(空)
   │                       │                
   │ 2. QuestUpdateQuestVarReq│ ────────→ HandlerQuestUpdateQuestVarReq
   │      [3, 1]              │              GameMainQuest.setQuestVar(3, 1)
   │                          │                fire QUEST_COND_QUEST_VAR_EQUAL
   │                          │                fire QUEST_CONTENT_QUEST_VAR_EQUAL
   │                          │ ←──── PacketQuestUpdateQuestVarRsp
   │                          │ ←──── PacketQuestUpdateQuestVarNotify(同步 var 状态)
   └──────────────────────────┘
                                           
   重新计算 Talk 可见性                      
   Talk 1101952 不可见了                    
   选项 A 消失                               
                                           
   ... 玩家继续选 B 和 C ...                 
                                           
   全选完后:                                 
   Talk 1101955 (汇合) 可见                
   玩家继续 talk → NpcTalkReq{1101955}      → SubQuest 1101911 finishCond 命中
                                              SubQuest 完成，任务推进
```

---

## 6. 给做剧情系统的启示

1. **位向量 + AND/OR 的声明式可见性**：不要写 if/else 链，用 questVar 位 + Talk beginCond 的逻辑表达。可读、可修改、可扩展。

2. **幂等的 setVar（而非 incVar）**：用 set 而非 inc 让操作幂等，对网络抖动友好。

3. **TALK_EXEC 不是引擎指令，是客户端的"请求生成器"**：客户端读到 TALK_EXEC_SET_QUEST_VAR 后**自己生成 QuestUpdateQuestVarReq 包**，服务器只接收通用 Req。这层间接让协议保持简洁——少一种 Req 类型。

4. **用 sibling talks（GKCFOJDKOJG 数组）连接选项组**：同组选项互相知道彼此，方便客户端 UI 一次渲染所有选项。

5. **"convergence talk" 是模式而非引擎特性**：用 AND 检查多个 var 即可——**不需要"等待所有分支完成"这种特殊机制**，纯靠声明式条件就实现了。

---

## 参考代码位

| 概念 | 文件 |
|---|---|
| 客户端 → 服务器 var 改动 | `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerQuestUpdateQuestVarReq.java` |
| Talk 完成处理 | `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerNpcTalkReq.java` |
| setQuestVar 链式触发 | `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/GameMainQuest.java:109` |
| Notify 同步 var 给客户端 | `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/send/PacketQuestUpdateQuestVarNotify.java` |
| 真实数据 | `GenshinData/BinOutput/Quest/11019.json:1555-1774` |
