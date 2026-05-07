# 06 · 全量任务语料分析（2360 个 MainQuest，20,893 个 SubQuest）

跑 `scripts/analyze_quests.py` 得到的真实使用分布——**揭示几个仅看代码看不出的设计细节**。

> 数据源：`GenshinData/BinOutput/Quest/*.json`（2360 文件，37 MB）。注意 fork 里字段名是版本特定的混淆 key，本脚本采用**递归全树扫描 + 字符串值前缀匹配**绕过命名问题。

## 1. 一图概览

```
MainQuest 文件数:            2,360
SubQuest 总数:              20,893    (平均 8.9/main)
Talk 引用总数:               7,630    (平均 3.2/main)
对话脚本路径总数:            6,532
含 luaPath 的 MainQuest:     2,028    (85.9%)
```

**SubQuest 数量分布**（每主线步骤数）：

```
0 步:      12 个
1-5 步:  1169 ##################################################
6-10 步:  520 ######################
11-20 步: 437 ##################
21-50 步: 200 ########
50+  步:   22
```

→ **绝大多数主线很短**（≤5 步），但有 22 个"巨型任务"超过 50 步。

**Top 5 巨型 MainQuest**：

```
mainId=79041:  500 SubQuests   ← 不可思议，可能是大型活动总流程
mainId=73231:  328
mainId=20051:  125
mainId=73025:   99
mainId=73287:   75
```

---

## 2. QuestCond 真实分布（33 种 / 共 19,000 次）

```
15370  QUEST_COND_STATE_EQUAL          ← 占 81%！绝对主导
  467  QUEST_COND_ITEM_GIVING_ACTIVED
  400  QUEST_COND_QUEST_VAR_EQUAL
  351  QUEST_COND_QUEST_GLOBAL_VAR_EQUAL
  213  QUEST_COND_QUEST_NOT_RECEIVE
  183  QUEST_COND_PACK_HAVE_ITEM
   68  QUEST_COND_STATE_NOT_EQUAL
   28  QUEST_COND_QUEST_VAR_LESS
   ...
```

### 关键发现

**`QUEST_COND_*` 主要不是用在 SubQuest 的 acceptCond，而是用在 Talk 对话的可见性条件**。即"什么状态下这条对话才能出现"。这解释了为什么 1001.json 的 SubQuest 没有 acceptCond——主任务的步骤切换大量靠 **`finishExec: ADD_QUEST_PROGRESS`** + **`finishCond: ADD_QUEST_PROGRESS`** 这种轻量级 sync，而不是状态订阅。

具体的 Talk 条件结构（从 1000.json 实测）：

```jsonc
{
    "_type": "QUEST_COND_STATE_EQUAL",     // 字面 _type 字段，未混淆
    "_param": ["100002", "2"]              // 字面 _param，注意是字符串数组
}
```

→ Talk 的 cond 用裸的 `_type` / `_param`，跟 SubQuest 不共享 schema。这是**两套 cond 系统**。

---

## 3. QuestContent 真实分布（66 种 / 共 ~22,500 次）

```
8515  QUEST_CONTENT_COMPLETE_TALK         ← 占 38%！对话完成是最常用完成条件
3401  QUEST_CONTENT_LUA_NOTIFY            ← 15%   Lua 主动通知
3337  QUEST_CONTENT_FINISH_PLOT           ← 15%   剧情/CG 结束
1268  QUEST_CONTENT_QUEST_STATE_EQUAL     ← 等待另一任务状态
 627  QUEST_CONTENT_MAIN_COOP_ENTER_SAVE_POINT    ← 多人合作存档点
 545  QUEST_CONTENT_GAME_TIME_TICK        ← 游戏时间到点
 505  QUEST_CONTENT_ADD_QUEST_PROGRESS    ← 进度计数器（手动 +N）
 459  QUEST_CONTENT_FINISH_ITEM_GIVING    ← 给 NPC 物品
 435  QUEST_CONTENT_ENTER_MY_WORLD        ← 进入世界
 421  QUEST_CONTENT_GADGET_STATE_CHANGE   ← 机关状态变化
 409  QUEST_CONTENT_OBTAIN_ITEM           ← 获得道具
 406  QUEST_CONTENT_FAIL_DUNGEON          ← 副本失败
 367  QUEST_CONTENT_NOT_FINISH_PLOT
 364  QUEST_CONTENT_LEAVE_SCENE
 ...
```

### 关键发现

1. **对话 + Lua + 剧情** 三者占 68%——任务完成判定**严重依赖剧情演出和脚本通知**，而非"杀 N 只怪"
2. `KILL_MONSTER` 类型出现频次很低（不在 top 30）——**击杀任务不是原神主流**
3. `ADD_QUEST_PROGRESS` 这个机制在底层作为"进度同步管道"被反复使用：业务系统调 `addQuestProgress(subId, count)` → 触发本事件 → 检测到该 SubQuest 完成

---

## 4. QuestExec 真实分布（61 种 / 共 ~10,500 次）

```
1553  QUEST_EXEC_REFRESH_GROUP_SUITE        ← 切换场景配置（NPC 出现/消失）
1526  QUEST_EXEC_UNREGISTER_DYNAMIC_GROUP   ← 卸载动态实体组
1513  QUEST_EXEC_ADD_QUEST_PROGRESS         ← 推进进度（如上面所述，是同步管道）
1420  QUEST_EXEC_ROLLBACK_QUEST             ← 重置某 SubQuest 进度
1087  QUEST_EXEC_NOTIFY_GROUP_LUA           ← 通知场景 Lua（任务→脚本桥）
 494  QUEST_EXEC_SET_QUEST_GLOBAL_VAR
 459  QUEST_EXEC_REMOVE_TRIAL_AVATAR        ← 移除试用角色
 399  QUEST_EXEC_DEL_PACK_ITEM              ← 删除背包道具
 343  QUEST_EXEC_NOTIFY_DAILY_TASK          ← 通知委托完成
 315  QUEST_EXEC_DEL_ALL_SPECIFIC_PACK_ITEM
 265  QUEST_EXEC_SET_QUEST_VAR
 201  QUEST_EXEC_UNLOCK_AVATAR_TEAM
 186  QUEST_EXEC_SET_WEATHER_GADGET         ← 改天气
 170  QUEST_EXEC_INC_QUEST_VAR
 135  QUEST_EXEC_SET_GAME_TIME              ← 强制设定游戏时间（剧情常用）
 ...
```

### 关键发现

1. **场景管理 + 任务进度 + Lua 通知** 占 60%——任务系统的"输出端"主要是**改场景状态**和**给脚本发信号**
2. `REFRESH_GROUP_SUITE` + `UNREGISTER_DYNAMIC_GROUP` + `REGISTER_DYNAMIC_GROUP` 一起暗示**"场景实体动态加载"系统**——任务驱动地图变化
3. `ROLLBACK_QUEST` 用了 1420 次！比想象中多——大量任务用回滚做"循环逻辑"或"分支重做"
4. 没有 `GIVE_REWARD_ITEMS` 类的高频 exec——奖励主要通过 `gainItems` 字段和 `MainQuest.rewardIdList`，不通过 finishExec

---

## 5. LogicType 实际分布

```
2792  LOGIC_AND               ← 73%
1069  LOGIC_OR                ← 25%
 241  LOGIC_A_AND_ETCOR
  15  LOGIC_A_AND_B_AND_ETCOR
   4  LOGIC_A_OR_ETCAND
   4  LOGIC_A_OR_B_OR_ETCAND
   2  LOGIC_A_AND_B_OR_ETCAND
```

→ **AND : OR ≈ 3 : 1**，奇葩组合（A_AND_ETCOR 等）极少用。验证了"复杂逻辑应该拆 SubQuest 而非靠组合"的设计取向。

---

## 6. Talk 子系统：原神有自己的"对话脚本语言"

Talk 系统出现频率竟然这么高（共 7630 个 Talk 引用），且有自己独立的 schema：

### Talk 类型分布（23 种 / ~22,000 次）

```
9824  TALK_BEGIN_MANUAL          ← 玩家主动点 NPC 触发
8720  TALK_HERO_MAIN             ← 主角对话
1460  TALK_ROLE_NPC              ← NPC 对话
 914  TALK_BEGIN_AUTO            ← 进入区域自动触发
 302  TALK_ROLE_PLAYER
 169  TALK_MARK_COMMON
  71  TALK_EXEC_SET_QUEST_VAR    ← !!! 对话框里执行 exec
  60  TALK_SHOW_FORCE_SELECT     ← 强制选项（不可跳过）
  55  TALK_ROLE_BLACK_SCREEN     ← 黑屏文字（旁白）
  31  TALK_EXEC_SET_QUEST_GLOBAL_VAR
  24  TALK_EXEC_SAVE_TALK_ID
  15  TALK_EXEC_INC_QUEST_VAR
  ...
```

### 重磅发现：`TALK_EXEC_*`

**对话本身可以执行 exec 副作用**——选某个对话选项就会改 questVar、保存进度等。这意味着：

- 选项不是简单的"显示文本"，而是 **state-changing actions**
- 任务系统 → 对话系统 → 任务系统 形成闭环
- 这是 RPG 选择分支的实现机制

### Talk 单元结构（实测）

```jsonc
{
    "JOLEJEFDNJJ": 100002,                      // talkId
    "CGMHJIBLJEE": "TALK_BEGIN_MANUAL",         // begin type
    "NKLEMELAGEE": "LOGIC_AND",                 // logic combine
    "MNPHAFOHNML": [                            // conditions
        {"_type": "QUEST_COND_STATE_EQUAL", "_param": ["100002", "2"]},
        {"_type": "QUEST_COND_STATE_EQUAL", "_param": ["100005", "2"]}
    ],
    "FOHDKIBNGJB": "QuestDialogue/AQ/Liyue1_1000/Q100001",   // 对话脚本路径
    "DFOGMKICPEF": [1005],                                   // 关联 NPC 列表
    "KAAKKMJJJIM": "TALK_HERO_MAIN",                         // 主体类型
    "GLABOIDHFKF": 1000,                                     // 所属 mainQuestId
    "ILJJONAKPMF": 15364519118938081779                      // 文本 hash
}
```

**`QuestDialogue/AQ/Liyue1_1000/Q100001`** 是真实的脚本路径——存在某种文件系统组织，AQ=Archon Quest（魔神），Liyue1_1000=区域+任务编号。

---

## 7. QUEST_GUIDE 子系统：导航提示层

我们之前完全没注意到这个子系统：

```
9300  QUEST_GUIDE_LAYER_SCENE       ← 场景层导航（屏幕指针）
6967  QUEST_GUIDE_STYLE_TARGET      ← "去往 X" 样式
5739  QUEST_GUIDE_NPC               ← 指向 NPC 的提示
5307  QUEST_GUIDE_LOCATION          ← 指向地点的提示
3436  QUEST_GUIDE_AUTO_ENABLE       ← 自动开启
2983  QUEST_GUIDE_STYLE_POINT       ← 标点样式
2788  QUEST_GUIDE_ITEM_DISABLE      ← 禁用导航
1082  QUEST_GUIDE_LAYER_UI          ← UI 层导航
 432  QUEST_GUIDE_STYLE_START       ← 起点样式
 349  QUEST_GUIDE_STYLE_FINISH      ← 终点样式
 165  QUEST_GUIDE_GADGET            ← 指向机关
  46  QUEST_GUIDE_SHOW_OR_HIDE_NPC
  42  QUEST_GUIDE_AUTO_DISABLE
   6  QUEST_GUIDE_HINT_DESHRET_MANUAL
   6  QUEST_GUIDE_HINT_READING_DIALOG
```

→ **任务的"导航提示层"是一套独立的小语言**，配置在 SubQuest 的 `guide` 字段（之前我们看到的 `ADAPCLIELKE: {}` 那个空对象）。

类型组合方式：
- `LAYER_*`：在哪一层显示（场景指针 / UI）
- `STYLE_*`：用什么样式（target / point / start / finish）
- `LOCATION` / `NPC` / `GADGET`：指向什么

总出现 ~35,000 次——平均每个 SubQuest 1.7 个导航提示。**这是为什么原神剧情中"该往哪走"几乎不会让玩家迷路**。

---

## 8. 新的设计洞察总结

把全量分析结果叠回我们的认知模型，得到几个修正：

### 修正 1：acceptCond 在 SubQuest 上不是主流
- 我们最初以为 SubQuest 接取靠 acceptCond 订阅事件
- **实际上**：大多数 SubQuest 没有 acceptCond，靠**`addQuestProgress` 进度推进**或**`addNewMainQuest` 取最小 order** 来流转
- acceptCond 主要用于**分支接取**（少数有条件的 SubQuest）和 **Talk 可见性**（大部分使用场景）

### 修正 2：Talk 是独立子系统
- Talk 不是 MainQuest 的小附庸，它有：
  - 自己的 cond 系统（`_type` / `_param` 字面字段）
  - 自己的 exec 系统（`TALK_EXEC_*`）
  - 自己的展现层（`TALK_BEGIN_*` / `TALK_ROLE_*` / `TALK_SHOW_*`）
  - 自己的脚本路径（`QuestDialogue/AQ/...`）
- 设计上 Talk 是**和 Quest 并列的子系统**，互相通过 cond 引用对方的状态

### 修正 3：场景实体由任务驱动
- `REFRESH_GROUP_SUITE` / `REGISTER_DYNAMIC_GROUP` / `UNREGISTER_DYNAMIC_GROUP` 共 3,178 次
- **任务系统主动操控场景**——NPC 的出现/消失、机关的状态、动态生成的实体，都通过任务 exec 完成
- 这印证了"任务系统是 progression 的中央总线"

### 修正 4：导航提示是独立子系统
- `QUEST_GUIDE_*` 共 ~35,000 次出现
- 完全独立的 mini-DSL，专管"该去哪 / 去找谁 / 看哪个机关"
- 这部分 Grasscutter 实现很简单（看代码就知道），但配表里非常详细

---

## 9. 怎么用这个分析数据做决策

如果你做类似系统：

1. **不要上来就给 SubQuest 加 acceptCond**——大多数情况下 sequential `addQuestProgress` 就够了
2. **Talk 系统要独立设计**——别想着"对话只是任务的子节点"
3. **`REFRESH_GROUP_SUITE` 这种"切换场景配置"概念要早期内置**——后期补成本巨大
4. **导航提示要做成独立 DSL**——它和"任务在做什么"是两件事
5. **统计驱动设计**：你的策划用得最多的 cond/exec 类型，决定了你**真的需要**优化哪些 handler 的性能

---

## 数据复现

```bash
python scripts/analyze_quests.py > my_analysis.txt
```

依赖：Python 3.8+ 标准库（`json`, `collections`, `pathlib`）。

需要先跑 `setup.sh` / `setup.ps1` 拉到 `GenshinData/`。
