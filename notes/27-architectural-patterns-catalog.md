# 27 · 架构模式总目录 · 26 篇笔记的横向提炼

研究 26 篇笔记后，发现一些**跨系统反复出现的设计模式**。本笔记**横向提炼**这些模式，每个模式给出：
1. **何时复用** —— 一句话场景识别
2. **核心结构** —— 代码骨架
3. **真实出现位置** —— 哪些 notes/系统用了
4. **取舍** —— 优势与代价
5. **不要这样用** —— 反例

→ 这是"如果让你从零设计大型在线 RPG，应该参考这些模式"的**架构师备忘录**。

---

## 模式 1：注解 + 反射 + 自动注册 handler（出现 7+ 次 ★★★）

**何时复用**：你有一个 `XxxType` 枚举（30-100 个值），每种 type 需要不同的处理逻辑。

**核心结构**：
```java
// 1. 抽象基类
abstract class XxxHandler {
    abstract boolean execute(...);
}

// 2. 注解
@Retention(RUNTIME)
@interface XxxValue {
    XxxType value();
}

// 3. 启动时反射注册
public void registerHandlers() {
    var classes = reflector.getSubTypesOf(XxxHandler.class);
    for (var cls : classes) {
        if (cls.isAnnotationPresent(XxxValue.class)) {
            int code = cls.getAnnotation(XxxValue.class).value().getValue();
            handlerMap.put(code, cls.getDeclaredConstructor().newInstance());
        }
    }
}

// 4. 子类只标注解
@XxxValue(SOME_TYPE)
public class SomeHandler extends XxxHandler { ... }
```

**真实出现位置**：

| 系统 | 注解 | 笔记 |
|---|---|---|
| Quest | `@QuestValueCond/Content/Exec` | notes/02 |
| Scene Script | (反射 + scriptlib_handlers/) | notes/14 |
| Ability | `@AbilityAction` / `@AbilityMixin` | notes/16 |
| Dungeon | `@DungeonValue` | notes/19 |
| Challenge | `@ChallengeTypeValue` | notes/19 |
| Activity | `@GameActivity` / `@ActivityWatcherType` | notes/20 |

**取舍**：
- ✅ 新增 type 零侵入（加枚举 + 写子类 + 标注解）
- ✅ 编译期类型安全（注解 value 是枚举）
- ❌ 启动期反射开销（一次性，~100ms 量级）
- ❌ IDE 难追溯（grep 注解才能找到所有实现）

**不要这样用**：
- 类型数 < 5 个时（直接 switch 更清晰）
- 类型频繁动态加载/卸载（反射不适合热替换）

---

## 模式 2：cond + LogicType + 进度数组（出现 5+ 次 ★★★）

**何时复用**：你需要表达"满足某些条件即触发某操作"的可配置规则。

**核心结构**：
```java
class SubXxx {
    List<Condition> conds;   // 多个独立条件
    LogicType combLogic;     // AND / OR / NOT / 等组合
    int[] progress;          // 每条件一个 int 槽位
}

// 触发评估
public boolean isFinished() {
    int[] finished = new int[conds.size()];
    for (int i = 0; i < conds.size(); i++) {
        finished[i] = conds.get(i).check() ? 1 : 0;
    }
    return LogicType.calculate(combLogic, finished);
}
```

**真实出现位置**：

| 系统 | 字段 | 笔记 |
|---|---|---|
| Quest | finishCond + finishCondComb + finishProgress | notes/02 |
| Quest | failCond + failCondComb + failProgress | notes/02 |
| Quest | acceptCond + acceptCondComb | notes/02 |
| Dungeon | passConfigData.conds + LogicType + finishedConditions | notes/19 |
| Scene Trigger | trigger.condition + 单条 | notes/14 |
| Talk | beginCond + beginCondComb (LOGIC_AND) | notes/08 |

**LogicType 9 种组合**（来自 notes/02）：
```
LOGIC_NONE / LOGIC_AND / LOGIC_OR / LOGIC_NOT
LOGIC_A_AND_ETCOR (A 必须 + 后面任一)
LOGIC_A_AND_B_AND_ETCOR (A,B 必须 + 后面任一)
LOGIC_A_OR_ETCAND / LOGIC_A_OR_B_OR_ETCAND
LOGIC_A_AND_B_OR_ETCAND
```

**取舍**：
- ✅ 配表驱动（策划改 cond 不动代码）
- ✅ 表达力强（9 种组合够用）
- ❌ 复杂逻辑要拆 SubQuest（不能在单个 SubQuest 写 `(A AND B) OR (C AND D)`）

---

## 模式 3：事件总线 + 异步 4 线程池（出现 5+ 次 ★★★）

**何时复用**：业务系统之间需要解耦但又要互通——一方"喊事件"，另一方按需订阅。

**核心结构**：
```java
class XxxManager {
    private static final ExecutorService eventExecutor 
        = new ThreadPoolExecutor(4, 4, 60, SECONDS, 
            new LinkedBlockingDeque<>(1000), threadFactory, AbortPolicy);

    public void queueEvent(EventType type, Object... params) {
        eventExecutor.execute(() -> triggerEvent(type, params));
    }

    private void triggerEvent(EventType type, Object... params) {
        // 按 type 找 handler 并调用
    }
}
```

**真实出现位置**：

| 系统 | 总线 | 笔记 |
|---|---|---|
| Quest | `queueEvent(QuestCond/Content)` 4 线程 | notes/02 |
| Scene Script | `callEvent(EventType)` 4 线程 + ThreadLocal 隔离 | notes/14 |
| Ability | `onAbilityInvoke` 4 线程 | notes/16 |
| Codex | (寄生设计, 直接调用 - 无总线) | notes/17 |
| BattlePass | `triggerMission(WatcherTriggerType)` 同步 | notes/22 |

**关键经验**（notes/14 踩坑记录）：
```java
/**
 * ThreadLocal 嵌套清理 NPE：
 * CallEvent → set TL → ScriptLib.xxx → CallEvent → set TL → remove → NPE
 * 解法：强制每个事件投递走线程池, 物理隔离调用栈。
 */
eventExecutor.execute(() -> realCallEvent(params));
```

**取舍**：
- ✅ 业务系统完全解耦（背包系统不知道任务系统存在）
- ✅ 异步避免嵌套调用 NPE
- ❌ 4 线程池存在调度延迟（事件不立即处理）
- ❌ 不适合需要返回值的同步操作

**不要这样用**：
- 数据完整性要求严格的事务（用同步操作）
- 系统数 < 3 个（直接调用更简单）

---

## 模式 4：倒排索引（出现 4+ 次 ★★★）

**何时复用**：你有 N 个"订阅者"和 M 个事件类型，每次事件都要找到关心的订阅者。

**核心结构**：
```java
// 启动时建索引
Map<EventType, List<Subscriber>> index = new HashMap<>();
for (Subscriber s : allSubscribers) {
    index.computeIfAbsent(s.getEventType(), k -> new ArrayList<>()).add(s);
}

// 运行时 O(1) 查
List<Subscriber> targets = index.get(eventType);
for (Subscriber s : targets) {
    s.onEvent(...);
}
```

**真实出现位置**：

| 系统 | 索引 | 笔记 |
|---|---|---|
| Quest | `beginCondQuestMap: Map<key, List<SubQuestData>>` | notes/02 |
| Scene Script | `triggersByEvent: Map<EventType, Set<Trigger>>` | notes/14 |
| BattlePass | `cachedTriggers: Map<WatcherTriggerType, List<MissionData>>` | notes/22 |

**Quest 系统的两层索引精妙**（notes/02）：
```
key = type + 首参数 + 字符串参数
例: "QUEST_COND_STATE_EQUAL100101"
   ↓
List<SubQuestData> 候选 (宽匹配)
   ↓
handler 用完整 param 二次验证 (精确)
```

**取舍**：
- ✅ O(1) 查找避免 O(N) 全扫
- ✅ 索引体积小（只按首参数分桶）
- ✅ 表达力不丢（handler 二次验证）

**不要这样用**：
- 数据量 < 1000 时（直接遍历更简单）
- key 频繁变化（索引维护成本高）

---

## 模式 5：统一入口 + 内部多态（出现 3+ 次 ★★★）

**何时复用**：多种数据/操作走相同流程但有微差异（如"加道具"对武器/材料/货币方式不同）。

**核心结构**：
```java
public boolean addItem(GameItem item, ActionReason reason) {
    switch (item.getItemData().getItemType()) {
        case ITEM_VIRTUAL  -> addVirtualItem(...);
        case ITEM_WEAPON    -> addToTab(WEAPON_TAB, ...);
        case ITEM_RELIQUARY -> addToTab(RELIC_TAB, ...);
        case ITEM_MATERIAL -> addToTab(MATERIAL_TAB, ...);
        // ...
    }
    triggerAddItemEvents(...);   // 反向通知 Quest/BP/Activity
}
```

**真实出现位置**：

| 系统 | 入口 | 笔记 |
|---|---|---|
| Inventory | `Inventory.addItem(itemId, count, ActionReason)` | notes/15 |
| Reward | 各种 reward 来源 → addItemParamDatas | notes/15 |
| Crafting | 4 种制作系统 → 都走 payItems + addItem | notes/25 |

**取舍**：
- ✅ 11+ 个奖励来源全部一个入口（不会"漏更新"）
- ✅ 审计统一（ActionReason 100+ 分类）
- ✅ 反向通知容易（统一 `triggerAddItemEvents`）

---

## 模式 6：审计标签全量细分（ActionReason 100+）

**何时复用**：业务有合规/审计需求，需要追溯每次操作的来源。

**核心结构**：
```java
public enum ActionReason {
    None(0),
    QuestReward(2),
    Shop(4),
    Gacha(30),
    MailAttachment(12),
    DungeonFirstPass(20),
    ForgeOutput(34),
    ForgeReturn(35),    // ★ 同一系统多个 reason 区分
    OpenChest(39),
    MonsterDie(37),
    // ... 100+ 个
}

// 调用时必带 reason
inventory.addItem(item, ActionReason.Gacha);
```

**真实出现位置**：notes/15 详细列出 100+ ActionReason

**为什么这么细**（notes/15 / notes/25）：
- 客户端弹窗文本（"通过任务获得" vs "在邮件附件中"）
- 反作弊审计（异常路径会暴露）
- 数据分析（哪条路径产生最多 mora）
- bug 追溯（玩家"我突然多了"→ 客服查日志）

**取舍**：
- ✅ 商业级 KYC，几乎"零成本"实现合规
- ❌ 必须从一开始就丰富（后期补加丢失大量历史）

---

## 模式 7：跨系统事件源（WatcherTriggerType 150+）

**何时复用**：多个独立系统需要响应同一玩家行为（如"击杀怪物"既影响任务又影响战令又影响活动）。

**核心结构**：
```java
public enum WatcherTriggerType {
    TRIGGER_LOGIN,
    TRIGGER_KILL_MONSTER,
    TRIGGER_OBTAIN_MATERIAL_NUM,
    TRIGGER_FINISH_DUNGEON,
    TRIGGER_GAIN_AVATAR,
    TRIGGER_COOK_NUM,
    TRIGGER_FORGE_NUM,
    // ... 150+
}

// 业务系统 fire
private void triggerAddItemEvents(GameItem result) {
    player.getBattlePassManager().triggerMission(TRIGGER_OBTAIN_MATERIAL_NUM, ...);
    player.getQuestManager().queueEvent(QUEST_CONTENT_OBTAIN_ITEM, ...);
    // Activity 也订阅同一 WatcherTriggerType
}
```

**真实出现位置**：

| 系统 | 用途 | 笔记 |
|---|---|---|
| BattlePass | mission 进度 | notes/22 |
| Activity | watcher 进度 | notes/20 |
| Quest | 部分 cond/content 重叠 | notes/02 |
| Inventory | addItem 触发 | notes/15 |

**取舍**：
- ✅ 一次 trigger 通知 N 个系统（不需要每个系统都 hook 业务代码）
- ✅ 新系统添加只需订阅枚举值
- ❌ 枚举值容易膨胀（150+ 是真实数字）

---

## 模式 8：服务器权威 vs 客户端权威（混合架构）

**何时复用**：实时类游戏，既要操作手感又要数据安全。

**划分原则**（notes/01 / notes/16 实测得出）：
```
[客户端权威]              [服务器权威]
- 元素反应判定           - HP / 实际血条
- 元素附着              - 能量 / 元素粒子
- 角色面板属性          - 体力
- 移动预测              - 摔伤计算
- 伤害数值              - 死亡判定
- 动画状态              - 任务进度
- UI 渲染               - 经济（货币/物品/抽卡）
                       - 玩家位置最终确认
                       - 商业核心（gacha/inventory/avatar level）
```

**判定标准**：**会写入存档 → 服务器；只用于即时反馈 → 客户端**。

**真实出现位置**：notes/01, /16 (Combat), /21 (Gacha 完全服务器), /23 (HomeWorld 客户端编辑)

**反作弊 hook**（notes/16）：
```java
// 元素爆发期间无敌
if (attackerId != currentAvatarEntity && abilityInvulnerable) break;

// 摔伤是服务器算
if (cachedLandingSpeed < -28) damageFactor = 1f;  // 秒杀
```

→ **服务器不重算所有伤害**（开销大），只在**关键 invariant 时刻 sanity check**。

---

## 模式 9：写时拷贝 + 默认配表（出现 3+ 次）

**何时复用**：玩家数据从配表初始化，但之后**玩家修改不影响配表也不影响其他玩家**。

**核心结构**：
```java
public XxxItem getXxxItem(int id) {
    return playerMap.computeIfAbsent(id, e -> {
        var defaultData = GameData.getDefaultDataMap().get(id);
        return XxxItem.parseFrom(defaultData, id);   // 复制成玩家私有
    });
}
```

**真实出现位置**：

| 系统 | 用法 | 笔记 |
|---|---|---|
| HomeWorld | 首次访问 → 复制默认布局 | notes/23 |
| Activity | PlayerActivityData 初始化 | notes/20 |
| BattlePass | mission 状态首次创建 | notes/22 |

**取舍**：
- ✅ 配表更新不影响已有玩家（版本固化）
- ✅ 玩家间数据隔离
- ❌ 老玩家拿不到配表的"内容更新"（需手动迁移）

---

## 模式 10：分段 ID 命名空间（出现 4+ 次）

**何时复用**：你有上万个 ID 但希望"看 ID 一眼能识别类型"。

**真实出现位置**：

```
[物品 ID] (notes/15)
1xx     货币/经验
2xx     核心货币 (原石/摩拉)
1xxxxx  实物消耗
1xxxx-15xxx  武器
2xxxx-25xxx  圣遗物
1xxxxxxx  角色

[Scene ID] (notes/18, 23)
3, 5, 7, 8...  普通场景
2000+         尘歌壶 realmId
副本 id        副本场景

[NPC ID] (notes/12)
1xxx     角色 NPC (1056=纳西妲, 1048=夜兰)
12xxx-13xxx  可交互物品/标记
```

**取舍**：
- ✅ 调试时一眼识别物品类型
- ✅ 路由逻辑简化（`if sceneId >= 2000 → home scene`）
- ❌ 一旦确定难变更（影响大量配表）

---

## 模式 11：异步 + 时间戳实时计算（出现 2 次）

**何时复用**：异步任务需要"完成多少"，但不想后台 cron 检查每个任务。

**核心结构**：
```java
class ActiveXxxData {
    int startTime;
    int totalCount;
    int costTimePerItem;
}

// 玩家查询时实时算
int finishedCount = (currentTime - startTime) / costTimePerItem;
finishedCount = Math.min(finishedCount, totalCount);
```

**真实出现位置**：

| 系统 | 用法 | 笔记 |
|---|---|---|
| Forge | 锻造队列 | notes/25 |
| Compound | 复合等待 | notes/25 |
| Daily Reset | 每天检查跨日 | notes/04 |

**取舍**：
- ✅ 不需要后台 cron（节省 CPU）
- ✅ 玩家上线时一次性算出（懒计算）
- ✅ 离线时间也在跑（留存设计）
- ❌ 时间戳必须是服务器侧（防客户端伪造）

**绝妙变体：懒检查日重置**（notes/04）：
```java
// Player.onTick (每个玩家自己检查)
if (currentDate.isAfter(lastResetDate)) {
    doDailyReset();
}
```
→ **5000 万玩家维护 5000 万 cron 是噩梦**，但**每玩家 O(1) onTick 自检**完美。

---

## 模式 12：双向冗余存储 / 反范式（出现 1 次但绝妙）

**何时复用**：双向关系（好友、关注、订阅），读频率远高于写。

**核心结构**：
```java
// 一段关系 = 两条记录
class Friendship {
    int ownerId;      // 这条记录的主人
    int friendId;     // 对方
    int askerId;      // 谁先发起
    boolean isFriend; // 已确认/pending
}

// 创建关系
new Friendship(A, B, A).save();   // A 视角
new Friendship(B, A, A).save();   // B 视角
```

**真实出现位置**：notes/26 Friend 系统

**优势**：
- 锁简化（每条只 owner 能改）
- 查询快（按 ownerId 索引）
- 离线对方友好（DB 直接改）

---

## 模式 13：多态 detail 字段（出现 2 次）

**何时复用**：通用容器持有不同子类型的具体数据（如不同活动有不同游戏数据）。

**核心结构**：
```java
@Entity
class GenericContainer {
    int activityId;
    int uid;
    Map<Integer, WatcherInfo> watcherInfoMap;   // 通用字段
    DetailObject detail;                          // 多态：每子系统有自己类型
    
    public <T> T getDetail(Class<T> clazz) {
        return (T) detail;
    }
}
```

**真实出现位置**：

| 系统 | 容器 | 笔记 |
|---|---|---|
| Activity | PlayerActivityData with AsterGamePlayerData/DragonspinePlayerData... | notes/20 |
| Player | Player 含多个独立 manager (AvatarStorage/Inventory/Codex/...) | 全局 |

**取舍**：
- ✅ 每个子系统数据独立，互不干扰
- ✅ 加新子系统不动核心 schema
- ❌ 类型擦除（运行时需要 instanceof / cast）

---

## 模式 14：N 段状态机（区别于二段）

**何时复用**：你的状态需要明确的"等待人工操作"环节。

**真实出现位置**：

| 系统 | 状态机 | 笔记 |
|---|---|---|
| Quest SubQuest | UNSTARTED → UNFINISHED → FINISHED/FAILED (4 段) | notes/03 |
| BattlePass Mission | UNFINISHED → FINISHED → POINT_TAKEN (3 段, 含"领取") | notes/22 |
| MainQuest | NONE → FINISHED/FAILED/CANCELED (4 段) | notes/03 |

**3 段 BP Mission 的精妙**：
```
任务进度满 → FINISHED（弹窗"任务完成"）
玩家手动点"领取" → POINT_TAKEN（弹窗"获得 XXX 积分"）
```
→ **强化成就感**——比"自动加分无感"用户体验好得多。

---

## 模式 15：插件化（每个特化场景独立 handler）

**何时复用**：业务有 N 种相似但具体差异大的玩法（如限时活动）。

**真实出现位置**：notes/20 Activity 系统 6 个独立 ActivityHandler

**核心结构**：
```java
abstract class ActivityHandler<DETAIL> {
    abstract DETAIL onInitPlayerActivityData(...);
    abstract void onProtoBuild(...);
}

@GameActivity(NEW_ACTIVITY_ASTER)
class AsterActivityHandler extends ActivityHandler<AsterGamePlayerData> {
    // 风花节专属逻辑
}

@GameActivity(NEW_ACTIVITY_DRAGONSPINE)
class DragonspineActivityHandler extends ActivityHandler<DragonspinePlayerData> {
    // 龙脊雪山专属逻辑
}
```

**取舍**：
- ✅ 新活动开发零侵入
- ✅ 失败也不影响其他活动
- ❌ 每个活动是独立 spike（简单签到也要写 Handler）
- ❌ 兜底用 `DefaultHandler` 功能弱

---

## 模式 16：寄生型 vs 独立型（系统选型）

**何时选择"寄生"**：逻辑简单 + 触发点固定 + 不需要异步池。

**真实对比**（notes/17 Codex vs Quest）：

| 维度 | 寄生型（Codex）| 独立型（Quest）|
|---|---|---|
| 实现复杂度 | 极低（130 行）| 高（数千行）|
| 触发方式 | 业务系统直接调 | 通过事件总线 |
| 扩展新触发点 | 改业务代码 | 完全独立 |
| 适用 | **逻辑简单 + 触发点固定** | **逻辑复杂 + 大量分支** |

**Codex 的寄生设计**：
```java
// 散落在业务系统：
Inventory.putItem -> codex.checkAddedItem(item)
Scene 实体死亡 -> codex.checkAnimal(target, KILL)
GameMainQuest.finish -> sendPacket(CodexDataUpdateNotify)
```

→ **该抽象的抽象，该简化的简化**——这是工程判断力。

---

## 模式 17：配表热重载（出现 1 次但商业必须）

**何时复用**：运营要求"改配置不停服"。

**核心结构**（notes/21 Gacha banner.json）：
```java
@Listener(GAME_SERVER_TICK)
public synchronized void watchBannerJson(GameServerTickEvent tickEvent) {
    // 检测 mtime 变化
    if (bannerJsonFile.lastModified() > lastLoadTime) {
        reload();
        lastLoadTime = bannerJsonFile.lastModified();
    }
}
```

→ **运营改 banner.json → 下次 tick 自动重载**。每两周一次新 banner 不需要技术支持。

---

## 模式 18：HTTP 路由作为补充协议

**何时复用**：数据量大、合规要求、浏览器友好的场景。

**真实出现位置**：notes/21 Gacha 历史
```java
String record = "http://...:port/gacha?s=" + sessionKey + "&gachaType=" + gachaType;
```

**为什么不走 packet**：
- 抽卡历史数据量大（数千条/账号）
- 中国大陆合规要求"概率公示"必须可在网页查看
- 客服可直接在浏览器打开查日志

→ **Game packet 不是万能锤**——HTTP 也有适用场景。

---

## 模式 19：UGC = 客户端编辑 + 服务器存储

**何时复用**：玩家创造内容（自定义场景、装扮、谱面等）。

**真实出现位置**：notes/23 HomeWorld

**核心分工**：
```
[客户端]                              [服务器]
3D 编辑器                              数据存储
碰撞检测                              权限控制
渲染                                  审核
玩家拖放                              持久化
↓ 保存
HomeBlockArrangementInfo proto  ─────→ HomeSceneItem.update(arrangementInfo)
```

→ **不要尝试在服务器做 3D 编辑**——算力大且无意义。

---

## 模式 20：异步社交（不需双方在线）

**何时复用**：UGC 类内容、留言板、点赞、邮件、好友家访问。

**真实出现位置**：notes/23 HomeWorld 三档进入权限
```java
DIRECT          离线也能进
NEED_CONFIRM    在线需房主同意
REFUSE          完全拒绝
```

**vs 同步社交**（notes/18 MP）：
- MP 必须双方在线
- HomeWorld 离线也能造访
- **异步社交是 UGC 核心价值**

---

## 模式 21：数据驱动到极致（配表 vs 代码）

**何时复用**：游戏数值/规则可以用表格表达。

**真实出现位置**：

| 系统 | 配表 | 笔记 |
|---|---|---|
| Avatar 升级 | AvatarPromoteData × promoteLevel | notes/24 |
| Quest 触发 | SubQuestData JSON | notes/02 |
| Reward | RewardData (rewardId → 9 slot itemList) | notes/15 |
| Gacha | banner.json (热重载) | notes/21 |
| Activity | ActivityConfig.json | notes/20 |
| BattlePass | BattlePassMissionData × N | notes/22 |

**精髓**（notes/02 / notes/24）：
- **代码只是"按 id 查表 + 累加"**
- 几乎所有数值/规则来自配表
- 策划无需改代码即可调整数值

---

## 模式 22：四层保底叠加（伪随机数学）★

**何时复用**：抽卡 / 概率开箱 / 任何"碰运气"系统。

**真实出现位置**：notes/21 Gacha 4 层保底
1. 整体 pity（出 5/4 星时机）→ Linear interpolation 软保底
2. Featured pity（UP 还是常驻）→ 大保底 50/50
3. Epitomized（武器池定轨）→ 累计失败必出指定
4. Pool balance（常驻池角色 vs 武器）

**线性插值的"伪随机感"**（notes/21）：
```java
DEFAULT_WEIGHTS_5 = {{1,75}, {73,150}, {90,10000}};
//                    ↑       ↑          ↑
//                  低概率   软保底      硬保底
```
→ 第 75-89 抽**线性概率提升**，让玩家"感觉手气好"——比硬切换体验好 10 倍。

---

## 模式 23：embedded entity 持久化

**何时复用**：父对象总是和子对象一起读取 / 写入（无独立访问需求）。

**真实出现位置**：

| 系统 | 嵌入 | 笔记 |
|---|---|---|
| GameHome | 内嵌 sceneMap / blockItems / furnitureLists | notes/23 |
| GameMainQuest | 内嵌 childQuests | notes/03 |
| BattlePassManager | 内嵌 missions / takenRewards | notes/22 |

**反例**（独立 entity）：
- Friendship（双向，需独立查询）
- GachaRecord（按 timestamp 查询，需独立 entity）
- Mail（按收件人查询）

---

## 模式 24：Counter + Reset 模式

**何时复用**：日常/周常活动，需要"做过/没做过"+"按时间清零"。

**真实出现位置**：

| 系统 | 计数 | 重置 | 笔记 |
|---|---|---|---|
| BattlePass | cyclePoints (周积分) | 周一 0 点 | notes/22 |
| Daily Task | finishedCurrentTasks | 每天 0 点 | notes/04 |
| Resin Buy Count | resinBuyCount | 每天 0 点 | notes/04 |
| Forge Points | forgePoints (锻造点) | 每天恢复 | notes/25 |
| Weekly Boss | takeNum / discountNum | 每周一 | notes/04 |

**与"懒检查"配合**（notes/04）：
```java
// Player.onTick 自检
if (currentDate.isAfter(lastDailyReset)) doDailyReset();
```
→ **不需要 cron**。每玩家自己跨天检查。

---

## 模式 25：包级 ItemUseAction 抽象

**何时复用**："使用物品"产生不同效果（解锁配方、加经验、给奖励）。

**真实出现位置**：notes/15, /22, /25

```
ItemUseAddExp                  加经验
ItemUseAddSelectItem            选定物品
ItemUseGrantSelectReward        选定奖励 ID
ItemUseUnlockCombine            解锁合成配方
ItemUseUnlockCookRecipe         解锁菜谱
ItemUseUnlockForge              解锁锻造蓝图
ItemUseUnlockCodex              解锁图鉴书
ItemUseCombineItem              直接合成
```

→ **物品就是"使用后产生效果"的抽象**。各种"礼包"、"配方书"、"经验书"都是 ItemUseAction 的子类。

---

## 综合架构哲学：5 条最重要的工程原则

### 原则 1：**数据驱动 > 代码驱动**

- 数值在配表，行为在 handler
- 策划改配表不改代码
- 配表用 ID 引用，永不硬编码

### 原则 2：**事件总线 + 异步隔离**

- 业务系统不直接调用对方
- 通过事件解耦
- 4 线程异步池避免嵌套 NPE

### 原则 3：**统一入口 + 多态分流**

- 所有"加道具"走 Inventory.addItem
- 所有"任务进度"走 queueEvent
- 所有"活动数据"走 ActivityManager
- **绝不要让多个系统各自实现"加道具"**

### 原则 4：**审计无处不在**

- ActionReason 100+ 类型
- 持久化每次抽卡 / 物品发放 / 任务完成
- 客服可查任意操作历史

### 原则 5：**服务器权威 + 客户端预测**

- 数据写存档 → 服务器
- UI/手感 → 客户端
- 反作弊靠 invariant check, 不重算所有

---

## 给做大型在线 RPG 服务端的总建议

按重要性排：

1. **统一物品入口 + ActionReason 100+** —— 不做这个会被外挂吃干净
2. **事件总线** —— 不做这个会写出意大利面代码
3. **配表驱动** —— 不做这个策划要找你改一辈子代码
4. **服务器权威** —— 不做这个商业模型崩盘
5. **注解 + 反射 handler 注册** —— 不做这个新功能开发慢 10 倍
6. **倒排索引** —— 不做这个性能撑不住 100 万玩家
7. **审计日志** —— 不做这个客服跟你打架
8. **异步 + 时间戳计算** —— 不做这个 cron 失控
9. **写时拷贝默认配表** —— 不做这个版本管理一团乱
10. **混合权威** —— 不做这个延迟反馈毁手感

---

## 26 篇笔记 → 25 个模式 → 10 条原则

**研究路径**：
```
26 篇笔记 (具体实现细节)
    ↓ 横向提炼
25 个架构模式 (本笔记)
    ↓ 抽象总结
10 条工程原则
    ↓ 应用
任何大型在线 RPG 服务端开发
```

→ 这就是研究项目的最终交付：**从代码考古到设计哲学**。

---

## 参考所有笔记

- notes/01-04: 服务器架构 + 任务系统设计
- notes/05-07: 真实任务案例
- notes/08-09: Talk + Lua 桥
- notes/10-13: 反混淆 + 翻译 + 可视化（工具篇）
- notes/14: Scene Script
- notes/15-16: Reward + Combat
- notes/17-19: Codex + Multiplayer + Dungeon
- notes/20-22: Activity + Gacha + BattlePass
- notes/23: HomeWorld
- notes/24-25: Avatar + Crafting
- notes/26: Friend / Social
- **notes/27（本篇）：横向提炼**
