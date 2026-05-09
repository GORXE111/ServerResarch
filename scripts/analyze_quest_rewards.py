"""
分析任务奖励完整流程：
- MainQuestExcelConfigData.rewardIdList → RewardExcelConfigData → 实际物品
- 物品 ID → MaterialExcel/AvatarExcel/WeaponExcel 翻译名字
- 按任务类型 (WQ/LQ/IQ/EQ/AQ) 分组统计

用法: python scripts/analyze_quest_rewards.py
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "GenshinData" / "ExcelBinOutput"
TM_PATH = REPO / "GenshinData" / "TextMap" / "TextMapCHS.json"


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main():
    print("[+] loading data...")
    mainquests = load_json(DATA / "MainQuestExcelConfigData.json")
    rewards = load_json(DATA / "RewardExcelConfigData.json")
    materials = load_json(DATA / "MaterialExcelConfigData.json")
    textmap = load_json(TM_PATH)

    # 构建查表
    reward_map = {r["rewardId"]: r for r in rewards}
    item_name = {}
    for m in materials:
        h = m.get("nameTextMapHash", 0)
        n = textmap.get(str(h))
        if n:
            item_name[m.get("id")] = n
    # 简单加几个货币 ID（virtual currency, 不在 MaterialExcel 里）
    item_name.setdefault(101, "角色经验")
    item_name.setdefault(102, "冒险阅历")
    item_name.setdefault(103, "星尘")
    item_name.setdefault(104, "星辉")
    item_name.setdefault(105, "好感经验")
    item_name.setdefault(106, "原粹树脂")
    item_name.setdefault(107, "传说钥匙")
    item_name.setdefault(201, "原石")
    item_name.setdefault(202, "摩拉")
    item_name.setdefault(203, "创世结晶")
    item_name.setdefault(204, "尘歌壶币")

    quest_title = {}
    for q in mainquests:
        h = q.get("titleTextMapHash", 0)
        t = textmap.get(str(h), "")
        quest_title[q["id"]] = t

    print(f"  {len(mainquests)} mainquests / {len(rewards)} rewards / {len(materials)} materials\n")

    # 1. 按任务类型分组
    by_type = defaultdict(list)
    no_reward_by_type = defaultdict(int)
    for q in mainquests:
        qtype = q.get("type") or "None"
        if q.get("rewardIdList"):
            by_type[qtype].append(q)
        else:
            no_reward_by_type[qtype] += 1

    print(f"=== 任务类型 vs 是否有奖励 ===")
    types = sorted(set(list(by_type.keys()) + list(no_reward_by_type.keys())))
    for t in types:
        with_r = len(by_type.get(t, []))
        without_r = no_reward_by_type.get(t, 0)
        total = with_r + without_r
        rate = with_r / total * 100 if total > 0 else 0
        print(f"  {t:<6}  total={total:>4}  with_reward={with_r:>4} ({rate:.1f}%)")

    # 2. 全部奖励物品分布
    print(f"\n=== 任务奖励物品 top 20 ===")
    item_freq = Counter()  # itemId -> 出现在多少 reward 里 (粗略次数)
    item_amount = Counter()  # itemId -> 累计数量

    for q in mainquests:
        for rid in q.get("rewardIdList", []) or []:
            r = reward_map.get(rid)
            if not r:
                continue
            for entry in r.get("rewardItemList", []):
                if not entry:
                    continue
                iid = entry.get("itemId")
                cnt = entry.get("itemCount", 0)
                if iid:
                    item_freq[iid] += 1
                    item_amount[iid] += cnt

    for iid, n in item_freq.most_common(20):
        name = item_name.get(iid, "?")
        total_amount = item_amount[iid]
        avg_amount = total_amount / n if n > 0 else 0
        print(f"  {n:>5}次  itemId={iid:>7}  {name:<25}  累计{total_amount:>10,}  均{avg_amount:>8.0f}/任务")

    # 3. 按任务类型看奖励量级
    print(f"\n=== 各类型任务奖励金额（摩拉/原石/冒险阅历）===")
    for t in ["AQ", "LQ", "WQ", "EQ", "IQ"]:
        if t not in by_type:
            continue
        mora_total = 0
        primogem_total = 0
        ar_exp_total = 0
        n = 0
        for q in by_type[t]:
            for rid in q.get("rewardIdList", []) or []:
                r = reward_map.get(rid)
                if not r: continue
                for entry in r.get("rewardItemList", []):
                    if not entry: continue
                    iid = entry.get("itemId")
                    cnt = entry.get("itemCount", 0)
                    if iid == 202: mora_total += cnt
                    elif iid == 201: primogem_total += cnt
                    elif iid == 102: ar_exp_total += cnt
            n += 1
        print(f"  {t}:  {n} 个任务  摩拉={mora_total:,}  原石={primogem_total:,}  冒险阅历={ar_exp_total:,}  | 均: 摩拉{mora_total//max(n,1):,}/原石{primogem_total//max(n,1)}/AR{ar_exp_total//max(n,1)}")

    # 4. 高价值任务 top 10 (按原石总量)
    print(f"\n=== 单任务原石奖励 top 10 ===")
    quest_primogem = []
    for q in mainquests:
        primogem = 0
        for rid in q.get("rewardIdList", []) or []:
            r = reward_map.get(rid)
            if not r: continue
            for entry in r.get("rewardItemList", []):
                if not entry: continue
                if entry.get("itemId") == 201:
                    primogem += entry.get("itemCount", 0)
        if primogem > 0:
            quest_primogem.append((primogem, q))

    quest_primogem.sort(key=lambda x: -x[0])
    for primogem, q in quest_primogem[:10]:
        title = quest_title.get(q["id"], "?")[:30]
        qtype = q.get("type", "?")
        print(f"  原石 {primogem:>4}  type={qtype}  id={q['id']}  {title}")

    # 5. 主线魔神任务（AQ 类型 + 标志性任务）的奖励详情
    print(f"\n=== 几个标志性任务的具体奖励 ===")
    for qid in [351, 363, 372, 405, 3003, 3022, 11019, 12039]:
        q = next((q for q in mainquests if q["id"] == qid), None)
        if not q: continue
        title = quest_title.get(qid, "?")
        rids = q.get("rewardIdList", []) or []
        print(f"\n  [{q.get('type')}] MainQuest {qid} - {title}")
        for rid in rids:
            r = reward_map.get(rid)
            if not r: continue
            print(f"    reward {rid}:")
            for entry in r.get("rewardItemList", []):
                if not entry: continue
                iid = entry.get("itemId")
                cnt = entry.get("itemCount", 0)
                name = item_name.get(iid, "?")
                print(f"      {iid:>7}  {name:<20}  × {cnt:,}")


if __name__ == "__main__":
    main()
