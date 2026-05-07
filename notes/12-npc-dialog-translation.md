# 12 · NPC 翻译 + Dialog 表打通 · 真实台词浮现

补完最后一块拼图——把 npcId 翻成名字，把 Talk 的 performId 解析成实际对话台词。

> 工具：`scripts/translate_text.py` (升级版)  
> 数据源：
> - `ExcelBinOutput/NpcExcelConfigData.json` (2.9 MB, 5,079 NPC)
> - `ExcelBinOutput/DialogExcelConfigData.json` (93 MB, **203,908 条对话节点**)

---

## 1. 数据架构（终于看清整个对话系统）

```
┌─────────────────────────────────────────────────────────────────┐
│ MainQuest (BinOutput/Quest/<id>.json)                           │
│   └── talks[]                                                    │
│         ├── id (Talk id)                                         │
│         ├── npcId  (玩家点击的 NPC，触发器)                       │
│         ├── beginCond                                            │
│         ├── performId  ───────┐  ← 链接到 Dialog 表              │
│         └── finishExec        │                                  │
└────────────────────────────────┼─────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│ Dialog 表 (ExcelBinOutput/DialogExcelConfigData.json)           │
│   每条:                                                          │
│     id (= performId)                                             │
│     talkRole: { type, id }     ← 谁说话 (NPC id)                 │
│     talkContentTextMapHash      ← 32-bit, 在 TextMap 里!          │
│     talkRoleNameTextMapHash    ← 角色名 hash                     │
│     nextDialogs[]               ← 下一句对话 id                  │
│     talkAssetPath / talkAudioName  ← 客户端资源                  │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│ TextMap_CHS.json                                                 │
│   talkContentTextMapHash (32-bit) → 中文文本                     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ NPC 表 (ExcelBinOutput/NpcExcelConfigData.json)                 │
│   每条:                                                          │
│     id (NPC id)                                                  │
│     nameTextMapHash → TextMap → "夜兰" / "派蒙" / "艾尔海森"     │
└─────────────────────────────────────────────────────────────────┘
```

### 关键洞察

之前以为 64-bit `talkTextMapHash` 应该是对话文本——**错的**。那个字段是 Talk 的某种内部标识。**真正的对话内容在 Dialog 表里**，且用 32-bit hash，TextMap 里有。

`performId` 是 Talk 跟 Dialog 表的**唯一连接键**。Talk 里的所有"开始对话"指针都通过 performId 走向 Dialog 表。

---

## 2. 翻译战果（全量）

```
=== Summary ===
  files written:        2,360
  textHash 命中:        21,938/34,045 (64.4%)
  npc 翻译:             15,253 处
  performId → dialog:   11,961/12,018 (99.5%) ★
```

99.5% 的 `performId` 都能在 Dialog 表里找到对应——基本完美打通。剩下 0.5% 可能是新版本添加的对话或活动期的内容。

---

## 3. 真实台词浮现：3 个案例

### 案例 A：夜兰任务"知人知面" — 真相揭晓

之前我们以为这是 3 选 1 的对话选项（玩家选择如何回复夜兰）。**翻译后才发现真相**：

```
任务标题: 知人知面
描述: 夜兰似乎对知易不太放心，决定到北码头找博来打听一下知易的风评。

Talk 1101952  npcId=12403=知易的规划书
   performText: [?] 看「海」篇。            → 设 var[3]=1

Talk 1101953  npcId=12403=知易的规划书
   performText: [?] 看「岩」篇。            → 设 var[0]=1

Talk 1101954  npcId=12403=知易的规划书
   performText: [?] 看「路」篇。            → 设 var[4]=1

Talk 1101955  npcId=12403=知易的规划书
   performText: [?] 不看了。
```

**原来不是夜兰对话选项**——是玩家**翻阅"知易的规划书"（一本笔记），选择读哪一章**。三章读完后选"不看了"汇合。

→ **配合 NPC 翻译，叙事意图终于清晰**：玩家翻读笔记的三个章节 = 收集证据。这是侦探题材任务的典型机制。

> ⚠️ **修正 notes/09**：之前称 "NPC 12403 = 夜兰" 是错的。**夜兰真实 NPC id = 1048**。`12403 = 知易的规划书`（书本/物品类 NPC）。这不影响 notes/09 的机制分析（位向量分支系统），但具体场景的描述不准确。

### 案例 B：3022 "识藏日" — 真实潜入对话

```
任务标题: 识藏日
描述: 终于到了「识藏日」这天，一切计划安排与一切的准备，
      都只为了一个目标——「拯救神明」。

Talk 302202  npc=1053=艾尔海森
   [艾尔海森] "你们来了，休息得怎么样？"

Talk 302203  npc=12808=维拉夫
   [维拉夫] "…书、书记官？"

Talk 302205  npc=1053=艾尔海森
   [派蒙] "这里就是教令院的图书馆吧？"

Talk 302206  npc=12837=调查
   [派蒙] "前面这个平台…是做什么的呀？"

Talk 302208  npc=12837=调查
   [派蒙] "呜哇——不要把我们关起来啊，放我们出去！"
```

→ 这是**冒充艾尔海森的书记官身份潜入教令院图书馆**的剧情。维拉夫被骗、派蒙好奇问平台、被卫兵关起来——完美对应须弥章 Act V 的剧情节点。

**NPC 翻译揭示了核心人物**：艾尔海森（Alhaitham）是合作 NPC，维拉夫（Wirav）是教令院学者，「调查」是机关交互对象。

### 案例 C：351 "流浪者的足迹" — Genshin 开场叙事

```
任务标题: 流浪者的足迹
描述: 神带走了你唯一的血亲，而你也被神封印，陷入沉眠。
     醒来后你先是独自流浪，后来又与奇妙的伙伴「派蒙」相遇，
     开启了提瓦特大陆的探索之旅…
```

→ 这就是新玩家进入游戏看到的**官方序章简介**。Grasscutter 代码里 `getMainQuestById(351)` 作为锚点，正是为此。

---

## 4. 个别 NPC 名核实

```
NPC id 1048   →  夜兰         (Yelan)
NPC id 1056   →  纳西妲       (Nahida) ← 我之前注释错了，1056 不是 Dainsleif
NPC id 1064   →  卡维         (Kaveh)
NPC id 1005   →  派蒙         (Paimon)
NPC id 1053   →  艾尔海森     (Alhaitham)
NPC id 12403  →  知易的规划书   (knowledge note item，不是夜兰！)
NPC id 12808  →  维拉夫       (Wirav)
NPC id 12837  →  调查         (interactive object placeholder)
```

观察：
- **角色 NPC** 用 4 位 id（1xxx）
- **可交互物品/标记** 用 5 位 id（12xxx, 13xxx 等）
- 「调查」、「书」、「箱」这种名字往往是机关交互的占位 NPC

---

## 5. 工具升级要点

`translate_text.py` 现在做 4 件事：

```python
# 1. 任意 *TextMapHash 字段 → 添加 sibling 文本
"titleTextMapHash": 2046717777,
"titleText": "(test)绝对领域控制$HIDDEN"   ← 新增

# 2. npcId 列表 → 添加 npcName 列表
"npcId": [1056],
"npcName": ["纳西妲"]                      ← 新增

# 3. talkRole._id → 添加 talkRoleName
"talkRole": { "_id": "1048", "_type": "TALK_ROLE_NPC" },
"talkRoleName": "夜兰"                     ← 新增

# 4. performId → 添加 performText (首句对话)
"performId": 110195101,
"performText": "[夜兰] 尽量不要让他发现，这样收集到的情报才更真实。"   ← 新增
"performNextDialogs": [110195102, ...]      ← 后续对话节点 id 链
```

输出依然向前兼容（保留所有原始字段）+ 向后增强（添加可读字段）。

---

## 6. 完整管线（最终版）

```
GenshinData/BinOutput/Quest/*.json (混淆 + hash)
        ↓ scripts/deobfuscate_keys.py        89 个 key 反混淆
GenshinData/BinOutput/Quest_clean/*.json (字段名清晰)
        ↓ scripts/translate_text.py          + TextMap + Npc + Dialog
GenshinData/BinOutput/Quest_translated/*.json (完全可读)
```

任何研究者只需要 3 个工具 + 公开数据，**不需要游戏二进制 / IL2CPP / 客户端 dump**，就能从原始混淆 JSON 一路走到完全翻译的可读剧情结构。

---

## 7. 后续真正剩下的缺口

仍然读不到的内容：

1. **客户端 Lua 表演脚本**（`QuestDialogue/AQ/Sumeru3_3022/Q302207.lua`）—— 镜头、表情、特效、场景切换
2. **64-bit `talkTextMapHash`** —— 真正用途未明，疑似客户端某种 ID
3. **`talkAssetPath` 内容** —— 对话 timeline 资源
4. **音频文件** —— 配音

这些都需要客户端 dump（涉及游戏二进制资源），不是数据层面能解决的。

但**对话文本本身、人物身份、剧情结构、任务流程**——这套管线已经全部打通。

---

## 参考代码位

- 翻译脚本：`scripts/translate_text.py`（升级版）
- NPC 数据：`GenshinData/ExcelBinOutput/NpcExcelConfigData.json` (5079 个)
- Dialog 数据：`GenshinData/ExcelBinOutput/DialogExcelConfigData.json` (203,908 个)
- 输出：`GenshinData/BinOutput/Quest_translated/*.json`
