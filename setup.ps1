# 拉取 Genshin 任务/对话相关数据到 GenshinData/
# 用法: pwsh setup.ps1
# 或:   powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:GENSHINDATA_REPO) { $env:GENSHINDATA_REPO } else { "https://github.com/Sycamore0/GenshinData.git" }
$TargetDir = "GenshinData"

if (Test-Path "$TargetDir/.git") {
    Write-Host "[skip] $TargetDir already exists. To re-fetch, delete it first."
    exit 0
}

Write-Host "==> 稀疏 + 无 blob 克隆 GenshinData ($RepoUrl)"
git clone --depth=1 --filter=blob:none --sparse $RepoUrl $TargetDir
if ($LASTEXITCODE -ne 0) { throw "git clone failed" }

Write-Host "==> 配置 sparse-checkout（任务相关数据）"
$SparsePatterns = @(
    "/BinOutput/Quest/",
    "/BinOutput/Talk/",
    "/BinOutput/CodexQuest/",
    "/ExcelBinOutput/QuestExcelConfigData.json",
    "/ExcelBinOutput/MainQuestExcelConfigData.json",
    "/ExcelBinOutput/SubQuestExcelConfigData.json",
    "/ExcelBinOutput/TalkExcelConfigData.json",
    "/ExcelBinOutput/CodexQuestExcelConfigData.json",
    "/ExcelBinOutput/ChapterExcelConfigData.json",
    "/ExcelBinOutput/DailyTaskExcelConfigData.json",
    "/ExcelBinOutput/DailyTaskLevelExcelConfigData.json",
    "/ExcelBinOutput/DailyTaskRewardExcelConfigData.json",
    "/ExcelBinOutput/CityTaskOpenExcelConfigData.json",
    "/ExcelBinOutput/TriggerExcelConfigData.json",
    "/ExcelBinOutput/RewardExcelConfigData.json"
)
$SparsePatterns -join "`n" | Out-File -Encoding utf8 -NoNewline "$TargetDir/.git/info/sparse-checkout"

Write-Host "==> 物化文件"
Push-Location $TargetDir
try {
    git read-tree -mu HEAD
    if ($LASTEXITCODE -ne 0) { throw "git read-tree failed" }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "==> 完成。统计："
$qcnt = (Get-ChildItem "$TargetDir/BinOutput/Quest" -ErrorAction SilentlyContinue).Count
$tcnt = (Get-ChildItem "$TargetDir/BinOutput/Talk" -ErrorAction SilentlyContinue).Count
$ccnt = (Get-ChildItem "$TargetDir/BinOutput/CodexQuest" -ErrorAction SilentlyContinue).Count
$totalSize = "{0:N1} MB" -f ((Get-ChildItem $TargetDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB)
Write-Host "  Quest:      $qcnt 个文件"
Write-Host "  Talk:       $tcnt 个文件"
Write-Host "  CodexQuest: $ccnt 个文件"
Write-Host "  总大小:     $totalSize"
Write-Host ""
Write-Host "如需 TextMap 翻译，参考 SETUP.md 步骤 3。"
