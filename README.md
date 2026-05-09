# ServerResearch — 某动漫游戏服务端架构研究

对**某 XXX 动漫游戏**服务端架构（任务系统、对话系统、场景脚本、经济、战斗、图鉴、联机等）的设计分析与代码考古。基于公开的社区私服 [Grasscutter-Quests](https://github.com/Anime-Game-Servers/Grasscutter-Quests) 与社区数据挖掘成果，还原服务端各子系统的运行机制、数据结构、调度策略，并提炼可复用的设计经验。

> **声明**：本仓库**仅含原创研究笔记**。所有引用的源码与游戏数据通过 git submodule 与 setup 脚本从上游拉取，不在本仓库内重新分发。版权归各原作者所有。研究目的为学习大型在线游戏的服务端架构设计，不涉及任何商业用途。

## 你能在这里看到什么

- 服务端的混合权威架构（client-side prediction vs server-side reconciliation）
- 任务系统的二级状态机（MainQuest / SubQuest）
- 触发器系统（QuestCond / QuestContent / QuestExec）
- 倒排索引调度（beginCondQuestMap）
- 每日委托如何寄生在主任务引擎上
- 每周任务为何"实际不存在"
- 真实任务 `MainQuest 1001` (小型测试) 的完整时序拆解
- 全量 2360 个任务（20,893 个 SubQuest）的使用分布统计与设计修正
- 真实剧情任务 `MainQuest 3022` 的电影化叙事架构
- Talk 对话系统的"客户端权威"设计 + Lua 脚本与任务系统的双向桥接
- 真实对话分支：`MainQuest 11019` 的 3 选项 + 汇合 talk 完整协议追踪
- 反混淆映射表：把 2360 个文件的版本特定混淆 key 全部翻译成可读字段名（89 个 key 已映射，覆盖 99.6%）
- TextMap 翻译：textHash → 中文标题和描述（21,938 个 hash 已翻译，含真实任务名核对）
- NPC + Dialog 翻译：5079 个 NPC ID 翻译成名字 + 203,908 条对话节点的实际台词浮现（performId → text 99.5% 命中率）
- 项目终章工具：Mermaid 流程图自动生成 + 完整剧情脚本重构（任意主任务对白从 JSON 还原）
- Scene Script 系统：30+ 种 EVENT 类型 + 100+ Lua API + Group/Suite 动态切换机制（任务系统的姐妹系统）
- Reward / 经济系统：物品 ID 命名空间 + 统一 Inventory.addItem 入口 + 100+ ActionReason 审计 + Mail 异步通道
- Combat / Ability 系统：混合权威模型的代码级分界 + AbilityManager（4 线程同构）+ 服务器算摔伤 + 反作弊 hooks
- Codex / 图鉴系统：寄生型设计反例（vs Quest 独立子系统）· 8 个 Set/Map + 6 个分散触发点
- Multiplayer / Coop 系统：World/Scene/Player 三级容器 + 邀请协议 + Team 同步 + 视野广播 + 解散逻辑
- Dungeon / Challenge 系统：Quest+Scene+Combat+Multiplayer+Reward 五大系统的交汇点 · 14 种 Challenge 工厂 · Trial Avatar
- Activity / 限时活动系统：插件式架构 + WatcherTriggerType 跨系统事件总线（150+ 类型）+ 6 个活动子类型实现
- Gacha / 抽卡系统：商业核心的伪随机数学 · 4 层保底叠加（整体/UP/定轨/池平衡）· 线性插值软保底 · 完全服务器权威
- BattlePass / 战令系统：长期成长 · 三段状态机 · 双轨奖励 · 周积分上限 · 跨系统共享 WatcherTriggerType
- HomeWorld / 尘歌壶系统：UGC 范式 · 玩家自定义场景 · 客户端 3D 编辑 + 服务器存储 · 异步社交（离线访问）

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
│   ├── 07-real-quest-3022-caribert.md  剧情任务 3022 深度拆解（save-point 模式）
│   ├── 08-talk-and-lua-bridge.md       Talk 对话系统 + Lua 脚本桥 双向架构
│   ├── 09-talk-exec-branching-example.md  Talk 11019 对话分支选项实例
│   ├── 10-deobfuscation-table.md       反混淆映射表 · 89 个 key 全清晰化
│   ├── 11-textmap-translation.md       TextMap 翻译 · textHash → 中文文本 + 重大命名修正
│   ├── 12-npc-dialog-translation.md    NPC 名 + Dialog 表打通 · 真实台词浮现
│   ├── 13-visualization-and-dialog-tree.md  Mermaid 流程图 + 剧情脚本重构
│   ├── 14-scene-script-and-lua-engine.md    Scene Script 系统 · 任务系统的镜像兄弟
│   ├── 15-reward-and-economy-system.md      Reward / 经济系统 · 统一入口 + 100+ 审计
│   ├── 16-combat-and-ability-system.md      Combat / Ability 系统 · 混合权威的具体落地
│   ├── 17-codex-archive-system.md           Codex 系统 · 寄生型图鉴的优雅实现
│   ├── 18-multiplayer-coop-system.md        Multiplayer / Coop 系统 · 联机房间与跨账号同步
│   ├── 19-dungeon-and-challenge-system.md   Dungeon / Challenge 系统 · 五大子系统的交汇点
│   ├── 20-activity-system.md                Activity / 限时活动系统 · 插件式架构与"临时世界"
│   ├── 21-gacha-wish-system.md              Gacha / 抽卡系统 · 商业核心的伪随机数学
│   ├── 22-battlepass-system.md              BattlePass / 战令系统 · 长期成长的统计型框架
│   └── 23-homeworld-system.md               HomeWorld / 尘歌壶 · UGC 范式与玩家自定义场景
├── scripts/
│   ├── analyze_quests.py               全量任务语料分析脚本
│   ├── deobfuscate_keys.py             混淆 key → 真实字段名 反混淆器
│   ├── translate_text.py               textHash → 中文文本 + NPC 名 + Dialog 翻译器
│   ├── visualize_quest.py              SubQuest 状态转移 → Mermaid 流程图
│   └── dialog_tree.py                  对话链重构 + 剧情脚本生成
├── Grasscutter-Quests/             [submodule] 上游开源私服源码（用于代码考古）
└── GenshinData/                    [.gitignore] 由 setup 脚本从上游拉取的客户端解包数据
```

## 阅读顺序建议

按编号顺序读 `notes/01` → `notes/18`，每篇可独立阅读。如果只想看一篇，**推荐 `notes/13` 项目终章工具篇**——它演示如何用本仓库提供的工具，把混淆的 JSON 配表一路还原成可读流程图与剧情脚本。

主线推荐顺序：
1. `notes/01` — 整体架构（混合权威模型）
2. `notes/02-04` — 任务系统设计
3. `notes/14` — Scene Script 系统（任务系统的镜像兄弟）
4. `notes/15-18` — 经济、战斗、图鉴、联机四大子系统

## 致谢与上游

| 项目 | 用途 | 链接 |
|---|---|---|
| Anime-Game-Servers/Grasscutter-Quests | 任务系统的 Java 实现（开源社区私服） | https://github.com/Anime-Game-Servers/Grasscutter-Quests |
| Sycamore0/GenshinData | 客户端解包数据（社区维护） | https://github.com/Sycamore0/GenshinData |

## 法律声明

研究目的：理解大型在线游戏的服务端架构设计，提炼可复用的工程经验。

本仓库**不分发任何第三方版权资产**——所有引用的源码与解包数据都通过 git submodule 与 setup 脚本从公开上游拉取，仓库本身只含原创分析笔记。

如果你来自相关版权方法务部门并希望联系，请通过 GitHub 提 Issue。研究内容可应法律要求下架。
