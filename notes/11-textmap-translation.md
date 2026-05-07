# 11 · TextMap 翻译 · 把 textHash 还原成中文台词

把反混淆后的 quest JSON 里的 `textMapHash` 用 TextMap_CHS 翻译成实际中文。

> 工具：`scripts/translate_text.py`  
> 输入：`GenshinData/BinOutput/Quest_clean/*.json` (deobfuscated)  
> 输出：`GenshinData/BinOutput/Quest_translated/*.json` (deobfuscated + translated)  
> 字典：`GenshinData/TextMap/TextMapCHS.json` (379,178 个条目，30 MB)

---

## 1. TextMap 的格式与覆盖

```jsonc
{
    "1003108497": "新怪物",
    "1075609745": "冒险家罗尔德的日志",
    "1205856401": "找？哦…我正在找一个可疑的家伙。",
    "2046717777": "(test)绝对领域控制$HIDDEN",
    "2813062135": "识藏日",
    ...
}
```

→ 简单的 `hash 字符串 → 中文文本` 字典。每条线性查表 O(1)。

### Hash 长度分布（重要！）

```
key length 4:        1 条
key length 5:       10 条
key length 6:       83 条
key length 7:      735 条
key length 8:    7,846 条
key length 9:   79,787 条
key length 10: 290,716 条
```

**所有 key 都 ≤ 10 位**——即**只覆盖 32-bit hash**（最大 4,294,967,295 = 10 位）。

### 重大限制：64-bit hash 的对话台词缺失

我们在 Talk 节点和 Dialog 节点里看到的 hash 经常是 64 位，例如：

```
Talk 1101952 (夜兰分支选项):  talkTextMapHash = 12266219804591188210  (20 位)
Talk 1101953:                 talkTextMapHash = 15052571724083118385  (20 位)
```

→ **这些都不在本 fork 的 TextMap_CHS 里**。

**为什么？**
- 32-bit hash：用于**任务标题、描述、UI 字符串、提示**——固定的、显示用的文本
- 64-bit hash：用于**实际对话台词、声音线**——量大且新版本经常增删

这两类 hash 用了**不同的散列算法和不同的存储**。本 fork 的 TextMap_CHS 只是 UI 文本部分；完整对话文本可能在：
- 单独的 `TextMapCHSDialog.json`（如果存在）
- 或客户端 `.bytes` 包里
- 或被加密在 `Bundle/` 资源里

→ **能翻译标题/描述/UI 文本，但读不到具体对话台词**——这是当前数据集的限制。

---

## 2. 翻译效果

```
=== Summary ===
  files written:    2,360
  textHash 命中:    21,938 / 34,045 (64.4%)
  textHash 未命中:  12,107
```

64.4% 命中率全部来自 32-bit hash；剩下的 35.6% 几乎都是 64-bit 对话 hash。

---

## 3. 真实任务身份核对（重磅修正）

通过翻译，**我们之前对几个任务的命名/性质判断是错的**。

### 修正 1：MainQuest 3022 ≠ Caribert

```
误称: "Caribert (须弥章第六幕)"
实际: "识藏日" (须弥章第三章第五幕，对应 Akasha Pulses, the Kalpa Flame Rises)

descText: 终于到了「识藏日」这天，一切计划安排与一切的准备，
          都只为了一个目标——「拯救神明」。
```

→ 这是**从教令院夺回小吉祥草王（纳希妲）**的高潮章节，不是 Caribert。Caribert 是 Mondstadt 的传说任务延伸，使用不同的 mainQuestId。notes/07 已加入修正声明。

### 修正 2：MainQuest 1001 是测试任务，不是早期教学

```
titleText: (test)绝对领域控制$HIDDEN
descText:  绝对领域控制$HIDDEN
```

`(test)` 前缀和 `$HIDDEN` 后缀确认：**这是开发期的测试 stub**——不是真正的玩家任务。我们在 notes/05 里把它当真任务分析（包括"3 路分支"等推论），实际上**它的特殊结构很可能是测试用的边界场景**，不代表正常剧情设计模式。

→ notes/05 的"案例本身"分析依然技术上正确，但**不应推广到真实剧情设计的范例**。

### 修正 3：很多"低 ID 任务"是开发遗留

举例：
- `303`: "女神像解锁$HIDDEN"
- `348`: "猫尾酒馆留言板$HIDDEN"
- `40063`: "(test)隐藏任务用于切换五歌仙板子$UNRELEASED$HIDDEN"

**`$HIDDEN` / `$UNRELEASED` / `(test)` 都是后缀标记**——表示开发期内部任务。客户端可能会用这些标记决定是否展示。

---

## 4. 真实剧情任务名称表（精选）

经过翻译，现在能识别真正的剧情任务：

| ID | 真实标题 | 描述前 80 字 |
|---|---|---|
| **351** | **流浪者的足迹** (Wanderlust Invocation) | 神带走了你唯一的血亲，而你也被神封印，陷入沉眠。醒来后你先是独自流浪，后来又与奇妙的伙伴「派蒙」相遇... |
| **3001** | 疗养观察 | 虽说不得已开始了化城郭的疗养生活，但似乎可以向柯莱打听一些情报。 |
| **3016** | 如凯旋的英雄一般 | (须弥章中段) |
| **3017** | 来自某位「神明」的凝视 | |
| **3018** | 剑拔弩张四人众 | |
| **3019** | 失踪的守村人 | |
| **3020** | 魔鳞病医院的哭声 | (须弥沙漠章节) |
| **3021** | 热沙中的秘密 | |
| **3022** | **识藏日** | 终于到了「识藏日」这天，一切计划安排与一切的准备，都只为了一个目标——「拯救神明」。 |
| **3024** | 行于黎明前夜幕里 | |
| **3025** | 如临神之畔 | |
| **11019** | **知人知面** | 夜兰似乎对知易不太放心，决定到北码头找博来打听一下知易的风评。 |
| **11020** | 旧日之影 | 行至珠钿舫，你和派蒙偶然认出了一张熟悉的面孔… |
| **12039** | 穷途望归路 | 原来失踪事件的罪魁祸首就是控制了天目优也的妖刀。为了拯救天目优也... 枫原万叶最终亲自拿起了妖刀。|
| **79041** | 千奇澴回 | 遵循着派蒙的建议——「藏着摩拉的宝箱，一个都不能错过！」，你与派蒙打算再次造访伊迪娅... (活动任务，500 SubQuests！) |

→ **351 是真正的"教瓦特冒险开始"**——Grasscutter 代码里把它当 quest 系统启用的初始锚点（`getMainQuestById(351)`）就是这个原因。

---

## 5. Yelan LQ 11019 配合翻译再读一次

```jsonc
{
    "id": 11019,
    "titleText": "知人知面",
    "descText": "夜兰似乎对知易不太放心，决定到北码头找博来打听一下知易的风评。",
    ...
    "talks": [
        {
            "id": 1101952,
            "talkTextMapHash": 12266219804591188210,    // 64-bit, 不在 TextMap
            "talkText": (无翻译),
            "finishExec": [
                { "type": "TALK_EXEC_SET_QUEST_VAR", "param": ["3", "1", "11019"] }
            ]
        }
    ]
}
```

→ 我们能确认这是夜兰任务"知人知面"的某一段对话，**但具体台词文字读不出来**（64-bit hash）。要看真实台词需要更完整的 TextMap，或直接打开客户端跑一遍。

---

## 6. 工具用法

### 翻译全部

```bash
python scripts/translate_text.py
```

输出：

```
[+] loading TextMap from .../TextMapCHS.json...
[+] loaded 379,178 text entries

[+] translating 2360 files...
=== Summary ===
  files written:    2360
  textHash 命中:    21,938 / 34,045 (64.4%)
```

输出位于 `GenshinData/BinOutput/Quest_translated/<id>.json`，约 35 MB（gitignored）。

### 只翻译指定任务

```bash
python scripts/translate_text.py --sample 351 3022 11019
```

### 输出格式

每个 `*TextMapHash` 字段旁边新增一个文本字段：

```jsonc
{
    "titleTextMapHash": 2046717777,    // 原始 hash 保留
    "titleText": "(test)绝对领域控制$HIDDEN",   // 新增的中文翻译
    "descTextMapHash": 2302617031,
    "descText": "绝对领域控制$HIDDEN"
}
```

→ **保留 hash + 添加文本**，向前兼容（旧脚本依然能用 hash），向后增强（新脚本能直接读文本）。

---

## 7. 反混淆 + 翻译 后的最终管线

```
原始 BinOutput/Quest/*.json  (混淆 + hash)
        ↓
[scripts/deobfuscate_keys.py]   89 个 key 映射
        ↓
BinOutput/Quest_clean/*.json  (字段名清晰，hash 还在)
        ↓
[scripts/translate_text.py]   TextMap 查表
        ↓
BinOutput/Quest_translated/*.json  (字段名清晰 + 文本可读)
```

整个链路**纯解析、零密钥、可重现**——给定上游数据和工具，任何人都能得到一致的清晰任务配表。

---

## 8. 后续可拓展

1. **拉多语言 TextMap** — 改 setup.sh 增加 `/TextMap/TextMapEN.json`，可同时输出英文翻译
2. **找完整 64-bit hash 对话表** — 这是当前最大缺口；需要从其他 fork 或客户端 dump
3. **配 ChapterExcelConfigData** — 把 quest 按章节聚合做"全章故事概览"
4. **配 NpcExcelConfigData** — 把 npcId（1056=Dainsleif、12403=夜兰）翻译成 NPC 名字

---

## 参考代码位

- 翻译脚本：`scripts/translate_text.py`
- TextMap 数据：`GenshinData/TextMap/TextMapCHS.json` (30 MB, 379,178 条)
- 完整管线：`scripts/deobfuscate_keys.py` → `scripts/translate_text.py`
- 输出：`GenshinData/BinOutput/Quest_translated/*.json` (gitignored)
