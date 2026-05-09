# 28 · 任务奖励深度专题 · 配表 → 物品 的完整数据流

虽然 notes/15（经济）和 notes/03（运行时流程）涉及过任务奖励，但**任务奖励作为单独的"线"** 跨多个数据源、多种任务类型、多种发放方式——值得单独深挖。本笔记用现有工具做端到端实证：从配表追到具体物品，含统计分析。

> 工具：`scripts/analyze_quest_rewards.py`（新增）  
> 数据：`MainQuestExcelConfigData.json` + `RewardExcelConfigData.json` + `MaterialExcelConfigData.json` + `WeaponExcelConfigData.json` + `TextMapCHS`

---

## 1. 任务奖励的 4 条数据路径

```
                    [玩家完成任务]
                          │
        ┌─────────────────┼────────────────┬────────────────┐
        │                 │                │                │
   ① MainQuest        ② SubQuest       ③ finishExec    ④ DungeonDrop
   .rewardIdList     .gainItems         里的"给/扣"      (副本通关)
        │                 │                │                │
        ↓                 ↓                ↓                ↓
   RewardExcel        直接配表          SET_QUEST_VAR     DungeonDrop
   (rewardId →        (itemId+count)     UPDATE_PARENT_  (随机+概率)
    9 slot list)                         REWARD_INDEX
        │                 │                │                │
        └─────────────────┴────────────────┴────────────────┘
                          ↓
              Inventory.addItem / addItemParamDatas
                          ↓
                    ActionReason.QuestReward / QuestItem
                          ↓
              背包 + 客户端弹窗 + 触发 Quest/BP/Activity 事件
```

**4 条路径并存**——每个任务可能用其中一种或组合：

| 路径 | 触发时机 | 用途 | 代码 |
|---|---|---|---|
| ① rewardIdList | MainQuest 完成 | 主奖励包 | GameMainQuest.finish (notes/03) |
| ② gainItems | SubQuest 完成 | 步骤小奖励 | GameQuest.finish (notes/03) |
| ③ finishExec exec | SubQuest 任意时机 | 解锁/状态变更 | ExecXxx (notes/02) |
| ④ DungeonDrop | 副本完成 | 副本特有奖励 | DungeonManager (notes/19) |

---

## 2. RewardId 的命名规律（实证发现）

通过分析 `MainQuestExcelConfigData.json` 的 1390 个含奖励任务，**发现命名空间约定**：

```
rewardId = 100000 + mainQuestId   (大部分情况)

举例:
  MainQuest 351   → reward 100351
  MainQuest 3022  → reward 103022
  MainQuest 11019 → reward 111019
  MainQuest 12039 → reward 112039
```

**为什么这样命名？**
- 一眼看出"这个 reward 属于哪个任务"
- 调试 / 客服查日志友好
- 避免 reward id 冲突（不同任务的 reward 自然不同）

**反例**：通用奖励（如等阶突破）用专属段：
```
rewardId 25001..25011  → "冒险等阶突破" 系列（每次 100 原石）
rewardId 33xxx          → 通用支线奖励
```

---

## 3. RewardData 的 9-slot 数组结构

```jsonc
// reward 100351 (流浪者的足迹奖励) 真实数据
{
    "rewardId": 100351,
    "rewardItemList": [
        { "itemId": 102, "itemCount": 225 },   // 冒险阅历
        {},                                    
        { "itemId": 202, "itemCount": 975 },   // 摩拉
        {},
        {},
        {},
        { "itemId": 101, "itemCount": 500 },   // 角色经验
        {},
        {}
    ]
}
```

**固定 9 个 slot**——空 slot 用 `{}` 占位，加载时过滤（`RewardData.onLoad`）：

```java
@Override
public void onLoad() {
    rewardItemList = rewardItemList.stream().filter(i -> i.getId() > 0).toList();
}
```

**为什么 9 slot 而非动态长度？**
- 客户端 UI 固定布局（最多显示 9 个奖励 icon）
- 配表工具兼容性（Excel 列数固定）
- 序列化效率（fixed-size 结构比变长简单）

---

## 4. MainQuest 类型分布与奖励差异（实证统计）

| 类型 | 含义 | 总数 | 含奖励 | 平均摩拉 | 平均原石 |
|---|---|---|---|---|---|
| **WQ** | World Quest（世界任务）| 1504 | 935 | **13,990** | 13 |
| **LQ** | Legendary Quest（传说任务）| 320 | 148 | **27,116** | 15 |
| **EQ** | Event Quest（活动任务）| 159 | 125 | 22,960 | 31 |
| **IQ** | ？类型（疑为 Investigation Quest）| 218 | 52 | 2,144 | 0 |
| **None** | 无类型（含 AQ 魔神 + 测试）| 159 | -- | -- | -- |

### 关键观察

1. **传说任务摩拉是世界任务的近 2 倍**（27k vs 14k）—— 鼓励玩家做传说
2. **活动任务原石密度最高**（31/任务）—— 限时内容补偿
3. **IQ 任务奖励普遍很低**（2k 摩拉，无原石）—— 应该是辅助/调查类小任务
4. **AQ 魔神任务被归到 type=None**—— 类型字段不严谨（看 Sumeru 章节别有方式判断）

---

## 5. 真实任务奖励详情（我们之前讲过的几个）

### 351 流浪者的足迹（Genshin 序章）

```
冒险阅历 × 225  + 摩拉 × 975  + 角色经验 × 500
（无原石！这是序章）
```

→ **新手期奖励小**——避免一开始就溢出，引导玩家做后续任务。

### 3022 识藏日（须弥 AQ 章节关键幕）

```
冒险阅历 × 1,200  + 原石 × 30  + 摩拉 × 47,625
+ 大英雄的经验 × 4  + 精锻用魔矿 × 8
```

→ **典型 AQ 章节奖励**：每 2-3 个任务给一次 30 原石，加大量摩拉 + 武器/角色养成材料。

### 11019 知人知面（夜兰传说）

```
冒险阅历 × 575  + 摩拉 × 34,075  
+ 大英雄的经验 × 4  + 精锻用魔矿 × 7
（无原石）
```

→ **传说任务摩拉密度高**，但单个任务通常无原石。

### 12039 穷途望归路（万叶传说终幕）

```
冒险阅历 × 575  + 原石 × 60  + 摩拉 × 38,100
+ 「勤劳」的指引 × 5  + 笼钓瓶一心 × 1 (4 星武器!)
+ 大英雄的经验 × 4  + 精锻用魔矿 × 8
```

→ **传说任务终幕有重磅奖励**：60 原石 + **赠送 4 星武器** + 培养材料。这是为什么传说任务必做。

### 372 那个绿色的家伙（早期支线）

```
冒险阅历 × 250  + 摩拉 × 5,450 + 角色经验 × 2,850 + 精锻用良矿 × 5
（无原石，5 星支线）
```

→ **早期支线奖励适度**——主要是养成材料和摩拉。

---

## 6. Sumeru AQ 章节的"原石节奏"实证

实测 Sumeru 章节 30 个 AQ 任务（3001-3030），**原石分布**：

```
3001  疗养观察                  原石=0     ←
3002  痼疾                      原石=0
3003  缄默的求知者              原石=30    ★
3004  智慧之神的踪影            原石=0
3005  失物匿于繁华              原石=0
3006  近在咫尺的目标            原石=30    ★
3007  终将到来的花神诞祭        原石=0
3008  已然来临的花神诞祭        原石=0
3009  流转存续的花神诞祭        原石=30    ★
3010-3011                       原石=0
3012  终将结束的花神诞祭        原石=30    ★
3013  黎明                      原石=0
3014  空幻回响的花神诞祭        原石=30    ★
3016  如凯旋的英雄一般          原石=0
3017-3019                       原石=0,0,30
3020-3021                       原石=0,30
3022  识藏日                    原石=30    ★
3024-3025                       原石=0,30
3026                            原石=0
3028  意识之舟所至之处          原石=30    ★
3029-3030                       原石=0,0
```

**规律**：
- **每 2-3 个任务给一次 30 原石**——节奏稳定
- 30 个 AQ 共约 **10 次原石奖励 = 300 原石/章节**（够 2 抽）
- 配合"日常任务"+"成就"+"宝箱" 凑足玩家氪金前的"白嫖原石"

→ **原石密度精心设计**——既给到鼓励玩家做主线，又稀缺到驱动氪金。

---

## 7. 顶级原石奖励 Top 10

```
原石 150  WQ id=71005  靖世九柱             (璃月隐藏支线)
原石 100  WQ id=25001  冒险等阶突破·一       (AR 阶段 1)
原石 100  WQ id=25005  冒险等阶突破·二       (AR 阶段 2)
原石 100  WQ id=25009  冒险等阶突破·三       (AR 阶段 3)
原石 100  WQ id=25011  冒险等阶突破·四       (AR 阶段 4)
原石  80  WQ id=73500  被嫌弃的木刻
原石  80  WQ id=79026  极夜幻想剧·王女执剑记！
原石  60  LQ id=454    卢皮卡，即是命运的选择 (Razor 传说)
原石  60  LQ id=463    凯亚的收获             (Kaeya 传说)
原石  60  LQ id=466    暗夜英雄的不在场证明   (Kaeya 传说续)
```

**观察**：
- "**靖世九柱**"是已知最高单任务原石（150）——璃月稻妻交界的解谜支线
- "**冒险等阶突破**"系列是新玩家的常规丰厚原石源
- 传说任务终幕约 60 原石（如 Razor、Kaeya 系列）
- AR 突破任务和稀有支线是主要的"原石爆炸点"

---

## 8. 完整代码追踪：从 finish() 到 inventory

```java
// GameMainQuest.finish (notes/03 看过)
public void finish(boolean isManualFinish) {
    ...
    
    // ★ Reward delivery
    val mainQuestData = getMainQuestData();
    if (mainQuestData != null && mainQuestData.getRewardIdList() != null 
        && mainQuestData.getRewardIdList().length > rewardIndex) {
        
        int rewardId = mainQuestData.getRewardIdList()[rewardIndex];   // ★ 取分支档位
        RewardData rewardData = GameData.getRewardDataMap().get(rewardId);
        
        if (rewardData != null) {
            getOwner().getInventory().addItemParamDatas(
                rewardData.getRewardItemList(),    // 9 slot 已 onLoad 过滤
                ActionReason.QuestReward            // ★ 审计标签
            );
        }
    }
}
```

**rewardIndex 的可变性**：`UPDATE_PARENT_QUEST_REWARD_INDEX` exec 可以在任意 SubQuest 完成时改这个值——**用于"分支结局多奖励"**：

```jsonc
// MainQuest 配表
"rewardIdList": [
    100001,   // index 0: 默认结局 - 50 原石
    100002,   // index 1: 救了村民 - 80 原石 + 4 星武器
    100003    // index 2: 阴谋成功 - 100 原石 + 限定皮肤
]

// SubQuest 完成时
"finishExec": [
    { "type": "QUEST_EXEC_UPDATE_PARENT_QUEST_REWARD_INDEX", "param": ["1"] }
]
```

→ **同一任务不同选择 = 不同奖励档位**。corpus 里 85 次 `UPDATE_PARENT_QUEST_REWARD_INDEX` 印证这是真实使用的机制。

---

## 9. SubQuest gainItems 的去向（数据缺失谜题）

`SubQuestData.java` schema 明确有：
```java
private List<GainItem> gainItems;
```

`GameQuest.finish()` 实际调用：
```java
val gainItems = questData.getGainItems();
if (gainItems != null && gainItems.size() > 0) {
    gainItems.forEach(item -> 
        getOwner().getInventory().addItem(item.getItemId(), item.getCount(), 
            ActionReason.QuestItem));
}
```

→ 代码层面 100% 支持，但 **BinOutput/Quest 反混淆数据里几乎没有**。原因：
1. **大多 SubQuest 不给小奖励**（只在 MainQuest 完成时给）
2. 仅个别任务（如教学引导）会用 gainItems 单步发奖
3. 我们的反混淆 key 表可能没覆盖 gainItems 字段（需要更多数据样本）

→ **gainItems 是次要发奖路径**，rewardIdList 才是主流。

---

## 10. ActionReason 完整审计标签

任务奖励涉及的 ActionReason（按用途分类）：

```java
ActionReason.QuestItem(1)        // SubQuest.gainItems 步骤奖励
ActionReason.QuestReward(2)      // MainQuest.rewardIdList 完成奖励
ActionReason.DungeonFirstPass    // 副本首通奖励
ActionReason.DungeonPass         // 副本重复通关
ActionReason.DailyTaskScore      // 委托积分奖励
ActionReason.DailyTaskHost       // 委托主奖励
ActionReason.RandTaskHost        // 随机任务主奖励
ActionReason.AreaExploreReward   // 探索度奖励（间接含任务）
```

→ **同一个"任务奖励"概念**根据具体场景拆成 8+ 个不同 ActionReason——客服查日志能精确知道"这件物品是哪一类任务的哪种发放路径"。

---

## 11. 任务奖励与其他系统的事件触发链

`Inventory.addItem` 内部触发的反向事件（notes/15 看过）：

```java
private void triggerAddItemEvents(GameItem result) {
    // 任务奖励发放后会触发:
    
    // 1. 战令进度
    getPlayer().getBattlePassManager().triggerMission(
        WatcherTriggerType.TRIGGER_OBTAIN_MATERIAL_NUM, 
        result.getItemId(), result.getCount());
    
    // 2. 任务（"收集 N 个 X"任务自动推进）
    getPlayer().getQuestManager().queueEvent(
        QuestContent.QUEST_CONTENT_OBTAIN_ITEM, 
        result.getItemId(), result.getCount());
    getPlayer().getQuestManager().queueEvent(
        QuestContent.QUEST_CONTENT_OBTAIN_VARIOUS_ITEM, 
        result.getItemId(), result.getCount());
    
    // 3. 任务条件 acceptCond
    getPlayer().getQuestManager().queueEvent(
        QuestCond.QUEST_COND_PACK_HAVE_ITEM, 
        result.getItemId(), result.getCount());
}
```

→ **任务完成发奖** → 奖励物品入背包 → **触发 BP/其他任务进度推进**。形成多层级联：

```
任务 A 完成 → 发奖（含某材料 5 个）
   ↓
材料入背包
   ↓
   ├─ BP "获得材料 N 次" mission +5
   ├─ Quest "收集材料 X" quest progress 推进
   └─ Quest acceptCond "PACK_HAVE_ITEM" 触发 → 任务 B 自动接取
```

---

## 12. 任务奖励的运营调控点

可调控的"杠杆"（运营/策划手中的工具）：

| 杠杆 | 修改方式 | 影响 |
|---|---|---|
| 整个 reward 替换 | 改 `MainQuest.rewardIdList` | 该任务奖励完全替换 |
| reward 内容调整 | 改 `RewardExcelConfigData.json` 某 reward 的 itemList | **跨多任务同时影响**（同 rewardId 多任务复用时）|
| 分支奖励调整 | 改 `rewardIdList` 中各 index 的 rewardId | 影响选择带来的差异 |
| 任务奖励触发条件 | 改 SubQuest 的 finishExec UPDATE_REWARD_INDEX | 改"什么选择给什么档位" |
| 奖励物品本身改造 | 改 `MaterialExcel`（如经验书改加经验数）| 全局影响所有发放此物品的路径 |

→ **运营可在 5 个层级调控**——粒度从单任务到全局。

---

## 13. 设计哲学：稀缺 + 节奏 + 分级

### 13.1 稀缺：原石密度严格控制

- **AQ 章节** 30 任务 → ~10 次原石（每次 30）= **300 原石/章节**
- **传说任务** 终幕约 60 原石
- **WQ 大多无原石**，少数特殊任务 80-150
- **每个原石都是商业敏感**——绝不"管够"

### 13.2 节奏：每 2-3 个任务给一次

AQ 章节实测：**给原石的任务和不给原石的任务交替**——保持新鲜感而非"做完一长串才给"。心理学：**间歇强化优于连续强化**。

### 13.3 分级：传说任务 > 主线 > 支线

- LQ 摩拉密度 27k > WQ 14k > IQ 2k
- LQ 终幕送 4 星武器（笼钓瓶一心给万叶）
- AQ 主线给原石 + 大量培养材料
- WQ 大多只给摩拉 + 经验

→ **任务类型决定奖励量级**——玩家通过奖励差异感知"重要性"。

### 13.4 闭环：奖励 → 触发新任务

奖励发放本身又是事件源，可以**触发下游任务**（如 PACK_HAVE_ITEM acceptCond）。形成"奖励 → 解锁新任务 → 完成 → 又奖励"的闭环。

---

## 14. 与 Gacha 系统的"原石经济"对比

```
[原石的来源]
- 任务奖励:    AR 阶段突破 (100×4) + AQ 章节 (300×N) + LQ 终幕 (60×N) + 活动 (80-150)
- 每日委托:    60 原石/天 = 1800/月
- 深境螺旋:    600 原石/期 (一期 2 周)
- 邮件补偿:    各种活动结束补偿
- 成就:        每成就 5-20 原石
- 充值:        商业 ↑
                ↓
            合计 / 期望 / 一年: 数千到上万原石

[原石的消耗]
- 抽卡:       160 原石 / 抽
- 树脂购买:   50/100/150... 原石 / 6 树脂 (notes/04)
- 商店稀有商品:  各种限定 100-200 原石
                ↑
            消耗远大于产出 → 商业转化
```

→ **任务奖励是原石经济的"白嫖入口"**——精心控制总量，让玩家"差一点点抽到 UP 角色"，触发氪金。

---

## 15. 可扩展性思考

### 假设你要做一个类似系统，应该如何设计？

**必须有**：
1. ✅ rewardId 命名空间（`100000 + questId` 这种约定）
2. ✅ 9-slot 固定结构（UI 友好 + Excel 兼容）
3. ✅ 多档位 rewardIdList + UPDATE_REWARD_INDEX 分支
4. ✅ 通过 `Inventory.addItem(ActionReason.QuestReward)` 统一入口
5. ✅ 反向触发 BP/Quest 事件
6. ✅ 任务类型字段决定奖励量级模板

**可选**：
- ⚠️ SubQuest gainItems（如果不需要"步骤奖励"可省）
- ⚠️ Trial Avatar 临时角色（特殊副本类）
- ⚠️ 章节性原石密度规划（如果是 LiveOps 游戏才必要）

**避免**：
- ❌ 让多个系统各自实现"加道具"（会失控）
- ❌ 客户端可见概率/数量（防作弊）
- ❌ 奖励直接写在 SubQuest 配表里硬编码（应通过 RewardData 复用）

---

## 16. 数据规模感（实证）

```
MainQuestExcelConfigData:  2,360 任务  (1,390 含奖励)
RewardExcelConfigData:     7,165 reward 条目
任务专用 reward (100xxx 段):  ~1,500 条
共享 reward (33xxx, 25xxx 段):  ~50 条
平均每 reward 物品数:        ~3 种（最多 9 种）

任务类型分布:
  WQ (世界):  1504  (63.7%)
  None (AQ): 159  (6.7%)
  LQ (传说): 320  (13.6%)
  IQ:        218  (9.2%)
  EQ (活动): 159  (6.7%)
```

---

## 17. 总结：任务奖励是"小型经济系统"

任务奖励看似简单（"完成任务给道具"），实际是**完整的小型经济系统**：

```
[配表层]   MainQuestExcel + RewardExcel + ItemExcel + 任务类型
[逻辑层]   GameMainQuest.finish + UPDATE_REWARD_INDEX + Inventory.addItem
[审计层]   8+ 种 ActionReason
[联动层]   triggerAddItemEvents → BP/Quest/Activity 反向推进
[运营层]   5 个层级的奖励调控杠杆
[商业层]   原石密度严格控制 + 章节节奏 + 任务类型分级
```

**理解任务奖励 = 理解这套游戏的整个商业循环**：
- 玩家投入时间做任务
- 获得原石（白嫖）
- 想抽更多 UP 角色 → 原石不够 → 充值
- 充值后继续做任务（不是因为奖励，而是因为内容）
- 形成留存 + 商业的双循环

→ **任务奖励 = 留存与变现的交汇点**。

---

## 18. 工具复现

```bash
# 跑全量奖励统计
python scripts/analyze_quest_rewards.py
```

输出包含：
- 任务类型分布
- 各类型平均奖励金额
- 顶级原石任务 top 10
- 标志性任务的具体奖励详情
- Sumeru 章节原石分布

---

## 19. 与之前笔记的关系

| 之前笔记 | 关系 |
|---|---|
| notes/03 运行时流程 | GameMainQuest.finish 调用 RewardData 的代码追踪（已细化）|
| notes/15 经济系统 | RewardData 9-slot 结构 + ActionReason 100+ |
| notes/16 Combat | 奖励里的角色经验直接 upgradeAvatar (notes/15) |
| notes/19 Dungeon | 副本 reward 走另一条路径 DungeonDrop |
| notes/21 Gacha | 原石的来源 (任务) 与去向 (抽卡) 形成商业循环 |
| notes/24 Avatar 升级 | 任务奖励里的经验书/材料用于角色养成 |

→ **本笔记是"奖励视角"的纵切**——把任务、经济、商业、运营串起来看。

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/GameMainQuest.java`（finish 调用）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/GameQuest.java`（gainItems 处理）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/data/excels/RewardData.java`（reward 实体）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/inventory/Inventory.java:104` (addItem)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/exec/ExecUpdateParentQuestRewardIndex.java`
- 数据：`MainQuestExcelConfigData.json`, `RewardExcelConfigData.json`
- 工具：`scripts/analyze_quest_rewards.py`
