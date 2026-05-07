# 15 · Reward 与经济系统

任务奖励只是冰山一角——真正的故事是**整个游戏经济的统一流动**：怎么把"原石、摩拉、武器、圣遗物"用同一套抽象塞进玩家背包。

> 数据：`RewardExcelConfigData.json` (2.1 MB, 7165 reward)、`MaterialExcelConfigData.json` (4.5 MB, 4989 material)、`WeaponExcelConfigData.json`、`ReliquaryExcelConfigData.json`、`AvatarExcelConfigData.json`  
> 代码：`game/inventory/Inventory.java` (统一入口) + `game/mail/` + `game/props/ActionReason.java`

---

## 1. 物品 ID 命名空间（实测出来的清晰规律）

### 货币 / 经验类（1xx-2xx 段）

```
101  角色经验            ← 升级角色用
102  冒险阅历            ← 玩家等级 EXP
103  星尘                ← 许愿后获得，星辉商店货币
104  星辉                ← 抽卡获得，可换常驻角色
105  好感经验            ← 给角色加好感度
106  原粹树脂            ← 体力 (虚拟 ITEM_VIRTUAL)
107  传说钥匙            ← 传说任务钥匙

201  原石 (Primogem)     ← 充值/抽卡核心货币
202  摩拉 (Mora)         ← 普通金币
203  创世结晶            ← 充值后产物（可转原石）
204  尘歌壶币 (HomeCoin) ← 尘歌壶专用
```

### 实物道具

```
1xxxxx 段：消耗品
  100001  苹果        ← 食材类基础
  104003  大英雄的经验  ← 角色经验书
  104013  精锻用魔矿   ← 武器突破
  ...

11xxx-15xxx 段：武器
20xxx-25xxx 段：圣遗物
1xxxxxxx 段：角色（avatar id）
```

→ ID 是**有结构的命名空间**，不只是顺序号。看 ID 大致就能猜物品类型。

---

## 2. ItemType 二级分类（决定走哪条入库路径）

```java
public enum ItemType {
    ITEM_NONE,
    ITEM_VIRTUAL,       // 虚拟（货币、经验、体力）→ 不进背包，直接改玩家属性
    ITEM_MATERIAL,      // 材料（药水、矿石、食物）→ MaterialInventoryTab
    ITEM_RELIQUARY,     // 圣遗物 → EquipInventoryTab（独立 GUID）
    ITEM_WEAPON,        // 武器 → EquipInventoryTab（独立 GUID）
    ITEM_FURNITURE      // 家具 → MaterialInventoryTab
}
```

每个 `InventoryTab` 有独立**容量上限**（INVENTORY_LIMITS 配置）：
- `weapons`: 武器栏位数
- `relics`: 圣遗物栏位数
- `materials`: 材料堆叠数
- `furniture`: 家具堆叠数

→ **同一种 addItem 接口，按 ItemType 内部分流**。这是"统一入口、内部多态"的好设计。

---

## 3. 统一入口：Inventory.addItem 链路

任何奖励——任务、商店、抽卡、副本、邮件、采集——**最终都走 `Inventory.addItem(itemId, count, ActionReason)`**：

```java
// Inventory.java:104
public boolean addItem(int itemId, int count, ActionReason reason) {
    ItemData itemData = GameData.getItemDataMap().get(itemId);
    if (itemData == null) return false;
    GameItem item = new GameItem(itemData, count);
    return addItem(item, reason);
}

// Inventory.java:132
public boolean addItem(GameItem item, ActionReason reason, boolean forceNotify) {
    boolean result = addItem(item);   // ← 真正的物品入库
    
    // 特殊：角色卡片 → 走 NoGachaAvatarCardNotify
    if (item.getItemData().getMaterialType() == MaterialType.MATERIAL_AVATAR) {
        getPlayer().sendPacket(new PacketAddNoGachaAvatarCardNotify(...));
    }
    
    // 弹窗"获得了 XX"
    if (reason != null && (forceNotify || result)) {
        getPlayer().sendPacket(new PacketItemAddHintNotify(item, reason));
    }
    return result;
}
```

`putItem` 内部分发：

```java
// Inventory.java:285
case ITEM_VIRTUAL:
    this.addVirtualItem(item.getItemId(), item.getCount());   // 货币特殊路径
    return item;
default:
    // 材料：找已有相同 itemId 堆叠 / 找空格放
    GameItem existing = tab.getItemById(item.getItemId());
    if (existing == null) {
        if (tab.getSize() >= tab.getMaxCapacity()) return null;  // 满了拒绝
        this.putItem(item, tab);
        item.save();
    } else {
        existing.setCount(Math.min(existing.getCount() + item.getCount(), 
                                    item.getItemData().getStackLimit()));
        existing.save();
    }
```

注意几个细节：
- **物品有堆叠上限**（`stackLimit`）——超过会丢弃多余的（一些 fork 改成进邮件补偿）
- **背包容量上限**——满了直接拒绝
- 每次添加都立即 `save()` 到 MongoDB

---

## 4. 货币虚拟处理（`addVirtualItem`）

```java
// Inventory.java:345
private void addVirtualItem(int itemId, int count) {
    switch (itemId) {
        case 101 -> // Character exp
            this.player.getTeamManager().getActiveTeam().stream()
                .map(e -> e.getAvatar()).forEach(
                    avatar -> upgradeAvatar(player, avatar, count)
                );
        case 102 -> // Adventure exp
            this.player.addExpDirectly(count);
        case 105 -> // Companionship exp
            this.player.getTeamManager().getActiveTeam().stream()
                .map(e -> e.getAvatar()).forEach(
                    avatar -> upgradeAvatarFetterLevel(player, avatar, 
                        count * (player.isInMultiplayer() ? 2 : 1))   // 联机翻倍！
                );
        case 106 -> // Resin
            this.player.getResinManager().addResin(count);
        case 107 -> // Legendary Key
            this.player.addLegendaryKey(count);
        case 201 -> this.player.setPrimogems(this.player.getPrimogems() + count);
        case 202 -> this.player.setMora(this.player.getMora() + count);
        case 203 -> this.player.setCrystals(this.player.getCrystals() + count);
        case 204 -> this.player.setHomeCoin(this.player.getHomeCoin() + count);
        default -> {
            if (virtualCurrencyHandlers.containsKey(itemId)) {
                virtualCurrencyHandlers.get(itemId).modifyCurrency(count);
            }
        }
    }
}
```

### 几个绝绝子的细节

1. **角色经验 (101) 不进背包**——直接给"当前活跃队伍所有角色升级"。所以你给玩家"100 个 101 物品" = 当前 4 角色每个升 100 经验。
2. **好感经验 (105) 联机时翻倍**——`isInMultiplayer() ? 2 : 1`。这是"鼓励多人组队"的具体实现。
3. **体力 (106) 通过 ResinManager**——有上限 / 自动恢复 / 时间维度的复杂逻辑，和普通货币不同。
4. **空 default 分支用 virtualCurrencyHandlers**——为后续添加新的虚拟货币留了扩展点（如活动专用代币）。

---

## 5. 触发反馈：addItem 反向通知 4 个事件

```java
// Inventory.java:241
private void triggerAddItemEvents(GameItem result) {
    // 给战令系统
    getPlayer().getBattlePassManager().triggerMission(
        WatcherTriggerType.TRIGGER_OBTAIN_MATERIAL_NUM, 
        result.getItemId(), result.getCount());
    // 给任务系统
    getPlayer().getQuestManager().queueEvent(
        QuestContent.QUEST_CONTENT_OBTAIN_ITEM, 
        result.getItemId(), result.getCount());
    getPlayer().getQuestManager().queueEvent(
        QuestContent.QUEST_CONTENT_OBTAIN_VARIOUS_ITEM, 
        result.getItemId(), result.getCount());
    getPlayer().getQuestManager().queueEvent(
        QuestCond.QUEST_COND_PACK_HAVE_ITEM, 
        result.getItemId(), result.getCount());
}
```

**任何加道具**都会同时通知：
- 战令系统（"获得 N 个材料"任务）
- 任务系统的 OBTAIN_ITEM（finishCond 用）
- 任务系统的 PACK_HAVE_ITEM（acceptCond 用）

→ 这就是"接到一个收集任务，找到道具自动推进"的实现机制。**经济系统是任务系统的事件源**。

---

## 6. ActionReason 100+ 个分类（审计 + 反作弊）

`ActionReason.java` 列出了 100+ 个枚举值——**每次 addItem 都要标注一个 reason**：

```java
None(0),
QuestItem(1),                  // 任务 SubQuest.gainItems
QuestReward(2),                // 任务 MainQuest.rewardIdList
Trifle(3),                     // 微小琐事
Shop(4),                       // 商店购买
PlayerUpgradeReward(5),        // 玩家升级奖励
AddAvatar(6),
Compound(9),                   // 合成
Cook(10),                      // 烹饪
Gather(11),                    // 采集
MailAttachment(12),            // 邮件附件
DungeonFirstPass(20),          // 副本首通
DungeonPass(21),               // 副本通关
DailyTaskScore(26),            // 委托积分
Expedition(29),                // 派遣
Gacha(30),                     // 抽卡
Combine(31),                   // 合成
ForgeOutput(34),               // 锻造产出
ForgeReturn(35),               // 锻造返还（武器分解）
OpenChest(39),                 // 开宝箱
MonsterDie(37),                // 怪物掉落
SubfieldDrop(42),              // 区域掉落
ActivityMonsterDrop(44),
... (共 100+)
```

### 为什么需要这么细？

1. **客户端弹窗文本**：不同 reason 显示不同提示（"通过任务获得"、"在邮件附件中"等）
2. **反作弊审计**：服务器日志记录每个物品的来源——异常路径会被发现
3. **数据分析**：哪些途径产生最多 mora、玩家从哪里赚原石——商业核心数据
4. **bug 追溯**：玩家"我突然多了个东西" → 客服查日志按 reason 排查

→ **每个 ActionReason 是一条独立审计追溯路径**。商业级别的 KYC 设计。

---

## 7. RewardData：简化的"奖励包"抽象

```jsonc
// 真实 reward 100351 (玩家升级奖励的一种)
{
    "rewardId": 100351,
    "rewardItemList": [
        { "itemId": 102, "itemCount": 225 },     // 冒险阅历 225
        {},                                        // 9 个 slot 固定，空 = 不用
        { "itemId": 202, "itemCount": 975 },     // 摩拉 975
        {}, {}, {},
        { "itemId": 101, "itemCount": 500 },     // 角色经验 500
        {}, {}
    ]
}
```

`RewardData.java` 加载时过滤空槽：

```java
@Override
public void onLoad() {
    rewardItemList = rewardItemList.stream().filter(i -> i.getId() > 0).toList();
}
```

### 关键：rewardId 在多处复用

同一个 `rewardId` 可以被：
- `MainQuest.rewardIdList[rewardIndex]` 引用（任务奖励）
- `DungeonExcel` 引用（副本奖励）
- `BattlePassReward` 引用（战令奖励）
- `MailAttachment` 引用（邮件附件）
- `ShopGoods` 引用（商店购买后给道具）
- ...

→ **改一个 RewardData，全游戏所有发放此奖励的地方都跟着改**。这是策划想"把所有 350 级奖励统一调整"时的关键设计。

---

## 8. 分支奖励机制：`UPDATE_PARENT_QUEST_REWARD_INDEX`

```jsonc
// 真实 SubQuest finishExec
"finishExec": [
    { 
        "type": "QUEST_EXEC_UPDATE_PARENT_QUEST_REWARD_INDEX",
        "param": ["1"]   // 设 rewardIndex = 1
    }
]
```

任务有多个分支结局时：

```java
// MainQuest 配表
"rewardIdList": [
    100001,   // index 0: 默认结局
    100002,   // index 1: 拯救了村民
    100003,   // index 2: 阴谋成功
]

// 玩家选择 → finishExec 改 rewardIndex
// 任务完成时:
int rewardId = mainQuestData.getRewardIdList()[rewardIndex];   // 取对应分支
```

→ **同一任务多结局，发不同奖励**。85 次 `UPDATE_PARENT_QUEST_REWARD_INDEX` 出现在 corpus（notes/06 数据），说明这是常用机制。

---

## 9. Mail：延迟奖励通道

```java
// Mail.java
@Entity(value = "mail")
public class Mail {
    private MailContent mailContent;       // 标题 + 正文
    private List<MailItem> itemList;       // 附件
    private long sendTime;
    private long expireTime;               // 默认 7 天 = 604800 秒
    private int importance;                // 0=普通, 1=星标
    private boolean isRead;
    private boolean isAttachmentGot;       // 附件是否已领
    private int stateValue;                // 1=收件箱, 3=礼物箱
}
```

### 邮件作为奖励通道的几种典型用途

| 场景 | 为什么用 mail |
|---|---|
| 玩家不在线时的活动奖励 | 异步发放，玩家上线领 |
| 客服补偿 | 有审计轨迹（mailContent 写明原因） |
| 抽卡保底奖励 | 一次性发放，玩家可慢慢领 |
| 背包满时的物品 | 临时存储，等玩家清空再领 |
| 重要通知 + 附件 | 标星标确保玩家看到 |

### 领取流程

```
[活动系统] sendMail(player, "活动奖励", attachments=[...], expire=7d)
   ↓
DatabaseHelper.savePlayerMail(uid, mail)  ← 直接进 DB，不走 player session
   ↓ (玩家上线/打开邮件 UI)
PacketMailListNotify (列表)
   ↓ 玩家点"领取"
HandlerGetAllMailReq → 把 itemList 调 inventory.addItem(reason=MailAttachment)
   ↓
mail.isAttachmentGot = true
```

→ **离线 reward 的标准方案**。"为什么登录就有一堆邮件" = 离线期间各活动结算后留的邮件。

---

## 10. 完整奖励来源总览

```
                     [玩家 Inventory]
                            ↑
                addItem(itemId, count, ActionReason)
                            ↑
       ┌────────────────────┼────────────────────────────────┐
       │                    │                                │
   [任务奖励]            [副本奖励]                       [Mail 邮件附件]
   QuestReward           DungeonPass                     MailAttachment
       │                    │                                ↑
   gainItems[]              │                            异步发放
   rewardIdList[]           │                            (活动/客服/补偿)
       │                    │
       │           ┌────────┼────────────┐
       │       [开宝箱]    [怪物掉落]    [采集]
       │       OpenChest   MonsterDie    Gather
       │                                                
   [活动/委托]
   DailyTaskScore                                      [Shop 购买]
   ActivityMonsterDrop                                 Shop / VirtualCurrency
                                                              │
   [战令]                                                  扣货币 + 加物品
   BattlePass*                                              ↑
                                                       [抽卡]
   [PlayerUpgradeReward]                              Gacha
   玩家等级提升送奖励                                     ↑ 扣 201 原石/204 创世结晶
                                                       
   [合成 / 烹饪 / 锻造]
   Compound/Cook/Forge*  → 输入物品 + 输出物品（同时 addItem 和 removeItem）
```

**11+ 个独立奖励来源**，全部通过 `Inventory.addItem(itemId, count, ActionReason)` 入库。设计极致简洁。

---

## 11. RewardPreviewData：客户端"奖励预览"

`RewardPreviewExcelConfigData.json` (1.9 MB) — 这是**给客户端 UI 的预览数据**，独立于 RewardData。

```jsonc
{
    "id": 2201,                              // previewRewardId
    "previewItems": [
        { "itemId": 202, "count": 200000 },  // 摩拉
        { "itemId": 102, "count": 750 },     // 冒险阅历
        ...
    ]
}
```

为什么独立？因为：
1. **预览的物品列表可能和实际发放不一致**（如 Daily Task 是从 RewardData 中**随机抽 4 个**奖励，但预览给"全部可能"列表，让玩家看到再决定要不要做）
2. **预览支持百分比/概率显示**（如"50% 概率获得稀有材料"）
3. **客户端 UI 只需要 preview 数据**，不需要完整后端 RewardData

→ **前后端数据分离的典型例子**：服务器权威数据（RewardData）vs 客户端展示数据（RewardPreviewData）。

---

## 12. 关键设计哲学

1. **统一入口 + 内部多态**：所有"加道具"走一个 addItem，按 ItemType 分流（虚拟/材料/装备）
2. **货币是虚拟物品**：`201 = 原石` 既是 itemId 也是 PlayerProperty 的快捷字段，统一表达
3. **审计无处不在**：每次 addItem 必带 ActionReason，100+ 分类为反作弊和数据分析铺垫
4. **奖励数据复用**：RewardId 跨任务/活动/邮件/商店共用，改一处即可
5. **Mail 是异步缓冲**：解决"玩家不在线/背包满"的发放问题，不阻塞主流程
6. **前后端预览分离**：预览数据独立，避免暴露后端逻辑（如随机概率）
7. **加道具反向触发任务**：经济系统主动通知任务/战令系统，关闭循环

---

## 13. 给做 MMO 经济系统开发者的提炼

1. **不要让 100 个系统各自实现"加道具"**——统一 addItem 入口，所有差异在 reason 上体现
2. **货币和实物用同一种 ID 命名空间**——简化所有奖励配置，按段位分类
3. **ActionReason 必须从一开始就丰富**——后期补充会丢失大量历史日志
4. **客户端不能信奖励数据**——服务器是权威，客户端只看 preview 表
5. **背包满 / 堆叠超限要有兜底**——丢进 mail 才不会让玩家抓狂
6. **任务系统必须监听经济事件**——"收集任务"是 RPG 标配，不能靠主动检查
7. **审计 + 反作弊要早做**——业务上线后再补加日志成本极高

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/data/excels/RewardData.java` — 简单的 reward 数据类
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/inventory/Inventory.java` — 统一入口（800+ 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/inventory/Inventory.java:345` — addVirtualItem (货币特殊处理)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/props/ActionReason.java` — 100+ 审计枚举
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/mail/Mail.java` — 邮件实体
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/GameMainQuest.java:208` — 任务奖励发放
- `GenshinData/ExcelBinOutput/RewardExcelConfigData.json` — 7165 个 reward
- `GenshinData/ExcelBinOutput/MaterialExcelConfigData.json` — 4989 个 material
