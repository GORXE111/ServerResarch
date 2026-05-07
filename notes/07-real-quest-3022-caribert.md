# 07 · 真实剧情任务深度拆解：MainQuest 3022 "识藏日"（须弥章第三章 第五幕）

> **🔧 命名修正（基于 notes/11 的 TextMap 翻译结果）**：本文最初误称此任务为 "Caribert"。实际通过 TextMap 翻译验证，MainQuest 3022 真名是 **"识藏日"**（英文版："Akasha Pulses, the Kalpa Flame Rises"，须弥章第三章第五幕），描述："终于到了「识藏日」这天，一切计划安排与一切的准备，都只为了一个目标——「拯救神明」。" 这是从教令院夺回小吉祥草王（纳希妲）的高潮章节。文章其余分析（save-point 模式、failExec 设计等）依然有效。


第二个真实任务案例。比 1001 大 16 倍，展示原神剧情任务的**电影化叙事架构**。

> 数据来源：`GenshinData/BinOutput/Quest/3022.json` (3493 行)
> Lua 入口：`Actor/Quest/AQ3022`
> 对话脚本：`QuestDialogue/AQ/Sumeru3_3022/Q302201` ~ `Q302225`（25 个独立对话文件）
> 这是须弥魔神任务最终幕——**与 Dainsleif 重逢、揭开旅行者兄妹真相**

---

## 1. 顶层结构对比

| 项 | MainQuest 1001 | MainQuest 3022 |
|---|---|---|
| SubQuest 数 | 3 | **49** |
| Talk 数 | 0 | **32** |
| 对话脚本数 | 0 | **25** |
| 总行数 | 107 | 3493 |
| Lua 入口 | `Actor/Quest/MQ1001` | `Actor/Quest/AQ3022` |
| 性质 | 教学/测试 | 真实主线魔神任务 |

3022 顶层有 **5 个独立数组字段**（SubQuests / Talks / 还有 3 个未明确的子结构），结构密度远超 1001。

---

## 2. 字段名混淆是 per-file 的（重要发现）

我们之前以为字段混淆 key 是版本固定的。但对比 1001 和 3022：

| 字段语义 | 1001 中的 key | 3022 中的 key |
|---|---|---|
| `subQuests` 数组 | `MPBNEILAFCB` | `MPBNEILAFCB` ✓ 相同 |
| `subId` | `ILPBLDDCLDB` | `ILPBLDDCLDB` ✓ 相同 |
| `finishCond` | `JCHNHPHNFPP` | `JCHNHPHNFPP` ✓ 相同 |
| `failCond` | `JABFCLMAGKN` | `JABFCLMAGKN` ✓ 相同 |
| `finishExec` | `GIJNFABJPLK` | `GIJNFABJPLK` ✓ 相同 |
| **`failExec`** | (没出现) | `KIOEECHONOG` ← 新出现的 key |

**修正**：混淆 key 在**整个版本内**是一致的，**1001 没有 failExec 字段**（因为它是个简单测试任务），不是 key 不同。我之前的猜测是错的——key 是版本固定的，全局可复用。

**3022 中识别出的 SubQuest 字段映射**：

```
ILPBLDDCLDB  →  subId
IEFDCPGFPFP  →  mainId
EPAEFJJNLEP  →  order
JIHEILBABBF  →  descTextMapHash
JCHNHPHNFPP  →  finishCond[]
JABFCLMAGKN  →  failCond[]
GIJNFABJPLK  →  finishExec[]      ← 完成时执行
KIOEECHONOG  →  failExec[]        ← 失败时执行（!!）
ADAPCLIELKE  →  guide{}           ← 导航提示对象
PCEKHDNNJFI  →  ??? (含 EILMHFHJPOJ 字段)
LEANNGJJHPH  →  isRewind          ← 可作 rewind 锚点
FJOHFMAOAEA  →  isMpBlock         ← 多人模式禁用
```

---

## 3. 真实 SubQuest 实例：第 6 步（subId 302205）

```jsonc
{
    "subId": 302205,
    "mainId": 3022,
    "order": 6,
    "descTextMapHash": 3074855916,
    "isMpBlock": true,                      // 多人模式禁用
    "isRewind": true,                       // 可作 rewind 锚点

    "guide": {                              // 导航提示
        "guideScene": 20162,                // 须弥沙漠场景 ID
        "layer": "QUEST_GUIDE_LAYER_SCENE",
        "style": "QUEST_GUIDE_STYLE_TARGET",
        "param": ["Q302205_N10000005"],     // 命名锚点
        "type": "QUEST_GUIDE_LOCATION"
    },

    "finishCond": [
        { "type": "QUEST_CONTENT_COMPLETE_TALK", "param": [302205, 0] }
    ],
    "failCond": [
        { "type": "QUEST_CONTENT_LEAVE_SCENE", "param": [20162, 0] }
    ],
    "failExec": [
        { "type": "QUEST_EXEC_UNLOCK_AVATAR_TEAM" },
        { "type": "QUEST_EXEC_ROLLBACK_QUEST", "param": ["302204"] }
    ]
}
```

### 设计模式 A：**Save-point + Replay 模式**

注意 `failExec` 中的 `ROLLBACK_QUEST 302204`——指向**SubQuest 302204**。

我数了 49 个 SubQuests 中至少 **18 个 SubQuests 的 failExec 都包含 `ROLLBACK_QUEST 302204`**。这是个"全局 save point"：

```
                            302204 (Save Point: order=4)
                                ↓ (玩家完成 302204 后进入剧情序列)
        ┌──────────┬───────────┴──────────┬──────────┐
        302205     302206       ...       302219     302220
        order=6    order=7                order=39    order=40
        失败→302204 失败→302204            失败→302204 失败→302204
```

**任何一个剧情步骤失败（玩家走出场景），都直接 rollback 到 302204 重新进入剧情**——这是原神剧情"传送回上一个安全点"的实现。

对比 1001：1001 用 rollback 来支持**简单的状态回退**（finishExec rollback 上一步）；3022 用 rollback 实现**电影化叙事的容错重做**（failExec rollback 到一个固定 save point）。

### 设计模式 B：**LEAVE_SCENE 作为失败触发**

`failCond: LEAVE_SCENE 20162` 的语义是 "**玩家离开场景 = 任务失败**"。

为什么？因为 302205 是个**locked-team 剧情对话**——player 只能用预定的角色（旅行者 + Paimon + Dainsleif）。如果允许玩家随意离开场景，剧情连贯性会被破坏。

所以原神的解决方案：
1. 对话开始时锁定场景 + 锁定角色队
2. failCond 监听 "离开场景" 事件
3. failExec 触发时：解锁角色队（让玩家自由）+ rollback 到 save point

**这是一种"沉浸式剧情边界"的优雅实现**——既限制玩家，又有逃生通道。

### 设计模式 C：**UNLOCK_AVATAR_TEAM 出现 20 次**

3022 全文有 **20 个** `QUEST_EXEC_UNLOCK_AVATAR_TEAM`。这意味着至少有 10+ 个段落经历过"锁定 → 解锁"循环。

锁定怎么做的？SubQuest 配表里没有 LOCK 类型——肯定是通过 **`REFRESH_GROUP_SUITE` 切换场景配置**实现的：进入剧情场景时，场景 Lua 自动锁定可换队伍；剧情结束或失败时，任务的 `UNLOCK_AVATAR_TEAM` exec 解锁。

---

## 4. 设计模式 D：用 `questGlobalVar[3022]` 做"叙事状态机"

注意 SubQuest 302207 的 finishExec：

```jsonc
"finishExec": [
    { "type": "QUEST_EXEC_SET_QUEST_GLOBAL_VAR", "param": ["3022", "1"] }
]
```

→ 完成第 8 步（order=8）时，把 `questGlobalVar[3022]` 设为 **1**。

再看 SubQuest 302219（order=39）：

```jsonc
"finishExec": [
    { "type": "QUEST_EXEC_SET_QUEST_GLOBAL_VAR", "param": ["3022", "6"] }
]
```

→ 设为 **6**。

这是一个**全局叙事状态变量**，在 49 个 SubQuests 间从 0→1→...→6 演进。它充当**"剧情已推进到第几幕"**的全局标记。其他系统（场景脚本、Talk 可见性、其他任务）可以订阅这个值。

**这就是分支剧情和"剧情后世界变化"的实现机制**：
- "玩家做完 识藏日 后，须弥某 NPC 才有新对话" → 该 NPC 的 Talk 条件订阅 `QUEST_COND_QUEST_GLOBAL_VAR_EQUAL [3022, 6]`
- "完成 3022 后特定地点解锁" → 场景 Lua 检查同一变量

---

## 5. SubQuest order 不连续！

我们之前以为 order 是 1, 2, 3, ... 顺序。但 3022 的实际 order 顺序：

```
order   subId
  1   302201
  2   302202
  3   302203
  4   302204    ← Save point
  5   302225    ← 注意：不是 302205！
  6   302205
  7   302206
  8   302207
  9   302233    ← 跳来跳去
 10   302208
 11   302237
 12   302243
 13   302244
 14   302209
 15   302231
 16   302232
 17   302249
 18   302210
 19   302235
 20   302239
 ...
```

**subId 数字大小 ≠ 执行顺序**。order 才是真实的执行顺序。

为什么？策划在制作过程中可能：
- 先按场景写一组 subId（例如 302201-302209 = 沙漠剧情，302210-302219 = 实验室剧情）
- 后期决定剧情走向（先实验室还是先沙漠），通过 order 重新排序
- 中间插入新步骤（如 302225），不需要重命名已有 subId

**subId 是稳定身份，order 是叙事时序**——分离了"标识"和"流程"两个变化轴。这是**配表系统抗 churn 的关键设计**。

---

## 6. 真实 Talk 实例

```jsonc
{
    "talkId": 30221017,
    "nextTalks": [30221018],                       // 链接到下一个 talk
    "talkRole": {                                  // talk 角色
        "_id": "1056",                             // NPC id 1056 = Dainsleif
        "_type": "TALK_ROLE_NPC"
    },
    "textMapHash": 1140390738
}
```

3022 中所有 talkRole 都是 NPC 1056（Dainsleif）——印证这是 识藏日 章，Dainsleif 是该剧情的核心 NPC。

talkId 30221017 → nextTalks [30221018] → ... 形成**对话链表**。Talk 系统可以表达：
- 单线对话（链表）
- 分支对话（nextTalks 多个，玩家选择）
- 强制选项（无法跳过的对话节点）

---

## 7. 综合时序图（前 10 步）

```
[玩家接受 MainQuest 3022]
   ↓
order=1  302201: GAME_TIME_TICK              ← 等待游戏时间
   ↓
order=2  302202: COMPLETE_TALK               ← 第一段对话
   ↓
order=3  302203: COMPLETE_TALK               ← 第二段对话
   ↓
order=4  302204: ?? (Save Point)             ← 后续所有失败都 rollback 到这
   ↓
order=5  302225: COMPLETE_TALK + LEAVE_SCENE ← 沉浸式对话开始
            failExec: UNLOCK_AVATAR_TEAM + ROLLBACK 302204
   ↓
order=6  302205: COMPLETE_TALK + LEAVE_SCENE
            failExec: UNLOCK_AVATAR_TEAM + ROLLBACK 302204
   ↓
order=7  302206: COMPLETE_TALK + LEAVE_SCENE
            failExec: UNLOCK_AVATAR_TEAM + ROLLBACK 302204
   ↓
order=8  302207: FINISH_PLOT + LEAVE_SCENE   ← CG 播放
            finishExec: SET_QUEST_GLOBAL_VAR [3022, 1]   ← 叙事进入第一幕
            failExec: UNLOCK_AVATAR_TEAM + ROLLBACK 302204
   ↓
order=9  302233: ...                          ← 进入新场景
   ↓
... (一路到 order=49)
```

---

## 8. 设计经验提炼（vs 1001）

| 维度 | 1001 (测试) | 3022 (真实剧情) | 差异 |
|---|---|---|---|
| **状态机分支** | 用 finishExec rollback 模拟 | 用 questGlobalVar 做线性状态机 | 真剧情用变量，不用 state 比对 |
| **失败处理** | 没有 failExec | 18+ 个 SubQuest 用 failExec 实现 save-point | 商业项目要给玩家"逃生口" |
| **场景边界** | 没有 | LEAVE_SCENE 作为 failCond | 沉浸式剧情必须限制活动区域 |
| **角色控制** | 没有 | UNLOCK_AVATAR_TEAM 锁/解锁队伍 | 锁定团队是剧情演出的常见手段 |
| **导航** | 没有 guide | 几乎每步都有 guide 指针 | 玩家不该需要找 NPC |
| **subId/order** | 顺序一致 | order ≠ subId 顺序 | 真项目需要"身份/流程"分离 |
| **Talk 数** | 0 | 32 | 对话是叙事的载体 |

---

## 9. 给做剧情系统的建议

如果你做类似的电影化叙事系统：

1. **设立 Save Point 概念**：每个长序列的开头放一个"哨兵 SubQuest"，所有后续步骤的 failExec 都 rollback 到它。这比"每步重做"友好得多。

2. **场景边界 + 角色锁定是必备工具**：剧情段落开始时锁定，结束/失败时解锁。**`LEAVE_SCENE` 作为 failCond** 是简洁优雅的实现。

3. **用全局任务变量驱动叙事状态机**：不要让"剧情进度"散落在多个 SubQuest 状态查询里。一个 `questGlobalVar[mainId]` 从 0→N 演进，**简洁 + 易调试 + 易订阅**。

4. **subId 和 order 必须分离**：策划在迭代时会反复重排步骤。如果你把"流程位置"和"身份"绑死，重排成本会指数级增加。

5. **导航提示 (`guide`) 不是可选项**：每个新位置/新动作都要有指引。玩家迷路 = 体验崩塌。

6. **对话不止是文字，是 state-changing action**：Talk 可以触发 `TALK_EXEC_*` 改变 questVar——这是 RPG 选项分支的真正实现。

---

## 10. 后续可挖

3022 我们只看了表面：

- **`MFMFGILBDJB`（19 元素数组）** 是什么？可能是 cutscene 时间轴定义
- **`KMHJFCOCNNG`（顶层 object）** 含 3 个字段，可能是任务的元配置
- **完整的对话链** 30221017 → 30221018 → ... 形成什么样的树？
- **场景 Lua** `Actor/Quest/AQ3022` 里有什么——它可能是 `LUA_NOTIFY` 事件的发出方
- **完整的"幕"切分**：questGlobalVar[3022] 从 0→1→2→3→4→5→6，每个值对应哪段剧情？

这些都需要更深入的工具或代码分析才能解开。
