# Scene / World 三级容器架构剖析

> 第 35 篇：被引用 100 次但从未专门解剖的**宇宙骨架** —— Entity 装在 Scene 里，Scene 装在 World 里，World 装在 GameServer 里。

---

## 0. 为什么这一篇重要

前 34 篇笔记里 Scene/World 出现频率超高，但具体怎么工作从没说清：
- "addEntity" 到底加到哪里？
- "broadcastPacket" 广播给谁？
- onTick 谁触发？以什么频率？
- 切场景到底发生了什么？
- 联机的 World 和单机的 World 有什么区别？
- 玩家死了怎么自动复活？

这一篇专门挖**容器层** —— 它是支撑所有 entity 系统 (Monster/Gadget/Avatar) 的底座。

---

## 1. 四级金字塔架构

```
                  ┌─────────────────────────────┐
                  │       GameServer            │
                  │  - 所有 World 注册中心          │
                  │  - 全局 tick 调度              │
                  │  - WorldDataSystem 等          │
                  └─────────────┬───────────────┘
                                │ registerWorld
                                ↓
                  ┌─────────────────────────────┐
                  │   World (1 个/玩家或 4 玩家组)│
                  │  - 1+ Player                  │
                  │  - 多个 Scene (缓存)            │
                  │  - 世界时间 / 天气              │
                  │  - 队伍同步                    │
                  └─────────────┬───────────────┘
                                │ registerScene
                                ↓
                  ┌─────────────────────────────┐
                  │   Scene (1 个/sceneId)         │
                  │  - GameEntity Map (100-500)    │
                  │  - SceneScriptManager (Lua)    │
                  │  - WorldChallenge / Dungeon    │
                  │  - SealBattle / Weather        │
                  └─────────────┬───────────────┘
                                │ addEntity
                                ↓
                  ┌─────────────────────────────┐
                  │   GameEntity (无数)             │
                  │  - EntityAvatar (notes/34)     │
                  │  - EntityMonster (notes/32)    │
                  │  - EntityGadget (notes/33)     │
                  │  - EntityNPC / EntityItem ...  │
                  └─────────────────────────────┘
```

**层级所有权**：
| 层 | 拥有者 | 何时创建 | 何时销毁 |
|---|---|---|---|
| GameServer | JVM | 启动时 | 关服 |
| World | Player.onLogin | 进游戏时 | 退游戏 / host 离开 |
| Scene | World.getSceneById | 玩家进新场景 | 场景空且非保留 |
| Entity | Scene.addEntity | spawn / 玩家上场 | killEntity / 切场景 |

---

## 2. World：玩家的"个人宇宙"

`World.java`（512 行）—— 每个玩家登录都创建一个 World。

### 2.1 字段全图（13 个）

```java
public class World implements Iterable<Player> {
    @Getter private final GameServer server;            // 反向引用
    @Getter private final Player owner;                  // ★ 主人（创建者）
    @Getter private final List<Player> players;          // 1+ 玩家
    @Getter private final Int2ObjectMap<Scene> scenes;   // 已加载场景缓存
    
    @Getter private EntityWorld entity;                  // 世界本身作为实体
    private int nextEntityId = 0;                        // 自增 entity ID
    private int nextPeerId = 0;                          // 联机玩家 ID
    @Getter private int worldLevel;                      // 世界等级 (影响怪物等级)
    
    @Getter private boolean isMultiplayer;               // ★ 单机/联机
    
    @Getter private int tickCount = 0;                   // tick 计数
    @Getter private boolean isPaused = false;
    @Getter private boolean isGameTimeLocked = false;    // 时间锁定
    @Getter private boolean isWeatherLocked = false;
    private long lastUpdateTime;
    @Getter private long currentWorldTime = 0;           // 真实时间 (ms)
    @Getter private long currentGameTime = 540;          // 游戏时间 (分钟)
    
    @Getter private Random worldRandomGenerator;         // 世界随机数生成器
}
```

### 2.2 单机 World vs 联机 World

```java
public World(Player player) {
    this(player, false);   // 单机
}

public World(Player player, boolean isMultiplayer) {
    this.owner = player;
    // ...
    this.isMultiplayer = isMultiplayer;
}
```

**关键差异**：
| 维度 | 单机 World | 联机 World |
|---|---|---|
| Player 数 | 1 | 1-4 |
| 谁是 host | 自己 | 房主（World.owner） |
| 时间同步 | 玩家本地 | host 决定 |
| 天气锁 | 玩家设置 | host 决定 |
| 队伍人数 | 4 | `singlePlayerTeam / playerCount` |
| 共享 entity | 是 (但只有自己) | 是 (所有玩家共享) |

→ 联机本质 = **多个 player 在同一个 World 对象中操作**。

### 2.3 peerId 机制

```java
public int getNextPeerId() {
    return ++this.nextPeerId;
}
```

`peerId` 是**联机里的玩家本地 ID**：
- 1 = 房主
- 2, 3, 4 = 加入的玩家
- 显示头像在屏幕一角的顺序

**与 player.uid 区别**：
- `uid` —— 全局玩家 ID（10000+）
- `peerId` —— 这次联机内的临时编号（1-4）

每个 World 实例的 nextPeerId 独立 —— 同一玩家在不同房间 peerId 可能不同。

### 2.4 EntityId 分配：类型混编

```java
public int getNextEntityId(EntityIdType idType) {
    return idType.toTypedEntityId(++this.nextEntityId);
}
```

EntityIdType 给 entity ID **加类型前缀**：
```
Monster:  0x10000000 | counter
Avatar:   0x01000000 | counter
Gadget:   0x20000000 | counter
NPC:      0x30000000 | counter
...
```

**好处**：客户端从 32-bit ID **立刻判断类型**，不需要查表：
```python
if entity_id & 0x10000000:
    is_monster = True
```

→ 这是经典的 **TypedID** 模式——空间换时间。

---

## 3. World 的 5 个核心操作

### 3.1 addPlayer：玩家加入

```java
public synchronized void addPlayer(Player player) {
    if (this.getPlayers().contains(player)) return;
    
    // 从旧 World 移除
    if (player.getWorld() != null) {
        player.getWorld().removePlayer(player);
    }
    
    // 注册到本 World
    player.setWorld(this);
    this.getPlayers().add(player);
    
    // 分配 peerId
    player.setPeerId(this.getNextPeerId());
    
    // 创建 TeamEntity (队伍展示用)
    player.getTeamManager().setEntity(new EntityTeam(player));
    
    // 联机时复制单机队伍配置
    if (this.isMultiplayer()) {
        player.getTeamManager().getMpTeam().copyFrom(
            player.getTeamManager().getCurrentSinglePlayerTeamInfo(),
            player.getTeamManager().getMaxTeamSize());
        player.getTeamManager().setCurrentCharacterIndex(0);
    }
    
    // 加入 Scene
    Scene scene = this.getSceneById(player.getSceneId());
    scene.addPlayer(player);
    
    // 通知其他玩家
    if (this.getPlayers().size() > 1) {
        this.updatePlayerInfos(player);
    }
}
```

**关键时机**：
1. 设置反向引用 (player.setWorld)
2. 分配 peerId
3. 创建 TeamEntity（这是单独的 entity，用于"队伍 marker"）
4. 联机时把单机队伍 copy 到 mpTeam（队伍人数会被截断）
5. 加入 Scene → 触发场景级初始化
6. 通知其他玩家（PacketWorldPlayerInfoNotify 等）

### 3.2 removePlayer：玩家离开（含 host 离开特殊处理）

```java
public synchronized void removePlayer(Player player) {
    // 通知客户端: TeamEntity 消失
    player.sendPacket(new PacketDelTeamEntityNotify(player.getSceneId(), ...));
    
    // 从 World/Scene 注销
    this.getPlayers().remove(player);
    player.setWorld(null);
    Scene scene = this.getSceneById(player.getSceneId());
    scene.removePlayer(player);
    
    // ★ host 离开 → 解散整个 World, 把所有玩家踢回自己的 World
    if (this.getHost() == player) {
        List<Player> kicked = new ArrayList<>(this.getPlayers());
        for (Player victim : kicked) {
            World world = new World(victim);
            world.addPlayer(victim);
            victim.sendPacket(new PacketPlayerEnterSceneNotify(
                victim, EnterType.ENTER_SELF, EnterReason.TeamKick,
                victim.getSceneId(), victim.getPosition()));
        }
    }
}
```

**host 离开 = 解散 World** —— 这就是为什么"房主退出，所有人都被踢回自己世界"。

技术上：
- 给每个剩下的玩家**新建独立 World**
- 触发 `TeamKick` 原因的 EnterSceneNotify
- 玩家在客户端看到"传送回自己的世界"动画

### 3.3 transferPlayerToScene：场景切换

`World.java:285-342`：
```java
public boolean transferPlayerToScene(Player player, TeleportProperties teleportProperties) {
    // 触发可拦截事件
    PlayerTeleportEvent event = new PlayerTeleportEvent(player, ...);
    event.call();
    if (event.isCanceled()) return false;
    
    // 校验目标场景存在
    if (GameData.getSceneDataMap().get(teleportProperties.getSceneId()) == null) {
        return false;
    }
    
    // 旧场景处理
    Scene oldScene = null;
    if (player.getScene() != null) {
        oldScene = player.getScene();
        // 同场景内传送 → 保留场景
        if (oldScene.getId() == teleportProperties.getSceneId()) {
            oldScene.setDontDestroyWhenEmpty(true);
        }
        oldScene.removePlayer(player);
    }
    
    // 加入新场景
    Scene newScene = this.getSceneById(teleportProperties.getSceneId());
    newScene.addPlayer(player);
    
    // 使用 SceneConfig 的默认出生点
    val config = newScene.getScriptManager().getConfig();
    if (teleportProperties.getTeleportTo() == null && config != null) {
        Optional.ofNullable(config.getBornPos()).map(Position::new).ifPresent(teleportProperties::setTeleportTo);
    }
    
    // 设置玩家位置
    Optional.ofNullable(teleportProperties.getTeleportTo()).ifPresent(player.getPosition()::set);
    Optional.ofNullable(teleportProperties.getTeleportRot()).ifPresent(player.getRotation()::set);
    
    if (oldScene != null && newScene != oldScene) {
        newScene.setPrevScene(oldScene.getId());
        oldScene.setDontDestroyWhenEmpty(false);
    }
    
    // 通知客户端
    player.sendPacket(new PacketPlayerEnterSceneNotify(player, teleportProperties));
    player.updateWeather(newScene);
    
    // Quest 触发
    if (teleportProperties.getTeleportType() != TeleportType.INTERNAL && 
        teleportProperties.getTeleportType() != SCRIPT) {
        player.getQuestManager().queueEvent(QuestContent.QUEST_CONTENT_ANY_MANUAL_TRANSPORT);
    }
    return true;
}
```

### 3.4 TeleportType 7 种

```java
case INTERNAL    -> EnterReason.TransPoint;       // 内部传送
case WAYPOINT    -> EnterReason.TransPoint;       // 锚点传送
case MAP         -> EnterReason.TransPoint;       // 地图传送
case COMMAND     -> EnterReason.Gm;               // GM 命令
case SCRIPT      -> EnterReason.Lua;              // 剧情 Lua
case CLIENT      -> EnterReason.ClientTransmit;   // 客户端发起
case DUNGEON     -> EnterReason.DungeonEnter;     // 进副本
```

每种传送有**对应的 EnterReason**，决定客户端怎么过渡（黑屏/cutscene/直接传）。

### 3.5 broadcastPacket：世界广播

```java
public void broadcastPacket(BasePacket packet) {
    for (Player player : this.getPlayers()) {
        player.getSession().send(packet);
    }
}
```

简单粗暴 —— **给 World 里所有 player 各发一次**。

**注意**：World 广播是给**整个世界**（包括不在当前 scene 的玩家）；Scene 广播是给**这个 scene 的人**。

---

## 4. World 的 onTick：核心心跳

```java
public boolean onTick() {
    if (this.getPlayerCount() == 0) return true;   // ★ 没人 = 销毁 World
    
    // 转发给有玩家的 scene
    this.scenes.values().stream()
        .filter(scene -> scene.getPlayerCount() > 0)
        .forEach(Scene::onTick);
    
    // 游戏时间推进
    if (!isGameTimeLocked && !isPaused) {
        currentGameTime++;
    }
    
    // 每 10 tick 同步时间
    if (tickCount % 10 == 0) {
        players.forEach(p -> p.sendPacket(new PacketPlayerGameTimeNotify(p)));
        isGameTimeLocked = getHost().getBoolProperty(PROP_IS_GAME_TIME_LOCKED);
        isWeatherLocked = getHost().getBoolProperty(PROP_IS_WEATHER_LOCKED);
    }
    
    // 每 60 tick 持久化时间
    if (tickCount % 60 == 0) {
        this.owner.updatePlayerGameTime(currentGameTime);
    }
    
    tickCount++;
    return false;
}
```

### 4.1 tick 频率

**1 tick ≈ 1 秒**（实际由 GameServer 决定）。
- 玩家时间同步：每 10 秒
- 时间持久化：每 60 秒
- 游戏时间推进：每 tick +1 分钟（游戏内时间）

**计算游戏时间**：
- 1 分钟（IRL） / 60 = 16.6 秒 IRL = 1 分钟游戏时间
- 实际：**1 tick = 1 游戏分钟**（每秒推进 1 分钟，加速 60 倍）

### 4.2 currentGameTime / currentWorldTime 区别

| 字段 | 含义 | 单位 | 是否暂停 |
|---|---|---|---|
| `currentWorldTime` | 真实流逝时间 | 毫秒 | ✓ 可暂停 |
| `currentGameTime` | 游戏内时间 | 分钟 | ✓ 可锁定 |

游戏时间锁了，**世界时间仍然走**——这就是为什么"日落锁住"但物理还在跑。

### 4.3 闲置 World 销毁

```java
if (this.getPlayerCount() == 0) return true;   // GameServer 收到 true 会销毁这个 World
```

→ 玩家全部登出 → World 销毁 → 关联 Scene 也销毁。

---

## 5. Scene：场景级容器

`Scene.java`（1165 行）—— 这是更具体的容器，每个 sceneId 一个。

### 5.1 字段全图（22 个）

```java
public class Scene {
    @Getter private final World world;                       // 反向引用
    @Getter private final SceneData sceneData;               // 配表数据
    @Getter private final SceneInstanceData sceneInstanceData; // 持久化数据
    
    @Getter private final List<Player> players;              // CopyOnWriteArrayList
    @Getter private final Map<Integer, GameEntity<?>> entities;     // ★ 主 entity 容器
    @Getter private final Map<Integer, GameEntity<?>> weaponEntities; // 武器分离
    
    private final Set<SpawnDataEntry> spawnedEntities;       // 已 spawn 的
    @Getter private final Set<SpawnDataEntry> deadSpawnedEntities; // 已死的（不复活）
    
    private final Set<SceneBlock> loadedBlocks;              // 已加载 block
    @Getter private final Set<SceneGroup> loadedGroups;      // 已加载 group
    private final Set<Integer> replacedGroup;                // 被替换的 group
    
    private final HashSet<Integer> unlockedForces;           // 解锁的力量场
    private final List<Runnable> afterLoadedCallbacks;       // 加载完成回调
    private final long startWorldTime;                       // 开始时间
    
    @Getter @Setter DungeonManager dungeonManager;           // 副本管理器
    @Getter @Setter SealBattleManager sealBattleManager;     // 封印之战
    
    @Getter Int2ObjectMap<Route> sceneRoutes;                // 路径配置
    @Getter List<ScenePointArrayData> pointArrays;
    
    @Getter @Setter private boolean dontDestroyWhenEmpty;    // 销毁锁
    @Getter private final SceneScriptManager scriptManager;  // Lua 引擎
    @Getter @Setter private WorldChallenge challenge;        // 挑战
    
    @Getter private final List<DungeonSettleListener> dungeonSettleListeners;
    @Getter @Setter private int prevScene;                   // 上一场景
    @Getter @Setter private int prevScenePoint;
    
    @Getter @Setter private int killedMonsterCount;
    @Getter @Setter private int killChestCount;
    
    private Set<SceneNpcBornEntry> npcBornEntrySet;          // NPC 出生表
    @Getter private boolean finishedLoading;
    @Getter private int tickCount;
    @Getter private boolean isPaused;
    
    @Getter private final Map<Integer, WeatherArea> weatherAreas;
    private boolean weatherLoaded;
    
    @Getter private final GameEntity sceneEntity;            // 场景本身作为实体
    @Getter @Setter private Map<Integer, Double> scheduledPlatforms;  // 平台调度
}
```

→ 22 个字段，是 grasscutter 中**最复杂的类之一**。它是真正的"场景管理器"。

### 5.2 三个 entity 集合

```java
private final Map<Integer, GameEntity<?>> entities;        // 主集合
private final Map<Integer, GameEntity<?>> weaponEntities;  // 武器（独立）
private final Set<SpawnDataEntry> spawnedEntities;         // 已生成的 spawn 表项
@Getter private final Set<SpawnDataEntry> deadSpawnedEntities; // 已死亡的
```

**为什么分四套**：
- `entities` —— 所有可交互 entity（玩家/怪/Gadget/NPC）
- `weaponEntities` —— 武器单独管理（避免污染主集合的查询）
- `spawnedEntities` —— 配表 spawn 入口（避免重复 spawn）
- `deadSpawnedEntities` —— **持久化**记录（重连后不复活）

### 5.3 EntityScene：场景本身也是实体

```java
private static final int SCENE_ENTITY_ID = 0x13800001;

@Getter private final GameEntity sceneEntity;   // EntityScene

public GameEntity<?> getEntityById(int id) {
    if (id == 0x13800001) return this.sceneEntity;   // ★ 场景本身
    else if (id == this.world.getLevelEntityId()) return this.world.getEntity();   // ★ 世界本身
    // ... 其他 entity
}
```

→ **Scene 和 World 也是 entity** —— 这是为了让能力系统能"对场景施法"（如改变天气）。

---

## 6. Scene 的 entity 管理

### 6.1 添加：三种方式

```java
// 方式 1: 已构造好的 entity
public synchronized void addEntity(GameEntity entity) {
    addEntityDirectly(entity);
    broadcastPacket(new PacketSceneEntityAppearNotify(entity));
    entity.afterCreate(this.players);
}

// 方式 2: 通过 config 构造（Monster/Gadget 二选一）
public synchronized void addEntity(CreateEntityConfig config) {
    GameEntity<?> entity = null;
    if (config.getClass() == CreateMonsterEntityConfig.class) {
        entity = new EntityMonster(this, (CreateMonsterEntityConfig) config);
    } else if (config.getClass() == CreateGadgetEntityConfig.class) {
        entity = new EntityGadget(this, (CreateGadgetEntityConfig) config);
    }
    addEntityDirectly(entity);
    broadcastPacket(new PacketSceneEntityAppearNotify(entity));
}

// 方式 3: 只给单个客户端 (隐形 entity?)
public synchronized void addEntityToSingleClient(Player player, GameEntity entity) {
    addEntityDirectly(entity);
    player.sendPacket(new PacketSceneEntityAppearNotify(entity));
    entity.afterCreate(List.of(player));
}
```

### 6.2 批量添加 + chunking

```java
public synchronized void addEntities(Collection<? extends GameEntity> entities, VisionType visionType) {
    if (entities == null || entities.isEmpty()) return;
    
    entities.forEach(this::addEntityDirectly);
    
    // ★ 100 个一批分包发送
    chopped(entities.stream().toList(), 100).forEach(l -> {
        broadcastPacket(new PacketSceneEntityAppearNotify(l, visionType));
        l.forEach(x -> x.afterCreate(this.players));
    });
}
```

**100 个 entity 一批** —— 避免单 packet 过大（KCP 单包有 MTU 限制）。

### 6.3 VisionType：6 种 entity 出现/消失原因

```java
public enum VisionType {
    VISION_NONE,
    VISION_MEET,       // 第一次进入视野
    VISION_REBORN,     // 重生
    VISION_REPLACE,    // 替换（切角色）
    VISION_WAYPOINT_RESET,
    VISION_MISS,       // 离开视野（不死）
    VISION_DIE,        // 死亡
    VISION_REMOVE,     // 强制移除
    VISION_CHANGE_COSTUME,
    VISION_BORN,       // 首次诞生
    ...
}
```

→ 客户端按 VisionType **播放不同动画**：BORN 飞下来 / DIE 倒地消失 / MEET 直接出现。

### 6.4 replaceEntity：原地替换

```java
public synchronized void replaceEntity(EntityAvatar oldEntity, EntityAvatar newEntity) {
    removeEntityDirectly(oldEntity);
    addEntityDirectly(newEntity);
    broadcastPacket(new PacketSceneEntityDisappearNotify(oldEntity, VisionType.VISION_REPLACE));
    broadcastPacket(new PacketSceneEntityAppearNotify(newEntity, VisionType.VISION_REPLACE, oldEntity.getId()));
}
```

→ 切角色用这个 —— 旧 entity 消失，新 entity 出现，同时携带"is REPLACE"。客户端做无缝过渡动画。

---

## 7. Scene 的 onTick：场景级心跳

```java
public void onTick() {
    // 家园场景不跑脚本
    if (getSceneType() == SceneType.SCENE_HOME_WORLD || getSceneType() == SceneType.SCENE_HOME_ROOM) {
        finishLoading();
        return;
    }
    
    // 脚本驱动 spawn vs 距离驱动 spawn
    if (this.scriptManager.isInit()) {
        if (this.tickCount % 2 == 0) checkGroups();    // 每 2 tick 检查 group
    } else {
        checkSpawns();   // 网格 spawn
    }
    
    // 触发器
    this.scriptManager.checkRegions();
    
    // 挑战超时
    Optional.ofNullable(this.challenge).ifPresent(WorldChallenge::onCheckTimeOut);
    
    // 所有 entity 的 tick
    this.entities.values().forEach(e -> e.onTick(getSceneTimeSeconds()));
    
    // 平台移动调度
    checkPlatforms();
    // NPC group 加载
    checkNpcGroup();
    // 加载完成回调
    finishLoading();
    // 自动复活
    checkPlayerRespawn();
    // 封印之战
    this.getSealBattleManager().onTick();
    
    // 每 10 tick 同步场景时间
    if (this.tickCount % 10 == 0) {
        broadcastPacket(new PacketSceneTimeNotify(this));
    }
    
    this.tickCount++;
}
```

**Scene tick 干 8 件事**：
1. 触发 group spawn/despawn（脚本驱动）
2. 距离驱动 spawn（无脚本时）
3. Region 触发器（玩家进入特定区域）
4. 挑战超时
5. **每 entity 的 onTick**（让平台移动、计时器走、回血等）
6. 平台调度执行
7. NPC group 动态加载
8. 玩家自动复活检测

---

## 8. 自动复活机制

`Scene.checkPlayerRespawn()`：
```java
private void checkPlayerRespawn() {
    if (this.scriptManager.getConfig() == null) return;
    
    val diePos = this.scriptManager.getConfig().getDieY();   // 死亡 Y 坐标 (虚空线)
    
    // 玩家掉到 die_y 以下 → 自动复活
    this.players.stream()
        .filter(p -> diePos >= p.getPosition().getY())
        .forEach(this::respawnPlayer);
    
    // entity 掉到 die_y 以下 → 杀掉
    this.entities.values().stream()
        .filter(e -> diePos >= e.getPosition().getY())
        .forEach(this::killEntity);
}
```

### 8.1 dieY：每场景的"虚空线"

每个 sceneId 在 SceneConfig 里配一个 `dieY`：
- 蒙德地表：dieY = -100（掉下海会死）
- 深空螺旋：dieY = -50（更紧的边界）
- 家园：dieY = ... 

玩家 Y 坐标低于这个值 → **自动判定为坠落 / 落空** → respawn。

### 8.2 respawnPlayer：复活流程

```java
public boolean respawnPlayer(Player player) {
    player.getTeamManager().onAvatarDieDamage();   // 队伍死亡处理
    
    return this.world.transferPlayerToScene(player, TeleportProperties.builder()
        .sceneId(getId())
        .prevSceneId(getId())
        .prevPos(player.getPosition())
        .teleportTo(getRespawnLocation(player))    // 锚点 / 副本起点
        .teleportRot(getRespawnRotation(player))
        .teleportType(PlayerTeleportEvent.TeleportType.INTERNAL)
        .enterReason(this.dungeonManager != null ? 
            EnterReason.DungeonReviveOnWaypoint : EnterReason.Revival)
        .build());
}
```

→ 本质：**调用 transferPlayerToScene 把玩家传送回锚点**。
→ 副本里复活回最后传送点；大地图复活回上次锚点。

---

## 9. handleAttack：伤害路由

`Scene.handleAttack()`：
```java
public void handleAttack(AttackResult result) {
    val target = getEntityById(result.getDefenseId());
    val attackType = ElementType.getTypeByValue(result.getElementType());
    if (target == null) return;
    
    // ★ 无敌模式检查
    if (target instanceof EntityAvatar entityAvatar) {
        if (entityAvatar.getPlayer().inGodmode()) return;
        
        if (result.getDamage() != result.getDamageShield()) {
            Optional.ofNullable(this.challenge).ifPresent(c ->
                c.onDamageMonsterOrShield(getEntityById(result.getAttackerId()),
                    result.getDamageShield() - result.getDamage()));
        }
    }
    
    target.damage(result.getDamage(), result.getAttackerId(), attackType);
    
    if (target instanceof EntityGadget gadget) {
        Optional.ofNullable(this.challenge).ifPresent(c -> c.onGadgetDamage(gadget));
    }
}
```

**Scene 是伤害的 "枢纽"**：
- 任何 entity 攻击任何 entity，AttackResult 都先到 Scene
- Scene 路由到正确的目标
- 处理无敌 (godmode)
- 处理挑战 (challenge)

---

## 10. checkSpawns：距离驱动的 spawn

`Scene.java:576-643`：
```java
private synchronized void checkSpawns() {
    // 计算玩家所在的 GridBlock 集合
    val loadedGridBlocks = this.players.stream()
        .map(p -> SpawnDataEntry.GridBlockId.getAdjacentGridBlockIds(p.getSceneId(), p.getPosition()))
        .flatMap(Arrays::stream).collect(Collectors.toSet());
    
    // 没变化 → 跳过
    if (this.loadedGridBlocks.containsAll(loadedGridBlocks)) return;
    
    this.loadedGridBlocks = loadedGridBlocks;
    val visible = loadedGridBlocks.stream()
        .map(GameDepot.getSpawnLists()::get)
        .filter(Objects::nonNull).flatMap(List::stream).collect(Collectors.toSet());
    
    // 世界等级 → 怪物等级
    final int worldLevelOverride = Optional.ofNullable(GameData.getWorldLevelDataMap().get(this.world.getWorldLevel()))
        .map(WorldLevelData::getMonsterLevel).orElse(0);
    
    List<GameEntity> toAdd = new ArrayList<>();
    List<GameEntity> toRemove = new ArrayList<>();
    
    for (SpawnDataEntry entry : visible) {
        if (spawnedEntities.contains(entry) || this.deadSpawnedEntities.contains(entry)) continue;
        
        GameEntity<?> entity = null;
        if (entry.getMonsterId() > 0) {
            final int level = getEntityLevel(entry.getLevel(), worldLevelOverride);
            val config = new CreateMonsterEntityConfig(entry).setLevel(level);
            entity = new EntityMonster(this, config);
        } else if (entry.getGadgetId() > 0) {
            val createConfig = new CreateGadgetEntityConfig(entry);
            val gadget = new EntityGadget(this, createConfig);
            gadget.setFightProperty(FIGHT_PROP_BASE_HP, Float.POSITIVE_INFINITY);
            // ↑ 距离 spawn 的 gadget 默认无敌
            entity = gadget;
        }
        if (entity == null) continue;
        toAdd.add(entity);
        spawnedEntities.add(entry);
    }
    
    // 离开视野的 entity 移除
    this.entities.values().stream()
        .filter(entity -> entity.getSpawnEntry() != null)
        .filter(entity -> !(entity instanceof EntityWeapon))
        .filter(entity -> !visible.contains(entity.getSpawnEntry()))
        .peek(toRemove::add).map(GameEntity::getSpawnEntry).forEach(spawnedEntities::remove);
    
    if (!toAdd.isEmpty()) {
        toAdd.forEach(this::addEntityDirectly);
        broadcastPacket(new PacketSceneEntityAppearNotify(toAdd, VisionType.VISION_BORN));
    }
    if (!toRemove.isEmpty()) {
        toRemove.forEach(this::removeEntityDirectly);
        broadcastPacket(new PacketSceneEntityDisappearNotify(toRemove, VisionType.VISION_REMOVE));
    }
}
```

### 10.1 GridBlock 空间索引

场景被切成**网格**（如 100m × 100m）。每个网格存"附近会出现的 SpawnDataEntry 列表"。

玩家在场景中跑：
- 计算玩家**当前 + 相邻**的 9 个 GridBlock
- 求并集得到"应该可见的 spawn 列表"
- 对比上次状态，**只 spawn 新进入的、despawn 离开的**

**性能关键**：
- 不是每 tick 都扫描全场（场景几千个 spawn 点）
- 只看玩家周围 1 个 grid 范围
- 仅当 grid 变化才重算

→ 这就是经典的 **Spatial Hash Grid** 算法。

### 10.2 死亡持久化

```java
if (spawnedEntities.contains(entry) || this.deadSpawnedEntities.contains(entry)) continue;
//                                      ↑ ★ 已死的不再 spawn
```

`deadSpawnedEntities` 是**持久化的死亡列表**：
- 你打死的丘丘人不会立刻重生
- 关服重连后还是死的
- 隔天 / 隔周才重置（每周一刷怪机制）

---

## 11. dontDestroyWhenEmpty：销毁锁

```java
@Getter @Setter private boolean dontDestroyWhenEmpty;
```

正常情况：场景没人 → 销毁。
但**有些场景需要保留**：
- 副本（玩家可能要回来）
- 同场景内传送（不要 reload）
- 联机其他玩家可能进来

`transferPlayerToScene` 中的逻辑：
```java
if (oldScene.getId() == teleportProperties.getSceneId()) {
    oldScene.setDontDestroyWhenEmpty(true);   // ★ 同场景传送, 保留
}
```

→ 同场景内 waypoint 跳跃**不会重新加载场景**——保留所有 entity 状态。

---

## 12. SceneInstanceData：场景持久化

```java
SceneInstanceData data = DatabaseHelper.loadSceneInstanceData(sceneData.getId(), world.getOwner());
if (data != null)
    this.sceneInstanceData = data;
else
    this.sceneInstanceData = new SceneInstanceData(this, world.getOwner());
```

`SceneInstanceData` 是 **per-player + per-scene 的持久数据**（见 notes/30）：
- 玩家在此场景的访问记录
- 持久化的 gadget 状态
- 已死 entity 列表

→ 同一场景对**不同玩家**显示不同状态（你开过的箱子 vs 新玩家没开过）。

---

## 13. afterLoadedCallbacks：延迟执行

```java
private final List<Runnable> afterLoadedCallbacks = new ArrayList<>();

public void runWhenFinished(Runnable runnable) {
    if (this.finishedLoading) {
        runnable.run();
        return;
    }
    this.afterLoadedCallbacks.add(runnable);
}

private void finishLoading() {
    if (this.finishedLoading) return;
    this.finishedLoading = true;
    this.afterLoadedCallbacks.forEach(Runnable::run);
    this.afterLoadedCallbacks.clear();
}
```

**用途**：场景还在加载 → 把动作排队，等加载完批量执行。

例子：
- Quest spawn 怪物，但场景还没准备好
- 加到 callbacks，loaded 后再 spawn
- 避免"entity 创建在空场景"的竞态

---

## 14. 联机时的复杂性

### 14.1 多玩家在同一 Scene

```java
@Getter private final List<Player> players = new CopyOnWriteArrayList<>();
//                                            ↑ ★ 线程安全（写少读多）
```

为什么用 `CopyOnWriteArrayList`：
- 读多（每 tick 都遍历）
- 写少（玩家加入/离开是事件）
- 完全避免读写锁

### 14.2 同步 GameEntity Map

```java
@Getter private final Map<Integer, GameEntity<?>> entities = new ConcurrentHashMap<>();
//                                                            ↑ ★ 并发读写
```

为什么用 ConcurrentHashMap：
- 战斗高频写（damage 改 HP / 死亡）
- 高频读（每 tick 遍历）
- 多玩家并发触发事件

### 14.3 视野广播

```java
public void broadcastPacket(BasePacket packet) {
    for (Player player : this.players) {
        player.getSession().send(packet);
    }
}
```

→ Scene 广播给**本 Scene 的玩家**——不广播给同 World 但在其他 Scene 的玩家。
→ 这就是为什么联机时其他玩家进副本就"看不到他们了"。

---

## 15. 实战：进入新场景的完整流程

把 World + Scene 串起来，看一次 sceneId 切换：

```
[玩家点击锚点]
    ↓
[客户端] PostEnterSceneReq { sceneId=3, position=(...) }
    ↓
[Handler] HandlerPostEnterSceneReq
    ↓
[World.transferPlayerToScene]
    ├── PlayerTeleportEvent.call() (plugin 可拦截)
    ├── 检查 sceneData 存在
    ├── 旧 Scene.removePlayer(player)
    │   ├── 从 players 移除
    │   ├── 移除 active team avatars (VISION_MISS)
    │   ├── 检查 PlayerCount == 0 → 销毁 Scene
    │   └── saveSceneInstanceData
    ├── 新 Scene = world.getSceneById(sceneId)
    │   ├── 如果不存在 → new Scene(this, sceneData)
    │   │   ├── 创建 SceneScriptManager
    │   │   ├── 创建 SealBattleManager
    │   │   ├── 创建 EntityScene
    │   │   └── 加载 SceneInstanceData
    │   └── registerScene
    ├── newScene.addPlayer(player)
    │   ├── 设置 player.setScene(this)
    │   ├── setupPlayerAvatars (创建 4 个 EntityAvatar)
    │   └── updateWeather
    ├── 设置位置
    ├── 发送 PacketPlayerEnterSceneNotify
    └── 触发 Quest.QUEST_CONTENT_ANY_MANUAL_TRANSPORT
    
[客户端] 收到 EnterSceneNotify → 黑屏 → 加载场景资源
    ↓
[客户端] EnterSceneDoneReq { sceneId=3 }
    ↓
[Handler] checkSpawns / showOtherEntities / appearNotify ...
    ↓
[Scene.onTick] 开始正常 tick
```

→ **一次场景切换 ≈ 25+ 步操作 + 5-10 个 packet 往返**。

---

## 16. 容器系统的关键收获

1. **四级金字塔**：GameServer → World → Scene → Entity，每层有明确职责
2. **World 是个人宇宙**：1 玩家 1 World（单机）；4 玩家共 1 World（联机）
3. **Scene 是场景集装箱**：22 字段 / 1165 行——最复杂的核心类
4. **EntityId 类型混编**：高位标记 Monster/Gadget/Avatar，客户端 O(1) 判类型
5. **peerId vs uid**：联机临时编号 vs 全局账号
6. **host 离开解散 World**：每个剩下的 player 新建独立 World 踢回
7. **tick 频率**：1 秒 1 tick，10 tick 同步时间，60 tick 持久化
8. **3 个 entity 集合**：主集合 + 武器 + spawn 表项（已 spawn/已死）
9. **EntityScene/EntityWorld 也是 entity**：能力系统可"对场景施法"
10. **6 种 VisionType**：BORN/MEET/REBORN/REPLACE/MISS/DIE —— 客户端按此播动画
11. **批量 100 个一包**：避免单 packet 过大
12. **dieY 虚空线**：每场景配出虚空高度，玩家落下 → 自动 respawn
13. **GridBlock 空间索引**：玩家周围 9 格 spawn，移动驱动检测
14. **deadSpawnedEntities 持久化**：死了不复活（直到周一刷）
15. **dontDestroyWhenEmpty 销毁锁**：同场景跳跃保留场景
16. **CopyOnWriteArrayList + ConcurrentHashMap**：精细的并发选型
17. **afterLoadedCallbacks 异步初始化**：加载完才执行排队的动作
18. **handleAttack 是伤害枢纽**：所有伤害先到 Scene 路由
19. **Scene 广播 vs World 广播**：场景内 vs 整个世界

---

## 17. 一句话总结

> **GameServer → World (个人宇宙) → Scene (场景集装箱) → Entity (40+ 类型) 四级金字塔。World 1 个 / 玩家 (单机) 或 4 玩家共享 (联机), 持有时间/天气/peerId/scenes 缓存; Scene 22 字段 1165 行管 entities 集合 + 脚本 + 挑战 + 副本; tick 1 秒 1 次按层级传播; EntityId 类型混编让客户端 O(1) 判类型; deadSpawnedEntities + dieY + dontDestroyWhenEmpty 三剑客控制 entity 生死与场景生命周期.**
> 
> **设计哲学: 显式所有权链 + 类型化 ID + 空间网格驱动 spawn + 并发优化集合 + 异步加载回调——是一套经典的"实时游戏服务器骨架".**

---

**前置笔记**：
- notes/32-34 三大实体三部曲 (Monster/Gadget/Avatar) - 装在容器里的内容
- notes/14 SceneScript - Scene 内的 Lua 引擎
- notes/19 Multiplayer - World 的多人版本
- notes/30 持久化 - SceneInstanceData
- notes/29 网络协议 - broadcastPacket 走 KCP

**关联文件**：
- `World.java`(512) - 个人宇宙
- `Scene.java`(1165) - 场景集装箱 (最复杂)
- `SceneInstanceData.java` - 持久化
- `SceneGroupInstance.java` - 组实例持久化
- `EntityWorld.java` / `EntityScene.java` / `EntityTeam.java` - 容器自身作为实体
- `TeleportProperties.java` - 传送参数
- `SpawnDataEntry.java` - spawn 配表
- `VisionType` 枚举 - 6 种出现/消失原因

**研究的源代码**: 1677 行 World + Scene 核心代码。
