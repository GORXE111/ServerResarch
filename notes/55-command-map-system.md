# CommandMap / Command 系统深度剖析

> 第 55 篇：跨多篇笔记引用但从未真正打开的"嵌入式命令引擎" —— **40 个 GM 命令** + 328 行 CommandMap + 第 16 次"注解+反射"模式 + Chat 桥接 + 双模式 (console / in-game) + 4 种 TargetRequirement。

---

## 0. 为什么这一篇重要

前 54 篇里 Command 系统反复出现但 runtime 没专门挖：
- notes/46 GameServer：`commandMap = new CommandMap(true)` 在 main 中创建
- notes/26 Chat/Friend：Chat 系统兼命令入口
- notes/47 Plugin/Event：ReceiveCommandFeedbackEvent
- notes/48 副本：`CutsceneCommand` 例

但**40 个命令怎么注册？@UID 目标怎么解析？permission 检查何时跑？threading 命令怎么并发？**——这一篇统一回答。

---

## 1. Command 系统全图

```
┌─────────────────────────────────────────────────────────────┐
│  @Command 注解 (28 行)                                        │
│  - label / aliases / usage                                    │
│  - permission / permissionTargeted                            │
│  - targetRequirement (4 种)                                   │
│  - threading                                                  │
└────────────────────────┬────────────────────────────────────┘
                         │ 标注
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  CommandHandler 接口 (89 行)                                  │
│  - execute(sender, targetPlayer, args)                        │
│  - 默认方法: getUsageString / sendUsageMessage / getLabel    │
│  - 静态工具: sendMessage / sendTranslatedMessage              │
└────────────────────────┬────────────────────────────────────┘
                         │ 实现
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  40 个 CommandHandler 子类 (commands/)                        │
│  - GiveCommand / TeleportCommand / KillAllCommand            │
│  - SetStatsCommand / SetSceneTagCommand / ...                │
└────────────────────────┬────────────────────────────────────┘
                         │ 反射注册
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  CommandMap (328 行)                                          │
│  - commands Map (label → handler)                             │
│  - aliases Map (alias → handler)                              │
│  - annotations Map                                            │
│  - targetPlayerIds (per playerId)                             │
│  - invoke() 6 步处理                                          │
│  - scan() 反射扫描                                            │
└─────────────────────────────────────────────────────────────┘
```

→ **40 个命令文件 + 4 个核心 = 完整命令引擎**。

---

## 2. @Command 注解：8 个字段

```java
@Retention(RetentionPolicy.RUNTIME)
public @interface Command {
    String label() default "";              // 主名 (如 "give")
    String[] aliases() default {};           // 别名 (如 {"g", "item"})
    String[] usage() default {""};           // 帮助文本
    String permission() default "";          // 执行者权限
    String permissionTargeted() default "";  // 对目标的权限
    
    TargetRequirement targetRequirement() default TargetRequirement.ONLINE;
    boolean threading() default false;       // 是否异步执行
    
    public enum TargetRequirement {
        NONE,       // 无目标
        OFFLINE,    // 必须离线
        PLAYER,     // 在线/离线都可
        ONLINE      // 必须在线
    }
}
```

### 2.1 使用示例

```java
@Command(label = "give",
         aliases = {"g", "item"},
         usage = {"<itemId|itemName> [amount]"},
         permission = "player.give",
         permissionTargeted = "player.give.others")
public final class GiveCommand implements CommandHandler {
    @Override
    public void execute(Player sender, Player targetPlayer, List<String> args) {
        // ...
    }
}
```

### 2.2 双权限模式

| 字段 | 检查对象 |
|---|---|
| `permission` | **执行者**是否能跑这命令 |
| `permissionTargeted` | **目标玩家**是否能被影响 |

→ 例：
- 执行者 A 跑 `/give @B mora 1000`
- 检查 A 是否有 `player.give` (能用 give)
- 检查 A 能否对 B 用 (`player.give.others`)

→ **双重权限**——既限制功能又限制范围。

### 2.3 TargetRequirement 4 种

```
NONE     → 不需要目标 (如 /help)
OFFLINE  → 仅离线目标 (如 /ban offlineUser)
PLAYER   → 在线/离线都可 (如 /reset)
ONLINE   → 必须在线 (如 /give)
```

→ 在 `invoke` 时**根据这个检查并报错**。

---

## 3. CommandHandler 接口（89 行）

```java
public interface CommandHandler {
    // ★ 静态工具: 发送消息
    static void sendMessage(Player player, String message) {
        ReceiveCommandFeedbackEvent event = new ReceiveCommandFeedbackEvent(player, message);
        event.call();
        if (event.isCanceled()) return;
        
        if (player == null) {
            Grasscutter.getLogger().info(event.getMessage());   // console 输出
        } else {
            player.dropMessage(event.getMessage().replace("\n\t", "\n\n"));   // 玩家消息
        }
    }
    
    static void sendTranslatedMessage(Player player, String messageKey, Object... args) {
        sendMessage(player, translate(player, messageKey, args));
    }
    
    // ★ 默认方法: 帮助文本
    default String getUsageString(Player player, String... args) { ... }
    default void sendUsageMessage(Player player, String... args) { ... }
    
    // ★ 默认方法: 元信息访问器
    default String getLabel() { ... }
    default String getDescriptionKey() { ... }
    default String getDescriptionString(Player player) { ... }
    
    // ★ 核心: 子类实现
    default void execute(Player sender, Player targetPlayer, List<String> args) { }
}
```

### 3.1 ReceiveCommandFeedbackEvent 钩子（notes/47）

```java
ReceiveCommandFeedbackEvent event = new ReceiveCommandFeedbackEvent(player, message);
event.call();
if (event.isCanceled()) return;
```

→ **插件可拦截命令反馈** —— 比如反作弊插件取消"作弊命令的成功消息"。

### 3.2 双输出模式

```java
if (player == null) {
    Grasscutter.getLogger().info(event.getMessage());   // ★ console 输出走 logger
} else {
    player.dropMessage(event.getMessage().replace("\n\t", "\n\n"));   // ★ 玩家走 chat
}
```

→ 同一个 `sendMessage(player, msg)` API:
- player = null → 输出到服务器 console
- player != null → 通过聊天发给玩家

→ **统一接口适配两种执行环境**。

### 3.3 Translation 集成

```java
static void sendTranslatedMessage(Player player, String messageKey, Object... args) {
    sendMessage(player, translate(player, messageKey, args));
}
```

→ 命令消息**自动按玩家语言翻译**（notes/11 TextMap）。

---

## 4. CommandMap.scan()：反射注册（第 16 次模式）

```java
private void scan() {
    Set<Class<?>> classes = Grasscutter.reflector.getTypesAnnotatedWith(Command.class);
    
    classes.forEach(annotated -> {
        Command cmdData = annotated.getAnnotation(Command.class);
        Object object = annotated.getDeclaredConstructor().newInstance();
        
        if (object instanceof CommandHandler) {
            this.registerCommand(cmdData.label(), (CommandHandler) object);
        }
    });
}
```

→ **第 16 次"注解+反射+自动注册"模式**（参见 memory/project_grasscutter_pattern）：
- 扫描所有 @Command 注解的类
- 反射实例化
- 按 label 注册到 commands Map
- 按 aliases 注册到 aliases Map

→ 加新命令：**写一个 class + @Command 注解** = 零代码改动 CommandMap。

### 4.1 启动时机

`Grasscutter.java:102`（notes/46）：
```java
commandMap = new CommandMap(true);   // true = 触发 scan
```

→ main 阶段 3 (commandMap 创建) **早于** Resource/DB 加载——命令系统**最早就绪**。

---

## 5. 40 个命令清单（覆盖 GM 全部操作）

```
AccountCommand       — 账号管理 (create/delete)
AnnounceCommand      — 广播公告
BanCommand           — 封禁
ClearCommand         — 清屏 / 清物品
CoopCommand          — 联机
CutsceneCommand      — 触发剧情 (notes/42)
EnterDungeonCommand  — 进副本
EntityCommand        — 生成实体
GiveCommand          — 给物品
GroupCommand         — 场景组管理
HealCommand          — 治疗
HelpCommand          — 帮助
KickCommand          — 踢人
KillAllCommand       — 杀所有怪
KillCharacterCommand — 杀玩家
LanguageCommand      — 切语言
...等共 40 个
```

→ 覆盖**几乎所有 GM 管理操作**。

### 5.1 GiveCommand 示例

```java
@Command(label = "give",
         aliases = {"g", "item"},
         usage = {"<itemId|itemName> [amount]"},
         permission = "player.give",
         permissionTargeted = "player.give.others")
public final class GiveCommand implements CommandHandler {
    @Override
    public void execute(Player sender, Player targetPlayer, List<String> args) {
        // 解析 itemId
        // 解析 amount
        // targetPlayer.getInventory().addItem(item, ActionReason.Gm);
    }
}
```

→ 标准模式：注解描述元数据 + execute 实现逻辑。

---

## 6. invoke()：6 步处理流程（核心 100 行）

```java
public void invoke(Player player, Player targetPlayer, String rawMessage) {
    // === Step 1: 日志 ===
    if (SERVER.logCommands) {
        Grasscutter.getLogger().info(
            "Command used by [" + (player == null ? "server console" : ...) + "]: " + rawMessage);
    }
    
    // === Step 2: 解析消息 ===
    rawMessage = rawMessage.trim();
    String[] split = rawMessage.split(" ");
    String label = split[0].toLowerCase();
    List<String> args = new ArrayList<>(Arrays.asList(split).subList(1, split.length));
    String playerId = (player == null) ? consoleId : player.getAccountId();
    
    // === Step 3: 特殊命令 @UID / target ===
    if (label.startsWith("@")) {
        this.setPlayerTarget(playerId, player, label.substring(1));
        return;
    } else if (label.equalsIgnoreCase("target")) {
        if (args.size() > 0) {
            this.setPlayerTarget(playerId, player, args.get(0).replace("@", ""));
        } else {
            this.setPlayerTarget(playerId, player, "");
        }
        return;
    }
    
    // === Step 4: 找 handler ===
    CommandHandler handler = this.getHandler(label);
    if (handler == null) {
        CommandHandler.sendTranslatedMessage(player, "commands.generic.unknown_command", label);
        return;
    }
    Command annotation = this.annotations.get(label);
    
    // === Step 5: 解析目标玩家 ===
    try {
        targetPlayer = getTargetPlayer(playerId, player, targetPlayer, args);
    } catch (IllegalArgumentException e) {
        return;
    }
    
    // === Step 6a: 权限检查 ===
    if (!Grasscutter.getPermissionHandler().checkPermission(
        player, targetPlayer, annotation.permission(), annotation.permissionTargeted())) {
        return;
    }
    
    // === Step 6b: 目标要求检查 ===
    Command.TargetRequirement targetRequirement = annotation.targetRequirement();
    if (targetRequirement != Command.TargetRequirement.NONE) {
        if (targetPlayer == null) {
            handler.sendUsageMessage(player);
            CommandHandler.sendTranslatedMessage(player, "commands.execution.need_target");
            return;
        }
        if (targetRequirement == ONLINE && !targetPlayer.isOnline()) {
            CommandHandler.sendTranslatedMessage(player, "commands.execution.need_target_online");
            return;
        }
        if (targetRequirement == OFFLINE && targetPlayer.isOnline()) {
            CommandHandler.sendTranslatedMessage(player, "commands.execution.need_target_offline");
            return;
        }
    }
    
    // === Step 7: 执行 (同步或异步) ===
    Runnable runnable = () -> handler.execute(player, targetPlayer, args);
    if (annotation.threading()) {
        new Thread(runnable).start();   // ★ 新线程异步
    } else {
        runnable.run();                  // 同步
    }
}
```

→ **7 步处理**：日志 → 解析 → 特殊处理 → 找 handler → 解析目标 → 双重检查 → 执行。

---

## 7. getTargetPlayer：3 级目标优先级

```java
private Player getTargetPlayer(String playerId, Player player, Player targetPlayer, List<String> args) {
    // === Priority 1: @UID 参数 ===
    for (int i = 0; i < args.size(); i++) {
        String arg = args.get(i);
        if (arg.startsWith("@")) {
            arg = args.remove(i).substring(1);
            if (arg.equals("")) {
                return null;   // ★ /command @ → 显式无目标
            }
            int uid = getUidFromString(arg);
            if (uid == INVALID_UID) {
                CommandHandler.sendTranslatedMessage(player, "commands.generic.invalid.uid");
                throw new IllegalArgumentException();
            }
            return Grasscutter.getGameServer().getPlayerByUid(uid, true);
        }
    }
    
    // === Priority 2: 显式 targetPlayer 参数 ===
    if (targetPlayer != null) return targetPlayer;
    
    // === Priority 3: targetPlayerIds 持久化目标 (set via /target @UID) ===
    if (targetPlayerIds.containsKey(playerId)) {
        targetPlayer = Grasscutter.getGameServer().getPlayerByUid(targetPlayerIds.getInt(playerId), true);
        if (targetPlayer == null) {
            CommandHandler.sendTranslatedMessage(player, "commands.execution.player_exist_error");
            throw new IllegalArgumentException();
        }
        return targetPlayer;
    }
    
    // === Priority 4: Fallback - 命令执行者自己 ===
    return player;
}
```

### 7.1 3 种目标选择方式

```
方式 1: 临时 @UID 参数
   /give @100015 mora 1000      → 给 UID=100015 1000 摩拉

方式 2: 持久 target
   /target @100015               → 设置默认目标
   /give mora 1000               → 给目标 (100015) 1000 摩拉

方式 3: 显式 null
   /command @                    → 不针对任何人
```

→ **灵活的目标系统**——支持单次和持久两种模式。

### 7.2 setPlayerTarget：持久化目标

```java
private boolean setPlayerTarget(String playerId, Player player, String targetUid) {
    if (targetUid.equals("")) {
        targetPlayerIds.removeInt(playerId);
        return true;
    }
    
    int uid = getUidFromString(targetUid);
    if (uid == INVALID_UID) return false;
    Player targetPlayer = Grasscutter.getGameServer().getPlayerByUid(uid, true);
    if (targetPlayer == null) return false;
    
    targetPlayerIds.put(playerId, uid);
    CommandHandler.sendTranslatedMessage(player, "commands.execution.set_target", ...);
    return true;
}
```

→ `targetPlayerIds: Map<playerId, uid>` —— **每玩家 1 个**默认目标。
→ 服务器重启清空（in-memory only）。

### 7.3 UID 或用户名解析

```java
private static int getUidFromString(String input) {
    try {
        return Integer.parseInt(input);   // 数字直接当 UID
    } catch (NumberFormatException ignored) {
        // 不是数字 → 当用户名
        var account = DatabaseHelper.getAccountByName(input);
        if (account == null) return INVALID_UID;
        var player = DatabaseHelper.getPlayerByAccount(account);
        if (player == null) return INVALID_UID;
        return player.getUid();
    }
}
```

→ `@100015` 或 `@Alice` 都可以——按数字/字符串自动判断。

---

## 8. threading：异步 vs 同步

```java
Runnable runnable = () -> handler.execute(player, targetPlayer, args);
if (annotation.threading()) {
    new Thread(runnable).start();   // ★ 新线程 (per command)
} else {
    runnable.run();                  // 同步
}
```

### 8.1 何时用 threading

```
threading = false (默认):
   - /give / /heal / /kill 等简单操作
   - 微秒级延迟, 同步即可

threading = true:
   - 长时间任务 (备份 / 批量处理)
   - I/O 重的命令
   - 避免阻塞主线程 / Chat 线程
```

→ **per-command 异步选项** —— 单个慢命令不影响其他命令。

### 8.2 极端简单的并发模型

```java
new Thread(runnable).start();
```

→ **每次新建线程** —— 没用线程池。
→ 对 GM 命令足够 —— 不会高频调用。

---

## 9. Chat 桥接（呼应 notes/26）

```bash
$ grep -rn "commandMap.invoke\|CommandMap.getInstance().invoke" --include="*.java"
ChatSystem.java: CommandMap.getInstance().invoke(sender, target, message)
Grasscutter.java:306: CommandMap.getInstance().invoke(null, null, input)
```

### 9.1 双入口

```
入口 1: 服务器 console
   Grasscutter.startConsole:
      input = consoleLineReader.readLine("> ")
      CommandMap.getInstance().invoke(null, null, input)
      ↑ player = null = console

入口 2: 游戏内 chat
   ChatSystem.handle(playerMessage):
      if (message.startsWith("/")):
         CommandMap.getInstance().invoke(player, target, message.substring(1))
         ↑ player = 实际玩家
```

→ Chat 系统**双重用途**：
- 普通聊天 → 转发给好友
- 以 `/` 开头 → 转给 CommandMap

→ 这是 notes/26 提到的"Chat 兼命令入口"的具体实现。

---

## 10. PermissionHandler 集成

```java
if (!Grasscutter.getPermissionHandler().checkPermission(
    player, targetPlayer, annotation.permission(), annotation.permissionTargeted())) {
    return;
}
```

### 10.1 DefaultPermissionHandler

```java
public class DefaultPermissionHandler implements PermissionHandler {
    @Override
    public boolean checkPermission(Player player, Player target, String permission, String permissionTargeted) {
        // grasscutter 默认: 允许所有
        return true;
    }
}
```

→ **grasscutter 默认无权限限制**——所有玩家都能跑所有命令。
→ 私服管理员可**替换 PermissionHandler 实现**实现 RBAC。

### 10.2 真实 mihoyo 服务器对比

```
mihoyo 正服肯定有:
   - GM 账号白名单
   - 命令分级 (一般玩家无 /give)
   - 审计日志
   - 异常报警

grasscutter 私服:
   - 默认开放
   - 服主可自定义
```

→ 反作弊取舍——grasscutter 主要给信任群体用。

---

## 11. 完整时序：玩家 /give 100015 5000 摩拉

```
[玩家在聊天框输入 /give @100015 mora 5000]
   ↓
ChatSystem.onPrivateChatReq / onPlayerChatReq:
   if (message.startsWith("/")):
      CommandMap.getInstance().invoke(player, target=null, "give @100015 mora 5000")
      
CommandMap.invoke:
   Step 1: 日志
      "Command used by [玩家 A]: give @100015 mora 5000"
   
   Step 2: 解析
      label = "give"
      args = ["@100015", "mora", "5000"]
      playerId = player.accountId
   
   Step 3: 不是 @ 或 target → 跳过特殊处理
   
   Step 4: 找 handler
      handler = commands.get("give")  // GiveCommand 实例
      annotation = @Command(label="give", permission="player.give", ...)
   
   Step 5: 解析目标 (getTargetPlayer)
      args[0] = "@100015" → 命中 Priority 1
      args 移除 "@100015"
      uid = 100015
      targetPlayer = Grasscutter.getGameServer().getPlayerByUid(100015)
      args = ["mora", "5000"]  // 已剥离 @UID
   
   Step 6a: 权限检查
      checkPermission(player, target=100015, "player.give", "player.give.others")
      → DefaultPermissionHandler 返回 true
   
   Step 6b: TargetRequirement = ONLINE
      target != null ✓
      target.isOnline() ✓
   
   Step 7: 执行 (threading = false)
      handler.execute(player, target=100015, args=["mora", "5000"]):
         itemId = parseItemId("mora") = 202
         amount = 5000
         target.getInventory().addItem(item, ActionReason.Gm)
         CommandHandler.sendMessage(player, "Gave 5000 mora to ...")
         → ReceiveCommandFeedbackEvent.call
         → player.dropMessage("Gave 5000 mora to 100015")
   
[玩家 A 聊天收到反馈]
[玩家 100015 获得 5000 摩拉 + 右上角 ItemAddHint (notes/38)]
```

→ **完整链路 7 步** + **5 个交互点** (玩家 → Chat → CommandMap → GiveCommand → Inventory → 玩家)。

---

## 12. 设计模式总结

### 12.1 第 16 次"注解+反射"模式

```
@Command(label, aliases, permission, ...)
   ↓ scan 反射注册
40 个 CommandHandler 子类自动加入 CommandMap
```

→ 加新命令零改动 CommandMap。

### 12.2 模板方法 + 默认实现

```java
default String getUsageString(Player player, String... args) { ... }
default void sendUsageMessage(Player player, String... args) { ... }
```

→ 接口提供默认实现 —— **子类只关注 execute()**。

### 12.3 双模式适配

```
console: player=null  → logger 输出
in-game: player≠null  → dropMessage
```

→ 同一 API 适配两种环境。

### 12.4 3 级目标优先级

```
@UID > 显式 target > 持久 target > 自己
```

→ 灵活的目标选择 + Fallback。

### 12.5 反射构造 + 默认构造器约定

```java
Object object = annotated.getDeclaredConstructor().newInstance();
```

→ **要求所有 Command 类有无参构造器** —— 简单约定。

### 12.6 ReceiveCommandFeedbackEvent 钩子

```
插件可拦截反馈消息
```

→ Extensibility 优于完美 —— 允许第三方扩展。

---

## 13. 与其他系统的联动

### 13.1 GiveCommand → Inventory (notes/38)

```java
target.getInventory().addItem(item, ActionReason.Gm);   // ActionReason.Gm = 38
```

### 13.2 CutsceneCommand → CutsceneNotify (notes/42)

```java
target.sendPacket(new PacketCutsceneBeginNotify(cutsceneId));
```

### 13.3 EnterDungeonCommand → DungeonSystem (notes/48)

```java
player.getServer().getDungeonSystem().enterDungeon(target, pointId, dungeonId);
```

### 13.4 HealCommand → 战斗系统 (notes/36)

```java
target.getTeamManager().getActiveTeam().forEach(avatar -> avatar.heal(...));
```

→ **CommandMap 是所有系统的"GM 桥"** —— 通过命令直接调任何系统。

---

## 14. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 自己跑 /give 偷物品 | ✗ DefaultPermission 默认允许（私服）/ ✓ 正服会拒 |
| 篡改 targetPlayerIds | ✗ 服务器内存 |
| Chat 伪造命令 | ✗ 服务器接收时验证 |
| 跑不存在命令 | ✗ getHandler null 拒绝 |
| @UID 不存在玩家 | ✗ INVALID_UID 检查 |

→ Command 系统**反作弊靠 PermissionHandler**——grasscutter 默认开放，私服需自定义。

---

## 15. 关键收获

1. **40 个 Command 实现** + 4 核心文件 (Command/CommandHandler/CommandMap/Helpers) = 完整命令引擎
2. **@Command 注解 8 字段**：label / aliases / usage / permission / permissionTargeted / targetRequirement / threading + 1 enum
3. **双权限模式**：permission (执行者) + permissionTargeted (对目标)
4. **4 种 TargetRequirement**：NONE / OFFLINE / PLAYER / ONLINE
5. **第 16 次"注解+反射"模式**：scan() 反射扫描 @Command + 自动注册
6. **invoke() 7 步**：日志 → 解析 → 特殊命令 → 找 handler → 解析目标 → 权限+目标检查 → 执行
7. **3 级目标优先级**：@UID 参数 > 显式 target > targetPlayerIds (per playerId 持久)
8. **@UID 支持 UID 或用户名**：`@100015` 或 `@Alice` 自动识别
9. **target 显式 null**：`/command @` 不针对任何人
10. **双输出模式**：console (logger) + in-game (dropMessage)
11. **threading 选项**：per-command 异步执行（new Thread）
12. **Chat 桥接**：聊天 `/` 开头 → CommandMap.invoke
13. **PermissionHandler 默认开放**：私服可自定义 RBAC
14. **ReceiveCommandFeedbackEvent 钩子**（notes/47）：插件可拦截命令反馈
15. **Translation 集成**：命令消息自动按玩家语言翻译
16. **CommandMap 是 GM 桥**：通过命令直接调 Inventory/Dungeon/Cutscene 等子系统
17. **default execute 空实现**：子类只需重写 execute
18. **commandMap.invoke 双入口**：console 输入 + Chat `/` 命令
19. **每命令独立线程 (threading)**：单慢命令不影响其他
20. **main 阶段 3 创建**：早于 ResourceLoader/DB —— 命令系统最早就绪

---

## 16. 一句话总结

> **CommandMap = 嵌入式命令引擎 (328 行 + 40 命令实现) + 第 16 次"注解+反射"模式; @Command 注解 8 字段 (label/aliases/usage/双 permission/TargetRequirement/threading) + CommandHandler 默认方法 + 7 步 invoke (日志→解析→特殊→找 handler→3 级目标解析→双重检查→执行) + Chat 双入口 + console/in-game 双输出 + threading per-command + Translation 自动集成 + GM 桥 (调任何系统).**
> 
> **设计哲学: 注解描述元数据 + 接口提供默认实现 + 反射注册 + 3 级目标灵活 + 双权限分级 + 4 种目标要求兜底 + 插件钩子扩展——这是 grasscutter 中"低代码量大功能"的最佳示范.**

---

**前置笔记**：
- notes/26 Chat/Friend - Chat 兼命令入口
- notes/27 架构模式 - 注解反射模式总图
- notes/38 Inventory - GiveCommand 调 addItem
- notes/42 表演 - CutsceneCommand 调 cutscene
- notes/46 GameServer - main 创建 commandMap
- notes/47 Plugin/Event - ReceiveCommandFeedbackEvent
- notes/48 Dungeon - EnterDungeonCommand 调 enterDungeon

**关联文件**：
- `Command.java`(28) - 注解定义
- `CommandHandler.java`(89) - 接口 + 默认方法
- `CommandMap.java`(328) - 主调度
- `CommandHelpers.java` - 工具方法
- `commands/`/*.java × 40 - GM 命令实现
- `PermissionHandler.java` (接口)
- `DefaultPermissionHandler.java` - 默认实现

**研究的源代码**: 445 行 Command 核心 + 40 个 Command 实现。
