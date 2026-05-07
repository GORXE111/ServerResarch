#!/usr/bin/env bash
# 拉取 Genshin 任务/对话相关数据到 GenshinData/，使用稀疏 + 无 blob 克隆
# 用法: bash setup.sh

set -euo pipefail

REPO_URL="${GENSHINDATA_REPO:-https://github.com/Sycamore0/GenshinData.git}"
TARGET_DIR="GenshinData"

if [[ -d "$TARGET_DIR/.git" ]]; then
    echo "[skip] $TARGET_DIR already exists. To re-fetch, delete it first."
    exit 0
fi

echo "==> 稀疏 + 无 blob 克隆 GenshinData ($REPO_URL)"
git clone --depth=1 --filter=blob:none --sparse "$REPO_URL" "$TARGET_DIR"

echo "==> 配置 sparse-checkout（任务相关数据）"
# 直接写文件，避免 Git Bash on Windows 的 MSYS 路径转换坑
cat > "$TARGET_DIR/.git/info/sparse-checkout" <<'EOF'
/BinOutput/Quest/
/BinOutput/Talk/
/BinOutput/CodexQuest/
/ExcelBinOutput/QuestExcelConfigData.json
/ExcelBinOutput/MainQuestExcelConfigData.json
/ExcelBinOutput/SubQuestExcelConfigData.json
/ExcelBinOutput/TalkExcelConfigData.json
/ExcelBinOutput/CodexQuestExcelConfigData.json
/ExcelBinOutput/ChapterExcelConfigData.json
/ExcelBinOutput/DailyTaskExcelConfigData.json
/ExcelBinOutput/DailyTaskLevelExcelConfigData.json
/ExcelBinOutput/DailyTaskRewardExcelConfigData.json
/ExcelBinOutput/CityTaskOpenExcelConfigData.json
/ExcelBinOutput/TriggerExcelConfigData.json
/ExcelBinOutput/RewardExcelConfigData.json
EOF

echo "==> 物化文件"
( cd "$TARGET_DIR" && git read-tree -mu HEAD )

echo ""
echo "==> 完成。统计："
echo "  Quest:      $(ls "$TARGET_DIR/BinOutput/Quest" 2>/dev/null | wc -l) 个文件"
echo "  Talk:       $(ls "$TARGET_DIR/BinOutput/Talk" 2>/dev/null | wc -l) 个文件"
echo "  CodexQuest: $(ls "$TARGET_DIR/BinOutput/CodexQuest" 2>/dev/null | wc -l) 个文件"
echo "  总大小:     $(du -sh "$TARGET_DIR" | cut -f1)"
echo ""
echo "如需 TextMap 翻译，参考 SETUP.md 步骤 3。"
