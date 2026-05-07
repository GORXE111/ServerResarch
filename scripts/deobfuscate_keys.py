"""
反混淆 GenshinData/BinOutput/Quest/*.json 的字段名。

上游数据的 key 是版本特定的混淆字符串（e.g., "JOLEJEFDNJJ"）。本脚本：
1. 用基于代码考古/实测得到的映射表把已知 key 重命名
2. 对未知 key，用 value 类型/模式推断；保留原 key 名以便人工核对
3. 跨文件一致性校验（同一 key 在所有文件里的 value 类型必须一致）
4. 输出到 GenshinData/BinOutput/Quest_clean/<id>.json

用法:
    python scripts/deobfuscate_keys.py
    python scripts/deobfuscate_keys.py --sample 1001 3022      # 只处理指定 ID
    python scripts/deobfuscate_keys.py --report                # 不写文件，只打印未知 key 报告
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
QUEST_DIR = REPO_ROOT / "GenshinData" / "BinOutput" / "Quest"
OUT_DIR   = REPO_ROOT / "GenshinData" / "BinOutput" / "Quest_clean"

# ============================================================================
# 已知混淆 key → 真实字段名映射
# 来源：(1) Grasscutter-Quests 源码中 MainQuestData.java / SubQuestData.java 的字段定义
#       (2) 实测多个 quest 文件后的 value-pattern 比对
#       (3) 跨文件一致性验证
# ----------------------------------------------------------------------------
# 注意：同一个 key 在不同嵌套层级语义可能略有不同（例如 JOLEJEFDNJJ 在
# MainQuest 顶层是 mainQuestId、在 SubQuest 是 subQuestId、在 Talk 是 talkId、
# 在 DialogNode 是 dialogId）—— 但都是"this 对象的 ID"，所以统一命名为 "id"
# 就能保留语义。
# ============================================================================
KEY_MAP = {
    # ── 通用 ID 字段 ─────────────────────────────────────────
    "JOLEJEFDNJJ": "id",
    "ILPBLDDCLDB": "subId",
    "IEFDCPGFPFP": "mainId",
    "GLABOIDHFKF": "talk_mainQuestId",   # Talk 内部指向所属 MainQuest
    "EPAEFJJNLEP": "order",

    # ── 章节/分类 ────────────────────────────────────────────
    "ILCLLODLLLG": "series",
    "ELDHIICIOEO": "chapterId",
    "BOLHKDOCBNM": "collectionId",

    # ── 文本 hash ────────────────────────────────────────────
    "OOPHEFKEDIO": "titleTextMapHash",
    "JIHEILBABBF": "descTextMapHash",
    "EMKCOIBADBJ": "textMapHash",        # 对话节点
    "ILJJONAKPMF": "talkTextMapHash",    # Talk 节点

    # ── 字符串字段 ───────────────────────────────────────────
    "PJNIIAADAAO": "luaPath",
    "GGDOMCGJGHB": "showType",
    "FOHDKIBNGJB": "performCfg",
    "JJEGNKNFNCL": "versionBegin",
    "DDODDBBMCAB": "versionEnd",
    "IHMGKFLMJAF": "talkExtraField",     # 总是 ""
    "OMNDEBJIOCP": "type",                # cond/exec/guide 内的 type
    "CGMHJIBLJEE": "beginWay",            # TALK_BEGIN_MANUAL/AUTO
    "KAAKKMJJJIM": "talkRoleType",        # TALK_HERO_MAIN

    # ── Logic / 组合 ─────────────────────────────────────────
    "NKLEMELAGEE": "beginCondComb",       # LOGIC_AND/OR

    # ── MainQuest 顶层数组字段 ───────────────────────────────
    "MPBNEILAFCB": "subQuests",
    "DMIMNILOLKP": "talks",
    "MFMFGILBDJB": "subQuestVarDefs",     # 推测：questVar 默认值定义
    "KJNKFMPAGAA": "dialogList",          # BinOutput/Talk 大型对话节点列表

    # ── SubQuest cond/exec 数组 ──────────────────────────────
    "JCHNHPHNFPP": "finishCond",
    "JABFCLMAGKN": "failCond",
    "GIJNFABJPLK": "finishExec",
    "KIOEECHONOG": "failExec",
    # beginExec 在 1001/3022 没出现，key 待补充

    # ── Talk 内部 ────────────────────────────────────────────
    "MNPHAFOHNML": "beginCond",
    "KDMIHPJGDFC": "priority",
    "FBALOFKGJKN": "performId",
    "DFOGMKICPEF": "npcId",
    "GKCFOJDKOJG": "siblingTalks",        # 同组对话选项 (a.k.a nextTalks)
    "CLMNEDLMAJL": "nextTalks",
    "DPKIMOJPGDN": "extraLoadMarkId",

    # ── SubQuest 其他 ────────────────────────────────────────
    "ADAPCLIELKE": "guide",
    "PCEKHDNNJFI": "questGuideTrigger",   # 推测：含 EILMHFHJPOJ 的对象
    "EILMHFHJPOJ": "triggerCfgPath",      # 嵌套字符串
    "LEANNGJJHPH": "isRewind",
    "DOJGLMGJBFN": "finishParent",
    "FJOHFMAOAEA": "isMpBlock",
    "HABIGOMPKMD": "talkAutoTrigger",     # 推测，bool
    "CJODFPCDAPO": "talkAutoTrigger2",    # 推测，bool
    "FAHGBAHMINB": "subQuestFlag1",       # 推测
    "FOIBHJHAFFP": "talkExtra1",          # 总是 ""
    "ANJMDLPMIEK": "talkExtra2",
    "KBIBLEDOMCP": "talkExtra3",
    "MKPDFLFNGDK": "talkExtra4",
    "GLGCPMKJFGC": "talkExtra5",
    "PGCBJDMIAKD": "talkExtra6",
    "ODLPANKOAPL": "talkExtra7",

    # ── cond/exec item 内部 ──────────────────────────────────
    "OHDDPLPMHKE": "param",
    "DKCPHNOMBAG": "count",

    # ── Guide 内部 ───────────────────────────────────────────
    "BILDDGBDOCD": "guideScene",
    "EALEAAKMOHN": "guideLayer",
    "HIBMMHHLAEC": "guideStyle",

    # ── Dialog node 内部（BinOutput/Talk） ───────────────────
    "IFAOOKCBDGD": "talkRole",            # 含 _type, _id

    # ── 顶层未明字段（保留原 key + 注释） ────────────────────
    "MOGKDOMAMHP": "_top1",
    "IIIGFAPFIBI": "_top2",
    "JNDLGECECCL": "_top3",
    "KMHJFCOCNNG": "npcTalkMap",          # MainQuest 顶层: NPC ID → talkIds 映射 (键是 NPC ID 字符串)
    "PBAEPDPNKEJ": "talkPackId",          # BinOutput/Talk 顶层
    "INKCBLMIHJP": "_top5",

    # ── 第二轮反混淆补充（基于跨文件 value-pattern 验证） ──
    "ELJHJLHLMFE": "activeMode",          # PLAY_MODE_ALL / PLAY_MODE_SINGLE
    "JMDCFNGKJKL": "guideAutoCfg",        # QUEST_GUIDE_AUTO_ENABLE/DISABLE
    "MNNOLNCNOPO": "showGuide",           # QUEST_GUIDE_ITEM_DISABLE/MOVE_HIDE
    "KPLKCIIELBN": "subIdSet",            # 关联 SubQuest ID 列表
    "HIAGKCJOPLC": "failParent",          # bigint hash (failParent 字段)
    "LGNEJEELAOM": "subShowType",         # SubQuest 内的 showType (与 GGDOMCGJGHB 区分)
    "NFMPLAIEPAM": "mainQuestTag",        # MAINQUEST_TAG_*
    "BELELBNNMOB": "guideTipsTextMapHash", # bigint
    "GGEPLBIMCLJ": "talkFlag1",           # bool
    "GHEJOIBMGMH": "talkFlag2",           # bool
    "EGGGLFDEFKM": "talkFlag3",           # bool (出现在 Talk)
    "HECNNKOIGEE": "talkFlag4",           # bool
    "EELNLDDAJPG": "talkFlag5",           # bool
    "KGAPFNAHDCO": "talkFlag6",           # bool
    "CGMCEGHOCEN": "mainQuestRepeatable", # bool, 在 MainQuest 顶层
    "JMOEMIFODBB": "guideExtra1",         # int
    "DEJMNMLNIGC": "questIntField1",      # int
    "NOFHLJPNBBK": "questIntField2",      # int
    "KIOMIBIHADB": "extraLoadMarkPos",    # list[str] 形如 "[scene:x,y,z]"
    "KLECBDMBKBM": "questIntField3",      # int
    "IOJIDCDKKKI": "talkBeginExtra",      # str_TALK
    "NBOLDNEAEFO": "dialogShowType",      # TALK_SHOW_FORCE_SELECT
    "EIKACHBNBMJ": "voiceTextMapHash",    # 对话节点的额外 textHash
    "FAHGBAHMINB": "subQuestRepeatable",  # bool / 在 SubQuest
    "GIJLDDDPHBP": "_unknown_extra",
    "DPKIMOJPGDN": "extraLoadMarkId",     # 额外加载标记 ID 列表
}

# 允许的字面 key（不混淆）
LITERAL_KEYS = {"_type", "_param", "_id"}


def deobfuscate(obj, unknown_counter, type_consistency):
    """递归反混淆。

    unknown_counter: Counter — 未知 key 出现次数
    type_consistency: dict[key -> set of value-type-tags] — 用于跨文件类型一致性检查
    """
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            new_v = deobfuscate(v, unknown_counter, type_consistency)
            # 数字字符串 key (NPC ID 等) 保持原样，不视作混淆
            if k.isdigit():
                new_obj[k] = new_v
                continue
            new_k = KEY_MAP.get(k, k)
            if k not in KEY_MAP and k not in LITERAL_KEYS:
                unknown_counter[k] += 1
                new_k = f"_UNK_{k}"
            # 类型一致性记录
            type_consistency.setdefault(k, set()).add(_value_tag(v))
            new_obj[new_k] = new_v
        return new_obj
    elif isinstance(obj, list):
        return [deobfuscate(x, unknown_counter, type_consistency) for x in obj]
    return obj


def _value_tag(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        if v == 0:
            return "int_zero"
        if v > 1_000_000_000:
            return "bigint"
        return "int"
    if isinstance(v, str):
        if v == "":
            return "str_empty"
        if v.startswith("QUEST_"):
            return f"str_QUEST_{v.split('_')[1] if '_' in v[6:] else 'X'}"
        if v.startswith("LOGIC_"):
            return "str_LOGIC"
        if v.startswith("TALK_"):
            return "str_TALK"
        if v.startswith("Actor/"):
            return "str_ActorPath"
        if v.startswith("QuestDialogue/"):
            return "str_DialogPath"
        return "str"
    if isinstance(v, list):
        return f"list[{len(v)}]" if not v else f"list_{_value_tag(v[0])}"
    if isinstance(v, dict):
        return "dict"
    if v is None:
        return "null"
    return "unknown"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", nargs="+", type=int, default=None,
                        help="只处理指定的 mainQuest ID")
    parser.add_argument("--report", action="store_true",
                        help="只生成报告，不写输出文件")
    args = parser.parse_args()

    if not QUEST_DIR.is_dir():
        print(f"[!] not found: {QUEST_DIR}", file=sys.stderr)
        sys.exit(1)

    files = sorted(QUEST_DIR.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
    if args.sample:
        wanted = {f"{i}.json" for i in args.sample}
        files = [f for f in files if f.name in wanted]

    if not args.report:
        OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[+] processing {len(files)} files...")
    unknown_counter = Counter()
    type_consistency = {}
    written = 0
    err = 0

    for f in files:
        try:
            with f.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            clean = deobfuscate(data, unknown_counter, type_consistency)
            if not args.report:
                out_path = OUT_DIR / f.name
                with out_path.open("w", encoding="utf-8") as fp:
                    json.dump(clean, fp, indent=2, ensure_ascii=False)
                written += 1
        except Exception as e:
            print(f"[!] {f.name}: {e}", file=sys.stderr)
            err += 1

    print(f"\n=== Summary ===")
    print(f"  written:  {written}")
    print(f"  errors:   {err}")
    print(f"  mapped keys: {len(KEY_MAP)}")
    print(f"  unique unknown keys: {len(unknown_counter)}")

    if unknown_counter:
        print(f"\n=== Unknown keys (top 30) ===")
        for k, n in unknown_counter.most_common(30):
            tags = sorted(type_consistency.get(k, set()))
            tags_str = ", ".join(tags)
            print(f"  {n:>7}  {k}  →  types: {tags_str}")

    print(f"\n=== Type consistency check ===")
    inconsistent = []
    for k, tags in type_consistency.items():
        # 过滤掉明显不重要的差异（比如 list_int 和 list_bigint）
        if k in KEY_MAP:
            simple = {t.split('_')[0] if '_' in t else t for t in tags}
            if len(simple) > 1 and "null" not in simple:
                inconsistent.append((k, tags))
    if inconsistent:
        print(f"  [!] {len(inconsistent)} known keys have inconsistent value types:")
        for k, tags in inconsistent[:10]:
            real = KEY_MAP.get(k, k)
            print(f"    {k} ({real}): {sorted(tags)}")
    else:
        print(f"  [+] all {len(KEY_MAP)} known keys have consistent types ✓")


if __name__ == "__main__":
    main()
