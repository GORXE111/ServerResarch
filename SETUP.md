# 环境搭建指南

完整步骤，含故障排查。

## 前置依赖

| 工具 | 用途 | 推荐版本 |
|---|---|---|
| `git` | 克隆仓库 + 子模块 | ≥ 2.30（需要 sparse-checkout cone-mode 之外的支持）|
| `bash` 或 `pwsh` | 运行 setup 脚本 | 任意 |

无需 Java / Maven 等运行时——本仓库仅做**代码阅读和分析**，不需要运行 Grasscutter。

## 步骤 1：克隆主仓库（含 submodule）

```bash
git clone --recurse-submodules https://github.com/GORXE111/ServerResarch.git
cd ServerResarch
```

如果你已经 clone 过但忘了带 `--recurse-submodules`：

```bash
git submodule update --init --recursive
```

完成后，`Grasscutter-Quests/` 目录会有完整源码（约 20 MB，shallow=1）。

## 步骤 2：拉取游戏数据（可选，但推荐）

游戏数据 `GenshinData/` **不进版本控制**，由 setup 脚本从上游 [Sycamore0/GenshinData](https://github.com/Sycamore0/GenshinData) 用稀疏 + 无 blob 模式拉取——只下你需要的任务/对话相关文件，约 **308 MB**。

### Linux / macOS / Git Bash

```bash
bash setup.sh
```

### Windows PowerShell

```powershell
pwsh setup.ps1
# 或老版 PowerShell：
powershell -ExecutionPolicy Bypass -File setup.ps1
```

完成后 `GenshinData/` 会包含：

| 路径 | 文件数 | 大小 | 用途 |
|---|---|---|---|
| `BinOutput/Quest/` | 2360 | 37 MB | 所有 MainQuest 配置 |
| `BinOutput/Talk/` | 27 | 168 MB | 大型对话脚本 |
| `BinOutput/CodexQuest/` | 274 | 30 MB | 任务图鉴元数据 |
| `ExcelBinOutput/*.json` | 10 | 49 MB | 扁平化的核心配表 |

## 步骤 3（可选）：拉 TextMap 翻译

如果你想把任务里的 `titleTextMapHash`、`descTextMapHash` 还原成中文/英文标题，需要额外下载 TextMap：

```bash
cd GenshinData
echo "/TextMap/TextMapCHS.json" >> .git/info/sparse-checkout
git read-tree -mu HEAD
```

`TextMapCHS.json` 约 110 MB。其他语言：`TextMapEN.json` / `TextMapJP.json` / etc.

## 步骤 4：开始阅读

主入口在 `notes/`，建议按编号顺序读，或直接跳到 `notes/05` 看真实任务拆解。

代码引用统一使用 `相对路径:行号` 格式，例如：

```
Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/GameQuest.java:187
```

可以直接在 VS Code / IntelliJ 里 Ctrl+点击跳转。

---

## 故障排查

### `git submodule update` 报 `Could not access submodule`

可能是上游 `Anime-Game-Servers/Grasscutter-Quests` 被 DMCA 暂时下架。临时方案：

```bash
# 找一个 fork 替代
git config -f .gitmodules submodule.Grasscutter-Quests.url <fork-url>
git submodule sync
git submodule update --init
```

### Windows 上 `setup.sh` 路径异常

如果用 Git Bash 运行 `setup.sh`，遇到形如 `C:/Program Files/Git/BinOutput/...` 的路径，那是 MSYS2 的 POSIX 路径自动转换坑。两个解法：

```bash
# 方案 A：在脚本前加环境变量
MSYS_NO_PATHCONV=1 bash setup.sh

# 方案 B：用 PowerShell 跑
pwsh setup.ps1
```

### 拉取速度慢

GitHub 国内访问不稳定。可以走代理：

```bash
git config --global http.https://github.com.proxy http://127.0.0.1:7890
```

或者用 GitHub 的镜像（按需）：

```bash
git clone https://hub.fastgit.xyz/GORXE111/ServerResarch.git
```

### sparse-checkout 没生效

直接编辑 `GenshinData/.git/info/sparse-checkout` 文件，每行一条路径（前导 `/` 表示从仓库根开始），然后：

```bash
cd GenshinData
git read-tree -mu HEAD
```

### 想拉所有 GenshinData（不稀疏）

清空 sparse 配置：

```bash
cd GenshinData
git sparse-checkout disable
git checkout main
```

警告：完整仓库 ~2 GB（含 TextMap 多语言 + AvatarExcel 等），慎用。

---

## 相关资源

- 私服项目主线：[Grasscutters/Grasscutter](https://github.com/Grasscutters/Grasscutter)（任务系统不如 Quests 分支完整）
- 数据挖掘工具链：[Perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper)、[djkaty/Il2CppInspector](https://github.com/djkaty/Il2CppInspector)
- 历史快照（DMCA 后备份）：[Internet Archive](https://archive.org/details/github.com-Dimbreath-GenshinData_-_2022-05-27_04-46-52)
