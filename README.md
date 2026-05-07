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
- 真实主线任务 `MainQuest 1001` 的完整时序拆解
- 全量 2360 个任务（20,893 个 SubQuest）的使用分布统计与设计修正

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
│   ├── 05-real-quest-walkthrough-1001.md  真实主线任务全流程拆解
│   └── 06-corpus-analysis.md           全量 2360 个任务的统计分析与设计修正
├── scripts/
│   └── analyze_quests.py               全量任务语料分析脚本
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
