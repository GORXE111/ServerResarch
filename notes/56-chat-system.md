# ChatSystem 聊天系统深度剖析

> 第 56 篇：notes/26 讲的是 Friend 系统，Chat 本身从未真正打开 —— **203 行 ChatSystem** 同时是聊天枢纽 + 命令拦截入口 (notes/55 双入口之一) + 系统通知通道 + 会话级内存历史。

---

## 0. 为什么这一篇重要

前 55 篇里 Chat 反复出现但 runtime 没专门挖：
- notes/26 Friend/Social/Chat：讲了"Chat 兼命令入口"概念
- notes/55 CommandMap：Chat 是命令的**双入口之一**
- notes/47 Plugin/Event：ReceiveCommandFeedbackEvent
- notes/40 Player Manager：`messageHandler` 字段

但**Chat 怎么桥接命令？多行命令怎么解析？历史为什么会话级清空？dropMessage 双轨是什么？**——这一篇统一回答。

---

## 1. ChatSystem 全图

```
┌─────────────────────────────────────────────────────────────┐
│  ChatSystemHandler 接口 (17 行)                                │
│  - sendPrivateMessage (text / emote)                          │
│  - sendTeamMessage (text / icon)                              │
│  - sendPrivateMessageFromServer (text / emote)                │
│  - handlePullPrivateChatReq / handlePullRecentChatReq         │
│  - clearHistoryOnLogout                                       │
└────────────────────────┬────────────────────────────────────┘
                         │ 实现
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  ChatSystem (203 行)                                          │
│  - history: Map<uid, Map<partnerId, List<ChatInfo>>>          │
│  - tryInvokeCommand → CommandMap 桥 (notes/55)                │
│  - 双 prefix: / 和 !                                          │
│  - SERVER_CONSOLE_UID 系统消息                                │
│  - welcome messages                                           │
└─────────────────────────────────────────────────────────────┘
                         │ 入口
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  3 个 Handler                                                 │
│  - HandlerPrivateChatReq (私聊)                               │
│  - HandlerPlayerChatReq (队伍/世界聊天)                       │
│  - HandlerPullPrivateChatReq / HandlerPullRecentChatReq       │
└─────────────────────────────────────────────────────────────┘
```

→ **220 行支撑整个聊天系统** —— 极简但身兼数职。

---

## 2. 双 Prefix：`/` 和 `!`

```java
static final String PREFIXES = "[/!]";
static final Pattern RE_PREFIXES = Pattern.compile(PREFIXES);
static final Pattern RE_COMMANDS = Pattern.compile("\n" + PREFIXES);
```

→ **两个命令前缀**：
- `/` —— 标准命令前缀（`/give`）
- `!` —— 替代前缀（`!give`，某些客户端 `/` 被吞掉时用）

→ `[/!]` 正则匹配任一字符。

---

## 3. tryInvokeCommand：Chat → Command 桥

```java
private boolean tryInvokeCommand(Player sender, Player target, String rawMessage) {
    // 1. 首字符必须是 / 或 !
    if (!RE_PREFIXES.matcher(rawMessage.substring(0, 1)).matches())
        return false;
    
    // 2. ★ 按 "\n/" 或 "\n!" 拆分多行命令
    for (String line : rawMessage.substring(1).split("\n[/!]"))
        CommandMap.getInstance().invoke(sender, target, line);
    
    return true;
}
```

### 3.1 多行命令

```
玩家在聊天框输入:
   /give mora 1000
   /heal
   /tp 100 200 300

rawMessage = "/give mora 1000\n/heal\n/tp 100 200 300"
   ↓ substring(1) = "give mora 1000\n/heal\n/tp 100 200 300"
   ↓ split("\n[/!]") = ["give mora 1000", "heal", "tp 100 200 300"]
   ↓ 逐行 CommandMap.invoke
```

→ **一条聊天消息可批量执行多个命令**——粘贴脚本很方便。

### 3.2 返回 boolean 决定是否广播

```java
boolean isCommand = tryInvokeCommand(player, target, message);
if ((target != null) && (!isCommand)) {
    target.sendPacket(packet);   // ★ 非命令才发给对方
}
```

→ **命令不广播给聊天对象** —— `/give` 不会让对方看到 "你输入了 /give"。
→ 命令是"私密执行"，普通聊天才广播。

---

## 4. 三类消息

### 4.1 私聊 (sendPrivateMessage)

```java
public void sendPrivateMessage(Player player, int targetUid, String message) {
    if (message == null || message.length() == 0) return;
    
    Player target = getServer().getPlayerByUid(targetUid);
    if (target == null && targetUid != SERVER_CONSOLE_UID) return;
    
    var packet = new PacketPrivateChatNotify(player.getUid(), targetUid, message);
    
    // 1. 发回给自己 + 记录历史
    player.sendPacket(packet);
    putInHistory(player.getUid(), targetUid, packet.getChatInfo());
    
    // 2. ★ 检查是否命令
    boolean isCommand = tryInvokeCommand(player, target, message);
    
    // 3. 非命令 → 发给对方 + 记录对方历史
    if ((target != null) && (!isCommand)) {
        target.sendPacket(packet);
        putInHistory(targetUid, player.getUid(), packet.getChatInfo());
    }
}
```

### 4.2 队伍/世界聊天 (sendTeamMessage)

```java
public void sendTeamMessage(Player player, int channel, String message) {
    if (message == null || message.length() == 0) return;
    
    // ★ 命令直接执行, 不广播
    if (tryInvokeCommand(player, null, message)) {
        return;
    }
    
    // 广播给整个 World
    player.getWorld().broadcastPacket(new PacketPlayerChatNotify(player, channel, message));
}
```

→ **队伍聊天广播给整个 World** (notes/35)——联机时所有人看到。
→ 命令在队伍频道也能用（`target=null`，针对自己）。

### 4.3 系统消息 (sendPrivateMessageFromServer)

```java
public void sendPrivateMessageFromServer(int targetUid, String message) {
    if (message == null || message.length() == 0) return;
    
    Player target = getServer().getPlayerByUid(targetUid);
    if (target == null) return;
    
    // ★ 发送者 = SERVER_CONSOLE_UID
    var packet = new PacketPrivateChatNotify(GameConstants.SERVER_CONSOLE_UID, targetUid, message);
    putInHistory(targetUid, GameConstants.SERVER_CONSOLE_UID, packet.getChatInfo());
    
    target.sendPacket(packet);
}
```

→ **服务器作为"特殊玩家"发消息** —— `SERVER_CONSOLE_UID` 是保留 UID。
→ 客户端显示为"系统/Server"的私聊。

---

## 5. 内存历史：会话级（不持久化）

```java
// uid → partnerId → [messages]
private final Map<Integer, Map<Integer, List<ChatInfo>>> history = new HashMap<>();

private void putInHistory(int uid, int partnerId, ChatInfo info) {
    this.history.computeIfAbsent(uid, x -> new HashMap<>())
                .computeIfAbsent(partnerId, x -> new ArrayList<>())
                .add(info);
}

public void clearHistoryOnLogout(Player player) {
    this.history.remove(player.getUid());   // ★ 登出即清
}
```

### 5.1 双向存储

```
玩家 A 发给 B "你好":
   putInHistory(A, B, info)   // A 的视角: 与 B 的对话
   putInHistory(B, A, info)   // B 的视角: 与 A 的对话
```

→ **双份记录** —— 每方各自有完整对话副本。
→ 与 Friend 系统的"双向 Friendship 反范式"一脉相承（notes/26）。

### 5.2 会话级（登出清空）

```java
public void clearHistoryOnLogout(Player player) {
    this.history.remove(player.getUid());
}
```

→ `Player.onLogout` 调用（notes/30）：
```java
this.getServer().getChatSystem().clearHistoryOnLogout(this);
```

→ **聊天历史不持久化** —— 登出即丢。
→ 这是 grasscutter 的设计取舍：私服无需保留聊天（隐私 + 简化）。
→ 米哈游正服肯定持久化（敏感词审查 + 客服）。

### 5.3 历史拉取

```java
public void handlePullPrivateChatReq(Player player, int partnerId) {
    var chatHistory = this.history
        .computeIfAbsent(player.getUid(), x -> new HashMap<>())
        .computeIfAbsent(partnerId, x -> new ArrayList<>());
    player.sendPacket(new PacketPullPrivateChatRsp(chatHistory));
}
```

→ 玩家点开与某人的对话 → 拉取该 partner 的全部历史（本会话）。

---

## 6. Welcome Messages：首次聊天触发

```java
public void handlePullRecentChatReq(Player player) {
    // ★ 没有与 SERVER 的历史 → 发欢迎消息
    if (!this.history.computeIfAbsent(player.getUid(), x -> new HashMap<>())
            .containsKey(GameConstants.SERVER_CONSOLE_UID)) {
        this.sendServerWelcomeMessages(player);
    }
    
    // 返回最近 3 条系统消息
    int historyLength = this.history.get(player.getUid()).get(SERVER_CONSOLE_UID).size();
    var messages = this.history.get(player.getUid()).get(SERVER_CONSOLE_UID)
        .subList(Math.max(historyLength - 3, 0), historyLength);
    player.sendPacket(new PacketPullRecentChatRsp(messages));
}

private void sendServerWelcomeMessages(Player player) {
    var joinOptions = GAME_INFO.joinOptions;
    
    if (joinOptions.welcomeEmotes != null && joinOptions.welcomeEmotes.length > 0) {
        this.sendPrivateMessageFromServer(player.getUid(), 
            joinOptions.welcomeEmotes[Utils.randomRange(0, joinOptions.welcomeEmotes.length - 1)]);
    }
    
    if (joinOptions.welcomeMessage != null && joinOptions.welcomeMessage.length() > 0) {
        this.sendPrivateMessageFromServer(player.getUid(), joinOptions.welcomeMessage);
    }
}
```

→ **玩家首次打开聊天界面**（pull recent chat）→ 触发欢迎消息：
- 随机一个 welcome emote
- 配置的 welcome message

→ 配置在 `GAME_INFO.joinOptions`（config.json）。

### 6.1 "懒触发"设计

→ 不是登录时发欢迎 —— 是**首次打开聊天界面时**发。
→ 节省：从不打开聊天的玩家不产生 history entry。

---

## 7. dropMessage：双轨消息

`Player.java:985`：
```java
public void dropMessage(Object message) {
    if (this.messageHandler != null) {
        this.messageHandler.append(message.toString());   // ★ 轨道 1: MessageHandler
        return;
    }
    
    this.getServer().getChatSystem().sendPrivateMessageFromServer(getUid(), message.toString());
    //                                ↑ 轨道 2: 系统私聊
}
```

### 7.1 两条轨道

```
messageHandler != null:
   → append 到 MessageHandler (用于命令批量收集输出)
   → 例: GM 跑命令, 输出汇总到一个 buffer 再统一返回

messageHandler == null:
   → 走系统私聊 (chat UI 显示)
```

### 7.2 MessageHandler 用途

`Player.messageHandler` (notes/40)：
- 默认 null → 命令反馈直接进聊天框
- 设置后 → 命令反馈汇总到 buffer

→ 用例：Web 后台/API 调用命令需要**捕获输出**而非发聊天。
→ CommandHandler.sendMessage (notes/55) → player.dropMessage → 这个双轨。

---

## 8. Emote / Icon 消息

```java
public void sendPrivateMessage(Player player, int targetUid, int emote) {
    Player target = getServer().getPlayerByUid(targetUid);
    if (target == null && targetUid != SERVER_CONSOLE_UID) return;
    
    var packet = new PacketPrivateChatNotify(player.getUid(), target.getUid(), emote);
    player.sendPacket(packet);
    putInHistory(player.getUid(), targetUid, packet.getChatInfo());
    
    if (target != null) {
        target.sendPacket(packet);
        putInHistory(targetUid, player.getUid(), packet.getChatInfo());
    }
}
```

→ **emote 消息**（聊天表情）走单独重载——`int emote` 而非 `String message`。
→ 不走 tryInvokeCommand（表情不可能是命令）。
→ PacketPrivateChatNotify 有 text/emote 两种构造。

---

## 9. Handler 入口

### 9.1 HandlerPrivateChatReq（私聊）

```java
public class HandlerPrivateChatReq extends TypedPacketHandler<PrivateChatReq> {
    @Override
    public void handle(GameSession session, byte[] header, PrivateChatReq req) {
        val content = req.getContent();
        if (content instanceof PrivateChatReq.Content.Text text) {
            session.getServer().getChatSystem().sendPrivateMessage(
                session.getPlayer(), req.getTargetUid(), text.getValue());
        } else if (content instanceof PrivateChatReq.Content.Icon icon) {
            session.getServer().getChatSystem().sendPrivateMessage(
                session.getPlayer(), req.getTargetUid(), icon.getValue());
        }
    }
}
```

→ **Java 17 sealed pattern matching**：`content instanceof X x` 直接解构。
→ 区分 Text vs Icon 内容。

### 9.2 HandlerPlayerChatReq（队伍/世界）

```java
session.getServer().getChatSystem().sendTeamMessage(
    session.getPlayer(), req.getChannelId(), text.getValue());
```

→ channel = 队伍/世界频道。

---

## 10. 完整时序：玩家 A 私聊 B 发命令

```
[玩家 A 在与 B 的私聊框输入 "/give mora 1000"]
   ↓ PrivateChatReq { targetUid: B, content: Text("/give mora 1000") }
HandlerPrivateChatReq:
   ChatSystem.sendPrivateMessage(A, B_uid, "/give mora 1000")
   
ChatSystem.sendPrivateMessage:
   1. message 非空 ✓
   2. target = getPlayerByUid(B)
   3. packet = PacketPrivateChatNotify(A_uid, B_uid, "/give mora 1000")
   4. A.sendPacket(packet)  ← A 自己看到自己发的
   5. putInHistory(A, B, info)  ← A 视角历史
   6. ★ tryInvokeCommand(A, B, "/give mora 1000"):
      - 首字符 "/" 匹配 ✓
      - split → ["give mora 1000"]
      - CommandMap.invoke(A, B, "give mora 1000")  (notes/55)
        → GiveCommand.execute(A, target=B, ["mora", "1000"])
        → B.getInventory().addItem(mora, 1000, ActionReason.Gm)
        → CommandHandler.sendMessage(A, "Gave 1000 mora")
          → A.dropMessage("Gave 1000 mora")
          → messageHandler null → sendPrivateMessageFromServer(A, "Gave 1000 mora")
          → A 收到系统私聊 "Gave 1000 mora"
      - return true (isCommand)
   7. isCommand == true → 不发给 B
      (B 看不到 "A 说: /give mora 1000")

[结果]
   A 看到: 自己发的 "/give mora 1000" + 系统消息 "Gave 1000 mora"
   B 看到: 背包 +1000 摩拉 + 右上角 ItemAddHint (notes/38)
   B 看不到 A 输入的命令文本
```

→ **命令通过私聊执行但对方无感知** —— 巧妙的设计。

---

## 11. 普通私聊时序（非命令）

```
[A 私聊 B "你好"]
ChatSystem.sendPrivateMessage:
   1-5. (同上, 发回 A + 记录)
   6. tryInvokeCommand: 首字符 "你" 不匹配 [/!] → return false
   7. isCommand == false + target != null:
      - B.sendPacket(packet)  ← B 收到 "A: 你好"
      - putInHistory(B, A, info)  ← B 视角历史
```

→ 普通消息**双向广播 + 双份历史**。

---

## 12. Chat 在 Command 系统中的角色（呼应 notes/55）

```
CommandMap 双入口 (notes/55):
   入口 1: console → Grasscutter.startConsole → invoke(null, null, input)
   入口 2: chat → ChatSystem.tryInvokeCommand → invoke(player, target, line)
              ↑ 本篇

Chat → Command 桥接点:
   - sendPrivateMessage → tryInvokeCommand (私聊命令, target = 对方)
   - sendTeamMessage → tryInvokeCommand (队伍命令, target = null)
```

→ **私聊命令可以带 target**（对方），队伍命令 target = null（自己）。
→ 这是 notes/55 "3 级目标优先级" 中 Priority 2 (显式 target) 的来源。

---

## 13. 设计模式总结

### 13.1 命令拦截器模式

```
sendXxxMessage → tryInvokeCommand → 是命令则拦截不广播
```

→ 聊天即命令通道——一个消息流，两种语义。

### 13.2 双向反范式历史

```
putInHistory(A, B) + putInHistory(B, A)
```

→ 与 Friend (notes/26) 一致——空间换查询简单。

### 13.3 会话级内存（无持久化）

```
history Map (in-memory) + clearHistoryOnLogout
```

→ 私服取舍：简化 + 隐私 > 历史保留。

### 13.4 系统作为伪玩家

```
SERVER_CONSOLE_UID 作为特殊发送者
```

→ 系统消息复用私聊通道——无需单独的"系统通知"协议。

### 13.5 双轨消息输出

```
messageHandler != null → buffer
messageHandler == null → chat
```

→ 同一 dropMessage API 适配"交互"和"批量捕获"。

### 13.6 懒触发欢迎

```
首次 pull recent chat 才发 welcome
```

→ 不打开聊天的玩家零开销。

---

## 14. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 伪造他人私聊 | ✗ player 来自 session |
| 刷屏 | ✗ 客户端发包频率受限 (无服务器端 rate limit) |
| 私聊命令偷物品 | ✗ 命令权限 (notes/55) |
| 篡改 history | ✗ 服务器内存 |
| 假装系统消息 | ✗ SERVER_CONSOLE_UID 服务器控制 |

→ Chat 反作弊**一般** —— 无敏感词过滤、无 rate limit（私服可接受）。

---

## 15. 关键收获

1. **ChatSystem 203 行 + ChatSystemHandler 17 行** = 聊天系统全部
2. **双 prefix `/` 和 `!`**：`[/!]` 正则匹配命令前缀
3. **tryInvokeCommand = Chat → Command 桥**：notes/55 双入口之一
4. **多行命令**：`split("\n[/!]")` 一条消息批量执行
5. **命令不广播给聊天对象**：isCommand=true → 不发给 target
6. **3 类消息**：private (text/emote) / team (text/icon) / server
7. **队伍聊天广播整个 World**（notes/35）
8. **history 三层 Map**：uid → partnerId → [ChatInfo]
9. **双向反范式存储**：putInHistory(A,B) + putInHistory(B,A)
10. **会话级内存**：clearHistoryOnLogout 登出即清，不持久化
11. **SERVER_CONSOLE_UID 系统伪玩家**：系统消息复用私聊通道
12. **welcome messages 懒触发**：首次 pull recent chat 才发（emote + message）
13. **dropMessage 双轨**：messageHandler buffer vs 系统私聊
14. **emote/icon 单独重载**：不走 tryInvokeCommand
15. **Java 17 sealed pattern matching**：`content instanceof X x` 解构
16. **私聊命令带 target，队伍命令 target=null**：notes/55 目标优先级来源
17. **私聊命令对方无感知**：巧妙的"隐式执行"
18. **GAME_INFO.joinOptions 配置 welcome**：config.json 可定制
19. **历史拉取分 2 种**：handlePullPrivateChatReq (与某人) / handlePullRecentChatReq (系统最近 3 条)
20. **反作弊一般**：无敏感词/rate limit（私服取舍）

---

## 16. 一句话总结

> **ChatSystem = 聊天枢纽 + 命令拦截入口 + 系统通知通道三位一体 (203 行); 双 prefix (/!）+ tryInvokeCommand 桥接 CommandMap (notes/55 双入口之一) + 多行命令批量 + 命令对方无感知; 三层 Map 双向反范式历史 + 会话级清空 (不持久化); SERVER_CONSOLE_UID 伪玩家发系统消息 + 懒触发 welcome; dropMessage 双轨 (buffer/chat) 适配交互与捕获.**
> 
> **设计哲学: 一个消息流两种语义 (聊天 vs 命令) + 命令隐式执行不广播 + 内存历史登出即清 (隐私+简化) + 系统复用私聊通道——这是 grasscutter 中"通讯枢纽身兼数职"的紧凑设计.**

---

**前置笔记**：
- notes/26 Friend/Social/Chat - 双向反范式存储 (Friendship)
- notes/30 持久化 - Player.onLogout 调 clearHistoryOnLogout
- notes/35 Scene/World - 队伍聊天 World.broadcastPacket
- notes/38 Inventory - 命令 give → addItem
- notes/40 Player Manager - messageHandler 字段 / dropMessage
- notes/47 Plugin/Event - ReceiveCommandFeedbackEvent
- notes/55 CommandMap - Chat 是命令双入口之一

**关联文件**：
- `ChatSystem.java`(203) - 主实现
- `ChatSystemHandler.java`(17) - 接口
- `HandlerPrivateChatReq.java`(22) - 私聊入口
- `HandlerPlayerChatReq.java` - 队伍/世界入口
- `Player.java:985` - dropMessage 双轨
- `PacketPrivateChatNotify` / `PacketPlayerChatNotify` - 出口包
- `GameConstants.SERVER_CONSOLE_UID` - 系统伪玩家 UID

**研究的源代码**: 220 行 Chat 核心 + 3 个 Handler + dropMessage。
