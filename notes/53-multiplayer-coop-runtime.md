# Multiplayer / Coop 系统运行时深度剖析

> 第 53 篇：grasscutter 中**两套同名"Coop"系统**完整解剖 —— `MultiplayerSystem` (真正的 4 人联机) + `CoopHandler` (角色同伴剧情, Hangout Events)，命名相同但**完全不同**的系统。

---

## 0. 为什么这一篇重要

前 52 篇里 Coop / Multiplayer 反复出现但 runtime 没专门挖：
- notes/18 多人协作：设计层（World/Scene/Player 三级容器 + 视野广播）
- notes/35 Scene/World：联机时 host 离开 World 解散
- notes/34 EntityAvatar：联机队伍人数公式 (floor vs ceil)
- notes/40 Player Manager：`coopHandler` 是 25 之一

但**邀请协议 RPC 怎么走？10 秒过期机制？联机切换是怎么"创建新 World"？CoopHandler 到底是干嘛的？**——这一篇统一回答。

---

## 1. 命名陷阱：两套不同的 "Coop"

```
┌──────────────────────────────────────────────────────────────┐
│  MultiplayerSystem (154 行, BaseGameSystem)                    │
│  - 真正的"多人联机"                                              │
│  - 邀请 / 加入 / 退出 / 踢人                                      │
│  - 操作 World 对象 (new World(host, true))                     │
└──────────────────────────────────────────────────────────────┘

                            ≠

┌──────────────────────────────────────────────────────────────┐
│  CoopHandler (348 行, BasePlayerDataManager)                   │
│  - "Coop" = Hangout Events (角色同伴剧情)                       │
│  - 同伴探索 / 邀约任务                                            │
│  - Coop Chapter / Coop Point / Coop CG                          │
│  - 完全单人玩法                                                  │
└──────────────────────────────────────────────────────────────┘
```

**为什么混淆**：
- mihoyo 给"同伴邀约"用了 `Coop` (Cooperation) 命名
- grasscutter 沿用同名
- **实际功能完全不同**——前者是联机，后者是单人剧情

→ 这一篇**两套都讲**，免得后人继续混淆。

---

## 2. MultiplayerSystem：真正的联机协调（154 行）

### 2.1 4 个核心操作

```java
public class MultiplayerSystem extends BaseGameSystem {
    public void applyEnterMp(Player player, int targetUid);              // 邀请
    public void applyEnterMpReply(Player hostPlayer, int applyUid, boolean isAgreed);  // 回应
    public boolean leaveCoop(Player player);                              // 自己退出
    public boolean kickPlayer(Player player, int targetUid);              // 踢人
}
```

→ **整个联机系统只 154 行** —— 极简但精炼。

### 2.2 applyEnterMp：申请加入

```java
public void applyEnterMp(Player player, int targetUid) {
    Player target = getServer().getPlayerByUid(targetUid);
    if (target == null) {
        player.sendPacket(new PacketPlayerApplyEnterMpResultNotify(
            targetUid, "", false, MpEnterResultReason.PLAYER_CANNOT_ENTER_MP));
        return;
    }
    
    // 已在联机 → 不允许加入别人
    if (player.getWorld().isMultiplayer()) return;
    
    // 检查重复申请
    CoopRequest request = target.getCoopRequests().get(player.getUid());
    if (request != null && !request.isExpired()) return;
    
    // 创建申请 + 通知 host
    request = new CoopRequest(player);
    target.getCoopRequests().put(player.getUid(), request);
    target.sendPacket(new PacketPlayerApplyEnterMpNotify(player));
}
```

### 2.3 CoopRequest：10 秒过期

```java
public class CoopRequest {
    private final Player requester;
    private final long requestTime;
    private final long expireTime;
    
    public CoopRequest(Player requester) {
        this.requester = requester;
        this.requestTime = System.currentTimeMillis();
        this.expireTime = this.requestTime + 10000;   // ★ 10 秒
    }
    
    public boolean isExpired() {
        return System.currentTimeMillis() > getExpireTime();
    }
}
```

→ **邀请 10 秒过期** —— 没回应自动作废。
→ 防止"申请堆积"——host 不被无数请求轰炸。

### 2.4 applyEnterMpReply：host 同意

```java
public void applyEnterMpReply(Player hostPlayer, int applyUid, boolean isAgreed) {
    CoopRequest request = hostPlayer.getCoopRequests().get(applyUid);
    if (request == null || request.isExpired()) return;
    
    Player requester = request.getRequester();
    hostPlayer.getCoopRequests().remove(applyUid);
    
    if (requester.getWorld().isMultiplayer()) {
        // 申请者已经在别处联机了
        requester.sendPacket(new PacketPlayerApplyEnterMpResultNotify(...PLAYER_CANNOT_ENTER_MP));
        return;
    }
    
    // 回应包 (同意/拒绝)
    requester.sendPacket(new PacketPlayerApplyEnterMpResultNotify(hostPlayer, isAgreed, MpEnterResultReason.PLAYER_JUDGE));
    
    if (!isAgreed) return;
    
    // === 同意流程 ===
    
    // 1. ★ host 还在单机 → 创建联机 World
    if (!hostPlayer.getWorld().isMultiplayer()) {
        World world = new World(hostPlayer, true);   // ★ isMultiplayer = true
        world.addPlayer(hostPlayer);
        
        hostPlayer.sendPacket(new PacketPlayerEnterSceneNotify(
            hostPlayer, hostPlayer, EnterType.ENTER_SELF, 
            EnterReason.HostFromSingleToMp, hostPlayer.getScene().getId(), hostPlayer.getPosition()));
    }
    
    // 2. 同步 requester 位置到 host
    requester.getPosition().set(hostPlayer.getPosition());
    requester.getRotation().set(hostPlayer.getRotation());
    requester.setSceneId(hostPlayer.getSceneId());
    
    // 3. 让 requester 加入 host 的 World
    hostPlayer.getWorld().addPlayer(requester);
    
    // 4. 通知 requester 进场景
    requester.sendPacket(new PacketPlayerEnterSceneNotify(
        requester, hostPlayer, EnterType.ENTER_OTHER, 
        EnterReason.TeamJoin, hostPlayer.getScene().getId(), hostPlayer.getPosition()));
}
```

### 2.5 单机 → 联机切换关键

```java
if (!hostPlayer.getWorld().isMultiplayer()) {
    World world = new World(hostPlayer, true);   // ★ 新建联机 World
    world.addPlayer(hostPlayer);
    hostPlayer.sendPacket(new PacketPlayerEnterSceneNotify(..., EnterReason.HostFromSingleToMp, ...));
}
```

**关键洞察**：
- host 原本在**单机 World** (isMultiplayer=false)
- 同意邀请时**创建新的联机 World** (isMultiplayer=true)
- host **重新进入新 World**（用 `EnterReason.HostFromSingleToMp` 通知客户端）
- requester 加入这个新 World

→ "**host 切换世界**" —— 这就是为什么"答应联机时自己也要重新加载场景"。

### 2.6 EnterReason 在联机中的使用

```java
EnterReason.HostFromSingleToMp    // host 从单机切到联机
EnterReason.TeamJoin              // 客人加入
EnterReason.TeamBack              // 自己离开回到单机
EnterReason.TeamKick              // 被踢
```

→ 4 种联机相关 EnterReason —— 每种对应不同客户端 UI 提示。

### 2.7 leaveCoop：自己退出

```java
public boolean leaveCoop(Player player) {
    if (!player.getWorld().isMultiplayer()) return false;
    
    // ★ 必须所有人场景加载完才能退
    for (Player p : player.getWorld().getPlayers()) {
        if (p.getSceneLoadState() != SceneLoadState.LOADED) {
            return false;
        }
    }
    
    // 创建新的单机 World
    World world = new World(player);
    world.addPlayer(player);
    
    player.sendPacket(new PacketPlayerEnterSceneNotify(
        player, EnterType.ENTER_SELF, EnterReason.TeamBack, 
        player.getScene().getId(), player.getPosition()));
    
    return true;
}
```

### 2.8 退出的"所有人加载完"检查

```java
for (Player p : player.getWorld().getPlayers()) {
    if (p.getSceneLoadState() != SceneLoadState.LOADED) {
        return false;
    }
}
```

→ "**联机加载中不能退出**" —— 防止中途退出造成同步混乱。
→ 玩家点退出时如果队友正在加载，按钮无效。

### 2.9 kickPlayer：踢人

```java
public boolean kickPlayer(Player player, int targetUid) {
    // 必须 host 才能踢
    if (!player.getWorld().isMultiplayer() || player.getWorld().getHost() != player) {
        return false;
    }
    
    Player victim = player.getServer().getPlayerByUid(targetUid);
    if (victim == null || victim == player) return false;
    
    if (victim.getSceneLoadState() != SceneLoadState.LOADED) return false;
    
    // ★ 把受害者拉到新的单机 World
    World world = new World(victim);
    world.addPlayer(victim);
    
    victim.sendPacket(new PacketPlayerEnterSceneNotify(
        victim, EnterType.ENTER_SELF, EnterReason.TeamKick, 
        victim.getScene().getId(), victim.getPosition()));
    
    return true;
}
```

→ 踢人逻辑与 leave 几乎相同——但**权限仅 host 有**。

### 2.10 host 离开整个 World 解散

参见 notes/35 §3.2：
```java
// World.removePlayer
if (this.getHost() == player) {
    List<Player> kicked = new ArrayList<>(this.getPlayers());
    for (Player victim : kicked) {
        World world = new World(victim);
        world.addPlayer(victim);
        victim.sendPacket(new PacketPlayerEnterSceneNotify(..., EnterReason.TeamKick, ...));
    }
}
```

→ **host 离开 = 整个 World 解散**——所有玩家被踢回自己单机 World。
→ 与 `kickPlayer` 用相同 EnterReason.TeamKick。

---

## 3. CoopHandler：角色同伴剧情（348 行）

### 3.1 关键澄清

**这不是联机系统！** —— mihoyo 的"Coop"指的是：
- "**邀约任务**"（Hangout Events）
- 玩家与单个角色的**专属剧情线**
- 完全**单人玩法**
- 多结局分支
- 解锁 CG / 名片 / 奖励

→ **每个角色一个 Coop Chapter**，玩家选项决定剧情走向。

### 3.2 字段全图

```java
@Entity
public class CoopHandler extends BasePlayerDataManager {
    @Getter private Map<Integer, CoopCardEntry> coopCards;   // chapterId → 该角色的所有数据
    @Getter @Setter private int curCoopPoint;                  // 当前剧情点
}
```

→ 持久化到 Player 文档中（notes/30 embedded）。

### 3.3 CoopCardEntry 嵌套数据

```java
@Entity
public static class CoopCardEntry {
    private Boolean accepted;                  // 是否已接受邀约
    private Boolean viewed;                    // 是否已查看
    private int totalEndCount;                  // 总结局数
    private int finishedEndCount;               // 已完成结局数
    private Map<Integer, CoopPointEntry> points;    // 剧情点
    private Map<Integer, CoopCGEntry> cgs;          // 解锁的 CG
    private Map<Integer, CoopRewardEntry> rewards;   // 奖励状态
    private MainCoopData mainCoop;               // 主剧情数据
    private int curCoopPoint;
}

@Entity
public static class CoopPointEntry {
    private int selfConfidence;                  // ★ 自信值 (剧情属性)
    private CoopPointState state;                // UNSTARTED / STARTED / FINISHED
}
```

### 3.4 MainCoopData：分支记录

```java
@Entity
public static class MainCoopData {
    private int id;
    private Map<Integer, Integer> normalVarMap;     // ★ 常规变量
    private List<Integer> savePointIdList;          // ★ save point 列表 (回到剧情节点)
    private Map<Integer, Integer> seenEndingMap;    // ★ 已见结局
    private int selfConfidence;                      // 总自信值
    private Status status;                            // 状态
    private Map<Integer, Integer> tempVarMap;        // 临时变量
}
```

**5 类剧情状态**：
- `normalVarMap` — 永久变量（选择 A/B/C 路线）
- `savePointIdList` — 剧情回溯节点（玩家可"读档"）
- `seenEndingMap` — 看过的结局
- `selfConfidence` — 角色对玩家的"自信值"（影响剧情走向）
- `tempVarMap` — 临时变量（当次剧情用）

→ 类似 Galgame 的存档系统——但服务器持久化每个变量。

---

## 4. 3 种 UnlockCond（章节解锁条件）

```java
private List<Integer> getLockReasonList(CoopChapterData chapter) {
    for (val condition : chapter.getUnlockCond()) {
        switch (condition.getType()) {
            case "COOP_COND_FINISH_QUEST" -> {
                // 必须完成某主线任务
                val quest = this.player.getQuestManager().getQuestById(arg);
                if (quest == null || !quest.getState().equals(QUEST_STATE_FINISHED))
                    lockReasonList.add(i + 1);
            }
            case "COOP_COND_PLAYER_LEVEL" -> {
                // 玩家等级要求
                if (this.player.getLevel() < arg) lockReasonList.add(i + 1);
            }
            case "COOP_COND_CHAPTER_END_ALL_FINISH" -> {
                // ★ 另一章节"所有结局都看过"
                val card = this.coopCards.get(arg);
                if (card.getFinishedEndCount() != card.getTotalEndCount())
                    lockReasonList.add(i + 1);
            }
        }
    }
}
```

### 4.1 3 种解锁逻辑

| Cond | 含义 |
|---|---|
| FINISH_QUEST | 完成主线任务 |
| PLAYER_LEVEL | 玩家等级 ≥ N |
| **CHAPTER_END_ALL_FINISH** | **必须看完前一章所有结局**才解锁下一章 |

→ "看完所有结局"机制 —— 鼓励玩家**反复重玩**看不同路线。

---

## 5. unlockChapterUpdateNotify：解锁章节

```java
public void unlockChapterUpdateNotify(int chapterId) {
    val coopCard = this.coopCards.get(chapterId);
    
    // 找起始点 (pointPosId = 1)
    val startPointId = GameData.getCoopPointDataMap().values().stream()
        .filter(j -> j.getChapterId() == chapterId && j.getPointPosId() == 1)
        .toList().get(0).getId();
    
    // ★ 初始化起点状态
    coopCard.getPoints().get(startPointId).setSelfConfidence(5);
    coopCard.getPoints().get(startPointId).setState(CoopPointState.STATE_STARTED);
    coopCard.setAccepted(true);
    
    // 构造 protobuf
    val coopChapter = new CoopChapter();
    coopChapter.setId(chapterId);
    coopChapter.setState(CoopChapterState.STATE_ACCEPT);
    coopChapter.setTotalEndCount(coopCard.getTotalEndCount());
    
    // CG / Point / Reward 列表
    coopChapter.setCoopCgList(...);
    coopChapter.setCoopPointList(...);
    coopChapter.setCoopRewardList(...);
    
    this.player.sendPacket(new PacketCoopChapterUpdateNotify(List.of(coopChapter)));
}
```

→ 玩家点"接受邀约"触发：
1. 找起始 point
2. 设 selfConfidence = 5（初始值）
3. 章节状态 → STATE_ACCEPT
4. 客户端展示章节地图

---

## 6. selfConfidence 自信值机制

```java
coopCard.getPoints().get(startPointId).setSelfConfidence(5);
```

**selfConfidence 是 Coop 系统的核心数值**：
- 初始 = 5
- 每个剧情选择影响 selfConfidence
- 不同 selfConfidence 解锁不同结局
- 类似 Galgame 的"好感度"

### 6.1 与剧情节点的关系

```java
public void updateCoopPoint(int coopPointId, int selfConfidence, CoopPointState state, boolean shouldNotify) {
    val coopPoint = this.coopCards.get(coopPointData.getChapterId()).getPoints().get(coopPointId);
    coopPoint.selfConfidence = selfConfidence;
    coopPoint.state = state;
    
    if (shouldNotify) {
        player.sendPacket(new PacketCoopPointUpdateNotify(new CoopPoint(coopPointId, selfConfidence, state)));
    }
}
```

→ 每个剧情点（CoopPoint）记录"到达时的 selfConfidence"——这是路线决策的快照。

---

## 7. checkNextCoopPointAccept：剧情推进

```java
public void checkNextCoopPointAccept(int questId) {
    val coopPointCanidateList = GameData.getCoopPointDataMap().values().stream()
        .filter(x -> x.getAcceptQuest() == questId)
        .toList();
    if (coopPointCanidateList.isEmpty()) return;
    
    val coopPointData = coopPointCanidateList.get(0);
    
    // 1. 完成上一个 coop point
    if (this.curCoopPoint != 0) {
        val curSelfConfidence = this.coopCards.get(curChapter).getMainCoop().selfConfidence;
        updateCoopPoint(curCoopPoint, curSelfConfidence, CoopPointState.STATE_FINISHED, true);
    }
    
    // 2. 设置新 current point
    this.curCoopPoint = coopPointData.getId();
    
    // 3. 启动新 coop point
    val curChapter = this.coopCards.get(coopPointData.getChapterId());
    boolean shouldNotify = curChapter.getPoints().get(coopPointData.getId()).getState() != CoopPointState.STATE_STARTED;
    updateCoopPoint(coopPointData.getId(), curChapter.getMainCoop().selfConfidence, CoopPointState.STATE_FINISHED, shouldNotify);
    
    this.player.sendPacket(new PacketCoopProgressUpdateNotify(coopPointData.getId(), true));
}
```

### 7.1 Coop ↔ Quest 桥接

```java
.filter(x -> x.getAcceptQuest() == questId)
```

→ **每个 Coop Point 关联一个 Quest** —— 玩家完成对应 quest 时推进 Coop 剧情。

→ 这是 Quest (notes/43) 与 Coop 系统的桥接点。

---

## 8. Coop Chapter / Point / CG / Reward 4 类数据

```java
GameData.getCoopChapterDataMap()    // 章节配置 (per character)
GameData.getCoopPointDataMap()       // 剧情点 (节点)
GameData.getCoopCGDataMap()           // CG 配置 (动画)
GameData.getCoopRewardDataMap()       // 奖励配置
```

### 8.1 数据关系

```
CoopChapter (per 角色)
   └── CoopPoint × N (剧情节点, 图状结构)
       ├── pointPosId = 1 (起点)
       ├── pointPosId = N (终点 / 结局)
       └── acceptQuest (关联的任务)
   └── CoopCG × M (剧情动画)
   └── CoopReward × R (奖励)
```

→ **每个 Coop Chapter ≈ 一部短篇 visual novel**。

---

## 9. 完整时序：玩家与角色同伴剧情

```
[阶段 1: 解锁章节]
玩家完成主线任务 / 等级达到要求 / 看完前章所有结局
   ↓
CoopHandler.conditionMetChapterUpdateNotify:
   - 重算 lockReasonList
   - 发 PacketCoopChapterUpdateNotify (STATE_COND_MEET)

[阶段 2: 接受邀约]
玩家点击"接受邀约"
   ↓ UnlockCoopChapterReq
HandlerUnlockCoopChapterReq:
   - CoopHandler.unlockChapterUpdateNotify(chapterId):
     - 找 startPoint
     - selfConfidence = 5
     - STATE_ACCEPT

[阶段 3: 开始剧情]
玩家进入起始 quest
   ↓
QuestManager.acceptQuest
   ↓
CoopHandler.checkNextCoopPointAccept(questId):
   - 完成上一个 CoopPoint
   - 设新 curCoopPoint
   - PacketCoopPointUpdateNotify

[阶段 4: 剧情中]
玩家做选择 / 完成对话
   ↓ 影响 selfConfidence
   ↓ 走不同 quest 分支
   ↓ 触发不同 CoopPoint

[阶段 5: 看到结局]
完成结局 quest
   ↓ checkNextCoopPointAccept (POINT_END 类型)
   ↓
finishedEndCount++
seenEndingMap[endingId] = 1
   ↓
PacketCoopProgressUpdateNotify

[阶段 6: 解锁新章 (如果是 CHAPTER_END_ALL_FINISH 条件)]
所有结局看完 → finishedEndCount == totalEndCount
   ↓
conditionMetChapterUpdateNotify:
   触发下一章解锁检查
```

→ Coop 剧情**完全异步**——玩家可以放着不玩，进度持久化。

---

## 10. Multiplayer 完整时序

```
[阶段 1: 申请加入]
玩家 A 找到玩家 B 的 UID
   ↓ PlayerApplyEnterMpReq { targetUid: B }
MultiplayerSystem.applyEnterMp:
   - 检查 A 不在联机
   - 创建 CoopRequest (10 秒过期)
   - 加入 B.coopRequests
   - 通知 B (PacketPlayerApplyEnterMpNotify)

[阶段 2: B 回应 (10 秒内)]
B 点击"同意" / "拒绝"
   ↓ PlayerApplyEnterMpResultReq { applyUid: A, isAgreed }
MultiplayerSystem.applyEnterMpReply:
   - 移除 request
   - 通知 A 结果

[阶段 3a: 拒绝]
   仅发 PacketPlayerApplyEnterMpResultNotify(isAgreed=false)
   结束

[阶段 3b: 同意]
   ↓
[阶段 3b.1: host 切换 World]
B 还在单机 World?
   - new World(B, isMultiplayer=true)
   - world.addPlayer(B)
   - B 收到 PacketPlayerEnterSceneNotify(EnterReason.HostFromSingleToMp)

[阶段 3b.2: A 加入]
   - A 位置同步到 B 位置
   - hostPlayer.getWorld().addPlayer(A)
   - A 收到 PacketPlayerEnterSceneNotify(EnterReason.TeamJoin)

[阶段 3b.3: 双方场景加载]
两人 EnterSceneDoneReq (notes/35)
   ↓
开始联机协作

[阶段 4: 联机中]
- 所有玩家共享 World.players
- 所有 Scene.entities 共享
- 怪物 AI 跑在 host 客户端 (notes/32)
- 战利品按 share/give 决定 (notes/39)
- 房主负责 World/Scene 的"权威"

[阶段 5a: 客人退出]
A 点击退出
   ↓ leaveCoop:
   - 检查所有人 sceneLoadState = LOADED
   - new World(A) 创建单机 World
   - A 收 EnterReason.TeamBack
   - B 看不到 A

[阶段 5b: 房主踢人]
B (host) 点踢出 A
   ↓ kickPlayer:
   - 检查 B 是 host
   - 检查 A.sceneLoadState = LOADED
   - new World(A) 创建单机 World
   - A 收 EnterReason.TeamKick

[阶段 5c: 房主退出]
B 离开
   ↓ World.removePlayer (notes/35):
   - 所有其他玩家都被踢
   - 每个人都被 new World 创建单机 World
   - 都收到 EnterReason.TeamKick

[阶段 6: 重新单机]
A/B 各自独立 World
- 单机进度继续
- 副本无法访问 (副本在联机 World, 已被销毁)
```

---

## 11. CoopRequests 字段在 Player

`Player.java:226`：
```java
private transient final Int2ObjectMap<CoopRequest> coopRequests;
```

→ 每个玩家有自己的 `coopRequests` Map：
- key = 申请者 UID
- value = CoopRequest (含过期时间)

→ 多人同时申请 → 多个 entry。
→ `transient` 不持久化（10 秒过期，无需存）。

---

## 12. 设计模式总结

### 12.1 World 切换 = 联机切换

```
单机: new World(player)              // isMultiplayer=false
   ↓ 申请 + 同意
联机 host: new World(host, true)    // isMultiplayer=true (重建!)
   ↓ requester.addPlayer
4 玩家共享同一 World
```

→ "**切换联机就是重新创建 World**" —— 没有"加 flag"逻辑，是**完全新建**。

### 12.2 EnterReason 编码状态转换

```
HostFromSingleToMp  → host 从单机切联机
TeamJoin            → 客人加入
TeamBack            → 客人退出
TeamKick            → 被踢 (或 host 走解散)
```

→ 4 种状态转换编码在 EnterReason 枚举里——客户端按此切换 UI。

### 12.3 10 秒过期保护

```java
expireTime = requestTime + 10000
```

→ 防止 host 被无数申请轰炸 / 防止 stale request 永久存在。

### 12.4 sceneLoadState 防护

```java
if (p.getSceneLoadState() != SceneLoadState.LOADED) return false;
```

→ 加载中无法 leave/kick —— 防止同步混乱。

### 12.5 双套 Coop 命名

```
MultiplayerSystem  = "联机" (mihoyo 内部叫 MP)
CoopHandler        = "邀约任务" (mihoyo 内部叫 Coop = 与角色合作)
```

→ **命名冲突来自 mihoyo** —— grasscutter 沿用。开发者要看上下文区分。

---

## 13. 反作弊视角

### 13.1 Multiplayer

| 攻击 | 是否有效 |
|---|---|
| 客户端发"我已在联机" | ✗ 服务器算 |
| 篡改 CoopRequest 过期 | ✗ 服务器存 |
| 不是 host 踢人 | ✗ host 检查 |
| 加载中退出 | ✗ sceneLoadState 检查 |
| 邀请不存在玩家 | ✗ getPlayerByUid 检查 |

### 13.2 Coop (Hangout)

| 攻击 | 是否有效 |
|---|---|
| 跳过解锁条件 | ✗ getLockReasonList 检查 |
| 改 selfConfidence | ✗ 服务器存 |
| 假装看完结局 | ✗ finishedEndCount 服务器算 |
| 重领奖励 | ✗ CoopRewardEntry 服务器记录 |

→ 两套系统**反作弊都强**。

---

## 14. 关键收获

1. **命名陷阱**：grasscutter "Coop" 是**两套不同系统**——MultiplayerSystem (4 人联机) + CoopHandler (角色邀约剧情)
2. **MultiplayerSystem 154 行**：极简但精炼，4 个核心操作 (applyEnterMp / applyEnterMpReply / leaveCoop / kickPlayer)
3. **CoopRequest 10 秒过期**：防申请堆积
4. **单机 ↔ 联机 = 重建 World**：`new World(host, true)` —— 完全新建而非加 flag
5. **4 种 EnterReason 联机相关**：HostFromSingleToMp / TeamJoin / TeamBack / TeamKick
6. **退出 / 踢人需 sceneLoadState = LOADED**：防止加载中操作
7. **host 离开 World 整体解散**：所有玩家被踢回单机 (notes/35)
8. **CoopHandler 348 行 (Hangout Events)**：角色邀约任务，**完全单人**剧情
9. **CoopCardEntry per chapter (per character)**：每个角色一个邀约章节
10. **3 种 UnlockCond**：FINISH_QUEST / PLAYER_LEVEL / **CHAPTER_END_ALL_FINISH**
11. **selfConfidence 自信值**：初始 5 + 选择影响 + 决定结局分支
12. **MainCoopData 5 类变量**：normalVarMap / savePointIdList / seenEndingMap / selfConfidence / tempVarMap
13. **CoopPoint state 3 状态**：UNSTARTED / STARTED / FINISHED
14. **CoopPoint ↔ Quest 桥接**：`acceptQuest` 字段关联——quest 完成时推进 Coop
15. **每个 Coop Chapter ≈ 短篇 visual novel**：CG + Point + Reward + 多结局
16. **CHAPTER_END_ALL_FINISH 鼓励重玩**：必须看完所有结局才解锁下一章
17. **持久化在 Player 文档 embedded**：CoopHandler 字段直接存
18. **Coop 与 Multiplayer 完全独立**：可同时使用（边联机边玩邀约？不可能，邀约是单人）

---

## 15. 一句话总结

> **grasscutter "Coop" 命名陷阱：MultiplayerSystem (154 行, 真正联机) + CoopHandler (348 行, 角色邀约剧情) 是两套独立系统。联机靠 World 切换 (new World(host, true)) + 10 秒过期 CoopRequest + 4 种 EnterReason + sceneLoadState 防护; 邀约靠 selfConfidence 自信值 + MainCoopData 5 类变量 + 3 种 UnlockCond (含 CHAPTER_END_ALL_FINISH 鼓励重玩) + CoopPoint ↔ Quest 桥接.**
> 
> **设计哲学: 联机 = 状态机切换 (单机 World → 联机 World 重建); 邀约 = visual novel 变量集 + Quest 桥接; 两套独立命名混淆来自 mihoyo, grasscutter 沿用——开发者需上下文区分.**

---

**前置笔记**：
- notes/18 多人协作设计 - World/Scene/Player 三级容器
- notes/30 持久化 - CoopHandler 嵌入 Player 文档
- notes/34 EntityAvatar - 联机队伍人数公式
- notes/35 Scene/World - World 切换 + host 离开解散
- notes/40 Player Manager - coopHandler 是 25 Manager 之一
- notes/43 Quest 引擎 - Coop ↔ Quest acceptQuest 桥接
- notes/46 GameServer - MultiplayerSystem 是 14 GameSystem 之一

**关联文件**：
- `MultiplayerSystem.java`(154) - 真正联机
- `CoopRequest.java`(32) - 10 秒过期申请
- `CoopHandler.java`(348) - 邀约剧情 (Hangout)
- `Player.java:226` - coopRequests transient 字段
- `CoopChapterData.java` / `CoopPointData.java` / `CoopCGData.java` / `CoopRewardData.java` - 4 类配表
- `CoopPointState` / `CoopChapterState` / `Status` - 3 类状态枚举

**研究的源代码**: 534 行联机+邀约核心 + 4 类 Coop 配表。
