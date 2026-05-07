# ServerResearch — 原神任务系统逆向研究

对原神（Genshin Impact）服务端任务系统的设计分析与代码考古。基于公开的社区私服 [Grasscutter-Quests](https://github.com/Anime-Game-Servers/Grasscutter-Quests) 与社区数据挖掘成果（[Sycamore0/GenshinData](https://github.com/Sycamore0/GenshinData)），还原任务系统的运行机制、数据结构、调度策略，并提炼可复用的设计经验。

> **声明**：本仓库**仅含原创研究笔记**。所有引用的源码与游戏数据通过 git submodule 与 setup 脚本从上游拉取，不在本仓库内重新分发。版权归各原作者所有。

## 你能在这里看到什么

- 原神服务端的混合权威架构（client-side prediction vs server-side reconciliation）
- 任务系统的二级状态机（MainQuest / SubQuest）
- 触发器系统（QuestCond / QuestContent / QuestExec）
- 倒排索引调度（beginCondQuestMap）
- 每日委托如何寄生在主任务引擎上
- 每周任务为何"实际不存在"
- 真实主线任务 `MainQuest 1001` (小型测试) 的完整时序拆解
- 全量 2360 个任务（20,893 个 SubQuest）的使用分布统计与设计修正
- 真实剧情任务 `MainQuest 3022` (须弥章 Caribert) 的电影化叙事架构
- Talk 对话系统的"客户端权威"设计 + Lua 脚本与任务系统的双向桥接
- 真实对话分支：夜兰 LQ 11019 的 3 选项 + 汇合 talk 完整协议追踪
- 反混淆映射表：把 2360 个文件的版本特定混淆 key 全部翻译成可读字段名（89 个 key 已映射，覆盖 99.6%）
- TextMap 翻译：把 textHash 用 TextMapCHS 还原成中文标题和描述（21,938 个 hash 已翻译，含真实任务名核对）
- NPC + Dialog 翻译：5079 个 NPC ID 翻译成名字 + 203,908 条对话节点的实际台词浮现（performId → text 99.5% 命中率）
- 项目终章工具：Mermaid 流程图自动生成 + 完整剧情脚本重构（"识藏日"潜入对白、夜兰任务谈价场景从 JSON 还原）
- Scene Script 系统：30+ 种 EVENT 类型 + 100+ Lua API + Group/Suite 动态切换机制（任务系统的姐妹系统）
- Reward / 经济系统：物品 ID 命名空间 + 统一 Inventory.addItem 入口 + 100+ ActionReason 审计 + Mail 异步通道
- Combat / Ability 系统：混合权威模型的代码级分界 + AbilityManager（4 线程同构）+ 服务器算摔伤 + 反作弊 hooks
- Codex / 图鉴系统：寄生型设计反例（vs Quest 独立子系统）· 8 个 Set/Map + 6 个分散触发点

## 快速开始

```bash
# 1. 克隆本仓库（包含子模块）
git clone --recurse-submodules https://github.com/GORXE111/ServerResarch.git
cd ServerResarch

# 2. 拉取游戏数据（默认稀疏拉取，~308 MB；详见 SETUP.md）
bash setup.sh           # Linux / macOS / Git Bash
# 或：
pwsh setup.ps1          # Windows PowerShell
```

## 仓库结构

```
ServerResarch/
├── README.md                       本文件
├── SETUP.md                        详细环境搭建指南（含故障排查）
├── setup.sh / setup.ps1            一键拉取游戏数据脚本
├── notes/                          研究笔记（核心内容）
│   ├── 01-server-architecture.md       服务端整体架构 + 客户端/服务器职责划分
│   ├── 02-quest-system-design.md       任务系统数据结构与调度设计
│   ├── 03-runtime-flow.md              接取/执行/完成的运行时流程
│   ├── 04-daily-weekly-tasks.md        每日/每周任务实现机制
│   ├── 05-real-quest-walkthrough-1001.md  小型测试任务 1001 全流程拆解
│   ├── 06-corpus-analysis.md           全量 2360 个任务的统计分析与设计修正
│   ├── 07-real-quest-3022-caribert.md  须弥章魔神任务 3022 (Caribert) 深度拆解
│   ├── 08-talk-and-lua-bridge.md       Talk 对话系统 + Lua 脚本桥 双向架构
│   ├── 09-talk-exec-branching-example.md  夜兰 LQ 11019 对话分支选项实例
│   ├── 10-deobfuscation-table.md       反混淆映射表 · 89 个 key 全清晰化
│   ├── 11-textmap-translation.md       TextMap 翻译 · textHash → 中文文本 + 重大命名修正
│   ├── 12-npc-dialog-translation.md    NPC 名 + Dialog 表打通 · 真实台词浮现
│   ├── 13-visualization-and-dialog-tree.md  Mermaid 流程图 + 剧情脚本重构
│   ├── 14-scene-script-and-lua-engine.md    Scene Script 系统 · 任务系统的镜像兄弟
│   ├── 15-reward-and-economy-system.md      Reward / 经济系统 · 统一入口 + 100+ 审计
│   ├── 16-combat-and-ability-system.md      Combat / Ability 系统 · 混合权威的具体落地
│   └── 17-codex-archive-system.md           Codex 系统 · 寄生型图鉴的优雅实现
├── scripts/
│   ├── analyze_quests.py               全量任务语料分析脚本
│   ├── deobfuscate_keys.py             混淆 key → 真实字段名 反混淆器
│   ├── translate_text.py               textHash → 中文文本 + NPC 名 + Dialog 翻译器
│   ├── visualize_quest.py              SubQuest 状态转移 → Mermaid 流程图
│   └── dialog_tree.py                  对话链重构 + 剧情脚本生成
├── Grasscutter-Quests/             [submodule] 私服源码（用于代码考古）
└── GenshinData/                    [.gitignore] 由 setup 脚本拉取的游戏数据
```

## 阅读顺序建议

按编号顺序读 `notes/01` → `notes/05`，每篇可独立阅读。如果只想看一篇，**推荐 `notes/05` 真实任务拆解**——它把前四篇的概念全部贯穿在一个具体例子里。

## 致谢与上游

| 项目 | 用途 | 链接 |
|---|---|---|
| Anime-Game-Servers/Grasscutter-Quests | 任务系统的 Java 实现（私服） | https://github.com/Anime-Game-Servers/Grasscutter-Quests |
| Sycamore0/GenshinData | 客户端解包数据（任务/对话配表） | https://github.com/Sycamore0/GenshinData |
| KeqingMains TCL | 客户端/服务器职责划分实测 | https://library.keqingmains.com/combat-mechanics/damage/other/client-and-server |

## 法律声明

研究目的：理解大型在线游戏的服务端任务系统设计。本仓库不分发任何米哈游版权资产。引用的私服源码与解包数据存在 DMCA 风险，**请遵守你所在司法辖区的法律**。如果你来自 miHoYo / HoYoverse 法务部门并希望联系，请通过 GitHub 提 Issue。
