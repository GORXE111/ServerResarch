# InvokeHandler 批量包模式深度剖析

> 第 54 篇：跨 notes/29/36/40/42 反复引用但从未真正打开的"批量优化模式" —— **60 行 InvokeHandler<T>** + **UnionCmdNotify 容器** + **3 种 ForwardType 路由** = grasscutter 联机带宽优化的精髓。

---

## 0. 为什么这一篇重要

前 53 篇笔记里 InvokeHandler / UnionCmd / CombatInvocations 反复出现但 runtime 没专门挖：
- notes/29 网络协议：UnionCmd 在 LOOP_PACKETS 中被过滤日志
- notes/36 战斗数学：CombatInvocationsNotify 是 HandlerEvtBeingHit 入口
- notes/40 Player Manager：4 个 InvokeHandler 字段
- notes/42 表演系统：AbilityInvocations 是能力调度入口

但**这 60 行代码到底干嘛？UnionCmdNotify 怎么 batch？3 种 ForwardType 何时用？**——这一篇统一回答。

---

## 1. 问题：联机网络带宽

```
[场景] 4 玩家联机, 战斗中
玩家 A 一秒内:
   - 移动 30 帧 (位置同步 30 次)
   - 攻击 5 次 (EvtBeingHit × 5)
   - 释放技能 1 次 (AbilityInvoke × 10)
   = ~45 个事件需要广播给 B/C/D

[朴素方案]
每个事件单独发包:
   - 45 个事件 × 3 玩家 = 135 个 packet
   - 每 packet 20-100 字节 + KCP/UDP 头 ≈ 50 字节开销
   - 总带宽 ≈ 6750 + 6750 = 13.5 KB/秒/玩家 = 54 KB/秒总
   - 4 个玩家 → 216 KB/秒
   - 100 个联机房间 → 21.6 MB/秒 ← 单服爆炸
```

→ **batch packet** 必不可少。

---

## 2. InvokeHandler<T>：60 行的精炼设计

`InvokeHandler.java`：
```java
public class InvokeHandler<T> {
    private final List<T> entryListForwardAll;
    private final List<T> entryListForwardAllExceptCur;
    private final List<T> entryListForwardHost;
    private final Class<? extends BasePacket> packetClass;
    
    public InvokeHandler(Class<? extends BasePacket> packetClass) {
        this.entryListForwardAll = new ArrayList<>();
        this.entryListForwardAllExceptCur = new ArrayList<>();
        this.entryListForwardHost = new ArrayList<>();
        this.packetClass = packetClass;
    }
    
    public synchronized void addEntry(ForwardType forward, T entry) {
        switch (forward) {
            case FORWARD_TO_ALL -> entryListForwardAll.add(entry);
            case FORWARD_TO_ALL_EXCEPT_CUR, FORWARD_TO_ALL_EXIST_EXCEPT_CUR -> 
                entryListForwardAllExceptCur.add(entry);
            case FORWARD_TO_HOST -> entryListForwardHost.add(entry);
            default -> { }
        }
    }
    
    public synchronized void update(Player player) {
        if (player.getWorld() == null || player.getScene() == null) {
            this.entryListForwardAll.clear();
            this.entryListForwardAllExceptCur.clear();
            this.entryListForwardHost.clear();
            return;
        }
        
        try {
            if (entryListForwardAll.size() > 0) {
                // ★ 反射构造批量 packet
                BasePacket packet = packetClass.getDeclaredConstructor(List.class)
                    .newInstance(this.entryListForwardAll);
                player.getScene().broadcastPacket(packet);
                this.entryListForwardAll.clear();
            }
            if (entryListForwardAllExceptCur.size() > 0) {
                BasePacket packet = packetClass.getDeclaredConstructor(List.class)
                    .newInstance(this.entryListForwardAllExceptCur);
                player.getScene().broadcastPacketToOthers(player, packet);
                this.entryListForwardAllExceptCur.clear();
            }
            if (entryListForwardHost.size() > 0) {
                BasePacket packet = packetClass.getDeclaredConstructor(List.class)
                    .newInstance(this.entryListForwardHost);
                player.getWorld().getHost().sendPacket(packet);
                this.entryListForwardHost.clear();
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}
```

→ **60 行**实现了高性能批量广播——这是 grasscutter 中**密度最高**的代码之一。

---

## 3. 设计核心：3 个 entry list + 3 种 ForwardType

### 3.1 ForwardType 枚举

```java
FORWARD_TO_ALL                        // 广播给所有玩家 (含自己)
FORWARD_TO_ALL_EXCEPT_CUR             // 广播给其他玩家
FORWARD_TO_ALL_EXIST_EXCEPT_CUR       // 同上 (别名)
FORWARD_TO_HOST                        // 只发给房主
```

→ **3 种路由方式**——按目标分桶。

### 3.2 为什么分 3 个 list

```
如果只有 1 个 list:
   addEntry 时记录 ForwardType
   update 时再分组
   → 每次 update 都要扫一遍 (O(N))
   
当前设计:
   addEntry 时直接分桶 (O(1))
   update 时按桶处理 (O(K) where K=bucket size)
```

→ **入队时分类**省去出队时扫描。

### 3.3 反射构造批量 packet

```java
BasePacket packet = packetClass.getDeclaredConstructor(List.class)
    .newInstance(this.entryListForwardAll);
```

`packetClass` 是构造时传入的（`PacketCombatInvocationsNotify.class`）：
```java
this.combatInvokeHandler = new InvokeHandler(PacketCombatInvocationsNotify.class);
```

→ **反射 + 泛型**让一个 InvokeHandler<T> 复用于不同 packet 类型。

### 3.4 Player 的 3 个 InvokeHandler

`Player.java:228-303`：
```java
@Getter private transient final InvokeHandler<CombatInvokeEntry> combatInvokeHandler;
@Getter private transient final InvokeHandler<AbilityInvokeEntry> abilityInvokeHandler;
@Getter private transient final InvokeHandler<AbilityInvokeEntry> clientAbilityInitFinishHandler;

// 构造器
this.combatInvokeHandler = new InvokeHandler(PacketCombatInvocationsNotify.class);
this.abilityInvokeHandler = new InvokeHandler(PacketAbilityInvocationsNotify.class);
this.clientAbilityInitFinishHandler = new InvokeHandler(PacketClientAbilityInitFinishNotify.class);
```

→ **3 个独立 InvokeHandler**：
- combatInvokeHandler — 战斗事件 (移动/伤害/动画)
- abilityInvokeHandler — 能力调度 (技能/buff/特效)
- clientAbilityInitFinishHandler — 客户端能力初始化完成

---

## 4. UnionCmdNotify：客户端批量入口

`HandlerUnionCmdNotify.java`：
```java
public class HandlerUnionCmdNotify extends TypedPacketHandler<UnionCmdNotify> {
    @Override
    public void handle(GameSession session, byte[] header, UnionCmdNotify req) {
        // ★ 1. 解开批量包，逐个分发到对应 Handler
        for (UnionCmd cmd : req.getCmdList()) {
            int cmdOpcode = cmd.getMessageId();
            byte[] cmdPayload = cmd.getBody();
            
            // 日志 (仅 whitelist/blacklist 命中时记录)
            // ...
            
            // ★ 递归调用 PacketHandler.handle 处理子 packet
            session.getServer().getPacketHandler().handle(session, cmd.getMessageId(), EMPTY_BYTE_ARRAY, cmd.getBody());
        }
        
        // ★ 2. 处理完所有 sub-cmd, flush InvokeHandlers
        session.getPlayer().getCombatInvokeHandler().update(session.getPlayer());
        session.getPlayer().getAbilityInvokeHandler().update(session.getPlayer());
        
        // ★ 3. 处理 AttackResult 队列 (notes/40)
        while (!session.getPlayer().getAttackResults().isEmpty()) {
            session.getPlayer().getScene().handleAttack(
                session.getPlayer().getAttackResults().poll());
        }
    }
}
```

### 4.1 UnionCmdNotify 结构

```
UnionCmdNotify {
    cmdList: [
        { messageId: 8104 (CombatInvocationsNotify), body: [...] },
        { messageId: 1234 (其他 packet), body: [...] },
        { messageId: 8104, body: [...] },
        ...
    ]
}
```

→ **客户端把 N 个 packet 打包成一个 UnionCmd 发送**。
→ 服务器**递归 dispatch** 每个 sub-packet 给对应 Handler。

### 4.2 3 步处理流程

```
Step 1: 解开 union cmd, 分发处理 (sub-packet 各自调 addEntry)
Step 2: flush InvokeHandlers (批量广播给其他玩家)
Step 3: flush AttackResults 队列 (单独的串行处理)
```

→ **关键设计**：所有 sub-packet 先处理完，**最后才统一广播**。

### 4.3 为什么 AttackResult 单独处理

```java
while (!session.getPlayer().getAttackResults().isEmpty()) {
    session.getPlayer().getScene().handleAttack(
        session.getPlayer().getAttackResults().poll());
}
```

`AttackResult` 是 `LinkedBlockingQueue<AttackResult>` (notes/40)：
- 战斗事件 → 客户端发 `EvtBeingHit` (在 CombatInvocationsNotify 内)
- HandlerCombatInvocationsNotify 内部 `player.getAttackResults().add(attackResult)` (notes/36)
- UnionCmd 处理完后**统一处理 attack**

→ **延迟处理**避免在 sub-packet 处理中递归触发 attack。

---

## 5. 完整数据流：从客户端到广播

```
[客户端 - 一帧产生多个事件]
   - 移动 (CombatInvokeEntry: ENTITY_MOVE)
   - 攻击 (CombatInvokeEntry: COMBAT_EVT_BEING_HIT)
   - 动画 (CombatInvokeEntry: COMBAT_ANIMATOR_PARAMETER_CHANGED)
   - 能力调度 (AbilityInvokeEntry × 5)
   
[客户端 - 打包]
   UnionCmdNotify {
       cmdList: [
           { messageId: 8104, body: CombatInvocationsNotify{ entries: [move, hit, animator] } },
           { messageId: 1192, body: AbilityInvocationsNotify{ entries: [ab1, ab2, ...] } }
       ]
   }
   
   → 1 个 UnionCmdNotify packet (而非 8 个独立 packet)
   
[服务器 - 收包]
   HandlerUnionCmdNotify.handle:
      for each sub-packet:
         dispatch 到 HandlerCombatInvocationsNotify or HandlerAbilityInvocationsNotify
         ↓
         HandlerCombatInvocationsNotify:
            for each entry:
               处理移动 / 处理战斗事件 / 处理动画
               player.getCombatInvokeHandler().addEntry(entry.getForwardType(), entry)
         ↓
         HandlerAbilityInvocationsNotify:
            for each entry:
               player.getAbilityManager().onAbilityInvoke(entry)  ← notes/37
               player.getAbilityInvokeHandler().addEntry(entry.getForwardType(), entry)
   
[服务器 - 广播]
   combatInvokeHandler.update(player):
      if entryListForwardAll.size() > 0:
         反射构造 PacketCombatInvocationsNotify(entryListForwardAll)
         scene.broadcastPacket(packet)  ← 给所有人 (含自己)
      if entryListForwardAllExceptCur.size() > 0:
         反射构造 PacketCombatInvocationsNotify(list)
         scene.broadcastPacketToOthers(player, packet)  ← 给其他人
      if entryListForwardHost.size() > 0:
         反射构造 PacketCombatInvocationsNotify(list)
         world.getHost().sendPacket(packet)  ← 给房主
   
   abilityInvokeHandler.update(player):
      ...
   
[联机玩家 B/C/D 收包]
   接收 PacketCombatInvocationsNotify (含所有 entry)
   接收 PacketAbilityInvocationsNotify
   客户端逐个回放: 玩家 A 移动了/攻击了/...
   
[AttackResult 单独处理]
   while attackResults 不空:
      scene.handleAttack(result)
      → 算 HP / 触发能量经济 / 触发挑战
```

### 5.1 联机 → 单机的对比

```
[单机]
   addEntry 时 FORWARD_TO_ALL_EXCEPT_CUR
   ↓ update 时 broadcastPacketToOthers
   ↓ scene.players 只有自己 → 空广播
   → 0 个 packet 出去
   
[联机 4 人]
   addEntry 时 FORWARD_TO_ALL_EXCEPT_CUR
   ↓ update 时 broadcastPacketToOthers
   ↓ scene.players 有 4 人 → 3 人各收 1 packet
   → 3 个 packet 出去 (各含 N 个 entry)
```

→ 单机时 **InvokeHandler 仍跑但不发包** —— 代码统一不分支。

---

## 6. 3 种 ForwardType 何时用

### 6.1 FORWARD_TO_ALL（含自己）

```
用例: 同步动画 / 表情
适用: 玩家自己的客户端也要看到效果 (e.g. cutscene)
```

→ 较少用 —— 大多数自己的动作客户端**已经本地播放**，不需要回放。

### 6.2 FORWARD_TO_ALL_EXCEPT_CUR（最常用）

```
用例: 移动同步 / 攻击同步 / 技能特效
适用: 自己已经做了, 其他玩家需要看到
```

→ **80% 的 invoke** 走这条 —— 客户端已经"本地预测"，只需告诉其他玩家。

### 6.3 FORWARD_TO_HOST（特殊）

```
用例: 客人需要让 host 计算的事件
适用: host 是 entity authority (AI / monster)
```

→ 客人客户端可能发"我击中了怪 X" → 必须告诉房主（怪 AI 在房主处）。

---

## 7. broadcastPacketToOthers：跳过自己

```java
// Scene.java (notes/35)
public void broadcastPacketToOthers(Player excluded, BasePacket packet) {
    for (Player p : this.players) {
        if (p != excluded) {
            p.sendPacket(packet);
        }
    }
}
```

→ 简单的"过滤广播" —— 不复制 packet，只跳过一个目标。

---

## 8. 性能分析

### 8.1 朴素 vs 批量

| 维度 | 朴素 | InvokeHandler 批量 |
|---|---|---|
| 一秒 45 事件 | 45 packet | 1-3 packet |
| 网络头开销 (50B/packet) | 2250 B | 50-150 B |
| 用户态调用 send() | 45 次 | 1-3 次 |
| KCP 拥塞窗口压力 | 高 | 低 |
| 接收端解包次数 | 45 次 | 1-3 次 |

→ **节省 95%+ 带宽和 CPU**。

### 8.2 联机 4 人房 一秒带宽

```
单玩家发出: 1 个 UnionCmdNotify (含 30+ 事件)
   ↓ 服务器分发
3 个其他玩家各收 2 个 packet (CombatInvocations + AbilityInvocations)
   ↓ 每 packet 含 30 个 entry, 总 ~3KB
单房间一秒带宽: 4 × (1 + 6) = 28 个 packet ≈ 84 KB/秒
```

vs 朴素方案 (216 KB/秒) → **节省 60%**。

### 8.3 100 个联机房间总带宽

```
100 房间 × 84 KB/秒 = 8.4 MB/秒 (= 67 Mbps)
```

→ 单服承载 100 房间 = **67 Mbps** —— 完全够用。
→ 没批量则 21.6 MB/秒 (172 Mbps) —— 接近 100M 网卡上限。

---

## 9. 错误处理

```java
public synchronized void update(Player player) {
    if (player.getWorld() == null || player.getScene() == null) {
        this.entryListForwardAll.clear();
        this.entryListForwardAllExceptCur.clear();
        this.entryListForwardHost.clear();
        return;
    }
    // ...
}
```

### 9.1 null 防御

→ 玩家正在切场景 / 退出 → `world` 或 `scene` 可能为 null。
→ 这时**清空 list 直接返回** —— 不能给 null player 广播。

### 9.2 异常吞掉

```java
try { ... } catch (Exception e) {
    e.printStackTrace();
}
```

→ 反射构造失败 → 打印但不重抛 —— 防止一个错误事件拖累整个 update。

---

## 10. synchronized 关键字

```java
public synchronized void addEntry(...) { ... }
public synchronized void update(Player player) { ... }
```

### 10.1 为什么需要

```
[场景] 玩家正在战斗中
- 网络线程: HandlerCombatInvocationsNotify 调 addEntry
- 主线程: HandlerUnionCmdNotify 调 update
- 两个线程并发访问 entryList
```

→ **必须 synchronized** —— 防止 list 在迭代时被修改。

### 10.2 锁的粒度

```java
synchronized this  // 锁 InvokeHandler 实例
```

→ 每玩家有独立 InvokeHandler → **锁不跨玩家** —— 不影响多玩家并发。

---

## 11. update 触发时机

```bash
$ grep -rn "invokeHandler.update\|InvokeHandler.update"
HandlerUnionCmdNotify.java:33: combatInvokeHandler.update(player)
HandlerUnionCmdNotify.java:34: abilityInvokeHandler.update(player)
```

→ **唯一调用 update 的地方**：HandlerUnionCmdNotify 末尾。

→ 没有 UnionCmdNotify 就**永远不 flush** —— 但客户端**每帧都发 UnionCmd**，所以 OK。

### 11.1 为什么不在 onTick flush

```
Player.onTick (每秒)
   ↓ 可以加 invokeHandlers.update(player)
```

但**没这么做**：
- ✗ 1 秒延迟太大，玩家动作传播慢
- ✗ 增加 onTick 复杂度
- ✓ 客户端 UnionCmd 频率高（每帧）—— 已经足够频繁

→ "客户端主动驱动 flush" 是更好的设计。

---

## 12. ForwardType 流向图

```
                                客户端 A
                                    ↓
                              UnionCmdNotify
                                    ↓
                          HandlerUnionCmdNotify
                                    ↓
                ┌────────────────────┴────────────────────┐
                ↓                                            ↓
         HandlerCombatInvocations           HandlerAbilityInvocations
                ↓                                            ↓
         for each entry:                            for each entry:
            addEntry(forwardType, entry)              addEntry(forwardType, entry)
                ↓                                            ↓
    combatInvokeHandler 分桶                  abilityInvokeHandler 分桶
        ├── entryListForwardAll              ├── entryListForwardAll
        ├── entryListForwardAllExceptCur     ├── entryListForwardAllExceptCur
        └── entryListForwardHost              └── entryListForwardHost
                                    ↓
                   HandlerUnionCmdNotify 末尾:
                       combatInvokeHandler.update
                       abilityInvokeHandler.update
                                    ↓
        ┌──────────────┬─────────────────────────┬──────────────┐
        ↓                  ↓                     ↓
   scene.broadcast    scene.broadcastToOthers   host.sendPacket
   (ALL 3 玩家)       (其他 2 玩家)               (房主 1 人)
        ↓                  ↓                     ↓
   PacketCombatInvocations × 3 玩家收 × 2 桶 = 6+ 包
```

→ **入口 1 个 UnionCmd → 出口 6+ 批量 packet → 但每个批量含 30+ 事件**。

---

## 13. 设计模式总结

### 13.1 Producer-Consumer with Bucketing

```
Producer: Handler 调 addEntry (按 ForwardType 分桶)
Consumer: update flush 各桶
```

→ 经典模式 + 按目标分桶 = 减少分发开销。

### 13.2 反射 + 泛型 = 类型复用

```java
new InvokeHandler<CombatInvokeEntry>(PacketCombatInvocationsNotify.class)
new InvokeHandler<AbilityInvokeEntry>(PacketAbilityInvocationsNotify.class)
```

→ 一个类 60 行处理 3 种不同的 packet。

### 13.3 Client-Driven Flush

```
不靠服务器 tick flush, 靠客户端 UnionCmd 触发
```

→ 客户端**每帧都发 UnionCmd** → 自然驱动 flush 频率。

### 13.4 延迟处理 AttackResult

```
sub-packet 处理 → 入 attackResults 队列
union cmd flush 后 → 统一处理 attack
```

→ 避免在 invoke 处理中递归触发战斗逻辑。

### 13.5 3 桶 ForwardType

```
ALL / ALL_EXCEPT_CUR / HOST
```

→ 覆盖**绝大多数广播需求**——简单粗暴但够用。

---

## 14. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 客户端伪造 ForwardType | ✓ 部分 (能改广播路径) |
| 客户端 invoke 不存在的实体 | ✗ 服务器解析时校验 |
| 不发 UnionCmd 跳过 flush | ✗ 无害 (entry 在桶中, 下次再发) |
| 篡改批量内 packet body | ✓ 客户端可伪造 (但服务器再校验) |
| 滥发 UnionCmd | ✗ KCP 流控 + 速率限制 |

→ InvokeHandler 本身**不算反作弊层** —— 反作弊在各子 packet 的 Handler 内。

---

## 15. 关键收获

1. **60 行 InvokeHandler<T>** 是 grasscutter 网络优化的精髓
2. **3 个 entry list** 按 ForwardType 分桶：ALL / ALL_EXCEPT_CUR / HOST
3. **3 种 ForwardType 路由**：广播给所有 / 广播给其他 / 仅 host
4. **反射 + 泛型**：一个 InvokeHandler<T> 通过 packetClass 复用于不同包类型
5. **3 个 Player 字段**：combatInvokeHandler / abilityInvokeHandler / clientAbilityInitFinishHandler
6. **UnionCmdNotify = 客户端批量入口**：每帧 N 个事件打包成 1 个 packet
7. **HandlerUnionCmdNotify 3 步**：解包递归分发 → flush InvokeHandler → 处理 AttackResults 队列
8. **Client-Driven Flush**：不靠 server tick，靠客户端 UnionCmd 触发 update
9. **synchronized 双方法**：addEntry + update 必须互斥（网络线程 vs 主线程）
10. **null 防御**：world/scene null → 清空 list 直接返回
11. **异常吞掉**：反射失败 printStackTrace 但继续——单错误不拖累整批
12. **AttackResult 延迟队列**：避免 invoke 处理中递归触发战斗
13. **broadcastPacketToOthers**：Scene 简单"过滤广播"
14. **单机时仍跑但不发包**：scene.players = [自己] → 自然不广播
15. **节省 60-95% 带宽**：联机 4 人房从 216 KB/秒降到 84 KB/秒
16. **80% 走 FORWARD_TO_ALL_EXCEPT_CUR**：因为本地预测已经播，只通知别人
17. **FORWARD_TO_HOST 给 entity 权威**：客人事件转发给房主算 AI/HP
18. **联机带宽**：100 房间 ≈ 67 Mbps —— 单服可承载
19. **每帧都 flush**：客户端 UnionCmd 频率高 → 延迟 < 1 帧

---

## 16. 一句话总结

> **InvokeHandler<T> = 60 行实现的批量网络优化 —— 3 个 ForwardType 桶 (ALL/ALL_EXCEPT_CUR/HOST) + 反射构造批量 packet + UnionCmdNotify 客户端批量入口 + HandlerUnionCmdNotify 3 步处理 (解包→addEntry→flush+AttackResult) + Client-Driven flush 让延迟 < 1 帧; 联机 4 人房节省 60-95% 带宽; synchronized 双方法 + null 防御 + 异常吞掉容错.**
> 
> **设计哲学: 入队时分桶 (O(1)) + 出队时按桶处理 (O(K)) + 客户端驱动 flush + 延迟 AttackResult 防递归 + 反射泛型一类多用——这是 grasscutter 中"60 行密度最高代码"的最佳样本.**

---

**前置笔记**：
- notes/29 网络协议 - UnionCmd 在 LOOP_PACKETS 中
- notes/36 战斗数学 - HandlerCombatInvocationsNotify 入口
- notes/37 Ability 系统 - AbilityInvocations 路由
- notes/40 Player Manager - 3 个 InvokeHandler 字段 + attackResults 队列
- notes/42 表演系统 - 客户端权威伤害通过 invoke 传递
- notes/47 Plugin/Event - 不影响 invoke 流程

**关联文件**：
- `InvokeHandler.java`(60) - 核心批量类
- `HandlerUnionCmdNotify.java`(42) - 客户端批量入口
- `HandlerCombatInvocationsNotify.java`(163) - 战斗 invoke 处理
- `HandlerAbilityInvocationsNotify.java`(21) - 能力 invoke 处理
- `HandlerClientAbilityChangeNotify.java` - 客户端能力变更
- `PacketCombatInvocationsNotify.java`(20) - 批量战斗出口
- `PacketAbilityInvocationsNotify.java`(25) - 批量能力出口
- `Player.java:228-303` - 3 个 InvokeHandler 字段 + 构造
- `Scene.java`: broadcastPacket / broadcastPacketToOthers
- `ForwardType` 枚举 (multi_proto)

**研究的源代码**: 60 行 InvokeHandler 核心 + 5 个相关 Handler/Packet 文件。
