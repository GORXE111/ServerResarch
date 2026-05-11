# Gadget 系统全景剖析

> 第 33 篇：所有**"非玩家、非怪物"的场景物体**——宝箱/机关/植物/风车/平台/载具/掉落物/锚点/视野点... 一切交互目标的运行时实现

---

## 0. 为什么这一篇重要

前 32 篇笔记里 Avatar（玩家）和 Monster（怪物）都各有深挖。但**场景中实际有交互的物体远不止这两类**：
- 你打开的**宝箱**
- 你按 F 摘的**白萝卜/晶蝶**
- 让你站上去触发机关的**按压板**
- 摆动旋转的**风车**
- 跟着轨道动的**平台**
- 七天神像
- 视野点
- 篝火 / 锅炉 / 椅子
- 浪船 / 沙海舟（载具）
- 怪物丢出去的**斧头**（武器实体）
- 雷弹丸 / 火药桶
- 凋零之缘 / 凋零兵团（须弥）
- 元素方碑（须弥沙漠）
- 玩家自家家具（尘歌壶）

所有这些**都是 Gadget**。它和 Avatar/Monster 平起平坐——是场景里的第三大实体类。

---

## 1. EntityType 全表：73 种实体

`EntityType.java` 一共 **73 种** —— 怪物只占其中 1 种 (Monster=2)，**Gadget 系**占 30+ 种：

```java
public enum EntityType {
    None (0), Avatar (1), Monster (2), Bullet (3),
    AttackPhysicalUnit (4), AOE (5), Camera (6), EnviroArea (7),
    Equip (8), MonsterEquip (9), Grass (10), Level (11),
    NPC (12),
    
    // ===== Gadget 子类 =====
    TransPointFirst (13), TransPointFirstGadget (14),   // 七天神像/锚点
    TransPointSecond (15), TransPointSecondGadget (16),
    DropItem (17),                                       // 掉落物
    Field (18),
    Gadget (19),                                         // 通用机关
    Water (20),
    GatherPoint (21), GatherObject (22),                 // 采集物（矿/草/花）
    AirflowField (23),                                   // 风场
    SpeedupField (24),                                   // 加速带
    Gear (25),
    Chest (26),                                          // 宝箱
    EnergyBall (27),                                     // 元素球
    ElemCrystal (28),                                    // 元素晶柱
    Timeline (29),
    Worktop (30),                                        // 工作台/面板
    Team (31), Platform (32),                            // 移动平台
    AmberWind (33),                                      // 蒙德风种子
    EnvAnimal (34),                                      // 环境动物
    SealGadget (35),                                     // 封印类机关
    Tree (36), Bush (37),                                // 树/灌木
    QuestGadget (38),                                    // 任务道具
    Lightning (39),
    RewardPoint (40), RewardStatue (41),                 // 神像
    MPLevel (42), WindSeed (43),                         // 蒙德风种
    MpPlayRewardPoint (44),
    ViewPoint (45),                                      // 视野点（解锁地图）
    RemoteAvatar (46),
    GeneralRewardPoint (47),
    PlayTeam (48),
    OfferingGadget (49),                                 // 供奉系统（神樱/圣樱）
    EyePoint (50),                                       // 望远镜点
    MiracleRing (51),                                    // 奇迹之环
    Foundation (52),                                     // 尘歌壶地基
    WidgetGadget (53),                                   // 小道具（捕虫网等）
    Vehicle (54),                                        // 载具（浪船）
    DangerZone (55),
    EchoShell (56),                                      // 海螺
    HomeGatherObject (57),                               // 尘歌壶采集物
    Projector (58),
    Screen (59),                                         // 屏幕
    CustomTile (60), FishPool (61), FishRod (62),
    CustomGadget (63),
    RoguelikeOperatorGadget (64),                        // 幻想真境剧诗
    ActivityInteractGadget (65),                         // 活动专用
    BlackMud (66),                                       // 黑泥（须弥）
    SubEquip (67),
    UIInteractGadget (68),
    NightCrowGadget (69),                                // 夜叉鸦传送
    Partner (70),                                        // 同伴
    DeshretObeliskGadget (71),                           // 赤砂之王方碑
    CoinCollectLevelGadget (72),                         // 集金挑战
    TrifleGadget (73),
    PlaceHolder (99);
}
```

**关键观察**：Gadget 类型**贯穿全游戏 5 年的内容更新**——从蒙德的风种子到须弥的赤砂之王方碑，每个新版本都加新 type。

---

## 2. Gadget 体系全图

```
                       GameEntity (父类)
                            ↓
              ┌──────────────┼──────────────┐
              ↓              ↓              ↓
      EntityAvatar    EntityMonster    EntityBaseGadget
                                            ↓
              ┌──────────────┬──────────────┬──────────────┐
              ↓              ↓              ↓              ↓
        EntityGadget   EntityItem    EntityVehicle   EntityClientGadget
        (310 行)       (78 行)       (60+ 行)        (84 行)
              ↓
        持有 GadgetContent ← 内容策略
              ↓
       ┌──────┬──────┬──────┬──────┬──────┬──────┐
       ↓      ↓      ↓      ↓      ↓      ↓      ↓
    Chest Worktop Reward Gather Object Screen ViewPoint
    Statue
    
    + NightCrow + DeshretObelisk + Offering ...
```

→ **两层抽象**：
- 第一层：**Entity 类型**（EntityGadget / EntityItem / EntityVehicle / EntityClientGadget）—— 决定**实体的本性**
- 第二层：**Content 策略**（GadgetChest / GadgetWorktop / ... 等 11 种）—— 决定**交互行为**

这是经典的**策略模式 + Entity-Component**：实体框架不变，内容可替换。

---

## 3. EntityGadget：核心运行时

`EntityGadget.java`（310 行）—— 最常见的 Gadget 实体。

### 3.1 字段（10 个核心）

```java
public class EntityGadget extends EntityBaseGadget implements ConfigAbilityDataAbilityEntity {
    @Getter @Setter private int pointType;        // 点位类型
    private Int2FloatMap fightProperties;          // 有 HP 的 gadget (火药桶等)
    @Getter @Setter private BaseRoute routeConfig; // 移动平台路径
    @Getter @Setter private int draftId;           // 草稿 ID (开发用)
    @Getter @Setter private int chestDropId;       // 宝箱掉落表
    @Getter @Setter private boolean chestShowCutscene; // 显示开箱动画
    @Getter boolean isPersistent;                  // 持久化标记
    @Getter @Setter private int stopValue = 0;    // Lua 控制变量
    @Getter @Setter private int startValue = 0;
}
```

### 3.2 状态机：`setState`

```java
@Override
public void setState(int state) {
    super.setState(state);
    val groupId = getGroupId();
    // 缓存到 SceneGroupInstance (持久化到 DB)
    if (groupId > 0) {
        var instance = getScene().getScriptManager().getCachedGroupInstanceById(groupId);
        if (instance != null) instance.cacheGadgetState(this);
    }
}
```

**关键设计**：Gadget 的状态**会持久化到 DB**：
- 你开过的宝箱 → ChestOpened 状态保存
- 你打开过的封印机关 → SealOpen 状态保存
- 玩家离线再上线 → 这些状态恢复（不会让你重新打开宝箱）

→ 这是 notes/30 提到的 **SceneGroupInstance** 半持久化机制的具体应用。

### 3.3 关键 `buildContent`：Content 策略选择

```java
@Override
public GadgetContent buildContent(CreateGadgetEntityConfig config) {
    if (this.getGadgetData() == null || this.getGadgetData().getType() == null) {
        return null;
    }
    
    return switch (this.getGadgetData().getType()) {
        case GatherObject     -> new GadgetGatherObject(this);   // 采集物
        case Worktop, SealGadget -> new GadgetWorktop(this);     // 工作台/封印
        case RewardStatue     -> new GadgetRewardStatue(this);   // 神像
        case Chest            -> new GadgetChest(this);          // 宝箱
        case Gadget, Platform -> new GadgetObject(this);         // 通用
        case Screen           -> new GadgetScreen(this);         // 屏幕
        case ViewPoint        -> new GadgetViewPoint(this);      // 视野点
        case NightCrowGadget  -> new GadgetNightCrow(this);      // 夜叉鸦
        case DeshretObeliskGadget -> new GadgetDeshretObelisk(this);
        case OfferingGadget   -> new GadgetOffering(this);
        default               -> null;
    };
}
```

**TODO refactor** 注释——开发者自己也觉得这个 switch 不优雅。

**这又是工厂模式**——按 GadgetType 字符串选择 Content 子类。如果加新类型只要：
1. EntityType 枚举加值
2. 写一个 `GadgetXxx extends GadgetContent`
3. 在这个 switch 加一行

→ 第 10 次"**注解+反射/工厂模式**"——但这里没用注解，是显式 switch（可能将来重构）。

### 3.4 `onInteract`：玩家按 F

```java
@Override
public void onInteract(Player player, GadgetInteractReq interactReq) {
    if (!isInteractEnabled()) return;
    
    if (this.getContent() == null) {
        Grasscutter.getLogger().warn("Missing Gadget content: {}", contentName);
        return;
    }
    
    boolean shouldDelete = this.getContent().onInteract(player, interactReq);
    //                            ↑ 委托给 Content 策略
    
    if (shouldDelete) {
        this.getScene().killEntity(this);   // 摘了草 → 草消失
    }
}
```

**返回 true = 自杀**：
- 采集物摘了消失
- 宝箱打开消失
- 一次性机关用过消失

**返回 false = 留着**：
- 工作台用了还能再用
- 神像供奉了还在那
- 视野点解锁了还在

---

## 4. 11 种 GadgetContent 策略

### 4.1 GadgetChest（宝箱 - 84 行）

宝箱是 grasscutter 中最复杂的 Gadget 之一：

```java
public boolean onInteract(Player player, GadgetInteractReq req) {
    // 1. 查找对应类型的 ChestInteractHandler
    val handler = chestInteractHandlerMap.get(getGadget().getGadgetData().getJsonName());
    //                                          ↑ "Chest_Common", "BossChest", etc.
    
    // 2. 两步交互（Boss 宝箱）
    if (req.getOpType() == InterOpType.INTER_OP_START && handler.isTwoStep()) {
        player.sendPacket(new PacketGadgetInteractRsp(..., InterOpType.INTER_OP_START));
        return false;   // 不删除, 等第二步
    }
    
    // 3. 真正打开
    boolean success;
    if (handler instanceof BossChestInteractHandler boss) {
        success = boss.onInteract(this, player, useCondensedResin);
    } else {
        success = handler.onInteract(this, player);
    }
    if (!success) return false;
    
    // 4. 状态改为 ChestOpened (持久化)
    getGadget().updateState(ScriptGadgetState.ChestOpened);
    player.sendPacket(new PacketGadgetInteractRsp(..., InteractType.INTERACT_OPEN_CHEST));
    return true;   // ★ 删除自己
}
```

#### NormalChestInteractHandler（普通宝箱）

```java
@Override
public boolean onInteract(GadgetChest chest, Player player) {
    player.earnExp(chestReward.getAdvExp());                  // 冒险阅历
    player.getInventory().addItem(201, chestReward.getResin()); // 体力(?)
    
    // ★ 世界等级影响摩拉 (WL 越高摩拉越多)
    var mora = chestReward.getMora() * (1 + (player.getWorldLevel() - 1) * 0.5);
    player.getInventory().addItem(202, (int)mora);
    
    // 固定掉落物
    for (int i = 0; i < chestReward.getContent().size(); i++) {
        chest.getGadget().getScene().addItemEntity(...);
    }
    
    // 随机掉落物
    var random = new Random(System.currentTimeMillis());
    for (int i = 0; i < chestReward.getRandomCount(); i++) {
        var index = random.nextInt(chestReward.getRandomContent().size());
        var item = chestReward.getRandomContent().get(index);
        chest.getGadget().getScene().addItemEntity(item.getItemId(), item.getCount(), ...);
    }
    return true;
}
```

**关键观察**：宝箱**不是直接 addItem**，而是**生成 EntityItem 实体**（`addItemEntity`）—— 物品作为可见实体落地，玩家走过去捡起。

→ 这就是为什么打开宝箱**看见物品飞出来**：那些是真的 EntityItem 实体。

#### BossChestInteractHandler（Boss 宝箱）

```java
public boolean onInteract(GadgetChest chest, Player player, boolean useCondensedResin) {
    // 优先级 1: 凋零之缘（每日活动宝箱）
    val blossomRewards = player.getScene().getWorld().getOwner()
        .getBlossomManager().onReward(player, chest.getGadget(), useCondensedResin);
    if (blossomRewards) return true;
    
    // 优先级 2: 周本 boss 宝箱
    val group = chestMetaGadget.getScene().getScriptManager().getGroupById(...);
    val monster = group.getMonsters().get(monsterCfgId);
    val reward = worldDataManager.getRewardByBossId(monster.getMonsterId());
    
    if (reward == null) {
        // 优先级 3: 副本宝箱
        return dungeonManager.getStatueDrops(player, useCondensedResin, chest.getGadget().getGroupId());
    }
    
    // 直接加到背包 (Boss 奖励一次性给)
    val rewards = Arrays.stream(reward.getPreviewItems())
        .map(param -> new GameItem(param.getId(), Math.max(param.getCount(), 1)))
        .toList();
    player.getInventory().addItems(rewards, ActionReason.OpenWorldBossChest);
    player.sendPacket(new PacketGadgetAutoPickDropInfoNotify(rewards));
    return true;
}
```

**两步交互的意义**：
- 客户端显示「使用 20 树脂打开？」对话框
- 玩家点确认 → 真正发请求
- 服务器扣树脂 + 发奖励

`isTwoStep()`：
| 宝箱类型 | 两步？ |
|---|---|
| NormalChest | ✗ 直接开 |
| BossChest | ✓ 树脂确认 |
| BlossomChest | ✓ 树脂确认 |

### 4.2 GadgetGatherObject（采集物 - 79 行）

白萝卜、晶蝶、铁矿、风车菊...

```java
public boolean onInteract(Player player, GadgetInteractReq req) {
    ItemData itemData = GameData.getItemDataMap().get(getItemId());
    if (itemData == null) return false;
    
    GameItem item = new GameItem(itemData, 1);
    player.getInventory().addItem(item, ActionReason.Gather);
    //                                       ↑ 100+ 个 ActionReason 之一
    
    // Lua 事件
    var scriptArgs = new ScriptArgs(getGadget().getGroupId(), 
        EventType.EVENT_GATHER, getGadget().getConfigId());
    getGadget().getScene().getScriptManager().callEvent(scriptArgs);
    
    // 广播给所有玩家
    getGadget().getScene().broadcastPacket(
        new PacketGadgetInteractRsp(getGadget(), InteractType.INTERACT_GATHER));
    
    return true;   // ★ 摘了消失
}
```

#### 采集 vs 落物两条路径

GadgetGatherObject 有两个方法：
- `onInteract(player, req)` —— 直接给 1 个进背包（白萝卜等）
- `dropItems(player)` —— 随机 1-2 个掉地上（矿物/水晶）

```java
public void dropItems(Player player) {
    int times = Utils.randomRange(1, 2);
    
    for (int i = 0; i < times; i++) {
        val createConfig = new CreateGadgetEntityConfig(itemData, 1)
            .setPlayerOwner(player)
            .setBornPos(getGadget().getPosition().nearby2d(1f).addY(2f));
            //                                    ↑ 抛物线起点偏移
        EntityItem item = new EntityItem(scene, createConfig);
        scene.addEntity(item);
    }
    scene.killEntity(this.getGadget(), ...);
}
```

→ 矿物/水晶**先变成 EntityItem 飞出来**——玩家要走过去捡。这就是为什么挖矿能看到水晶**飞起来落地**。

### 4.3 GadgetWorktop（工作台 - 76 行）

带多个选项按钮的机关：「升起平台」/「开启传送」/「重置谜题」等。

```java
public class GadgetWorktop extends GadgetContent {
    private IntSet worktopOptions;            // 可选项 ID 列表
    private WorktopWorktopOptionHandler handler;  // 回调
    
    public boolean onSelectWorktopOption(SelectWorktopOptionReq req) {
        if (this.handler != null) {
            this.handler.onSelectWorktopOption(this, req.getOptionId());
        }
        return false;
    }
}
```

**用法**：
- 配置 worktopOptions = {1, 2, 3}（三个按钮）
- Lua 脚本注册 `setOnSelectWorktopOptionEvent(handler)`
- 玩家点按钮 1 → handler 调用 → Lua 执行剧情

**典型场景**：稻妻、须弥地区的"机关谜题"——玩家进入区域看到按钮面板，按不同按钮触发不同效果。

### 4.4 GadgetItemContent（落地物 - 68 行）

EntityItem 的内容策略——表示**地上一个可拾取的物品**。
- 自动拾取范围内（小物品）
- 必须走过去（大物品）
- 触发 `PacketGadgetAutoPickDropInfoNotify`

### 4.5 GadgetRewardStatue（神像 - 41 行）

七天神像 —— 供奉/解锁地图/恢复 HP。

### 4.6 GadgetClient（客户端 Gadget - 61 行）

特殊：见 §5。

### 4.7 GadgetScreen / GadgetViewPoint / GadgetNightCrow / GadgetDeshretObelisk / GadgetOffering

各 28-37 行的小策略，处理特定交互。

---

## 5. EntityClientGadget：玩家创造的 Gadget

`EntityClientGadget.java`（84 行）—— 这是个特殊概念。

### 5.1 什么是 Client Gadget

```java
public class EntityClientGadget extends EntityBaseGadget {
    public EntityClientGadget(Scene scene, EvtCreateGadgetNotify notify, ...) {
        super(scene, createConfig);
        this.id = notify.getEntityId();   // ★ 客户端分配的 ID
    }
}
```

**不同于 EntityGadget**：
- ID 由**客户端**分配，不是 `world.getNextEntityId(GADGET)`
- 来源是 `EvtCreateGadgetNotify`（客户端发来的"我创建了一个 gadget"通知）
- AbilityManager 走 `getPlayerOwner()` 而不是 host

### 5.2 典型例子

- **角色技能创造的物体**：温迪的风场 / 钟离的岩柱 / 砂糖的奇术风之印
- **客户端动画产物**：火焰留痕 / 元素粒子残影
- **临时投射物**：捕虫网创造的网（短暂存在）

**这些都不需要"服务器权威"**——客户端创建出来，告诉服务器一声，服务器记录给其他玩家广播。

### 5.3 完整流程

```
[客户端] 温迪放风场技能
    ↓
   EvtCreateGadgetNotify { entityId=999, gadgetId=42012 }
    ↓
[服务器] 创建 EntityClientGadget(id=999)
    ↓ 广播
[其他玩家客户端] 看到风场出现
```

→ 这又印证 grasscutter 的**混合权威**：客户端能"创造"实体，服务器只是个登记处。

### 5.4 数量对比

| 类型 | 由谁创建 | 比例（粗估）|
|---|---|---|
| EntityGadget | 服务器 spawn (SceneScript) | 99% |
| EntityClientGadget | 客户端 EvtCreateGadgetNotify | 1% |

但 EntityClientGadget 的**频次很高**——战斗中每秒可能多次（技能特效）。

---

## 6. EntityItem：掉落物

`EntityItem.java`（78 行）—— 怪物死后/采集后**飞出来的物品**。

```java
@Override
public SceneEntityInfo toProto() {
    val gadgetInfo = new SceneGadgetInfo(getGadgetId());
    gadgetInfo.setBornType(GadgetBornType.GADGET_BORN_IN_AIR);  // ★ 空中诞生
    gadgetInfo.setAuthorityPeerId(this.getWorld().getHostPeerId());
    gadgetInfo.setEnableInteract(isInteractEnabled());
    ...
}
```

**关键**：
- `GADGET_BORN_IN_AIR` —— 客户端展示**飞出来落地**的动画
- 落地后可拾取
- 一段时间不捡会消失（gc）

### 6.1 怪物掉落 → EntityItem 的完整链路

```
[1] EntityMonster.onDeath
    ↓
[2] dropSubfield / GameEntity.dropSubfieldItem
    ↓ 按 DropTable 概率
[3] 生成 EntityItem (每个掉落物一个实体)
    ↓
[4] scene.addEntity → 广播给所有客户端
    ↓
[5] 客户端展示飞舞 + 玩家拾取
    ↓
[6] addItem(player.inventory) (服务器记账)
    ↓
[7] killEntity(EntityItem) (从场景移除)
```

### 6.2 DropTable 算法

`GameEntity.dropSubfieldItem` 第 287-335 行：
```java
switch (dropTableEntry.getRandomType()) {
    case 0: // select one (按权重选一个)
        int weightCount = sum_of_all_weights;
        int randomValue = random.nextInt(weightCount);
        // 命中区间则选中
        for (entry : dropVec) {
            if (randomValue 在 [weightCount, weightCount+entry.weight)) {
                itemsToDrop.put(entry.itemId, count_random);
            }
        }
        break;
    case 1: // select various (每个独立掷骰)
        for (entry : dropVec) {
            if (entry.getWeight() < random.nextInt(10000)) {
                itemsToDrop.put(entry.itemId, count_random);
            }
        }
        break;
}
```

→ 两种掉落策略：**互斥（任选一）** vs **独立（每个独立判断）**。

**互斥用例**：稀有掉落（强弱锁）—— "出了金币就不出银币"
**独立用例**：通用掉落（材料）—— "每个材料独立概率"

---

## 7. EntityVehicle：载具

`EntityVehicle.java`（60+ 行）—— 浪船 / 沙海舟 / 远古船。

### 7.1 与普通 Gadget 不同

```java
public class EntityVehicle extends EntityBaseGadget {
    @Getter private final Player owner;            // ★ 谁拥有
    @Getter @Setter private float curStamina;      // ★ 体力（浪船有"耐久"）
    @Getter private final List<VehicleMember> vehicleMembers;  // ★ 谁上船了
}
```

**特殊属性**：
- 有**所有者**（不是公共的，是玩家私有的）
- 有**体力/耐久**条
- 有**乘员列表**（联机时多人共乘）
- 注入 `FIGHT_PROP_CUR_SPEED` + `FIGHT_PROP_CHARGE_EFFICIENCY`

### 7.2 浪船在 Multiplayer 的有趣行为

- 房主上船 → 浪船 host 权 = 房主
- 其他玩家上船 → 作为 VehicleMember 同步状态
- 浪船**移动 AI 在房主客户端跑**（和 monster 一样的 host 权机制）

---

## 8. Platform 移动平台：3 种 Route

`EntityGadget` 的 `routeConfig` 字段决定平台动画：

```java
EntityGadget.startPlatform()
    ↓
    routeConfig instanceof PointArrayRoute → 按点列表移动
    routeConfig instanceof ConfigRoute     → 按配置脚本移动  
    routeConfig instanceof AbilityRoute    → 由能力驱动
```

### 8.1 ConfigRoute

`EntityGadget.schedulePlatform()`：
```java
var route = this.getScene().getSceneRouteById(configRoute.getRouteId());
var points = route.getPoints();

double distance = points[currIndex].getPos().computeDistance(points[currIndex + 1].getPos());
double time = 1000 * distance / points[currIndex].getTargetVelocity();
time += this.getScene().getSceneTime();
this.getScene().getScheduledPlatforms().put(this.getId(), time);
//                              ↑ 调度系统会在到达时间触发下一段
```

→ **服务器计算每段移动时间**，调度系统在 sceneTime 到达时**触发下一段**。客户端跟着动。

### 8.2 PointArrayRoute

`scheduleArrayPoints()`：用于"随机点位"的平台——一组路标，按选择顺序行进。

```java
val routePointList = platformPointList.stream()
    .map(x -> Arrays.stream(points).filter(y -> y.getPointId() == x).findFirst().orElse(null).toProto())
    .toList();
pointArrayRoute.setRoutePoints(routePointList);
```

### 8.3 用途

| 类型 | 例子 |
|---|---|
| ConfigRoute | 循环旋转平台、固定轨道平台 |
| PointArrayRoute | 玩家解谜后激活的"逐点跳跃"平台 |
| AbilityRoute | 风元素能力推动的平台 |

---

## 9. Gadget 状态系统

### 9.1 State 是什么

`ScriptGadgetState` 是个枚举：
- `Default` —— 默认状态
- `ChestOpened` —— 宝箱已开
- `SealOpen` —— 封印已解
- `GearAction1/2/3` —— 齿轮的不同阶段
- ...

每个 Gadget 都有一个 state，**决定它的视觉表现和可交互性**。

### 9.2 state 改变的副作用

```java
@Override
public void setState(int state) {
    super.setState(state);
    val groupId = getGroupId();
    if (groupId > 0) {
        var instance = getScene().getScriptManager().getCachedGroupInstanceById(groupId);
        if (instance != null) instance.cacheGadgetState(this);  // ★ 持久化
    }
}
```

**state 触发链**：
1. 状态改变
2. 持久化到 SceneGroupInstance
3. 广播 `PacketGadgetStateNotify`
4. 触发 Lua 事件 `EVENT_GADGET_STATE_CHANGE`
5. Lua 可能触发其他 gadget 联动（"开宝箱 → 升起平台"）

---

## 10. 死亡级联

`EntityGadget.onDeath()`：
```java
@Override
public void onDeath(int killerId) {
    super.onDeath(killerId);
    
    // 1. 加入死亡列表（持久化, 重连不复活）
    if (this.getSpawnEntry() != null) {
        this.getScene().getDeadSpawnedEntities().add(this.getSpawnEntry());
    }
    
    // 2. Lua 通用事件
    getScene().getScriptManager().callEvent(
        new ScriptArgs(this.getGroupId(), EventType.EVENT_ANY_GADGET_DIE, this.getConfigId()));
    
    // 3. SceneGroupInstance 持久化
    SceneGroupInstance groupInstance = getScene().getScriptManager().getCachedGroupInstanceById(this.getGroupId());
    if (groupInstance != null && getConfigId() > 0)
        groupInstance.getDeadEntities().add(getConfigId());
    
    // 4. 凋零之缘特殊处理
    val hostBlossom = getScene().getWorld().getHost().getBlossomManager();
    val removedChest = hostBlossom.getSpawnedChest().remove(getConfigId());
    if (removedChest != null) {
        getScene().unregisterDynamicGroup(getGroupId());
        getScene().getScriptManager().callEvent(
            new ScriptArgs(getGroupId(), EventType.EVENT_BLOSSOM_CHEST_DIE, getConfigId()));
        hostBlossom.buildNextCamp(getGroupId());  // 触发下一波凋零
    }
}
```

→ Gadget 死后**触发 Lua 事件 + 持久化**——和 Monster.onDeath 一脉相承。

---

## 11. 和怪物的对比（精彩对照）

| 维度 | EntityMonster | EntityGadget |
|---|---|---|
| 类型数 | 7 (MonsterType) | 73 (EntityType 中约 30 个) |
| Content 策略 | 无（同一类怪走同一逻辑）| 11 种 (Chest/Worktop/...) |
| AI | aiId → 客户端跑 | 没 AI，被动等交互 |
| 死亡触发 | 7 件事 | 4 件事 (任务进度由 Lua 联动) |
| 持久化 | DeadSpawnedEntities | DeadSpawnedEntities + cacheGadgetState |
| 战斗属性 | 必有 (HP/攻/防/抗性) | 大多无 (火药桶等少数有) |
| 联机权威 | host | host (大部分) / owner (Vehicle, ClientGadget) |
| 主要交互 | 受伤 (EvtBeingHit) | 按 F (GadgetInteractReq) |
| 数量 | 同时 5-50 个 (战斗) | 同时 100-500 个 (探索) |

→ **Monster 是"动态对抗"的载体；Gadget 是"静态探索"的载体**。两者互补，构成完整场景。

---

## 12. 实战：一只丘丘人的全场景

把前面所有元素串起来——**一只丘丘人到玩家拾取宝箱的全流程**：

```
[T+0] 场景加载
    ↓
[T+1] SceneScript Lua 加载 group_NNN
    ↓ create 实体
[T+2] EntityMonster (丘丘人) + EntityGadget (战利品宝箱) + EntityGadget (背景火堆)
    ↓ 广播 SceneEntityAppearNotify
[T+3] 客户端展示

[T+5] 玩家进入怪物视野
    ↓
[T+5.1] [客户端] MonsterAlertChangeNotify { alert=1, monsterEntityId=... }
    ↓ 服务器记录
[T+5.2] EVENT_MONSTER_BATTLE 触发, Lua 可能 spawn 增援

[T+10] 玩家平砍
    ↓
[T+10.1] [客户端] EvtBeingHitNotify { defenseId=monsterId, damage=4500 }
    ↓
[T+10.2] EntityMonster.damage(4500)
    ↓
[T+10.3] HP 减少, 穿过 50% 阈值
    ↓
[T+10.4] EnergyManager.handleMonsterEnergyDrop → 生成 EntityItem (元素球)
    ↓ 广播
[T+10.5] 客户端看见元素球, 玩家走过去吸收 → 充能

[T+30] 玩家持续打怪, HP=0
    ↓
[T+30.1] EntityMonster.onDeath
    ↓ 7 件事并行触发
[T+30.2.1] Lua EVENT_ANY_MONSTER_DIE
[T+30.2.2] BattlePass.triggerMission(TRIGGER_MONSTER_DIE)
[T+30.2.3] QuestManager.queueEvent(QUEST_CONTENT_MONSTER_DIE)
[T+30.2.4] DungeonPassConditionType.DUNGEON_COND_KILL_MONSTER (如果在副本)
[T+30.2.5] dropSubfield → 生成 EntityItem 战利品
[T+30.2.6] SceneGroupInstance.deadEntities.add(configId) (持久化)

[T+30.3] Lua 收到 EVENT_ANY_MONSTER_DIE
    ↓
[T+30.4] 判断 if (isClearedGroupMonsters) → setGroupVariable("cleared", true)
    ↓ 触发宝箱可交互
[T+30.5] EntityGadget (宝箱).setInteractEnabled(true) → PacketGadgetStateNotify

[T+31] 玩家走到宝箱前按 F
    ↓
[T+31.1] [客户端] GadgetInteractReq { gadgetEntityId, opType=Start }
    ↓
[T+31.2] EntityGadget.onInteract → GadgetChest.onInteract
    ↓ (普通宝箱单步)
[T+31.3] NormalChestInteractHandler.onInteract
    ↓
[T+31.4] earnExp(225) + addItem(摩拉, 1500) + addItem(随机材料 ×3)
    ↓ 生成 EntityItem ×3 飞出来
[T+31.5] 宝箱 setState(ChestOpened) → 持久化
    ↓
[T+31.6] killEntity(EntityGadget 宝箱)

[T+32] 玩家走过去拾取掉出来的物品
    ↓
[T+32.1] [客户端] 自动吸附 → PickupItem
    ↓
[T+32.2] EntityItem 消失 + addItem 入背包
    ↓
[T+32.3] Quest 进度更新 (打 1 个丘丘 / 开 1 个宝箱 / 拾取 N 个材料)

[完成]
```

→ 这就是**一次最普通的"打怪开宝箱"的完整服务端时序**——涉及 12+ 个子系统协作。

---

## 13. 关键收获

1. **Gadget 是场景的"第三大实体"**（继 Avatar/Monster 之后）—— 73 种 EntityType 中 30+ 个属 Gadget 系
2. **两层抽象**：Entity (本性) + Content (策略) —— EntityGadget × 11 种 GadgetContent 组合
3. **EntityClientGadget vs EntityGadget**：客户端创造 vs 服务器创造，前者用于技能特效/临时投射物
4. **EntityItem 是 Gadget 的子类**：掉落物本质是"可拾取的 Gadget"
5. **EntityVehicle 也是 Gadget**：载具有 owner + stamina + members
6. **Chest 三种 Handler**：Normal（直接开）/ Boss（树脂确认两步）/ Blossom（凋零之缘）
7. **GatherObject 两条路径**：onInteract 直接入袋 / dropItems 飞出来捡
8. **Worktop 是"工作台模式"**：选项面板 + handler 回调
9. **Platform 3 种 Route**：ConfigRoute / PointArrayRoute / AbilityRoute
10. **状态持久化**：setState 自动 cacheGadgetState 到 SceneGroupInstance
11. **死亡级联 4 件事**：Lua 事件 + 死亡列表 + 持久化 + 特殊钩子（凋零之缘）
12. **每个版本都加新 Gadget 类型**：从蒙德的 AmberWind 到须弥的 DeshretObelisk
13. **DropTable 两种算法**：select-one（互斥）/ select-various（独立掷骰）

---

## 14. 一句话总结

> **Gadget = 场景里所有"非玩家非怪物"的物体——宝箱/采集物/机关/平台/载具/掉落物/锚点... 73 种 EntityType 中占 30+。两层抽象：Entity (本性) + Content (策略, 11 种)；按 F 触发 onInteract 委托给 Content；状态持久化到 SceneGroupInstance 让宝箱开过不复现。**
> 
> **设计哲学：怪物负责动态对抗，Gadget 负责静态探索/解谜/收集——两类实体互补构成完整开放世界。每加新版本只要加 EntityType + 写一个 GadgetXxx Content 就能扩展。**

---

**前置笔记**：
- notes/14 SceneScript - Lua 引擎与 Gadget spawn 机制
- notes/16 战斗 - Gadget 也走 EvtBeingHit (能打的火药桶)
- notes/19 副本 - Chest 在副本通关时生成
- notes/30 持久化 - SceneGroupInstance.cacheGadgetState
- notes/32 怪物系统 - Monster vs Gadget 的对照（本篇第 11 章）

**关联文件**：
- `EntityGadget.java`(310) - 主实体
- `EntityClientGadget.java`(84) - 客户端 Gadget
- `EntityItem.java`(78) - 掉落物
- `EntityVehicle.java`(60+) - 载具
- `EntityType.java`(114) - 73 类枚举
- `gadget/content/GadgetChest.java`(84) - 宝箱内容
- `gadget/content/GadgetWorktop.java`(76) - 工作台
- `gadget/content/GadgetGatherObject.java`(79) - 采集物
- `gadget/chest/NormalChestInteractHandler.java`(42)
- `gadget/chest/BossChestInteractHandler.java`(66)
- `gadget/platform/ConfigRoute.java` / `PointArrayRoute.java` / `AbilityRoute.java`

**研究的源代码**: 1200+ 行 Gadget 相关代码。
