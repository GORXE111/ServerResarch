# ShopSystem 商店系统深度剖析

> 第 58 篇：notes/15 提过经济、notes/46 提过它是 14 GameSystem 之一——但商店运行时从未真正打开。**373 行 (5 文件)** 的"经济闭环出口"：多货币扣费 + 3 种刷新周期 + 服务器端限购校验 + "不信任客户端"防作弊。

---

## 0. 为什么这一篇重要

前 57 篇里 Shop 反复出现但 runtime 没专门挖：
- notes/15 经济系统：商店是经济输出之一
- notes/46 GameServer：ShopSystem 是 14 GameSystem 之一
- notes/38 Inventory：`ActionReason.Shop(4)` + payItems 原子消费
- notes/50 Resin：买树脂走 inventory.payItem

但**货架怎么配？限购怎么算？日/周/月刷新怎么实现？多货币怎么扣？**——这一篇统一回答。

---

## 1. Shop 系统全图

```
┌─────────────────────────────────────────────────────────────┐
│  ShopSystem (110 行) — BaseGameSystem                         │
│  - shopData: Map<shopId, List<ShopInfo>>                      │
│  - shopChestData: Map<chestId, List<ItemParamData>>           │
│  - getShopNextRefreshTime (DAILY/WEEKLY/MONTHLY)              │
│  - REFRESH_HOUR=4 GMT+8                                       │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  ShopInfo (88 行) — 单货物配置                                 │
│  - goodsId / goodsItem (商品)                                 │
│  - scoin(202) / hcoin(201) / mcoin(203) / costItemList (成本) │
│  - buyLimit / boughtNum                                       │
│  - beginTime / endTime / minLevel / maxLevel                  │
│  - ShopRefreshType (NONE/DAILY/WEEKLY/MONTHLY)                │
└────────────────────────┬────────────────────────────────────┘
                         │ per Player 持久化
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  ShopLimit (43 行) — @Entity 限购记录                          │
│  - shopGoodId / hasBought / hasBoughtInPeriod / nextRefreshTime │
└────────────────────────┬────────────────────────────────────┘
                         │ 3 Handler
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  GetShopReq / BuyGoodsReq / GetShopmallDataReq               │
└─────────────────────────────────────────────────────────────┘
```

→ **373 行 + 3 Handler** 支撑整个商店系统。

---

## 2. ShopSystem：双数据源加载（110 行）

### 2.1 两个 Map

```java
private final Int2ObjectMap<List<ShopInfo>> shopData;        // shopId → 货架
private final Int2ObjectMap<List<ItemParamData>> shopChestData;  // 礼包内容
```

### 2.2 loadShop：JSON + Excel 双源

```java
private void loadShop() {
    // 1. 从 Shop.json 加载 (社区维护)
    List<ShopTable> banners = DataLoader.loadList("Shop.json", ShopTable.class);
    for (ShopTable shopTable : banners) {
        shopTable.getItems().forEach(ShopInfo::removeVirtualCosts);   // ★ 虚拟成本转换
        getShopData().put(shopTable.getShopId(), shopTable.getItems());
    }
    
    // 2. ★ 可选: 从 Excel ShopGoodsData 加载 (官方配表)
    if (GAME_OPTIONS.enableShopItems) {
        GameData.getShopGoodsDataEntries().forEach((k, v) -> {
            if (!getShopData().containsKey(k.intValue()))
                getShopData().put(k.intValue(), new ArrayList<>());
            for (ShopGoodsData sgd : v) {
                getShopData().get(k.intValue()).add(new ShopInfo(sgd));
            }
        });
    }
}
```

→ **双源合并**：
- `Shop.json` —— 社区手工维护的货架（默认）
- `ShopGoodsExcelConfigData` —— mihoyo 官方配表（`enableShopItems` 开关）

→ 两者**叠加** —— 官方配表追加到已有 shopData。

### 2.3 loadShopChest：礼包内容

```java
private void loadShopChest() {
    Map<Integer, String> chestMap = DataLoader.loadMap("ShopChest.v2.json", Integer.class, String.class);
    chestMap.forEach((chestId, itemStr) -> {
        // 解析 "itemId:count,itemId:count,..." 字符串
        var entries = itemStr.split(",");
        var list = new ArrayList<ItemParamData>();
        for (var entry : entries) {
            var idAndCount = entry.split(":");
            list.add(new ItemParamData(Integer.parseInt(idAndCount[0]), Integer.parseInt(idAndCount[1])));
        }
        this.shopChestData.put((int) chestId, list);
    });
}
```

→ **ShopChest = "买礼包→开出多个物品"** —— 紧凑的 `"id:count,id:count"` 字符串格式。
→ 例：买"新手礼包" → 开出原石+摩拉+材料。

---

## 3. ShopInfo：货物配置（88 行）

```java
public class ShopInfo {
    private int goodsId;                       // 货物唯一 ID
    private ItemParamData goodsItem;           // 卖什么 (itemId + count)
    private int scoin;                         // ★ 摩拉成本 (item 202)
    private int hcoin;                         // ★ 原石成本 (item 201)
    private int mcoin;                         // ★ 创世结晶成本 (item 203)
    private List<ItemParamData> costItemList;  // 其他物品成本
    private int boughtNum;
    private int buyLimit;                      // 限购数 (0=无限)
    private int beginTime, endTime;            // 上架时间窗
    private int minLevel, maxLevel;            // 玩家等级要求
    private List<Integer> preGoodsIdList;      // 前置商品 (需先买)
    private int disableType;
    private int secondarySheetId;              // 子标签页
    
    @Setter private transient ShopRefreshType shopRefreshType;
    @Getter @Setter private int shopRefreshParam;
}
```

### 3.1 3 种货币 + 物品成本

| 成本字段 | itemId | 含义 |
|---|---|---|
| scoin | 202 | 摩拉 |
| hcoin | 201 | 原石 |
| mcoin | 203 | 创世结晶 |
| costItemList | 任意 | 物品成本（如纪行点/活动币）|

→ 一个商品可**同时需要多种货币 + 物品**（如"5000 摩拉 + 3 个活动币"）。

### 3.2 removeVirtualCosts：巧妙转换

```java
private boolean evaluateVirtualCost(ItemParamData item) {
    return switch (item.getId()) {
        case 201 -> {this.hcoin += item.getCount(); yield true;}   // 原石 → hcoin 字段
        case 203 -> {this.mcoin += item.getCount(); yield true;}   // 结晶 → mcoin 字段
        default -> false;
    };
}

public void removeVirtualCosts() {
    if (this.costItemList != null)
        this.costItemList.removeIf(item -> evaluateVirtualCost(item));
}
```

→ **配表统一用 costItemList 写所有成本**，加载时把 201/203 这类虚拟币**抽出**到专用字段。
→ 因为虚拟币（notes/38）扣费走 Player 字段而非物品 —— 分离让扣费逻辑清晰。

### 3.3 ShopRefreshType 3 种

```java
public enum ShopRefreshType {
    NONE(0),
    SHOP_REFRESH_DAILY(1),    // 每日刷新
    SHOP_REFRESH_WEEKLY(2),   // 每周刷新
    SHOP_REFRESH_MONTHLY(3);  // 每月刷新
}
```

→ "每日礼包"/"每周纪行商店"/"每月星辉兑换"等限购周期。

---

## 4. 刷新时间计算

```java
private static final int REFRESH_HOUR = 4;            // ★ 凌晨 4 点
private static final String TIME_ZONE = "Asia/Shanghai";  // ★ GMT+8

public static int getShopNextRefreshTime(ShopInfo shopInfo) {
    return switch (shopInfo.getShopRefreshType()) {
        case SHOP_REFRESH_DAILY ->
            Utils.getNextTimestampOfThisHour(REFRESH_HOUR, TIME_ZONE, shopInfo.getShopRefreshParam());
        case SHOP_REFRESH_WEEKLY ->
            Utils.getNextTimestampOfThisHourInNextWeek(REFRESH_HOUR, TIME_ZONE, shopInfo.getShopRefreshParam());
        case SHOP_REFRESH_MONTHLY ->
            Utils.getNextTimestampOfThisHourInNextMonth(REFRESH_HOUR, TIME_ZONE, shopInfo.getShopRefreshParam());
        default -> 0;
    };
}
```

→ **固定凌晨 4 点 GMT+8 刷新**——与正服一致（亚服时间）。
→ `shopRefreshParam` 偏移参数（某些商店错峰刷新）。

---

## 5. HandlerBuyGoodsReq：购买完整流程（核心）

```java
public void handle(GameSession session, byte[] header, BuyGoodsReq buyGoodsReq) {
    // ★ 1. 服务器重新查配置 (不信任客户端)
    List<ShopInfo> configShop = session.getServer().getShopSystem()
        .getShopData().get(buyGoodsReq.getShopType());
    if (configShop == null) return;
    
    var player = session.getPlayer();
    List<Integer> targetShopGoodsId = List.of(buyGoodsReq.getGoods().getGoodsId());
    
    for (int goodsId : targetShopGoodsId) {
        // ★ 2. 服务器找货物配置 (客户端只传 goodsId)
        Optional<ShopInfo> sg2 = configShop.stream()
            .filter(x -> x.getGoodsId() == goodsId).findFirst();
        if (sg2.isEmpty()) continue;
        ShopInfo sg = sg2.get();
        
        // ★ 3. 限购检查 + 刷新
        int currentTs = Utils.getCurrentSeconds();
        ShopLimit shopLimit = player.getGoodsLimit(sg.getGoodsId());
        int bought = 0;
        if (shopLimit != null) {
            if (currentTs > shopLimit.getNextRefreshTime()) {
                shopLimit.setNextRefreshTime(ShopSystem.getShopNextRefreshTime(sg));   // ★ 过期 → 重置
            } else {
                bought = shopLimit.getHasBoughtInPeriod();   // ★ 周期内已买
            }
            player.save();
        }
        
        // ★ 4. 限购上限检查
        if ((bought + buyGoodsReq.getBuyCount() > sg.getBuyLimit()) && sg.getBuyLimit() != 0) {
            return;   // 超限 → 拒绝
        }
        
        // ★ 5. 构造成本 (合并货币 + 物品)
        List<ItemParamData> costs = new ArrayList<>(sg.getCostItemList());
        costs.add(new ItemParamData(202, sg.getScoin()));   // 摩拉
        costs.add(new ItemParamData(201, sg.getHcoin()));   // 原石
        costs.add(new ItemParamData(203, sg.getMcoin()));   // 结晶
        
        // ★ 6. 原子扣费 (notes/38 payItems)
        if (!player.getInventory().payItems(costs, buyGoodsReq.getBuyCount())) {
            return;   // 钱不够 → 拒绝
        }
        
        // ★ 7. 记录限购 + 发货
        player.addShopLimit(sg.getGoodsId(), buyGoodsReq.getBuyCount(), 
            ShopSystem.getShopNextRefreshTime(sg));
        GameItem item = new GameItem(sg.getGoodsItem().getId(), 
            buyGoodsReq.getBuyCount() * sg.getGoodsItem().getCount());
        player.getInventory().addItem(item, ActionReason.Shop, true);
        
        // ★ 8. 回包
        session.send(new PacketBuyGoodsRsp(...));
    }
}
```

### 5.1 "不信任客户端" 防作弊（关键注释）

```java
// Don't trust your users' input
var player = session.getPlayer();
List<Integer> targetShopGoodsId = List.of(buyGoodsReq.getGoods().getGoodsId());
Optional<ShopInfo> sg2 = configShop.stream().filter(x -> x.getGoodsId() == goodsId).findFirst();
```

→ **客户端只传 goodsId + buyCount** —— 价格/限购**全部服务器重查配置**。
→ 客户端篡改"价格 0"无效——服务器用自己的 ShopInfo。
→ 这是 grasscutter 中**少有的显式反作弊注释**。

### 5.2 限购周期机制

```java
if (currentTs > shopLimit.getNextRefreshTime()) {
    shopLimit.setNextRefreshTime(ShopSystem.getShopNextRefreshTime(sg));   // lazy 重置
} else {
    bought = shopLimit.getHasBoughtInPeriod();
}
```

→ **Lazy 刷新**（同 notes/50 Resin / notes/57 Mail）：
- 购买时检查 `nextRefreshTime`
- 已过期 → 重置 nextRefreshTime（周期内购买数隐式归零）
- 未过期 → 用 hasBoughtInPeriod 累计

→ 无后台定时任务 —— 购买时懒计算。

### 5.3 ShopLimit 持久化（@Entity）

```java
@Entity
public class ShopLimit {
    private int shopGoodId;
    private int hasBought;          // 历史总购买数
    private int hasBoughtInPeriod;  // 当前周期购买数
    private int nextRefreshTime;    // 下次刷新时间戳
}
```

→ 存在 `Player.shopLimit: ArrayList<ShopLimit>`（notes/40 Player 字段）—— 嵌入 Player 文档持久化。

### 5.4 原子扣费（notes/38 payItems）

```java
if (!player.getInventory().payItems(costs, buyGoodsReq.getBuyCount())) {
    return;
}
```

→ `payItems` 是**原子事务**（notes/38 §5.3）：先全检查再全扣除。
→ "钱不够"不会扣一半 —— 整体回滚。

---

## 6. 3 个 Handler

```
HandlerGetShopReq                — 打开某商店 (返回货架 proto + 限购状态)
HandlerGetShopmallDataReq        — 商城总览 (所有商店列表)
HandlerGetActivityShopSheetInfoReq — 活动商店标签页
HandlerBuyGoodsReq               — 购买 (核心)
```

### 6.1 GetShopReq 返回限购状态

→ 打开商店时，服务器把每个货物的 `hasBoughtInPeriod` / `buyLimit` 一起返回。
→ 客户端显示"今日已购 2/5"。

---

## 7. 完整时序：玩家买每周纪行商店道具

```
[玩家打开纪行商店]
   ↓ GetShopReq { shopType: 1402 }
HandlerGetShopReq:
   configShop = shopSystem.shopData.get(1402)
   for each ShopInfo:
     查 player.getGoodsLimit(goodsId)
     算 hasBoughtInPeriod / buyLimit
   返回 PacketGetShopRsp (货架 + 限购状态)

[客户端显示]
   "摩拉袋 (5万) — 2000 纪行点 — 本周 0/2"

[玩家点购买 ×1]
   ↓ BuyGoodsReq { shopType: 1402, goods: {goodsId: X}, buyCount: 1 }
HandlerBuyGoodsReq:
   1. configShop = shopData.get(1402)  ← 服务器重查 (不信任客户端)
   2. sg = configShop.filter(goodsId == X)  ← 找配置
   3. shopLimit = player.getGoodsLimit(X)
      currentTs > nextRefreshTime?
        是 → nextRefreshTime = 下周一 4:00 GMT+8 (lazy 重置, bought=0)
        否 → bought = hasBoughtInPeriod (本周已买)
   4. bought(0) + buyCount(1) > buyLimit(2)? 否 ✓
   5. costs = [纪行点×2000, scoin×0, hcoin×0, mcoin×0]
   6. payItems(costs, 1):  ← 原子扣 (notes/38)
        检查纪行点 >= 2000 ✓
        扣 2000 纪行点
   7. addShopLimit(X, 1, nextRefreshTime):
        hasBoughtInPeriod += 1
        持久化 ShopLimit
      addItem(摩拉袋, ActionReason.Shop):  ← notes/38
        摩拉袋 → 实际是 5万摩拉 (虚拟币)
        触发 Quest/BattlePass 钩子
   8. PacketBuyGoodsRsp (新的 hasBoughtInPeriod = 1)

[客户端更新]
   "摩拉袋 — 本周 1/2"
   背包 +5万摩拉

[下周一 4:00 GMT+8 后再买]
   currentTs > nextRefreshTime → 重置
   bought 重新从 0 开始
```

---

## 8. 与其他系统的联动

### 8.1 Inventory (notes/38)

```java
player.getInventory().payItems(costs, buyCount);   // 扣费 (原子)
player.getInventory().addItem(item, ActionReason.Shop, true);   // 发货
```

→ Shop 完全复用 Inventory 的 payItems + addItem。

### 8.2 Player 持久化 (notes/40)

```java
@Getter private ArrayList<ShopLimit> shopLimit;   // Player 字段
player.getGoodsLimit(goodsId);
player.addShopLimit(goodsId, count, nextRefresh);
```

→ ShopLimit 嵌入 Player 文档（notes/30 embedded）。

### 8.3 战令 (notes/22)

→ `addItem(item, ActionReason.Shop)` 触发 `TRIGGER_BUY_SHOP_GOODS`（notes/41 WatcherTriggerType 405）。
→ "在商店购买 N 次"战令任务。

### 8.4 GameData 配表 (notes/45)

```java
GameData.getShopGoodsDataEntries();   // ShopGoodsExcelConfigData
```

→ 官方配表来源（enableShopItems 开关）。

---

## 9. 设计模式总结

### 9.1 不信任客户端（防作弊核心）

```
客户端只传 goodsId + buyCount
服务器重查 ShopInfo (价格/限购全服务器算)
```

→ 篡改价格无效——这是经济系统反作弊的关键。

### 9.2 Lazy 刷新（第 3 次出现）

```
购买时检查 nextRefreshTime
过期 → 重置, 否则用 hasBoughtInPeriod
```

→ 同 Resin (notes/50) / Mail (notes/57) —— grasscutter 偏爱 lazy evaluation。

### 9.3 虚拟成本分离

```
配表统一 costItemList
加载时 201/203 抽到 hcoin/mcoin 专用字段
```

→ 虚拟币扣费走 Player 字段（notes/38），物品走背包——分离让扣费清晰。

### 9.4 双数据源叠加

```
Shop.json (社区) + ShopGoodsExcelConfigData (官方)
enableShopItems 开关控制官方源
```

→ 灵活：用社区货架或官方配表或两者叠加。

### 9.5 原子扣费复用

```
payItems(costs, quantity) — notes/38 原子事务
```

→ 不重新实现扣费——复用 Inventory 的原子保证。

---

## 10. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 篡改商品价格 | ✗ 服务器重查 ShopInfo |
| 伪造 goodsId | ✗ configShop.filter 找不到则 skip |
| 超量购买 | ✗ buyLimit + hasBoughtInPeriod 检查 |
| 钱不够强买 | ✗ payItems 原子失败 |
| 篡改 ShopLimit | ✗ 服务器存 Player 文档 |
| 刷新前重买 | ✗ nextRefreshTime 服务器算 |

→ Shop 系统**反作弊极强** —— 经济出口必须无懈可击，"不信任客户端"是核心原则。

---

## 11. 关键收获

1. **373 行 (5 文件) + 3 Handler** = 整个商店系统
2. **双数据源**：Shop.json (社区) + ShopGoodsExcelConfigData (官方, enableShopItems 开关) 叠加
3. **ShopChest 礼包**：`"id:count,id:count"` 紧凑字符串格式
4. **3 种货币**：scoin(202 摩拉) / hcoin(201 原石) / mcoin(203 结晶) + costItemList 物品成本
5. **removeVirtualCosts**：配表统一 costItemList，加载时把 201/203 抽到专用字段
6. **3 种刷新类型**：DAILY / WEEKLY / MONTHLY，凌晨 4 点 GMT+8 (Asia/Shanghai)
7. **shopRefreshParam 偏移**：某些商店错峰刷新
8. **"Don't trust your users' input"**：客户端只传 goodsId+buyCount，价格/限购服务器重查（少有的显式反作弊注释）
9. **Lazy 刷新**：购买时检查 nextRefreshTime 过期则重置——无后台任务（第 3 次 lazy 模式）
10. **ShopLimit @Entity**：shopGoodId / hasBought / hasBoughtInPeriod / nextRefreshTime 嵌入 Player 文档
11. **buyLimit=0 表示无限购**
12. **payItems 原子扣费**（notes/38）：先全检查再全扣，钱不够不扣一半
13. **HandlerBuyGoodsReq 8 步**：重查配置 → 找货物 → 限购检查 → 上限检查 → 构造成本 → 原子扣费 → 记限购+发货 → 回包
14. **GetShopReq 返回限购状态**：客户端显示"本周 1/2"
15. **ActionReason.Shop(4)**：addItem 触发标准钩子（含战令 TRIGGER_BUY_SHOP_GOODS）
16. **复用 Inventory**：payItems + addItem 不重新实现扣费
17. **minLevel/maxLevel/beginTime/endTime**：等级 + 时间窗限制
18. **preGoodsIdList 前置商品**：需先买某商品才解锁
19. **secondarySheetId 子标签页**：商店内分类
20. **反作弊极强**：经济出口"不信任客户端"是核心原则

---

## 12. 一句话总结

> **ShopSystem = 经济闭环出口 (373 行) —— 双数据源 (Shop.json 社区 + ShopGoods Excel 官方) + 3 货币 (scoin/hcoin/mcoin) + costItemList 物品成本 + removeVirtualCosts 虚拟币分离 + 3 种刷新 (日/周/月 凌晨 4 点 GMT+8) + ShopLimit @Entity 持久化限购 + Lazy 刷新 (购买时检查 nextRefreshTime); HandlerBuyGoodsReq 8 步以"不信任客户端"重查配置 + payItems 原子扣费 (notes/38) + addItem(Shop) 触发钩子.**
> 
> **设计哲学: 不信任客户端 (价格/限购服务器重算) + Lazy 刷新 (复用 Resin/Mail 模式) + 虚拟成本分离 + 原子扣费复用 Inventory —— 这是 grasscutter 中"经济出口反作弊"的标准实现, 显式注释"Don't trust your users' input"点明核心.**

---

**前置笔记**：
- notes/15 经济系统 - Shop 是经济输出
- notes/38 Inventory - payItems 原子扣费 + ActionReason.Shop + 虚拟币
- notes/40 Player Manager - shopLimit ArrayList 字段
- notes/41 事件总线 - TRIGGER_BUY_SHOP_GOODS (405)
- notes/45 资源加载 - ShopGoodsExcelConfigData
- notes/46 GameServer - ShopSystem 是 14 之一
- notes/50 Resin / notes/57 Mail - Lazy evaluation 模式

**关联文件**：
- `ShopSystem.java`(110) - 双数据源加载 + 刷新计算
- `ShopInfo.java`(88) - 货物配置 + removeVirtualCosts
- `ShopLimit.java`(43) - @Entity 限购记录
- `ShopTable.java`(25) - Shop.json 映射
- `ShopType.java`(107) - 商店类型枚举
- `HandlerBuyGoodsReq.java` - 购买核心
- `HandlerGetShopReq` / `HandlerGetShopmallDataReq` / `HandlerGetActivityShopSheetInfoReq`
- `PacketBuyGoodsRsp.java` - 购买回包

**研究的源代码**: 373 行 Shop 核心 + HandlerBuyGoodsReq + 3 Handler。
