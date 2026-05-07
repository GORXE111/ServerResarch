# 21 · Gacha / 抽卡系统 · 商业核心的伪随机数学

抽卡是这类游戏的**商业核心** —— 决定了玩家充值意愿、留存周期、商业回报。**完全服务器权威**（任何客户端伪造概率 = 经济崩盘）。本笔记还原服务端 450 行代码里的概率机制：**保底 + UP + 定轨 + Pool balance** 四层叠加。

> 核心代码：`game/gacha/GachaSystem.java`（450 行）+ `GachaBanner.java`（267 行）+ `PlayerGachaBannerInfo.java` + `GachaRecord.java`

---

## 1. 整体架构

```
GachaSystem (全局, 单例)
  ├── gachaBanners Map<scheduleId, GachaBanner>
  ├── banner.json watcher (热重载)
  └── doPulls(player, scheduleId, times)  ← 抽卡总入口

GachaBanner (一个池子的元数据)
  ├── BannerType (STANDARD / BEGINNER / CHARACTER / CHARACTER2 / WEAPON)
  ├── beginTime / endTime / gachaTimesLimit
  ├── costItemId / costItemAmount (单抽消耗) + costItemId10 / costItemAmount10 (十连消耗)
  ├── rateUpItems5[]  当期 UP 5 星
  ├── rateUpItems4[]  当期 UP 4 星
  ├── fallbackItems3/4/5[Pool1, Pool2]  歪了池
  ├── weights4[][] / weights5[][]   ★ 概率插值表
  ├── eventChance4 / eventChance5   ★ UP 概率（50% 角色池, 75% 武器池）
  ├── poolBalanceWeights4/5         ★ 池平衡（防止"角色出多了不出武器"）
  └── wishMaxProgress = 2           ★ 武器池定轨阈值

PlayerGachaBannerInfo (per Player per Banner)
  ├── totalPulls                    总抽数
  ├── pity5 / pity4                 距上次 5/4 星的抽数
  ├── pityPool[rarity][1|2]         分池保底计数
  ├── failedFeaturedItemPulls[rarity]  上次是否歪了
  ├── wishItemId                    选定的定轨 5 星 (武器池)
  └── failedChosenItemPulls         没出定轨次数

GachaRecord (持久化每次抽卡, MongoDB)
  ├── itemId / uid / gachaType / timestamp
  └── 玩家"抽卡记录"页面通过 HTTP /gacha?s=token 拉取
```

---

## 2. 五种 BannerType（每池有不同默认概率）

```java
public enum BannerType {
    STANDARD   (200, 224, DEFAULT_WEIGHTS_4, DEFAULT_WEIGHTS_5,        50, 50, ...);
    BEGINNER   (100, 224, DEFAULT_WEIGHTS_4, DEFAULT_WEIGHTS_5,        50, 50, ...);
    EVENT      (301, 223, DEFAULT_WEIGHTS_4, DEFAULT_WEIGHTS_5_CHARACTER, 50, 50, ...);
    CHARACTER  (301, 223, DEFAULT_WEIGHTS_4, DEFAULT_WEIGHTS_5_CHARACTER, 50, 50, ...);
    CHARACTER2 (400, 223, DEFAULT_WEIGHTS_4, DEFAULT_WEIGHTS_5_CHARACTER, 50, 50, ...);
    WEAPON     (302, 223, DEFAULT_WEIGHTS_4_WEAPON, DEFAULT_WEIGHTS_5_WEAPON, 75, 75, ...);
}
```

不同池区别：
- **STANDARD 常驻**：50/50 出常驻 5 星，概率较低
- **CHARACTER / CHARACTER2 角色池**：50/50 (UP vs 常驻歪)
- **WEAPON 武器池**：**75/25** (UP 概率高) + **定轨系统**

cost item:
- `224` = 相遇之缘（常驻/新手）
- `223` = 纠缠之缘（限时/角色/武器）

`gachaType` 是客户端识别用：100/200/301/302/400 等

---

## 3. 概率系统的核心：Linear Interpolation Pity（线性插值保底）

### 3.1 Weight 表的天才设计

```java
// 5 星权重 (默认, STANDARD 池)
static final int[][] DEFAULT_WEIGHTS_5 = {{1,75}, {73,150}, {90,10000}};
//                                          ↑       ↑          ↑
//                                       前 72 抽   软保底     硬保底
```

**含义解读**：
- `{1, 75}`: 第 1 抽时，5 星权重 = 75
- `{73, 150}`: 第 73 抽时，5 星权重 = 150
- `{90, 10000}`: 第 90 抽时，5 星权重 = 10000（满权重 = 必出）

**插值公式**（`Utils.lerp(pity, weights)`）：
- 第 1-72 抽：保持 75（基础概率 0.75%）
- 第 73-89 抽：从 75 线性增长到 10000（**软保底期间概率快速提升**）
- 第 90 抽：10000（硬保底，**100% 出 5 星**）

### 3.2 角色池 vs 武器池的差异

```java
DEFAULT_WEIGHTS_5_CHARACTER = {{1,80},  {73,80},  {90,10000}};
// 角色池: 基础概率略高 (0.8%), 软保底从 73 抽开始

DEFAULT_WEIGHTS_5_WEAPON    = {{1,100}, {62,100}, {73,7800}, {80,10000}};
// 武器池: 基础概率 1%, 软保底从 62 抽开始, 80 抽硬保底
```

→ **武器池硬保底 80 抽**（vs 角色池 90 抽），**软保底也更早**——所以武器池"看起来更容易抽"，配合 75% UP 概率。

### 3.3 实际感觉到的"概率突变"

```
第 70 抽: 0.75%    ← 还在低概率
第 75 抽: ~5%     ← 开始爬升（玩家感受："最近多抽几把肯定出"）
第 80 抽: ~32%
第 85 抽: ~65%
第 89 抽: ~99%
第 90 抽: 100%
```

→ **软保底是玩家"感觉手气好"的来源**——其实就是数学上的线性概率提升。这套机制让玩家觉得"再抽几把一定出"，**而不是平均概率拖到 90 抽才命中**——大大改善游戏体验。

---

## 4. drawRoulette 算法（带 cutoff 的加权随机）

```java
private synchronized int drawRoulette(int[] weights, int cutoff) {
    int total = 0;
    for (int weight : weights) total += weight;
    
    // ★ 关键: 取 min(total, cutoff) 作为 roll 上限
    int roll = ThreadLocalRandom.current().nextInt((total < cutoff) ? total : cutoff);
    
    int subTotal = 0;
    for (int i = 0; i < weights.length; i++) {
        subTotal += weights[i];
        if (roll < subTotal) return i;
    }
    return 0;
}
```

**精髓**：cutoff 用 10000。如果 weights 之和 > 10000，意味着**前面几个权重已经吃满概率，后面的根本不会被选中**。这就是"硬保底"的实现：

```
weights = [10000, 0, 0]    // 100% 选第 0 个
cutoff = 10000
roll ∈ [0, 10000)
subTotal[0] = 10000 → 总是命中第 0 个
```

→ **同一个函数同时实现"加权随机" + "10000=必出" 两个功能**。优雅。

---

## 5. 单次抽卡完整流程（doPull）

```java
private synchronized int doPull(GachaBanner banner, PlayerGachaBannerInfo gachaInfo, BannerPools pools) {
    // 1. 全部 pity 计数器 ++
    gachaInfo.incPityAll();
    
    // 2. 决定稀有度（5/4/3 星）
    int[] weights = {
        banner.getWeight(5, gachaInfo.getPity5()),     // 5 星权重 (插值)
        banner.getWeight(4, gachaInfo.getPity4()),     // 4 星权重 (插值)
        10000                                            // 3 星兜底
    };
    int levelWon = 5 - drawRoulette(weights, 10000);
    
    // 3. 按稀有度选具体物品
    return switch (levelWon) {
        case 5:
            gachaInfo.setPity5(0);   // 重置 5 星 pity
            yield doRarePull(pools.rateUpItems5, pools.fallbackItems5Pool1, pools.fallbackItems5Pool2, 5, banner, gachaInfo);
        case 4:
            gachaInfo.setPity4(0);
            yield doRarePull(pools.rateUpItems4, pools.fallbackItems4Pool1, pools.fallbackItems4Pool2, 4, banner, gachaInfo);
        default:
            yield getRandom(banner.getFallbackItems3());
    };
}
```

→ 三步走：
1. **pity 计数** ++
2. **rarity 判定**（drawRoulette + cutoff 10000）
3. **rarity 命中后再选具体 item**

---

## 6. UP（featured）选择：50/50 大保底机制

```java
private synchronized int doRarePull(int[] featured, int[] fallback1, int[] fallback2, 
                                     int rarity, GachaBanner banner, PlayerGachaBannerInfo gachaInfo) {
    boolean epitomized = banner.hasEpitomized() && rarity == 5 && gachaInfo.getWishItemId() != 0;
    boolean pityEpitomized = (gachaInfo.getFailedChosenItemPulls() >= banner.getWishMaxProgress());
    
    // ★ 大保底: 上一次歪了, 这次必出 UP
    boolean pityFeatured = (gachaInfo.getFailedFeaturedItemPulls(rarity) >= 1);
    
    // 50/50 投硬币 (角色池) 或 75/25 (武器池)
    boolean rollFeatured = (this.randomRange(1, 100) <= banner.getEventChance(rarity));
    
    boolean pullFeatured = pityFeatured || rollFeatured;
    
    int itemId = 0;
    if (epitomized && pityEpitomized) {
        // 武器池定轨: 累计失败 wishMaxProgress(2) 次, 第 3 次必出指定
        gachaInfo.setFailedFeaturedItemPulls(rarity, 0);
        itemId = gachaInfo.getWishItemId();
    } else {
        if (pullFeatured && featured.length > 0) {
            // 出 UP 物品: 重置失败计数
            gachaInfo.setFailedFeaturedItemPulls(rarity, 0);
            itemId = getRandom(featured);
        } else {
            // 歪了: 失败计数 +1, 走 fallback (常驻池)
            gachaInfo.addFailedFeaturedItemPulls(rarity, 1);
            itemId = doFallbackRarePull(fallback1, fallback2, rarity, banner, gachaInfo);
        }
    }
    
    if (epitomized) {
        if (itemId == gachaInfo.getWishItemId()) {
            gachaInfo.setFailedChosenItemPulls(0);
        } else {
            gachaInfo.addFailedChosenItemPulls(1);
        }
    }
    return itemId;
}
```

**三种保底机制层叠**：

### 6.1 小保底（pity counter）
- 决定**何时出 5 星**
- 通过 `weights5` 线性插值实现（90 抽硬保底）

### 6.2 大保底（featured pity）
- 决定**5 星是 UP 还是歪了**
- 上次出常驻（歪了）→ `failedFeaturedItemPulls = 1` → 这次**必出 UP**
- 上次出 UP → `failedFeaturedItemPulls = 0` → 重新 50/50

### 6.3 定轨（武器池 epitomized，仅 WEAPON）
- 玩家选 1 个 `wishItemId`（指定想要的 UP 5 星武器）
- 抽 5 星但**没出指定**那个 → `failedChosenItemPulls += 1`
- 累计 2 次失败 → 第 3 次 5 星**必出选定**

```java
// 简化逻辑
if (epitomized && failedChosenItemPulls >= 2) {
    return wishItemId;  // 直接保底
}
```

→ 武器池 5 星**最多 3 次内必出指定武器**（虽然可能是 5×3=15 个 5 星，理论 3*90=270 抽内必出）。

---

## 7. Pool Balance（池平衡）—— 第四层保底

```java
private static int[][] poolBalanceWeights5 = {{1,30}, {147,150}, {181,10230}};
```

**问题**：常驻池 fallback 有 6 个角色 + 10 把武器。如果纯随机，可能**连续 100+ 抽都不出武器**。

**解法**：分别记录 `pityPool[5][1]`（常驻 5 星角色池保底）和 `pityPool[5][2]`（常驻 5 星武器池保底）。如果一边没出太久，**插值权重指数级提升**：

```
pityPool[5][2] = 1   → poolBalanceWeight = 30
pityPool[5][2] = 100 → poolBalanceWeight ≈ 100  
pityPool[5][2] = 180 → poolBalanceWeight ≈ 10230  (基本必中)
```

→ 这是**第四层伪随机控制**：
1. 整体 pity（出不出 5 星）
2. featured pity（UP 还是常驻）
3. epitomized（武器池定轨）
4. **pool balance**（常驻池内部角色 vs 武器分布）

---

## 8. doPulls 总入口（含反作弊）

```java
public synchronized void doPulls(Player player, int scheduleId, int times) {
    // 反作弊 1: times 必须是 1 或 10
    if (times != 10 && times != 1) {
        player.sendPacket(new PacketDoGachaRsp(Retcode.RET_GACHA_INVALID_TIMES));
        return;
    }
    
    // 反作弊 2: 检查武器/角色背包不会溢出
    if (inventory.getInventoryTab(ITEM_WEAPON).getSize() + times > maxCapacity) {
        player.sendPacket(new PacketDoGachaRsp(Retcode.RET_ITEM_EXCEED_LIMIT));
        return;
    }
    
    // 反作弊 3: 检查总次数限制（新手池只能抽 20 次）
    if (gachaTimesLimit != Integer.MAX_VALUE && totalPulls + times > gachaTimesLimit) {
        player.sendPacket(new PacketDoGachaRsp(Retcode.RET_GACHA_TIMES_LIMIT));
        return;
    }
    
    // 反作弊 4: 扣货币（原石/纠缠之缘）
    ItemParamData cost = banner.getCost(times);
    if (!inventory.payItem(cost)) {
        player.sendPacket(new PacketDoGachaRsp(Retcode.RET_GACHA_COST_ITEM_NOT_ENOUGH));
        return;
    }
    
    // 进入抽卡循环
    gachaInfo.addTotalPulls(times);
    for (int i = 0; i < times; i++) {
        int itemId = doPull(banner, gachaInfo, pools);
        ItemData itemData = GameData.getItemDataMap().get(itemId);
        
        // 持久化每一抽
        GachaRecord gachaRecord = new GachaRecord(itemId, player.getUid(), banner.getGachaType());
        DatabaseHelper.saveGachaRecord(gachaRecord);
        
        // 处理重复角色 → 命座 + 星辉; 武器 → 星尘/星辉
        ...
    }
}
```

→ **整个方法 `synchronized`**——同一玩家同时只能抽一次（防止 race condition 刷出物品）。

---

## 9. 重复抽到角色：命座 + 货币转换

```java
int constellation = InventorySystem.checkPlayerAvatarConstellationLevel(player, itemId);
switch (constellation) {
    case -2:  // 武器
        switch (itemData.getRankLevel()) {
            case 5 -> addStarglitter = 10;     // 5 星武器 → 10 星辉
            case 4 -> addStarglitter = 2;      // 4 星武器 → 2 星辉
            default -> addStardust = 15;       // 3 星武器 → 15 星尘
        }
        break;
    case -1:  // 新角色
        gachaItem.setGachaItemNew(true);
        break;
    default:
        if (constellation >= 6) {  // C6 满命
            addStarglitter = (itemData.getRankLevel() == 5) ? 25 : 5;  // 慰问星辉
        } else {  // 重复角色 → 命座物品
            addStarglitter = (itemData.getRankLevel() == 5) ? 10 : 2;
            int constItemId = itemId + 100;  // 命座物品 id 约定
            ...
        }
        break;
}
```

→ **3 种货币转换路径**：
- 武器：直接转 starglitter (5/4 星) 或 stardust (3 星)
- 新角色：直接给角色卡
- 重复角色（未满命）：给命座物品 + 少量 starglitter
- 重复角色（C6 满命）：慰问 starglitter（不再给命座）

**Starglitter (无相之星辉) / Stardust (无相之星尘)** 是抽卡专用货币，可换商店物品（常驻 5 星武器、4 星角色等）。这是**经济闭环**——抽卡产出转换为可控商店购买力。

---

## 10. 抽卡历史的 HTTP 路由

```java
// GachaBanner.toProto (line 162)
String record = "http://...:port/gacha?s=" + sessionKey + "&gachaType=" + gachaType;
String details = "http://...:port/gacha/details?s=" + sessionKey + "&scheduleId=" + scheduleId;
info.setGachaRecordUrl(record);
info.setGachaProbUrl(details);
```

→ 抽卡历史和概率公示**走 HTTP 而非游戏 packet**。客户端打开"历史记录"时拉这个 URL，server 返回 HTML 表格。这是因为：
- 历史数据量可能很大（玩家抽过 1000+ 次）
- 用 HTTP 流式返回比 packet 更适合
- 概率公示是**法律要求**（中国大陆游戏法规要求公示），用网页展示更标准

---

## 11. Banner 配置热重载

```java
@Listener(EventListener.EventType.GAME_SERVER_TICK)
public synchronized void watchBannerJson(GameServerTickEvent tickEvent) {
    // 检测 banner.json 文件变化
    // 修改后无需重启服务器
}
```

→ banner.json 监听文件 mtime，**修改后下次 tick 自动重载**。**运营改 banner 不需要停服**——这是商业必须。

---

## 12. 关键设计精髓

### 12.1 完全服务器权威

抽卡是**绝对的经济敏感操作**。任何服务器侧的不一致 = 经济崩盘：
- 客户端不参与概率计算（连概率表都不知道）
- 客户端不存 pity counter（pity 只在服务器内存 + DB）
- 客户端只能发 `DoGachaReq{scheduleId, times}` 然后接收结果
- `synchronized` 防止 race condition
- 物品发放走 inventory 统一入口（notes/15 ActionReason.Gacha）

### 12.2 四层保底叠加

| 保底类型 | 触发 | 上限 |
|---|---|---|
| 整体 pity（pity5/pity4） | 5/4 星出货时机 | 90 抽 / 10 抽 |
| Featured pity（大保底） | UP 还是常驻 | 1 次歪了下次必 UP |
| Epitomized（定轨） | 指定武器（武器池） | 累计 2 次失败必出 |
| Pool balance（池平衡） | 常驻池角色 vs 武器 | 防长期不出某类 |

→ **数学伪随机 + 保底机制**让每个玩家都能在可预测的次数内得到目标——**降低焦虑, 提升留存**。

### 12.3 从软保底到硬保底的"伪随机感"

线性插值的妙处：玩家**感觉**自己"手气好"出货，其实是数学上必然概率提升。这比"前 89 抽 0.6% / 第 90 抽 100%" 的硬切换体验**好 10 倍**——前者像运气，后者像被强制。

### 12.4 货币转换闭环

抽卡 → 重复物品 → starglitter / stardust → 商店换其他卡 → 又能抽。**形成自循环**——避免"抽到重复就废了"的失落感。

### 12.5 配置驱动，无需停服

`banner.json` 热重载 + 时间窗自动启用 = **运营只改文件**，全自动切换。这是为什么每两周一次新 banner 不需要技术支持。

---

## 13. 反作弊总结

```java
1. synchronized 防 race condition
2. times 必须是 1 或 10 (RET_GACHA_INVALID_TIMES)
3. 背包容量检查 (RET_ITEM_EXCEED_LIMIT)
4. 总次数限制 (新手池 RET_GACHA_TIMES_LIMIT)
5. 货币足够 (RET_GACHA_COST_ITEM_NOT_ENOUGH)
6. 服务器内部 pity 计数, 客户端无法操控
7. 物品发放走 Inventory.addItem(ActionReason.Gacha)  
   - 进入审计日志
   - 触发 BattlePass / Quest 事件 (notes/15)
8. GachaRecord 持久化每次抽卡 - DB 全量审计
```

→ **所有抽卡操作有审计追溯**：从 GachaRecord（哪天抽了什么）到 ActionReason（每次 addItem 的来源）—— 公司客服可查任意玩家任意时间点的抽卡历史。

---

## 14. 给做抽卡系统开发者的提炼

1. **服务器绝对权威**——客户端连概率表都不该知道
2. **`synchronized` 是必须的**——多线程抽卡会刷出物品
3. **线性插值的伪随机**——比硬切换体验好 10 倍
4. **多层保底叠加**：整体 pity / featured pity / 定轨 / 池平衡
5. **重复物品要有出路**——starglitter/stardust 闭环避免失落感
6. **配置驱动 + 热重载**——运营改 banner 不能停服
7. **抽卡历史走 HTTP**——满足合规 + 数据量大
8. **审计无处不在**——GachaRecord + ActionReason 全量留痕
9. **物品发放统一入口**——走 Inventory.addItem 才能 trigger Quest/BattlePass
10. **概率公示是法律要求**（中国大陆）——单独网页展示

---

## 15. 数据规模感

- 5 种 BannerType
- 任意时刻 4 个 active banner（常驻 + 角色 1 + 角色 2 + 武器）
- 每 banner 平均 2-3 个 UP 5 星 + 3-5 个 UP 4 星
- 常驻池：6 个 5 星角色 + 10 把 5 星武器 + 24 个 4 星角色
- 玩家 GachaRecord：可存数千条/账号
- banner.json 每两周改一次

代码规模：
- `GachaSystem.java`：450 行（核心算法）
- `GachaBanner.java`：267 行（数据 + 配置）
- `PlayerGachaBannerInfo.java`：~100 行（玩家状态）
- `GachaRecord.java`：~50 行（持久化记录）
- 总核心 ~900 行 = **整个商业核心**

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/gacha/GachaSystem.java`（450 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/gacha/GachaBanner.java`（267 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/gacha/PlayerGachaBannerInfo.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/gacha/GachaRecord.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerDoGachaReq.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerGachaWishReq.java`
- 配置：`banner.json`（社区维护，运营级配置）
