"""
反混淆 + 翻译完整管线最终版。

输入：GenshinData/BinOutput/Quest_clean/*.json (反混淆但未翻译)
输出：GenshinData/BinOutput/Quest_translated/*.json (反混淆 + 翻译 + NPC 名 + 对话内容)

数据源：
- TextMap/TextMapCHS.json (379,178 条文本)
- ExcelBinOutput/NpcExcelConfigData.json (5079 NPC)
- ExcelBinOutput/DialogExcelConfigData.json (203,908 条对话节点)

翻译动作：
1. 任意 *TextMapHash 字段 → 添加 sibling 文本字段 (xxxText)
2. npcId 字段 (int 或 list) → 添加 sibling npcName 字段
3. talkRole._id → 添加 talkRoleName
4. performId (Talk 的首对话 id) → 添加 performText (该对话首句台词)

用法:
    python scripts/translate_text.py
    python scripts/translate_text.py --sample 11019 3022 351
"""
import argparse
import json
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
TEXTMAP_PATH = REPO / "GenshinData" / "TextMap" / "TextMapCHS.json"
NPC_PATH     = REPO / "GenshinData" / "ExcelBinOutput" / "NpcExcelConfigData.json"
DIALOG_PATH  = REPO / "GenshinData" / "ExcelBinOutput" / "DialogExcelConfigData.json"
INPUT_DIR    = REPO / "GenshinData" / "BinOutput" / "Quest_clean"
OUTPUT_DIR   = REPO / "GenshinData" / "BinOutput" / "Quest_translated"


def make_text_key(hash_key):
    """titleTextMapHash → titleText；否则补 _text 后缀。"""
    if hash_key.endswith("TextMapHash"):
        return hash_key[:-len("TextMapHash")] + "Text"
    return hash_key + "_text"


def load_lookups():
    """加载并构建所有翻译查表。"""
    print(f"[+] loading TextMap...")
    with TEXTMAP_PATH.open(encoding="utf-8") as f:
        textmap = json.load(f)
    print(f"    {len(textmap):,} entries")

    print(f"[+] loading NpcExcel + building npcId → name table...")
    with NPC_PATH.open(encoding="utf-8") as f:
        npc_data = json.load(f)
    npc_name = {}
    for npc in npc_data:
        nid = npc.get("id")
        if nid is None: continue
        h = npc.get("nameTextMapHash", 0)
        name = textmap.get(str(h))
        # fallback: 用 alias 或 npc<id>
        if not name or name.startswith("Npc"):
            name = npc.get("alias") or f"npc{nid}"
        npc_name[nid] = name
    print(f"    {len(npc_name):,} NPCs")

    print(f"[+] loading DialogExcel + building dialogId → text table (this takes ~10s)...")
    with DIALOG_PATH.open(encoding="utf-8") as f:
        dialog_data = json.load(f)
    dialog_lookup = {}
    for de in dialog_data:
        # GFLDJMJKIKE 是 dialog id
        did = de.get("GFLDJMJKIKE")
        if did is None: continue
        content_h = de.get("talkContentTextMapHash")
        role = de.get("talkRole", {})
        role_id = role.get("id", "")
        role_name = npc_name.get(int(role_id), f"npc{role_id}") if role_id and role_id.isdigit() else "?"
        content = textmap.get(str(content_h), "(text not in TextMap)") if content_h else ""
        dialog_lookup[did] = {
            "speaker": role_name,
            "text": content,
            "next": de.get("nextDialogs", []),
        }
    print(f"    {len(dialog_lookup):,} dialog nodes")

    return textmap, npc_name, dialog_lookup


def translate(obj, textmap, npc_name, dialog_lookup, stats, parent_key=""):
    """递归翻译。"""
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            new_v = translate(v, textmap, npc_name, dialog_lookup, stats, k)
            new_obj[k] = new_v

            # ── 1. 文本 hash 翻译 ──
            if isinstance(v, int) and ("TextMapHash" in k or k == "textMapHash"):
                txt = textmap.get(str(v))
                if txt is not None:
                    new_obj[make_text_key(k)] = txt
                    stats["text_hit"] += 1
                else:
                    stats["text_miss"] += 1

            # ── 2. NPC 翻译 ──
            elif k == "npcId" and isinstance(v, list) and v and all(isinstance(x, int) for x in v):
                names = [npc_name.get(nid, f"npc{nid}") for nid in v]
                new_obj["npcName"] = names
                stats["npc_resolved"] += len(v)

            # ── 3. talkRole._id → 角色名 ──
            elif k == "talkRole" and isinstance(v, dict) and v.get("_id"):
                rid = v["_id"]
                if rid.isdigit():
                    new_obj["talkRoleName"] = npc_name.get(int(rid), f"npc{rid}")

            # ── 4. performId → 首对话内容 ──
            elif k == "performId" and isinstance(v, int):
                d = dialog_lookup.get(v)
                if d:
                    new_obj["performText"] = f"[{d['speaker']}] {d['text']}"
                    if d["next"]:
                        new_obj["performNextDialogs"] = d["next"]
                    stats["dialog_hit"] += 1
                else:
                    stats["dialog_miss"] += 1

        return new_obj
    elif isinstance(obj, list):
        return [translate(x, textmap, npc_name, dialog_lookup, stats, parent_key) for x in obj]
    return obj


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", nargs="+", type=int, default=None)
    args = parser.parse_args()

    for p in [TEXTMAP_PATH, NPC_PATH, DIALOG_PATH]:
        if not p.is_file():
            print(f"[!] missing: {p}", file=sys.stderr)
            sys.exit(1)
    if not INPUT_DIR.is_dir():
        print(f"[!] run scripts/deobfuscate_keys.py first", file=sys.stderr)
        sys.exit(1)

    textmap, npc_name, dialog_lookup = load_lookups()

    files = sorted(INPUT_DIR.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
    if args.sample:
        wanted = {f"{i}.json" for i in args.sample}
        files = [f for f in files if f.name in wanted]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n[+] translating {len(files)} files...")

    stats = {"text_hit": 0, "text_miss": 0, "npc_resolved": 0,
             "dialog_hit": 0, "dialog_miss": 0}
    written = 0
    for f in files:
        try:
            with f.open(encoding="utf-8") as fp:
                data = json.load(fp)
            result = translate(data, textmap, npc_name, dialog_lookup, stats)
            out = OUTPUT_DIR / f.name
            with out.open("w", encoding="utf-8") as fp:
                json.dump(result, fp, indent=2, ensure_ascii=False)
            written += 1
        except Exception as e:
            print(f"[!] {f.name}: {e}", file=sys.stderr)

    print(f"\n=== Summary ===")
    print(f"  files written:        {written}")
    th = stats["text_hit"]; tm = stats["text_miss"]
    if th + tm > 0:
        print(f"  textHash 命中:        {th:,}/{th+tm:,} ({th/(th+tm)*100:.1f}%)")
    print(f"  npc 翻译:             {stats['npc_resolved']:,} 处")
    dh = stats["dialog_hit"]; dm = stats["dialog_miss"]
    if dh + dm > 0:
        print(f"  performId → dialog:   {dh:,}/{dh+dm:,} ({dh/(dh+dm)*100:.1f}%)")


if __name__ == "__main__":
    main()
