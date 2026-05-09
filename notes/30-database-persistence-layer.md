# 数据库持久化层深度剖析

> 第 30 篇：**MongoDB 持久层**——回答"哪些数据落盘？哪些只在内存？崩溃后能恢复什么？"

---

## 0. 为什么这一篇重要

前 29 篇笔记讲的全是**内存对象**：
- Player 在内存里有 100+ 字段
- World 维护 Scene 集合
- AbilityManager 跑在 4 个线程池里
- QuestManager 持有 2360 个任务的状态
- ……

但**服务器重启后**？这些内存对象怎么恢复？哪些**必须持久**、哪些可以**丢弃**？

这一篇专挖**持久化边界**：
1. 14 个 MongoDB collection 的全图
2. **Embedded 嵌入 vs Reference 引用**的取舍
3. save/load 的时机与触发链
4. 没有事务的 MongoDB 怎么保证一致性
5. 内存 vs 磁盘的边界（什么时候同步、什么时候不同步）

---

## 1. 技术栈：MongoDB + Morphia + 反射

### 1.1 选型

```
emu.grasscutter.database/
├── DatabaseManager.java   (123 行) — 连接管理 + Datastore 创建
├── DatabaseHelper.java    (388 行) — 50+ DAO 方法
└── DatabaseCounter.java   (23 行)  — 自增 ID 生成器
```

| 组件 | 选型 | 用途 |
|---|---|---|
| 数据库 | MongoDB（4.x+）| NoSQL 文档存储 |
| ORM | Morphia（dev.morphia）| Java 对象 ↔ BSON 文档映射 |
| 注解 | `@Entity` / `@Id` / `@Indexed` / `@Transient` | 声明持久化策略 |
| 注册 | **反射扫描**（`Grasscutter.reflector`）| 自动发现 entity 类 |
| 编解码 | 自定义 `CodecProvider` | 处理特殊类型（如 MapMarkPoint）|

### 1.2 双数据库架构

`DatabaseManager.java:24-43`：
```java
private static Datastore gameDatastore;     // 游戏数据
private static Datastore dispatchDatastore; // 账户数据

public static Datastore getAccountDatastore() {
    if (SERVER.runMode == ServerRunMode.GAME_ONLY) {
        return dispatchDatastore;   // ← 独立 dispatch 服务器时账户单独库
    } else {
        return gameDatastore;       // ← 单服模式时全在一个库
    }
}
```

**部署模式**：
```
[模式 A: HYBRID 单机]    [模式 B: GAME_ONLY 独立 dispatch]
                         
   gameDatastore            gameDatastore        dispatchDatastore
   ├── accounts             ├── players          ├── accounts (only)
   ├── players              ├── avatars          └── counters
   ├── avatars              ├── ...              
   └── ...
```

**为什么要分**？
- **横向扩展**：dispatch 服务器可独立部署，多个游戏服务器共享账户
- **隔离故障**：游戏库挂了，登录鉴权还能跑（虽然没游戏）
- **权限隔离**：dispatch 用低权限访问 accounts；game 用高权限读写一切

### 1.3 自动注册机制

`DatabaseManager.initialize()`：
```java
Class<?>[] entities = Grasscutter.reflector
    .getTypesAnnotatedWith(Entity.class)   // ← 反射扫描所有 @Entity 类
    .stream()
    .filter(cls -> {
        Entity e = cls.getAnnotation(Entity.class);
        return e != null && !e.value().equals(Mapper.IGNORED_FIELDNAME);
    })
    .toArray(Class<?>[]::new);

gameDatastore.getMapper().map(entities);
ensureIndexes(gameDatastore);   // ← 自动建索引（@Indexed 标注的字段）
```

**这又是"反射 + 注解"模式**——和 Quest/Activity/PacketHandler 一致：
- 加新 entity 不需要改代码
- 自动建索引
- 自动支持序列化

→ **第 9 次"注解+反射+架构模式"出现**（继 Network 协议层后又一次）

---

## 2. 全部 14 个顶层 Collection

`@Entity(value = "...")` 显式声明的**主表**：

| Collection 名 | Entity 类 | 主键 | 主索引 | 内容 |
|---|---|---|---|---|
| `accounts` | `Account` | `String id` | `username` | 账号表（dispatch DB）|
| `players` | `Player` | `int id`（=UID）| `accountId` (unique) | 主玩家文档 |
| `avatars` | `Avatar` | `ObjectId` | `ownerId` | 角色（每个独立文档）|
| `items` | `GameItem` | `ObjectId` | `ownerId` | 背包物品/装备 |
| `quests` | `GameMainQuest` | `ObjectId` | `ownerUid` | 任务进度 |
| `mail` | `Mail` | `ObjectId` | `ownerUid` | 邮件 |
| `friendships` | `Friendship` | `ObjectId` | `ownerId` / `friendId` | 好友关系 |
| `gachas` | `GachaRecord` | `ObjectId` | `ownerId` + `gachaType` | 抽卡历史 |
| `homes` | `GameHome` | - | `ownerUid` | 家园数据 |
| `battlepass` | `BattlePassManager` | - | `ownerUid` | 战令进度 |
| `activities` | `PlayerActivityData` | - | `uid` + `activityId` | 活动玩家数据 |
| `music_game_beatmaps` | `MusicGameBeatmap` | - | `musicShareId` | 音游谱面（UGC）|
| `group_instances` | `SceneGroupInstance` | `ObjectId` | `ownerUid` + `groupId` | 玩家修改的场景组实例 |
| `counters` | `DatabaseCounter` | `String _id`（class名）| - | 自增 ID 生成器 |

**注意**：还有 50+ 个 `@Entity` 标注的类是**嵌入式**的（无 `value` 字段）——见 §3。

---

## 3. Embedded vs Top-level：聚合设计

### 3.1 何时 embedded，何时 top-level

**MongoDB 的核心设计哲学**：**先聚合，后引用**。
- ✅ Embedded（嵌入）：父子强关联，子无独立含义
- ✅ Top-level + ownerId（弱引用）：子有独立含义，会被独立查询/分页

### 3.2 Player 文档的 embedded 字段

`Player` 是**最大的聚合根**——一次查 Player，载入超过 30 个嵌入对象：

```java
@Entity(value = "players", useDiscriminator = false)
public class Player {
    @Id private int id;                          // UID
    @Indexed(unique=true) private String accountId;
    
    // ===== 全部 embedded（无独立 collection）=====
    private TeamManager teamManager;             // 队伍配置
    private TowerManager towerManager;           // 深境螺旋
    private DailyTaskManager dailyTaskManager;   // 每日委托
    private ResinManager resinManager;           // 体力
    private CookingManager cookingManager;       // 料理
    private ForgingManager forgingManager;       // 锻造
    private FurnitureManager furnitureManager;   // 家具
    private CoopHandler coopHandler;             // 联机
    private PlayerCodex codex;                   // 图鉴
    private PlayerProfile profile;               // 个人主页
    private BlossomManager blossomManager;       // 雾花/凝血结晶
    private ExpeditionInfo expeditionInfo;       // 派遣
    private MapMarkManager mapMarkManager;       // 地图标记
    // ... 30+ 个嵌入对象
    
    @Transient private World world;              // ← 不持久化! 运行时创建
    @Transient private Account account;          // 引用, 不内嵌
    @Transient private GameSession session;      // 网络层, 不入库
}
```

**取舍**：
- ✅ 一次查询拿到所有玩家数据（避免 N+1 query）
- ✅ 写时一致（save player 自动连带 30 个子对象）
- ❌ 单文档膨胀（活跃玩家 Player 文档可达 1-5 MB）
- ❌ 部分更新困难（Morphia 通常 full-document save）

### 3.3 为什么 Avatar/Item 不嵌入

**理论上** Avatar 也可以嵌入 Player（玩家所有角色一次拿）。但选择独立 collection：

```java
@Entity(value = "avatars", useDiscriminator = false)
public class Avatar {
    @Id private ObjectId id;
    @Indexed private int ownerId;       // ← 玩家 UID
    private int avatarId;
    private int level;
    private Map<String, Float> fightProperty;
    // ... 大量字段
}
```

**为什么独立**：
1. **数量大**：满级账号 70+ 角色，单文档太膨胀
2. **独立查询**：好友看你公开角色 → 不需要载入整个 Player
3. **并发更新**：升级角色 vs 改昵称 → 不互相阻塞
4. **页面化**：背包翻页时部分加载

**Item 同理**——单玩家可有 5000+ 物品，肯定独立。

### 3.4 数字对比

| 容器 | Embedded | Top-level + ownerId |
|---|---|---|
| Player | 30+ Manager | - |
| Mail | MailContent / MailItem (内部) | 主表 mail |
| BattlePass | Mission / Reward (内部) | 主表 battlepass |
| Friendship | PlayerProfile snapshot | 主表 friendships |
| GameHome | HomeBlockItem / HomeNPCItem (内部) | 主表 homes |

**规律**：**带"List 集合"的子对象**通常 embedded（同一文档读写一次）；**独立生命周期**的子对象（角色/物品/邮件/任务）独立 collection。

---

## 4. Counter Collection：MongoDB 的"SEQUENCE"

### 4.1 问题

MongoDB 没有 SQL 那种 `AUTO_INCREMENT`。但 Player 需要数字 UID（不是 ObjectId）—— 客户端用 4 字节 int 标识玩家。

### 4.2 解法

`DatabaseManager.getNextId()`：
```java
public static synchronized int getNextId(Class<?> c) {
    DatabaseCounter counter = getGameDatastore().find(DatabaseCounter.class)
        .filter(Filters.eq("_id", c.getSimpleName()))   // ← 用类名作 key
        .first();
    if (counter == null) {
        counter = new DatabaseCounter(c.getSimpleName());
    }
    try {
        return counter.getNextId();   // ← 自增
    } finally {
        getGameDatastore().save(counter);   // ← 立即落盘
    }
}
```

`counters` collection 长这样：
```
{ "_id": "Player",   "next": 100015 }
{ "_id": "Account",  "next": 50001 }
{ "_id": "GameItem", "next": 999 }
```

### 4.3 关键观察

```java
public static synchronized int getNextId(Class<?> c)
//          ^^^^^^^^^^^^
```

**JVM 级 synchronized**！这意味着：
- ✓ 单 JVM 内并发安全
- ✗ **多服务器实例分库时不保证唯一**

**生产环境改进**（Grasscutter 没做）：
- 用 MongoDB 的 `findAndModify` + `$inc` 原子操作
- 或用 Redis 的 `INCR`（现成的分布式 counter）

### 4.4 reservedUid 机制

`HandlerGetPlayerTokenReq.java:88`：
```java
reservedUid = account.getReservedPlayerUid();
```

**用途**：管理员可以**保留特定 UID** 给某账号（如运营号、测试号要用 100100100 这种）。
```java
if (reservedId > 0 && !checkIfPlayerExists(reservedId)) {
    id = reservedId;     // 用预留的
} else {
    id = DatabaseManager.getNextId(...);  // 否则自增
}
```

---

## 5. Save / Load 的时机链

### 5.1 Player.loadFromDatabase()

`Player.java:1348-1374`，登录时触发：
```java
public void loadFromDatabase() {
    // 兜底初始化（防止旧数据缺字段）
    if (this.getTeamManager() == null) this.teamManager = new TeamManager(this);
    if (this.blossomManager == null) this.blossomManager = new BlossomManager(this);
    if (this.getCodex() == null) this.codex = new PlayerCodex(this);
    if (this.getProfile().getUid() == 0) this.getProfile().syncWithCharacter(this);
    
    // 从各自 collection 加载
    this.getAvatars().loadFromDatabase();        // ← 查 avatars
    this.getInventory().loadFromDatabase();      // ← 查 items
    this.getFriendsList().loadFromDatabase();    // ← 查 friendships
    this.getMailHandler().loadFromDatabase();    // ← 查 mail
    this.getQuestManager().loadFromDatabase();   // ← 查 quests
    
    this.loadBattlePassManager();                // ← 查 battlepass
    this.getDailyTaskManager().setPlayer(this);  // 仅注入 player ref
    this.getAvatars().postLoad();                // 后处理（圣遗物副词条等）
}
```

**调用链**：
```
KCP 收到 GetPlayerTokenReq
    ↓
HandlerGetPlayerTokenReq.handle()
    ├── DatabaseHelper.getPlayerByAccount() — 查 players (1 次)
    │       ↓ 拿到 Player 对象（含 30+ embedded manager）
    └── player.loadFromDatabase()
            ├── 查 avatars (1 次, ownerId 索引)
            ├── 查 items (1 次)
            ├── 查 friendships (2 次, 双向)
            ├── 查 mail (1 次)
            ├── 查 quests (1 次)
            └── 查 battlepass (1 次)
```

**总开销**：登录瞬间 **7-8 次 MongoDB 查询**——所有都走 `ownerId` 索引，毫秒级。

### 5.2 Player.save()

`Player.save()`：
```java
public void save() {
    DatabaseHelper.savePlayer(this);   // ← 一行
}
```

→ Morphia 自动序列化整个 Player 文档（含 30+ embedded）一次写入。

### 5.3 Save 触发时机

`Player.java:1477-1510` 的 `onLogout()` 是主要 save 点：
```java
public void onLogout() {
    // ... 清理状态 ...
    this.save();                           // ← 保存 Player 主文档
    this.getTeamManager().saveAvatars();   // ← 保存所有角色
    this.getFriendsList().save();          // ← 保存好友列表
}
```

**还有这些散点 save**：
| 触发条件 | 保存对象 | 代码位置 |
|---|---|---|
| 完成一个任务 | GameMainQuest | `GameMainQuest.save()` |
| 收到/读取邮件 | Mail | `MailHandler.save()` |
| 抽卡 | GachaRecord | `DatabaseHelper.saveGachaRecord()` |
| 添加好友 | Friendship | `FriendsList.save()` |
| 进入新场景修改 group | SceneGroupInstance | `DatabaseHelper.saveGroupInstance()` |
| 战令升级 | BattlePassManager | `BattlePassManager.save()` |
| 接受派遣 | Player（包含 expedition）| `Player.save()` |

### 5.4 没有"定时全量持久"

**Grasscutter 没有 cron 定时 save** —— 完全靠**事件驱动**：
- ✓ 性能好（写入按需触发）
- ✗ 崩溃丢数据风险高（玩家在线 8 小时未触发关键事件 → 全丢）

**生产级游戏的做法**（米哈游正服肯定有）：
- 定时 incremental save（每 5 分钟落盘"脏数据"）
- WAL（Write-Ahead Log）+ snapshot
- Redis 写穿透 + 后台异步刷盘

---

## 6. 索引设计

### 6.1 索引清单

来自 `@Indexed` 注解：

```java
// players collection
@Indexed(options = @IndexOptions(unique = true))
private String accountId;     // 一个 account 只能一个 player

// avatars / items / quests / mail / homes / battlepass / activities
@Indexed protected int ownerId / ownerUid;

// friendships - 双向索引
@Indexed private int ownerId;    // 我的好友
@Indexed private int friendId;   // 谁加我为好友

// gachas - 复合查询
filter(eq("ownerId", uid), eq("gachaType", type))
// → 没显式 @Indexed (ownerId, gachaType), 但 ownerId 单字段索引足够

// activities - 复合
filter(eq("uid", uid), eq("activityId", aid))
```

### 6.2 索引的时机

`DatabaseManager.ensureIndexes()`：
```java
private static void ensureIndexes(Datastore datastore) {
    try {
        datastore.ensureIndexes();
    } catch (MongoCommandException e) {
        if (e.getCode() == 85) {   // ← Duplicate index error
            // 把所有索引删掉重建
            for (String name : collections) {
                datastore.getDatabase().getCollection(name).dropIndexes();
            }
            datastore.ensureIndexes();
        }
    }
}
```

**自愈机制**：
- 启动时尝试建索引
- 如果版本不一致（field 改名等）报错 85 → 全删重建
- 不会因为索引问题让服务器起不来

### 6.3 没有的索引

注意以下查询**没有索引**：
- `find(Player.class).filter(eq("nickname", ...))` — 找昵称（管理员功能）
- `find(GameItem.class).filter(eq("itemId", ...))` — 全服找某物品
- 全量统计（`count()` 无 filter）

→ 这些都是**罕用管理操作**，全表扫描可接受。

---

## 7. 删除：手动级联（无外键）

### 7.1 deleteAccount 的复杂性

`DatabaseHelper.deleteAccount()` (`DatabaseHelper.java:122-158`)：
```java
// MongoDB 没有外键, 全靠手动！
DatabaseManager.getGameDatabase().getCollection("activities").deleteMany(eq("uid", uid));
DatabaseManager.getGameDatabase().getCollection("homes").deleteMany(eq("ownerUid", uid));
DatabaseManager.getGameDatabase().getCollection("mail").deleteMany(eq("ownerUid", uid));
DatabaseManager.getGameDatabase().getCollection("avatars").deleteMany(eq("ownerId", uid));
DatabaseManager.getGameDatabase().getCollection("gachas").deleteMany(eq("ownerId", uid));
DatabaseManager.getGameDatabase().getCollection("items").deleteMany(eq("ownerId", uid));
DatabaseManager.getGameDatabase().getCollection("quests").deleteMany(eq("ownerUid", uid));
DatabaseManager.getGameDatabase().getCollection("battlepass").deleteMany(eq("ownerUid", uid));

// 双向 friendships
DatabaseManager.getGameDatabase().getCollection("friendships").deleteMany(eq("ownerId", uid));
DatabaseManager.getGameDatabase().getCollection("friendships").deleteMany(eq("friendId", uid));

// player 最后删
DatabaseManager.getGameDatastore().find(Player.class).filter(Filters.eq("id", uid)).delete();

// account 最最后
DatabaseManager.getAccountDatastore().find(Account.class).filter(Filters.eq("id", target.getId())).delete();
```

**注意源码注释**：
> "This should optimally be wrapped inside a transaction, to make sure an error thrown mid-way does not leave the database in an inconsistent state, but unfortunately Mongo only supports that when we have a replica set ..."

→ 单实例 MongoDB **不支持事务**，所以**中途崩溃 = 部分数据残留**。
→ 生产环境必须用 replica set 才能开事务。

### 7.2 残留数据的风险

如果 deleteAccount 在中间 crash：
```
✓ activities 已删
✓ homes 已删
✓ mail 已删
✗ ↓ ← 这里 crash
- avatars 未删（残留）
- gachas 未删
- items 未删
- ... 后续全没删
- player 未删 ← 用户重新登录还能进入残破账号!
```

**Grasscutter 的态度**：开源私服，能跑就行；正服肯定不会这么写。

---

## 8. 持久化 vs 内存：边界全图

### 8.1 必须持久化（崩溃后必须恢复）

| 数据 | 在哪 | 为什么必须 |
|---|---|---|
| 账号信息 | accounts | 没了登不进去 |
| 玩家进度 | players (含 30+ embedded) | 没了号没了 |
| 角色 | avatars | 抽到的角色 |
| 物品 | items | 武器/圣遗物/材料 |
| 任务 | quests | 主线进度 |
| 邮件 | mail | 补偿邮件不能丢 |
| 好友 | friendships | 社交关系 |
| 抽卡历史 | gachas | 法律要求展示概率 |
| 家园 | homes | UGC 内容 |
| 战令 | battlepass | 充值的进度 |
| 活动 | activities | 限时进度 |
| 玩家修改的场景元素 | group_instances | 比如砍树/破石头/收集 |

### 8.2 不持久化（仅内存 / @Transient）

| 数据 | 类型 | 为什么不需要 |
|---|---|---|
| World / Scene | 运行时容器 | 玩家登入时重建 |
| 战斗实体 (Monster/Gadget) | Entity 子类 | 进场景时根据 SceneGroup 实例化 |
| AbilityManager 状态 | Manager | 战斗状态短暂, 重连重置接受 |
| GameSession | 网络对象 | 跟着 KCP 走 |
| Online Friends 列表 | 派生数据 | 从 friendships 实时算 |
| Chat 历史 | 内存 Deque | 重启清空（设计取舍）|
| 当前位置（玩家坐标）| Position | 重新登录回到 lastSavedPos |

### 8.3 半持久化（特殊情况）

`SceneGroupInstance` —— **场景动态对象的持久化**：
- 玩家砍掉了一棵树 → 树的 status 改了 → SceneGroupInstance 落盘
- 玩家走了，重连后那棵树还是被砍状态
- 但**怪物的血量 / 当前位置不持久**——重生后满血回点

→ 这是"**改动事件持久, 即时状态不持久**"的设计哲学。

---

## 9. 与游戏行业其他做法对比

### 9.1 MongoDB vs MySQL vs Redis

| 方案 | 写性能 | 查询能力 | 适合场景 |
|---|---|---|---|
| **MongoDB**（Grasscutter）| 中 | 中（NoSQL）| 玩家文档型（聚合根）|
| MySQL（万王传统）| 中 | 强（JOIN）| 强关系（公会/拍卖）|
| Redis | 极高 | 弱 | 排行榜/会话/缓存 |
| Cassandra | 高 | 弱 | 海量写入（日志/事件）|
| 自研 KV | 极高 | 弱 | 米哈游/腾讯量级 |

**米哈游正服**几乎肯定不用 MongoDB，至少：
- Redis 作为玩家数据缓存（热数据）
- 自研存储或 MySQL 作为冷数据
- Kafka 做事件流

但 Grasscutter 是**单机私服**，MongoDB 的灵活 schema 完美匹配开发节奏。

### 9.2 Grasscutter 持久层的取舍

**正面**：
- ✓ 灵活：加字段不需要改表结构
- ✓ 简洁：30+ entity 用同一个 ORM 抽象
- ✓ 自动化：反射注册 + 自动建索引

**负面**：
- ✗ 无事务（单实例 MongoDB）
- ✗ 无横向扩展（counter 是 JVM 级 sync）
- ✗ 大文档（Player 几 MB）→ 网络传输浪费
- ✗ 无定时落盘（崩溃丢失风险）

→ **够用即可**——这是开源私服的合理选型。

---

## 10. 实战演练

### 10.1 用 mongo shell 看玩家数据

```bash
# 连接到 game 库
$ mongo grasscutter
> use grasscutter
> db.getCollectionNames()
[ "accounts", "activities", "avatars", "battlepass", "counters",
  "friendships", "gachas", "group_instances", "homes", "items",
  "mail", "music_game_beatmaps", "players", "quests" ]

# 查某玩家
> db.players.findOne({_id: 100015})
{
    "_id": 100015,
    "accountId": "1",
    "nickname": "Traveler",
    "level": 60,
    "exp": 0,
    "primogems": 16000,
    "mora": 5839204,
    "teamManager": { /* embedded TeamManager */ },
    "towerManager": { /* embedded */ },
    // ... 30+ embedded
}

# 这个玩家所有角色
> db.avatars.find({ownerId: 100015}).count()
12

# 所有物品
> db.items.find({ownerId: 100015}).count()
347

# 所有任务
> db.quests.find({ownerUid: 100015}).count()
58
```

### 10.2 数据表大小估算

```
单玩家 footprint:
  players document        :   1-5 MB (满级 30+ embedded)
  avatars (12-70 个)      :   5-50 KB × N
  items (300-5000 个)     :   1-3 KB × N
  quests (50-200 个)      :   2-10 KB × N
  mail (100-500 封)       :   1-5 KB × N
  gachas (1000+ 条)       :   200 B × N
  
合计: 10-50 MB / 满级玩家
```

10 万人在线 → 1-5 TB 持久化数据。这就是为什么生产级游戏要分库分表。

---

## 11. 数据完整性的脆弱处

### 11.1 玩家退款 / 客服处理

如果玩家充值了，钱进了 BattlePass，但战令没生效：
- **没有事务** → 钱已扣，但 `battlepass.exp += 100` 可能没写入
- **客服只能手动补偿**（发邮件给原石）

### 11.2 Crash recovery

```
[玩家在抽卡]
1. addItem(角色) → items collection 写入
2. updateGachaInfo() → players.gachaInfo 修改
3. saveGachaRecord() → gachas collection 写入

如果 step 2 后 crash:
✓ 玩家拿到了角色
✗ 玩家保底没扣 (没记录在 players)
✗ 历史记录没了 (没记录在 gachas)
→ 玩家投诉"我抽的没记录"
```

**正确做法**：所有 3 步必须**原子**——但 MongoDB 单实例不支持。Grasscutter 接受这种风险。

### 11.3 Concurrent edit

```
[玩家用两个客户端登入同一账号]
HandlerGetPlayerTokenReq 检测到已登录:
    Player exists = getPlayerByAccountId(accountId);
    if (exists != null) {
        existsSession.close();    // ← 踢掉旧 session
        exists.onLogout();        // ← 触发 save
    }
```

**但**仍有并发风险：
- 旧 session 的 onLogout save 中
- 新 session 的 loadFromDatabase 中
- 两个操作交错 → **数据可能旧的覆盖新的**

→ Grasscutter 的解决：`exists.onLogout(); // must save immediately, or the below will load old data`（强制串行）。

---

## 12. 关键收获

1. **MongoDB + Morphia + 反射**：14 个顶层 collection + 50+ 嵌入 entity
2. **双数据库**：accounts 单独, games 一起；支持 dispatch 模式分离
3. **Embedded vs Top-level**：聚合根（Player）vs 独立生命周期（Avatar/Item/Quest/Mail）
4. **Counter collection**：MongoDB 没 SEQUENCE，用单独表 + JVM-sync 自增
5. **save/load 链**：登录 7 次查询；登出 3 次保存；中途散点 save
6. **索引**：全部 ownerId/ownerUid 单字段；accountId unique；其他靠全表扫
7. **删除级联**：手动写 9 行 deleteMany；无事务保护
8. **持久边界**：玩家进度全部持久；World/Scene/Combat 全部内存
9. **半持久化**：SceneGroupInstance（玩家修改的场景元素）持久；怪物 HP 不持久
10. **第 9 次"注解+反射+架构模式"**——`@Entity` 自动扫描注册

---

## 13. 一句话总结

> **持久层用 14 个 MongoDB collection 把玩家进度落盘，30+ 嵌入对象塞进 players 文档；World/Scene/Combat 全部只在内存，登出/事件触发 save，登录瞬间 7 次查询恢复；无事务、手动级联删除——开源私服的"够用即可"取舍。**
> 
> **设计哲学：聚合根（Player）大文档 + 独立子表（Avatar/Item/Quest）按 ownerId 索引——这是 NoSQL 文档型存储的经典玩家持久化范式。**

---

**前置笔记**：
- notes/01 服务端整体架构 - 内存对象的生命周期
- notes/19 多人协作 - World/Scene/Player 三级容器（运行时）
- notes/29 网络协议层 - GetPlayerTokenReq 触发持久化加载

**关联文件**：
- `DatabaseManager.java`(123) - 连接 + 反射注册
- `DatabaseHelper.java`(388) - 50+ DAO 方法
- `Account.java` - dispatch DB 唯一 entity
- `Player.java`(1500+) - 主聚合根
- `Avatar.java` / `GameItem.java` / `GameMainQuest.java` - 独立 collection 的代表

**研究的源代码**: 534 行核心 DB 层 + 61 个 @Entity 注解类的全图。
