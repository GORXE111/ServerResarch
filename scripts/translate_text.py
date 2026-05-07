"""
把反混淆后的 quest JSON 里的 textMapHash 翻译成实际中文台词。

工作方式：
1. 加载 GenshinData/TextMap/TextMapCHS.json (~30 MB) 到内存字典
2. 走 GenshinData/BinOutput/Quest_clean/*.json 全部文件
3. 对任何字段名包含 "TextMapHash" 的字段，新增一个 sibling 文本字段：
     "titleTextMapHash": 2046717777
   →
     "titleTextMapHash": 2046717777,
     "titleText": "捕风的异乡人"
4. 输出到 GenshinData/BinOutput/Quest_translated/

用法:
    python scripts/translate_text.py
    python scripts/translate_text.py --sample 1001 3022 11019
"""
import argparse
import json
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
TEXTMAP_PATH = REPO / "GenshinData" / "TextMap" / "TextMapCHS.json"
INPUT_DIR    = REPO / "GenshinData" / "BinOutput" / "Quest_clean"
OUTPUT_DIR   = REPO / "GenshinData" / "BinOutput" / "Quest_translated"


def make_text_key(hash_key):
    """titleTextMapHash → titleText。其它形式补 _text 后缀。"""
    if hash_key.endswith("TextMapHash"):
        return hash_key[:-len("TextMapHash")] + "Text"
    return hash_key + "_text"


def translate(obj, textmap, stats):
    """递归翻译。stats 记录命中/未命中数量。"""
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            new_v = translate(v, textmap, stats)
            new_obj[k] = new_v
            # 找 hash 字段
            if isinstance(v, int) and "TextMapHash" in k or k == "textMapHash":
                key_str = str(v)
                if key_str in textmap:
                    new_obj[make_text_key(k)] = textmap[key_str]
                    stats["hit"] += 1
                else:
                    stats["miss"] += 1
        return new_obj
    elif isinstance(obj, list):
        return [translate(x, textmap, stats) for x in obj]
    return obj


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", nargs="+", type=int, default=None)
    args = parser.parse_args()

    if not TEXTMAP_PATH.is_file():
        print(f"[!] TextMap not found: {TEXTMAP_PATH}", file=sys.stderr)
        print("    Add /TextMap/TextMapCHS.json to GenshinData/.git/info/sparse-checkout, then run git read-tree -mu HEAD", file=sys.stderr)
        sys.exit(1)
    if not INPUT_DIR.is_dir():
        print(f"[!] Input dir not found: {INPUT_DIR}", file=sys.stderr)
        print("    Run scripts/deobfuscate_keys.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"[+] loading TextMap from {TEXTMAP_PATH}...")
    with TEXTMAP_PATH.open("r", encoding="utf-8") as f:
        textmap = json.load(f)
    print(f"[+] loaded {len(textmap):,} text entries\n")

    files = sorted(INPUT_DIR.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
    if args.sample:
        wanted = {f"{i}.json" for i in args.sample}
        files = [f for f in files if f.name in wanted]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[+] translating {len(files)} files...")
    stats = {"hit": 0, "miss": 0}
    written = 0
    for f in files:
        try:
            with f.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            translated = translate(data, textmap, stats)
            out_path = OUTPUT_DIR / f.name
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(translated, fp, indent=2, ensure_ascii=False)
            written += 1
        except Exception as e:
            print(f"[!] {f.name}: {e}", file=sys.stderr)

    print(f"\n=== Summary ===")
    print(f"  files written:    {written}")
    total = stats["hit"] + stats["miss"]
    if total > 0:
        rate = stats["hit"] / total * 100
        print(f"  textHash 命中:    {stats['hit']:,} / {total:,} ({rate:.1f}%)")
        print(f"  textHash 未命中:  {stats['miss']:,}")


if __name__ == "__main__":
    main()
