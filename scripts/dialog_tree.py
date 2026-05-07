"""
重构 Talk 对话树 / Dialog 链 为可读 Markdown。

数据源：
- ExcelBinOutput/DialogExcelConfigData.json (203,908 节点，主表)
- BinOutput/Talk/*.json (27 大型对话包，含线性对话链)
- TextMap/TextMapCHS.json
- ExcelBinOutput/NpcExcelConfigData.json

用法:
    # 从某个 dialog id 开始展开
    python scripts/dialog_tree.py --dialog 110195101

    # 直接给 MainQuest id, 展开它所有 Talk 的 performId
    python scripts/dialog_tree.py --quest 11019

    # 搜索包含指定文本的 dialog
    python scripts/dialog_tree.py --search "派蒙"
"""
import argparse
import json
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
TM_PATH = REPO / "GenshinData" / "TextMap" / "TextMapCHS.json"
NPC_PATH = REPO / "GenshinData" / "ExcelBinOutput" / "NpcExcelConfigData.json"
DIALOG_PATH = REPO / "GenshinData" / "ExcelBinOutput" / "DialogExcelConfigData.json"
TALK_DIR = REPO / "GenshinData" / "BinOutput" / "Talk"
QUEST_DIR = REPO / "GenshinData" / "BinOutput" / "Quest_translated"
OUT_DIR = REPO / "output" / "dialogs"


def load_textmap():
    print("[+] loading TextMap...", file=sys.stderr)
    with TM_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def load_npcs(textmap):
    print("[+] loading NPCs...", file=sys.stderr)
    with NPC_PATH.open(encoding="utf-8") as f:
        npcs = json.load(f)
    out = {}
    for npc in npcs:
        nid = npc.get("id")
        if nid is None: continue
        name = textmap.get(str(npc.get("nameTextMapHash", 0)))
        if not name or name.startswith("Npc"):
            name = npc.get("alias") or f"npc{nid}"
        out[nid] = name
    return out


def load_dialogs(textmap, npc_name):
    """从 DialogExcel 构建 id → entry 字典。"""
    print("[+] loading DialogExcel (~10s)...", file=sys.stderr)
    with DIALOG_PATH.open(encoding="utf-8") as f:
        dialogs = json.load(f)
    out = {}
    for de in dialogs:
        did = de.get("GFLDJMJKIKE")
        if did is None: continue
        role = de.get("talkRole", {})
        rid = role.get("id", "")
        speaker = npc_name.get(int(rid), f"npc{rid}") if rid and rid.isdigit() else "?"
        text_h = de.get("talkContentTextMapHash")
        text = textmap.get(str(text_h), "(text not in TextMap)") if text_h else ""
        out[did] = {
            "id": did,
            "speaker": speaker,
            "text": text,
            "next": de.get("nextDialogs", []) or [],
            "audio": de.get("talkAudioName", "") or "",
        }
    return out


def load_binoutput_talks(textmap, npc_name, dialog_lookup):
    """从 BinOutput/Talk/*.json 补充更多对话节点（这些是大型对话包，DialogExcel 未必包含）。"""
    if not TALK_DIR.is_dir():
        return
    print("[+] loading BinOutput/Talk packs...", file=sys.stderr)
    extra = 0
    # 字段名是混淆的，用值模式识别
    for f in TALK_DIR.glob("*.json"):
        try:
            with f.open(encoding="utf-8") as fp:
                pack = json.load(fp)
        except:
            continue
        # 找 dialogList: 一个对象数组，元素含 talkRole 模式
        dialog_list = None
        for v in pack.values() if isinstance(pack, dict) else []:
            if isinstance(v, list) and v and isinstance(v[0], dict):
                # 检测：含 _type/_id 的 talkRole 对象
                sample = v[0]
                has_role = any(
                    isinstance(sv, dict) and sv.get("_type", "").startswith("TALK_ROLE_")
                    for sv in sample.values()
                )
                if has_role:
                    dialog_list = v
                    break
        if not dialog_list:
            continue

        for node in dialog_list:
            # node 的 id 是某个 int 字段（混淆 key, 一般是 JOLEJEFDNJJ）
            nid = None
            speaker = "?"
            text_h = None
            for k, val in node.items():
                if isinstance(val, int):
                    if 100000000 <= val < 1000000000 and nid is None:
                        nid = val
                    elif val > 1_000_000:
                        text_h = val
                elif isinstance(val, dict) and val.get("_type", "").startswith("TALK_ROLE_"):
                    rid = val.get("_id", "")
                    if rid and rid.isdigit():
                        speaker = npc_name.get(int(rid), f"npc{rid}")
            if nid is None:
                continue
            if nid in dialog_lookup:
                continue  # 已有
            text = textmap.get(str(text_h), "") if text_h else ""
            dialog_lookup[nid] = {
                "id": nid,
                "speaker": speaker,
                "text": text,
                "next": [],
                "audio": "",
            }
            extra += 1
    print(f"    +{extra:,} extra dialog nodes", file=sys.stderr)


def render_tree(start_id, dialog_lookup, max_depth=30, visited=None):
    """以 markdown 缩进形式渲染对话链。"""
    if visited is None:
        visited = set()
    out = []
    stack = [(start_id, 0)]
    while stack:
        did, depth = stack.pop()
        if did in visited or depth > max_depth:
            continue
        visited.add(did)
        d = dialog_lookup.get(did)
        if not d:
            indent = "  " * depth
            out.append(f"{indent}- ⚠️ Dialog {did}: 不在 DialogExcel 也不在 BinOutput/Talk")
            continue
        indent = "  " * depth
        speaker = d["speaker"]
        text = d["text"] or "(empty)"
        out.append(f"{indent}- **[{speaker}]** {text}  *(dialog {did})*")
        # 倒序压栈以保证 next 顺序
        for n in reversed(d["next"]):
            stack.append((n, depth + 1))
    return "\n".join(out)


def cmd_dialog(args, dialog_lookup):
    print(f"# Dialog tree from {args.dialog}\n")
    print(render_tree(args.dialog, dialog_lookup))


def cmd_quest(args, dialog_lookup):
    f = QUEST_DIR / f"{args.quest}.json"
    if not f.exists():
        print(f"[!] {f} not found", file=sys.stderr)
        return
    with f.open(encoding="utf-8") as fp:
        q = json.load(fp)
    title = q.get("titleText", "")
    desc = q.get("descText", "")
    parts = [
        f"# MainQuest {args.quest} — {title}\n",
        f"> {desc}\n" if desc else "",
    ]
    talks = q.get("talks", []) or []
    parts.append(f"## 该任务包含 {len(talks)} 个 Talk\n")
    for t in talks:
        tid = t.get("id")
        pid = t.get("performId")
        npcs = t.get("npcName") or t.get("npcId", [])
        beg = t.get("beginCondComb", "")
        parts.append(f"### Talk {tid} → performId={pid}")
        parts.append(f"- 触发对象：{npcs}  / 起始方式：{t.get('beginWay', '?')}")
        if beg:
            parts.append(f"- 条件组合：{beg}")
        parts.append("")
        if pid:
            parts.append(render_tree(pid, dialog_lookup, max_depth=20))
        parts.append("")
    out_path = OUT_DIR / f"quest_{args.quest}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"  ✓ → {out_path}", file=sys.stderr)


def cmd_search(args, dialog_lookup, limit=20):
    needle = args.search
    print(f"# Search '{needle}' in dialog content\n")
    hits = 0
    for did, d in dialog_lookup.items():
        if needle in (d.get("text") or ""):
            print(f"- dialog {did}: **[{d['speaker']}]** {d['text']}")
            hits += 1
            if hits >= limit:
                print(f"\n... (limit {limit}, more results truncated)")
                break
    if hits == 0:
        print("(no matches)")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dialog", type=int, help="从某 dialog id 开始展开对话链")
    g.add_argument("--quest", type=int, help="给定 mainQuest id, 展开其所有 Talk")
    g.add_argument("--search", type=str, help="搜索 dialog 内容包含的关键字")
    args = ap.parse_args()

    textmap = load_textmap()
    npc_name = load_npcs(textmap)
    dialog_lookup = load_dialogs(textmap, npc_name)
    load_binoutput_talks(textmap, npc_name, dialog_lookup)
    print(f"[+] total dialog nodes available: {len(dialog_lookup):,}\n", file=sys.stderr)

    if args.dialog:
        cmd_dialog(args, dialog_lookup)
    elif args.quest:
        cmd_quest(args, dialog_lookup)
    elif args.search:
        cmd_search(args, dialog_lookup)


if __name__ == "__main__":
    main()
