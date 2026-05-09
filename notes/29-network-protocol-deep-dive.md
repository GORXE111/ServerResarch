# 网络协议层深度剖析

> 第 29 篇：从 KCP 到 Protobuf 的**最底层支撑**——一切游戏业务都跑在这条管道上

---

## 0. 为什么这一篇重要

前 28 篇笔记都是**业务逻辑**：任务、对话、奖励、家园、抽卡 …… 但所有这些 packet **怎么传到客户端**？怎么**编码、加密、防篡改**？

这一篇专门解剖**网络协议层**——它是支撑一切的基础设施：

```
[客户端]                      [服务器]
  ↓                              ↓
应用层（业务）       任务系统、对话、奖励 …  ← 前 28 篇笔记
  ↓                              ↓
表示层（编解码）         Protobuf 序列化     ← 本篇
  ↓                              ↓
安全层（加密/校验）   XOR 加密 + 魔数 + RSA   ← 本篇
  ↓                              ↓
传输层（可靠 UDP）     KCP（基于 UDP）        ← 本篇
  ↓                              ↓
网络层               IP / 互联网
```

---

## 1. 整体技术栈一览

| 层 | 选型 | 文件 |
|---|---|---|
| 网络框架 | Netty + KCP-Java | `kcp.highway.Ukcp` |
| 可靠传输 | KCP（UDP 之上的可靠协议）| `GameSessionManager.java` |
| 包格式 | 自定义二进制 + Protobuf payload | `BasePacket.java` |
| 序列化 | Protobuf（multi_proto-gi）| `org.anime_game_servers.multi_proto.gi` |
| 加密 | XOR 流密码（DispatchKey/SecretKey 双层）| `Crypto.java` |
| 握手 | RSA-2048 签名 + 双向种子交换 | `HandlerGetPlayerTokenReq.java` |
| 路由 | 反射 + opcode → handler 映射 | `GameServerPacketHandler.java` |

**关键观察**：**没有 TLS/SSL**。这是有意的——见 §10 取舍分析。

---

## 2. KCP：为什么不用 TCP？

### 2.1 KCP 是什么

`kcp.highway.Ukcp` 是 KCP 协议的 Java 实现。KCP 是**基于 UDP 的可靠传输协议**，特点：

| 维度 | TCP | KCP |
|---|---|---|
| 底层 | 内核态 | 用户态（运行在 UDP 之上）|
| 拥塞控制 | 默认 Cubic（保守） | 可配置（更激进）|
| 延迟 | 较高（重传慢启动）| 低 30-40% |
| 流控 | 严格 | 可以"宁可多发，确保到达" |
| 队头阻塞 | 严重 | 轻微 |
| 实现层 | 操作系统 | 应用层库 |

### 2.2 为什么原神选 KCP

**实时游戏对延迟敏感**：
- 角色移动同步、技能释放、伤害结算——这些都不能等
- TCP 的 RTT 抖动会让玩家感受到"瞬间卡顿"
- KCP 通过**激进重传 + 用户态拥塞控制**牺牲带宽换延迟

**手游网络环境复杂**：
- 4G/5G 切换、地铁里弱网、Wi-Fi 抖动
- TCP 在丢包率 5-10% 时性能急剧下降
- KCP 在 30% 丢包率下仍能保持可用

### 2.3 服务端 KCP 处理代码

`GameSessionManager.java:18-93`：

```java
private static final KcpListener listener = new KcpListener(){
    @Override
    public void onConnected(Ukcp ukcp) {
        // 客户端 UDP 包打到服务器, KCP 库识别为新连接
        GameSession conversation = new GameSession(server);
        conversation.onConnected(new KcpTunnel(){
            @Override public void writeData(byte[] bytes) {
                ByteBuf buf = Unpooled.wrappedBuffer(bytes);
                ukcp.write(buf);   // ← 数据通过 KCP 通道写出
            }
            @Override public void close() { ukcp.close(); }
            @Override public int getSrtt() { return ukcp.srtt(); }
        });
        sessions.put(ukcp, conversation);
    }
    
    @Override
    public void handleReceive(ByteBuf buf, Ukcp kcp) {
        byte[] byteData = Utils.byteBufToArray(buf);
        logicThread.execute(() -> {                     // ← 切到逻辑线程
            sessions.get(kcp).handleReceive(byteData);  // ← 进入业务处理
        });
    }
};
```

**架构亮点**：
- KCP 库负责**重传、确认、滑动窗口**——业务代码只看"已组装好的字节流"
- `logicThread.execute(...)` 把 IO 线程和逻辑线程隔离——网络抖动不会卡逻辑
- `sessions: ConcurrentHashMap<Ukcp, GameSession>`——KCP 连接对象作为 Session 主键

### 2.4 SRTT（Smoothed Round-Trip Time）

`getSrtt()` 直接暴露 KCP 内部统计的**平滑 RTT**——业务可以拿这个数：
- 决定是否给客户端降级（例如减少粒子效果同步）
- 服务器端 ping 显示
- 多人协作中的"延迟感知"逻辑

---

## 3. 包结构：自定义二进制头 + Protobuf 体

### 3.1 一个包的完整字节布局

`BasePacket.build()` (`BasePacket.java:89-122`)：

```
┌──────────────────────────────────────────────────────────────┐
│ 0x4567                          (uint16, 2 bytes) 起始魔数      │
├──────────────────────────────────────────────────────────────┤
│ opcode                          (uint16, 2 bytes) 包类型 ID   │
├──────────────────────────────────────────────────────────────┤
│ header_length                   (uint16, 2 bytes)             │
├──────────────────────────────────────────────────────────────┤
│ payload_length                  (uint32, 4 bytes)             │
├──────────────────────────────────────────────────────────────┤
│ header (PacketHead protobuf)    (变长, sequence/timestamp)    │
├──────────────────────────────────────────────────────────────┤
│ payload (业务 protobuf)         (变长, 真正的请求/响应)       │
├──────────────────────────────────────────────────────────────┤
│ 0x89AB                          (uint16, 2 bytes) 结束魔数      │
└──────────────────────────────────────────────────────────────┘
                       整包 XOR 加密
```

### 3.2 魔数双锚（0x4567 / 0x89AB）

- **0x4567** = `17767`（开头）
- **0x89AB** = `-30293`（结尾，因为 Java 没有无符号 short，看着是负数）

**作用**：双锚校验
1. 解密后第一个 short 不是 0x4567 → 密钥错误（错版本/错时机）
2. 末尾不是 0x89AB → 包损坏或长度声明错误

`GameSession.handleReceive()` 严格校验：

```java
int const1 = packet.readShort();
if (const1 != 17767) {
    Grasscutter.getLogger().error("Bad Data Package: got {} ,expect 17767", const1);
    return;   // 立即丢弃整个 UDP 段
}
// ... 读 opcode/length/header/payload ...
int const2 = packet.readShort();
if (const2 != -30293) { return; }
```

**为什么需要双锚**？
- KCP 已经保证字节流可靠 ✓
- 但如果**密钥错了**怎么办？XOR 出来全是噪音，得有方法快速判断
- 双锚 = 4 字节的"已知明文"——错密钥下匹配概率 ≈ 1/2³² ≈ 0
- 比 CRC 校验快（无需算 hash）

### 3.3 PacketHead 是什么

`buildHeader()` 创建 `PacketHead` protobuf：
```java
val packetHead = new PacketHead();
packetHead.setClientSequenceId(clientSequence);    // 单调递增的客户端序列号
packetHead.setSentMs(System.currentTimeMillis());  // 发送时间戳
```

**用途**：
- `clientSequenceId` —— 用于配对 Req/Rsp（客户端发出 100，服务器响应携带 100）
- `sentMs` —— 用于计算单边延迟、调试时序
- 不是所有包都需要 header（`shouldBuildHeader()` 控制）—— Notify 包通常不带

---

## 4. 加密：XOR + 双密钥

### 4.1 双密钥设计

`Crypto.java:20-25`：
```java
public static byte[] DISPATCH_KEY;   // 调度密钥（登录前用）
public static byte[] DISPATCH_SEED;
public static byte[] ENCRYPT_KEY;    // 会话密钥（登录后用）
public static long ENCRYPT_SEED = 11468049314633205968L;  // ← 长长的种子
```

### 4.2 时机切换

```
[客户端连接] ──→ [DispatchKey 加密阶段] ──→ [GetPlayerTokenReq/Rsp] ──→ [SecretKey 加密阶段]
                  ↑                          ↑                          ↑
                  整个握手过程              session.useSecretKey=true   后续所有业务包
```

代码体现 (`GameSession.java:174-176`)：
```java
public void handleReceive(byte[] bytes) {
    Crypto.xor(bytes, useSecretKey() ? Crypto.ENCRYPT_KEY : Crypto.DISPATCH_KEY);
    //                ↑                  ↑
    //              登录后用            登录前用
}
```

### 4.3 为什么是 XOR

```java
public static void xor(byte[] packet, byte[] key) {
    for (int i = 0; i < packet.length; i++) {
        packet[i] ^= key[i % key.length];   // 循环密钥
    }
}
```

**优点**：
- ✓ 加密 = 解密（同一函数对称）
- ✓ CPU 极快（手机/Switch 上不卡）
- ✓ 实现简单，几行代码
- ✓ 没有 IV 管理麻烦

**缺点**：
- ✗ 数学上**已知明文攻击秒破**：知道密文 c 和明文 p，key = c ^ p
- ✗ 密钥重用必死（同一个 key 加密多个包，可以差分破解）

**那为什么还用？**
- key 长度足够长（Buffer 形式 4096 字节级别），不太容易短期内推出全密钥
- 真正的安全在**握手协议**里（RSA + 随机种子，§5）
- XOR 只是**混淆而非加密**——目的是劝退脚本小子，不是抵抗国家级攻击者
- 性能优先于密码学正确性——这是**游戏行业的普遍取舍**

### 4.4 ENCRYPT_KEY 的生成

`Crypto.java:73-77`：
```java
public static byte[] createSessionKey(int length) {
    byte[] bytes = new byte[length];
    secureRandom.nextBytes(bytes);
    return bytes;
}
```

每次会话用 `SecureRandom` 生成新的 key —— **不是固定的**。
但 `ENCRYPT_SEED` 是固定的（写死在代码里），用于**还原 key**：客户端和服务器都从同样的种子推出相同的 key 流。

**ENCRYPT_SEED_BUFFER 是预计算的种子表**——存储在 `keys/secretKeyBuffer.bin`，握手时下发给客户端。

---

## 5. 登录握手：RSA + 双向种子交换

### 5.1 握手时序

```
客户端                                  服务器
  │                                       │
  │ ─────[1. UDP 连接 + KCP 建立]─────→  │
  │                                       │
  │ ←────[2. DispatchKey 加密阶段]────→ │
  │                                       │
  │ ─[3. GetPlayerTokenReq（含 RSA 加密 client_seed）]─→
  │                                       │
  │                              [4. RSA 解密拿到 client_seed]
  │                              [5. 计算 server_seed = client_seed XOR ENCRYPT_SEED]
  │                              [6. RSA 加密 server_seed (用客户端公钥)]
  │                              [7. SHA256-RSA 签名]
  │                                       │
  │ ←─[8. GetPlayerTokenRsp（serverRandKey + sign）]──
  │                                       │
  │ [9. 客户端验签 + 解密拿 server_seed]    │
  │                                       │
  │ ──────[10. 切换 SecretKey 加密]─────→ │
  │                                       │
  │ ─────[11. PlayerLoginReq + 后续业务]─→ │
```

### 5.2 关键代码

`HandlerGetPlayerTokenReq.java:180-212`：

```java
if (req.getKeyId() > 0) {
    Cipher cipher = Cipher.getInstance("RSA/ECB/PKCS1Padding");
    cipher.init(Cipher.DECRYPT_MODE, Crypto.CUR_SIGNING_KEY);
    
    // 1. 解密客户端发来的 client_seed（用服务器私钥）
    var client_seed_encrypted = Utils.base64Decode(req.getClientRandKey());
    var client_seed = ByteBuffer.wrap(cipher.doFinal(client_seed_encrypted)).getLong();
    
    // 2. 服务器和客户端各持有 ENCRYPT_SEED → XOR 后双方得到同一个 session_seed
    byte[] seed_bytes = ByteBuffer.wrap(new byte[8])
        .putLong(Crypto.ENCRYPT_SEED ^ client_seed)
        .array();
    
    // 3. 用客户端公钥加密 session_seed 发回去
    cipher.init(Cipher.ENCRYPT_MODE, Crypto.EncryptionKeys.get(req.getKeyId()));
    var seed_encrypted = cipher.doFinal(seed_bytes);
    
    // 4. SHA256-RSA 签名（服务器私钥签）防篡改
    Signature privateSignature = Signature.getInstance("SHA256withRSA");
    privateSignature.initSign(Crypto.CUR_SIGNING_KEY);
    privateSignature.update(seed_bytes);
    
    rsp.setServerRandKey(Utils.base64Encode(seed_encrypted));
    rsp.setSign(Utils.base64Encode(privateSignature.sign()));
}
```

### 5.3 为什么需要这个握手

**目标**：客户端和服务器**协商出一个会话密钥**，且：
- ✓ 中间人无法窃听（RSA 公钥加密保护种子）
- ✓ 中间人无法篡改（SHA256-RSA 签名保护种子）
- ✓ 即使长期密钥泄漏，老会话仍安全（每次随机种子）—— 类似 forward secrecy

**这是经典的 Diffie-Hellman 思路**（虽然实现是 RSA + XOR）：双方各自贡献一半，混合后得到只有他们俩知道的秘密。

### 5.4 KeyId 多版本支持

```java
EncryptionKeys = new HashMap<>();   // Map<Integer, PublicKey>
// 文件名 "{N}_Pub.der" → keyId=N
```

不同游戏版本用不同的客户端公钥（`2_Pub.der`, `3_Pub.der`, …）——服务器维护一组公钥，按客户端声明的 `keyId` 选用对应的。**客户端版本升级 = 换 keyId**。

---

## 6. 会话状态机：4 个 + 1 个

### 6.1 状态枚举

`GameSession.SessionState`：
```java
public enum SessionState {
    INACTIVE,             // 已断连
    WAITING_FOR_TOKEN,    // 刚连上, 等 GetPlayerTokenReq
    WAITING_FOR_LOGIN,    // 已认证, 等 PlayerLoginReq
    PICKING_CHARACTER,    // 新号, 等选生日设角色
    ACTIVE,               // 正常游戏中
    ACCOUNT_BANNED        // 被封, 拒绝任何包
}
```

### 6.2 状态转移

```
[创建 GameSession]
        ↓
   WAITING_FOR_TOKEN ──────[GetPlayerTokenReq 成功]──────→ WAITING_FOR_LOGIN
        ↓ (失败)                                                  ↓
     close()                                          [PlayerLoginReq, 老号]
                                                                  ↓
                                                                ACTIVE
                                                                  ↑
                                              [SetPlayerBornDataReq] (新号)
                                                                  ↑
                                                          PICKING_CHARACTER
                                                                  ↑
                                                  [PlayerLoginReq, 新号]
                                                                  ↑
                                                          WAITING_FOR_LOGIN
```

### 6.3 状态机的强制执行

`GameServerPacketHandler.handle()`：
```java
if ("PingReq".equals(packageName)) {
    // Ping 任何状态都允许
} else if ("GetPlayerTokenReq".equals(packageName)) {
    if (state != SessionState.WAITING_FOR_TOKEN) return;
    // 不在等 token 状态? 拒绝!
} else if (state == SessionState.ACCOUNT_BANNED) {
    session.close();
    return;
} else if ("PlayerLoginReq".equals(packageName)) {
    if (state != SessionState.WAITING_FOR_LOGIN) return;
} else if ("SetPlayerBornDataReq".equals(packageName)) {
    if (state != SessionState.PICKING_CHARACTER) return;
} else {
    if (state != SessionState.ACTIVE) return;   // ← 大部分包都要求 ACTIVE
}
```

**这是一道纵深防御**：
- 攻击者伪造一个 `EnterSceneReq`，但 session 还在 `WAITING_FOR_TOKEN` → 直接丢弃
- 即使绕过了加密，也必须按状态顺序走

---

## 7. opcode 路由：反射 + opcode→handler 表

### 7.1 注册流程

启动时调用 `registerHandlers()` (`GameServerPacketHandler.java:64-78`)：
```java
public void registerHandlers(Class<? extends PacketHandler> handlerClass) {
    Set<Class<? extends PacketHandler>> handlerClasses = 
        Grasscutter.reflector.getSubTypesOf(handlerClass);
    
    for (Class<? extends PacketHandler> obj : handlerClasses) {
        if (TypedPacketHandler.class.isAssignableFrom(obj))
            this.registerTypedPacketHandler((Class<TypedPacketHandler<?>>) obj);
        if (TypedPacketPairHandler.class.isAssignableFrom(obj))
            this.registerTypedPairPacketHandler((Class<TypedPacketPairHandler<?,?>>) obj);
    }
}
```

**反射扫描所有 PacketHandler 子类** → 逐个注册到 `versionHandlers: Map<String, PacketHandler>`。

注册到的**键是 protobuf 类名**（如 `"GetPlayerTokenReq"`），不是 opcode 数字——这就支持了**多版本**：opcode 数值在版本间会变（2.7 的 GetPlayerTokenReq=4023, 3.0 可能=4078），但**类名不变**。

### 7.2 路由查找

`getHandler()`：
```java
private PacketHandler getHandler(String packageName, int opcode) {
    PacketHandler handler = this.versionHandlers.get(packageName);
    return handler != null ? handler : this.handlers.get(opcode);
}
```

**两层查找**：
1. 按包名（versioned）查 → 命中
2. 没有 → 按 opcode 数字查（legacy fallback）

### 7.3 包数量

```bash
$ ls server/packet/recv/*.java | wc -l
228 个 Recv 处理器（客户端 → 服务器）

$ ls server/packet/send/*.java | wc -l
388 个 Send 包构造（服务器 → 客户端）
```

**总计 600+ 种 packet 类型**——这是原神网络层的"业务面积"。

### 7.4 TypedPacketPairHandler 模式

`TypedPacketPairHandler<REQ, RSP>` (`TypedPacketPairHandler.java:26-119`)：

```java
public abstract class TypedPacketPairHandler<REQ extends ProtoModel, RSP extends ProtoModel> 
    extends PacketHandler {
    
    @Override
    public void handle(GameSession session, byte[] header, byte[] payload) {
        REQ req = (REQ) parseReqMethodHandle.invokeExact(payload, session.getVersion());
        RSP rsp = (RSP) rspConstructorHandle.invokeExact();
        // ↑ MethodHandle 高速反射
        
        val shouldSend = handle(session, header, req, rsp);   // ← 业务实现
        if (shouldSend)
            sendRsp(session, rsp);
    }
    
    public abstract boolean handle(GameSession session, byte[] header, REQ request, RSP response);
}
```

**每个 Req/Rsp 对**：
- 编译时类型安全（泛型 `<REQ, RSP>`）
- 自动 parsing/construction（MethodHandle 比反射快 3-5 倍）
- 自动响应发送（`sendRsp`）
- 业务实现只关心 `handle(session, header, req, rsp)`

→ 这又是一次**注解+反射+架构模式**（27 篇笔记里第 8 次出现这个模式！）

---

## 8. 安全防护：BANNED_PACKETS

### 8.1 黑名单包

`PacketOpcodesUtils.java:21-24`：
```java
public static final Set<String> BANNED_PACKETS = Set.of(
    "WindSeedClientNotify",     // ← 风种子, 客户端代码注入通道
    "PlayerLuaShellNotify"      // ← Lua 脚本远程执行
);
```

### 8.2 为什么要黑名单这两个

**WindSeedClientNotify** 是 mihoyo 在客户端预留的"远程代码执行"通道：
- 服务器发个 WindSeedClientNotify，客户端会**执行其中的字节码**
- 用于热更新 / 反作弊检测脚本下发
- 如果私服服务器**乱发这个包**——客户端会执行恶意代码，可能造成：
  - 客户端崩溃（搞坏玩家本地存档）
  - 客户端泄漏隐私（运行恶意 native 代码）
  - 法律风险（私服服主可能因此被追责）

**PlayerLuaShellNotify** 同理 —— Lua 脚本远程执行。

### 8.3 BANNED 的强制位置

`GameSession.send()` (`GameSession.java:122-125`)：
```java
val paketName = PacketOpcodesUtils.getOpcodeName(opcode, this);
if (PacketOpcodesUtils.BANNED_PACKETS.contains(paketName)) {
    return;   // 静默丢弃, 即使有插件想发也不行
}
```

**写在最底层**：任何 plugin / event handler 都无法绕过。注释里强调:
> "DO NOT REMOVE (unless we find a way to validate code before sending to client which I don't think we can)"

→ 这是**深思熟虑的安全策略**，不能改。

---

## 9. LOOP_PACKETS：日志噪声过滤

### 9.1 心跳类包

```java
public static final Set<String> LOOP_PACKETS = Set.of(
    "PingReq", "PingRsp",                  // 心跳
    "WorldPlayerRTTNotify",                // RTT 上报
    "UnionCmdNotify",                      // 命令打包通知
    "QueryPathReq", "QueryPathRsp",        // 寻路
    "PlayerTimeNotify",                    // 时间同步
    "PlayerGameTimeNotify",
    "AvatarPropNotify",                    // 角色属性 tick
    "AvatarSatiationDataNotify"            // 饱腹度 tick
);
```

### 9.2 用途

调试时**默认不打印这些**——它们每秒发几十次，会淹没真正有价值的业务包：
```java
case ALL -> {
    if (!PacketOpcodesUtils.LOOP_PACKETS.contains(paketName) || GAME_INFO.isShowLoopPackets) {
        logPacket("SEND", opcode, packet.getData(version));
    }
}
```

只有 `isShowLoopPackets=true` 才记录——**生产环境**默认关，**深度调试**才开。

### 9.3 包速率思考

```
[心跳] PingReq     每 5 秒一次
[场景] PlayerTimeNotify        约每秒
[属性] AvatarPropNotify        每帧（30Hz）
[移动] CombatInvocationsNotify 每帧
[战斗] EvtDoSkillSuccNotify    技能触发时
```

**每秒一个客户端发出去的 packet 数 ≈ 30-100 个**。10 万人在线 = **每秒 300-1000 万包** —— 这就是为什么 KCP/Protobuf/XOR 都要选**最快**的方案。

---

## 10. 关键设计取舍分析

### 10.1 为什么不用 TLS

**常规：HTTPS = TCP + TLS**
**原神：UDP + KCP + 自研握手 + XOR**

| 维度 | TLS | 原神方案 |
|---|---|---|
| 握手次数 | 2-RTT | 1-RTT |
| 加密强度 | AES-256-GCM | XOR |
| CPU 开销 | 中（手机有专门指令）| 极低 |
| 实现复杂度 | 高（CA / 证书 / 协议栈）| 低 |
| 抗中间人 | 强 | 中（依靠 RSA 签名）|
| 灵活性 | 标准, 难定制 | 完全自控 |

**关键考量**：
- ✓ 性能：TLS 在弱网下抖动严重；XOR 在弱网下表现稳定
- ✓ 控制：私有协议反作弊更难破解（公开协议有现成攻击工具）
- ✓ 历史包袱：游戏行业 10 年来都用 XOR/RC4 这类，工具链成熟
- ✗ 安全：的确比 TLS 弱，但**游戏不是银行**——能挡住 99% 的脚本作弊就行

### 10.2 为什么不用 gRPC

**gRPC = Protobuf + HTTP/2 + TLS**

```
gRPC：稳定优雅, 工业级, 适合 RPC
游戏：UDP/KCP 自定义, 适合实时
```

gRPC 的缺点对游戏致命：
- ❌ 基于 HTTP/2 = 基于 TCP = **队头阻塞**
- ❌ 必须有 stream → 状态复杂
- ❌ 依赖 OAuth/TLS 证书生态

游戏只需要"高频小包+实时双向"，KCP+Protobuf 完美匹配。

### 10.3 为什么 opcode 而不是路径名

REST：`POST /api/quest/finish`（10+ 字节）
原神：`opcode=2003`（2 字节）

**包大小差 5x**。一秒 100 万包 × 8 字节差 = **8MB/秒**节省。

### 10.4 为什么状态机而不是无状态

REST 风格：每个请求带 token 自证身份
游戏：连接级状态机（4 状态）+ 每包携带 sequence

游戏的特殊性：
- 玩家状态**密集**（背包/位置/任务进度全在内存）
- 重新认证开销大
- 长连接是天然的 → 状态机随连接走最自然

---

## 11. 实战：如何抓包 + 解码

### 11.1 工具链（教育目的）

```
Wireshark     →  抓 UDP 包
↓
KCP 解协议    →  从 UDP 重组 KCP 字节流
↓
XOR 解密      →  需要密钥（DispatchKey 或 SecretKey）
↓
拆包          →  按 0x4567/opcode/header_len/payload_len/payload/0x89AB
↓
Protobuf 解析 →  按 opcode 找到对应的 .proto 定义
↓
JSON 输出     →  人类可读
```

### 11.2 调试模式

服务器开 `DEBUG_MODE_INFO.logPackets = ALL`：
```
[12:34:56] RECV: GetPlayerTokenReq (4023) [180 bytes hex...]
[12:34:56] SEND: GetPlayerTokenRsp (4042) [240 bytes hex...]
[12:34:57] RECV: PlayerLoginReq (4001) [50 bytes hex...]
```

→ 这是**逆向研究的基础数据**——所有 28 篇笔记的 packet 名字都来自这里。

---

## 12. 性能数字（粗略估计）

| 操作 | 延迟 |
|---|---|
| KCP 重组（应用层）| < 1 μs |
| XOR 解密（4KB 包）| ~ 5 μs |
| Protobuf parse | ~ 20-50 μs |
| Handler 路由（HashMap）| < 1 μs |
| 业务逻辑 | 100 μs - 5 ms |
| Protobuf serialize | ~ 20-50 μs |
| XOR 加密 | ~ 5 μs |
| KCP 发送 | < 1 μs |

**整体单包处理 ≈ 200 μs - 5 ms**（取决于业务复杂度）

→ 单线程能跑 **~200-5000 QPS/玩家**，10 核 CPU = **2K-50K 玩家** 单机承载。
→ 这就是 grasscutter 单实例能撑几百人的来源。

---

## 13. 对比业界其他方案

| 项目 | 协议 | 加密 | 序列化 |
|---|---|---|---|
| 原神 | KCP | XOR + RSA | Protobuf |
| 王者荣耀 | UDP | RC4 + RSA | Protobuf |
| LoL | UDP | RSA + AES | 自定义二进制 |
| Counter-Strike | UDP | 无（信任 LAN+反作弊）| 二进制 |
| Roblox | UDP+TCP 混合 | DTLS | 自定义 |
| Minecraft Java | TCP | RSA+AES | NBT/varint |

**特点**：
- 实时游戏 = UDP 系（KCP/RC4）
- 慢节奏 / 沙盒 = TCP 系（Minecraft）
- 加密都是 RSA 握手 + 流密码（XOR/RC4/AES）

→ 原神选型是**业界主流**，在性能侧走得更激进（KCP 而非裸 UDP）。

---

## 14. 与前 28 篇笔记的关联

| 业务系统（之前笔记）| 网络协议层（本篇）|
|---|---|
| 任务系统 (notes/02) | `QuestListNotify`, `QuestProgressUpdateNotify` |
| Talk 对话 (notes/04) | `DialogSelectReq`, `TalkOptionRsp` |
| 联机 (notes/19) | `EnterScenePeerNotify`, `WorldPlayerInfoNotify` |
| 抽卡 (notes/21) | `DoGachaReq/Rsp` |
| 战斗 (notes/16) | `CombatInvocationsNotify` (核心战斗包) |
| 家园 (notes/23) | `HomeChangeModuleReq` (UGC 编辑) |
| 邮件 (notes/13) | `MailListNotify`, `GetMailItemReq` |

→ **所有这些上层系统都跑在同一条管道上**：KCP → XOR → 0x4567 → opcode → handler。

**网络层提供的接口很简单**：
```
[业务] new SomeRsp() → packet.send()
[业务] handler(session, req) → 处理
```

业务侧**根本不知道**底下是 UDP/KCP/XOR——这就是**抽象**的价值。

---

## 15. 关键收获

1. **协议栈 4 层**：Netty → KCP → 自定义二进制头 → Protobuf
2. **加密双密钥**：DispatchKey（握手期）+ SecretKey（会话期），切换点是 `GetPlayerTokenRsp`
3. **握手设计**：RSA-2048 + SHA256 签名 + 双方种子 XOR → 协商出会话密钥
4. **包结构**：双锚 0x4567/0x89AB 防错密钥，opcode 路由 600+ 个 handler
5. **状态机**：4 个核心状态 + 1 个 banned 状态，强制状态转换
6. **路由**：MethodHandle + 反射注册，按 protobuf 类名查找（兼容多版本）
7. **安全黑名单**：WindSeed/LuaShell 静默丢弃，是私服的安全底线
8. **设计取舍**：性能 > 密码学正确性 —— 行业普遍选择
9. **第 8 次"注解+反射+架构模式"**——超越 ChallengeFactory/QuestExec/Activity/Watcher/AbilityAction/DungeonValue/ActivityWatcherType 等所有业务模式，**网络层也走这套**

---

## 16. 一句话总结

> **网络协议层是支撑全部 600+ packet 的"快递系统"——用 UDP 跑 KCP，用 XOR 替代 TLS，用 RSA 双向种子换会话密钥，用反射把 228 个收包+388 个发包路由到对应的 handler。**
> 
> **它的设计哲学：性能优先, 安全够用就好——对游戏场景而言，比金融级加密更合适。**

---

**前置笔记**：
- notes/16 战斗系统 - 服务器权威 vs 客户端权威（业务层视角）
- notes/19 多人协作 - 多 Session 协调
- notes/27 架构模式总目录 - 注解+反射模式

**关联文件**：
- `Crypto.java`(78) - 加密入口
- `BasePacket.java`(146) - 包结构定义
- `GameSession.java`(295) - 会话管理
- `GameSessionManager.java`(110) - KCP listener
- `GameServerPacketHandler.java`(140) - opcode 路由
- `HandlerGetPlayerTokenReq.java`(223) - 握手核心
- `TypedPacketPairHandler.java`(119) - 反射 handler 框架
- `PacketOpcodesUtils.java`(50) - 黑名单/循环包

**研究的源代码**: 1100+ 行核心网络代码（不含 protobuf 自动生成代码、不含 KCP 库本身）。
