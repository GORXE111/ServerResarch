# Inventory 物品系统全景剖析

> 第 38 篇：经济中枢 —— 任务奖励 / 副本掉落 / 商店购买 / 邮件附件 / 抽卡 / 锻造 / 烹饪 / 摘草 / 开宝箱 **全部走这一个入口**。

---

## 0. 为什么这一篇重要

前面笔记里 Inventory 被引用 30+ 次但从未专门解剖：
- notes/15 经济系统提到 `Inventory.addItem` 是统一入口
- notes/25 制作系统说"产出走 addItem"
- notes/13 邮件附件说"领取 → addItem"
- notes/21 抽卡说"出货 → addItem"
- notes/32 怪物掉落说"拾取 → addItem"

但 **addItem 内部到底干了什么**？为什么"摩拉 / 原石 / 体力 / 角色经验书"都是物品但行为不同？容量上限怎么管？

这一篇专攻 Inventory 内部机制。

---

## 1. 4 大 ItemType + 30+ MaterialType

### 1.1 ItemType（顶层 7 种）

`ItemType.java`：
```java
public enum ItemType {
    ITEM_NONE       (0),
    ITEM_VIRTUAL    (1),   // ★ 摩拉 / 原石 / 角色经验 / 体力 (没有"物品实体")
    ITEM_MATERIAL   (2),   // ★ 材料 / 食物 / 任务道具 / 角色头像
    ITEM_RELIQUARY  (3),   // ★ 圣遗物
    ITEM_WEAPON     (4),   // ★ 武器
    ITEM_DISPLAY    (5),   // 展示物 (头像 / 命之星座)
    ITEM_FURNITURE  (6);   // 家具
}
```

**关键区分**：
- ITEM_VIRTUAL —— 没有 stack 概念，**单纯数字**（如玩家.mora 字段）
- ITEM_MATERIAL —— stackable，存在 GameItem 实例
- ITEM_WEAPON / ITEM_RELIQUARY —— 独立 GameItem（每件都有 guid）

### 1.2 MaterialType（材料子类 40+ 种）

```java
MATERIAL_NONE (0)
MATERIAL_FOOD (1),                  // 食物 (烹饪产出)
MATERIAL_QUEST (2),                 // 任务道具
MATERIAL_EXCHANGE (4),              // 兑换物
MATERIAL_CONSUME (5),               // 消耗品
MATERIAL_EXP_FRUIT (6),             // 经验书
MATERIAL_AVATAR (7),                // ★ 角色头像/卡片 (使用即解锁角色)
MATERIAL_ADSORBATE (8),
MATERIAL_CRICKET (9),               // 蟋蟀
MATERIAL_ELEM_CRYSTAL (10),
MATERIAL_WEAPON_EXP_STONE (11),     // 武器经验石
MATERIAL_CHEST (12),                // 宝箱礼包
MATERIAL_RELIQUARY_MATERIAL (13),   // 圣遗物升级材料
MATERIAL_AVATAR_MATERIAL (14),      // 角色突破材料
MATERIAL_NOTICE_ADD_HP (15),
MATERIAL_SEA_LAMP (16),             // 海灯节专用
MATERIAL_SELECTABLE_CHEST (17),     // 可选礼包
MATERIAL_FLYCLOAK (18),             // 风之翼
MATERIAL_NAMECARD (19),             // 名片
MATERIAL_TALENT (20),               // 天赋材料
MATERIAL_WIDGET (21),               // 小道具 (篝火/钓竿)
MATERIAL_CHEST_BATCH_USE (22),      // 批量礼包
MATERIAL_WOOD (25),                 // 木材
MATERIAL_FURNITURE_FORMULA (27),    // 家具图纸
MATERIAL_COSTUME (30),              // 角色服装
MATERIAL_HOME_SEED (31),            // 家园种子
MATERIAL_FISH_BAIT (32),            // 鱼饵
MATERIAL_FISH_ROD (33),             // 鱼竿
MATERIAL_FIREWORKS (35),            // 烟花
MATERIAL_BGM (36),                  // 家园 BGM
MATERIAL_SPICE_FOOD (37),           // 香料食物
MATERIAL_ACTIVITY_ROBOT (38),       // 活动机器人
...
```

→ **每个版本都加新 MaterialType**（如须弥的 MATERIAL_DESHRET_MANUAL=46）。

### 1.3 特殊 MaterialType：自动使用类

```java
case MATERIAL_AVATAR:        // 角色头像 → 自动解锁角色
case MATERIAL_FLYCLOAK:      // 风之翼 → 自动解锁
case MATERIAL_COSTUME:       // 服装 → 自动解锁
case MATERIAL_NAMECARD:      // 名片 → 自动解锁
    // ↑ 这 4 类不能直接进背包, 必须 isUseOnGain=true 自动消费
```

→ "**获得即使用**"机制：拿到角色头像不是放在背包，而是直接解锁角色。

---

## 2. Inventory 字段全图

`Inventory.java`：
```java
public class Inventory extends BasePlayerManager implements Iterable<GameItem> {
    private final Long2ObjectMap<GameItem> store;            // ★ 主存储 (guid → item)
    private final Int2ObjectMap<InventoryTab> inventoryTypes;// ★ 4 个分类 tab
    private final Int2ObjectMap<VirtualCurrencyHandlerEntry> virtualCurrencyHandlers;
    
    public Inventory(Player player) {
        super(player);
        this.store = new Long2ObjectOpenHashMap<>();
        this.inventoryTypes = new Int2ObjectOpenHashMap<>();
        
        // 4 个分类 tab
        this.createInventoryTab(ItemType.ITEM_WEAPON,    new EquipInventoryTab(INVENTORY_LIMITS.weapons));     // 默认 2000
        this.createInventoryTab(ItemType.ITEM_RELIQUARY, new EquipInventoryTab(INVENTORY_LIMITS.relics));      // 默认 1500
        this.createInventoryTab(ItemType.ITEM_MATERIAL,  new MaterialInventoryTab(INVENTORY_LIMITS.materials));// 默认 2000
        this.createInventoryTab(ItemType.ITEM_FURNITURE, new MaterialInventoryTab(INVENTORY_LIMITS.furniture));// 默认 2000
    }
}
```

### 2.1 双层索引

```
[Level 1] Inventory.store: guid → GameItem    ← 主索引，按 guid 找物品
[Level 2] InventoryTab: 按 ItemType 分页       ← 4 个 tab 限制容量
            ├── EquipInventoryTab (武器/圣遗物)
            └── MaterialInventoryTab (材料/家具)
```

**为什么需要双层**：
- store —— 全局 guid 索引（O(1) 查找物品）
- tab —— **按 ItemType 限制容量**（防止背包爆）

### 2.2 4 大 InventoryTab

| Tab | ItemType | 默认容量 |
|---|---|---|
| EquipInventoryTab (weapons) | ITEM_WEAPON | 2000 件武器 |
| EquipInventoryTab (relics) | ITEM_RELIQUARY | 1500 件圣遗物 |
| MaterialInventoryTab (materials) | ITEM_MATERIAL | 2000 种材料 |
| MaterialInventoryTab (furniture) | ITEM_FURNITURE | 2000 种家具 |

**ITEM_VIRTUAL 没有 tab** —— 因为不存"物品"，只存"数字"（在 Player 字段上）。

---

## 3. addItem 完整流程（核心）

### 3.1 入口（4 个重载）

```java
public boolean addItem(int itemId)
public boolean addItem(int itemId, int count)
public boolean addItem(int itemId, int count, ActionReason reason)
public boolean addItem(GameItem item)
public boolean addItem(GameItem item, ActionReason reason)
public boolean addItem(GameItem item, ActionReason reason, boolean forceNotify)
public boolean addItem(ItemParamData itemParam)
public boolean addItem(ItemParamData itemParam, ActionReason reason)
public void addItems(Collection<GameItem> items, ActionReason reason)   // 批量
public void addItemParams(Collection<ItemParam> items)
public void addItemParamDatas(Collection<ItemParamData> items, ActionReason reason)
```

→ **11 个重载** —— 因为输入类型多样（int/GameItem/ItemParamData/Collection）。

### 3.2 核心 putItem 流程

```java
private synchronized GameItem putItem(GameItem item) {
    var data = item.getItemData();
    if (data == null) return null;
    
    // 1. 记录历史
    this.player.getProgressManager().addItemObtainedHistory(item.getItemId(), item.getCount());
    
    // 2. 自动使用类物品 (头像/风之翼/服装/名片)
    if (data.isUseOnGain()) {
        var params = new UseItemParams(this.player, data.getUseTarget());
        params.usedItemId = data.getId();
        this.player.getServer().getInventorySystem().useItemDirect(data, params);
        return null;
    }
    
    // 3. 按 ItemType 分支
    ItemType type = item.getItemData().getItemType();
    InventoryTab tab = getInventoryTab(type);
    
    switch (type) {
        case ITEM_WEAPON:
        case ITEM_RELIQUARY:
            // 装备类: 容量检查 + 独立存
            if (tab.getSize() >= tab.getMaxCapacity()) return null;
            item.setCount(Math.max(item.getCount(), 1));
            this.putItem(item, tab);
            item.save();
            return item;
        
        case ITEM_VIRTUAL:
            // 虚拟物品: 不入 tab, 直接改 Player 数字
            this.addVirtualItem(item.getItemId(), item.getCount());
            return item;
        
        default:
            // MATERIAL / FURNITURE: stackable
            switch (item.getItemData().getMaterialType()) {
                case MATERIAL_AVATAR:    // 4 类必须 isUseOnGain
                case MATERIAL_FLYCLOAK:
                case MATERIAL_COSTUME:
                case MATERIAL_NAMECARD:
                    logger.warn("Resources error: missing isUseOnGain");
                    return null;
                default:
                    GameItem existingItem = tab.getItemById(item.getItemId());
                    if (existingItem == null) {
                        // 新种类: 占用 tab 槽位
                        if (tab.getSize() >= tab.getMaxCapacity()) return null;
                        this.putItem(item, tab);
                        item.save();
                        return item;
                    } else {
                        // 已有: 叠加 count, 上限是 stackLimit
                        existingItem.setCount(
                            Math.min(existingItem.getCount() + item.getCount(), 
                                     item.getItemData().getStackLimit()));
                        existingItem.save();
                        return existingItem;
                    }
            }
    }
}
```

### 3.3 完整决策树

```
addItem(item, reason)
    ↓
putItem
    ↓
data.isUseOnGain ?
├─ Yes → useItemDirect (角色头像/风之翼自动消费) → return
└─ No → 按 ItemType:
        ├─ WEAPON/RELIQUARY:
        │   ├─ tab 满? return null
        │   ├─ count=max(count,1) (装备只能 1 件)
        │   ├─ putItem(item, tab) + save
        │   └─ return item
        │
        ├─ VIRTUAL:
        │   ├─ 不入 tab
        │   ├─ addVirtualItem (改 Player 数字)
        │   └─ return item
        │
        └─ MATERIAL/FURNITURE:
            ├─ 4 类 isUseOnGain 漏配 → warn + return null
            ├─ 否则:
            │   ├─ 已有此 itemId? → 叠 count, 上限 stackLimit
            │   └─ 新 itemId? → tab 满? return null / 否则放入
            └─ save
```

→ 这是 grasscutter **最长的决策树之一**——但每分支逻辑清晰。

---

## 4. Virtual Items：8 种"数字物品"

### 4.1 ID 规约

```java
case 101 -> // ★ 角色经验书 (内部 ID)
    this.player.getTeamManager().getActiveTeam().stream().map(e -> e.getAvatar()).forEach(
        avatar -> ... upgradeAvatar(player, avatar, count));

case 102 -> this.player.addExpDirectly(count);  // ★ 冒险阅历

case 105 -> // ★ 友谊经验
    this.player.getTeamManager().getActiveTeam().forEach(
        avatar -> upgradeAvatarFetterLevel(... count * (isInMultiplayer ? 2 : 1)));

case 106 -> this.player.getResinManager().addResin(count);   // ★ 体力 (树脂)
case 107 -> this.player.addLegendaryKey(count);              // ★ 传说任务钥匙

case 201 -> this.player.setPrimogems(this.player.getPrimogems() + count);  // ★ 原石
case 202 -> this.player.setMora(this.player.getMora() + count);            // ★ 摩拉
case 203 -> this.player.setCrystals(this.player.getCrystals() + count);    // ★ 创世结晶 (氪金)
case 204 -> this.player.setHomeCoin(this.player.getHomeCoin() + count);    // ★ 洞天宝钱

default -> // 其他 itemId: 走插件注册的 VirtualCurrencyHandler
    if (virtualCurrencyHandlers.containsKey(itemId)) { ... }
```

### 4.2 8 大虚拟币

| itemId | 含义 | 玩家可见名 | 存在哪 |
|---|---|---|---|
| 101 | 角色经验 | 大英雄的经验 等 (汇总) | 应用到所有 active 角色 |
| 102 | 冒险阅历 | (AR 经验) | Player.exp |
| 105 | 友谊经验 | (好感度) | 应用到所有 active 角色 |
| 106 | 体力 | 原粹树脂 | Player.resinManager |
| 107 | 传说钥匙 | 传说任务钥匙 | Player.legendaryKey |
| 201 | 原石 | (主要软通货) | Player.primogems |
| 202 | 摩拉 | (主要游戏货币) | Player.mora |
| 203 | 创世结晶 | (氪金通货) | Player.crystals |
| 204 | 洞天宝钱 | (家园专用) | Player.homeCoin |

→ 这 9 个 ID 是**游戏经济的核心**——所有奖励都是其中之一或材料。

### 4.3 联机时友谊经验加倍

```java
case 105 ->
    upgradeAvatarFetterLevel(... count * (this.player.isInMultiplayer() ? 2 : 1));
```

→ **联机时 105 加倍** —— 这就是"开个房一起做任务，好感度涨更快"的代码实现。

---

## 5. payItem / payItems：消费机制

### 5.1 payItem

```java
public synchronized boolean payItem(int id, int count) {
    if (this.getVirtualItemCount(id) < count) return false;   // 检查余量
    this.payVirtualItem(id, count);                            // 扣除
    return true;
}
```

### 5.2 payVirtualItem 内部

```java
private GameItem payVirtualItem(int itemId, int count) {
    switch (itemId) {
        case 201 -> player.setPrimogems(player.getPrimogems() - count);
        case 202 -> player.setMora(player.getMora() - count);
        case 203 -> player.setCrystals(player.getCrystals() - count);
        case 106 -> player.getResinManager().useResin(count);
        case 107 -> player.useLegendaryKey(count);
        case 204 -> player.setHomeCoin(player.getHomeCoin() - count);
        default -> {
            if (virtualCurrencyHandlers.containsKey(itemId)) {
                virtualCurrencyHandlers.get(itemId).modifyCurrency(-count);
                return null;
            }
            // ★ 走真实物品 (材料) 路径
            var gameItem = getInventoryTab(ItemType.ITEM_MATERIAL).getItemById(itemId);
            removeItem(gameItem, count);
            return gameItem;
        }
    }
    return null;
}
```

→ payItem **不区分虚拟/真实** —— 用同一接口扣除原石、摩拉、材料、装备升级石。

### 5.3 payItems：原子消费

```java
public synchronized boolean payItems(ItemParamData[] costItems, int quantity, ActionReason reason) {
    // ★ 步骤 1: 全部检查 (任一不够立即返回 false)
    for (ItemParamData cost : costItems)
        if (this.getVirtualItemCount(cost.getId()) < (cost.getCount() * quantity))
            return false;
    
    // ★ 步骤 2: 全部扣除 (确认能扣才动)
    for (ItemParamData cost : costItems) {
        this.payVirtualItem(cost.getId(), cost.getCount() * quantity);
    }
    return true;
}
```

**"先检查全部 → 再批量扣除"** —— 这是经典的**原子事务**模式。

**用例**：抽卡 10 连
```java
ItemParamData[] cost = { new ItemParamData(223, 1) };   // 1 缘结
payItems(cost, 10);   // 10 连 = 10 缘结
```

如果只剩 8 缘结：**不会扣 8，直接 false**——避免"扣了一半余额"问题。

---

## 6. ActionReason：190+ 个原因码

### 6.1 完整分类

`ActionReason.java`（284 行）—— **190+ 个枚举值**，覆盖所有给物品的"原因"。

#### 段位 1：核心来源 (0-102)
```
None(0), QuestItem(1), QuestReward(2), Trifle(3), Shop(4),
PlayerUpgradeReward(5), AddAvatar(6), GadgetEnvAnimal(7),
MonsterEnvAnimal(8), Compound(9), Cook(10), Gather(11),
MailAttachment(12), DungeonFirstPass(20), DungeonPass(21),
FetterOpen(25), Gacha(30), Combine(31), MonsterDie(37),
Gm(38), OpenChest(39), GadgetDie(40), MonsterChangeHp(41),
SubfieldDrop(42), TowerScheduleReward(47), TowerFloorStarReward(48),
... 共约 60 种
```

#### 段位 2：详细动作 (1001-1100)
```
PlayerUseItem(1001), DropItem(1002),
WeaponUpgrade(1011), WeaponPromote(1012), WeaponAwaken(1013),
RelicUpgrade(1014), Ability(1015), DungeonStatueDrop(1016),
AvatarUpgrade(1018), AvatarPromote(1019),
UpgradeSkill(1024), UnlockTalent(1025), UpgradeProudSkill(1026),
ForgeCost(1031), GadgetInteract(1034),
SeaLampCiMaterial(1036), BargainDeduct(1043),
BattlePassPaidReward(1044), AchievementReward(1049),
... 共约 60 种
```

#### 段位 3：活动相关 (1100-1200)
```
LunaRiteSacrifice(1108),         // 月祭
FishBite(1110), FishSucc(1111),  // 钓鱼
RogueChallengeSettle(1116),      // 幻想真境剧诗
LanternRiteDungeonReward(1128),  // 海灯节
GcgLevelReward(1143),            // 七圣召唤
AlchemySimSell(1165),            // 炼金模拟
CatcafeFeed(1173),               // 猫咖
... 共约 90 种
```

### 6.2 ActionReason 的作用

```java
public boolean addItem(GameItem item, ActionReason reason, boolean forceNotify) {
    boolean result = addItem(item);
    if (item.getItemData().getMaterialType() == MaterialType.MATERIAL_AVATAR) {
        getPlayer().sendPacket(new PacketAddNoGachaAvatarCardNotify(..., reason, item));
    }
    if (reason != null && (forceNotify || result)) {
        getPlayer().sendPacket(new PacketItemAddHintNotify(item, reason));
        //                                                  ↑ 客户端显示"获得 X 来源 Y"
    }
    return result;
}
```

→ **客户端右上角浮动提示**："任务奖励：摩拉 +1000"——这个 "任务奖励" 就是 ActionReason。

### 6.3 审计价值

ActionReason 给客服 / 数据分析提供：
- "这个玩家的原石主要哪来的？" → ActionReason 分组统计
- "副本通关 vs 任务奖励 vs 抽卡 给了多少？"
- "可疑数据：这个 itemId 通过 Gm(38) 进来 → 是不是用 GM 命令刷的？"

→ 但 grasscutter **没存到 DB**——只是 packet 通知客户端。米哈游正服肯定写日志。

---

## 7. 触发钩子：每次加/减物品触发 3 事件

### 7.1 addItem 触发

```java
private void triggerAddItemEvents(GameItem result) {
    getPlayer().getBattlePassManager().triggerMission(
        WatcherTriggerType.TRIGGER_OBTAIN_MATERIAL_NUM, result.getItemId(), result.getCount());
    getPlayer().getQuestManager().queueEvent(
        QuestContent.QUEST_CONTENT_OBTAIN_ITEM, result.getItemId(), result.getCount());
    getPlayer().getQuestManager().queueEvent(
        QuestContent.QUEST_CONTENT_OBTAIN_VARIOUS_ITEM, result.getItemId(), result.getCount());
    getPlayer().getQuestManager().queueEvent(
        QuestCond.QUEST_COND_PACK_HAVE_ITEM, result.getItemId(), result.getCount());
}
```

**4 个事件并发触发**：
1. **战令任务**：TRIGGER_OBTAIN_MATERIAL_NUM
2. **任务进度**：QUEST_CONTENT_OBTAIN_ITEM
3. **任务进度**：QUEST_CONTENT_OBTAIN_VARIOUS_ITEM
4. **任务条件**：QUEST_COND_PACK_HAVE_ITEM

→ "收集 100 个琉璃袋" 这类任务**就是通过这里触发**。

### 7.2 removeItem 触发

```java
private void triggerRemItemEvents(GameItem item, int removeCount) {
    getPlayer().getBattlePassManager().triggerMission(
        WatcherTriggerType.TRIGGER_COST_MATERIAL, item.getItemId(), removeCount);
    getPlayer().getQuestManager().queueEvent(
        QuestContent.QUEST_CONTENT_ITEM_LESS_THAN, item.getItemId(), item.getCount());
    getPlayer().getQuestManager().queueEvent(
        QuestCond.QUEST_COND_ITEM_NUM_LESS_THAN, item.getItemId(), item.getCount());
}
```

**3 个事件**：
1. **战令任务**：TRIGGER_COST_MATERIAL（"消耗 100 摩拉"任务）
2. **任务进度**：QUEST_CONTENT_ITEM_LESS_THAN
3. **任务条件**：QUEST_COND_ITEM_NUM_LESS_THAN

→ "把材料用光" / "材料不足才进入下一关" 走这里。

---

## 8. equipItem / unequipItem：装备绑定

### 8.1 装备到角色

```java
public boolean equipItem(long avatarGuid, long equipGuid) {
    Avatar avatar = getPlayer().getAvatars().getAvatarByGuid(avatarGuid);
    GameItem item = this.getItemByGuid(equipGuid);
    
    if (avatar != null && item != null) {
        return avatar.equipItem(item, true);
    }
    return false;
}
```

### 8.2 卸下装备（武器不能卸只能换）

```java
public boolean unequipItem(long avatarGuid, int slot) {
    Avatar avatar = getPlayer().getAvatars().getAvatarByGuid(avatarGuid);
    EquipType equipType = EquipType.getTypeByValue(slot);
    
    if (avatar != null && equipType != EquipType.EQUIP_WEAPON) {   // ★ 武器不能卸
        if (avatar.unequipItem(equipType)) {
            getPlayer().sendPacket(new PacketAvatarEquipChangeNotify(avatar, equipType));
            avatar.recalcStats();   // ★ 卸了重算属性
            return true;
        }
    }
    return false;
}
```

**关键限制**：武器**只能换不能卸**——空手角色没法战斗，所以 grasscutter 不允许"裸装"状态。

→ 注意这里**没看到 recalcStats** 在 equipItem 流程里——是 `avatar.equipItem` 内部调的，但卸下时**重算**。

---

## 9. loadFromDatabase：登录恢复

```java
public void loadFromDatabase() {
    List<GameItem> items = DatabaseHelper.getInventoryItems(getPlayer());
    
    for (GameItem item : items) {
        if (item.getObjectId() == null) continue;
        
        ItemData itemData = GameData.getItemDataMap().get(item.getItemId());
        if (itemData == null) continue;
        item.setItemData(itemData);
        
        InventoryTab tab = getInventoryTab(item.getItemData().getItemType());
        putItem(item, tab);
        
        // ★ 装备类: 重新挂回角色
        if (item.isEquipped()) {
            Avatar avatar = getPlayer().getAvatars().getAvatarById(item.getEquipCharacter());
            boolean hasEquipped = false;
            if (avatar != null) {
                hasEquipped = avatar.equipItem(item, false);
            }
            if (!hasEquipped) {
                item.setEquipCharacter(0);   // 兜底: 解绑
                item.save();
            }
        }
    }
}
```

### 9.1 数据完整性兜底

- `itemData == null` → 跳过（配表删了的物品不再加载）
- `objectId == null` → 跳过（破损数据）
- `avatar == null` → 装备解绑（角色没了不挂武器）

→ 这是为什么"删除了某个老物品，重启不报错"——加载时静默跳过。

### 9.2 N+1 查询风险

```java
for (GameItem item : items) {
    Avatar avatar = getPlayer().getAvatars().getAvatarById(item.getEquipCharacter());
}
```

理论上每件装备查一次 avatar——但 `avatars` 在内存里（已经 loadFromDatabase），所以是 Map 查询，**实际 O(N)**。

---

## 10. VirtualCurrencyHandler：插件扩展机制

### 10.1 注册接口

```java
public interface VirtualCurrencyHandler<T> {
    int getCurrency(T extraData, int itemId);
    void setCurrency(T extraData, int itemId, int count);
    void modifyCurrency(T extraData, int itemId, int count);
}

public <T> void registerVirtualCurrencyHandler(int itemId, VirtualCurrencyHandler<T> handler, T extraData) {
    this.virtualCurrencyHandlers.put(itemId, new VirtualCurrencyHandlerEntry<>(itemId, handler, extraData));
}
```

### 10.2 用途

**核心 9 个 itemId**（101/102/105/106/107/201-204）**硬编码**在 switch 里。
**其他虚拟币**通过这个接口注册：
- 活动通货（如海灯节飞天明霄灯计数）
- 七圣召唤代币
- 罗刹勋章
- 各种新通货

```java
inventory.registerVirtualCurrencyHandler(2100, new ActivityCurrencyHandler(activityData), activityData);
```

→ **加新活动通货不需要改 Inventory 代码** —— 又一个开放扩展点。

---

## 11. 完整经济流程（端到端）

### 11.1 任务完成发奖励链

```
[1] GameMainQuest.finish()
    ↓
    RewardData reward = getRewardData(rewardId);
    
[2] for (ItemParam param : reward.rewardItemList):
    [a] inventory.addItem(param.itemId, param.itemCount, ActionReason.QuestReward)
    
[3] addItem()
    ↓
    putItem(item)
    ↓ 按 ItemType 分支处理
    
[4] [WEAPON/RELIQUARY 分支]
    ↓
    tab 满? return null (背包提示满了)
    ↓ 否则
    putItem(item, tab) → save 到 DB → store + tab 更新
    
    [VIRTUAL 分支]
    ↓
    addVirtualItem(itemId, count)
    ↓
    根据 itemId 改 Player 字段 (mora/primogems/exp 等)
    
    [MATERIAL 分支]
    ↓
    existingItem 已有? 叠加 count (capped by stackLimit)
    新种类? tab 满? 否则 new slot
    
[5] PacketStoreItemChangeNotify (通知客户端更新背包)
    + PacketItemAddHintNotify (右上角浮动 "任务奖励 + 摩拉 × 1000")

[6] triggerAddItemEvents:
    [a] 战令: TRIGGER_OBTAIN_MATERIAL_NUM
    [b] 任务: QUEST_CONTENT_OBTAIN_ITEM × 2
    [c] 任务: QUEST_COND_PACK_HAVE_ITEM
    
[7] 这些 trigger 可能触发新任务进度更新
    → 任务完成 → 又来一遍 addItem  (递归!)
```

### 11.2 联动其他系统

| Inventory 操作 | 触发的系统 |
|---|---|
| addItem | BattlePass / Quest / Achievement / Codex (角色头像)|
| removeItem | BattlePass / Quest |
| addItem (装备) | 客户端展示新装备特效 |
| addItem (角色头像) | useItemDirect → AvatarStorage 加角色 |
| addItem (服装) | useItemDirect → 服装解锁 |
| addItem (风之翼) | useItemDirect → 风之翼解锁 |
| payItem (体力) | ResinManager 扣 + 定时器 |
| payItem (摩拉) | 改 Player.mora 字段 + 客户端通知 |

→ **Inventory 是经济中枢**：所有给 / 拿 / 用物品都过它。

---

## 12. 设计模式总结

### 12.1 统一接口 + 多态分支

```
addItem(int|GameItem|ItemParam)   ← 11 个重载，1 个入口
   ↓
putItem(GameItem)                  ← 统一内部表示
   ↓
switch (ItemType)
   ├─ WEAPON/RELIQUARY: 独立存储
   ├─ VIRTUAL: 改 Player 字段
   └─ MATERIAL/FURNITURE: 叠加
```

### 12.2 isUseOnGain：自动消费

```yaml
ItemData.isUseOnGain = true
  → 拿到立即 useItemDirect, 不入背包
  → 适用: 角色头像/风之翼/服装/名片
```

**优势**：客户端不需要"使用按钮"——拿到自动解锁。

### 12.3 原子消费 (payItems)

```
先全部检查 → 再全部扣除
任一失败 → 不扣
```

避免"扣一半余额"的事务问题。

### 12.4 双层索引

```
store (guid → item)         ← O(1) 按 guid 找
tab.itemById (itemId → item) ← O(1) 按 itemId 找
```

→ 两种查询模式都 O(1)。

### 12.5 插件扩展点

```
registerVirtualCurrencyHandler(itemId, handler, extraData)
```

加新活动通货零代码改动。

---

## 13. 反作弊薄弱处

| 攻击 | 是否有效 | 原因 |
|---|---|---|
| 伪造 addItem 包请求 | ✗ 无效 | client 不能直接发, 走 Handler |
| 篡改 ActionReason | ✓ 显示伪造 (但实际给数 ok) |
| 改本地 store 数字 | ✗ 无效 | 服务器内存独立 |
| 复制装备 (item.copy) | ✗ 无效 | guid 唯一约束 |
| 给自己加角色 | ✗ 无效 | 角色头像 itemId 走 useItemDirect 服务器算 |
| 加大量摩拉 | ✗ 无效 | addItem 内部 |

→ Inventory **接口保护得比较好** —— 客户端不能直接发"给我加物品"，必须通过具体玩法（战斗胜利/任务完成/抽卡）。

---

## 14. 关键收获

1. **7 大 ItemType + 40+ MaterialType**：每个版本都加新类
2. **4 InventoryTab + ITEM_VIRTUAL 不入 tab**：双层索引 (store + tab)
3. **8 大虚拟币 itemId**：101/102/105/106/107/201/202/203/204（角色经验/AR/友谊/树脂/钥匙/原石/摩拉/结晶/宝钱）
4. **联机友谊经验 ×2**：105 走 `count * (inMultiplayer ? 2 : 1)`
5. **isUseOnGain 自动消费**：角色/服装/风之翼/名片不入背包
6. **addItem 11 重载** + putItem 统一内部 + ItemType 分支决策树
7. **payItems 原子事务**：先检查全部再扣除全部
8. **ActionReason 190+ 个**：分 3 段 (核心0-102 / 详细1001-1100 / 活动1100-1200)
9. **触发 4 个事件 (addItem) + 3 个事件 (removeItem)**：BattlePass + Quest + Achievement 联动
10. **武器不能卸只能换**：避免"裸装"
11. **loadFromDatabase 兜底**：itemData 缺失 / 角色没了 / objectId null 全部跳过
12. **VirtualCurrencyHandler 插件扩展点**：活动新通货零代码
13. **客户端不能直接 addItem**：必须走具体玩法 Handler（反作弊基础）
14. **stackLimit 容量上限**：每个 itemId 配的栈上限（如某些材料 9999）

---

## 15. 一句话总结

> **Inventory = 经济中枢 —— 4 大 tab (武器/圣遗物/材料/家具) + store guid 索引 + 8 虚拟币硬编码 + 插件扩展点；addItem 11 重载 → putItem 统一 → ItemType 分支决策；isUseOnGain 自动消费角色/服装/风之翼；payItems 原子事务避免"扣一半"；每次加减触发 4-3 个钩子联动 BattlePass/Quest/Achievement；ActionReason 190+ 个原因码贯穿全游戏经济。**
> 
> **设计哲学：统一入口 + 多态分支 + 插件扩展 + 原子事务 —— 让 5 年来无数新玩法（活动通货/七圣召唤/炼金/钓鱼/猫咖...）都能通过同一套机制接入。**

---

**前置笔记**：
- notes/15 经济系统总览 - Inventory 是其中一环
- notes/25 制作系统 - Combine/Cook 走 addItem
- notes/24 Avatar 升级 - equipItem 时 recalcStats
- notes/30 持久化 - GameItem 独立 collection
- notes/32 怪物 - dropSubfield 生成 EntityItem 后拾取走 addItem

**关联文件**：
- `Inventory.java`(680) - 主管理器
- `GameItem.java`(292) - 物品实例
- `ActionReason.java`(284) - 190+ 原因枚举
- `ItemType.java`(45) - 7 大类
- `MaterialType.java`(79) - 40+ 子类
- `EquipInventoryTab.java` / `MaterialInventoryTab.java` - 两类 tab
- `EquipType.java` - 装备槽位

**研究的源代码**: 1283 行 Inventory 核心代码。
