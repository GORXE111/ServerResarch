# MailHandler 邮件系统深度剖析

> 第 57 篇：notes/13 提过"异步通道"、notes/30 提过 mail collection——但邮件运行时从未真正打开。**248 行 (MailHandler 101 + Mail 147)** 的"通用异步奖励/补偿通道"，是离线发奖、活动补偿、首充返利的统一出口。

---

## 0. 为什么这一篇重要

前 56 篇里 Mail 反复出现但 runtime 没专门挖：
- notes/13 项目终章工具：提到"Mail 异步通道"概念
- notes/30 持久化层：`mail` collection (ownerUid indexed)
- notes/38 Inventory：`ActionReason.MailAttachment` 是 190+ 之一
- notes/47 Plugin/Event：`PlayerReceiveMailEvent`

但**邮件怎么发？离线玩家怎么收？附件防重领怎么实现？过期怎么处理？**——这一篇统一回答。

---

## 1. Mail 系统全图

```
┌─────────────────────────────────────────────────────────────┐
│  Mail (147 行) — @Entity "mail" collection                    │
│  - ownerUid (indexed)                                         │
│  - MailContent (title/content/sender)                         │
│  - List<MailItem> (附件: itemId/count/level)                  │
│  - sendTime / expireTime / importance / isRead / isAttachmentGot │
│  - save() 智能: 过期则 delete                                  │
└────────────────────────┬────────────────────────────────────┘
                         │ per Player
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  MailHandler (101 行) — BasePlayerManager                     │
│  - List<Mail> mail (内存缓存)                                  │
│  - sendMail / deleteMail / loadFromDatabase                   │
│  - PlayerReceiveMailEvent 钩子                                │
└────────────────────────┬────────────────────────────────────┘
                         │ 6 个 Handler
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  GetAllMailReq / GetMailItemReq / DelMailReq                  │
│  ReadMailNotify / ChangeMailStarNotify / GetAllMailNotify     │
└─────────────────────────────────────────────────────────────┘
```

→ **248 行 + 6 Handler** 支撑整个邮件系统。

---

## 2. Mail Entity：mail collection

```java
@Entity(value = "mail", useDiscriminator = false)
public class Mail {
    @Getter @Id private ObjectId id;
    @Getter @Setter @Indexed private int ownerUid;        // ★ 索引: 按玩家查
    @Getter public MailContent mailContent;                // title/content/sender
    @Getter public List<MailItem> itemList;                // 附件
    @Getter public long sendTime;
    @Getter public long expireTime;                         // ★ 过期时间戳
    @Getter public int importance;                          // 0=无星 1=星标
    @Getter public boolean isRead;
    @Getter public boolean isAttachmentGot;                 // ★ 防重领
    @Getter public int stateValue;                          // 1=默认 3=礼物箱
    @Transient private boolean shouldDelete;
}
```

### 2.1 4 级构造器（默认值递进）

```java
public Mail() {
    this(new MailContent(), new ArrayList<MailItem>(), 
         (int) Instant.now().getEpochSecond() + 604800);   // ★ 默认 7 天过期
}
public Mail(content, items, expireTime) { this(..., 0); }       // importance=0
public Mail(content, items, expireTime, importance) { this(..., 1); }  // state=1
public Mail(content, items, expireTime, importance, state) { ... }
```

→ **604800 秒 = 7 天** —— 默认邮件 7 天过期（与正服一致）。

### 2.2 MailContent 嵌套

```java
@Entity
public static class MailContent {
    public String title;
    public String content;
    public String sender;
    
    public MailContent(String title, String content) {
        this(title, content, "Server");   // ★ 默认发件人 "Server"
    }
    public MailContent(String title, String content, Player sender) {
        this(title, content, sender.getNickname());   // 玩家发件人
    }
}
```

→ 支持**系统发件**（"Server"）和**玩家发件**（昵称）。

### 2.3 MailItem 嵌套（附件）

```java
@Entity
public static class MailItem {
    public int itemId;
    public int itemCount;
    public int itemLevel;
    
    public MailItem(int itemId, int itemCount, int itemLevel) { ... }
    
    public org.anime_game_servers...MailItem toProto() {
        return new MailItem(null, 
            new EquipParam(this.itemId, this.itemCount, this.itemLevel, 0));
    }
}
```

→ 每个附件 = itemId + count + level（武器/圣遗物有 level）。

### 2.4 stateValue：邮箱分类

```
stateValue = 1: 默认邮箱
stateValue = 3: 礼物箱 (玩家赠礼?)
```

→ 客户端按 stateValue 分不同标签页。

---

## 3. save()：智能持久化（过期即删）

```java
public void save() {
    if (this.expireTime * 1000 < System.currentTimeMillis()) {
        DatabaseHelper.deleteMail(this);   // ★ 过期 → 删除
    } else {
        DatabaseHelper.saveMail(this);     // 未过期 → 保存
    }
}
```

### 3.1 设计精髓

→ **没有定时清理任务** —— 每次 save 时检查过期：
- 过期 → 从 mail collection 删除
- 未过期 → 正常保存

→ 类似 notes/50 Resin 的 **lazy evaluation** —— 不需要后台 GC。

### 3.2 deleteMail 利用过期机制

```java
public boolean deleteMail(int mailId) {
    Mail message = getMailById(mailId);
    if (message != null) {
        this.getMail().remove(mailId);
        message.expireTime = 0;   // ★ 设过期为 0
        message.save();            // ★ save 检测到过期 → 删除
        return true;
    }
    return false;
}
```

→ **删除 = 把 expireTime 设 0 + save** —— 复用过期删除逻辑，不需要单独的 delete API 调用。
→ 优雅的代码复用。

---

## 4. sendMail：发邮件流程

```java
public void sendMail(Mail message) {
    // 1. ★ 可取消事件钩子
    PlayerReceiveMailEvent event = new PlayerReceiveMailEvent(this.getPlayer(), message);
    event.call();
    if (event.isCanceled()) return;
    message = event.getMessage();
    
    // 2. 设 owner + 持久化
    message.setOwnerUid(this.getPlayer().getUid());
    message.save();
    
    // 3. 加入内存缓存
    this.mail.add(message);
    
    // 4. ★ 在线才推送通知
    if (this.getPlayer().isOnline()) {
        this.getPlayer().sendPacket(new PacketMailChangeNotify(this.getPlayer(), message));
    }
    // TODO: 离线收件通知 (注释里承认未实现)
}
```

### 4.1 PlayerReceiveMailEvent 钩子（notes/47）

```java
PlayerReceiveMailEvent event = new PlayerReceiveMailEvent(player, message);
event.call();
if (event.isCanceled()) return;
message = event.getMessage();
```

→ 插件可：
- 取消邮件（黑名单玩家不收）
- 修改邮件（敏感词替换）
- 注入额外附件（活动加码）

### 4.2 离线收件

```java
if (this.getPlayer().isOnline()) {
    this.getPlayer().sendPacket(new PacketMailChangeNotify(...));
}
// TODO: setup a way for the mail notification to show up when someone receives mail when they were offline
```

→ **离线玩家**：邮件已 `save()` 到 DB，但**不推送通知**。
→ 玩家下次登录 `loadFromDatabase` 时拿到。
→ 注释承认"离线收件通知"未实现 —— 玩家上线不会有"你有新邮件"红点（小瑕疵）。

### 4.3 关键：MailHandler 是 per-Player

```java
public class MailHandler extends BasePlayerManager {
    public void sendMail(Mail message) {
        // this.getPlayer() = 收件人
    }
}
```

→ **给玩家 X 发邮件 = 调 X.getMailHandler().sendMail(mail)**。
→ 但**离线玩家**没有 MailHandler 实例（未登录）！

→ 离线发邮件实际路径（推断）：
```java
// 系统发邮件给离线玩家
Mail mail = new Mail(content, items, expireTime);
mail.setOwnerUid(targetUid);
mail.save();   // ★ 直接写 DB, 不经 MailHandler
// 玩家上线 loadFromDatabase 时读取
```

---

## 5. loadFromDatabase：登录恢复

```java
public void loadFromDatabase() {
    List<Mail> mailList = DatabaseHelper.getAllMail(this.getPlayer());
    for (Mail mail : mailList) {
        this.getMail().add(mail);
    }
}
```

`DatabaseHelper.getAllMail` (notes/30)：
```java
public static List<Mail> getAllMail(Player player) {
    return getGameDatastore().find(Mail.class)
        .filter(Filters.eq("ownerUid", player.getUid())).stream().toList();
}
```

→ 按 `ownerUid` 索引查所有邮件（含离线期间收到的）。
→ `Player.loadFromDatabase` (notes/30) 调用 `mailHandler.loadFromDatabase()`。

### 5.1 过期邮件加载

→ 注意 `loadFromDatabase` **不过滤过期** —— 加载全部。
→ 过期邮件下次 `save()` 时才删除（lazy）。
→ 玩家可能短暂看到过期邮件（直到操作触发 save）。

---

## 6. 附件领取：GetMailItemReq（防重领核心）

`HandlerGetMailItemReq.java`：
```java
public void handle(GameSession session, byte[] header, GetMailItemReq req) {
    session.send(new PacketGetMailItemRsp(session.getPlayer(), req.getMailIdList()));
}
```

`PacketGetMailItemRsp` 构造器（实际领取逻辑）：
```java
public PacketGetMailItemRsp(Player player, List<Integer> mailList) {
    List<Mail> claimedMessages = new ArrayList<>();
    List<EquipParam> claimedItems = new ArrayList<>();
    
    synchronized (player) {   // ★ 玩家级锁
        boolean modified = false;
        for (int mailId : mailList) {
            Mail message = player.getMail(mailId);
            
            // ★ 防重领核心
            if (!message.isAttachmentGot) {
                for (Mail.MailItem mailItem : message.itemList) {
                    // 构造领取物品
                    GameItem gameItem = new GameItem(GameData.getItemDataMap().get(mailItem.itemId));
                    gameItem.setCount(mailItem.itemCount);
                    gameItem.setLevel(mailItem.itemLevel);
                    gameItem.setPromoteLevel(GameItem.getMinPromoteLevel(mailItem.itemLevel));
                    
                    // ★ 加入背包 (notes/38)
                    player.getInventory().addItem(gameItem, ActionReason.MailAttachment);
                    
                    claimedItems.add(item);
                }
                
                message.isAttachmentGot = true;   // ★ 标记已领
                claimedMessages.add(message);
                player.replaceMailByIndex(mailId, message);
                modified = true;
            }
        }
        if (modified) {
            player.save();
        }
    }
    
    proto.setMailIdList(...);
    proto.setItemList(claimedItems);
}
```

### 6.1 防重领机制

```java
if (!message.isAttachmentGot) {   // 只有未领过才发
    // ... addItem ...
    message.isAttachmentGot = true;
}
```

→ **isAttachmentGot 布尔标记** —— 领过的邮件再次请求**直接跳过**。
→ 防止"重复点领取刷物品"。

### 6.2 synchronized(player) 玩家级锁

```java
synchronized (player) { ... }
```

→ **锁玩家对象** —— 防止并发领取（双客户端/快速点击）导致重复发放。
→ 锁粒度 = 单玩家，不阻塞其他玩家。

### 6.3 批量领取

```java
for (int mailId : mailList) { ... }
```

→ 客户端可一次请求领多封邮件附件（"一键领取全部"）。
→ 每封独立检查 isAttachmentGot。

### 6.4 ActionReason.MailAttachment

```java
player.getInventory().addItem(gameItem, ActionReason.MailAttachment);
```

→ ActionReason 190+ (notes/38) 中的 `MailAttachment(12)`。
→ 走标准 Inventory.addItem —— 触发 4 个事件钩子（Quest/BattlePass 等）。

---

## 7. deleteMail 批量

```java
public void deleteMail(List<Integer> mailList) {
    List<Integer> sortedMailList = new ArrayList<>(mailList);
    Collections.sort(sortedMailList, Collections.reverseOrder());   // ★ 倒序
    
    List<Integer> deleted = new ArrayList<>();
    for (int id : sortedMailList) {
        if (this.deleteMail(id)) {
            deleted.add(id);
        }
    }
    
    player.getSession().send(new PacketDelMailRsp(deleted));
    player.getSession().send(new PacketMailChangeNotify(player, null, deleted));
}
```

### 7.1 为什么倒序删除

```java
Collections.sort(sortedMailList, Collections.reverseOrder());
```

→ `mail` 是 `List<Mail>` —— 删除靠 **index**：
```java
public boolean deleteMail(int mailId) {
    this.getMail().remove(mailId);   // ★ remove(int index)
}
```

→ **如果正序删除**：删 index 0 后，原 index 1 变 index 0 → 后续删除错位！
→ **倒序删除**：删大 index 不影响小 index → 正确。

→ 这是**经典的"边遍历边删除 List"陷阱**的正确处理。

### 7.2 index 作为 mailId 的隐患

```java
public Mail getMailById(int index) { return this.mail.get(index); }
public int getMailIndex(Mail message) { return this.mail.indexOf(message); }
```

→ **mailId = List 中的 index** —— 不是 Mail.id (ObjectId)！
→ 这是个**脆弱设计**：
- 删除一封 → 后面所有 mailId 偏移
- 客户端/服务器 mailId 必须同步
- 倒序删除 + 操作后重发 PacketMailChangeNotify 维持一致

→ 比用稳定 ID 更易出错，但实现简单。

---

## 8. 6 个 Mail Handler

```
HandlerGetAllMailReq      — 拉取所有邮件 (打开邮箱)
HandlerGetAllMailNotify   — 邮件列表通知
HandlerGetMailItemReq     — 领取附件 (核心)
HandlerDelMailReq         — 删除邮件
HandlerReadMailNotify     — 标记已读
HandlerChangeMailStarNotify — 星标/取消星标 (importance)
```

→ 覆盖邮件全部操作：拉取 / 领取 / 删除 / 已读 / 星标。

### 8.1 ReadMailNotify

→ 客户端打开某封邮件 → ReadMailNotify → `mail.isRead = true` + save。
→ 影响红点显示。

### 8.2 ChangeMailStarNotify

→ 星标邮件 → `mail.importance = 1` → 排序靠前 + 不易误删。

---

## 9. 完整时序：系统发补偿邮件

```
[运维: GM 命令或自动补偿]
   创建 Mail:
     content = MailContent("维护补偿", "感谢您的耐心", "Server")
     items = [MailItem(201, 600)]  // 600 原石
     expireTime = now + 30 天
   
[发给在线玩家 A]
   A.getMailHandler().sendMail(mail):
     1. PlayerReceiveMailEvent.call (插件可拦截)
     2. mail.setOwnerUid(A.uid)
     3. mail.save() → DatabaseHelper.saveMail (mail collection)
     4. mailHandler.mail.add(mail)  (内存缓存)
     5. A 在线 → PacketMailChangeNotify (红点 + 通知)
   
[发给离线玩家 B]
   B 无 MailHandler 实例 (未登录)
   → 直接构造 Mail + setOwnerUid(B.uid) + save() 写 DB
   → B 上线时 loadFromDatabase 读取
   
[玩家 A 打开邮箱]
   GetAllMailReq → PacketGetAllMailRsp (mail 列表 proto)
   
[玩家 A 点击邮件]
   ReadMailNotify → mail.isRead = true → save
   
[玩家 A 领取附件]
   GetMailItemReq { mailIdList: [0] }
   ↓ PacketGetMailItemRsp 构造器:
     synchronized(A):
       mail = A.getMail(0)
       if !mail.isAttachmentGot:
         GameItem(201, 600)
         A.getInventory().addItem(item, ActionReason.MailAttachment)  (notes/38)
           → 600 原石进背包 + 触发 Quest/BattlePass 钩子
         mail.isAttachmentGot = true
         A.replaceMailByIndex(0, mail)
       A.save()
   ↓ 返回 claimedItems
   
[玩家 A 删除邮件]
   DelMailReq { mailList: [0, 2, 5] }
   ↓ deleteMail(List):
     倒序排序 [5, 2, 0]
     逐个 deleteMail(id):
       mail.remove(index)
       mail.expireTime = 0
       mail.save() → 检测过期 → DatabaseHelper.deleteMail
     PacketDelMailRsp + PacketMailChangeNotify
   
[邮件自然过期]
   30 天后玩家操作触发 save → expireTime < now → 自动 delete
   (无后台清理任务)
```

---

## 10. 邮件作为"通用异步通道"

Mail 是 grasscutter 中**最通用的异步奖励出口**：

| 场景 | 用 Mail 因为 |
|---|---|
| 维护补偿 | 玩家可能离线 |
| 活动奖励 | 背包满时不丢失 |
| 首充返利 | 跨会话延迟发放 |
| 客服补单 | 手动操作 |
| 退款 | 异步处理 |
| 生日礼物 | 定时触发 |
| 周本 boss 奖励溢出 | 背包满兜底 |

→ **背包满兜底**：很多系统"addItem 失败 → 转 Mail"——保证奖励不丢。

### 10.1 vs 直接 addItem

```
直接 addItem (notes/38):
   - 即时, 玩家必须在线
   - 背包满 → 失败丢失
   
Mail:
   - 异步, 离线也能发
   - 7 天领取窗口
   - 背包满 → 还在邮箱里
```

→ "**重要奖励走 Mail，即时小奖励走 addItem**" 是设计原则。

---

## 11. 设计模式总结

### 11.1 Lazy 过期删除

```
save() 检测过期 → delete
deleteMail = expireTime=0 + save
```

→ 无后台 GC，复用过期逻辑实现删除。

### 11.2 isAttachmentGot 防重领

```
布尔标记 + synchronized(player)
```

→ 简单可靠的幂等保证。

### 11.3 index 作 mailId（脆弱但简单）

```
mailId = List index
倒序删除 + 操作后重发 ChangeNotify
```

→ trade-off：简单 vs 易错。

### 11.4 per-Player Manager + 离线直写 DB

```
在线: mailHandler.sendMail
离线: 直接 Mail.save() 写 DB
```

→ 两条路径，登录时 loadFromDatabase 统一。

### 11.5 PlayerReceiveMailEvent 钩子

```
插件可取消/修改邮件
```

→ Extensibility（notes/47）。

---

## 12. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 重复领取附件 | ✗ isAttachmentGot 防 |
| 并发领取 | ✗ synchronized(player) |
| 篡改邮件附件 | ✗ 服务器存 mail collection |
| 伪造系统邮件 | ✗ 客户端不能发邮件给自己 |
| 领过期邮件 | ✓ 可能 (过期 lazy 删除有窗口) |

→ 邮件**反作弊较强** —— 唯一窗口是"过期邮件未及时删除时仍可领"（小瑕疵）。

---

## 13. 关键收获

1. **248 行 (MailHandler 101 + Mail 147) + 6 Handler** = 整个邮件系统
2. **Mail @Entity "mail" collection**：ownerUid indexed (notes/30)
3. **默认 7 天过期**：604800 秒，4 级构造器默认值递进
4. **MailContent 系统/玩家发件**：默认 sender "Server"
5. **stateValue 邮箱分类**：1=默认 3=礼物箱
6. **save() 智能 lazy 删除**：过期则 deleteMail 否则 saveMail —— 无后台 GC
7. **deleteMail = expireTime=0 + save**：复用过期逻辑实现删除
8. **sendMail 5 步**：PlayerReceiveMailEvent → setOwner → save → 内存缓存 → 在线推送
9. **离线收件**：邮件 save 到 DB 但不推送通知 (TODO 未实现红点)
10. **离线玩家无 MailHandler**：系统直接 `Mail.setOwnerUid + save` 写 DB
11. **loadFromDatabase 不过滤过期**：玩家可能短暂看到过期邮件
12. **isAttachmentGot 防重领**：领过的跳过——幂等保证
13. **synchronized(player) 玩家级锁**：防并发领取重复发放
14. **批量领取**：一次请求领多封 (一键领取)
15. **ActionReason.MailAttachment**：走标准 Inventory.addItem (notes/38) 触发 4 钩子
16. **mailId = List index（脆弱设计）**：删除需倒序 + 重发 ChangeNotify 维持一致
17. **倒序删除**：避免 List index 偏移陷阱
18. **6 Handler**：GetAll / GetItem / Del / Read / ChangeStar / GetAllNotify
19. **通用异步通道**：补偿/活动/首充/客服/背包满兜底——重要奖励走 Mail
20. **反作弊较强**：唯一瑕疵是过期邮件 lazy 删除窗口期可领

---

## 14. 一句话总结

> **MailHandler = 通用异步奖励/补偿通道 (248 行) —— Mail @Entity (mail collection, ownerUid 索引, 7 天默认过期) + save() lazy 过期删除 (无后台 GC, deleteMail 复用过期逻辑) + isAttachmentGot 防重领 + synchronized(player) 防并发 + mailId=List index 脆弱设计需倒序删除; sendMail 经 PlayerReceiveMailEvent 钩子, 离线玩家直写 DB 登录时 loadFromDatabase; 领取走 Inventory.addItem(MailAttachment) 触发标准钩子.**
> 
> **设计哲学: 离线兜底 (写 DB 不依赖在线) + lazy 过期 (复用 save 逻辑) + 幂等防重领 (布尔标记+锁) + 背包满兜底 (重要奖励走 Mail) —— 这是 grasscutter 中"异步可靠投递"的标准实现, 代价是 mailId=index 的脆弱性.**

---

**前置笔记**：
- notes/13 项目终章工具 - Mail 异步通道概念
- notes/30 持久化 - mail collection (ownerUid indexed) + deleteAccount 清理
- notes/38 Inventory - ActionReason.MailAttachment + addItem 4 钩子
- notes/47 Plugin/Event - PlayerReceiveMailEvent 可取消
- notes/50 Resin - lazy evaluation 模式 (save 检测过期同理)

**关联文件**：
- `Mail.java`(147) - Entity + MailContent + MailItem
- `MailHandler.java`(101) - per-Player 管理
- `PacketGetMailItemRsp.java` - 领取附件核心逻辑
- `HandlerGetMailItemReq.java`(15) - 领取入口
- `HandlerDelMailReq` / `HandlerReadMailNotify` / `HandlerChangeMailStarNotify` / `HandlerGetAllMailReq`
- `DatabaseHelper.getAllMail / saveMail / deleteMail` (notes/30)

**研究的源代码**: 248 行 Mail 核心 + 6 Handler + PacketGetMailItemRsp。
