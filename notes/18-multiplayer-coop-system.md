# 18 · Multiplayer / Coop 系统 · 联机房间与跨账号同步

原神最复杂的实时互联场景：玩家 A 邀请玩家 B 进入自己的世界，B 进入后能看到 A 的角色、攻击 A 的怪、共同推进任务（部分）。这一整套有多紧凑？**MultiplayerSystem 全部 155 行**。

> 核心代码：`game/systems/MultiplayerSystem.java`（155 行）、`game/world/World.java`（500+ 行）、`game/world/Scene.java`（1400+ 行）、邀请相关 Handler

---

## 1. 三级容器：World > Scene > Player

```
World (房间)              一个 host + 0-3 个 guest = 最多 4 人
  ├── host (Player)        创建房间的人
  ├── peerId 0-3 分配      每人一个 World 内序号
  ├── Map<sceneId, Scene>  房间内活跃场景集合
  │     ├── Scene 3 (蒙德)
  │     │    ├── EntityAvatar (host's character)
  │     │    ├── EntityAvatar (guest1's character)
  │     │    ├── EntityMonster ...
  │     │    └── EntityGadget ...
  │     └── Scene 5 (副本)
  │          └── ...
  └── isMultiplayer = true
```

→ 关键关系：
- **Scene 是 World 的子单元**（World 有多个 Scene，玩家可分布在不同 Scene）
- **World 是房间，scene 是场景**——副本里 + 大世界里 = 不同 scene 但同 world
- `peerId` 是 World 内的"序号"，host 是 0，guest 们 1-3

```java
// World.addPlayer (World.java:147)
public synchronized void addPlayer(Player player) {
    if (player.getWorld() != null) player.getWorld().removePlayer(player);
    
    player.setWorld(this);
    this.getPlayers().add(player);
    player.setPeerId(this.getNextPeerId());                  // ← 分配 peerId
    player.getTeamManager().setEntity(new EntityTeam(player));
    
    // 进入 MP 时自动复制单人队伍配置
    if (this.isMultiplayer()) {
        player.getTeamManager().getMpTeam().copyFrom(
            currentSinglePlayerTeamInfo, maxTeamSize);
        player.getTeamManager().setCurrentCharacterIndex(0);
    }
    
    Scene scene = this.getSceneById(player.getSceneId());
    scene.addPlayer(player);
    
    if (this.getPlayers().size() > 1) {
        this.updatePlayerInfos(player);                       // 给其他玩家更新名单
    }
}
```

---

## 2. 邀请协议（端到端，2 阶段握手）

### 2.1 阶段一：requester 发申请

```java
// HandlerPlayerApplyEnterMpReq.java
public void handle(GameSession session, byte[] header, PlayerApplyEnterMpReq req) {
    session.getServer().getMultiplayerSystem()
        .applyEnterMp(session.getPlayer(), req.getTargetUid());
    session.send(new PacketPlayerApplyEnterMpRsp(req.getTargetUid()));
}

// MultiplayerSystem.applyEnterMp
public void applyEnterMp(Player player, int targetUid) {
    Player target = getServer().getPlayerByUid(targetUid);
    if (target == null) {
        player.sendPacket(...PLAYER_CANNOT_ENTER_MP);    // 目标不在线
        return;
    }
    
    // 反作弊 1: 自己已在 MP 不能再申请
    if (player.getWorld().isMultiplayer()) return;
    
    // 反作弊 2: 已有 pending 请求
    CoopRequest existing = target.getCoopRequests().get(player.getUid());
    if (existing != null && !existing.isExpired()) return;
    
    // 推送给 target
    request = new CoopRequest(player);
    target.getCoopRequests().put(player.getUid(), request);
    target.sendPacket(new PacketPlayerApplyEnterMpNotify(player));   // ← 弹窗"X 想加入"
}
```

### 2.2 阶段二：target 同意/拒绝

```java
// MultiplayerSystem.applyEnterMpReply
public void applyEnterMpReply(Player hostPlayer, int applyUid, boolean isAgreed) {
    CoopRequest request = hostPlayer.getCoopRequests().get(applyUid);
    if (request == null || request.isExpired()) return;
    
    Player requester = request.getRequester();
    hostPlayer.getCoopRequests().remove(applyUid);
    
    // 反作弊：requester 在 reply 期间已加入别的 MP
    if (requester.getWorld().isMultiplayer()) {
        requester.sendPacket(...PLAYER_CANNOT_ENTER_MP);
        return;
    }
    
    // 通知 requester 结果
    requester.sendPacket(new PacketPlayerApplyEnterMpResultNotify(
        hostPlayer, isAgreed, MpEnterResultReason.PLAYER_JUDGE));
    
    if (!isAgreed) return;   // 拒绝就到此为止
    
    // 同意：如果 host 当前是单人世界，转成多人
    if (!hostPlayer.getWorld().isMultiplayer()) {
        World mpWorld = new World(hostPlayer, true);   // ★ 创建新的 MP world
        mpWorld.addPlayer(hostPlayer);                  // host 先进
        hostPlayer.sendPacket(new PacketPlayerEnterSceneNotify(
            hostPlayer, hostPlayer, EnterType.ENTER_SELF, 
            EnterReason.HostFromSingleToMp, ...));
    }
    
    // 同步 requester 位置到 host 当前位置
    requester.getPosition().set(hostPlayer.getPosition());
    requester.getRotation().set(hostPlayer.getRotation());
    requester.setSceneId(hostPlayer.getSceneId());
    
    // 加入 host 的 World
    hostPlayer.getWorld().addPlayer(requester);
    
    // 通知 requester 进入了新 scene
    requester.sendPacket(new PacketPlayerEnterSceneNotify(
        requester, hostPlayer, EnterType.ENTER_OTHER, EnterReason.TeamJoin, ...));
}
```

### 2.3 流程图

```
[Requester]                       [Server]                    [Host]
   │                                 │                           │
   │ PlayerApplyEnterMpReq(host)    │                           │
   │────────────────────────────── ▶│                           │
   │ ◀───── PlayerApplyEnterMpRsp   │                           │
   │                                 │ PlayerApplyEnterMpNotify  │
   │                                 │──────────────────────── ▶│
   │                                 │ (host 弹窗，玩家点同意)   │
   │                                 │ PlayerApplyEnterMpResultReq
   │                                 │◀──────────────────────── │
   │ PlayerApplyEnterMpResultNotify  │                           │
   │◀──────────────────────────────  │                           │
   │                                 │                           │
   │ (server 内部:                                                │
   │   - 如 host 单人 → 创新 MP World, host 加入                  │
   │   - sync requester 位置到 host                              │
   │   - host.world.addPlayer(requester) ← 真正进入)              │
   │                                                              │
   │ PlayerEnterSceneNotify          │                           │
   │   (ENTER_OTHER, TeamJoin)       │                           │
   │◀──────────────────────────────  │                           │
   │                                 │                           │
   │ EnterSceneReadyReq             │                           │
   │────────────────────────────── ▶│                           │
   │ ... (loading + entity sync) ...                             │
   │ EnterSceneDoneReq              │                           │
   │────────────────────────────── ▶│                           │
   │   ▲                             │                           │
   │   │ 所有 entity 推送 + Player 列表推送给两边                  │
```

---

## 3. Team 同步：单人/多人队伍分离

```java
// 进 MP 的瞬间
player.getTeamManager().getMpTeam().copyFrom(
    currentSinglePlayerTeamInfo, maxTeamSize);
player.getTeamManager().setCurrentCharacterIndex(0);
```

**关键设计**：每个玩家有 **3 套队伍配置**：
1. **3 个单人队伍 (TeamA/TeamB/TeamC)**：单人模式下自由切换
2. **1 个多人队伍 (MpTeam)**：进 MP 时复制单人当前 team
3. **临时队伍**：试用角色等

进入 MP 时**复制当前单人队伍**，不直接共用。这样：
- 你在单人玩 ABCD 队伍，进 MP 后还是 ABCD（但分开存储）
- 单人切换队伍不影响 MP 队伍记忆
- 退出 MP 回到单人原队伍

`maxTeamSize` 由 MP 玩家数决定：
- 单人 4 人队伍
- 双人 2 人/人
- 3 人 1 人/人 + 1 个 host 1 人
- 4 人 1 人/人

---

## 4. 离开 / 踢人 / 解散

### 4.1 主动离开（leaveCoop）

```java
public boolean leaveCoop(Player player) {
    if (!player.getWorld().isMultiplayer()) return false;
    
    // 反作弊：所有人 scene 都 loaded 才能走
    for (Player p : player.getWorld().getPlayers()) {
        if (p.getSceneLoadState() != SceneLoadState.LOADED) return false;
    }
    
    // 创建新 single-player world
    World world = new World(player);
    world.addPlayer(player);
    
    player.sendPacket(new PacketPlayerEnterSceneNotify(
        player, EnterType.ENTER_SELF, EnterReason.TeamBack, ...));
    return true;
}
```

→ 创建一个**新的 World 实例**给离开者，原 World 保持不变（host + 其他 guest 还在）。

### 4.2 host 踢人（kickPlayer）

```java
public boolean kickPlayer(Player player, int targetUid) {
    // 反作弊：只有 host 能踢
    if (!player.getWorld().isMultiplayer() || player.getWorld().getHost() != player) 
        return false;
    
    Player victim = player.getServer().getPlayerByUid(targetUid);
    if (victim == null || victim == player) return false;
    if (victim.getSceneLoadState() != SceneLoadState.LOADED) return false;
    
    World world = new World(victim);   // 新单人 world
    world.addPlayer(victim);
    
    victim.sendPacket(new PacketPlayerEnterSceneNotify(
        victim, ENTER_SELF, EnterReason.TeamKick, ...));
    return true;
}
```

→ **只有 host 能踢人**。被踢者得到新单人 world，reason 是 `TeamKick`（客户端可能弹"被踢出房间"提示）。

### 4.3 host 离开 = 解散全房

```java
// World.removePlayer (World.java:206)
if (this.getHost() == player) {
    List<Player> kicked = new ArrayList<>(this.getPlayers());
    for (Player victim : kicked) {
        World world = new World(victim);
        world.addPlayer(victim);
        victim.sendPacket(new PacketPlayerEnterSceneNotify(
            victim, ENTER_SELF, EnterReason.TeamKick, ...));
    }
}
```

→ host 走了 → **遍历所有剩余 guest，每人新建独立 world**。整个房间瞬间解散。

### 4.4 EnterReason 分类（部分枚举）

```
TeamJoin           加入他人世界
TeamBack           离开 MP 回单人
TeamKick           被踢/被解散
HostFromSingleToMp 房主从单人转 MP
TransPoint         传送点
DungeonEnter       进副本
DungeonReplay      副本重打
DungeonQuit        退副本
Lua                Lua 脚本传送
Gm                 GM 命令
ClientTransmit     客户端主动切场景
Revival            复活
```

→ 每种"进/出场景"都标 reason，**客户端按 reason 决定播放什么转场效果**（如 TeamKick 可能弹错误提示，TransPoint 是传送动画）。

---

## 5. 视野同步：scene.broadcastPacket / world.broadcastPacket

进入 MP 后，**任何实体变化都需要让其他玩家看到**。机制：

### 5.1 World 级广播（房间所有人）

```java
// World.broadcastPacket (World.java:371)
public void broadcastPacket(BasePacket packet) {
    for (Player player : this.getPlayers()) {
        player.sendPacket(packet);
    }
}
```

→ 用于"所有 guest 都关心的事件"：
- 队伍信息变化（PlayerTeamUpdateNotify）
- 房主信息（PlayerInfoNotify）
- 全局任务进度（FinishedParentQuestNotify）

### 5.2 Scene 级广播（同一 Scene 的人）

```java
// Scene.broadcastPacket - 给当前 scene 的玩家广播
scene.broadcastPacket(new PacketEntityFightPropUpdateNotify(...));
```

→ 用于"只有同 scene 玩家关心的事件"：
- 实体 HP 变化
- 实体出现/消失（EntityAppearNotify / EntityDisappearNotify）
- 怪物状态变化
- 战斗动作转发

注意：**同一 World 但不同 Scene 的玩家互相不可见**。例如 host 在大世界，guest 在副本——双方各自的实体不互相同步。这是合理的——副本里的怪没必要让大世界玩家看到。

### 5.3 实体可见性的 peerId 过滤

某些实体只属于特定玩家（如 guest 自己的角色 EntityAvatar）：

```java
// 简化逻辑
if (entity.getOwnerPeerId() == player.getPeerId() || entity.getVisionLevel() == GLOBAL) {
    sendPacket(EntityAppearNotify(entity));
}
```

→ peerId 决定"这个实体属于谁的"。客户端可以**只显示自己关心的部分**（如 guest 不需要看到 host 的临时 trial avatar）。

### 5.4 EntityTeam 概念

```java
// World.addPlayer
player.getTeamManager().setEntity(new EntityTeam(player));
```

→ 每个玩家有一个 **EntityTeam 实体**——它是"队伍占位实体"，代表这个玩家在场景里的存在（不是具体角色）。其他玩家通过 EntityTeam 知道"这个玩家在这里"。

`removePlayer` 时：
```java
player.sendPacket(new PacketDelTeamEntityNotify(
    sceneId,
    players.stream().map(p -> p.getTeamManager().getEntity().getId()).toList()
));
```
→ 通知离开的玩家"清除所有其他玩家的 EntityTeam"。

---

## 6. SceneLoadState 状态机（重要的同步握手）

每个玩家在场景加载过程中有 4 个状态：

```java
enum SceneLoadState {
    NONE,       // 未在任何场景
    LOADING,    // 客户端正在加载场景资源
    INIT,       // 资源加载完，等待 PostEnterScene
    LOADED      // 完全就绪
}
```

进 scene 流程：
```
[server] PlayerEnterSceneNotify(scene=3)
[client] 加载场景资源（mesh、贴图、Lua）
[client] EnterSceneReadyReq → server   (sceneLoadState = LOADING)
[server] 推送场景实体（怪物/机关/NPC）
[client] EnterSceneDoneReq → server    (sceneLoadState = INIT)
[server] PostEnterSceneRsp
[client] PostEnterSceneReq → server    (sceneLoadState = LOADED)
```

**为什么状态机重要**：
- `leaveCoop` 检查所有玩家都 LOADED 才能离开（防止半加载状态丢包）
- `kickPlayer` 同样检查
- 战斗事件转发到加载未完成的客户端会让客户端崩

---

## 7. 任务系统的 MP 隔离

回顾 `notes/02` 看到的 `isMpBlock` 字段：

```jsonc
// SubQuest 配表
{
    "subId": 302207,
    "isMpBlock": true,   // 在多人模式下不可用
    ...
}
```

**剧情任务大多 isMpBlock=true**——guest 进入 host 的世界后，host 的剧情对 guest 来说**不可推进**：
- guest 不能触发 host 的剧情对话
- guest 看不到 host 的任务标记
- guest 在大世界各处玩，但剧情节点跳不过去

这是**避免剧情错乱**的设计：每个玩家的剧情进度是独立的，host 的剧情只对 host 自己推进。

但有些任务**必须 MP**（如世界 boss 战）：
- `MAIN_COOP_*` 类型 = 多人协作任务
- `QUEST_CONTENT_MAIN_COOP_ENTER_SAVE_POINT` 是这类任务的 finishCond
- 627 次出现（corpus 里）

---

## 8. 跨账号交互

### 8.1 实时交互 packet（同 world）

| 类型 | packet | 同步对象 |
|---|---|---|
| 移动 | EntityMoveInfo (within CombatInvocations) | 同 scene 玩家看 |
| 攻击 | CombatInvokeEntry | 同 scene 玩家看 |
| 技能 | AbilityInvokeEntry | 同 scene 玩家看 |
| 聊天 | PlayerChatNotify | 同 world 所有玩家 |
| 表情 | EmojiCollectionRsp 等 | 同 scene 玩家 |

### 8.2 异步交互（无需同 world）

| 类型 | 实现 |
|---|---|
| 好友邀请 | `HandlerAskAddFriendReq` |
| 好友列表 | `HandlerGetPlayerFriendListReq` |
| 黑名单 | `HandlerGetPlayerBlacklistReq` |
| 邮件 | Mail 系统 (notes/15)，跨账号也可以发 |
| 尘歌壶访问 | HomeWorld 系统 |

→ **MP 是"实时同 world"，但跨账号交互比 MP 范围更广**——加好友、私聊、互访尘歌壶都不需要在同一房间。

---

## 9. 反作弊点（这个简单系统的所有保险）

```java
// 1. 自己已在 MP 不能申请别人
if (player.getWorld().isMultiplayer()) return;

// 2. 已有 pending 请求不重发
if (existing != null && !existing.isExpired()) return;

// 3. 离开/踢人前所有人 scene 必须 LOADED
if (p.getSceneLoadState() != SceneLoadState.LOADED) return false;

// 4. 只有 host 能踢人
if (player.getWorld().getHost() != player) return false;

// 5. 不能踢自己
if (victim == player) return false;

// 6. CoopRequest 有过期时间，避免长期挂着
if (request.isExpired()) return;
```

→ 简单系统的保险来自**严格的状态前置检查**。每个操作前都要校验前置条件，不依赖额外的反作弊架构。

---

## 10. 完整端到端：A 加入 B 的世界，玩半小时，A 离开

```
[初始]
  A.world = World_A (single, A 一人)
  B.world = World_B (single, B 一人)

[A 申请加入 B 的世界]
  A → PlayerApplyEnterMpReq(B.uid)
  Server: target=B 在线 ✓
  Server: A 不在 MP ✓ (反作弊 1)
  Server: 没有 pending request ✓ (反作弊 2)
  Server: 给 B 发 PlayerApplyEnterMpNotify(A)
  B 屏幕弹窗: "A 想加入你的世界"

[B 同意]
  B → PlayerApplyEnterMpResultReq(A.uid, agreed=true)
  Server: 检查 A 仍单人 ✓
  Server: 创建 World_AB(host=B, multiplayer=true)
  Server: World_B 销毁，B 进 World_AB
  Server: B → PlayerEnterSceneNotify(HostFromSingleToMp)
  
  Server: A.position = B.position, A.sceneId = B.sceneId
  Server: World_AB.addPlayer(A)
    - A.peerId = 1 (host 是 0)
    - 复制单人队伍到 MP 队伍
    - 创建 EntityTeam_A 加入 scene
    - 通知房间内 (B) 有新玩家
  Server: A → PlayerEnterSceneNotify(ENTER_OTHER, TeamJoin)

[A 加载场景]
  A → EnterSceneReadyReq      (sceneLoadState = LOADING)
  Server 推送场景实体给 A
  A → EnterSceneDoneReq       (sceneLoadState = INIT)
  Server → PostEnterSceneRsp
  A → PostEnterSceneReq       (sceneLoadState = LOADED)

[A 和 B 一起打怪 30 分钟]
  - A 打怪 → A 客户端发 CombatInvocationsNotify
  - Server 处理 (notes/16) → World_AB.broadcastPacket(...)
  - B 看到 A 攻击动画 + 怪物受击
  - B 击杀怪物 → 同上
  - 怪物 HP 由 server 维护，扣减后广播给两人

[A 主动离开]
  A → 客户端 UI 点 "离开队伍"
  A → some packet (LeaveCoopReq)
  Server: leaveCoop(A)
    - 检查所有人 scene LOADED ✓
    - 创建新 World_A (single)
    - World_AB.removePlayer(A) → 同时 sendPacket DelTeamEntityNotify 给 A
    - World_A.addPlayer(A) → A 现在是单人
    - A → PlayerEnterSceneNotify(TeamBack)
    
  剩下 World_AB 只有 B 一人
  (B 是 host，仍是 MP world，可继续接受其他人加入)
```

---

## 11. 给做大型联机游戏开发者的提炼

1. **World/Scene/Player 三级容器**——不要扁平化设计，多层抽象让"房间内多场景"成为可能
2. **peerId 是 World 内的相对 ID**——避免到处传 player.uid，简化广播逻辑
3. **EntityTeam 占位实体**——其他玩家不需要知道你队伍内具体角色，只需知道"这个玩家在这里"
4. **进入 MP 时复制队伍**而非共享——保持单人/多人独立，避免互相污染
5. **SceneLoadState 必须严格**——状态机不规整会导致幽灵实体、丢包、客户端崩
6. **EnterReason 枚举要细**——客户端按 reason 播放不同动画/转场
7. **host 有特权**（踢人）但权力受限（不能 mid-flight 解散）——避免恶意房主
8. **反作弊 = 状态前置检查**：简单系统不需要额外架构，每个操作前严格校验
9. **scene 级 vs world 级广播分开**——节省网络流量（不同场景不互相干扰）
10. **剧情系统要 isMpBlock**——MP 模式下不能推进 host 的剧情，避免 guest 跳过自己的剧情

---

## 12. 数据规模感

* `EnterReason` 枚举：~30 个值
* `SceneLoadState`：4 个状态
* World 最多 4 人（host + 3 guest）
* CoopRequest 过期时间：通常 30 秒（公开邀请有效期）
* MP 队伍 size：4 / num_players（双人 = 4 共享，三人 = 各 1 + host 1，四人 = 各 1）

代码规模：
- `MultiplayerSystem.java`：155 行
- `World.java`：500+ 行
- `Scene.java`：1400+ 行（含视野管理 + 实体管理 + 副本逻辑）

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/systems/MultiplayerSystem.java` (155 行核心)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/world/World.java` (容器 + 广播)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/world/Scene.java` (实体 + 视野)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerPlayerApplyEnterMpReq.java` (邀请入口)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerPlayerApplyEnterMpResultReq.java` (回复入口)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/CoopRequest.java` (邀请请求实体)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/entity/EntityTeam.java` (玩家占位实体)
