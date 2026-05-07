"""
把 MainQuest 的 SubQuest 状态转移画成 Mermaid 流程图。

用法:
    python scripts/visualize_quest.py 11019         # 输出到 output/diagrams/11019.md
    python scripts/visualize_quest.py 3022 --stdout # 直接打印
    python scripts/visualize_quest.py 1001 11019 3022  # 多个

输入: GenshinData/BinOutput/Quest_translated/<id>.json (deobfuscated + translated)

边的语义:
    实线箭头  →  顺序流（按 order 推进）
    虚线红色  →  failExec rollback (回滚到 save-point)
    虚线蓝色  →  acceptCond 条件依赖 (state/var/...)
    虚线橙色  →  finishExec ADD_QUEST_PROGRESS (跨步推进)

节点形状/颜色:
    [圆角]      Save point (无 finishCond / 是其他步骤的 rollback 目标)
    [矩形]      普通 SubQuest
    [双圆]      finishParent=true (关闭整个 MainQuest)
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
INPUT_DIR = REPO / "GenshinData" / "BinOutput" / "Quest_translated"
OUT_DIR = REPO / "output" / "diagrams"


def short_label(s, max_len=40):
    if not s:
        return ""
    s = s.replace('"', "'").replace("\n", " ").strip()
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


def build_graph(quest):
    """从 quest 配表抽取 SubQuest 关系图。"""
    main_id = quest.get("id")
    main_title = quest.get("titleText", str(main_id))
    subs = quest.get("subQuests", []) or []
    sub_by_id = {s["subId"]: s for s in subs}

    # 找 save-point 候选: finishCond 几乎为空 + 被多个 failExec 引用
    rollback_targets = defaultdict(int)  # subId -> 被 rollback 引用次数
    for s in subs:
        for ex in s.get("failExec", []) or []:
            if ex.get("type") == "QUEST_EXEC_ROLLBACK_QUEST":
                p = ex.get("param", [])
                if p:
                    try:
                        rollback_targets[int(p[0])] += 1
                    except (ValueError, IndexError):
                        pass

    save_points = {sid for sid, n in rollback_targets.items() if n >= 2}

    edges = []   # (from, to, type, label)
    nodes = []   # (subId, label, shape)
    sub_orders = sorted([(s.get("order", 999), s["subId"]) for s in subs])

    for s in subs:
        sid = s["subId"]
        order = s.get("order")
        title = s.get("descText", "")
        is_finish_parent = s.get("finishParent", False)

        # 节点形状选择
        if is_finish_parent:
            shape = "double"
        elif sid in save_points:
            shape = "stadium"  # 圆角=save-point
        else:
            shape = "box"

        # 标签内容
        label_parts = [f"{sid}"]
        if order is not None:
            label_parts.append(f"order={order}")
        if title:
            label_parts.append(short_label(title, 30))
        # 主要 finishCond 类型
        for fc in (s.get("finishCond", []) or [])[:1]:
            t = fc.get("type", "")
            if t.startswith("QUEST_CONTENT_"):
                label_parts.append(t.replace("QUEST_CONTENT_", "FC:"))
        label = "<br/>".join(label_parts)
        nodes.append((sid, label, shape))

        # ── 边 1: 顺序流 (order N → order N+1, 优先级最低)
        # 只画在没有显式 acceptCond 时

        # ── 边 2: acceptCond 推断
        for ac in s.get("acceptCond", []) or []:
            t = ac.get("type", "")
            params = ac.get("param", []) or []
            if t == "QUEST_COND_STATE_EQUAL" and len(params) >= 2:
                try:
                    src_sid = int(params[0])
                    state = int(params[1])
                except (ValueError, TypeError):
                    continue
                if src_sid in sub_by_id and src_sid != sid:
                    state_name = {3: "FINISHED", 4: "FAILED", 2: "UNFINISHED"}.get(state, f"state={state}")
                    edges.append((src_sid, sid, "cond", state_name))
            elif t == "QUEST_COND_QUEST_VAR_EQUAL" and len(params) >= 2:
                # 跨 subquest 的变量依赖（变量是某 subquest 的副作用）
                edges.append((None, sid, "var", f"var[{params[0]}]={params[1]}"))

        # ── 边 3: failExec rollback
        for ex in s.get("failExec", []) or []:
            if ex.get("type") == "QUEST_EXEC_ROLLBACK_QUEST":
                p = ex.get("param", [])
                if p:
                    try:
                        target = int(p[0])
                    except (ValueError, TypeError):
                        continue
                    edges.append((sid, target, "rollback", "fail→rollback"))

        # ── 边 4: finishExec ADD_QUEST_PROGRESS
        for ex in s.get("finishExec", []) or []:
            if ex.get("type") == "QUEST_EXEC_ADD_QUEST_PROGRESS":
                p = ex.get("param", []) or []
                if p:
                    try:
                        target = int(p[0])
                    except (ValueError, TypeError):
                        continue
                    edges.append((sid, target, "progress", "addProgress"))

    # 顺序流 fallback: 如果两个相邻 order 的 SubQuest 没有任何 cond 边连接，加一条顺序边
    cond_edge_set = {(f, t) for f, t, et, _ in edges if et in ("cond", "progress") and f is not None}
    for i in range(len(sub_orders) - 1):
        _, a = sub_orders[i]
        _, b = sub_orders[i + 1]
        if (a, b) not in cond_edge_set and a != b:
            sub_b = sub_by_id.get(b, {})
            if not sub_b.get("acceptCond"):
                edges.append((a, b, "seq", "next"))

    return main_id, main_title, nodes, edges, save_points


def render_mermaid(main_id, main_title, nodes, edges, save_points):
    """渲染成 Mermaid 流程图代码。"""
    lines = [
        "```mermaid",
        "flowchart TD",
        f'  %% MainQuest {main_id} — {main_title}',
        "",
    ]

    # Nodes
    for sid, label, shape in nodes:
        node_id = f"q{sid}"
        if shape == "stadium":
            lines.append(f'  {node_id}(["{label}"]):::savepoint')
        elif shape == "double":
            lines.append(f'  {node_id}["{label}"]:::finishparent')
        else:
            lines.append(f'  {node_id}["{label}"]')

    lines.append("")

    # Edges
    for f, t, etype, label in edges:
        if f is None:
            continue
        f_id = f"q{f}"
        t_id = f"q{t}"
        if etype == "rollback":
            lines.append(f'  {f_id} -.->|{label}| {t_id}:::rollbackedge')
            # Mermaid 不支持给 edge 命名 class，所以用样式间接
        elif etype == "cond":
            lines.append(f'  {f_id} ==>|{label}| {t_id}')
        elif etype == "progress":
            lines.append(f'  {f_id} -.->|{label}| {t_id}')
        elif etype == "seq":
            lines.append(f'  {f_id} --> {t_id}')
        elif etype == "var":
            # 用一个虚拟节点表达变量依赖
            pass  # 简化：跳过 var 边的渲染

    lines += [
        "",
        "  classDef savepoint fill:#ffe6e6,stroke:#a00,stroke-width:2px",
        "  classDef finishparent fill:#e6ffe6,stroke:#080,stroke-width:3px",
        "```",
    ]
    return "\n".join(lines)


def render_md(quest, mermaid_code):
    main_id = quest.get("id")
    title = quest.get("titleText", "")
    desc = quest.get("descText", "")
    sub_n = len(quest.get("subQuests", []))
    talk_n = len(quest.get("talks", []))

    parts = [
        f"# MainQuest {main_id} — {title}",
        "",
        f"**描述**：{desc}" if desc else "*(无描述)*",
        "",
        f"- SubQuest 数: {sub_n}",
        f"- Talk 数: {talk_n}",
        f"- Lua 入口: `{quest.get('luaPath', '?')}`",
        "",
        "## 状态转移图",
        "",
        mermaid_code,
        "",
        "## 图例",
        "",
        "- **粗实线箭头**：acceptCond 状态依赖（A 完成/失败 → B 接取）",
        "- **细实线箭头**：默认顺序流（按 order 推进）",
        "- **虚线箭头**：finishExec / failExec 副作用（rollback / addProgress）",
        "- **圆角节点**：Save Point（被多次 rollback 引用的安全点）",
        "- **绿色加粗节点**：finishParent=true（完成它就关闭整个 MainQuest）",
    ]
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+", type=int, help="MainQuest IDs")
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args()

    if not INPUT_DIR.is_dir():
        print(f"[!] missing {INPUT_DIR} — run translate_text.py first", file=sys.stderr)
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for mid in args.ids:
        f = INPUT_DIR / f"{mid}.json"
        if not f.exists():
            print(f"[!] {mid}.json not found", file=sys.stderr)
            continue
        with f.open(encoding="utf-8") as fp:
            quest = json.load(fp)
        main_id, title, nodes, edges, save_points = build_graph(quest)
        mermaid = render_mermaid(main_id, title, nodes, edges, save_points)
        md = render_md(quest, mermaid)

        if args.stdout:
            print(md)
            print()
        else:
            out = OUT_DIR / f"{mid}.md"
            out.write_text(md, encoding="utf-8")
            print(f"  ✓ {mid} ({title}): {len(nodes)} nodes / {len(edges)} edges → {out}")


if __name__ == "__main__":
    main()
