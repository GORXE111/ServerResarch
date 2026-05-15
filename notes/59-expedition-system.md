# ExpeditionSystem 派遣系统深度剖析

> 第 59 篇：notes/46 提过它是 14 GameSystem 之一——但派遣运行时从未打开。**112 行 (5 文件)** 的极简"时间锁被动收益"系统，**lazy evaluation 模式第 4 次确认**（继 Resin/Mail/Shop 之后）。

---

## 0. 为什么这一篇重要

前 58 篇里 Expedition 出现但 runtime 没专门挖：
- notes/46 GameServer：ExpeditionSystem 是 14 GameSystem 之一
- notes/38 Inventory：`ActionReason.ExpeditionReward(1075)` 是 190+ 之一
- notes/41 事件总线：`TRIGGER_AVATAR_EXPEDITION(303)` / `TRIGGER_START_AVATAR_EXPEDITION(308)`

但**角色外派怎么记时间？完成怎么检测？lazy 模式是否延续？**——这一篇统一回答，并**验证 grasscutter 的 lazy evaluation 系统性偏好**。

---

## 1. Expedition 系统全图

```
┌─────────────────────────────────────────────────────────────┐
│  ExpeditionSystem (41 行) — BaseGameSystem                    │
│  - expeditionRewardData: Map<expId, List<RewardDataList>>     │
│  - 只加载 ExpeditionReward.json (无运行时逻辑!)               │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  ExpeditionInfo (26 行) — @Entity (嵌入 Player)               │
│  - state (DOING / FINISH_WAIT_REWARD)                         │
│  - expId / hourTime / startTime                               │
└────────────────────────┬────────────────────────────────────┘
                         │ per Player
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  Player.expeditionInfo: Map<avatarGuid, ExpeditionInfo>       │
│  - addExpeditionInfo / removeExpeditionInfo / getExpeditionInfo │
│  - onLogin/onTick lazy 完成检测                               │
└────────────────────────┬────────────────────────────────────┘
                         │ 4 Handler
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  AllDataReq / StartReq / GetRewardReq / CallBackReq           │
└─────────────────────────────────────────────────────────────┘
```

→ **112 行 + 4 Handler** —— grasscutter 中**最小的 GameSystem**之一。

---

## 2. ExpeditionSystem：纯数据加载（41 行）

```java
public class ExpeditionSystem extends BaseGameSystem {
    private final Int2ObjectMap<List<ExpeditionRewardDataList>> expeditionRewardData;
    
    public ExpeditionSystem(GameServer server) {
        super(server);
        this.expeditionRewardData = new Int2ObjectOpenHashMap<>();
        this.load();
    }
    
    public synchronized void load() {
        getExpeditionRewardDataList().clear();
        List<ExpeditionRewardInfo> banners = DataLoader.loadList("ExpeditionReward.json", ExpeditionRewardInfo.class);
        for (ExpeditionRewardInfo di : banners) {
            getExpeditionRewardDataList().put(di.getExpId(), di.getExpeditionRewardDataList());
        }
    }
}
```

### 2.1 ExpeditionSystem 几乎"空壳"

→ **没有任何运行时逻辑** —— 只是个 `ExpeditionReward.json` 配置容器。
→ 实际逻辑分散在：
- `HandlerAvatarExpeditionStartReq` —— 开始派遣
- `HandlerAvatarExpeditionGetRewardReq` —— 领奖
- `HandlerAvatarExpeditionCallBackReq` —— 提前召回
- `Player` —— 状态存储 + lazy 完成检测

→ 这是 grasscutter 的常见模式：**System = 配置加载器，Handler = 业务逻辑，Player = 状态**。

### 2.2 ExpeditionReward.json 结构

```json
[
  {
    "expId": 1001,
    "expeditionRewardDataList": [
      { "hourTime": 4,  "rewards": [{itemId: 104001, count: 4}] },
      { "hourTime": 8,  "rewards": [{itemId: 104001, count: 9}] },
      { "hourTime": 12, "rewards": [{itemId: 104001, count: 14}] },
      { "hourTime": 20, "rewards": [{itemId: 104001, count: 25}] }
    ]
  }
]
```

→ 每个派遣点 (expId) 有**多档时长**（4/8/12/20 小时），时长越久奖励越多。

---

## 3. ExpeditionInfo：4 字段状态（26 行）

```java
@Entity
@Getter @Setter
public class ExpeditionInfo {
    private AvatarExpeditionState state;   // DOING / FINISH_WAIT_REWARD
    private int expId;                      // 派遣点 ID
    private int hourTime;                   // 选择的时长 (4/8/12/20)
    private int startTime;                  // ★ 开始时间戳 (秒)
}
```

→ **极简 4 字段** —— `startTime` 是 lazy 计算的关键。

### 3.1 AvatarExpeditionState 状态机

```
AVATAR_EXPEDITION_DOING               ← 派遣中
AVATAR_EXPEDITION_FINISH_WAIT_REWARD  ← 完成待领奖
(领奖后 ExpeditionInfo 直接删除, 无 "已领" 状态)
```

→ **2 个有效状态** —— 领奖即删除，不保留历史。

### 3.2 持久化在 Player

`Player.java:149`：
```java
@Getter private Map<Long, ExpeditionInfo> expeditionInfo;   // avatarGuid → info
```

→ `Map<avatarGuid, ExpeditionInfo>` —— 每个外派角色一条。
→ 嵌入 Player 文档持久化（notes/30 embedded）。

---

## 4. 开始派遣：HandlerAvatarExpeditionStartReq

```java
public void handle(GameSession session, byte[] header, AvatarExpeditionStartReq req) {
    var player = session.getPlayer();
    
    int startTime = Utils.getCurrentSeconds();   // ★ 记录当前时间
    player.addExpeditionInfo(req.getAvatarGuid(), req.getExpId(), req.getHourTime(), startTime);
    player.save();
    session.send(new PacketAvatarExpeditionStartRsp(player.getExpeditionInfo()));
}

// Player.addExpeditionInfo
public void addExpeditionInfo(long avatarGuid, int expId, int hourTime, int startTime) {
    ExpeditionInfo exp = new ExpeditionInfo();
    exp.setExpId(expId);
    exp.setHourTime(hourTime);
    exp.setState(AvatarExpeditionState.AVATAR_EXPEDITION_DOING);   // ★ 初始 DOING
    exp.setStartTime(startTime);
    expeditionInfo.put(avatarGuid, exp);
}
```

→ **开始派遣 = 记录 (avatarGuid, expId, hourTime, startTime) + 状态 DOING + save**。
→ 不启动任何定时器 —— 完成检测靠 lazy。

---

## 5. Lazy 完成检测（核心，模式第 4 次确认）

`Player.java:1264-1279`（在 onLogin / 周期性触发）：
```java
// Expedition
var timeNow = Utils.getCurrentSeconds();
var needNotify = false;
for (ExpeditionInfo e : expeditionInfo.values()) {
    if (e.getState() == AvatarExpeditionState.AVATAR_EXPEDITION_DOING) {
        // ★ 检查是否到时间
        if (timeNow - e.getStartTime() >= e.getHourTime() * 60 * 60) {
            e.setState(AvatarExpeditionState.AVATAR_EXPEDITION_FINISH_WAIT_REWARD);
            needNotify = true;
        }
    }
}
if (needNotify) {
    this.save();
    this.sendPacket(new PacketAvatarExpeditionDataNotify(this.getExpeditionInfo()));
}
```

### 5.1 完成判定公式

```
timeNow - startTime >= hourTime * 60 * 60
   ↑ 已过秒数        ↑ 派遣时长(秒)
```

→ **不需要定时器！** 玩家登录 / 周期检查时**懒计算**：
- 当前时间 - 开始时间 ≥ 派遣时长 → 状态改 FINISH_WAIT_REWARD
- 否则保持 DOING

### 5.2 lazy evaluation 模式第 4 次确认

| 系统 | lazy 触发点 | 计算 |
|---|---|---|
| Resin (notes/50) | useResin / onLogin | `recharge = (now - nextRefresh) / rechargeTime` |
| Mail (notes/57) | save() | `expireTime < now → delete` |
| Shop (notes/58) | buyGoods | `now > nextRefreshTime → reset` |
| **Expedition (notes/59)** | onLogin / 周期 | `now - startTime >= hourTime*3600 → finish` |

→ **grasscutter 系统性偏好 lazy evaluation** —— **4 个时间相关系统全部"无后台定时任务，操作时懒计算"**。

→ 设计哲学：**用计算换调度** —— 不维护 N 个 timer/cron，玩家行动时一次性算出当前状态。

### 5.3 离线派遣

→ 玩家**离线时派遣继续**（startTime 已记录）：
- 玩家上线 → onLogin 触发 lazy 检测
- `now - startTime >= hourTime*3600` → 状态改 FINISH_WAIT_REWARD
- 发 PacketAvatarExpeditionDataNotify（红点）

→ 离线 12 小时派遣 12 小时点 → 上线即可领奖。**离线收益**机制。

---

## 6. 领取奖励：HandlerAvatarExpeditionGetRewardReq

```java
public void handle(GameSession session, byte[] header, AvatarExpeditionGetRewardReq req) {
    var player = session.getPlayer();
    
    ExpeditionInfo expInfo = player.getExpeditionInfo(req.getAvatarGuid());
    List<GameItem> items = new ArrayList<>();
    List<ExpeditionRewardDataList> expeditionRewardDataLists = 
        session.getServer().getExpeditionSystem()
            .getExpeditionRewardDataList().get(expInfo.getExpId());
    
    if (expeditionRewardDataLists != null) {
        expeditionRewardDataLists.stream()
            .filter(r -> r.getHourTime() == expInfo.getHourTime())   // ★ 按时长档匹配
            .map(ExpeditionRewardDataList::getRewards)
            .forEach(items::addAll);
    }
    
    player.getInventory().addItems(items);
    player.sendPacket(new PacketItemAddHintNotify(items, ActionReason.ExpeditionReward));
    
    player.removeExpeditionInfo(req.getAvatarGuid());   // ★ 领奖即删除
    player.save();
    session.send(new PacketAvatarExpeditionGetRewardRsp(player.getExpeditionInfo(), items));
}
```

### 6.1 奖励按时长档匹配

```java
.filter(r -> r.getHourTime() == expInfo.getHourTime())
```

→ 玩家选 20 小时 → 拿 20 小时档奖励（最多）。
→ 选 4 小时 → 拿 4 小时档奖励（最少）。

### 6.2 领奖即删除（无防重领标记）

```java
player.removeExpeditionInfo(req.getAvatarGuid());
```

→ 与 Mail (notes/57 `isAttachmentGot`) 不同 —— Expedition **直接删除 ExpeditionInfo**。
→ 删除后再请求 → `getExpeditionInfo` 返回 null → NPE 风险？

→ **潜在 bug**：没有 null 检查 + 没有 state 校验（不检查是否 FINISH_WAIT_REWARD）。
→ 理论上客户端在 DOING 状态点领奖也会发奖（信任客户端）。
→ 这是 grasscutter 的**反作弊薄弱点**——领奖未校验完成状态。

### 6.3 缺少完成校验（反作弊隐患）

对比 notes/58 Shop "Don't trust your users' input"：
- Shop：服务器重查配置 + 限购校验
- **Expedition：不检查 state == FINISH_WAIT_REWARD**

→ 理论上玩家 mod 客户端可在派遣刚开始就发 GetReward → 立即拿奖。
→ 私服可接受，但**正服必然校验 state**。

---

## 7. 提前召回：HandlerAvatarExpeditionCallBackReq

```java
public void handle(GameSession session, byte[] header, AvatarExpeditionCallBackReq req) {
    var player = session.getPlayer();
    
    for (int i = 0; i < req.getAvatarGuid().size(); i++) {
        player.removeExpeditionInfo(req.getAvatarGuid().get(i));   // ★ 直接删除, 无奖励
    }
    
    player.save();
    session.send(new PacketAvatarExpeditionCallBackRsp(player.getExpeditionInfo()));
}
```

→ **提前召回 = 删除 ExpeditionInfo, 不发奖励**。
→ 批量召回（List\<avatarGuid\>）。
→ 玩家想换角色队伍 → 召回外派角色（放弃奖励）。

---

## 8. 4 个 Handler

```
HandlerAvatarExpeditionAllDataReq   — 拉取所有派遣状态 (打开界面)
HandlerAvatarExpeditionStartReq     — 开始派遣 (记 startTime)
HandlerAvatarExpeditionGetRewardReq — 领奖 (按 hourTime 档) + 删除
HandlerAvatarExpeditionCallBackReq  — 提前召回 (删除, 无奖励)
```

→ 覆盖派遣全生命周期：查询 / 开始 / 领奖 / 召回。

---

## 9. 完整时序：派遣角色 20 小时

```
[玩家打开派遣界面]
   ↓ AvatarExpeditionAllDataReq
HandlerAvatarExpeditionAllDataReq:
   返回 player.expeditionInfo (所有外派状态)

[玩家选择角色 X + 派遣点 1001 + 20 小时]
   ↓ AvatarExpeditionStartReq { avatarGuid: X, expId: 1001, hourTime: 20 }
HandlerAvatarExpeditionStartReq:
   startTime = Utils.getCurrentSeconds()  // 例如 T=1000000
   player.addExpeditionInfo(X, 1001, 20, 1000000):
     ExpeditionInfo { state=DOING, expId=1001, hourTime=20, startTime=1000000 }
     expeditionInfo.put(X, info)
   player.save()
   PacketAvatarExpeditionStartRsp

[玩家下线]  (派遣继续, startTime 已记录)

[20 小时后玩家上线 (T=1072000)]
Player.onLogin / 周期检查:
   timeNow = 1072000
   for e in expeditionInfo:
     e.state == DOING ✓
     timeNow - startTime = 72000 秒
     hourTime * 3600 = 20 * 3600 = 72000 秒
     72000 >= 72000 ✓ → state = FINISH_WAIT_REWARD
   needNotify = true
   player.save()
   PacketAvatarExpeditionDataNotify  ← 红点提示

[玩家点领奖]
   ↓ AvatarExpeditionGetRewardReq { avatarGuid: X }
HandlerAvatarExpeditionGetRewardReq:
   expInfo = getExpeditionInfo(X)  // hourTime=20
   rewardDataLists = expeditionSystem.get(1001)
   items = filter(hourTime == 20).rewards  // 20 小时档奖励
   player.getInventory().addItems(items)  ← notes/38
   PacketItemAddHintNotify(items, ActionReason.ExpeditionReward)
   player.removeExpeditionInfo(X)  ← ★ 删除
   player.save()
   PacketAvatarExpeditionGetRewardRsp

[结果]
   角色 X 派遣槽空出 (可重新派遣)
   背包 + 20 小时档奖励 (如 25 个角色经验书)
```

---

## 10. 与其他系统的联动

### 10.1 Inventory (notes/38)

```java
player.getInventory().addItems(items);   // 批量加奖励
PacketItemAddHintNotify(items, ActionReason.ExpeditionReward);   // ActionReason 1075
```

### 10.2 Player 持久化 (notes/40)

```java
@Getter private Map<Long, ExpeditionInfo> expeditionInfo;   // 嵌入 Player 文档
```

### 10.3 战令 (notes/22 / notes/41)

→ 开始派遣 → `TRIGGER_START_AVATAR_EXPEDITION(308)`
→ 派遣完成 → `TRIGGER_AVATAR_EXPEDITION(303)`
→ "派遣 N 次"战令任务。

### 10.4 Player.onLogin (notes/40)

→ lazy 完成检测嵌在 onLogin 流程（与 Resin notes/50 一致）。

---

## 11. 设计模式总结

### 11.1 Lazy evaluation 第 4 次（系统性偏好确认）

```
Resin (notes/50): recharge = (now - nextRefresh) / rechargeTime
Mail (notes/57):  expireTime < now → delete
Shop (notes/58):  now > nextRefreshTime → reset
Expedition (本篇): now - startTime >= hourTime*3600 → finish
```

→ **grasscutter 4 个时间系统全用 lazy** —— 不是巧合，是**系统性架构决策**。
→ 收益：无 N 个 timer/cron，简化并发，离线友好。
→ 代价：状态"延迟感知"（玩家不操作就不更新）。

### 11.2 System = 配置容器

```
ExpeditionSystem 41 行只加载 JSON
逻辑分散在 Handler + Player
```

→ grasscutter 常见：System 薄，Handler/Player 厚。

### 11.3 领奖即删除（vs Mail 防重领）

```
Mail: isAttachmentGot 布尔标记
Expedition: removeExpeditionInfo 直接删
```

→ 删除天然防重领（再请求 → null）—— 但缺 null/state 校验是隐患。

### 11.4 离线收益

```
startTime 记录 → 离线继续 → 上线 lazy 检测
```

→ 时间锁系统的标准"离线友好"设计。

---

## 12. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 篡改 hourTime 拿高档奖励 | ✓ 部分有效 (start 时 hourTime 服务器存, 但不校验客户端传值合理性) |
| DOING 状态强领奖 | ✓ 有效 (无 state 校验!) |
| 重复领奖 | ✗ removeExpeditionInfo 删除后 null |
| 篡改 startTime | ✗ 服务器 Utils.getCurrentSeconds() |
| 伪造派遣完成 | ✗ lazy 检测服务器算 |

→ Expedition **反作弊较弱** —— **领奖不校验完成状态**是明显隐患（grasscutter 私服取舍，正服必校验）。

---

## 13. 关键收获

1. **112 行 (5 文件) + 4 Handler** = grasscutter 最小 GameSystem 之一
2. **ExpeditionSystem 41 行纯配置容器**：只加载 ExpeditionReward.json，无运行时逻辑
3. **System 薄 / Handler+Player 厚**：逻辑分散在 4 Handler + Player
4. **ExpeditionInfo 4 字段**：state / expId / hourTime / startTime
5. **2 状态机**：DOING → FINISH_WAIT_REWARD（领奖即删，无"已领"状态）
6. **Player.expeditionInfo Map<avatarGuid, ExpeditionInfo>**：嵌入 Player 文档
7. **ExpeditionReward.json 多档时长**：4/8/12/20 小时，越久奖励越多
8. **开始派遣**：记 startTime + state=DOING + save（无定时器）
9. **Lazy 完成检测**：`now - startTime >= hourTime*3600` → FINISH_WAIT_REWARD
10. **★ Lazy evaluation 模式第 4 次确认**：Resin/Mail/Shop/Expedition 全用——grasscutter 系统性架构决策
11. **离线收益**：startTime 记录，离线继续，上线 lazy 检测 + 红点
12. **领奖按 hourTime 档匹配**：filter(r.hourTime == expInfo.hourTime)
13. **领奖即删除**：removeExpeditionInfo（天然防重领，但缺 null/state 校验）
14. **缺完成状态校验（反作弊隐患）**：不检查 state == FINISH_WAIT_REWARD，理论上 DOING 可强领
15. **提前召回**：批量删除 ExpeditionInfo，无奖励（换队伍用）
16. **4 Handler**：AllData / Start / GetReward / CallBack
17. **联动**：Inventory addItems (ActionReason 1075) + 战令 TRIGGER_AVATAR_EXPEDITION(303/308)
18. **lazy 检测嵌 Player.onLogin**：与 Resin (notes/50) 同位置
19. **设计哲学**：用计算换调度——无 timer/cron，离线友好，代价是延迟感知
20. **反作弊较弱**：领奖不校验完成状态是明显隐患（私服取舍）

---

## 14. 一句话总结

> **ExpeditionSystem = 极简时间锁被动收益 (112 行) —— ExpeditionSystem 41 行纯配置容器 + ExpeditionInfo 4 字段 (state/expId/hourTime/startTime) + Player.expeditionInfo Map 持久化; 开始记 startTime, Lazy 完成检测 (now - startTime >= hourTime*3600 → FINISH_WAIT_REWARD) 嵌 onLogin; 领奖按 hourTime 档匹配 + removeExpeditionInfo 删除; lazy evaluation 模式第 4 次确认 (继 Resin/Mail/Shop).**
> 
> **设计哲学: grasscutter 4 个时间系统 (Resin/Mail/Shop/Expedition) 全用 lazy evaluation——系统性"用计算换调度"架构决策, 无 N 个 timer/cron, 离线友好, 代价是状态延迟感知 + 本系统领奖缺完成校验的反作弊隐患.**

---

**前置笔记**：
- notes/38 Inventory - addItems + ActionReason.ExpeditionReward(1075)
- notes/40 Player Manager - expeditionInfo Map + onLogin lazy 检测
- notes/41 事件总线 - TRIGGER_AVATAR_EXPEDITION(303) / TRIGGER_START_AVATAR_EXPEDITION(308)
- notes/46 GameServer - ExpeditionSystem 是 14 之一
- notes/50 Resin - lazy evaluation 模式 #1
- notes/57 Mail - lazy evaluation 模式 #2
- notes/58 Shop - lazy evaluation 模式 #3 + "不信任客户端"对比

**关联文件**：
- `ExpeditionSystem.java`(41) - 配置容器
- `ExpeditionInfo.java`(26) - @Entity 4 字段
- `ExpeditionRewardData.java`(15) / `ExpeditionRewardDataList.java`(20) / `ExpeditionRewardInfo.java`(10)
- `Player.java:790-805` - addExpeditionInfo/removeExpeditionInfo/getExpeditionInfo
- `Player.java:1264-1279` - lazy 完成检测
- `HandlerAvatarExpeditionStartReq` / `GetRewardReq` / `CallBackReq` / `AllDataReq`

**研究的源代码**: 112 行 Expedition 核心 + 4 Handler + Player lazy 检测。
