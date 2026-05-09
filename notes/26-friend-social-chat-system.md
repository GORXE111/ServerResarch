# 26 · Friend / Social / Chat 系统 · 社交基础设施

社交系统是游戏的"周边设施"——不是核心循环但缺了用户黏性会下降。整个 Friend + Chat 共 **647 行代码**（远小于 Quest 系统的 2500 行）——但有几处绝妙的工程决定。

> 核心代码：`game/friends/`（FriendsList 253 + Friendship 73 + PlayerProfile 101 = 427 行）+ `game/chat/`（ChatSystem 203 + Handler 17 = 220 行）

---

## 1. 整体架构

```
FriendsList (per Player, 内存对象)
  ├── friends: Int2ObjectMap<Friendship>          确认的好友
  └── pendingFriends: Int2ObjectMap<Friendship>   待审核的请求

Friendship (双向, 持久化到 MongoDB)
  ├── ownerId          这条记录的拥有者
  ├── friendId         对方 uid
  ├── askerId          谁先发起的
  ├── isFriend         true=确认 / false=pending
  └── friendProfile: PlayerProfile

PlayerProfile (好友看到的简化档案)
  ├── uid / nickname / level / signature
  ├── nameCardId / avatarId
  └── lastActiveTime

ChatSystem (单例, 内存历史)
  └── history: Map<senderUid, Map<partnerUid, List<ChatInfo>>>
      内存存储, 登出即清
```

---

## 2. 双向 Friendship 设计（最有意思的决定）

### 2.1 一段关系 = 两条记录

```java
// FriendsList.sendFriendRequest
public synchronized void sendFriendRequest(int targetUid) {
    Player target = ...;
    
    // ★ 双向创建 Friendship
    Friendship myFriendship = new Friendship(getPlayer(), target, getPlayer());
    Friendship theirFriendship = new Friendship(target, getPlayer(), getPlayer());
    
    // 各自存自己视角的关系
    this.addPendingFriend(myFriendship);
    if (target.isOnline() && target.getFriendsList().hasLoaded()) {
        target.getFriendsList().addPendingFriend(theirFriendship);
        target.sendPacket(new PacketAskAddFriendNotify(theirFriendship));
    }
    
    myFriendship.save();
    theirFriendship.save();   // ★ DB 里两条记录
}
```

### 2.2 为什么不用单条记录？

**单条记录的问题**：
- 多线程同时访问需要锁（A 和 B 同时改"我们的关系"）
- 查询"我的好友"要扫整个 Friendship 表（找 ownerA=me 或 ownerB=me）
- 离线对方修改时复杂（如何 lock 离线 B 的状态？）

**双向 Friendship 的优势**：
- **每个 Friendship 只属于 owner**——只 owner 能改
- **查询直接按 ownerId 索引**——`SELECT * WHERE ownerId = me`
- **离线对方的 Friendship 可以被修改**（DB 层面写）
- **删除好友只删自己这一条 + 通知对方删自己那条**

```java
public synchronized void deleteFriend(int targetUid) {
    Friendship myFriendship = this.getFriendById(targetUid);
    this.getFriends().remove(targetUid);
    myFriendship.delete();   // 我的视角删除
    
    Friendship theirFriendship = null;
    Player friend = myFriendship.getFriendProfile().getPlayer();
    if (friend != null) {
        // 对方在线: 直接改对方的 FriendsList
        theirFriendship = friend.getFriendsList().getFriendById(this.getPlayer().getUid());
        if (theirFriendship != null) {
            friend.getFriendsList().getFriends().remove(theirFriendship.getFriendId());
            theirFriendship.delete();
            friend.sendPacket(new PacketDeleteFriendNotify(theirFriendship.getFriendId()));
        }
    } else {
        // 对方离线: 直接 DB 删
        theirFriendship = DatabaseHelper.getReverseFriendship(myFriendship);
        if (theirFriendship != null) theirFriendship.delete();
    }
}
```

→ **冗余存储换简洁逻辑**——这是数据库设计的经典权衡。比起"双方关系一条记录"的范式化，**反范式的双向冗余**让代码简单 5 倍。

---

## 3. 好友请求的状态机

```
┌──────────────────────────────────────────────────────────────┐
│  Player A 点 "添加好友 B"                                     │
│      ↓                                                         │
│  sendFriendRequest(B.uid)                                     │
│      ↓ 创建 myFriendship (A 视角, isFriend=false)              │
│      ↓ 创建 theirFriendship (B 视角, isFriend=false)           │
│      ↓ A.pendingFriends.put(B.uid, myFriendship)              │
│      ↓ B.pendingFriends.put(A.uid, theirFriendship)           │
│      ↓ 双方各自 save()                                          │
│      ↓                                                         │
│  B.sendPacket(AskAddFriendNotify) ← B 客户端弹窗"A 想加你"     │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  Player B 同意 / 拒绝                                          │
│      ↓                                                         │
│  handleFriendRequest(A.uid, ACCEPT/REFUSE)                     │
│      ↓                                                         │
│  反作弊: 必须不是 asker 自己处理（A 不能自己确认 A→B 请求）    │
│      ↓                                                         │
│  if ACCEPT:                                                    │
│    myFriendship.isFriend = true                                │
│    theirFriendship.isFriend = true                             │
│    pendingFriends 移除 → friends                               │
│    双方 save                                                    │
│  else:                                                          │
│    myFriendship.delete()                                        │
│    theirFriendship.delete()                                     │
└──────────────────────────────────────────────────────────────┘
```

### 反作弊：asker 不能自己处理请求

```java
// 必须不是 asker 自己处理
if (myFriendship.getAskerId() == this.getPlayer().getUid()) {
    return;
}
```

→ A 发送好友请求后，**A 不能自己确认**——必须等 B 主动操作。这是防客户端伪造"已通过"的简单保险。

### 离线场景的 reverse friendship

```java
// 对方离线时
theirFriendship = DatabaseHelper.getReverseFriendship(myFriendship);
```

→ 对方不在线，直接走 DB 查询/修改。**不依赖对方进程在内存**——异步友好。

---

## 4. PlayerProfile：好友看到的简化档案

```java
public class PlayerProfile {
    private int uid;
    private String nickname;
    private int playerLevel;
    private int worldLevel;
    private long lastActiveTime;
    private String signature;
    private int nameCardId;
    private int avatarId;
    private boolean isOnline;
    ...
}
```

→ 好友列表里**不需要看完整 Player 数据**——只看名片信息。**PlayerProfile 是 Player 的"对外公开视图"**——这是 GraphQL 思想的体现：暴露什么取决于场景。

```java
// 在线状态自动更新
if (target.isOnline()) {
    theirFriendship.setFriendProfile(player);   // 拿在线 player 的实时数据
} else {
    // 否则用 DB 里持久化的旧 profile
}
```

→ **在线时取实时数据，离线时用持久化快照**——好友列表不会因为对方离线就显示"未知"。

---

## 5. Chat 系统：内存历史 + 兼命令入口

### 5.1 内存历史结构

```java
// ChatSystem.history
private final Map<Integer, Map<Integer, List<ChatInfo>>> history = new HashMap<>();
//          uid → Map<partnerId → List<message>>
```

→ **三层 Map**：玩家 → 聊天对象 → 消息列表。

```java
// 登出清空（不持久化）
public void clearHistoryOnLogout(Player player) {
    this.history.remove(player.getUid());
}
```

**关键决定**：Chat 历史**不存 DB**——会话级。每次重新登录就空。

**为什么不持久化？**
- 数据量大（消息数 × 玩家数 × 时间）
- 隐私问题（聊天日志运营有合规压力）
- 性能（每次发消息都 IO）

→ **故意设计成"会话内"**——消息丢失是可接受的，毕竟不是核心系统。

### 5.2 命令入口（绝妙的复用）

```java
private boolean tryInvokeCommand(Player sender, Player target, String rawMessage) {
    if (!RE_PREFIXES.matcher(rawMessage.substring(0, 1)).matches())
        return false;
    for (String line : rawMessage.substring(1).split("\n[/!]"))
        CommandMap.getInstance().invoke(sender, target, line);
    return true;
}
```

**前缀 `/` 或 `!` = 命令**：
- `/give 100 102`（给玩家 100 个冒险阅历）
- `/teleport 1234`（传送到坐标 1234）
- `!help`（查命令）

**Chat 兼做 GM 命令入口**——客户端不知道命令，**只是把所有输入发到 server**，server 看到 `/` 前缀就尝试解析。**复用现有 packet 通道**——没单独搞命令面板。

```java
// sendPrivateMessage
public void sendPrivateMessage(Player player, int targetUid, String message) {
    // 创建消息 + 发给自己
    var packet = new PacketPrivateChatNotify(player.getUid(), targetUid, message);
    player.sendPacket(packet);
    putInHistory(player.getUid(), targetUid, packet.getChatInfo());
    
    // ★ 检查是否是命令
    boolean isCommand = tryInvokeCommand(player, target, message);
    
    // ★ 命令不发给对方（避免暴露 GM 操作）
    if ((target != null) && (!isCommand)) {
        target.sendPacket(packet);
        putInHistory(targetUid, player.getUid(), packet.getChatInfo());
    }
}
```

→ **命令不发给聊天对方**——只在 sender 自己看到，对方看不到。**精妙的隐藏 GM 操作**。

### 5.3 系统欢迎消息（SERVER_CONSOLE_UID）

```java
public static final int SERVER_CONSOLE_UID = 1;   // 系统消息发送者

private void sendServerWelcomeMessages(Player player) {
    if (joinOptions.welcomeEmotes != null) {
        sendPrivateMessageFromServer(player.getUid(), 
            joinOptions.welcomeEmotes[randomRange(0, len-1)]);   // 随机欢迎表情
    }
    if (joinOptions.welcomeMessage != null) {
        sendPrivateMessageFromServer(player.getUid(), joinOptions.welcomeMessage);
    }
}
```

→ **服务器是"特殊玩家 uid=1"**——发系统通知就像普通玩家发私聊。**复用私聊系统而非另起广播**——简单。

---

## 6. 三种聊天通道

```java
public void sendPrivateMessage(Player player, int targetUid, String message)
    // 私聊: 发给特定 uid

public void sendTeamMessage(Player player, int channel, String message)
    // 队伍聊天: world.broadcastPacket
    // 同房间所有玩家收到 (notes/18 multiplayer)

public void sendPrivateMessageFromServer(int targetUid, String message)
    // 系统消息: server console 发送
```

→ **3 个 method 解决所有聊天场景**：私聊、队聊、系统通知。**没有公共大厅频道**——这种类型游戏本来就不是 MMO 大厅模型。

---

## 7. Chat Emoji（表情包收藏）

```
HandlerGetChatEmojiCollectionReq    获取收藏列表
HandlerSetChatEmojiCollectionReq    更新收藏列表
```

每个玩家有一组"常用表情"（5-9 个 emoteId）：
- 私聊菜单里显示这些表情
- 玩家可自定义选择
- 通过 `Player` 实体的 `chatEmojiCollection: List<Integer>` 字段持久化

→ **收藏功能不需要复杂逻辑**——简单的 list set/get。这是为什么这个 handler 极简。

---

## 8. 完整流程示例：A 加 B 好友 → 私聊 → 删除

```
[A 客户端] 输入 B 的 uid 1001
A → AskAddFriendReq(1001)
   ↓
A.FriendsList.sendFriendRequest(1001):
   - target = playerByUid(1001) ✓
   - 双向 Friendship 创建
   - A.pendingFriends.put(1001) + save
   - B.pendingFriends.put(A.uid) + save
   - B.sendPacket(AskAddFriendNotify(A 的 profile))
   ↓
[B 客户端] 弹窗 "A 想加你为好友"

[B 接受]
B → DealAddFriendReq(A.uid, ACCEPT)
   ↓
B.FriendsList.handleFriendRequest(A.uid, ACCEPT):
   - 反作弊: askerId 不是 B ✓
   - myFriendship.isFriend = true
   - theirFriendship.isFriend = true (在 A 侧)
   - pendingFriends → friends
   - 双方 save
   ↓
A 在线 → A.FriendsList 同步更新
A.sendPacket(DealAddFriendRsp(B.uid, ACCEPT))
   ↓
[双方好友列表显示对方 profile]

[A 私聊 B "嗨"]
A → PrivateChatReq(targetUid=1001, message="嗨")
   ↓
ChatSystem.sendPrivateMessage(A, 1001, "嗨"):
   - 创建 PacketPrivateChatNotify(A.uid, 1001, "嗨")
   - A.sendPacket(packet)
   - putInHistory(A.uid, 1001, message)
   - tryInvokeCommand: "嗨" 不以 / ! 开头, 跳过
   - target 在线 → B.sendPacket(packet)
   - putInHistory(1001, A.uid, message)
   ↓
[B 客户端] 收到 A 的消息

[A 删除 B]
A → DeleteFriendReq(1001)
   ↓
A.FriendsList.deleteFriend(1001):
   - A.friends.remove(1001) + myFriendship.delete()
   - target B 在线:
     - B.friends.remove(A.uid) + theirFriendship.delete()
     - B.sendPacket(DeleteFriendNotify(A.uid))
   - 离线时走 DatabaseHelper.getReverseFriendship + delete
```

---

## 9. 关键设计经验

### 9.1 双向冗余存储（反范式）

```java
// 一段关系 → 两条 Friendship 记录
A.Friendship[B] + B.Friendship[A]
```

**优点**：
- 锁简化：只 owner 能改自己的 Friendship
- 查询快：按 ownerId 索引，**O(log N) 查所有好友**
- 离线友好：DB 直接改对方记录

**代价**：
- 双倍存储（小代价，每条记录 < 1 KB）
- 一致性风险（极小，只在创建/删除时双写）

→ **典型的"用空间换简洁性"**。社交关系的查询频率远高于修改频率，**读优化合理**。

### 9.2 PlayerProfile 是公开视图

`Player` 实体几百字段，但好友只关心其中 ~10 个。**单独抽 PlayerProfile** 类避免序列化整个 Player。

→ **数据隐私 + 网络效率**双赢。

### 9.3 Chat 历史会话级（不持久化）

```java
public void clearHistoryOnLogout(Player player) {
    this.history.remove(player.getUid());
}
```

**有意识的工程决定**：
- 数据量太大（消息无限增长）
- 隐私合规风险（聊天日志监管）
- 不影响游戏循环（消息丢失可接受）

→ **不是所有数据都要持久化**——按重要性分级。

### 9.4 Chat 兼命令入口（精妙复用）

```
/give 100 102   ← Chat 输入框输入命令
   ↓ 走 PrivateChatReq packet
   ↓ ChatSystem 检测 / 前缀
   ↓ CommandMap.invoke
   ↓ 执行 GM 命令
```

**好处**：
- 不需要单独的命令面板 UI
- 不需要新 packet 类型
- GM 命令可在私聊/队聊/聊天框任何地方触发

**精妙处理**：命令不发给聊天对方（避免暴露 GM 身份）。

### 9.5 系统消息复用 SERVER_CONSOLE_UID

把"server"当作 uid=1 的特殊玩家。**不需要"广播 channel"**——直接私聊系统消息。

→ **简化模型**：用现有抽象表达新概念。

---

## 10. 反作弊点

```java
// 1. asker 不能自己处理请求
if (myFriendship.getAskerId() == this.getPlayer().getUid()) return;

// 2. 不能加自己
if (target == this.getPlayer()) return;

// 3. 不能重复加
if (this.getPendingFriends().containsKey(targetUid) || 
    this.getFriends().containsKey(targetUid)) return;

// 4. 删除好友检查 friendship 存在
Friendship myFriendship = this.getFriendById(targetUid);
if (myFriendship == null) return;

// 5. 命令权限检查 (CommandMap 内部)
// 不是 GM 玩家用 / 命令会被拒绝
```

→ 简单系统的简单反作弊——**前置 sanity check**。

---

## 11. 与之前系统的连接

| 系统 | 连接 |
|---|---|
| **MP (notes/18)** | `world.broadcastPacket(PlayerChatNotify)` 队伍聊天复用 World 广播 |
| **HomeWorld (notes/23)** | `setFriendEnterHomeOption` 是 Friend handler 之一 |
| **Player (notes/01)** | PlayerProfile 是 Player 的对外简化视图 |
| **Command 系统** | Chat 是命令入口（输入 `/cmd` 触发）|

→ Friend 系统是**社交基础设施**，被多个系统引用但不引用别人——是**底层依赖**。

---

## 12. 给做社交系统开发者的提炼

1. **双向冗余存储好友关系**——读优化，锁简化，离线友好
2. **PlayerProfile 公开视图**——数据隐私 + 网络效率
3. **Chat 历史会话级足够**——不是所有数据都要持久化
4. **Chat 兼命令入口**——复用现有抽象，不另起 UI
5. **命令不发给聊天对方**——隐藏 GM 操作
6. **系统消息用特殊 uid**——简化广播模型
7. **欢迎消息随机化**——`welcomeEmotes[random]` 增加新鲜感
8. **asker 不能自己确认**——简单反作弊
9. **离线对方走 DB**——不依赖进程内存
10. **删除好友双向通知**——在线推 packet，离线 DB 改

---

## 13. 数据规模感

* 平均每玩家好友数：~30-50（上限 60）
* 待审核请求：通常 < 10 个
* 私聊会话：每会话 < 100 条消息（且会话级）
* Chat emoji 收藏：5-9 个

代码规模：
- `FriendsList.java`：253 行（最大）
- `Friendship.java`：73 行
- `PlayerProfile.java`：101 行
- `ChatSystem.java`：203 行
- `ChatSystemHandler.java`：17 行
- 加上 11 个 Handler：~50 行/个 × 11 = 550 行
- 总核心：**~647 行 + handlers ≈ 1200 行** = 整个社交基础设施

---

## 14. 26 篇笔记的"系统全景图"（最终版）

```
[流程型] Quest → Talk → Codex
              ↓
[战斗] Combat / Ability → Dungeon → Scene Script
              ↓
[经济] Inventory + 8 ItemType + ActionReason → Reward → Mail
              ↓                              ↓
              ↓                          Crafting (Combine/Cook/Compound/Forge/Decompose)
              ↓
[养成] Avatar Leveling (7 层属性叠加) → Gacha (4 层保底)
              ↓
[长期] BattlePass + Activity (插件式)
              ↓
[社交] Multiplayer (实时) → HomeWorld (异步 UGC) → Friend / Chat
              ↓
[全部]  服务器架构: 注解+反射+异步池 模式 (出现 7+ 次)
```

→ **15 大系统，~30 个 Manager，~25,000 行 Java 代码**——一套完整的服务端架构。

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/friends/FriendsList.java`（253 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/friends/Friendship.java`（73 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/friends/PlayerProfile.java`（101 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/chat/ChatSystem.java`（203 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/chat/ChatSystemHandler.java`（接口）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/Handler*Friend*.java`（5 个）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/Handler*Chat*.java`（4 个）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/command/CommandMap.java`（命令系统）
