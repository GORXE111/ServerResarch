# GameServer 启动序列与全局调度深度剖析

> 第 46 篇：把 notes/29 (KCP) + notes/30 (DB) + notes/43 (Quest) + notes/44 (Lua) + notes/45 (GameData) 这些"系统"串成**完整的服务器生命周期**——main() 到 onTick 到 shutdown。

---

## 0. 为什么这一篇重要

前 45 篇笔记把每个子系统都挖了，但**服务器是怎么"跑起来"的**？
- `main()` 到客户端能连入要经过多少步？
- 14+ GameSystem 在哪创建？谁先谁后？
- `onTick()` 每秒做什么？
- KCP 怎么启动？参数是什么？
- 关服时按什么顺序清理？

这一篇是 grasscutter 服务器的**生命周期总览**。

---

## 1. main() 启动序列（11 步）

`Grasscutter.java:93-171` —— **整个服务器的入口**：

```java
public static void main(String[] args) throws Exception {
    // === 阶段 1: 静态初始化 (在 main 前已执行) ===
    // - logback 配置
    // - MongoDB logger 静音
    // - loadConfig() + Language + startupCheck
    
    // === 阶段 2: main 主流程 ===
    Crypto.loadKeys();                          // 1. 加密密钥 (RSA + Dispatch + Secret)
    
    if (StartupArguments.parse(args)) {         // 2. CLI 参数 (-c run-mode 等)
        System.exit(0);
    }
    
    commandMap = new CommandMap(true);          // 3. 命令系统 (/give /tp 等)
    
    Grasscutter.updateDayOfWeek();              // 4. 周几 (用于每日重置)
    ResourceLoader.loadAll();                   // 5. ★ 加载 GameData (notes/45, 15-30 秒!)
    
    Tools.createGmHandbooks();                  // 6. 生成 GM 手册
    Tools.generateGachaMappings();              // 7. 抽卡映射
    
    DatabaseManager.initialize();               // 8. ★ MongoDB 连接 + 索引 (notes/30)
    
    authenticationSystem = new DefaultAuthentication();  // 9. 鉴权系统
    permissionHandler = new DefaultPermissionHandler();
    
    httpServer = new HttpServer();              // 10. ★ HTTP 服务器 (notes/31)
    gameServer = new GameServer();              // 11. ★ Game 服务器 (KCP)
    new ServerHook(gameServer, httpServer);    
    
    pluginManager = new PluginManager();        // 12. 插件管理器
    
    // 注册 HTTP routes (notes/31)
    httpServer.addRouter(UnhandledRequestRouter.class);
    httpServer.addRouter(DefaultRequestRouter.class);
    httpServer.addRouter(RegionHandler.class);
    // ... 9 个 router 共计
    
    // === 阶段 3: 启动监听 ===
    var runMode = Grasscutter.getRunMode();
    if (runMode == ServerRunMode.HYBRID) {      // ★ 同时启动 HTTP + KCP
        httpServer.start();
        gameServer.start();
    } else if (runMode == ServerRunMode.DISPATCH_ONLY) {
        httpServer.start();
    } else if (runMode == ServerRunMode.GAME_ONLY) {
        gameServer.start();
    }
    
    pluginManager.enablePlugins();              // 13. 启用所有插件
    
    Runtime.getRuntime().addShutdownHook(new Thread(Grasscutter::onShutdown));
    
    startConsole();                              // 14. 控制台命令交互
}
```

### 1.1 静态初始化（main 之前）

```java
static {
    System.setProperty("logback.configurationFile", "src/main/resources/logback.xml");
    
    // 关掉 MongoDB 日志（太吵）
    val mongoLogger = (Logger) LoggerFactory.getLogger("org.mongodb.driver");
    mongoLogger.setLevel(Level.OFF);
    
    Grasscutter.loadConfig();
    ConfigContainer.updateConfig();
    Grasscutter.loadLanguage();
    Utils.startupCheck();
}
```

→ JVM 加载 Grasscutter 类时就执行——配置 / 语言 / 启动检查**早于 main**。

### 1.2 11 步关键节点

| 步 | 操作 | 时间 |
|---|---|---|
| 1 | Crypto.loadKeys | < 100ms |
| 5 | **ResourceLoader.loadAll** | **15-30s ★大头** |
| 6-7 | 生成 GM 手册 + Gacha 映射 | 1-2s |
| 8 | DatabaseManager.initialize | 1-3s |
| 10 | HttpServer 创建 | < 200ms |
| 11 | GameServer 创建 (14 系统) | 1-2s |
| 12-13 | 启动 HTTP + KCP | < 500ms |

→ **总启动 18-35 秒** —— 之后才接收客户端。

---

## 2. 全局对象与反射根

`Grasscutter.java` 顶部声明：
```java
public final class Grasscutter {
    @Getter private static final Logger logger = LoggerFactory.getLogger(Grasscutter.class);
    @Getter @Setter private static Language language;
    
    public static final File configFile = new File("./config.json");
    
    @Getter private static HttpServer httpServer;
    @Getter private static GameServer gameServer;
    @Getter private static PluginManager pluginManager;
    @Getter private static CommandMap commandMap;
    
    @Getter @Setter private static AuthenticationSystem authenticationSystem;
    @Getter @Setter private static PermissionHandler permissionHandler;
    
    public static final Reflections reflector = new Reflections("emu.grasscutter");
    //                       ↑ ★ 全局反射根
    @Getter public static ConfigContainer config;
}
```

### 2.1 Reflections reflector：全局反射

```java
public static final Reflections reflector = new Reflections("emu.grasscutter");
```

**用途**（贯穿 13+ 个子系统）：
- `QuestSystem` 反射注册 190+ handler (notes/43)
- `AbilityManager` 反射注册 15+ ActionHandler (notes/37)
- `PacketHandler` 反射注册 600+ packet (notes/29)
- `DatabaseManager` 反射注册 @Entity (notes/30)
- `ResourceLoader` 反射扫描 GameResource (notes/45)
- `CommandMap` 反射注册命令
- ...

→ **一个 Reflections 实例**扫描整个包，所有子系统共享 —— 启动时一次扫描，运行时反复用。

→ 这是 grasscutter 中**"注解+反射"模式的根** —— 13+ 次注解注册全靠它。

---

## 3. GameServer 构造器（80-136 行）

```java
public GameServer(InetSocketAddress address) {
    // === Step 1: KCP 通道配置 ===
    ChannelConfig channelConfig = new ChannelConfig();
    channelConfig.nodelay(true, GAME_INFO.kcpInterval, 2, true);
    channelConfig.setMtu(1400);             // ★ MTU 1400 (避开 IP 分片)
    channelConfig.setSndwnd(256);            // 发送窗口
    channelConfig.setRcvwnd(256);            // 接收窗口
    channelConfig.setTimeoutMillis(30 * 1000);  // 30s 超时
    channelConfig.setUseConvChannel(true);
    channelConfig.setAckNoDelay(false);
    
    this.init(GameSessionManager.getListener(), channelConfig, address);
    
    // === Step 2: 静态 Manager initialize ===
    EnergyManager.initialize();              // 加载 EnergyDrop.json
    StaminaManager.initialize();              // 加载体力配置
    CookingManager.initialize();              // 加载烹饪配方
    CookingCompoundManager.initialize();
    CombineManger.initialize();
    
    // === Step 3: 基础容器 ===
    this.address = address;
    this.packetHandler = new GameServerPacketHandler(PacketHandler.class);   // 反射注册 600+ packet
    this.players = new ConcurrentHashMap<>();
    this.worlds = Collections.synchronizedSet(new HashSet<>());
    
    // === Step 4: 调度器 ===
    this.scheduler = new ServerTaskScheduler();
    this.taskMap = new TaskMap(false);
    
    // === Step 5: 14 个 GameSystem 实例化 ===
    this.scriptSystem = new ScriptSystem(this);
    this.inventorySystem = new InventorySystem(this);
    this.gachaSystem = new GachaSystem(this);
    this.shopSystem = new ShopSystem(this);
    this.multiplayerSystem = new MultiplayerSystem(this);
    this.dungeonSystem = new DungeonSystem(this);
    this.dropSystem = new DropSystem(this);
    this.expeditionSystem = new ExpeditionSystem(this);
    this.combineSystem = new CombineManger(this);
    this.towerSystem = new TowerSystem(this);
    this.worldDataSystem = new WorldDataSystem(this);
    this.battlePassSystem = new BattlePassSystem(this);
    this.announcementSystem = new AnnouncementSystem(this);
    this.questSystem = new QuestSystem(this);
    
    // === Step 6: Chat 单独 ===
    this.chatManager = new ChatSystem(this);
    
    // === Step 7: 任务扫描 ===
    taskMap.scan();   // 反射扫描 @Scheduled
    
    // === Step 8: 关服钩子 ===
    Runtime.getRuntime().addShutdownHook(new Thread(this::onServerShutdown));
}
```

### 3.1 KCP 配置细节

```java
channelConfig.setMtu(1400);            // < 1500 避免 IP 分片
channelConfig.setSndwnd(256);           // 发送窗口大 = 高吞吐
channelConfig.setRcvwnd(256);           // 接收窗口大 = 应对突发
channelConfig.setTimeoutMillis(30000);  // 30s 超时（客户端 30s 没动静断开）
channelConfig.nodelay(true, kcpInterval, 2, true);
//                ↑ nodelay: 启用快速重传
```

**KCP 调优意图**：
- `nodelay=true` —— 牺牲带宽换延迟
- `sndwnd=256` —— 比默认 32 大 8 倍，**高吞吐**
- `mtu=1400` —— 比常见 1500 小一点避免分片

### 3.2 14 个 GameSystem（grasscutter 服务端骨架）

| System | 行 | 功能 |
|---|---|---|
| ScriptSystem | ? | Lua 引擎全局 |
| InventorySystem | ? | 物品操作工具 |
| GachaSystem | ? | 抽卡核心 (notes/21) |
| ShopSystem | ? | 商店 |
| MultiplayerSystem | ? | 联机协调 (notes/18) |
| **DungeonSystem** | ? | 副本入口 (notes/19) |
| DropSystem | 112 | 怪物掉落 (notes/39) |
| ExpeditionSystem | ? | 派遣 |
| **CombineManger** | ? | 合成 (notes/25) |
| TowerSystem | ? | 深境螺旋 |
| WorldDataSystem | ? | 世界全局数据 |
| BattlePassSystem | ? | 战令 (notes/22) |
| AnnouncementSystem | ? | 公告调度 |
| **QuestSystem** | 174 | 任务核心 (notes/43) |
| (ChatSystem 单独) | ? | 聊天 |

→ **14 个 System + 1 个 Chat = 15 个全局子系统**。每个都是 `BaseGameSystem` 子类（仅 13 行的简单基类）。

### 3.3 BaseGameSystem：极简基类

```java
public abstract class BaseGameSystem {
    protected final GameServer server;
    
    public BaseGameSystem(GameServer server) {
        this.server = server;
    }
    
    public GameServer getServer() {
        return this.server;
    }
}
```

→ **13 行**就这么多。System 主要靠**约定**（构造器接 GameServer + 提供 service 方法）。

### 3.4 Static initialize 模式

5 个 Manager 有 `static initialize()`：
```java
EnergyManager.initialize();        // 加载 EnergyDrop.json / SkillParticleGeneration.json
StaminaManager.initialize();        // 加载体力配置
CookingManager.initialize();        // 加载烹饪配方
CookingCompoundManager.initialize();
CombineManger.initialize();
```

→ 这些**不是 GameSystem 子类**，是 BasePlayerManager 子类（per player）。它们的**静态数据**需要预加载。

→ **设计不一致点**：有些数据走 ResourceLoader（自动），有些走 static initialize（手动）。grasscutter 演化痕迹。

---

## 4. start()：启动 Timer 主循环

```java
public void start() {
    // ★ Java 标准库 Timer (单线程)
    Timer gameLoop = new Timer();
    gameLoop.scheduleAtFixedRate(new TimerTask() {
        @Override
        public void run() {
            try {
                onTick();
            } catch (Exception e) {
                Grasscutter.getLogger().error("game_update_error", e);
            }
        }
    }, new Date(), 1000L);   // ★ 每 1000ms 触发
    
    Grasscutter.getLogger().info(translate("messages.status.free_software"));
    Grasscutter.getLogger().info("Game bound to {}:{}", ...);
    
    ServerStartEvent event = new ServerStartEvent(ServerEvent.Type.GAME, OffsetDateTime.now());
    event.call();
}
```

### 4.1 Timer vs ScheduledExecutorService

```java
Timer gameLoop = new Timer();
gameLoop.scheduleAtFixedRate(task, 0, 1000L);
```

**为什么选 Timer 而不是 ScheduledExecutorService**：
- ✗ Timer 单线程，一个 task 慢拖累后面
- ✗ Timer 没有更细粒度的并发控制
- ✓ 简单——一个 Timer 就够
- ✓ scheduleAtFixedRate 固定间隔（不漂移）

→ grasscutter 接受"tick 慢就慢"——反正 1 秒间隔很宽松。

### 4.2 tick 异常隔离

```java
try {
    onTick();
} catch (Exception e) {
    Grasscutter.getLogger().error("game_update_error", e);
}
```

→ tick 抛异常**不会停止 Timer** —— 下一秒继续。

→ 单个玩家/World 出错不影响整服。

### 4.3 ServerStartEvent 钩子

```java
ServerStartEvent event = new ServerStartEvent(ServerEvent.Type.GAME, OffsetDateTime.now());
event.call();
```

→ **插件可监听** —— 启动时初始化插件状态。

---

## 5. onTick()：每秒做什么

```java
public synchronized void onTick() {
    var tickStart = Instant.now();
    
    // 1. Tick worlds (没人就销毁)
    this.worlds.removeIf(World::onTick);
    
    // 2. Tick players
    this.players.values().forEach(Player::onTick);
    
    // 3. Tick scheduler (跑延迟任务)
    this.getScheduler().runTasks();
    
    // 4. 触发插件钩子
    ServerTickEvent event = new ServerTickEvent(tickStart, Instant.now());
    event.call();
}
```

### 5.1 4 步 tick 顺序

```
[Step 1] worlds.removeIf(World::onTick)
   ↓
   每个 World.onTick (notes/35):
     - 检查 PlayerCount 0 → 返回 true → 从 set 移除 (销毁 World)
     - 遍历 scene 调 Scene.onTick (notes/35)
       - checkRegions (Region trigger, notes/44)
       - entity.onTick × N
       - checkPlatforms
       - checkNpcGroup
       - checkPlayerRespawn

[Step 2] players.forEach(Player::onTick)
   ↓
   每个 Player.onTick:
     - questManager.onTick (notes/43, time-var 检查)
     - 心跳逻辑

[Step 3] scheduler.runTasks
   ↓
   ServerTaskScheduler.runTasks (148 行)
     - 跑所有到期的 delayed task
     - 跑所有 repeating task

[Step 4] ServerTickEvent.call
   ↓
   插件监听器执行
```

### 5.2 synchronized 关键字

```java
public synchronized void onTick() {
```

→ **整个 onTick 串行执行** —— 防止两次 tick 重叠（Timer 通常不会重叠，但保险）。

### 5.3 没玩家的 World 自动销毁

```java
this.worlds.removeIf(World::onTick);
//          ↑ removeIf 用 World::onTick 返回值过滤
```

`World.onTick` (notes/35) 返回 true 表示"该销毁"（playerCount == 0）。**这一行优雅**。

---

## 6. registerWorld / registerPlayer 注册中心

```java
public void registerWorld(World world) {
    this.getWorlds().add(world);
    // ... (有注释掉的 RefreshPolicy 代码 - 未实现)
}

public void deregisterWorld(World world) {
    world.save();   // 保存玩家的 World
}

public void registerPlayer(Player player) {
    getPlayers().put(player.getUid(), player);
}
```

### 6.1 双 Map 设计

```java
private final Map<Integer, Player> players;   // uid → Player
private final Set<World> worlds;              // 所有 World 集合
```

**为什么 Player 用 Map**：
- 按 UID 查 player O(1)
- "联机邀请玩家 X" 需要快速找到

**为什么 World 用 Set**：
- 不需要按 ID 查（World 没有全局 ID）
- 只需要遍历 tick

### 6.2 getPlayerByUid 双查找

```java
public Player getPlayerByUid(int id, boolean allowOfflinePlayers) {
    if (id == GameConstants.SERVER_CONSOLE_UID) return null;
    
    Player player = this.getPlayers().get(id);   // 1. 在线玩家
    
    if (!allowOfflinePlayers) return player;
    
    if (player == null) {                         // 2. 离线则查 DB
        player = DatabaseHelper.getPlayerByUid(id);
    }
    return player;
}
```

→ 双查找：在线 Map 优先，离线 fallback 到 DB。
→ "查看好友资料"功能（好友离线也能看）用 `allowOfflinePlayers=true`。

---

## 7. shutdown 流程

`onServerShutdown` 在 JVM 关闭时触发：
```java
public void onServerShutdown() {
    ServerStopEvent event = new ServerStopEvent(ServerEvent.Type.GAME, OffsetDateTime.now());
    event.call();
    
    // 1. 关闭所有玩家 session
    List<Player> list = new ArrayList<>(this.getPlayers().values());
    for (Player player : list) {
        player.getSession().close();   // ★ 触发 onLogout → save
    }
    
    // 2. 保存所有 World
    getWorlds().forEach(World::save);
}
```

### 7.1 关服顺序

```
[JVM SIGTERM]
   ↓
[Grasscutter.onShutdown]
   ↓
   pluginManager.disablePlugins
   
[GameServer.onServerShutdown]
   ↓
   ServerStopEvent (插件钩子)
   ↓
   每个 player.session.close
     ↓ 触发 handleClose (notes/29)
     ↓ player.onLogout (notes/30)
     ↓ save() + teamManager.saveAvatars + friendsList.save
   ↓
   每个 World.save
     ↓ saveSubQuestGroup 等
```

→ **关服时间**：通常 5-15 秒（取决于在线玩家数 + Mongo 写入速度）。

### 7.2 玩家最终 save 时机

```
[Player onLogout]
   - this.save()   // 保存 Player 主文档
   - teamManager.saveAvatars()
   - friendsList.save()
```

→ **每个玩家关服时都会触发 save** —— 防止数据丢。

→ 但**世界中 entity 状态不保存**（怪物 HP、位置等）—— 仅 SceneGroupInstance 持久化。

---

## 8. 三种 RunMode

```java
public enum ServerRunMode {
    HYBRID,         // ★ 默认: HTTP + Game 同进程
    DISPATCH_ONLY,  // 只跑 HTTP (用于分布式)
    GAME_ONLY       // 只跑 Game (依赖远程 dispatch)
}
```

### 8.1 启动分支

```java
var runMode = Grasscutter.getRunMode();
if (runMode == ServerRunMode.HYBRID) {
    httpServer.start();   // 启动 HTTP (dispatch)
    gameServer.start();   // 启动 KCP (game)
} else if (runMode == ServerRunMode.DISPATCH_ONLY) {
    httpServer.start();   // 仅 HTTP
} else if (runMode == ServerRunMode.GAME_ONLY) {
    gameServer.start();   // 仅 KCP
}
```

### 8.2 分布式部署

```
[Dispatch 服务器 (DISPATCH_ONLY)]
   - HTTP/HTTPS 443
   - 单独 MongoDB (账户)
   ↓ 配置 regions:
[Game 服务器 1 (GAME_ONLY)]
   - KCP 22102
   - 共享游戏 MongoDB
[Game 服务器 2 (GAME_ONLY)]
   - KCP 22103
   - 共享游戏 MongoDB
```

→ **一个 dispatch + 多个 game** 是典型部署。

---

## 9. ServerTaskScheduler：延迟任务

```java
public class ServerTaskScheduler {
    private final List<ServerTask> tasks = new CopyOnWriteArrayList<>();
    private final Map<Integer, AsyncServerTask> asyncTasks = ...;
    
    public void runTasks() {
        // 每 tick 调用
        // 1. 跑到期的 delayed tasks
        // 2. 跑 repeating tasks (按 interval)
    }
    
    public void scheduleDelayedTask(Runnable task, int delayTicks);
    public void scheduleDelayedRepeatingTask(Runnable task, int initialDelay, int interval);
    public void scheduleAsyncTask(Runnable task);
}
```

### 9.1 使用场景

```java
// notes/43 Quest.checkQuestAlreadyFulfilled
Grasscutter.getGameServer().getScheduler().scheduleDelayedTask(() -> {
    val shouldFinish = questSystem.initialCheckContent(...);
    if (shouldFinish) quest.finish(false);
}, 1);   // 延迟 1 tick

// notes/44 Timer 事件
Grasscutter.getGameServer().getScheduler().scheduleDelayedTask(() -> {
    callEvent(new ScriptArgs(groupID, EVENT_TIMER_EVENT));
}, (int)(time * 60));
```

→ "**N tick 后做某事**" 都走这里。

---

## 10. ServerTickEvent / ServerStartEvent / ServerStopEvent

```java
ServerStartEvent  // ★ 服务器启动后触发
ServerStopEvent   // ★ 服务器关闭前触发
ServerTickEvent   // ★ 每 tick 触发 (含 tickStart / tickEnd 时间)
```

### 10.1 插件订阅

```java
// 假想插件
@Override
public void onEnable() {
    EventBus.subscribe(ServerTickEvent.class, this::onTick);
}

private void onTick(ServerTickEvent event) {
    long tickDuration = Duration.between(event.getTickStart(), event.getTickEnd()).toMillis();
    if (tickDuration > 500) {
        logger.warn("Slow tick: " + tickDuration + "ms");
    }
}
```

→ 插件可监控 tick 性能、玩家行为统计、外挂检测等。

---

## 11. 完整服务器生命周期图

```
[JVM 启动]
   ↓
[Grasscutter 类静态块]
   logback / mongoLogger.OFF / loadConfig / loadLanguage / startupCheck
   ↓
[main() 阶段 2: 启动序列]
   1. Crypto.loadKeys
   2. StartupArguments.parse
   3. CommandMap
   4. updateDayOfWeek
   5. ★ ResourceLoader.loadAll  (15-30 秒)
   6-7. Tools.* (开发工具)
   8. ★ DatabaseManager.initialize (1-3 秒)
   9. Authentication / Permission
   10. HttpServer
   11. GameServer (14 GameSystem)
   12. PluginManager
   13. 9 个 HTTP router
   ↓
[main() 阶段 3: 启动监听]
   if HYBRID: httpServer.start() + gameServer.start()
   pluginManager.enablePlugins()
   addShutdownHook
   ↓
[GameServer.start]
   Timer.scheduleAtFixedRate(onTick, 1000ms)
   ServerStartEvent.call()
   ↓
[运行时主循环 每秒]
   onTick():
     1. worlds.removeIf(World::onTick)   ← 销毁空 World
     2. players.forEach(Player::onTick)
     3. scheduler.runTasks               ← 延迟任务
     4. ServerTickEvent.call             ← 插件钩子
   ↓
[等待客户端]
   KCP 监听 22102
   HTTP 监听 443
   ↓
[客户端连接]
   notes/31 HTTP dispatch
   notes/29 KCP handshake
   notes/43/30 Quest + Player 加载
   ↓
[正常游戏]
   handlePacket × 600+ 种
   各 GameSystem 服务
   ↓
[关服触发 (Ctrl-C / SIGTERM)]
   Runtime ShutdownHook
   ↓
[Grasscutter.onShutdown]
   pluginManager.disablePlugins
   ↓
[GameServer.onServerShutdown]
   ServerStopEvent.call
   每个 player.session.close → onLogout → save
   每个 World.save
   ↓
[JVM 退出]
```

→ **完整生命周期**：启动 18-35 秒 → 运行 N 小时/天 → 关服 5-15 秒。

---

## 12. 关键设计模式

### 12.1 Lazy static singleton

```java
@Getter private static HttpServer httpServer;
@Getter private static GameServer gameServer;
@Getter private static PluginManager pluginManager;
```

→ 全局静态字段 + Getter —— **没用过 Spring/DI 框架**。
→ grasscutter 是**静态单例 + 简单构造器注入**风格。

### 12.2 全局 Reflections 共享

```java
public static final Reflections reflector = new Reflections("emu.grasscutter");
```

→ 一个实例扫描整个包 → 13+ 子系统共享 → 避免重复扫描。

### 12.3 14 GameSystem + GameServer 反向引用

```
GameServer
   ├── questSystem ← .getServer() 反向引用
   ├── dropSystem  ← .getServer()
   ├── ... 14 个
```

→ System 之间不直接互引用 —— **全通过 GameServer 中介**（类似 Player 中介 25 Manager）。

### 12.4 Timer 简单粗暴

```java
Timer + scheduleAtFixedRate + synchronized
```

→ 没用 Akka / Reactor 等复杂框架——**简单胜过完美**。

### 12.5 Hook 模式

```
ShutdownHook        ← JVM SIGTERM
ServerStartEvent    ← 启动后
ServerStopEvent     ← 关服前
ServerTickEvent     ← 每秒
```

→ 让插件可扩展，但服务器核心**不依赖**插件。

---

## 13. 关键收获

1. **main() 11 步启动序列**：Crypto → Args → CommandMap → ResourceLoader → DB → Auth → HttpServer + GameServer → Plugin → RunMode 启动
2. **静态初始化阶段**：logback / mongoLogger / Config / Language / startupCheck **早于 main**
3. **总启动 18-35 秒**：ResourceLoader 15-30s + DB 1-3s + 其他 < 5s
4. **Reflections reflector 全局共享**：13+ 子系统共用 —— 整个 grasscutter 反射模式的根
5. **GameServer 14 GameSystem**：Quest / Dungeon / Drop / Gacha / Shop / Tower / BattlePass / Multiplayer / Expedition / Combine / Inventory / Script / WorldData / Announcement
6. **BaseGameSystem 13 行**：仅持有 GameServer 引用，靠约定
7. **5 个 static initialize**：Energy / Stamina / Cooking × 2 / Combine —— 演化遗留的不一致
8. **KCP 配置**：MTU 1400 / sndwnd 256 / rcvwnd 256 / 30s 超时 / nodelay=true
9. **start() 用 Timer.scheduleAtFixedRate(1000ms)** + synchronized onTick
10. **onTick 4 步**：worlds → players → scheduler → ServerTickEvent
11. **`worlds.removeIf(World::onTick)`** 优雅销毁空 World
12. **getPlayerByUid 双查找**：在线 Map + 离线 DB fallback
13. **shutdown 顺序**：plugins → players (session.close → save) → worlds.save
14. **3 种 RunMode**：HYBRID / DISPATCH_ONLY / GAME_ONLY 支持分布式
15. **ServerTaskScheduler 延迟任务**：scheduleDelayedTask / scheduleDelayedRepeatingTask
16. **3 个 ServerEvent**：Start / Stop / Tick 插件钩子
17. **静态单例风格**：无 Spring/DI，全局 Grasscutter.xxx 访问

---

## 14. 一句话总结

> **GameServer 启动序列 = main() 11 步 (Crypto/Args/Resource/DB/Auth/HTTP/Game/Plugin/Routes/RunMode/Console) + GameServer 构造器 (KCP/14 GameSystem/Scheduler) + start (Timer 1000ms) + onTick (worlds/players/scheduler/event) + shutdown (plugins/sessions/worlds.save); Reflections reflector 是 13+ 子系统的全局反射根; 总启动 18-35 秒主要在 ResourceLoader; 14 个 GameSystem 通过 BaseGameSystem 13 行基类 + GameServer 中介模式协作.**
> 
> **设计哲学: 静态单例 + 简单 Timer + 全局反射 + 14 系统中介——没用 Spring/Akka/DI 框架, 一切手工挡, 但通过 ServerEvent (Start/Stop/Tick) 钩子保留扩展性. 这是"够用即可"的实用主义服务器设计.**

---

**前置笔记**：
- notes/29 网络层 - KCP 监听细节
- notes/30 持久化 - DatabaseManager.initialize
- notes/31 Dispatch HTTP - 9 个 router 注册
- notes/35 Scene/World 容器 - World.onTick / Scene.onTick 实际逻辑
- notes/43 Quest 引擎 - QuestSystem 是 14 之一
- notes/44 Lua 引擎 - ScriptSystem 是 14 之一
- notes/45 资源加载 - ResourceLoader.loadAll 是关键步

**关联文件**：
- `Grasscutter.java`(324) - main() + 全局静态
- `GameServer.java`(294) - 14 GameSystem + onTick
- `BaseGameSystem.java`(13) - 极简基类
- `ServerTaskScheduler.java`(148) - 延迟任务
- `server/event/internal/ServerStartEvent.java`
- `server/event/internal/ServerStopEvent.java`
- `server/event/game/ServerTickEvent.java`
- 14 个 GameSystem 类: QuestSystem(174) / DropSystem(112) / ScriptSystem / ... 

**研究的源代码**: 618 行 Grasscutter + GameServer 核心 + 14 GameSystem 子类引用。
