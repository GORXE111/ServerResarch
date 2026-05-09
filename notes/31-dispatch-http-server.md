# Dispatch HTTP 服务器深度剖析

> 第 31 篇：**客户端连游戏前的入口** —— 6+ 个 HTTP API 完成区服发现 + 账号鉴权 + token 换发 + 公告/抽卡网页

---

## 0. 为什么这一篇重要

notes/29 讲了 game server 的 KCP/UDP 协议——但客户端**怎么知道**game server 在哪？怎么**鉴权**？这一切都在客户端启动后**先打 HTTP 几次**完成。

```
[客户端启动]
    ↓
[1. HTTP] /query_region_list      ← 我能玩哪些区服？
    ↓
[2. HTTP] /query_cur_region/{name} ← 这个区服的 game IP 是多少？
    ↓
[3. HTTP] /mdk/shield/api/login    ← 用户名密码登录, 拿 token
    ↓
[4. HTTP] /combo/granter/login/v2  ← token 换 combo_token (game 用)
    ↓
[5. KCP] 连接 game server (notes/29)
    ↓
[6. KCP] GetPlayerTokenReq with combo_token
    ↓
[正式进入游戏]
```

**前 4 步是 HTTP**——这一篇就专挖这层。它和 notes/29 (game KCP) + notes/30 (DB) 一起组成**完整登录链路**。

---

## 1. 技术栈与 9 个 Router

### 1.1 框架：Javalin + Jetty

`HttpServer.java:24-52`：
```java
public final class HttpServer {
    private final Javalin javalin;
    
    public HttpServer() {
        this.javalin = Javalin.create(config -> {
            config.server(HttpServer::createServer);   // ← 用 Jetty 作为底层
            config.enforceSsl = HTTP_ENCRYPTION.useEncryption;
            // CORS / 调试日志 / SSL 配置
        });
    }
}
```

| 层 | 选型 | 用途 |
|---|---|---|
| Web 框架 | Javalin（轻量级 Kotlin/Java）| 路由 + Context 抽象 |
| HTTP 服务器 | Jetty（嵌入式）| 底层 socket + SSL |
| SSL | JKS keystore + sslContextFactory | 模拟 mihoyo 的 HTTPS |
| 序列化 | Gson | JSON 解析（`JsonUtils.decode`）|

### 1.2 9 个 Router 全清单

`Grasscutter.java:127-145` 注册时序：
```java
httpServer = new HttpServer();
httpServer.addRouter(HttpServer.UnhandledRequestRouter.class);  // 404
httpServer.addRouter(HttpServer.DefaultRequestRouter.class);    // /
httpServer.addRouter(RegionHandler.class);                       // ← 核心：区服查询
httpServer.addRouter(DispatchHandler.class);                     // 占位（空实现）
httpServer.addRouter(LogHandler.class);                          // 客户端日志上报
httpServer.addRouter(GenericHandler.class);                      // 杂项 mock
httpServer.addRouter(AnnouncementsHandler.class);                // 公告（mock）
httpServer.addRouter(GachaHandler.class);                        // 抽卡网页
httpServer.addRouter(DocumentationServerHandler.class);          // 开发文档
httpServer.addRouter(AuthHandler.class);                         // ← 核心：账号鉴权
```

| Router | 行数 | 作用 |
|---|---|---|
| `RegionHandler` | 323 | 区服列表 + game IP 下发（核心）|
| `AuthHandler` | 185 | 5+ 个登录路径 |
| `MaPassportAuthenticator` | 170 | 米哈游通行证模拟 |
| `GachaHandler` | 138 | 抽卡历史网页 |
| `AnnouncementsHandler` | 97 | 公告（fake）|
| `RSADecryptionUtil` | 76 | RSA 解密辅助 |
| `GenericHandler` | 69 | 杂项 mock |
| `LogHandler` | 25 | 客户端 crash 日志 |
| `DispatchHandler` | 17 | **空** |

**总代码量**：1340 行支撑整个 HTTP 入口。

### 1.3 Router 接口

`Router.java`（33 行，极简）：
```java
public interface Router {
    void applyRoutes(Javalin javalin);
}
```

每个 Router 自己注册路由——和 §1.2 的反射式 PacketHandler 不同，这里是**显式声明 + 反射构造**：
```java
public HttpServer addRouter(Class<? extends Router> router, Object... args) {
    var constructor = router.getDeclaredConstructor(types);
    var routerInstance = constructor.newInstance(args);
    routerInstance.applyRoutes(this.javalin);   // ← 让 Router 自己注册
    return this;
}
```

---

## 2. RegionHandler：客户端最先请求的端点

### 2.1 两个核心路由

```java
javalin.get("/query_region_list",           RegionHandler::queryRegionList);
javalin.get("/query_cur_region/{region}",   RegionHandler::queryCurrentRegion);
```

### 2.2 query_region_list：返回所有可玩区服

**真实 mihoyo 行为**：客户端启动时打 `https://dispatchosglobal.yuanshen.com/query_region_list`，服务器返回 region 列表。

**Grasscutter 模拟**：
```java
private static void queryRegionList(Context ctx) {
    RegionType targetRegion = RegionType.OS;   // 默认海外
    
    // 从 ?version=2.7.0&platform=Win 推断 OS/CN
    String versionCode = versionName.replaceAll("[/.0-9]*", "");
    if ("CNRELiOS".equals(versionCode) || "CNRELWin".equals(versionCode)
        || "CNRELAndroid".equals(versionCode)) {
        targetRegion = RegionType.CN;
    } else if ("OSRELiOS".equals(versionCode) || ...) {
        targetRegion = RegionType.OS;
    }
    
    QueryAllRegionsEvent event = new QueryAllRegionsEvent(regionListResponses.get(targetRegion));
    event.call();
    ctx.result(event.getRegionList());   // 返回 base64 编码的 protobuf
}
```

**OS vs CN 配置区别**：
```java
customConfig.addProperty("sdkenv", "2");   // OS 用 2
customConfig.addProperty("sdkenv", "0");   // CN 用 0
```

### 2.3 query_cur_region：下发 game server IP

**这是关键端点**——客户端拿到这个响应后，才知道 game server 的 IP/port。

```java
private static void queryCurrentRegion(Context ctx) {
    String regionName = ctx.pathParam("region");          // 路径参数
    var region = regions.get(regionName);
    String regionData = region.getBase64(version);
    
    if (version.getId() > Version.GI_2_7_0.getId()) {     // 2.7.5+ 走 RSA 加密路径
        // 客户端发来的 dispatchSeed
        if (ctx.queryParam("dispatchSeed") == null) {
            // 老版本简单路径
            rsp.content = event.getRegionInfo();
            rsp.sign = "TW9yZSBsb3ZlIGZvciBVQSBQYXRjaCBwbGF5ZXJz";  // base64: "More love for UA Patch players"
            ctx.json(rsp);
            return;
        }
        
        // 新版本: RSA + SHA256 签名 + chunked 加密
        String key_id = ctx.queryParam("key_id");
        Cipher cipher = Cipher.getInstance("RSA/ECB/PKCS1Padding");
        cipher.init(Cipher.ENCRYPT_MODE, Crypto.EncryptionKeys.get(Integer.valueOf(key_id)));
        var regionInfo = Utils.base64Decode(event.getRegionInfo());
        
        // RSA-2048 一次最多加密 256-11=245 字节, 大数据要分块
        int chunkSize = 256 - 11;
        int numChunks = (int) Math.ceil(regionInfoLength / (double) chunkSize);
        for (int i = 0; i < numChunks; i++) {
            byte[] chunk = Arrays.copyOfRange(regionInfo, i * chunkSize, ...);
            byte[] encryptedChunk = cipher.doFinal(chunk);
            encryptedRegionInfoStream.write(encryptedChunk);
        }
        
        // 服务器签名（防伪）
        Signature privateSignature = Signature.getInstance("SHA256withRSA");
        privateSignature.initSign(Crypto.CUR_SIGNING_KEY);
        privateSignature.update(regionInfo);
        
        rsp.content = Utils.base64Encode(encryptedRegionInfoStream.toByteArray());
        rsp.sign = Utils.base64Encode(privateSignature.sign());
    }
}
```

### 2.4 RegionInfo 的内容

返回给客户端的核心字段：
```java
var regionInfo = new RegionInfo();
regionInfo.setGateserverIp(region.Ip);      // ← game server IP
regionInfo.setGateserverPort(region.Port);  // ← game server 端口
regionInfo.setSecretKey(Crypto.DISPATCH_SEED); // ← XOR 解密用的种子
```

**这个 secretKey 就是 notes/29 §4.1 提到的 `DISPATCH_KEY`**：
- 客户端拿到这个 key
- 后续 KCP 包用它做 XOR 加密
- 直到 GetPlayerTokenRsp 切到 SecretKey

**Dispatch HTTP 是密钥分发的源头**——所有后续 KCP 加密都从这里发出去。

### 2.5 RSA 分块加密的工程细节

```java
int chunkSize = 256 - 11;  // RSA-2048 PKCS1Padding 一块最多 245 字节
```

**为什么 -11**：
- RSA-2048 = 256 字节明文上限
- PKCS#1 v1.5 padding 至少占 11 字节
- 所以**有效载荷 245 字节/块**

`regionInfo` 可能几 KB 大 → 必须分块。每个客户端能拿到 IP 都要做这个**遍历 RSA 加密**。

源码里 GitHub Copilot 的注释 hilarious 留下：
> // Thank you so much GH Copilot

---

## 3. AuthHandler：账号鉴权

### 3.1 5+ 个登录路径

`AuthHandler.java:18-55` 一次注册多条路由：
```java
String[] regionPaths = new String[] {"hk4e_global", "hk4e_cn"};
Arrays.stream(regionPaths).forEach(regionPath -> {
    // 用户名+密码登录
    javalin.post("/"+regionPath+"/mdk/shield/api/login", AuthHandler::clientLogin);
    
    // 缓存的 token 登录（自动登录）
    javalin.post("/"+regionPath+"/mdk/shield/api/verify", AuthHandler::tokenLogin);
    
    // session_key 换 combo_token
    javalin.post("/"+regionPath+"/combo/granter/login/v2/login", AuthHandler::sessionKeyLogin);
    
    // 米哈游通行证（新版本）
    javalin.post("/"+regionPath+"/account/ma-passport/api/appLoginByPassword", AuthHandler::maPassportLogin);
    javalin.post("/"+regionPath+"/account/ma-passport/token/verifySToken", AuthHandler::maPassportVerify);
});

// 第三方 OAuth (Twitter)
javalin.post("/hk4e_global/mdk/shield/api/loginByThirdparty", ...);
javalin.get("/Api/twitter_login", ...);
```

→ **路径模仿米哈游的真实 URL**：`mdk/shield/api`、`combo/granter`、`ma-passport` 都是 mihoyo SDK 的实际路径。

### 3.2 三层 token 设计

mihoyo（被 grasscutter 模仿）使用**三层 token**：

```
[第 1 层: 账号 token]      用户名+密码 → token
                                ↓
[第 2 层: session_key]    token 验证 → session_key  
                                ↓
[第 3 层: combo_token]    session_key → combo_token (用于 game server)
                                ↓
                          客户端拿 combo_token 进 game server
```

**为什么要三层**？
- ✓ **隔离责任**：登录系统和游戏系统不共享主密钥
- ✓ **撤销粒度**：禁用某 combo_token 不影响登录账号
- ✓ **多客户端**：同一账号可在多端有不同 combo_token

### 3.3 clientLogin：用户名密码

```java
private static void clientLogin(Context ctx) {
    var bodyData = JsonUtils.decode(rawBodyData, LoginAccountRequestJson.class);
    
    var responseData = Grasscutter.getAuthenticationSystem()
        .getPasswordAuthenticator()              // ← 抽象认证器
        .authenticate(AuthenticationSystem.fromPasswordRequest(ctx, bodyData));
    
    ctx.json(responseData);
}
```

**关键设计**：`AuthenticationSystem` 是**抽象**——可以替换：
- 默认 `DefaultAuthenticators` —— 不验证密码（私服特性）
- 自定义 `OAuthAuthenticator` —— 走第三方
- 自定义 `MaPassportAuthenticator` —— 完整模拟米哈游

这又是**插件式架构**——和 Activity 系统的策略模式一致。

### 3.4 真实流程：从客户端打开到进入游戏

```
[T+0ms]    客户端启动
[T+50ms]   GET /query_region_list?version=2.7.0&platform=Win
            ← 200: { regionList: [...] }
            
[T+100ms]  GET /query_cur_region/os_usa?version=2.7.0&dispatchSeed=...&key_id=2
            ← 200: { content: <RSA-encrypted regionInfo>, sign: <SHA256-RSA> }
            ← 客户端拿到: gateIp=1.2.3.4, gatePort=22102, secretKey=<DispatchSeed>
            
[T+200ms]  POST /hk4e_global/mdk/shield/api/login
            { account: "user", password: "pass" }
            ← 200: { retcode: 0, data: { token, account_uid } }
            
[T+250ms]  POST /hk4e_global/combo/granter/login/v2/login
            { token, account_uid }
            ← 200: { retcode: 0, data: { combo_token } }
            
[T+300ms]  KCP 连接 1.2.3.4:22102
            ↓ XOR-DispatchKey
[T+350ms]  GetPlayerTokenReq { account_uid, combo_token }
            ↓ 服务端验证, 切到 SecretKey
[T+400ms]  GetPlayerTokenRsp { secretKeySeed }
            ↓
[T+500ms]  PlayerLoginReq → PlayerLoginRsp
            ↓
[T+1000ms] EnterScene → 进入游戏世界
```

**总耗时**：1 秒级登录，4 次 HTTP + KCP 多次握手。

---

## 4. AnnouncementsHandler：被 fake 的公告

### 4.1 路径模仿

```java
this.allRoutes(javalin, "/common/hk4e_global/announcement/api/getAlertPic", 
    new HttpJsonResponse("{\"retcode\":0,\"message\":\"OK\",\"data\":{\"total\":0,\"list\":[]}}"));

this.allRoutes(javalin, "/common/hk4e_global/announcement/api/getAlertAnn",
    new HttpJsonResponse("{\"retcode\":0,\"message\":\"OK\",\"data\":{\"alert\":false,...}}"));
```

**完全 fake** —— 直接返回固定 JSON，里面 `total: 0` 和 `alert: false`。

### 4.2 真实公告（如果配置）

```java
if (Objects.equals(ctx.endpointHandlerPath(), "/common/hk4e_global/announcement/api/getAnnContent")) {
    data = FileUtils.readToString(DataLoader.load("GameAnnouncement.json"));
}
```

**支持本地公告**：放 `data/GameAnnouncement.json` 就会被加载。但默认空。

### 4.3 模板替换

```java
data = data
    .replace("{{DISPATCH_PUBLIC}}", dispatchDomain)
    .replace("{{SYSTEM_TIME}}", String.valueOf(System.currentTimeMillis()));
```

→ 简易模板替换 —— 没用 Mustache/Velocity，简单就够。

---

## 5. GachaHandler：抽卡历史网页

### 5.1 中间件特性

mihoyo 的抽卡详情链接：`https://hk4e-api.mihoyo.com/event/gacha_info/...`

Grasscutter 模仿：
```java
javalin.get("/gacha", GachaHandler::gachaRecords);
javalin.get("/gacha/details", GachaHandler::gachaDetails);
```

**用途**：客户端内 webview 打开这些 URL 看抽卡历史。

### 5.2 鉴权

```java
String sessionKey = ctx.queryParam("s");
Account account = DatabaseHelper.getAccountBySessionKey(sessionKey);
if (account == null) {
    ctx.status(403).result("Requested account was not found");
    return;
}
```

→ 用 `?s=<sessionKey>` 鉴权。和正式 cookie/session 不同——是**一次性 URL token**。

### 5.3 模板渲染

```java
String template = new String(FileUtils.read(FileUtils.getDataPath("gacha/records.html")), StandardCharsets.UTF_8)
    .replace("{{REPLACE_RECORDS}}", records)
    .replace("{{REPLACE_MAXPAGE}}", String.valueOf(maxPage))
    .replace("{{TITLE}}", translate(player, "gacha.records.title"))
    ...
```

**模板**：本地 `gacha/records.html` 文件 + 简单 `{{...}}` 替换。和 Announcements 用法一样。

---

## 6. URL 模仿全表

Grasscutter 模仿了米哈游真实端点的**url 路径**：

| 真实 mihoyo URL | Grasscutter 路径 | 含义 |
|---|---|---|
| `dispatchosglobal.yuanshen.com/query_region_list` | `/query_region_list` | 区服列表 |
| `dispatchosglobal.yuanshen.com/query_cur_region/...` | `/query_cur_region/{region}` | 当前区服详情 |
| `hk4e-sdk-os.hoyoverse.com/hk4e_global/mdk/shield/api/login` | `/hk4e_global/mdk/shield/api/login` | 用户名密码登录 |
| `hk4e-sdk-os.hoyoverse.com/hk4e_global/mdk/shield/api/verify` | `/hk4e_global/mdk/shield/api/verify` | token 验证 |
| `hk4e-sdk-os.hoyoverse.com/hk4e_global/combo/granter/login/v2/login` | `/hk4e_global/combo/granter/login/v2/login` | combo token |
| `hk4e-api-os.hoyoverse.com/common/hk4e_global/announcement/api/getAnnList` | 同 | 公告列表 |
| `hk4e-sdk-os.hoyoverse.com/hk4e_global/mdk/shopwindow/shopwindow/listPriceTier` | 同 | 商城价格 |
| `webstatic.mihoyo.com/.../gacha-record-page` | `/gacha` | 抽卡记录网页 |

**这是私服必备能力**：客户端代码里写死了这些 URL 路径——服务器要**完全匹配**才能让客户端不崩。

→ 私服上线的工作量：要 mock 出**几十个 URL**，每一个的 JSON schema 都要对得上。

---

## 7. SSL/HTTPS 配置

### 7.1 真客户端要求 HTTPS

mihoyo 的客户端**默认走 HTTPS**——意味着 dispatch 必须有有效证书。

```java
if (HTTP_ENCRYPTION.useEncryption) {
    var sslContextFactory = new SslContextFactory.Server();
    var keystoreFile = new File(HTTP_ENCRYPTION.keystore);
    
    if (!keystoreFile.exists()) {
        HTTP_ENCRYPTION.useEncryption = false;
        Grasscutter.getLogger().warn("messages.dispatch.keystore.no_keystore_error");
    } else {
        sslContextFactory.setKeyStorePath(keystoreFile.getPath());
        sslContextFactory.setKeyStorePassword(HTTP_ENCRYPTION.keystorePassword);
    }
}
```

### 7.2 私服 SSL 的两条路

**方法 A: 自签证书 + 改 hosts**（grasscutter 经典做法）：
```
1. 用 keytool 生成自签 keystore
2. 把客户端打的域名（如 dispatchosglobal.yuanshen.com）解析到本地
3. 把自签证书加到系统受信根证书
4. 客户端 HTTPS 通信验证通过
```

**方法 B: UA Patch 客户端**：
- 修改客户端二进制让它**信任所有证书**
- 此时不需要正确 SSL 配置
- 注释里能看到 `// More love for UA Patch users` 处处迁就

### 7.3 SSL 失败兜底

```java
} catch (Exception ignored) {
    sslContextFactory.setKeyStorePassword("123456");   // ← 默认密码兜底
    Grasscutter.getLogger().warn("messages.dispatch.keystore.default_password");
}
```

→ 给随便起服的人留了个"密码忘了试 123456"的退路——**优先可用性**。

---

## 8. 与 game server 的关系

### 8.1 单进程 vs 双进程

```java
public enum ServerRunMode {
    HYBRID,        // 单进程: dispatch + game 一起
    DISPATCH_ONLY, // 仅 dispatch
    GAME_ONLY      // 仅 game (依赖远程 dispatch)
}
```

**HYBRID** 模式（默认）：
```
java -jar grasscutter.jar
  ├── HTTP server (port 443) ← dispatch
  └── KCP server (port 22102) ← game
```

**分布式部署**：
```
[Dispatch 服务器]            [Game 服务器 1] [Game 服务器 2] ...
  HTTP 443                     KCP 22102      KCP 22103
       ↓
       └─→ 配 region 列表里
```

### 8.2 DISPATCH_INFO.regions 配置

```yaml
# config.json
dispatch:
  regions:
    - name: "os_usa"
      title: "USA"
      ip: "game.example.com"
      port: 22102
    - name: "os_eu"
      title: "Europe"
      ip: "game-eu.example.com"
      port: 22102
```

→ 一个 dispatch 可以指向多个 game server，**根据玩家选择动态返回 IP**。

---

## 9. 与 notes/29 + notes/30 的关联

### 9.1 完整登录数据流

```
[Step 1-2 HTTP]
   GET /query_region_list  →  RegionHandler 内存返回 (无 DB)
   GET /query_cur_region   →  RegionHandler 内存返回 (无 DB)
                              ↓
[Step 3 HTTP]
   POST /mdk/shield/api/login
                              ↓
                            AuthHandler.clientLogin
                              ↓
                            DatabaseHelper.getAccountByName  ← 查 accounts (notes/30)
                              ↓
                            生成 token, save  ← 写 accounts
                              ↓
   [客户端拿 token]
                              ↓
[Step 4 HTTP]
   POST /combo/granter/login/v2/login
                              ↓
                            DatabaseHelper.getAccountBySessionKey
                              ↓
                            生成 combo_token
                              ↓
[Step 5 KCP]
   连接 KCP, XOR with DispatchKey  (notes/29)
                              ↓
[Step 6 KCP]
   GetPlayerTokenReq with combo_token
                              ↓
                            HandlerGetPlayerTokenReq
                              ↓
                            DatabaseHelper.getPlayerByAccount  ← 查 players
                              ↓
                            player.loadFromDatabase()
                              ↓
                            7 次 DB 查询恢复全部状态  (notes/30)
                              ↓
[Step 7 KCP]
   切换到 SecretKey
                              ↓
[Step 8+ KCP]
   PlayerLoginReq, EnterScene, ... ← 正式游戏开始
```

**三层各自的角色**：
| 层 | 协议 | 用途 |
|---|---|---|
| Dispatch (notes/31) | HTTP/HTTPS | 区服发现 + 账号鉴权 + token 换发 |
| Game (notes/29) | KCP/UDP | 游戏逻辑 + 实时同步 |
| DB (notes/30) | MongoDB | 持久化 + 玩家恢复 |

→ **三个文件加起来 = 完整的服务器入口栈**。

---

## 10. 架构亮点

### 10.1 路由注册的灵活性

```java
public HttpServer addRouter(Class<? extends Router> router, Object... args) {
    var constructor = router.getDeclaredConstructor(types);
    var routerInstance = constructor.newInstance(args);
    routerInstance.applyRoutes(this.javalin);
    return this;
}
```

**链式 + 反射构造**：
- 加新 Router 不需要改 HttpServer
- Router 自己声明路由（封装好）
- 支持构造器参数（如带依赖）

→ 这又是**插件式架构**——和 PacketHandler / Activity 一致。

### 10.2 多版本兼容

```java
if (version.getId() > Version.GI_2_7_0.getId()) {
    // 走 RSA + chunked + signature 路径
} else {
    // 老路径
}
```

**HTTP 层也维护**多版本兼容——和 KCP 层（PacketIdProvider）的版本机制一脉相承。

### 10.3 Event hooks

```java
QueryAllRegionsEvent event = new QueryAllRegionsEvent(...);
event.call();
ctx.result(event.getRegionList());   // ← Plugin 可以替换 region 列表
```

**Plugin 可以拦截**这些 HTTP 响应：
- `QueryAllRegionsEvent` —— 改 region 列表
- `QueryCurrentRegionEvent` —— 改 game IP 下发

→ 给私服管理员留了**钩子**，便于扩展（如多区动态分配）。

---

## 11. 安全考量（或缺乏）

### 11.1 默认配置不安全

- ✗ 无密码验证（DefaultAuthenticators 接受任何密码）
- ✗ 抽卡 sessionKey 在 URL query 里（可能被代理日志记录）
- ✗ 自签证书（中间人攻击防护弱）
- ✗ 没 rate limiting（暴力破解未挡）

### 11.2 这些缺陷**对开源私服可接受**

- 设计目的：让 1-100 个朋友本地玩
- 不是公开运营
- 用户教育："不要把 dispatch 暴露公网"

### 11.3 真实 mihoyo 服务器的额外防护

肯定有但 grasscutter 没模拟：
- WAF / DDoS 防护
- 设备指纹（device_id 校验）
- IP 限频
- 登录异常检测
- 验证码（可疑请求）
- bcrypt/argon2 密码 hash

---

## 12. 关键收获

1. **9 个 Router** 注册到同一个 Javalin app，1340 行代码完成整个 HTTP 入口
2. **登录链路 4 步 HTTP + 多步 KCP**：region list → cur region → login → combo token → KCP 连接
3. **三层 token 设计**：账号 token / session_key / combo_token —— 隔离责任
4. **RegionHandler 是密钥分发源**：`DISPATCH_KEY` 通过 query_cur_region 下发，game KCP 后续基于此加密
5. **RSA chunked 加密**：256-11=245 字节/块，因为 RSA-2048 + PKCS1Padding 的限制
6. **完全模仿 mihoyo URL**：`hk4e_global/mdk/shield/api/login` 等路径 1:1 还原
7. **Announcements 完全 fake**：返回固定 JSON 让客户端不崩
8. **HYBRID/DISPATCH_ONLY/GAME_ONLY** 三种部署模式，支持横向扩展
9. **多版本兼容**：2.7.0 前后走不同代码路径（RSA vs 简单）
10. **Event hooks** 给 plugin 拦截机会

---

## 13. 一句话总结

> **Dispatch HTTP 层是客户端连游戏前的"门卫"——4 个 HTTP 请求完成区服发现+账号鉴权+三层 token 换发，把 game server IP 和 DispatchKey 通过 RSA 加密下发；它和 game KCP (notes/29) + DB (notes/30) 一起组成完整登录栈。**
> 
> **设计哲学：完全模仿 mihoyo URL 路径 + 兜底优先（自签证书、空密码、UA Patch 兼容）—— 让私服能在 1-100 人朋友圈跑起来即可。**

---

**前置笔记**：
- notes/29 网络协议层 - game server KCP/UDP 协议（HTTP 之后的下一步）
- notes/30 数据库持久化 - HandlerGetPlayerTokenReq 触发的 DB 加载

**关联文件**：
- `HttpServer.java`(207) - Javalin + Jetty 主框架
- `Router.java`(33) - 路由抽象
- `RegionHandler.java`(323) - 核心：区服查询 + 密钥下发
- `AuthHandler.java`(185) - 5+ 个登录端点
- `MaPassportAuthenticator.java`(170) - 米哈游通行证模拟
- `AnnouncementsHandler.java`(97) - 公告（mock）
- `GachaHandler.java`(138) - 抽卡历史网页

**真实 URL 模仿全表**: 8+ 个端点 1:1 复刻 mihoyo 路径。

**研究的源代码**: 1340 行 HTTP 入口层。
