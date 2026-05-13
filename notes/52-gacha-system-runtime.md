# GachaSystem 抽卡运行时引擎深度剖析

> 第 52 篇：notes/21 讲了"概率数学"层——这一篇打开**运行时引擎**：6 种 BannerType × 4 套 pool × 4 类 pity × 命定值 × C6 移除 × 双池平衡 × 热重载，是商业模式的核心。

---

## 0. 为什么这一篇重要

前 51 篇里 GachaSystem 反复出现但 runtime 没专门挖：
- notes/21 抽卡数学：讲了"4 层保底叠加 + 线性插值软保底"概率模型
- notes/30 持久化：`gachas` collection 存抽卡历史
- notes/31 Dispatch HTTP：`/gacha` URL 让玩家网页看抽卡历史
- notes/46 GameServer：GachaSystem 是 14 GameSystem 之一

但**doPulls 怎么实现？6 种 BannerType 各自不同？命定值 (epitomized) 何时触发？4 套 fallback pool 怎么选？**——这一篇统一回答。

---

## 1. Gacha 体系全图

```
┌─────────────────────────────────────────────────────────────┐
│  GachaSystem (BaseGameSystem, 450 行)                         │
│  - gachaBanners: Map<scheduleId, GachaBanner>                │
│  - doPulls / doPull / doRarePull / doFallbackRarePull        │
│  - WatchService 热重载 Banners.json                           │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ↓
┌─────────────────────────────────────────────────────────────┐
│  GachaBanner (267 行)                                         │
│  - 6 种 BannerType                                           │
│  - rateUpItems4 / rateUpItems5                               │
│  - fallbackItems4Pool1/2 / fallbackItems5Pool1/2             │
│  - eventChance4/5 (50/75)                                    │
│  - cost / cost10 / 时间窗口                                   │
└──────────────────────────────┬──────────────────────────────┘
                               │ per Player
                               ↓
┌─────────────────────────────────────────────────────────────┐
│  PlayerGachaInfo (per Player, 持久化)                          │
│  - bannerInfos: Map<gachaType, PlayerGachaBannerInfo>        │
│  - wishItemId (命定武器)                                       │
└──────────────────────────────┬──────────────────────────────┘
                               │ per Banner
                               ↓
┌─────────────────────────────────────────────────────────────┐
│  PlayerGachaBannerInfo                                        │
│  - pity4 / pity5 (保底计数)                                    │
│  - failedFeaturedItemPulls[] (歪了次数)                        │
│  - failedChosenItemPulls (武器命定值 0-2)                      │
│  - pityPool[] (双池平衡)                                       │
│  - totalPulls                                                 │
└─────────────────────────────────────────────────────────────┘
```

→ **717 行代码 + 持久化**支撑抽卡系统。

---

## 2. 6 种 BannerType

```java
public enum BannerType {
    STANDARD   (200, 224, weights4, weights5,         50, 50, pool5_1, pool5_2),  // 常驻
    BEGINNER   (100, 224, weights4, weights5,         50, 50, pool5_1, pool5_2),  // 新手 (20 抽 8 折)
    EVENT      (301, 223, weights4, weights5_char,    50, 50, pool5_1, pool5_2),  // 旧版人物 (兼容)
    CHARACTER  (301, 223, weights4, weights5_char,    50, 50, pool5_1, EMPTY),    // ★ 人物 1 池
    CHARACTER2 (400, 223, weights4, weights5_char,    50, 50, pool5_1, EMPTY),    // ★ 人物 2 池
    WEAPON     (302, 223, weights4_wpn, weights5_wpn, 75, 75, EMPTY,   pool5_2)   // ★ 武器
}
```

### 2.1 关键参数差异

| BannerType | gachaType | weights5 | eventChance5 | 5 星 pool |
|---|---|---|---|---|
| STANDARD | 200 | DEFAULT (90 抽硬保底) | 50% | 2 池都有 |
| BEGINNER | 100 | DEFAULT | 50% | 2 池都有 |
| EVENT | 301 | CHARACTER | 50% | 2 池都有 (legacy) |
| **CHARACTER** | 301 | CHARACTER | 50% | **只 pool1** |
| **CHARACTER2** | 400 | CHARACTER | 50% | **只 pool1** |
| **WEAPON** | 302 | WEAPON (80 抽硬保底) | **75%** | **只 pool2** |

### 2.2 武器池的特殊性

- **eventChance = 75%** —— 武器歪率比角色低（25% vs 50%）
- **weights5_wpn 80 抽硬保底** —— 比角色 90 抽少
- **只 pool2** —— 不与角色 pool 冲突

### 2.3 cost item

| Banner | costItemId | 单抽 | 十连 |
|---|---|---|---|
| STANDARD | 224 (无封缄之蓝) | 1 | 10 |
| EVENT/CHARACTER/WEAPON | 223 (相遇之缘/纠缠之缘) | 1 | 10 |

→ 单抽 1 个 / 十连 10 个 —— 不打折（mihoyo 设计）。

---

## 3. 4 套 fallback pool（核心抽卡模型）

```java
private class BannerPools {
    public int[] rateUpItems4;          // ★ 4 星 UP
    public int[] rateUpItems5;          // ★ 5 星 UP
    public int[] fallbackItems4Pool1;   // 4 星常驻 pool 1
    public int[] fallbackItems4Pool2;   // 4 星常驻 pool 2
    public int[] fallbackItems5Pool1;   // 5 星常驻 pool 1 (人物)
    public int[] fallbackItems5Pool2;   // 5 星常驻 pool 2 (武器)
}
```

### 3.1 双池设计的原因

```
为什么 5 星分 2 池?

人物池: pool1 = 7 个常驻 5 星人物 [迪卢克/七七/莫娜/刻晴/琴/温迪/可莉 ...]
武器池: pool2 = 10 个常驻 5 星武器 [天空之傲/天空之翼/...]

CHARACTER banner: 歪了去 pool1 (人物) - pool2 空
WEAPON banner: 歪了去 pool2 (武器) - pool1 空
STANDARD banner: 两池都可能
```

→ 这就是为什么"人物池歪了不会出武器"——是**池子结构**决定的。

### 3.2 autoStripRateUpFromFallback

```java
if (banner.isAutoStripRateUpFromFallback()) {
    fallbackItems4Pool1 = Utils.setSubtract(fallbackItems4Pool1, rateUpItems4);
    fallbackItems5Pool1 = Utils.setSubtract(fallbackItems5Pool1, rateUpItems5);
    // ...
}
```

→ **从 fallback 池中剥离 UP 物品** —— 避免"歪了又抽到 UP"的悖论。

---

## 4. doPull：单抽流程

```java
private synchronized int doPull(GachaBanner banner, PlayerGachaBannerInfo gachaInfo, BannerPools pools) {
    // 1. ★ 所有 pity 计数器 +1
    gachaInfo.incPityAll();
    
    // 2. ★ 用 lerp 算 5/4/3 各自的 weight
    int[] weights = {
        banner.getWeight(5, gachaInfo.getPity5()),   // 5 星权重 (线性插值)
        banner.getWeight(4, gachaInfo.getPity4()),   // 4 星权重 (线性插值)
        10000                                          // 3 星 (剩下的)
    };
    
    // 3. ★ drawRoulette 选稀有度
    int levelWon = 5 - drawRoulette(weights, 10000);
    
    // 4. 按稀有度分发
    return switch (levelWon) {
        case 5:
            gachaInfo.setPity5(0);   // ★ 5 星重置 pity5
            yield doRarePull(pools.rateUpItems5, pools.fallbackItems5Pool1, pools.fallbackItems5Pool2, 5, banner, gachaInfo);
        case 4:
            gachaInfo.setPity4(0);
            yield doRarePull(pools.rateUpItems4, pools.fallbackItems4Pool1, pools.fallbackItems4Pool2, 4, banner, gachaInfo);
        default:
            yield getRandom(banner.getFallbackItems3());   // 3 星武器随机
    };
}
```

### 4.1 lerp 软保底（呼应 notes/21）

```java
public int getWeight(int rarity, int pity) {
    return switch (rarity) {
        case 4 -> Utils.lerp(pity, weights4);
        default -> Utils.lerp(pity, weights5);
    };
}
```

→ `weights5` 是个**插值表**，pity 在不同区间权重不同：
- 0-72 抽：5 星权重 = 60 (0.6%)
- 72-89 抽：**线性插值**逐渐提高（软保底）
- 90 抽：硬保底 100%

→ 这是 notes/21 "线性插值软保底"的实现位置。

### 4.2 drawRoulette 轮盘 + cutoff

```java
private synchronized int drawRoulette(int[] weights, int cutoff) {
    int total = 0;
    for (int weight : weights) total += weight;
    
    int roll = ThreadLocalRandom.current().nextInt((total < cutoff) ? total : cutoff);
    
    int subTotal = 0;
    for (int i = 0; i < weights.length; i++) {
        subTotal += weights[i];
        if (roll < subTotal) return i;
    }
    return 0;
}
```

→ `cutoff` 上界 = 10000 —— 防止权重和过大导致严重偏差。

---

## 5. doRarePull：UP 命中 + 命定值

```java
private synchronized int doRarePull(int[] featured, int[] fallback1, int[] fallback2, 
                                     int rarity, GachaBanner banner, PlayerGachaBannerInfo gachaInfo) {
    int itemId = 0;
    
    // ★ 命定值检测（武器池专属）
    boolean epitomized = (banner.hasEpitomized()) && (rarity == 5) && (gachaInfo.getWishItemId() != 0);
    boolean pityEpitomized = (gachaInfo.getFailedChosenItemPulls() >= banner.getWishMaxProgress());
    
    // ★ "歪了"检测
    boolean pityFeatured = (gachaInfo.getFailedFeaturedItemPulls(rarity) >= 1);
    boolean rollFeatured = (this.randomRange(1, 100) <= banner.getEventChance(rarity));
    boolean pullFeatured = pityFeatured || rollFeatured;
    
    // === 命运修正逻辑 ===
    if (epitomized && pityEpitomized) {
        // 命定值满 → 强制给命定武器
        gachaInfo.setFailedFeaturedItemPulls(rarity, 0);
        itemId = gachaInfo.getWishItemId();
    } else {
        if (pullFeatured && (featured.length > 0)) {
            // 中 UP
            gachaInfo.setFailedFeaturedItemPulls(rarity, 0);
            itemId = getRandom(featured);
        } else {
            // 歪了 → 走 fallback
            gachaInfo.addFailedFeaturedItemPulls(rarity, 1);
            itemId = doFallbackRarePull(fallback1, fallback2, rarity, banner, gachaInfo);
        }
    }
    
    // 命定值更新
    if (epitomized) {
        if (itemId == gachaInfo.getWishItemId()) {
            gachaInfo.setFailedChosenItemPulls(0);   // 抽到命定 → 重置
        } else {
            gachaInfo.addFailedChosenItemPulls(1);    // 未抽到 → +1
        }
    }
    
    return itemId;
}
```

### 5.1 大保底（"歪了一次必出 UP"）

```java
boolean pityFeatured = (gachaInfo.getFailedFeaturedItemPulls(rarity) >= 1);
```

→ "**上次没中 UP（歪了）→ 这次必出 UP**" 的实现。

### 5.2 小保底（50% / 75%）

```java
boolean rollFeatured = (this.randomRange(1, 100) <= banner.getEventChance(rarity));
```

→ `eventChance` 50 (角色) 或 75 (武器) —— 直接 1d100 判断。

### 5.3 命定值 epitomized（武器池）

```java
boolean epitomized = banner.hasEpitomized() && rarity == 5 && wishItemId != 0;
boolean pityEpitomized = failedChosenItemPulls >= wishMaxProgress;   // 通常 wishMaxProgress = 2

if (epitomized && pityEpitomized) {
    itemId = gachaInfo.getWishItemId();   // ★ 命定满 (2/2) 强制给
}
```

**命定值机制**（武器池专属）：
- 玩家点选"命定武器" → `wishItemId` 设置
- 抽到 5 星但不是命定的 → `failedChosenItemPulls` +1
- 抽到 5 星且是命定的 → `failedChosenItemPulls` = 0
- 累积到 `wishMaxProgress` (=2) → **下次 5 星强制命定**

→ "**3 次 5 星内必出选定武器**"的实现。

---

## 6. doFallbackRarePull：双池平衡算法

```java
private synchronized int doFallbackRarePull(int[] fallback1, int[] fallback2, int rarity, 
                                              GachaBanner banner, PlayerGachaBannerInfo gachaInfo) {
    if (fallback1.length < 1) {
        if (fallback2.length < 1) {
            return getRandom(/* DEFAULT pool */);
        } else {
            return getRandom(fallback2);
        }
    } else if (fallback2.length < 1) {
        return getRandom(fallback1);
    } else {
        // ★ 双池平衡: 用 pityPool 计数算权重
        int pityPool1 = banner.getPoolBalanceWeight(rarity, gachaInfo.getPityPool(rarity, 1));
        int pityPool2 = banner.getPoolBalanceWeight(rarity, gachaInfo.getPityPool(rarity, 2));
        
        // 较大权重的池子优先 (硬截断 cutoff = 10000)
        int chosenPool = switch ((pityPool1 >= pityPool2) ? 1 : 0) {
            case 1 -> 1 + drawRoulette(new int[] {pityPool1, pityPool2}, 10000);
            default -> 2 - drawRoulette(new int[] {pityPool2, pityPool1}, 10000);
        };
        
        return switch (chosenPool) {
            case 1:
                gachaInfo.setPityPool(rarity, 1, 0);
                yield getRandom(fallback1);
            default:
                gachaInfo.setPityPool(rarity, 2, 0);
                yield getRandom(fallback2);
        };
    }
}
```

### 6.1 双池平衡的妙处

```
玩家在 STANDARD banner 抽 100 次
   pool1 (人物) 出了 7 次
   pool2 (武器) 出了 3 次

下次歪了:
   pityPool1 += 1 (人物没出)  → 权重大
   pityPool2 += 1 (武器没出)  → 权重小

drawRoulette 优先抽 pool1
→ 长期平衡 50/50 出人物/武器
```

→ 防止"一直只出人物从不出武器"的极端情况。
→ 这是 notes/21 提到的"**池平衡保底**"——比单纯随机更公平。

---

## 7. doPulls：完整十连流程（核心 150 行）

```java
public synchronized void doPulls(Player player, int scheduleId, int times) {
    // 1. 合法性检查 (只支持 1/10 抽)
    if (times != 10 && times != 1) {
        player.sendPacket(new PacketDoGachaRsp(Retcode.RET_GACHA_INVALID_TIMES));
        return;
    }
    
    // 2. 武器包检查 (10 抽需要 10 空)
    Inventory inventory = player.getInventory();
    if (inventory.getInventoryTab(ITEM_WEAPON).getSize() + times > maxCapacity) {
        player.sendPacket(new PacketDoGachaRsp(Retcode.RET_ITEM_EXCEED_LIMIT));
        return;
    }
    
    // 3. 获取 banner
    GachaBanner banner = this.getGachaBanners().get(scheduleId);
    if (banner == null) {
        player.sendPacket(new PacketDoGachaRsp());
        return;
    }
    
    // 4. 检查 banner 总抽数上限 (BEGINNER 池有 20 抽限)
    PlayerGachaBannerInfo gachaInfo = player.getGachaInfo().getBannerInfo(banner);
    if (banner.getGachaTimesLimit() != MAX_VALUE && 
        (gachaInfo.getTotalPulls() + times) > banner.getGachaTimesLimit()) {
        player.sendPacket(new PacketDoGachaRsp(Retcode.RET_GACHA_TIMES_LIMIT));
        return;
    }
    
    // 5. 扣 cost (相遇之缘 × times)
    ItemParamData cost = banner.getCost(times);
    if (cost.getCount() > 0 && !inventory.payItem(cost)) {
        player.sendPacket(new PacketDoGachaRsp(Retcode.RET_GACHA_COST_ITEM_NOT_ENOUGH));
        return;
    }
    
    // 6. 增加总抽数
    gachaInfo.addTotalPulls(times);
    
    // 7. ★ 创建临时 BannerPools (避免修改原 banner)
    BannerPools pools = new BannerPools(banner);
    List<GachaItem> list = new ArrayList<>();
    int stardust = 0, starglitter = 0;
    
    // 8. C6 移除策略 (非原版扩展)
    if (banner.isRemoveC6FromPool()) {
        pools.rateUpItems4 = removeC6FromPool(pools.rateUpItems4, player);
        pools.rateUpItems5 = removeC6FromPool(pools.rateUpItems5, player);
        // 所有 fallback pool 同理
    }
    
    // 9. ★ 逐次抽卡 (1 或 10 次)
    for (int i = 0; i < times; i++) {
        int itemId = doPull(banner, gachaInfo, pools);
        ItemData itemData = GameData.getItemDataMap().get(itemId);
        if (itemData == null) continue;
        
        // ★ 持久化 GachaRecord
        GachaRecord gachaRecord = new GachaRecord(itemId, player.getUid(), banner.getGachaType());
        DatabaseHelper.saveGachaRecord(gachaRecord);
        
        // ★ Stardust/Starglitter 处理
        int constellation = InventorySystem.checkPlayerAvatarConstellationLevel(player, itemId);
        switch (constellation) {
            case -2:  // 武器
                addStarglitter = (itemData.getRankLevel() == 5) ? 10 : 2;
                if (rankLevel == 3) addStardust = 15;
                break;
            case -1:  // 新角色
                gachaItem.setGachaItemNew(true);
                break;
            default:
                if (constellation >= 6) {  // C6 满命
                    addStarglitter = (rankLevel == 5) ? 25 : 5;
                } else {
                    addStarglitter = (rankLevel == 5) ? 10 : 2;
                    int constItemId = itemId + 100;   // 命座物品 ID
                    // 给命座物品 (通过 transferItem)
                }
                break;
        }
        
        // 加入背包
        inventory.addItem(new GameItem(itemData));
        
        // 累积 stardust/starglitter
        stardust += addStardust;
        starglitter += addStarglitter;
    }
    
    // 10. 一次性发 stardust/starglitter
    if (stardust > 0) inventory.addItem(stardustId, stardust);
    if (starglitter > 0) inventory.addItem(starglitterId, starglitter);
    
    // 11. 回包
    player.sendPacket(new PacketDoGachaRsp(banner, list, gachaInfo));
    
    // 12. 战令任务
    player.getBattlePassManager().triggerMission(WatcherTriggerType.TRIGGER_GACHA_NUM, 0, times);
}
```

→ **150 行涵盖完整抽卡流程**。

---

## 8. Stardust / Starglitter 经济

`GachaSystem.java`：
```java
private static final int starglitterId = 221;   // 大月卡 (starglitter)
private static final int stardustId = 222;       // 小星辰 (stardust)
```

### 8.1 转换规则

| 抽到的物品 | 玩家持有命座 | Starglitter | Stardust | 命座物品 |
|---|---|---|---|---|
| 5 星武器 | -2 (武器) | 10 | 0 | - |
| 4 星武器 | -2 | 2 | 0 | - |
| 3 星武器 | -2 | 0 | 15 | - |
| 新角色 | -1 (新) | 0 | 0 | - |
| 5 星角色 (C0-C5) | 0-5 | 10 | 0 | 命座 (itemId+100) |
| 5 星角色 (C6) | 6 | **25** | 0 | - (满命无命座) |
| 4 星角色 (C0-C5) | 0-5 | 2 | 0 | 命座 |
| 4 星角色 (C6) | 6 | **5** | 0 | - |

### 8.2 命座物品 ID 规约

```java
int constItemId = itemId + 100;   // ★ 命座 = 角色 itemId + 100
```

→ 例：迪卢克 itemId = 10000016 → 命座物品 = 10000116。
→ 简单的 ID 偏移规则，不需要单独配表。

### 8.3 transferItem 机制

```java
val gachaTransferItem = new GachaTransferItem();
gachaTransferItem.setItem(itemParam);                // 命座物品
gachaTransferItem.setTransferItemNew(haveConstItem); // 是否首次获得
transferItemsList.add(gachaTransferItem);
```

→ "**抽到角色但已经满 C5 → 自动转换成 starglitter**"——客户端动画就是这样。
→ 服务器**不直接 addItem**，而是发"transfer 通知"让客户端展示转换。

---

## 9. C6 移除策略（非原版扩展）

```java
if (banner.isRemoveC6FromPool()) {
    pools.rateUpItems4 = removeC6FromPool(pools.rateUpItems4, player);
    // ...
}

private synchronized int[] removeC6FromPool(int[] itemPool, Player player) {
    IntList temp = new IntArrayList();
    for (int itemId : itemPool) {
        if (InventorySystem.checkPlayerAvatarConstellationLevel(player, itemId) < 6) {
            temp.add(itemId);
        }
    }
    return temp.toIntArray();
}
```

→ **grasscutter 非原版特性**：满命的角色不再出现在 pool 中。
→ "终极保底"——避免 C6+1（C7 不存在所以转 starglitter，玩家不爽）。

### 9.1 10 抽内动态移除

```java
case 5: // C0-C5 抽到角色
    if (banner.isRemoveC6FromPool() && constellation == 5) {
        // ★ 抽到的是 C5 角色 → 这次给了变 C6
        // 立即从 pools 移除避免本次 10 连再出
        pools.removeFromAllPools(new int[] {itemId});
    }
```

→ 10 连第 1 抽出 C5→C6 → 后续 9 抽**不会再出同一个角色**。

---

## 10. Banner 时间窗口 + 热重载

```java
private synchronized GetGachaInfoRsp createProto(Player player) {
    long currentTime = System.currentTimeMillis() / 1000L;
    
    proto.setGachaInfoList(getGachaBanners().values().stream()
        .filter(banner -> 
            // ★ 在时间窗口内 OR 是常驻池
            (banner.getEndTime() >= currentTime && banner.getBeginTime() <= currentTime) 
            || (banner.getBannerType() == BannerType.STANDARD))
        .map(banner -> banner.toProto(player))
        .toList());
    
    return proto;
}
```

### 10.1 Banner 显示规则

```
filter:
   - 限时 banner (EVENT/CHARACTER/WEAPON): begin <= now <= end
   - 常驻 banner (STANDARD): 永远显示
   - BEGINNER (新手): 不在列表中，特殊处理
```

→ 客户端打开抽卡界面时**只显示当前活跃 banner**。

### 10.2 WatchService 热重载

```java
private synchronized void startWatcher(GameServer server) {
    this.watchService = FileSystems.getDefault().newWatchService();
    FileUtils.getDataUserPath("").register(watchService, StandardWatchEventKinds.ENTRY_MODIFY);
}

@Subscribe
public synchronized void watchBannerJson(GameServerTickEvent tickEvent) {
    if (GAME_OPTIONS.watchGachaConfig) {
        WatchKey watchKey = watchService.take();
        for (WatchEvent<?> event : watchKey.pollEvents()) {
            final Path changed = (Path) event.context();
            if (changed.endsWith("Banners.json")) {
                Grasscutter.getLogger().info("Reloading gacha config");
                this.load();
            }
        }
    }
}
```

→ **运维特性**：服主修改 `Banners.json` 不需要重启服务器——文件变化自动 reload。
→ 监听器订阅 `GameServerTickEvent`（notes/47）—— 每 tick 检查 watchKey。

---

## 11. GachaRecord 持久化

```java
GachaRecord gachaRecord = new GachaRecord(itemId, player.getUid(), banner.getGachaType());
DatabaseHelper.saveGachaRecord(gachaRecord);
```

→ **每次抽卡写一条 DB 记录**。

### 11.1 gachas collection 结构

```java
@Entity(value = "gachas")
public class GachaRecord {
    @Id private ObjectId id;
    @Indexed private int ownerId;
    private int itemId;
    private int gachaType;
    private Date transactionDate;
}
```

### 11.2 网页查询 (notes/31)

通过 `/gacha?s={sessionKey}&gachaType={type}` URL 玩家可在浏览器看：
- 时间倒序
- 分页 (10/页)
- 4/5 星高亮

→ `gachaInfo.getGachaRecordUrl()` 返回这个 URL。

---

## 12. 4 类 pity 状态

```java
public class PlayerGachaBannerInfo {
    private int pity4;                                     // 4 星 pity (0-9)
    private int pity5;                                     // 5 星 pity (0-89/79)
    private int[] failedFeaturedItemPulls = new int[2];    // 4/5 星歪了次数 (大保底)
    private int failedChosenItemPulls;                     // 武器命定值 (0-2)
    private int[] pityPool;                                // 双池平衡
    private int wishItemId;                                // 命定武器 ID
    private int totalPulls;                                // 总抽数
}
```

### 12.1 4 类 pity 的清零规则

```
pity5 → 抽到 5 星就清零 (无论 UP 还是常驻)
pity4 → 抽到 4 星就清零 (无论 UP 还是常驻)
failedFeaturedItemPulls[5] → 抽到 UP 5 星清零 (歪了不清)
failedFeaturedItemPulls[4] → 抽到 UP 4 星清零
failedChosenItemPulls → 抽到命定武器清零, 抽到非命定 5 星 +1, 达 2 强制命定
pityPool[5][1/2] → 抽到对应池子清零
```

→ **4 类 pity 独立维护**，每个有自己的清零规则。

### 12.2 BannerType 共享 vs 独立

```java
public PlayerGachaBannerInfo getBannerInfo(GachaBanner banner) {
    int gachaType = banner.getGachaType();
    return bannerInfos.computeIfAbsent(gachaType, k -> new PlayerGachaBannerInfo());
}
```

→ **按 gachaType 共享 pity** —— 同 type 的 banner 共享保底。
→ 例：CHARACTER (301) 不同 schedule 但**保底共享**（"上池没出，下池继续保底"）。

→ 但 EVENT (301) 和 CHARACTER (301) 也共享 —— legacy 兼容。

---

## 13. 完整抽卡时序

```
[玩家点击抽卡]
   ↓ DoGachaReq { scheduleId, times }
HandlerDoGachaReq → GachaSystem.doPulls(player, scheduleId, times):

   1. 合法性检查 (times in {1, 10})
   2. 武器背包空间检查 (notes/38)
   3. 找 banner (gachaBanners.get(scheduleId))
   4. 检查 BEGINNER 总抽限 (20)
   5. 扣 cost (notes/38 inventory.payItem)
   6. 总抽数 +times
   7. 创建临时 BannerPools (避免污染原 banner)
   8. C6 移除策略 (如开启 removeC6FromPool)
   
   for (i = 0; i < times; i++):
     9. doPull:
        a. incPityAll (pity4++ / pity5++)
        b. lerp 算 5/4/3 权重
        c. drawRoulette 选稀有度
        d. 命中 5/4 星 → doRarePull
           - epitomized + pityEpitomized → 强制命定
           - pityFeatured / rollFeatured → UP
           - 歪了 → doFallbackRarePull
              - 双池平衡 (pool balance)
           - 命定值更新
        e. 命中 3 星 → 随机 fallback3
     10. 持久化 GachaRecord (gachas collection)
     11. Constellation 检查 (-2 武器 / -1 新角色 / 0-6 命座等级)
     12. Stardust/Starglitter 计算
     13. addItem 入背包 (notes/38)
        (角色 itemId+100 → 命座物品自动给)
   end for
   
   14. 一次性发 stardust/starglitter
   15. PacketDoGachaRsp 回客户端
   16. BattlePass TRIGGER_GACHA_NUM

[客户端]
   播放抽卡动画
   显示 GachaItem 列表
   transferItem 处理 (满命转 starglitter 动画)
```

→ **完整时序 16 步** —— 每次 10 连约 100-200ms 服务器处理时间。

---

## 14. 设计模式总结

### 14.1 数据驱动 + 配置热重载

```
Banners.json (DataLoader)
   ↓ load
GachaBanner × N
   ↓
WatchService 监听 Banners.json
   → 自动 reload
```

→ 运营可热调银，**零停机**。

### 14.2 BannerType 枚举驱动差异

```
BannerType.weights5_wpn vs DEFAULT_WEIGHTS_5
BannerType.eventChance5: 50 vs 75
```

→ 6 种 BannerType **共享代码路径**，差异仅在常量数组。

### 14.3 临时 Pool 副本

```java
BannerPools pools = new BannerPools(banner);
// ★ 修改 pools 不影响原 banner
```

→ C6 移除等"per pull" 操作只影响**当次** —— 经典副本模式（呼应 notes/51 Tower）。

### 14.4 4 类 pity 独立维护

```
pity4 / pity5 → 稀有度保底
failedFeaturedItemPulls → 大保底
failedChosenItemPulls → 命定值
pityPool → 池平衡
```

→ **保底语义解耦**——每类 pity 解决不同问题。

### 14.5 ID 偏移规约

```java
int constItemId = itemId + 100;   // 命座 = 角色 ID + 100
```

→ 简单的命名规约 = 无需额外查表。

### 14.6 4 层概率模型（notes/21 总结）

```
[Layer 1] 整体保底: lerp 软保底 + 90/80 硬保底
[Layer 2] UP 保底: failedFeatured 歪一次必出
[Layer 3] 命定值: 武器 2 次失败强制命定
[Layer 4] 池平衡: 双池均衡分布
```

→ 4 层独立 + 叠加 = 商业级抽卡数学。

---

## 15. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 客户端发"我抽到 5 星" | ✗ 服务器算 |
| 篡改 pity 计数 | ✗ 服务器存 |
| 改 wishItemId | ✗ 服务器存 |
| 不扣资源抽卡 | ✗ payItem 服务器执行 |
| 改 banner pool | ✗ 服务器存 (除非有 DB 权限) |
| GachaRecord 伪造 | ✗ 服务器写 |
| 看到别人 wishItemId | ✗ 不在客户端展示其他玩家 |

→ Gacha 系统**反作弊极强**——商业核心代码必须无懈可击。

---

## 16. 关键收获

1. **GachaSystem 450 行 + GachaBanner 267 行 + 持久化** 共 717 行支撑抽卡
2. **6 种 BannerType**：STANDARD / BEGINNER / EVENT / CHARACTER / CHARACTER2 / WEAPON
3. **武器池特殊**：eventChance 75% (vs 50%) / 80 抽硬保底 / 只 pool2
4. **CHARACTER vs CHARACTER2 共享 gachaType 301** —— 不同 schedule 但同 pity
5. **4 套 fallback pool**：4-star × 2 + 5-star × 2 = 双池设计
6. **autoStripRateUpFromFallback**：UP 自动从 fallback 剥离
7. **doPull 4 步**：incPity → lerp 算权重 → drawRoulette → 分发到 doRarePull/fallback3
8. **lerp 软保底**（呼应 notes/21）：72 抽后权重渐高，90/80 硬保底
9. **doRarePull 命运修正逻辑**：epitomized > pityFeatured > rollFeatured > fallback
10. **doFallbackRarePull 双池平衡**：pityPool 计数 → 较大权重池优先
11. **4 类 pity 独立**：pity4/5 / failedFeatured / failedChosen / pityPool
12. **命定值 (epitomized) 武器专属**：3 次 5 星内必出选定武器
13. **C6 移除策略**（非原版扩展）：满命角色不再出，10 连内动态移除
14. **Stardust/Starglitter 经济**：4-25 starglitter / 15 stardust，按稀有度+命座等级
15. **命座物品 ID = 角色 itemId + 100**：简单偏移规约
16. **transferItem 机制**：满命转换由客户端动画展示，服务器发 transfer 通知
17. **Banner 时间窗口**：限时 banner 按 begin/end 过滤，STANDARD 永久
18. **WatchService 热重载**：Banners.json 修改自动 reload，零停机调整
19. **gachas collection 持久化**：每次抽卡一条 DB 记录，玩家可网页查
20. **反作弊极强**：商业核心代码不容许伪造

---

## 17. 一句话总结

> **GachaSystem = 商业核心引擎 (717 行) + 6 种 BannerType × 4 套 fallback pool × 4 类 pity × 命定值 × 双池平衡; lerp 软保底 + 50/75 eventChance + 大保底 + 命定值 4 层概率模型; 临时 BannerPools 副本避免污染 + C6 移除非原版扩展 + Stardust/Starglitter 转换经济 + 命座 itemId+100 ID 规约 + transferItem 客户端动画通知 + WatchService 热重载 + gachas collection 持久化。**
> 
> **设计哲学: 数据驱动 (Banners.json) + 4 层概率独立 (保底/UP/命定/池平衡) + BannerType 枚举差异化 + 临时副本不污染原 banner + 反作弊全服务器算——这是商业级抽卡的标准实现.**

---

**前置笔记**：
- notes/21 抽卡数学层 - 4 层保底叠加 + 线性插值软保底
- notes/30 持久化 - gachas collection
- notes/31 Dispatch HTTP - /gacha URL 玩家网页查抽卡历史
- notes/38 Inventory - payItem (扣相遇之缘) + addItem (出货)
- notes/41 事件总线 - TRIGGER_GACHA_NUM 战令任务
- notes/46 GameServer - GachaSystem 是 14 之一
- notes/47 Plugin/Event - WatchService 订阅 GameServerTickEvent

**关联文件**：
- `GachaSystem.java`(450) - 核心运行时
- `GachaBanner.java`(267) - 配置 + 6 BannerType
- `PlayerGachaInfo.java` - 持久化总信息
- `PlayerGachaBannerInfo.java` - per banner pity 状态
- `GachaRecord.java` - 抽卡记录 (gachas collection)
- `Banners.json` (data) - 热重载配置

**研究的源代码**: 717 行 Gacha 核心 + Banners.json 配置驱动。
