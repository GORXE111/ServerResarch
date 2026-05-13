# 事件类型统一总线深度剖析

> 第 41 篇：跨 40 篇笔记出现 30+ 次的 `triggerMission` / `queueEvent` / `callEvent` 终于汇总——4 套并行事件类型枚举，500+ 事件代码，是 grasscutter 跨系统协作的"神经网络"。

---

## 0. 为什么这一篇重要

前 40 篇笔记里，**事件类型**贯穿各处但从未系统化：
- notes/02 任务系统：QuestContent / QuestCond 是任务触发核心
- notes/14 SceneScript：Lua EventType 30+ 种
- notes/20 Activity 系统：WatcherTriggerType 150+ 类型
- notes/22 BattlePass：trigger Mission 走 WatcherTriggerType
- notes/32 怪物：onDeath 触发 7 件事中含 3 种事件类型
- notes/38 Inventory：addItem 触发 4 个钩子（2 个 QuestContent + 1 个 QuestCond + 1 个 WatcherTrigger）

但**"到底有多少种事件？谁触发？谁监听？它们之间什么关系？"**——这一篇统一回答。

---

## 1. 4 套并行的事件系统

Grasscutter **没有"一个统一事件总线"**——它有**4 套并行系统**，各自演化：

```
┌───────────────────────────────────────────────────────────────┐
│              事件类型 4 套并行                                  │
├───────────────────────────────────────────────────────────────┤
│                                                                 │
│  [1] WatcherTriggerType          ─── 299 类型 ─── BattlePass 用 │
│      Achievement / Mission                                      │
│                                                                 │
│  [2] QuestContent                ─── 80+ 类型 ─── Quest 进度    │
│      "完成 N 次 X" 的目标                                       │
│                                                                 │
│  [3] QuestCond                   ─── 80+ 类型 ─── Quest 条件    │
│      "如果 X 则解锁" 的条件                                     │
│                                                                 │
│  [4] Lua EventType               ─── 30+ 类型 ─── SceneScript   │
│      场景剧情/机关 的事件                                       │
│                                                                 │
└───────────────────────────────────────────────────────────────┘
```

**总计 500+ 事件类型** —— 是 grasscutter 跨系统协作的核心字典。

### 1.1 为什么 4 套而非 1 套

历史包袱：
- WatcherTriggerType 来自 mihoyo BattlePass 设计
- QuestContent/QuestCond 来自 Quest 系统独立演化
- Lua EventType 来自 SceneScript（Lua 引擎独立）
- 4 套**用途不同**，不能合并

每套都有自己的注册/触发机制 —— 但**很多事件其实重复**（"怪物死"在 4 套里都有！）

---

## 2. WatcherTriggerType：BattlePass / Achievement 的事件

`WatcherTriggerType.java` —— **299 个枚举值**，按 ID 段分类。

### 2.1 9 大 ID 段

```
段位 1   (1-99)     战斗/视野通用       8 个
段位 100 (101-130)  世界事件             24 个 (打开宝箱/解锁/升级等)
段位 200 (201-231)  升级培养             31 个 (角色/武器/天赋升级)
段位 300 (301-340)  日常副本             40 个 (副本/塔/委托)
段位 400 (401-440)  制作 / 联机         40 个
段位 500 (501-505)  登录                 5 个
段位 600 (601-660)  挑战 / 机制          60 个
段位 700 (700-701)  任务                 2 个
其他 (1000+)        旧版兼容             89 个
```

### 2.2 典型代表

```java
TRIGGER_NEW_MONSTER (6)             // 第一次看到某种怪
TRIGGER_NEW_AFFIX (8)               // 看到新词条
TRIGGER_ELEMENT_BALL (101)          // 拾取元素球
TRIGGER_MONSTER_DIE (109)            // ★ 杀怪
TRIGGER_OPEN_WORLD_CHEST (120)       // 开宝箱
TRIGGER_OBTAIN_AVATAR (201)          // 获得角色
TRIGGER_PLAYER_LEVEL (202)           // 玩家升级
TRIGGER_AVATAR_UPGRADE (203)         // 角色升级
TRIGGER_OBTAIN_MATERIAL_NUM (212)    // ★ 拿到材料
TRIGGER_GACHA_NUM (214)             // 抽卡次数
TRIGGER_DAILY_TASK (301)             // 每日委托
TRIGGER_FINISH_TOWER_LEVEL (304)     // 通关深境螺旋
TRIGGER_FINISH_DUNGEON (307)         // 通关副本
TRIGGER_WEEKLY_BOSS_KILL (329)       // 周本 boss
TRIGGER_DO_COOK (401)                // 烹饪
TRIGGER_BUY_SHOP_GOODS (405)         // 商店购买
TRIGGER_LOGIN (501)                  // ★ 登录
TRIGGER_COST_MATERIAL (502)          // ★ 消耗材料
TRIGGER_FINISH_CHALLENGE (601)       // 完成挑战
TRIGGER_FINISH_QUEST_AND (700)       // 完成多个任务
TRIGGER_FINISH_QUEST_OR (701)        // 完成任一任务
```

### 2.3 触发 API

```java
player.getBattlePassManager().triggerMission(
    WatcherTriggerType.TRIGGER_MONSTER_DIE, 
    monsterId,    // param 1
    1);           // param 2 (count)
```

→ 战令任务 / 成就 监听这些事件并按 `param1/param2` 累计。

### 2.4 命名规律

观察 299 个名字的规律：
- `TRIGGER_OBTAIN_*` —— 拾取/获得类（OBTAIN_AVATAR / OBTAIN_MATERIAL_NUM）
- `TRIGGER_FINISH_*` —— 完成类（FINISH_DUNGEON / FINISH_CHALLENGE）
- `TRIGGER_DO_*` —— 主动行为类（DO_COOK / DO_FORGE）
- `TRIGGER_KILL_*` —— 击杀类（KILL_MONSTER_IN_AREA / KILL_GROUP_MONSTER）
- `TRIGGER_REACH_*` —— 到达数值类（REACH_MP_PLAY_SCORE）
- `TRIGGER_UNLOCK_*` —— 解锁类（UNLOCK_AREA / UNLOCK_RECIPE）

→ 这是**典型的命令-查询分离**命名。

---

## 3. QuestContent：任务进度的目标

`QuestContent.java` —— **80+ 个枚举**，描述"任务的 ContentTrigger 目标"。

### 3.1 完整清单（部分）

```java
QUEST_CONTENT_KILL_MONSTER (1)
QUEST_CONTENT_COMPLETE_TALK (2)           // ★ 完成对话
QUEST_CONTENT_MONSTER_DIE (3)             // ★ 杀怪
QUEST_CONTENT_FINISH_PLOT (4)
QUEST_CONTENT_OBTAIN_ITEM (5)             // ★ 获得物品
QUEST_CONTENT_TRIGGER_FIRE (6)            // 触发火元素 (剧情用)
QUEST_CONTENT_CLEAR_GROUP_MONSTER (7)     // ★ 清空组怪
QUEST_CONTENT_ENTER_DUNGEON (9)
QUEST_CONTENT_ENTER_MY_WORLD (10)
QUEST_CONTENT_FINISH_DUNGEON (11)
QUEST_CONTENT_DESTROY_GADGET (12)
QUEST_CONTENT_ENTER_ROOM (17)
QUEST_CONTENT_GAME_TIME_TICK (18)         // 游戏时间 tick
QUEST_CONTENT_FAIL_DUNGEON (19)
QUEST_CONTENT_LUA_NOTIFY (20)             // ★ Lua 触发的任务事件
QUEST_CONTENT_TEAM_DEAD (21)
QUEST_CONTENT_COMPLETE_ANY_TALK (22)
QUEST_CONTENT_UNLOCK_TRANS_POINT (23)
QUEST_CONTENT_ADD_QUEST_PROGRESS (24)
QUEST_CONTENT_INTERACT_GADGET (25)
QUEST_CONTENT_FINISH_ITEM_GIVING (27)
QUEST_CONTENT_SKILL (107)
QUEST_CONTENT_CITY_LEVEL_UP (109)
QUEST_CONTENT_ITEM_LESS_THAN (111)        // ★ 物品少于 N
QUEST_CONTENT_PLAYER_LEVEL_UP (112)
QUEST_CONTENT_QUEST_VAR_EQUAL (119)       // 任务变量等于
QUEST_CONTENT_QUEST_VAR_GREATER (120)
QUEST_CONTENT_QUEST_VAR_LESS (121)
QUEST_CONTENT_OBTAIN_VARIOUS_ITEM (122)   // ★ 获得多种物品
QUEST_CONTENT_BARGAIN_SUCC (124)          // 讨价还价成功
QUEST_CONTENT_MAIN_COOP_ENTER_SAVE_POINT (128)
QUEST_CONTENT_ANY_MANUAL_TRANSPORT (129)
QUEST_CONTENT_USE_ITEM (130)
QUEST_CONTENT_ENTER_VEHICLE (147)
QUEST_CONTENT_SCENE_LEVEL_TAG_EQ (148)
QUEST_CONTENT_LEAVE_SCENE (149)
QUEST_CONTENT_GADGET_STATE_CHANGE (155)
QUEST_CONTENT_UNKNOWN (9999)
```

### 3.2 触发 API

```java
player.getQuestManager().queueEvent(
    QuestContent.QUEST_CONTENT_MONSTER_DIE, 
    monsterId,    // param 1
    count);       // param 2
```

### 3.3 与 WatcherTriggerType 的重叠

| 事件 | WatcherTriggerType | QuestContent |
|---|---|---|
| 杀怪 | TRIGGER_MONSTER_DIE (109) | QUEST_CONTENT_MONSTER_DIE (3) |
| 获得物品 | TRIGGER_OBTAIN_MATERIAL_NUM (212) | QUEST_CONTENT_OBTAIN_ITEM (5) |
| 通关副本 | TRIGGER_FINISH_DUNGEON (307) | QUEST_CONTENT_FINISH_DUNGEON (11) |
| 完成对话 | (无) | QUEST_CONTENT_COMPLETE_TALK (2) |
| 玩家升级 | TRIGGER_PLAYER_LEVEL (202) | QUEST_CONTENT_PLAYER_LEVEL_UP (112) |
| 联机进入 | (无) | QUEST_CONTENT_ENTER_MY_WORLD (10) |
| Lua 通知 | (无) | QUEST_CONTENT_LUA_NOTIFY (20) |

→ **核心事件两套都有**——但 ID 不同！代码必须**同时触发两套**。

### 3.4 同时触发的代码体现

`Inventory.triggerAddItemEvents()`：
```java
private void triggerAddItemEvents(GameItem result) {
    // BattlePass 走 WatcherTriggerType
    getPlayer().getBattlePassManager().triggerMission(
        WatcherTriggerType.TRIGGER_OBTAIN_MATERIAL_NUM, result.getItemId(), result.getCount());
    
    // Quest 走 QuestContent (2 个变体)
    getPlayer().getQuestManager().queueEvent(
        QuestContent.QUEST_CONTENT_OBTAIN_ITEM, result.getItemId(), result.getCount());
    getPlayer().getQuestManager().queueEvent(
        QuestContent.QUEST_CONTENT_OBTAIN_VARIOUS_ITEM, result.getItemId(), result.getCount());
    
    // Quest Cond
    getPlayer().getQuestManager().queueEvent(
        QuestCond.QUEST_COND_PACK_HAVE_ITEM, result.getItemId(), result.getCount());
}
```

→ **一次 addItem 同时触发 4 个事件** —— 因为 4 套系统并行不通信，必须各自通知。

---

## 4. QuestCond：任务激活的条件

`QuestCond.java` —— **80+ 个枚举**，描述"任务需要满足什么条件才能 accept/finish"。

### 4.1 完整清单（部分）

```java
QUEST_COND_STATE_EQUAL (1)             // ★ 任务状态等于
QUEST_COND_PACK_HAVE_ITEM (3)          // ★ 背包有物品
QUEST_COND_ITEM_NUM_LESS_THAN (8)      // 物品少于
QUEST_COND_DAILY_TASK_START (9)
QUEST_COND_OPEN_STATE_EQUAL (10)
QUEST_COND_PLAYER_LEVEL_EQUAL_GREATER (17)   // ★ 玩家等级 >=
QUEST_COND_ITEM_GIVING_FINISHED (20)
QUEST_COND_IS_DAYTIME (21)             // ★ 是白天
QUEST_COND_QUEST_VAR_EQUAL (24)
QUEST_COND_FORGE_HAVE_FINISH (27)
QUEST_COND_ACTIVITY_COND (30)
QUEST_COND_COMPLETE_TALK (38)
QUEST_COND_QUEST_GLOBAL_VAR_EQUAL (44)
QUEST_COND_QUEST_GLOBAL_VAR_GREATER (45)
QUEST_COND_PERSONAL_LINE_UNLOCK (47)
QUEST_COND_MAIN_COOP_START (49)
QUEST_COND_LUA_NOTIFY (53)             // ★ Lua 触发的条件
QUEST_COND_CUR_CLIMATE (54)            // 当前气候
QUEST_COND_AVATAR_FETTER_GT (58)        // 角色好感度 >
QUEST_COND_HISTORY_GOT_ANY_ITEM (69)    // 历史拿过物品
QUEST_COND_TIME_VAR_GT_EQ (65)
QUEST_COND_TIME_VAR_PASS_DAY (66)
QUEST_COND_SCENE_POINT_UNLOCK (76)
QUEST_COND_SCENE_LEVEL_TAG_EQ (77)
QUEST_COND_PLAYER_ENTER_REGION (78)
QUEST_COND_UNKNOWN (9999)
```

### 4.2 Content vs Cond 的区别

```
QuestContent = "做什么"   (杀 5 个史莱姆 / 拿 10 个琉璃袋)
QuestCond    = "条件"    (玩家等级 >= 30 / 完成过某任务)
```

**Content** 是任务**进度**（不断累积）；**Cond** 是任务**前置**（决定是否能接）。

### 4.3 80+ Cond 的复杂度

注意 QuestCond.java 第 22-87 行很多带 `//#Missing` 注释：
```java
QUEST_COND_AVATAR_ELEMENT_EQUAL (4),  //#Missing #NpcGroup #TalkExcel
QUEST_COND_DAILY_TASK_OPEN (11),      //#Missing #NpcGroup #TalkExcel
QUEST_COND_AVATAR_CAN_CHANGE_ELEMENT (6),  //#Missing
```

→ **`#Missing` = grasscutter 还没实现这个条件检查** —— 但任务表里**已经引用**它了。
→ 这就是为什么"某些任务在 grasscutter 里没法 accept"——条件代码缺失。

---

## 5. Lua EventType：场景脚本事件

`EventType` 在 `gi_lua` 模块里，约 30+ 个。

### 5.1 监控到的事件（grep 出来）

```java
EVENT_GROUP_LOAD                  // ★ 组加载
EVENT_GROUP_REFRESH               // ★ 组刷新
EVENT_VARIABLE_CHANGE             // ★ 变量改变

EVENT_ANY_MONSTER_LIVE            // 任意怪刷出
EVENT_ANY_MONSTER_DIE             // ★ 任意怪死
EVENT_SPECIFIC_MONSTER_HP_CHANGE  // ★ 特定怪 HP 变化
EVENT_MONSTER_BATTLE              // ★ 怪进战
EVENT_MONSTER_TIDE_DIE            // 车轮战死亡进度

EVENT_GADGET_CREATE               // gadget 创建
EVENT_GADGET_STATE_CHANGE         // ★ gadget 状态改变
EVENT_ANY_GADGET_DIE              // gadget 死亡
EVENT_SPECIFIC_GADGET_HP_CHANGE   // 特定 gadget HP

EVENT_AVATAR_NEAR_PLATFORM        // 玩家靠近平台
EVENT_GATHER                      // ★ 采集
EVENT_SELECT_OPTION               // 选择 worktop 选项

EVENT_ENTER_REGION                // ★ 进入区域
EVENT_LEAVE_REGION                // ★ 离开区域
EVENT_UNLOCK_TRANS_POINT          // 解锁锚点

EVENT_QUEST_START                 // ★ 任务开始
EVENT_QUEST_FINISH                // ★ 任务完成
EVENT_LUA_NOTIFY                  // ★ Lua 主动通知 (跨边界)

EVENT_CHALLENGE_SUCCESS           // 挑战胜利
EVENT_CHALLENGE_FAIL              // 挑战失败
EVENT_DUNGEON_REWARD_GET          // 拿副本奖励
EVENT_DUNGEON_SETTLE              // 副本结算
EVENT_PLATFORM_REACH_POINT        // 平台到达点
EVENT_TIMER_EVENT                 // ★ 定时器
EVENT_BLOSSOM_CHEST_DIE
EVENT_BLOSSOM_PROGRESS_FINISH
EVENT_SEAL_BATTLE_BEGIN
EVENT_SEAL_BATTLE_END
EVENT_SEAL_BATTLE_PROGRESS_DECREASE
```

### 5.2 触发 API

```java
scriptManager.callEvent(new ScriptArgs(groupId, EventType.EVENT_ANY_MONSTER_DIE, configId));
```

`ScriptArgs` 携带：
- `groupId` —— 所在场景组
- `eventType` —— 事件类型
- `param1, param2, ...` —— 参数

### 5.3 Lua 接收

Lua 脚本里：
```lua
function on_monster_die(context, monster_id)
    if monster_id == 1001 then
        spawn_chest(context)
    end
end
```

服务器调 callEvent → Lua 引擎查找对应函数 → 执行 → 可能调回 Java（spawn_chest 是 Java API）。

### 5.4 跨边界的 LUA_NOTIFY

`EVENT_LUA_NOTIFY` 是**跨边界事件**：
- Lua 主动通知服务器 "我想触发一个 quest content"
- 服务器收到后调 `queueEvent(QUEST_CONTENT_LUA_NOTIFY, ...)`
- Quest 系统**实质上听 Lua 的指挥**

→ 这是 Lua 和 Quest 系统**双向沟通**的桥梁（参见 notes/08 Talk 系统）。

---

## 6. 4 套系统的关系总览

### 6.1 调用统计

```bash
$ grep -c "triggerMission\|queueEvent\|callEvent\|EventType\."
156 处调用
```

分布：
- `triggerMission(WatcherTriggerType.xxx)` —— BattlePass 系统**自调用** + 各 Manager 通知
- `queueEvent(QuestContent.xxx)` —— 各 Manager 通知 Quest
- `queueEvent(QuestCond.xxx)` —— 各 Manager 通知 Quest（条件）
- `callEvent(EventType.xxx)` —— 各 Manager 通知 Lua

### 6.2 谁触发哪套

| 触发者 | WatcherTrigger | QuestContent | QuestCond | Lua Event |
|---|---|---|---|---|
| **Inventory.addItem** | ✓ | ✓ ✓ | ✓ | - |
| **EntityMonster.onDeath** | ✓ | ✓ ✓ | - | ✓ |
| **EntityGadget.onDeath** | - | - | - | ✓ |
| **EntityGadget.onInteract** | - | ✓ | - | ✓ |
| **Player.onLogin** | ✓ | - | - | - |
| **Quest.finish** | - | - | - | ✓ |
| **DungeonManager** | ✓ | ✓ | - | - |
| **TalkSystem** | - | ✓ | ✓ | - |
| **Avatar.upgrade** | ✓ | - | - | - |
| **GadgetGatherObject** | - | - | - | ✓ |

→ **每个 Manager 自行判断要触发哪几套**——没有"统一发布"机制。

### 6.3 4 套都触发的代码（最饱和）

```java
// 假想的"完整事件触发"
player.getBattlePassManager().triggerMission(TRIGGER_MONSTER_DIE, monsterId, 1);  // BattlePass
player.getQuestManager().queueEvent(QUEST_CONTENT_MONSTER_DIE, monsterId);        // Quest 进度
player.getQuestManager().queueEvent(QUEST_COND_HISTORY_GOT_ANY_ITEM, ...);        // Quest 条件
scene.getScriptManager().callEvent(EVENT_ANY_MONSTER_DIE, configId);              // Lua
```

→ **实际怪物死亡触发的事件数**就这么多——4 套并行各自接到通知。

---

## 7. 典型案例：怪物死亡的完整事件级联

把 notes/32 §11 的 onDeath 7 件事放到事件总线视角：

```
[event source] EntityMonster.onDeath()
    ↓ 同步触发 4 套系统
    
[1] 战令系统
    player.getBattlePassManager().triggerMission(
        WatcherTriggerType.TRIGGER_MONSTER_DIE, monsterId, 1)
    ↓ 战令任务进度更新
    
[2] Quest 系统 (两次)
    player.getQuestManager().queueEvent(
        QuestContent.QUEST_CONTENT_MONSTER_DIE, monsterId)
    player.getQuestManager().queueEvent(
        QuestContent.QUEST_CONTENT_KILL_MONSTER, monsterId)
    ↓ 杀怪任务进度
    
[3] 组清空检测
    if (scriptManager.isClearedGroupMonsters(groupId))
        player.getQuestManager().queueEvent(
            QuestContent.QUEST_CONTENT_CLEAR_GROUP_MONSTER, groupId)
    ↓ "清光这组怪" 任务
    
[4] Lua 系统
    scriptManager.callEvent(new ScriptArgs(groupId, EventType.EVENT_ANY_MONSTER_DIE, configId))
    ↓ Lua 可能触发: spawn 下波怪 / 开宝箱 / 切剧情
    
[5] 副本系统
    scene.triggerDungeonEvent(DungeonPassConditionType.DUNGEON_COND_KILL_MONSTER, monsterId)
    scene.triggerDungeonEvent(DungeonPassConditionType.DUNGEON_COND_KILL_TYPE_MONSTER, type)
    scene.triggerDungeonEvent(DungeonPassConditionType.DUNGEON_COND_KILL_GROUP_MONSTER, groupId)
    ↓ 副本通关条件检测
    
[6] 持久化
    SceneGroupInstance.deadEntities.add(configId)
    DeadSpawnedEntities.add(spawnEntry)
    ↓ 重连后不复活
    
[7] 封印之战
    scene.getSealBattleManager().onKill(this)
    ↓ 须弥 boss 战进度
```

→ **一次怪物死 → 7 套子系统 + 4 套事件类型 + 10+ 次事件调用**。这是 grasscutter 跨系统协作的"高密度时刻"。

---

## 8. 注册到使用：事件订阅链

### 8.1 BattlePass Watcher 怎么订阅

```yaml
# BattlePassMissionExcelConfigData.json
mission_id: 4123
trigger_type: TRIGGER_MONSTER_DIE        # ← 监听哪个事件
target_count: 100                          # 累计 100 次
reward: bp_exp:10                          # 完成给 10 BP 经验
```

`BattlePassManager.triggerMission(TRIGGER_MONSTER_DIE, monsterId, 1)`：
- 扫描所有 mission 配置
- 找出监听 `TRIGGER_MONSTER_DIE` 的
- 累计 progress
- 达 target_count → 给奖励

### 8.2 Quest Content 怎么订阅

```json
// MainQuest.json
{
  "subId": 30220101,
  "finishCond": [
    {"type": "QUEST_CONTENT_MONSTER_DIE", "param": [21010101, 5]}
  ]
}
```

`QuestManager.queueEvent(QUEST_CONTENT_MONSTER_DIE, 21010101)`：
- 扫描所有 active SubQuest
- 找出 finishCond 含 `QUEST_CONTENT_MONSTER_DIE` 的
- 累计 progress
- 全部 cond 满足 → SubQuest finish

### 8.3 Lua Event 怎么订阅

```lua
-- group.lua
triggers = {
    { config_id = 70001, name = "any_monster_die_trigger",
      event = EventType.EVENT_ANY_MONSTER_DIE,
      source = "1001",   -- ← 只关心 configId=1001 的怪
      condition = "condition_event_any_monster_die",
      action = "action_event_any_monster_die" }
}

function condition_event_any_monster_die(context, evt)
    return evt.param1 == 1001
end

function action_event_any_monster_die(context, evt)
    spawn_gadget(context, 70010)   -- 触发宝箱
end
```

→ Lua 走"trigger 注册 + condition + action"三段式（notes/14）。

---

## 9. 事件参数协议

### 9.1 各套系统的参数

```java
// WatcherTriggerType
triggerMission(triggerType, param1, param2, ...)
// 通常: param1=ID, param2=count

// QuestContent
queueEvent(contentType, param1, param2, ...)
// 通常: param1=ID, param2=count/state

// QuestCond
queueEvent(condType, param1, param2)
// 通常: param1=ID, param2=value

// Lua EventType
callEvent(new ScriptArgs(groupId, eventType, param1).setParam2(p2).setParam3(p3))
// ScriptArgs 携带最多 4 个 param
```

→ **统一是 (type + param1 + param2 + ...)** 结构 —— 但参数语义随事件类型变化。

### 9.2 参数语义示例

| 事件 | param1 | param2 | param3 |
|---|---|---|---|
| TRIGGER_MONSTER_DIE | monsterId | 1 (count) | - |
| TRIGGER_OBTAIN_MATERIAL_NUM | itemId | count | - |
| TRIGGER_AVATAR_UPGRADE | avatarId | newLevel | - |
| QUEST_CONTENT_MONSTER_DIE | monsterId | - | - |
| QUEST_CONTENT_QUEST_VAR_EQUAL | varKey | varValue | - |
| EVENT_ANY_MONSTER_DIE | configId | - | - |
| EVENT_SPECIFIC_MONSTER_HP_CHANGE | configId | monsterId | newHP |

→ **配置 mission/quest/trigger 时必须知道每个事件的参数语义** —— 没文档，靠源码注释 + 配表反推。

---

## 10. 4 套系统的设计取舍

### 10.1 优点

- ✓ **各自演化** —— BattlePass / Quest / Lua 独立团队，互不阻塞
- ✓ **职责清晰** —— "成就用 W"、"任务进度用 QC"、"任务前置用 QCo"、"场景脚本用 Lua"
- ✓ **可独立优化** —— BattlePass 可以异步处理，Lua 可以 4 线程并发

### 10.2 缺点

- ✗ **重复触发** —— 每个事件 Manager 要触发 2-4 套
- ✗ **遗漏风险** —— 加新事件容易漏触发某套
- ✗ **配置者负担** —— 配 BP 任务用 WatcherTriggerType，配主线任务用 QuestContent，命名不一致
- ✗ **测试复杂** —— 4 套系统都要回归

### 10.3 改进方向（如果重写）

```
[统一事件总线设计]
EventBus.publish("monster.die", {monsterId, count, killer})
   ↓
Subscriber 注册:
  - BattlePass 监听 "monster.die"
  - Quest 监听 "monster.die"
  - Lua 监听 "monster.die"
   ↓
触发者只发 1 次, 多个订阅者并行处理
```

→ grasscutter 没这么做（历史包袱），但**Akka / Kafka / Spring Event** 这种现代框架就是这样。

---

## 11. 反作弊视角

### 11.1 可伪造的事件

| 攻击 | 是否有效 |
|---|---|
| 客户端发包伪造杀怪 | ✗ 服务器 onDeath 才触发 |
| 篡改 mission 进度数字 | ✗ 服务器存 |
| 直接发"完成 Quest" | ✗ 没有这种 packet |
| Lua 注入新 trigger | ✗ Lua 在服务器跑 |

→ **事件总线本身反作弊较强** —— 因为触发都在服务器，客户端不能直接发"事件触发"。

### 11.2 间接作弊路径

```
[客户端] 伪造伤害 → 怪 HP=0 → onDeath
    ↓ 事件总线触发
[服务器] 4 套系统照常累计进度
```

→ 通过伤害伪造**间接刷事件**——但每只怪只能死一次，刷不出额外。

---

## 12. 关键收获

1. **4 套并行事件系统**：WatcherTriggerType (299) / QuestContent (80+) / QuestCond (80+) / Lua EventType (30+)
2. **总计 500+ 事件类型** —— 是 grasscutter 跨系统协作的"神经网络"
3. **156 处调用** triggerMission / queueEvent / callEvent
4. **WatcherTriggerType 9 段位**：战斗 / 世界 / 升级 / 日常 / 制作 / 登录 / 挑战 / 任务 / 旧版
5. **核心事件 4 套都有**：杀怪 / 获得物品 / 通关副本 / 玩家升级——必须**同时触发 4 套**
6. **Inventory.triggerAddItemEvents 一次触发 4 个事件**：BP + QuestContent ×2 + QuestCond
7. **Content vs Cond 区别**：Content = "做什么"(进度) / Cond = "条件"(前置)
8. **#Missing 注释 = 未实现的条件**：导致某些任务在 grasscutter 无法接
9. **Lua EventType 跨边界**：`EVENT_LUA_NOTIFY` 让 Lua 主动通知 Quest 系统
10. **怪物死触发 7 套子系统 + 10+ 次事件调用**
11. **事件参数语义无统一文档**：靠源码 + 配表反推
12. **设计取舍**：4 套并行 = 各自演化但重复触发——历史包袱
13. **反作弊较强**：事件触发都在服务器，客户端不能直接发"事件触发"

---

## 13. 一句话总结

> **事件类型 = grasscutter 跨系统协作的"神经网络" —— 4 套并行枚举 (WatcherTriggerType 299/QuestContent 80+/QuestCond 80+/Lua EventType 30+) 共 500+ 种, 156 处 triggerMission/queueEvent/callEvent 调用; 核心事件 (杀怪/物品/升级) 4 套都有, 触发者必须同时调 4 个; Content="做什么"/Cond="条件"; 怪物死触发 7 子系统 + 10+ 事件; 反作弊较强 (服务器触发)。**
> 
> **设计哲学: 各子系统独立演化导致重复枚举但职责清晰——4 套各自有自己的 mission 配表 / quest 配表 / trigger 配置, 加新内容时配置者明确知道用哪套, 代价是每个事件源要触发多套。**

---

**前置笔记**：
- notes/02-09 任务系统 - QuestContent/QuestCond 触发器
- notes/14 SceneScript - Lua EventType 30+ 类
- notes/20 Activity - WatcherTriggerType 150+ 类
- notes/22 BattlePass - WatcherTriggerType 实战
- notes/27 架构模式 - 第 1 次提到"跨系统事件总线"
- notes/32 怪物 - onDeath 7 件事
- notes/38 Inventory - addItem 触发 4 套
- notes/40 Player Manager 横切 - 25+ Manager 协作

**关联文件**：
- `WatcherTriggerType.java`(337) - 299 个 TRIGGER_*
- `QuestContent.java`(117) - 80+ 个 QUEST_CONTENT_*
- `QuestCond.java`(121) - 80+ 个 QUEST_COND_*
- `gi_lua` 模块的 `EventType` - 30+ 个 EVENT_*
- 156 处调用散布在: Inventory / EntityMonster / EntityGadget / Avatar / Quest / BattlePass / Achievement / Codex / Dungeon

**研究的源代码**: 575 行枚举定义 + 156 处事件触发代码。
