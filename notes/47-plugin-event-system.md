# Plugin / Event 系统深度剖析

> 第 47 篇：跨 46 篇笔记反复出现的 `event.call()` 终于解剖 —— 27 个 Event 类、Cancellable 双轨、3 级 HandlerPriority、PluginManager 310 行的"插件骨架"。

---

## 0. 为什么这一篇重要

前 46 篇里 Event 出现 50+ 次：
- notes/29 `SendPacketEvent.call()` 让 plugin 拦截发包
- notes/32 `EntityDamageEvent.call()` 反作弊伤害钩子
- notes/34 `PlayerMoveEvent.call()` 位置篡改检测
- notes/35 `PlayerTeleportEvent.call()` 传送拦截
- notes/30 `PlayerCreationEvent.call()` 新玩家钩子
- notes/46 `ServerTickEvent.call()` 每秒钩子

但**EventBus 到底怎么实现？插件怎么订阅？Cancellable 怎么工作？**——这一篇填上最后一块拼图。

---

## 1. Event 系统全图

```
┌────────────────────────────────────────────────────────────────┐
│  抽象基类 (4 个核心)                                              │
│  - Event (33 行)         — 所有事件基类                            │
│  - Cancellable (9 行)    — 可取消标记接口                          │
│  - EventHandler<T> (83 行) — 监听器配置 (Builder)                  │
│  - HandlerPriority (19 行) — 3 级 (HIGH/NORMAL/LOW)               │
└────────────────────────────────────────────────────────────────┘
                                  │
                                  ↓
┌────────────────────────────────────────────────────────────────┐
│  27 个具体 Event 子类 (5 个目录)                                   │
│  ├── dispatch/   (2)  QueryAllRegions / QueryCurrentRegion        │
│  ├── entity/     (3)  EntityDamage / EntityDeath / EntityMove     │
│  ├── game/       (4)  PlayerCreation / SendPacket / ReceivePacket │
│  │                    / ServerTick / ReceiveCommandFeedback        │
│  ├── internal/   (3)  ServerStart / ServerStop / ServerLog        │
│  ├── player/     (7)  PlayerJoin / Quit / Move / Teleport         │
│  │                    / UseFood / ReceiveMail / TeamDeath          │
│  └── types/      (1)  EntityEvent (共享基类)                       │
└────────────────────────────────────────────────────────────────┘
                                  │
                                  ↓
┌────────────────────────────────────────────────────────────────┐
│  PluginManager (310 行) — 调度中心                                 │
│  - Map<String, Plugin> plugins                                    │
│  - Map<Plugin, List<EventHandler>> listeners                      │
│  - invokeEvent → 按 priority 分发 → 调 callback                    │
└────────────────────────────────────────────────────────────────┘
                                  │
                                  ↓
┌────────────────────────────────────────────────────────────────┐
│  Plugin API                                                       │
│  - Plugin (基类, onLoad/onEnable/onDisable)                       │
│  - ServerHook / PlayerHook (便利包装)                              │
│  - Item (插件 API 工具)                                            │
│  - PluginConfig (plugin.json 反序列化)                             │
│  - PluginIdentifier (元信息)                                       │
└────────────────────────────────────────────────────────────────┘
```

→ **整个 Event/Plugin 系统 ~700 行**——比 Player.java 还小，但极其重要。

---

## 2. Event 抽象基类（33 行核心）

`Event.java`：
```java
public abstract class Event {
    private boolean cancelled = false;
    
    public boolean isCanceled() {
        return this.cancelled;
    }
    
    public void cancel() {
        if (this instanceof Cancellable)   // ★ 关键检查
            this.cancelled = true;
    }
    
    public void call() {
        Grasscutter.getPluginManager().invokeEvent(this);
    }
}
```

### 2.1 Cancellable 双轨设计

```java
public interface Cancellable {
    void cancel();
}
```

→ **不是所有 Event 都能 cancel**。
→ Event 默认有 cancel 方法但**只有实现 Cancellable 的才生效**：

```java
public class SendPacketEvent extends Event implements Cancellable { ... }
//                                          ↑ 可被插件取消发包

public class ServerTickEvent extends Event { ... }
//                                  ↑ 没 Cancellable, cancel 调了也没用
```

**5 个 Cancellable Event**（推断）：
- SendPacketEvent (拦截发包)
- ReceivePacketEvent (拦截处理)
- PlayerTeleportEvent (拦截传送)
- EntityDamageEvent (拦截伤害)
- PlayerMoveEvent / EntityMoveEvent (拦截移动)

→ 这些**反作弊钩子**——插件可阻止异常行为。

### 2.2 call() 一行触发

```java
public void call() {
    Grasscutter.getPluginManager().invokeEvent(this);
}
```

→ 任何代码处 `new XxxEvent(...).call()` 就完成事件分发——**API 极简**。

→ 业务代码模式（贯穿全笔记）：
```java
SomeEvent event = new SomeEvent(...);
event.call();
if (event.isCanceled()) return;   // 被插件拦截
// 继续业务
```

---

## 3. HandlerPriority 3 级

```java
public enum HandlerPriority {
    HIGH,    // 第一批调用
    NORMAL,  // 第二批
    LOW;     // 最后
}
```

### 3.1 用途

```
HIGH    → 反作弊插件 (优先检查, 取消异常行为)
NORMAL  → 常规业务插件
LOW     → 日志/统计插件 (最后跑, 拿到最终结果)
```

→ 类似 Spring 的 `@Order` 注解。
→ 注意 ≠ ResourceType.LoadPriority (5 级)——**两套独立**的优先级枚举。

---

## 4. EventHandler<T>：Builder 模式

```java
public final class EventHandler<T extends Event> {
    private final Class<T> eventClass;
    private EventConsumer<T> listener;
    private HandlerPriority priority;
    private boolean handleCanceled;
    
    public EventHandler(Class<T> eventClass) { this.eventClass = eventClass; }
    
    public EventHandler<T> listener(EventConsumer<T> listener) { ... return this; }
    public EventHandler<T> priority(HandlerPriority priority) { ... return this; }
    public EventHandler<T> ignore(boolean ignore) { ... return this; }
    
    public void register(Plugin plugin) {
        Grasscutter.getPluginManager().registerListener(plugin, this);
    }
}
```

### 4.1 链式注册

```java
new EventHandler<>(PlayerJoinEvent.class)
    .listener(event -> {
        Player player = event.getPlayer();
        player.sendMessage("Welcome!");
    })
    .priority(HandlerPriority.NORMAL)
    .ignore(false)        // 不忽略 cancel 状态
    .register(this);      // ★ this = Plugin 实例
```

→ **泛型类型安全**：`<T extends Event>` 让 listener 自动推断为 `EventConsumer<PlayerJoinEvent>`。

### 4.2 ignore 字段

```java
public boolean ignoresCanceled() {
    return this.handleCanceled;
}
```

→ **handler 是否处理已 cancel 的 event**：
- `ignore=false` (默认) — 被 cancel 的 event 跳过 handler
- `ignore=true` — 即使 cancel 也处理（如日志插件想知道"被拦截了"）

---

## 5. PluginManager：调度中心（310 行）

### 5.1 字段

```java
public final class PluginManager {
    private final Map<String, Plugin> plugins = new LinkedHashMap<>();
    private final Map<Plugin, List<EventHandler<? extends Event>>> listeners = new LinkedHashMap<>();
}
```

→ **2 个 Map**：插件清单 + 每个插件的监听器列表。

### 5.2 invokeEvent：分批触发

```java
public void invokeEvent(Event event) {
    EnumSet.allOf(HandlerPriority.class)
        .forEach(priority -> this.checkAndFilter(event, priority));
}

private void checkAndFilter(Event event, HandlerPriority priority) {
    this.listeners.values().stream()
        .flatMap(Collection::stream)
        .filter(handler -> handler.handles().isInstance(event))     // 类型匹配
        .filter(handler -> handler.getPriority() == priority)        // 优先级匹配
        .forEach(handler -> this.invokeHandler(event, handler));
}

private <T extends Event> void invokeHandler(Event event, EventHandler<T> handler) {
    if (!event.isCanceled() || (event.isCanceled() && handler.ignoresCanceled())) {
        handler.getCallback().consume((T) event);
    }
}
```

### 5.3 执行流程

```
event.call()
   ↓
PluginManager.invokeEvent(event)
   ↓
按 EnumSet.allOf(HandlerPriority.class) 顺序遍历 (HIGH → NORMAL → LOW)
   ↓
每个优先级:
   ├── 遍历所有插件的所有 handler
   ├── filter: 类型匹配 (handler.handles() isInstance event)
   ├── filter: priority 匹配
   └── 调 handler.callback.consume(event)
      └── 如果 event.canceled 且 handler 不 ignore → 跳过
```

→ **同 priority 内顺序不定** —— 不保证插件 A 早于插件 B。

### 5.4 EnumSet.allOf 顺序

```java
EnumSet.allOf(HandlerPriority.class)
//   ↑ enum 定义顺序: HIGH, NORMAL, LOW
```

→ **按 enum 声明顺序遍历** —— HIGH 先跑。

---

## 6. 27 个 Event 完整分类

### 6.1 dispatch/ 目录（HTTP 阶段）

```java
QueryAllRegionsEvent      // /query_region_list 回复前
QueryCurrentRegionEvent   // /query_cur_region/{name} 回复前
```

→ 插件可改 region 列表 / 改 game IP。notes/31 提到。

### 6.2 entity/ 目录

```java
EntityDamageEvent extends EntityEvent implements Cancellable
   - amount, attackType, attacker, target
   
EntityDeathEvent extends EntityEvent
   - killerId, target (没 Cancellable, 已经死了不能撤)
   
EntityMoveEvent extends EntityEvent implements Cancellable
   - position, rotation, speed, motionState
```

→ notes/32 怪物伤害 + notes/34 玩家移动 都触发。

### 6.3 game/ 目录

```java
PlayerCreationEvent
   - session, playerClass (可改用哪个 Player 子类!)
   
SendPacketEvent extends Event implements Cancellable
   - 服务器发包前
   
ReceivePacketEvent extends Event implements Cancellable
   - 服务器收包后
   
ServerTickEvent
   - tickStart, tickEnd (每秒)
   
ReceiveCommandFeedbackEvent
   - 命令反馈
```

→ notes/29 网络层 + notes/46 tick 都触发。

### 6.4 internal/ 目录

```java
ServerStartEvent
   - type, time (启动后)
   
ServerStopEvent
   - type, time (关服前)
   
ServerLogEvent
   - 日志事件
```

→ 服务器生命周期钩子。

### 6.5 player/ 目录（7 个，最多）

```java
PlayerJoinEvent            // 玩家加入
PlayerQuitEvent            // 玩家退出
PlayerMoveEvent (Cancellable) // 玩家移动
PlayerTeleportEvent (Cancellable) // 传送
PlayerUseFoodEvent (Cancellable) // 使用食物
PlayerReceiveMailEvent     // 收到邮件
PlayerTeamDeathEvent       // 全队死亡
```

→ 玩家全生命周期覆盖。

---

## 7. PluginManager.loadPlugins：jar 动态加载

`PluginManager.java:44-160`：

```java
private void loadPlugins() {
    File pluginsDir = FileUtils.getPluginPath("").toFile();
    File[] files = pluginsDir.listFiles();
    
    List<File> plugins = Arrays.stream(files)
        .filter(file -> file.getName().endsWith(".jar"))
        .toList();
    
    URL[] pluginNames = new URL[plugins.size()];
    plugins.forEach(plugin -> {
        pluginNames[plugins.indexOf(plugin)] = plugin.toURI().toURL();
    });
    
    // ★ 一个 URLClassLoader 加载所有插件
    URLClassLoader classLoader = new URLClassLoader(pluginNames);
    List<PluginData> dependencies = new ArrayList<>();
    
    for (var plugin : plugins) {
        URL url = plugin.toURI().toURL();
        try (URLClassLoader loader = new URLClassLoader(new URL[]{url})) {
            // 1. 读 plugin.json
            URL configFile = loader.findResource("plugin.json");
            InputStreamReader fileReader = new InputStreamReader(configFile.openStream());
            PluginConfig pluginConfig = JsonUtils.loadToClass(fileReader, PluginConfig.class);
            
            // 2. 加载 jar 内所有 .class
            JarFile jarFile = new JarFile(plugin);
            Enumeration<JarEntry> entries = jarFile.entries();
            while (entries.hasMoreElements()) {
                JarEntry entry = entries.nextElement();
                if (entry.isDirectory() || !entry.getName().endsWith(".class")) continue;
                String className = entry.getName().replace(".class", "").replace("/", ".");
                classLoader.loadClass(className);   // ★ 共享 classLoader
            }
            
            // 3. 实例化 mainClass
            Class<?> pluginClass = classLoader.loadClass(pluginConfig.mainClass);
            Plugin pluginInstance = (Plugin) pluginClass.getDeclaredConstructor().newInstance();
            
            // 4. 处理依赖
            if (pluginConfig.loadAfter != null && pluginConfig.loadAfter.length > 0) {
                dependencies.add(new PluginData(...));
                continue;
            }
            
            this.loadPlugin(pluginInstance, ...);
        }
    }
    
    // 5. 解析依赖链 (最多 30 层)
    int depth = 0; final int maxDepth = 30;
    while (!dependencies.isEmpty()) {
        if (depth >= maxDepth) break;
        var pluginData = dependencies.get(0);
        if (!this.plugins.keySet().containsAll(List.of(pluginData.getDependencies()))) {
            depth++;
            continue;
        }
        dependencies.remove(pluginData);
        this.loadPlugin(...);
    }
}
```

### 7.1 共享 URLClassLoader

```java
URLClassLoader classLoader = new URLClassLoader(pluginNames);
```

→ **所有插件共享一个 ClassLoader** —— 这意味着：
- ✓ 插件间可互相调类（A 调 B 的工具方法）
- ✗ 类版本冲突不可避免（两个插件依赖不同版本的 jar）
- ✗ 卸载插件不能真正释放类（类还在 ClassLoader 里）

→ 比 OSGi/Eclipse 简单，但**功能受限**。

### 7.2 依赖解析（loadAfter）

```json
// plugin.json 示例
{
  "mainClass": "com.example.MyPlugin",
  "name": "MyPlugin",
  "version": "1.0",
  "loadAfter": ["BasePlugin", "EconomyPlugin"]
}
```

→ "我必须在 BasePlugin 之后加载"——保证依赖先初始化。

### 7.3 30 层依赖深度

```java
final int maxDepth = 30;
while (!dependencies.isEmpty()) {
    if (depth >= maxDepth) break;
    // ...
}
```

→ **最多 30 层依赖链** —— 防循环依赖死循环。
→ 实战中 30 层完全够用（plugin 一般 < 10）。

---

## 8. Plugin 4 lifecycle

```java
public abstract class Plugin {
    private PluginIdentifier identifier;
    private URLClassLoader classLoader;
    
    // 反射初始化（PluginManager 通过反射调）
    private void initializePlugin(PluginIdentifier id, URLClassLoader cl) {
        this.identifier = id;
        this.classLoader = cl;
    }
    
    public abstract void onLoad();    // ★ 加载时 (一次)
    public abstract void onEnable();  // ★ 启用时
    public abstract void onDisable(); // ★ 禁用时
    
    // 可选: 配置文件读写
    protected void saveConfig(Object config) { ... }
    protected <T> T loadConfig(Class<T> clazz) { ... }
}
```

### 8.1 lifecycle 触发时机

```
[main 启动]
   PluginManager.<init>:
     loadPlugins → plugin.onLoad   ★ 阶段 1
   ...
   pluginManager.enablePlugins → plugin.onEnable  ★ 阶段 2
   
[运行时]
   plugins 工作中
   
[关服]
   pluginManager.disablePlugins → plugin.onDisable ★ 阶段 3
```

→ 类似 Bukkit / Spigot 的 4 阶段（少了 reload）。

---

## 9. 与 grasscutter 自身的协作

### 9.1 grasscutter 内部代码也"调 event.call()"

虽然 PluginManager 给插件用，但 grasscutter **自己的核心代码也调 event.call()**：
- notes/29 `GameSession.send` 内部 `SendPacketEvent.call()`
- notes/32 `GameEntity.damage` 内部 `EntityDamageEvent.call()`
- notes/34 `EntityAvatar.move` 内部 `PlayerMoveEvent.call()`
- notes/35 `World.transferPlayerToScene` 内部 `PlayerTeleportEvent.call()`
- notes/46 `GameServer.onTick` 内部 `ServerTickEvent.call()`

→ **没插件时 event.call() 也照样调** —— 只是没 listener 而已。
→ 这是 grasscutter 的"**event-driven 内核**"——所有可拦截点都通过 Event 暴露。

### 9.2 反作弊钩子点

Cancellable Events 形成**反作弊接口**：
```java
PlayerMoveEvent:    速度过快 → cancel
EntityDamageEvent:  伤害过大 → cancel  
PlayerTeleportEvent: 异常传送 → cancel
SendPacketEvent:    特定 packet → cancel
```

→ **私服管理员可写 anti-cheat 插件**：监听这些事件，按规则取消。
→ grasscutter 默认**无反作弊**，但**留好了接口**。

---

## 10. ServerHook / PlayerHook：便利包装

```java
public final class ServerHook {
    public ServerHook(GameServer gameServer, HttpServer httpServer) {
        this.gameServer = gameServer;
        this.httpServer = httpServer;
        instance = this;
    }
    public GameServer getGameServer() { return this.gameServer; }
    public HttpServer getHttpServer() { return this.httpServer; }
    public static ServerHook getInstance() { return instance; }
}
```

→ `Grasscutter.java:130` 在 main 创建：`new ServerHook(gameServer, httpServer);`

→ 插件用 `ServerHook.getInstance().getGameServer()...` 替代 `Grasscutter.getGameServer()...`——更友好的 API。

---

## 11. 设计模式总结

### 11.1 Observer 模式 + 优先级

```
Subject (Event) → invoke → Observers (EventHandler × N)
                     ↓ 按 priority 分批
```

→ 经典观察者模式 + 3 级优先级。

### 11.2 Builder 模式

```java
new EventHandler<>(Class.class)
    .listener(...)
    .priority(...)
    .ignore(...)
    .register(plugin);
```

→ 链式构造一个完整 handler。

### 11.3 Cancellable 标记接口

```java
class SendPacketEvent extends Event implements Cancellable
```

→ 用 Marker Interface 区分**可取消/不可取消** —— Java 经典做法。

### 11.4 类型擦除 + 泛型 + isInstance

```java
.filter(handler -> handler.handles().isInstance(event))
//                ↑ 运行时类型检查
```

→ Java 泛型类型擦除，运行时**只能 isInstance** —— 妥协方案。

### 11.5 ClassLoader 隔离

```java
URLClassLoader classLoader = new URLClassLoader(pluginNames);
```

→ jar 动态加载——但**共享 ClassLoader** 是 trade-off：兼容性 vs 隔离性。

---

## 12. Plugin 实战例子（虚构）

### 12.1 反作弊插件

```java
public class AntiCheatPlugin extends Plugin {
    private static final float MAX_MOVE_PER_TICK = 50.0f;
    
    @Override
    public void onEnable() {
        // 监听玩家移动
        new EventHandler<>(PlayerMoveEvent.class)
            .priority(HandlerPriority.HIGH)     // 高优先级，先于其他处理
            .listener(event -> {
                Player player = event.getPlayer();
                Position from = event.getFrom();
                Position to = event.getDestination();
                float distance = from.computeDistance(to);
                
                if (distance > MAX_MOVE_PER_TICK) {
                    event.cancel();   // ★ 拦截
                    player.sendMessage("§cYou're moving too fast!");
                    getLogger().warn("Player {} caught speedhacking", player.getUid());
                }
            })
            .register(this);
    }
    
    @Override public void onLoad() {}
    @Override public void onDisable() {}
}
```

### 12.2 统计插件

```java
public class StatsPlugin extends Plugin {
    private long totalDamageDealt = 0;
    
    @Override
    public void onEnable() {
        new EventHandler<>(EntityDamageEvent.class)
            .priority(HandlerPriority.LOW)    // 最后跑，拿到最终伤害
            .ignore(true)                      // 即使 cancel 也统计
            .listener(event -> {
                totalDamageDealt += event.getDamage();
                // 持久化 / 发数据
            })
            .register(this);
    }
}
```

### 12.3 自定义 Region 插件

```java
public class CustomRegionPlugin extends Plugin {
    @Override
    public void onEnable() {
        // 把游戏 IP 改成自己控制的负载均衡器
        new EventHandler<>(QueryCurrentRegionEvent.class)
            .listener(event -> {
                String myCustomIp = "balancer.example.com";
                event.setRegionInfo(rebuildWithIp(myCustomIp));
            })
            .register(this);
    }
}
```

---

## 13. 反作弊 + 可观察性的统一

Event 系统是 grasscutter **最优雅的设计之一**：
- ✓ **反作弊**：5 个 Cancellable Event 形成拦截点
- ✓ **可观察性**：所有 Event 都可被监听，做日志/统计
- ✓ **扩展性**：插件零侵入加新功能
- ✓ **API 简洁**：`event.call()` 一行 + Builder 链式

→ 类似 Bukkit/Spigot 但**更轻量**（700 行 vs 数千行）。

---

## 14. 与 4 套事件总线对比（呼应 notes/41）

| 系统 | 用途 | 数量 | 异步? | 可取消? |
|---|---|---|---|---|
| **Plugin Event (本篇)** | 插件钩子 | 27 类 | 同步 | 部分 (5+ Cancellable) |
| WatcherTriggerType (notes/41) | BattlePass / Achievement | 299 类 | 通过 Manager 异步 | ✗ |
| QuestContent (notes/41) | Quest 进度 | 80+ | queueEvent 异步 | ✗ |
| QuestCond (notes/41) | Quest 条件 | 80+ | queueEvent 异步 | ✗ |
| Lua EventType (notes/41) | 场景脚本 | 30+ | eventExecutor 异步 | ✗ |

→ **5 套并行 Event 系统**！
- Plugin Event = 服务端代码的扩展点
- 其他 4 套 = 游戏内部业务事件

→ 之所以 5 套并存：**它们解决不同问题**，但都叫 "Event"——这是 grasscutter 的命名混乱处。

---

## 15. 反作弊钩子点全图

| Event | 触发位置 | 可取消? | 反作弊用途 |
|---|---|---|---|
| PlayerMoveEvent | EntityAvatar.move | ✓ | 速度过快 / 穿墙 |
| EntityDamageEvent | GameEntity.damage | ✓ | 异常伤害值 |
| EntityMoveEvent | HandlerCombatInvocationsNotify | ✓ | 实体位置篡改 |
| PlayerTeleportEvent | World.transferPlayerToScene | ✓ | 异常传送 |
| SendPacketEvent | GameSession.send | ✓ | 阻止特定 packet 发出 |
| ReceivePacketEvent | GameServerPacketHandler.handle | ✓ | 阻止伪造请求 |
| PlayerUseFoodEvent | InventorySystem.useItem | ✓ | 食物刷新限制 |

→ **7 个 Cancellable 钩子** —— 形成完整的反作弊接口。

→ 但 grasscutter 默认**不附带反作弊插件** —— 私服管理员要自己写。

---

## 16. 关键收获

1. **27 个 Event 类 + 4 个核心基类**：~700 行实现完整事件系统
2. **Cancellable 标记接口双轨**：Event 默认有 cancel 但只有 Cancellable 才生效
3. **HandlerPriority 3 级**：HIGH/NORMAL/LOW —— 不要与 ResourceType.LoadPriority 5 级混淆
4. **Event.call() 一行触发**：`new XxxEvent().call()` —— API 极简
5. **EventHandler<T> Builder 模式**：listener/priority/ignore/register 链式
6. **PluginManager 310 行**：2 个 Map (plugins + listeners) + invokeEvent 分批
7. **invokeEvent 按 priority 顺序**：HIGH → NORMAL → LOW (EnumSet.allOf 顺序)
8. **共享 URLClassLoader**：所有插件一个 ClassLoader → 类共享 + 卸载受限
9. **30 层依赖深度上限**：防止循环依赖死循环
10. **Plugin 4 lifecycle**：initializePlugin (反射) + onLoad + onEnable + onDisable
11. **grasscutter 内核也调 event.call()**：核心代码 = event-driven 内核
12. **7 个 Cancellable 反作弊钩子**：Move/Damage/Teleport/Packet/UseFood/...
13. **5 套并行 Event 系统**：Plugin Event + 4 套游戏事件（共 ~500+ 类型）—— 命名混乱
14. **Achievement 系统未实现**：在 grasscutter 中只有占位枚举（启发本研究）
15. **ServerHook 便利包装**：插件用 `ServerHook.getInstance()` 替代 `Grasscutter.xxx()`

---

## 17. 一句话总结

> **Plugin/Event 系统 = grasscutter 的"扩展骨架" —— 27 个 Event 类 + 4 核心基类 (~700 行) + PluginManager 310 行 + Cancellable 双轨 + HandlerPriority 3 级 + Builder 链式 EventHandler + URLClassLoader 动态加载 + 4 lifecycle。grasscutter 内核也 event-driven (50+ event.call() 调用点), 7 个 Cancellable 形成反作弊接口。**
> 
> **设计哲学: 经典 Observer + Builder + 标记接口, 700 行实现 Bukkit/Spigot 级扩展能力; 反作弊由插件实现 (开源私服默认无 anti-cheat); 内核代码主动调 event.call() 让插件零侵入扩展——这是 grasscutter 中"做事少但留好接口"的最佳实例.**

---

**前置笔记**：
- notes/29 网络层 - SendPacketEvent / ReceivePacketEvent
- notes/30 持久化 - PlayerCreationEvent
- notes/31 Dispatch HTTP - QueryAllRegionsEvent / QueryCurrentRegionEvent
- notes/32 怪物 - EntityDamageEvent / EntityDeathEvent / EntityMoveEvent
- notes/34 EntityAvatar - PlayerMoveEvent
- notes/35 Scene/World - PlayerTeleportEvent
- notes/41 事件总线 4 套并行 - 本篇 = 第 5 套
- notes/46 GameServer - ServerStart/Stop/Tick

**关联文件**：
- `Event.java`(33) - 抽象基类
- `Cancellable.java`(9) - 标记接口
- `EventHandler.java`(83) - Builder
- `HandlerPriority.java`(19) - 3 级枚举
- `PluginManager.java`(310) - 调度中心
- `Plugin.java` - 插件基类
- `ServerHook.java` / `PlayerHook.java` - 便利包装
- `PluginConfig.java` / `PluginIdentifier.java` - 元信息
- `server/event/dispatch/` × 2
- `server/event/entity/` × 3
- `server/event/game/` × 4-5
- `server/event/internal/` × 3
- `server/event/player/` × 7
- `server/event/types/` × 1

**研究的源代码**: 700+ 行 Plugin/Event 系统核心 + 28 个 Event 类。
