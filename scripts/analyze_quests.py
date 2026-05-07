"""
扫描 GenshinData/BinOutput/Quest/*.json 全量 MainQuest 文件，聚合任务系统的真实使用分布。

由于上游数据源（Sycamore0/GenshinData）的字段名是混淆过的（每个版本 key 名不同），
本脚本采用**递归全树扫描 + 字符串模式匹配**，不依赖具体 key 名。

识别规则：
- 字符串以 "QUEST_COND_*" 开头  → QuestCond 类型（多用于 Talk 条件）
- 字符串以 "QUEST_CONTENT_*" 开头  → QuestContent 类型（finish/fail Cond）
- 字符串以 "QUEST_EXEC_*" 开头  → QuestExec 类型（begin/finish/fail Exec）
- 字符串以 "LOGIC_*" 开头  → LogicType
- 字符串以 "QUEST_HIDDEN" / "QUEST_DEFAULT" 等  → showType
- 字符串以 "QUEST_GUIDE_*" 开头  → 导航/引导类型
- 字符串以 "Actor/Quest/" 开头  → MainQuest luaPath
- 字符串以 "QuestDialogue/" 开头  → Talk 对话脚本路径
- 字符串以 "TALK_BEGIN_*" / "TALK_HERO_*" 开头  → Talk 类型枚举

用法:
    python scripts/analyze_quests.py
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

# 强制 stdout 用 utf-8（Windows console 默认 cp936 会乱码）
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUEST_DIR = Path(__file__).resolve().parent.parent / "GenshinData" / "BinOutput" / "Quest"


def find_subquests_array(main_obj):
    """在 MainQuest 顶层找 subQuests 数组——它是值为对象数组、且对象内含 subId/mainId/order 模式的字段。"""
    candidates = []
    for v in main_obj.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            candidates.append(v)
    # 启发式：subQuests 数组通常是最大的对象数组之一
    candidates.sort(key=lambda a: -len(a))
    # 取第一个其元素含至少 5 个字段、且没有"_type" 等 talk 标志的
    for arr in candidates:
        sample = arr[0]
        if "_type" in sample:
            continue  # 这是 talk cond 数组，不是 subQuests
        if len(sample) >= 5:
            return arr
    return candidates[0] if candidates else []


def find_talks_array(main_obj, exclude=None):
    """找 Talk 数组。Talk 内部含 _type / TALK_* 等字段。"""
    for v in main_obj.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            if v is exclude:
                continue
            sample = v[0]
            sample_text = json.dumps(sample)
            if "TALK_" in sample_text or "QuestDialogue" in sample_text:
                return v
    return []


def walk_strings(obj):
    """深度递归遍历，返回所有字符串值（不含 key）。"""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk_strings(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from walk_strings(x)
    elif isinstance(obj, str):
        yield obj


def main():
    if not QUEST_DIR.is_dir():
        print(f"[!] not found: {QUEST_DIR}", file=sys.stderr)
        sys.exit(1)

    files = sorted(QUEST_DIR.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
    print(f"[+] scanning {len(files)} MainQuest files (recursive)...\n")

    total_main = 0
    total_sub = 0
    total_talks = 0
    sub_counts = []                               # [(mainId, subN)]
    cond_types = Counter()                        # QUEST_COND_*
    content_types = Counter()                     # QUEST_CONTENT_*
    exec_types = Counter()                        # QUEST_EXEC_*
    logic_types = Counter()                       # LOGIC_*
    show_types = Counter()                        # QUEST_HIDDEN / QUEST_DEFAULT
    guide_types = Counter()                       # QUEST_GUIDE_*
    talk_kind_types = Counter()                   # TALK_BEGIN_*, TALK_HERO_*
    has_lua = 0
    dialog_paths_total = 0

    main_richness = []                            # (richness, mainId, subN, fname)

    for f in files:
        try:
            with f.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
        except Exception as e:
            print(f"[skip] {f.name}: {e}", file=sys.stderr)
            continue

        total_main += 1
        main_id = int(f.stem) if f.stem.isdigit() else None
        subs = find_subquests_array(data)
        talks = find_talks_array(data, exclude=subs)
        sub_counts.append((main_id, len(subs)))
        total_sub += len(subs)
        total_talks += len(talks)

        richness = 0
        for s in walk_strings(data):
            if not s:
                continue
            if s.startswith("QUEST_COND_"):
                cond_types[s] += 1
            elif s.startswith("QUEST_CONTENT_"):
                content_types[s] += 1
                richness += 1
            elif s.startswith("QUEST_EXEC_"):
                exec_types[s] += 1
                richness += 2
            elif s.startswith("LOGIC_"):
                logic_types[s] += 1
            elif s.startswith("QUEST_GUIDE_"):
                guide_types[s] += 1
            elif s.startswith("QUEST_") and s != "QUEST_":
                show_types[s] += 1
            elif s.startswith("TALK_"):
                talk_kind_types[s] += 1
            elif s.startswith("Actor/Quest/"):
                has_lua += 1
            elif s.startswith("QuestDialogue/"):
                dialog_paths_total += 1

        main_richness.append((richness, main_id, len(subs), f.name))

    # ---- 输出 ----
    bar = "=" * 70
    print(bar)
    print(" 总览 / Overview")
    print(bar)
    print(f"  MainQuest 文件数:           {total_main}")
    print(f"  SubQuest 总数:               {total_sub}  (平均 {total_sub/max(total_main,1):.1f}/main)")
    print(f"  Talk 引用总数:               {total_talks}  (平均 {total_talks/max(total_main,1):.1f}/main)")
    print(f"  含 luaPath 的 MainQuest:     {has_lua} ({has_lua/max(total_main,1)*100:.1f}%)")
    print(f"  对话脚本路径总数:            {dialog_paths_total}")

    # SubQuest 数量分布
    print()
    print(bar)
    print(" SubQuest 数量分布 (每个 MainQuest)")
    print(bar)
    buckets = Counter()
    for _, c in sub_counts:
        if c == 0:
            buckets["0"] += 1
        elif c <= 5:
            buckets["1-5"] += 1
        elif c <= 10:
            buckets["6-10"] += 1
        elif c <= 20:
            buckets["11-20"] += 1
        elif c <= 50:
            buckets["21-50"] += 1
        else:
            buckets["50+"] += 1
    max_v = max(buckets.values()) if buckets else 1
    for k in ["0", "1-5", "6-10", "11-20", "21-50", "50+"]:
        v = buckets[k]
        bar_len = int(v / max_v * 50)
        print(f"  {k:>6}  {v:>5}  {'#' * bar_len}")

    # Top 最大
    print()
    print(bar)
    print(" Top 15 最大 MainQuest (按 SubQuest 数)")
    print(bar)
    sub_counts.sort(key=lambda x: -x[1])
    for mid, c in sub_counts[:15]:
        if mid is not None:
            print(f"  mainId={mid:>7}: {c:>4} SubQuests")

    # Cond
    print()
    print(bar)
    print(f" QuestCond 类型分布 (主要用于 Talk 条件) — top 25 / 共 {len(cond_types)} 种")
    print(bar)
    for t, n in cond_types.most_common(25):
        print(f"  {n:>7}  {t}")

    # Content
    print()
    print(bar)
    print(f" QuestContent 类型分布 (finishCond / failCond) — top 30 / 共 {len(content_types)} 种")
    print(bar)
    for t, n in content_types.most_common(30):
        print(f"  {n:>7}  {t}")

    # Exec
    print()
    print(bar)
    print(f" QuestExec 类型分布 — top 30 / 共 {len(exec_types)} 种")
    print(bar)
    for t, n in exec_types.most_common(30):
        print(f"  {n:>7}  {t}")

    # LogicType
    print()
    print(bar)
    print(f" LogicType 使用分布 — 共 {len(logic_types)} 种")
    print(bar)
    for t, n in logic_types.most_common():
        print(f"  {n:>7}  {t}")

    # showType
    print()
    print(bar)
    print(f" showType / 其他 QUEST_* 字符串分布 (top 15)")
    print(bar)
    for t, n in show_types.most_common(15):
        print(f"  {n:>7}  {t}")

    # Guide
    print()
    print(bar)
    print(f" 导航/引导类型分布 (QUEST_GUIDE_*)")
    print(bar)
    for t, n in guide_types.most_common(15):
        print(f"  {n:>7}  {t}")

    # Talk
    print()
    print(bar)
    print(f" Talk 类型分布 (TALK_*) — top 20 / 共 {len(talk_kind_types)} 种")
    print(bar)
    for t, n in talk_kind_types.most_common(20):
        print(f"  {n:>7}  {t}")

    # 富度 top
    print()
    print(bar)
    print(" Top 10 最 '丰富' 的 MainQuest (按 cond/content/exec 总数)")
    print(bar)
    main_richness.sort(key=lambda x: -x[0])
    for richness, mid, sub_n, fname in main_richness[:10]:
        print(f"  richness={richness:>5}  mainId={mid:>7}  subN={sub_n:>3}  {fname}")


if __name__ == "__main__":
    main()
