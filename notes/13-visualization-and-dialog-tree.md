# 13 · Quest 可视化 + 对话树重构 · 项目终章

两个工具把 12 篇笔记的成果做成"看得见、读得到"的最终产物：

> 🛠 工具：
> - `scripts/visualize_quest.py` — Mermaid 流程图生成器
> - `scripts/dialog_tree.py` — 对话链 / 完整剧本重构器

---

## Part A · Quest 可视化（Mermaid 流程图）

### 用法

```bash
python scripts/visualize_quest.py 1001 11019 3022 351
# → output/diagrams/<id>.md
```

每个文件含一段 Mermaid 代码，**直接在 GitHub / VS Code 渲染成流程图**。

### 效果一：3022 "识藏日"（49 SubQuest, 22 个 rollback edge）

我们之前在 notes/07 推论的 "save-point + replay" 模式，**自动从配表抽出来变成可视化**：

```mermaid
flowchart TD
  q302201["302201<br/>order=1<br/>等到第二天8点-12点<br/>FC:GAME_TIME_TICK"]
  q302202["302202<br/>order=2<br/>前往教令院外与艾尔海森汇合<br/>FC:COMPLETE_TALK"]
  q302203["302203<br/>order=3<br/>与艾尔海森一起进入教令院<br/>FC:COMPLETE_TALK"]
  q302204(["302204<br/>order=4<br/>进入智慧宫<br/>FC:LUA_NOTIFY"]):::savepoint
  q302205["302205<br/>order=6<br/>与艾尔海森对话<br/>FC:COMPLETE_TALK"]
  q302241["302241<br/>order=49<br/>离开净善宫<br/>FC:ENTER_MY_WORLD"]:::finishparent
  ...
  q302205 -.->|fail→rollback| q302204
  q302206 -.->|fail→rollback| q302204
  q302207 -.->|fail→rollback| q302204
  ...（共 17 个 SubQuest 失败时全部回滚到 302204）

  classDef savepoint fill:#ffe6e6,stroke:#a00,stroke-width:2px
  classDef finishparent fill:#e6ffe6,stroke:#080,stroke-width:3px
```

**302204 = "进入智慧宫"** 自动识别为 **Save Point**（圆角节点），完美对应 notes/07 的人工分析。

### 节点形状语义

| 形状 | 含义 |
|---|---|
| `[矩形]` | 普通 SubQuest |
| `(圆角)` Save Point | 被多次 rollback 引用（≥2 次） |
| `**绿色加粗**` | `finishParent=true`（完成它就关闭整个 MainQuest） |

### 边类型

| 边 | 语义 |
|---|---|
| 粗实线 `==>` | acceptCond 状态依赖（A 完成/失败 → B 接取） |
| 细实线 `-->` | 默认顺序流（按 order 推进） |
| 虚线 `-.->` | finishExec / failExec 副作用（rollback / addProgress） |

### 检测逻辑

```python
# 自动识别 SubQuest 关系：
1. acceptCond.QUEST_COND_STATE_EQUAL[X, 3]  → A FINISHED → B accepts
2. acceptCond.QUEST_COND_STATE_EQUAL[X, 4]  → A FAILED   → B accepts
3. failExec.ROLLBACK_QUEST[X]               → A 失败时回滚到 X
4. finishExec.ADD_QUEST_PROGRESS[X, count]  → A 完成时推进 X
5. 顺序回退：相邻 order 间没有显式 cond → 加 fallback 顺序边
6. Save-point 检测：被 rollback 引用 ≥ 2 次 = save-point
```

---

## Part B · 对话树重构

### 三种使用方式

```bash
# 1. 给一个 dialog id, 沿 nextDialogs 展开整条对话链
python scripts/dialog_tree.py --dialog 110195101

# 2. 给一个 MainQuest id, 自动展开它所有 Talk 的对话
python scripts/dialog_tree.py --quest 11019
# → output/dialogs/quest_11019.md

# 3. 搜索包含关键字的对话节点
python scripts/dialog_tree.py --search "纳西妲"
```

### 效果一：完整还原 Yelan 任务"知人知面"剧情

**Talk 1101902 — 与博来对话**（任务起手戏）：

```
[博来]   不是我说，你们这批货要价也太高了。进货价都贵成这样，我还怎么往外卖？
  [杞平] 老板，话不能这么说。这批日落果，我认第二，没人敢认第一。
    [杞平] 当初我不小心掉到井里一枚，整个井的水都变甜了！
      [博来] 就算这样…
        [杞平] 嘘，老板，再告诉你个秘密。「荣发商铺」的老板也想从我这进货，我没卖给他。
          [杞平] 您要是不收，我只能勉为其难跟他们合作了。
            [博来] 好吧，既然如此，那价钱就按你说的来。有多少我要多少，一枚也不能留给「荣发商铺」！
              ... (与博来谈价的商人离开了…)
                [博来] 嗯？你们怎么来了？
                  ...
                    [派蒙] 其实，我们是想向你打听一下知易。你听说过这个人吗？
                      [博来] 知易？那家伙的名声可挺响的。
                        [博来] 我听过一个关于他的故事。据说他出身贫寒，父母早亡...
                          [博来] 他曾经的邻居就经常对他恶语相向，然而知易并没有因此报复...
                            [派蒙] 听上去是个好人呢！
                              [博来] 你们打听这个做什么？
                                [博来] 好吧，既然如此，那咱们价高者先...
                                  [派蒙] 哪里哪里？
                                    [夜兰] #确实是知易，看上去正在和琳琅聊天…走，{NICKNAME}，我们跟上去听听。
```

→ 这就是**完整的真实游戏对白**，从纯混淆 JSON 还原出来。注意 `{NICKNAME}` 是玩家角色名占位符，运行时由客户端替换。

### 效果二：识藏日的 Akademiya 潜入剧情

**Talk 302202 — 任务开场**：

```
[艾尔海森] 你们来了，休息得怎么样？
  [派蒙] 我睡得不太好…想起今天要做的事情就紧张，快天亮了才睡着…
    [派蒙] 你呢，艾尔海森？
      [艾尔海森] 前夜的休息也可以视为计划的一部分，精力也是可利用的重要资源，我当然好好休息过了。
        [派蒙] 你、你只是想炫耀自己很冷静吧！
          [艾尔海森] 在正式执行计划之前缓解紧张气氛也是很重要的一环。
            [派蒙] 你只是在惹人生气，哪里有缓解紧张气氛啦！
              [艾尔海森] 好了，关于我们一会儿要做的事，还需要我重复一遍么？
                [玩家选项] 以防万一还是讲一下吧。
                  [艾尔海森] 我们的目标是大贤者阿扎尔的办公室...
                    [艾尔海森] 许多机密指令和操作都是通过那个操作台来完成的...
                      [派蒙] 说起来，我一直想问，贤者他们究竟是用了什么样的技术，才能将神明囚禁的呀？
                        [艾尔海森] 光靠那些学者的水平自然不行。
                                   净善宫中其实原本就有大慈树王为了独自冥想而隔绝一切外物的装置…
                          [艾尔海森] 而五百年前的大贤者将那个装置改造，使其无法再从内部操控，
                                     也就相当于用神明的技术囚禁了神明。
```

→ **完整的剧情台词**，包括：
- 艾尔海森冷静吐槽派蒙
- 揭示 5 百年前大贤者囚禁神明的技术细节
- 玩家选项分支（"以防万一"/"我想再确认一遍"）

### 效果三：搜索功能

```bash
$ python scripts/dialog_tree.py --search "纳西妲"

- dialog 30090335: [纳西妲]  对了，我叫纳西妲哦。
- dialog 30090345: [派蒙]    纳西妲还是很喜欢用这种奇奇怪怪的比喻呢。
- dialog 30090923: [迪希雅]  那个…你们说的那位纳西妲…她说了什么？
- dialog 30100110: [派蒙]    把我们两个比作小白鼠吗，那你又是什么呢，纳西妲...
- dialog 30120101: [派蒙]    早上好，纳西妲！我全想起来了，
                           是不是该说「早上好，小吉祥草王」了呢？
...
```

→ 即时全文搜索，用来找特定角色出现的所有对话或追踪某段剧情。

---

## 整体管线一图全貌

```
                    上游数据 (Sycamore0/GenshinData)
                              │
            ┌─────────────────┼──────────────────┐
            │                 │                   │
   /BinOutput/Quest/    /TextMap/CHS.json    /ExcelBinOutput/
   2360 个混淆 JSON     379K 32-bit hashes    NpcExcel + DialogExcel
            │                 │                   │
            ↓                 │                   │
   [deobfuscate_keys.py]      │                   │
   89 个 key 反混淆           │                   │
            │                 │                   │
            ↓                 │                   │
  /Quest_clean/*.json   ← ────┴────[translate_text.py]
  字段名清晰                  textHash + npcId + performId 三层翻译
            │                                     │
            ↓                                     ↓
  /Quest_translated/*.json
  完全可读
            │
   ┌────────┴─────────┐
   ↓                  ↓
[visualize_quest]  [dialog_tree]
SubQuest 流程图    剧情对白还原
   ↓                  ↓
output/diagrams/   output/dialogs/
*.md (Mermaid)     *.md (脚本)
```

**3 层数据 + 4 个工具 + 2360 个文件**，从纯混淆 JSON 一路走到可读剧情脚本与流程图——**全程纯解析、零密钥、不需要游戏二进制**。

---

## 局限性

### Quest 可视化
- ✅ 顺序流、状态依赖、rollback、save-point 全部捕获
- ⚠️ acceptCond 中的 `QUEST_VAR_EQUAL` 边没渲染（变量来源散在多处，复杂；目前简化为不画）
- ⚠️ Talk-driven SubQuest 转移（COMPLETE_TALK → finish）没显式画出，需要看 SubQuest 节点的 FC 类型理解

### 对话树重构
- ✅ DialogExcel 内 nextDialogs 链完整跟随
- ✅ NPC 名字、speaker、文本全翻译
- ⚠️ **客户端 Lua 控制的对话流不在配表里**：`performCfg` 路径指向客户端 Lua 文件（如 `QuestDialogue/AQ/Sumeru3_3022/Q302207.lua`），里面有动画、镜头、特效——我们看不到
- ⚠️ 一些大型对话包（BinOutput/Talk/）的内部对话节点可能有 64-bit textHash 不在 TextMap 里

但**关键内容（标题、描述、对白、人物身份、流程结构）已经全部还原**。

---

## 这套工具能帮你回答哪些问题

```
Q: 须弥章第三幕的剧情是什么？
   → python scripts/dialog_tree.py --quest 3022

Q: 这个任务的状态机长什么样？
   → python scripts/visualize_quest.py 3022

Q: 派蒙说过哪些话？
   → python scripts/dialog_tree.py --search "派蒙" | grep "派蒙"

Q: 哪些任务用 save-point 模式？
   → 看可视化里有圆角节点的图

Q: 任务系统接 NPC 对话怎么和场景脚本协作？
   → 翻 notes/03, /08, /13 三篇笔记串起来读
```

---

## 参考代码位

- 可视化：`scripts/visualize_quest.py`
- 对话树：`scripts/dialog_tree.py`
- 输出样例：`output/diagrams/<id>.md` & `output/dialogs/quest_<id>.md`（gitignored，每次 setup 后本地生成）

---

## 项目终章感言

12 篇笔记 + 4 个 Python 脚本 + 308 MB 公开数据，把原神任务系统从底层数据格式到顶层叙事呈现都拆解完整。**它真的是行业标杆**——不仅是因为内容量，更因为它的：

1. **数据驱动到极致**：核心代码 < 2500 行，复杂度全在配表里
2. **触发器 + 倒排索引 + 异步事件总线** 的架构在 Quest/Lua/Talk 三层重复出现
3. **失败处理（save-point/rollback）哲学**：reset 而非 punish
4. **客户端权威 + 服务器仲裁**的混合模型
5. **subId/order 分离**让策划可迭代不破坏配表
6. **同一种"位向量 + AND/OR"模式**统一表达单选/多选/汇合

**这些设计原则可复用到任何大型在线 RPG**——不局限于做"原神 like"。
