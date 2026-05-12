# Player 主类与 25+ Manager 横切图

> 第 40 篇：玩家侧的"圣殿"——1676 行 Player.java 持有 25+ Manager / 80+ 字段。我们在 40 篇笔记里说过 `player.questManager.xxx`、`player.battlePassManager.xxx` ... 但 **Player 本身**从未打开。

---

## 0. 为什么这一篇重要

每个 Player 实例都是**玩家在服务器的"全息投影"**：
- 持久化字段（昵称、UID、解锁的角色头像、风之翼列表 ...）
- 25+ 个 Manager（Inventory / Quest / BattlePass / Resin / Cooking / ...）
- 4 个并发输入流（AttackResult / CombatInvoke / AbilityInvoke / ClientAbilityInitFinish）
- 双重身份 IDs（uid / accountId / peerId / nextGuid）

前面笔记里每个 manager **独立挖过**了，但**横切图**还没有。这一篇就是把 Player 这个"全聚合根"展开看一遍。

---

## 1. 类首信息

`Player.java` —— **1676 行**，是 grasscutter 中**最大的类**。

```java
@Entity(value = "players", useDiscriminator = false)
public class Player {
    @Id private int id;                                          // ★ 玩家 UID
    @Indexed(unique = true) private String accountId;             // ★ 账号 ID
    @Setter private transient Account account;                    // 反向引用
    @Getter @Setter private transient GameSession session;        // 网络会话
    // ... 80+ 字段
}
```

### 1.1 双数据库主键

- **Player.id (int)** = UID（如 100015）→ MongoDB `_id`
- **Player.accountId (String)** = 账号 ID（如 "1"）→ MongoDB `accountId` 索引

→ 一个账号一个 Player（unique）—— 但不同账号的 Player UID 不同。

### 1.2 4 类字段（按持久化策略）

```java
@Id                            // MongoDB 主键
@Indexed                       // 建索引
(普通)                          // 直接持久化（含 manager）
transient                      // 不持久化（网络/反向引用/缓存）
```

→ Player **80+ 字段**中约一半 transient（不持久化）。

---

## 2. 25+ Manager 横切图

`Player.java:170-208` 一段集中声明所有 Manager。按**功能分类**汇总：

### 2.1 经济类（5 个）

| Manager | 文件 | 功能 | 持久化？ |
|---|---|---|---|
| `Inventory` | inventory/Inventory.java | 物品/装备/虚拟币 (notes/38) | transient (单独 collection) |
| `BargainManager` | managers/bargain/ | 商人讨价还价 | transient |
| `ForgingManager` | managers/forging/ | 锻造队列 | transient |
| `CookingManager` | managers/cooking/ | 烹饪 | transient |
| `CookingCompoundManager` | managers/cooking/ | 食材合成 | transient |

### 2.2 任务/活动类（5 个）

| Manager | 功能 |
|---|---|
| `QuestManager` | 任务系统 (notes/02-09) |
| `DailyTaskManager` | 每日委托 |
| `ActivityManager` | 限时活动 (notes/20) |
| `BattlePassManager` | 战令 (notes/22) |
| `GivingManager` | 物品提交（"给 NPC 5 个白萝卜"）|

### 2.3 战斗/属性类（5 个）

| Manager | 功能 |
|---|---|
| `AbilityManager` | 能力系统 (notes/37) |
| `EnergyManager` | 元素能量经济 |
| `StaminaManager` | 体力 |
| `ResinManager` | 树脂 |
| `PlayerBuffManager` | 玩家 buff |
| `TeamManager` | 队伍 (notes/34) - **持久化** |

### 2.4 角色/装备类（2 个，存在独立 collection）

| Manager | 功能 |
|---|---|
| `AvatarStorage` | 持有所有角色 (notes/24) |
| (Inventory 持有装备) | |

### 2.5 副本/塔类（3 个）

| Manager | 功能 |
|---|---|
| `TowerManager` | 深境螺旋 |
| `DungeonEntryManager` | 副本入口 |
| `BlossomManager` | 凋零之缘（每日地脉花）|

### 2.6 社交类（4 个）

| Manager | 功能 |
|---|---|
| `FriendsList` | 好友列表 |
| `MailHandler` | 邮件 |
| `CoopHandler` | 联机协作 |
| `MapMarksManager` | 地图标记 |

### 2.7 家园/收藏类（3 个）

| Manager | 功能 |
|---|---|
| `FurnitureManager` | 家具 |
| `PlayerCodex` | 图鉴 (notes/17) |
| `home: GameHome` | 家园（不是 Manager 但同级）|

### 2.8 进度/Misc 类（4 个）

| Manager | 功能 |
|---|---|
| `PlayerProgressManager` | 进度追踪（开放状态、物品历史）|
| `SotSManager` | Statue of the Seven (七天神像)|
| `DeforestationManager` | 砍树系统 |
| `MessageHandler` | 客户端消息（聊天/系统提示）|

### 2.9 总计

**25+ Manager** = 几乎每个游戏子系统都有专属 Manager。

加上 `coopRequests`, `attackResults` 等专用队列 → Player 是**真正的"全聚合根"**。

---

## 3. Player 构造器：60 行初始化

`Player.java:248-328` —— **默认构造器**做的事：

```java
public Player() {
    // 1. 创建 8 个核心 Manager
    this.inventory = new Inventory(this);
    this.avatars = new AvatarStorage(this);
    this.friendsList = new FriendsList(this);
    this.mailHandler = new MailHandler(this);
    this.towerManager = new TowerManager(this);
    this.abilityManager = new AbilityManager(this);
    this.deforestationManager = new DeforestationManager(this);
    this.questManager = new QuestManager(this);
    this.dailyTaskManager = new DailyTaskManager(this);
    this.buffManager = new PlayerBuffManager(this);
    
    // 2. 初始化位置 (默认 mondstadt sceneId=3)
    this.position = new Position(GameConstants.START_POSITION);
    this.rotation = new Position(0, 307, 0);
    this.sceneId = 3;
    this.regionId = 1;
    
    // 3. 初始化 properties (57 个 PlayerProperty 全部 = 0)
    this.properties = new HashMap<>();
    for (PlayerProperty prop : PlayerProperty.values()) {
        if (prop.getId() < 10000) continue;
        this.properties.put(prop.getId(), 0);
    }
    
    // 4. 初始化 12 个 Set/Map (角色头像/风之翼/服装/任务变量等)
    this.gachaInfo = new PlayerGachaInfo();
    this.nameCardList = new HashSet<>();
    this.flyCloakList = new HashSet<>();
    // ... 12 个集合
    
    // 5. 初始化 4 个并发输入流
    this.attackResults = new LinkedBlockingQueue<>();
    this.coopRequests = new Int2ObjectOpenHashMap<>();
    this.combatInvokeHandler = new InvokeHandler(PacketCombatInvocationsNotify.class);
    this.abilityInvokeHandler = new InvokeHandler(PacketAbilityInvocationsNotify.class);
    this.clientAbilityInitFinishHandler = new InvokeHandler(PacketClientAbilityInitFinishNotify.class);
    
    // 6. 再创建 15 个 Manager
    this.codex = new PlayerCodex(this);
    this.progressManager = new PlayerProgressManager(this);
    // ...
    this.bargainManager = new BargainManager(this);
}
```

### 3.1 为什么默认构造器和带参构造器都 new Manager

```java
public Player() { /* 默认 */ }

public Player(GameSession session) {
    this();   // ★ 调默认
    // ... 再 new 一遍部分 Manager (重复!)
    this.mapMarksManager = new MapMarksManager(this);
    this.staminaManager = new StaminaManager(this);
    // ...
}
```

→ **代码异味**：构造器逻辑重复。但作用是确保**两种创建路径**（DB 反序列化 / 新玩家创建）都有完整 Manager。

### 3.2 Morphia 反序列化的特殊性

`@Deprecated` 标在默认构造器上：
```java
@Deprecated
@SuppressWarnings({"rawtypes", "unchecked"}) // Morphia only!
public Player() { ... }
```

→ **只给 Morphia 用** —— 业务代码不应直接 new Player()。Morphia 反序列化时调用，然后 `loadFromDatabase` 恢复其余状态。

---

## 4. PlayerProperty 完整体系

`PlayerProperty.java` —— **57 个枚举**（ID 范围 10000+），和 FightProperty (130+) 平行。

### 4.1 关键字段（部分）

```java
PROP_MAX_SPRING_VOLUME            (10002, 0, 8_500_000)   // 七天神像存量上限
PROP_CUR_SPRING_VOLUME            (10003)                 // 七天神像当前
PROP_IS_SPRING_AUTO_USE           (10004, 0, 1)           // 接近神像自动回血
PROP_IS_FLYABLE                   (10006, 0, 1)           // 能否飞行
PROP_IS_WEATHER_LOCKED            (10007, 0, 1)
PROP_IS_GAME_TIME_LOCKED          (10008, 0, 1)
PROP_IS_TRANSFERABLE              (10009, 0, 1)
PROP_MAX_STAMINA                  (10010, 0, 24_000)      // ★ 体力上限
PROP_CUR_PERSIST_STAMINA          (10011)                 // ★ 当前体力
PROP_CUR_TEMPORARY_STAMINA        (10012)                 // 临时体力（食物）
PROP_PLAYER_LEVEL                 (10013, 1, 60)          // ★ 冒险等阶
PROP_PLAYER_EXP                   (10014)                 // 冒险阅历
PROP_PLAYER_HCOIN                 (10015)                 // ★ 原石
PROP_PLAYER_SCOIN                 (10016, 0)              // ★ 摩拉 [0,+inf)
PROP_PLAYER_MP_SETTING_TYPE       (10017, 0, 2)           // 联机权限 [0=no 1=direct 2=approval]
PROP_IS_MP_MODE_AVAILABLE         (10018, 0, 1)
PROP_PLAYER_WORLD_LEVEL           (10019, 0, 8)           // ★ 世界等级
PROP_PLAYER_RESIN                 (10020, 0, 2000)        // ★ 体力（树脂）
PROP_PLAYER_MCOIN                 (10025)                 // ★ 创世结晶
PROP_PLAYER_LEGENDARY_KEY         (10027, 0)              // 传说任务钥匙
PROP_PLAYER_FORGE_POINT           (10029, 0, 300_000)     // 锻造点
PROP_CUR_CLIMATE_METER            (10035)                 // 当前气候计数
PROP_CUR_CLIMATE_TYPE             (10036)
// ...
```

### 4.2 PlayerProperty vs FightProperty

| 维度 | PlayerProperty | FightProperty |
|---|---|---|
| 数量 | 57 | 130+ |
| 范围 | 10000-10100 | 1-3046 |
| 单位 | 玩家级"账号属性" | 战斗属性（角色 entity 用）|
| 持久化 | Map\<Integer, Integer\> | Map\<Integer, Float\> |
| 用途 | 体力/原石/摩拉/AR/WL/解锁状态 | HP/ATK/暴击/抗性/元素能量 |
| 修改 | setProperty + 包通知 | recalcStats / addEnergy |
| 客户端通知 | PlayerPropChangeReasonNotify | EntityFightPropUpdateNotify |
| 存哪 | Player.properties | Avatar.fightProperties |

→ **PlayerProperty 是账号属性**（不属于具体角色）；**FightProperty 属于战斗实体**。

### 4.3 范围校验

```java
PROP_PLAYER_LEVEL  (10013, 1, 60)       // 范围 [1, 60]
PROP_PLAYER_RESIN  (10020, 0, 2000)     // 范围 [0, 2000]
```

→ 设值时**自动 clamp**：超过上限/低于下限会被截断。
- 树脂买太多溢出 2000 → 被截到 2000
- AR 不能高于 60

### 4.4 4 类常用 prop

```
[等阶系列] PROP_PLAYER_LEVEL / EXP / WORLD_LEVEL
[货币系列] PROP_PLAYER_HCOIN (原石) / SCOIN (摩拉) / MCOIN (创世结晶) / RESIN (树脂)
[设置系列] PROP_IS_FLYABLE / PROP_IS_WEATHER_LOCKED / PROP_IS_GAME_TIME_LOCKED
[体力系列] PROP_MAX_STAMINA / PROP_CUR_PERSIST_STAMINA / PROP_CUR_TEMPORARY_STAMINA
```

→ 玩家面板看到的几乎所有数字都是 PlayerProperty。

---

## 5. 身份 ID 系统（5 个层次）

Player 有**5 种不同的 ID**：

```java
private int id;              // ★ UID (100015) - 全局唯一玩家标识
private String accountId;    // 账号 ID ("1") - 关联 Account 表
@Transient private int peerId;  // 联机内编号 (1-4)
@Transient private long nextGuid = 0;  // 物品/角色 guid 计数器
```

加上 entity 的：
- `EntityAvatar.id` —— 场景内 entity_id（带类型前缀，参见 notes/35）

### 5.1 nextGameGuid 算法

```java
public long getNextGameGuid() {
    long nextId = ++this.nextGuid;
    return ((long) this.getUid() << 32) + nextId;
}
```

→ **uid 在高 32 位，counter 在低 32 位**：
- UID 100015 (0x186A7) | counter 1 → guid = 0x186A700000001
- 解决"不同玩家 guid 冲突"问题

**用途**：物品 guid、角色 guid、邮件 guid 都从这里取。

### 5.2 5 种 ID 的关系

```
[Account] id="1"
   ↓ 1:1
[Player] uid=100015, accountId="1"
   ↓ 1:n
[Avatar] guid=0x186A700000005    ← 用 player.nextGameGuid
[GameItem] guid=0x186A700000234
   ↓
[EntityAvatar] entityId=0x01000023  ← 场景内 (类型化, notes/35)
```

→ **5 层 ID 各司其职**，但都从 UID 派生。

---

## 6. 4 个并发输入流

```java
@Transient @Getter private final Queue<AttackResult> attackResults;
@Transient @Getter private final InvokeHandler<CombatInvokeEntry> combatInvokeHandler;
@Transient @Getter private final InvokeHandler<AbilityInvokeEntry> abilityInvokeHandler;
@Transient @Getter private final InvokeHandler<AbilityInvokeEntry> clientAbilityInitFinishHandler;
```

### 6.1 4 流各自的作用

| 流 | 来源 | 内容 | 处理 |
|---|---|---|---|
| `attackResults` | `EvtBeingHitNotify` | 客户端发的伤害结果 | 排队 → tick 处理 |
| `combatInvokeHandler` | `CombatInvocationsNotify` | 战斗事件批 | tick 转发其他玩家 |
| `abilityInvokeHandler` | `AbilityInvocationsNotify` | 能力事件批 | tick 转发其他玩家 |
| `clientAbilityInitFinishHandler` | `ClientAbilityInitFinish` | 能力初始化完成 | 队伍同步通知 |

### 6.2 InvokeHandler 模式

```java
@Getter private transient final InvokeHandler<CombatInvokeEntry> combatInvokeHandler 
    = new InvokeHandler(PacketCombatInvocationsNotify.class);
```

→ **批量包**：客户端把多个事件打包一起发，服务器**累积一段后批量转发给其他玩家**。

**为什么需要批量**：联机时玩家 A 攻击产生 10 个事件，如果每个都立即广播给玩家 B/C/D → 网络包爆炸。批量后**100ms 内打包发一次** = 大幅减包数。

### 6.3 LinkedBlockingQueue 选型

```java
this.attackResults = new LinkedBlockingQueue<>();
```

→ **生产者多 (客户端发) / 消费者单 (tick 处理)** 的并发队列首选。

---

## 7. Manager 生命周期

### 7.1 创建 → 加载 → 在线 → 登出

```
[阶段 1: 创建] Player(GameSession)
   ↓
   25+ Manager 全部 new (空状态)
   
[阶段 2: 加载] Player.loadFromDatabase()
   ↓
   - avatars.loadFromDatabase
   - inventory.loadFromDatabase
   - friendsList.loadFromDatabase
   - mailHandler.loadFromDatabase
   - questManager.loadFromDatabase
   - battlePassManager (load via DatabaseHelper)
   - ...
   
[阶段 3: 上线] Player.onLogin()
   ↓
   - activityManager = new ActivityManager(this)   ← 部分 manager 延迟创建
   - dailyReset 检测
   - 发 12+ 个 Notify 包给客户端 (背包/角色/任务/战令 等)
   - resinManager.onPlayerLogin
   - dailyTaskManager.onPlayerLogin
   - towerManager.onLogin
   - home.onOwnerLogin
   - activityManager.onLogin
   
[阶段 4: 在线] tick 由 World/Scene 驱动
   ↓
   - 输入流持续接收 packet
   - 各 Manager 处理事件
   - 周期性 save (邮件读取、任务完成、装备等)
   
[阶段 5: 登出] Player.onLogout()
   ↓
   - clearChatHistory
   - staminaManager.stopSustainedStaminaHandler
   - exitDungeon
   - world.removePlayer
   - profile.setPlayer(null)
   - save() + teamManager.saveAvatars + friendsList.save
   - PlayerQuitEvent
```

→ Manager 不是同时创建/加载/在线——**生命周期错开**。

---

## 8. 持久化策略全图

### 8.1 Player 文档（players collection）

```java
@Entity(value = "players", useDiscriminator = false)
public class Player {
    @Id private int id;                  // ★ 持久化
    @Indexed String accountId;            // ★ 持久化
    String nickname, signature;            // ★ 持久化
    int headImage, nameCardId;             // ★
    Position position, rotation;           // ★
    Map<Integer, Integer> properties;      // ★ 57 个 PlayerProperty
    Set<Integer> nameCardList, flyCloakList, costumeList;  // ★
    
    @Getter private TeamManager teamManager;         // ★ embedded
    @Getter private PlayerProfile playerProfile;     // ★ embedded
    @Getter private CoopHandler coopHandler;         // ★ embedded
    @Getter private PlayerGachaInfo gachaInfo;       // ★ embedded
    // ...
    
    @Transient World world;          // ✗ 不存（运行时重建）
    @Transient GameSession session;  // ✗ 不存
    @Transient Inventory inventory;  // ✗ 不存（独立 collection）
    @Transient AvatarStorage avatars;// ✗ 不存（独立 collection）
    @Transient AbilityManager abilityManager;  // ✗ 不存
    @Transient QuestManager questManager;       // ✗ 不存
    @Transient DailyTaskManager dailyTaskManager;
    @Transient int peerId;
    @Transient long nextGuid;
}
```

### 8.2 三类持久化模式

| 模式 | 在哪 | 例子 |
|---|---|---|
| **Player 文档 embedded** | Player document 内 | properties, nameCardList, flyCloakList, teamManager, gachaInfo |
| **独立 collection** | avatars/items/quests/mail/.. | Avatar, GameItem, GameMainQuest, Mail |
| **transient 完全不存** | 内存 only | session, world, peerId, abilityManager |

→ 见 notes/30 持久化层全图。

### 8.3 为什么 abilityManager / questManager 不持久化

**它们没自己的状态** —— 全部状态在：
- abilityManager → entity.instancedModifiers / entity.instancedAbilities
- questManager → loadFromDatabase 时从 `quests` collection 重建

→ Manager 是**逻辑容器 + 行为方法**，不是**状态存储**。

---

## 9. 跨 Manager 协作的常见模式

### 9.1 经典调用链：完成任务

```
1. Quest.finish()
2. → reward.rewardItemList
3. → for item: inventory.addItem(item, ActionReason.QuestReward)
4.   → triggerAddItemEvents:
5.     → battlePassManager.triggerMission(TRIGGER_OBTAIN_MATERIAL_NUM)
6.     → questManager.queueEvent(QUEST_CONTENT_OBTAIN_ITEM × 2)
7.     → questManager.queueEvent(QUEST_COND_PACK_HAVE_ITEM)
8. → codex.checkAddedItem(item)  ← 图鉴检查
9. → progressManager.addItemObtainedHistory(...)
10. → 客户端 packet: StoreItemChangeNotify + ItemAddHintNotify
```

→ **完成 1 个任务 → 10 个 Manager 协作**。

### 9.2 Manager 间通信靠 Player

```java
// Manager 之间不直接互引用
class BattlePassManager {
    public void triggerMission(...) {
        // 通过 player 反向找其他 manager
        Player p = this.getPlayer();
        p.getQuestManager().queueEvent(...);
    }
}
```

→ Player 是**中介**——Manager 不直接调对方，全经过 Player.

**好处**：Manager 单元测试时只需 mock Player；解耦。

### 9.3 BasePlayerManager / BasePlayerDataManager 父类

```java
public class BasePlayerManager {
    @Getter protected Player player;
    public BasePlayerManager(Player player) {
        this.player = player;
    }
}

public class BasePlayerDataManager extends BasePlayerManager {
    // 可以保存到 Player 文档中
}
```

→ 25+ Manager 共享 player 引用 + setPlayer 注入模式。

---

## 10. 玩家上线后客户端收到的 12+ 个 Notify

`Player.onLogin()` 第 1407-1442 行（参见 notes/30 引用）：
```java
session.send(new PacketMainCoopUpdateNotify(...));
session.send(new PacketPlayerDataNotify(this));            // 玩家基础数据
session.send(new PacketLevelTagDataNotify(this));
session.send(new PacketStoreWeightLimitNotify());          // 背包上限
session.send(new PacketPlayerStoreNotify(this));            // 背包内容
session.send(new PacketAvatarDataNotify(this));             // 角色列表
session.send(new PacketAvatarWeaponSkinDataNotify(this));   // 武器皮肤
session.send(new PacketFinishedParentQuestNotify(this));    // 已完成任务
session.send(new PacketBattlePassAllDataNotify(this));      // 战令
session.send(new PacketQuestListNotify(this));              // 任务列表
session.send(new PacketDailyTaskUnlockedCitiesNotify(this));
session.send(new PacketCodexDataFullNotify(this));          // 图鉴
session.send(new PacketAllWidgetDataNotify(this));
session.send(new PacketCoopDataNotify(this));
session.send(new PacketCombineDataNotify(...));             // 合成菜单
session.send(new PacketTowerBriefDataNotify(this));          // 深境螺旋
session.send(new PacketPlayerEnterSceneNotify(this));        // 进入场景
session.send(new PacketPlayerLevelRewardUpdateNotify(...)); // 等级奖励
```

→ **登录瞬间下发 12+ 个 Notify** —— 这就是为什么"加载界面进度条"的时间花在等服务器初始数据。

---

## 11. Player vs EntityAvatar：账号 vs 实体

回顾 notes/34 提到的 "双层模型"：

| 维度 | Player | EntityAvatar |
|---|---|---|
| 数量 | 1 个 / 玩家 | 1-4 个 / 玩家 (active team) |
| 持久化 | ✓ MongoDB players collection | ✗ 仅内存 |
| 位置 | ✓ player.position (单个) | 共享 player.position |
| Manager | ★ 25+ 个 Manager | ✗ 5 字段委托 |
| 字段数 | 80+ | 5 |
| 生命周期 | 登录到登出 | 场景级（切场景重建）|
| 代码量 | 1676 行 | 318 行 |

→ **Player 是"账号"，EntityAvatar 是"具象"**。

类比：
- Player = Steam 账号
- Avatar = 你买的某个游戏
- EntityAvatar = 当前正在跑的游戏进程

---

## 12. 关键收获

1. **Player.java 1676 行** —— grasscutter 最大单文件
2. **25+ Manager 横切图**：经济(5)/任务活动(5)/战斗(5)/角色装备(2)/副本(3)/社交(4)/家园(3)/进度(4)
3. **80+ 字段** 按持久化分 4 类：@Id / @Indexed / 普通 / transient
4. **57 PlayerProperty** (10000+) vs 130+ FightProperty(1-3046)——前者账号属性，后者战斗实体属性
5. **5 种 ID 系统**：uid / accountId / peerId / nextGuid / entityId（各司其职）
6. **nextGameGuid 算法**：uid 高 32 位 + counter 低 32 位 = 全局唯一 guid
7. **4 并发输入流**：attackResults + 3 个 InvokeHandler — 客户端批量发, 服务器累积转发
8. **Manager 间通过 Player 中介通信** — 不直接互引用，解耦
9. **Manager 生命周期 5 阶段**：构造 → loadFromDatabase → onLogin → tick → onLogout
10. **持久化 3 模式**：embedded / 独立 collection / transient
11. **abilityManager / questManager 不持久**：状态散布在 entity / quest collection
12. **登录发 12+ 个 Notify**：从 PlayerDataNotify 到 PlayerEnterSceneNotify
13. **构造器写两遍**：默认 + 带参，确保 DB 反序列化和新玩家创建都完整
14. **PlayerProperty 范围 clamp**：超过 max 截断（树脂 2000 / AR 60）
15. **Player 是"账号"，EntityAvatar 是"具象"** — 双层模型

---

## 13. 一句话总结

> **Player.java 1676 行是玩家侧的"圣殿" —— 25+ Manager 横切游戏所有子系统、80+ 字段含 57 个 PlayerProperty (账号级) 与 transient 输入流、5 种 ID 层次 (uid/accountId/peerId/nextGuid/entityId)、4 并发批量队列、Manager 间靠 Player 中介解耦、登录发 12+ Notify 一次性同步全状态。**
> 
> **设计哲学：全聚合根 + 25+ Manager 各管一摊 + transient 区分持久化与运行时 + ID 分层避免冲突 + 中介模式解耦 Manager 间依赖——是经典的"玩家中心化"服务器架构。**

---

**前置笔记**：
- notes/30 持久化层 - Player 是聚合根, 30+ 嵌入对象
- notes/34 EntityAvatar - Player 与 entity 的双层模型
- notes/35 Scene/World 容器 - Player 与 World 关系
- notes/36 战斗数学 - PlayerProperty vs FightProperty
- notes/37 Ability - AbilityManager 横切
- notes/38 Inventory - addItem 触发跨 manager 链
- notes/27 架构模式 - Manager 4 线程异步池模式

**关联文件**：
- `Player.java`(1676) - 主类
- `PlayerProperty.java` - 57 个枚举
- `BasePlayerManager.java` - Manager 基类
- `BasePlayerDataManager.java` - 持久化 Manager 基类
- 各子系统 Manager 文件 × 25+

**研究的源代码**: 1700+ 行 Player 主类 + 50+ Manager 类零散引用。
