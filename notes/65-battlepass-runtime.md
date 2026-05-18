# BattlePass 战令运行时深度剖析

> 第 65 篇：notes/22 只覆盖战令**配置**，运行时从未挖。本弧 notes/60/64 多次出现 `getBattlePassManager().triggerMission(...)` 却没展开终点。本篇追完**触发扇入 → 任务进度 → 点数升级 → 领奖**全链，串起 4 条既有线索：notes/41 事件总线扇入、notes/30 独立 collection vs 内嵌持久化、[[grasscutter-resource-execution-models]] 三分法（懒每日重置）、notes/64 锻造点重置同位；并抓到一个 **自引用 clamp 等级溢出 bug**（出现 2 次）。

---

## 0. 为什么这一篇重要：扇入终点 + 持久化反例

前面笔记一直看到各系统 `triggerMission(TRIGGER_XXX)` 往战令打点，但战令**怎么收、怎么转点数、怎么升级发奖**是黑盒。同时它是 notes/30 持久化模型的**关键反例**——不像合成台/锻造/派遣内嵌 Player 文档，战令是**独立 MongoDB collection**。本篇补齐这两块拼图。

---

## 1. 战令运行时全图

```
┌── 扇入: 9+ 系统打点 (notes/41 WatcherTriggerType) ──────────┐
│ Gacha 抽卡 / Inventory 获得-消耗 / Login / MonsterDie /     │
│ Forge(notes/64) / Resin / Dungeon 完成 / ...                │
│   每处: player.getBattlePassManager().triggerMission(T,p,n) │
└────────────────────────┬───────────────────────────────────┘
                         ↓ 委托
┌── BattlePassSystem (BaseGameSystem, 服务器级) ──────────────┐
│ cachedTriggers: Map<WatcherTriggerType, List<MissionData>>  │
│   ★ 启动时一次性预索引 (所有玩家共享只读配置, 正确的 static)│
│ triggerMission: 查触发表 → 过滤 mainParams → 每个匹配任务   │
│   loadMissionById → addProgress → 达标置 FINISHED → save     │
└────────────────────────┬───────────────────────────────────┘
                         ↓ per-player 状态
┌── BattlePassManager @Entity("battlepass") ★独立 collection ─┐
│ ObjectId id / @Indexed ownerUid / point / cyclePoints /     │
│ level / missions Map / takenRewards Map                     │
│ save() → DatabaseHelper.saveBattlePass(this) ★非 player.save│
└────────────────────────┬───────────────────────────────────┘
                         ↓ 玩家操作
  takeMissionPoint(领任务点→addPointsDirectly→升级)
  takeReward(领等级奖→去重/等级/excel 校验→addItems)
  buyLevels(原石买级)
  doDailyReset (notes 三分法: 懒每日/周重置, 无 cron)
```

---

## 2. 持久化反例：独立 collection 而非内嵌（notes/30 线）

```java
@Entity(value = "battlepass", useDiscriminator = false)
public class BattlePassManager extends BasePlayerDataManager {   // ★ 不是 BasePlayerManager
    @Id @Getter private ObjectId id;
    @Indexed private int ownerUid;                                // ★ 自带索引, 按 uid 查
    ...
    public void save() { DatabaseHelper.saveBattlePass(this); }   // ★ 独立保存, 非 player.save()
}
```

→ 对比本弧持久化模型两类：

| 方式 | 代表 | 机制 |
|---|---|---|
| **内嵌 Player 文档** | 合成台/锻造/派遣 (notes/62/64/59) | `@Entity` 对象进 `Map`/`List` 字段，随 `player.save()` 落盘 |
| **独立 collection** | **BattlePass (本篇)** | 自有 `_id` + `@Indexed ownerUid`，`DatabaseHelper.saveBattlePass` 独立读写 |

→ **为何独立**：战令含 `missions` + `takenRewards` 两张大 Map，访问独立于主玩法（打开战令界面才需），且写入频繁（每次 triggerMission 都 save）。拆出独立 collection 避免每次打点重写整个 Player 巨文档——**性能驱动的持久化拆分**。
→ 这补全 notes/30：grasscutter per-player 数据**不是非黑即白内嵌**，按"访问独立性 + 写频"决定内嵌还是独立 collection。`BasePlayerDataManager`（区别于 `BasePlayerManager`）正是"独立持久化的玩家子数据"基类标志。

---

## 3. 扇入引擎：BattlePassSystem（notes/41 事件总线的 fan-in）

notes/41 确立 grasscutter 有 4 套并行事件系统，`WatcherTriggerType` 是其一。本篇看到它的**消费侧**：

```java
public BattlePassSystem(GameServer server) {
    // 启动时按触发类型预索引所有任务配置
    for (BattlePassMissionData md : GameData.getBattlePassMissionDataMap().values())
        if (md.isValidRefreshType())
            cachedTriggers.computeIfAbsent(md.getTriggerType(), e -> new ArrayList<>()).add(md);
}

public void triggerMission(Player player, WatcherTriggerType triggerType, int param, int progress) {
    List<BattlePassMissionData> triggerList = getTriggers().get(triggerType);
    if (triggerList == null || triggerList.isEmpty()) return;        // 快速短路
    for (BattlePassMissionData data : triggerList) {
        if (param != 0 && !data.getMainParams().contains(param)) continue;   // 参数过滤
        BattlePassMission mission = player.getBattlePassManager().loadMissionById(data.getId());
        if (mission.isFinshed()) continue;
        mission.addProgress(progress, data.getProgress());           // 进度封顶
        if (mission.getProgress() >= data.getProgress())
            mission.setStatus(MISSION_STATUS_FINISHED);
        player.getBattlePassManager().save();                         // ★ 每次打点即 save
        player.sendPacket(new PacketBattlePassMissionUpdateNotify(mission));
    }
}
```

要点：
1. **服务器级预索引 `cachedTriggers`**：启动时把 N 个任务配置按 `triggerType` 分桶。所有玩家共享这份**只读配置**——这是**正确的 static-ish 全局缓存**，与 notes/62 合成台 `static unlocked`（错误地把玩家态 static）形成正反对照：**判据是"被 static 的是只读配置还是玩家可变态"**。
2. **同构架构再现**（[[grasscutter-同构架构模式]]）：预索引 `Map<触发类型, List<配置>>` 启动建表、运行时 O(1) 查桶——与 notes/41/16 等"注册式索引"骨架同款。
3. **fan-in 广度**：Gacha/Inventory(获得+消耗)/Login/MonsterDie/Forge/Resin/Dungeon… 9+ 系统打点，全收口于此。**triggerMission 是 grasscutter 任务进度的总线汇聚点**。
4. **每次打点即 save**：高频写——这正是 §2 拆独立 collection 的动因（否则每次打点重写 Player 巨文档）。
5. **mainParams 过滤**：`param==0` 跳过校验（通配），否则要求 param ∈ 配置 mainParams（如"用风元素角色"才计数特定任务）。

---

## 4. 点数 → 等级经济 + 自引用 clamp bug

```java
public void addPointsDirectly(int points, boolean isWeekly) {
    int amount = points;
    if (isWeekly) amount = Math.min(amount, BATTLE_PASS_POINT_PER_WEEK - this.cyclePoints);  // 周上限 10000
    if (amount <= 0) return;
    this.point += amount;
    this.cyclePoints += amount;
    if (this.point >= BATTLE_PASS_POINT_PER_LEVEL && this.level < BATTLE_PASS_MAX_LEVEL) {
        int levelups = Math.floorDiv(this.point, BATTLE_PASS_POINT_PER_LEVEL);   // 每 1000 点 1 级
        levelups = Math.min(levelups, BATTLE_PASS_MAX_LEVEL - levelups);          // ❌ BUG (见下)
        this.point = this.point - (levelups * BATTLE_PASS_POINT_PER_LEVEL);
        this.level += levelups;
    }
}
```

常量（GameConstants）：每级 1000 点 / 周上限 10000 点 / 满级 50 / 买级 150 原石。

### 4.1 ❌ 自引用 clamp 等级溢出 bug（出现 2 次）

`levelups = Math.min(levelups, BATTLE_PASS_MAX_LEVEL - levelups);`

→ 满级保护本应是 `MAX_LEVEL - this.level`（还能升多少级），却写成 `MAX_LEVEL - levelups`（**用自己 clamp 自己**，无意义）。
→ 反例：`level=48`，攒到 `point=5000` → `levelups=5`，`min(5, 50-5=45)=5` → `level=48+5=53 > 50` **溢出满级**。
→ **`buyLevels` 同款 bug**：`int boughtLevels = Math.min(buyLevel, BATTLE_PASS_MAX_LEVEL - buyLevel);`——同样应是 `MAX_LEVEL - this.level`。
→ **2 处同一错误公式** = 复制粘贴扩散的逻辑 bug（类比 notes/64 payItems 缺 return 的复制扩散）。这是一类**"自引用 clamp"代码异味**：`min(x, MAX - x)` 形态，clamp 项引用了被 clamp 的量而非当前累计状态。修复：两处都改 `MAX_LEVEL - this.level`。
→ 影响：BP 等级可超 50（领奖时 `option.getTag().getLevel() > this.getLevel()` 反而更易通过 → 多领高等级奖励）。属经济漏洞。

---

## 5. 领取：takeMissionPoint / takeReward

### 5.1 takeMissionPoint（任务点 → 升级）

```java
if (missionIdList.size() > getBattlePassMissionDataMap().size()) return;  // 朴素防刷(仅查表长)
for (int id : missionIdList) {
    if (!hasMission(id)) continue;
    BattlePassMission m = loadMissionById(id);
    if (m.getData() == null) { getMissions().remove(m.getId()); continue; }  // 脏数据自愈
    if (m.getStatus() == MISSION_STATUS_FINISHED) {                          // ★ 必须 FINISHED
        addPointsDirectly(m.getData().getAddPoint(), m.getData().isCycleRefresh());
        m.setStatus(MISSION_STATUS_POINT_TAKEN);                             // 防重领
    }
}
```
→ 防重领靠**状态机**（FINISHED → POINT_TAKEN，二次请求状态不符跳过），同 notes/57 Mail `isAttachmentGot` 思想。
→ "脏数据自愈"：配置已删的任务 `getData()==null` 时从 Map 移除——容错设计。

### 5.2 takeReward（等级奖励，多层校验）

```java
if (rewardId==0 || getTakenRewards().containsKey(rewardId)) continue;   // 去重
if (option.getTag().getLevel() > this.getLevel()) continue;             // 等级门槛
BattlePassRewardData rd = getBattlePassRewardDataMap().get(CURRENT_INDEX*100 + level);
if (rd.getFreeRewardIdList().contains(rewardId)) rewardList.add(option);          // 免费档
else if (isPaid() && rd.getPaidRewardIdList().contains(rewardId)) rewardList.add(option); // 付费档
// 选择型宝箱: MATERIAL_SELECTABLE_CHEST → ITEM_USE_ADD_SELECT_ITEM / ITEM_USE_GRANT_SELECT_REWARD
```
→ 4 层校验：去重 → 等级 → excel free/paid 名单核对（**不信任客户端 rewardId**，notes/58 哲学）→ 选择型宝箱二级展开。
→ `isPaid()` **硬编码 `return true`**（注释 `ToDo: Change this when we actually support unlocking "paid" BP`）——**所有玩家白嫖付费战令奖励**。这是 grasscutter "私服全解锁"哲学的又一标本（接 notes/62 鱼类配方全解锁、§同主题）。

---

## 6. 三分法验证：懒每日/周重置（无 cron）

`Player.doDailyReset()`（onTick 内，与 notes/64 Forge notify、notes/59 Expedition 同位）：

```java
var currentDate   = LocalDate.ofInstant(Instant.ofEpochSecond(now), zone);
var lastResetDate = LocalDate.ofInstant(Instant.ofEpochSecond(getLastDailyReset()), zone);
if (!currentDate.isAfter(lastResetDate)) return;                  // ★ 懒判定: 没跨天就不重置
this.setForgePoints(300_000);                                     // notes/64 锻造点重置同位
this.getBattlePassManager().resetDailyMissions();                 // 日任务 status/progress 清零
this.getBattlePassManager().triggerMission(TRIGGER_LOGIN);        // 在线跨天也补登录任务
if (currentDate.getDayOfWeek() == DayOfWeek.MONDAY)
    this.getBattlePassManager().resetWeeklyMissions();            // 周任务(周一)
```

→ **每日/周重置 = 模型①Lazy**：无 cron/scheduler，onTick 时比较 `currentDate` vs `lastResetDate`，跨天才执行。完美符合 [[grasscutter-resource-execution-models]] 判据——"重置"是状态查询型（"自上次重置起是否过了一天/到周一"可由 now 反推）。
→ **三分法第 4 次验证**（notes/62①、63 第0类、64①、本篇① 重置子场景），且首次验证对象是"周期重置调度"而非"资源产出"——判据普适性增强。
→ `getScheduleProto()` 里 `nextSundayTime` 等也是纯 `LocalDate.now()` 现算，无持久化时间态——同 lazy 思想。

---

## 7. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 伪造 rewardId 领奖 | ✗ excel free/paid 名单核对 + 去重 |
| 重复领任务点 | ✗ FINISHED→POINT_TAKEN 状态机 |
| **攒点冲过满级 50** | **✓ 自引用 clamp bug，level 可 >50（§4.1）** |
| **buyLevels 买超满级** | **✓ 同款 clamp bug** |
| 客户端伪报 mission 进度 | ⚠ 经 triggerMission，但进度源自服务端各系统打点，客户端难直接注入 |
| 白嫖付费战令奖励 | ✓ isPaid()==true 硬编码（设计如此，私服哲学）|
| takeMissionPoint 超大列表 | ✗ size > 配置总数直接 return |

→ 校验整体扎实（excel 核对/状态机/去重），但 **§4.1 自引用 clamp 等级溢出**是真经济漏洞；isPaid 全开是私服有意取舍。

---

## 8. 关键收获

1. **BattlePass runtime 串 4 条既有线**：notes/41 事件扇入 / notes/30 持久化 / 三分法 / notes/64 锻造点同位
2. **持久化反例**：`@Entity("battlepass")` 独立 collection（`BasePlayerDataManager` + ObjectId + @Indexed ownerUid + DatabaseHelper.saveBattlePass），**非内嵌 Player**
3. **内嵌 vs 独立 collection 判据**：访问独立性 + 写频。战令高频 save + 大 Map → 拆独立避免重写 Player 巨文档（性能驱动）
4. **补全 notes/30**：grasscutter per-player 数据非非黑即白；`BasePlayerDataManager` 是"独立持久化子数据"基类标志
5. **BattlePassSystem = WatcherTriggerType fan-in 汇聚点**（notes/41 消费侧），9+ 系统打点收口
6. **服务器级 cachedTriggers 预索引**：启动 `Map<触发类型,List<配置>>` 建表，O(1) 查桶（[[grasscutter-同构架构模式]] 注册式索引再现）
7. **正反对照 notes/62**：BP `cachedTriggers` static 是**正确**（只读配置全局共享）vs Compound `static unlocked` **错误**（玩家态 static）——判据"被 static 的是配置还是玩家可变态"
8. **点数经济**：每 1000 点 1 级 / 周上限 10000 / 满级 50 / 买级 150 原石 / cyclePoints 周封顶
9. **★ 自引用 clamp bug 出现 2 次**：`Math.min(x, MAX - x)`（应 `MAX - this.level`），addPointsDirectly + buyLevels 复制扩散 → BP 等级可溢出 50
10. **新代码异味"自引用 clamp"**：clamp 上界引用被 clamp 量而非当前累计状态（候选 bug 类，待更多实例）
11. **防重领靠状态机**：FINISHED→POINT_TAKEN（同 notes/57 Mail isAttachmentGot 思想）
12. **takeReward 4 层校验**：去重→等级→excel free/paid 名单（不信任客户端 rewardId, notes/58）→选择型宝箱二级展开
13. **isPaid() 硬编码 true**：白嫖付费战令——私服全解锁哲学（接 notes/62 鱼类全解锁同主题）
14. **脏数据自愈**：getData()==null 的任务自动从 Map 移除（容错）
15. **每日/周重置 = 模型①Lazy**：onTick 比 currentDate vs lastResetDate，无 cron（三分法第 4 验证，首次验证"周期重置调度"场景）
16. **三分法判据普适性增强**：不仅资源产出，"周期重置"也属①Lazy（状态查询型）
17. **doDailyReset 同位 notes/64**：与锻造点重置（300_000）同一 onTick 块——印证 onTick 是 grasscutter 的"懒重置/通知汇聚点"
18. **每次 triggerMission 即 save**：高频写实证 §2 拆独立 collection 的必要性
19. **BattlePassMission 极简 @Entity**：id/progress/status，data @Transient 懒查 GameData（配置不持久化，只持久化进度）
20. **notes/22 配置 + 本篇 runtime 合璧**：战令系统配置↔运行时全链打通

---

## 9. 一句话总结

> **BattlePass runtime = 任务进度总线汇聚 + 独立持久化 —— BattlePassSystem(BaseGameSystem) 启动预索引 `Map<WatcherTriggerType,List<MissionData>>`，9+ 系统经 triggerMission 扇入打点 → addProgress 达标 FINISHED → takeMissionPoint 转点数(每1000点1级,周上限10000,满级50) → takeReward 经去重/等级/excel名单/选择宝箱 4 层校验发奖；状态存独立 collection `@Entity("battlepass")`（BasePlayerDataManager，非内嵌 Player，高频 save 性能驱动）；每日/周重置走 onTick 懒判定(currentDate vs lastResetDate, 无 cron).**
>
> **方法论意义: 串起 notes/41(事件总线消费侧 fan-in)、notes/30(持久化补全: 内嵌 vs 独立 collection 按访问独立性+写频, BasePlayerDataManager 为独立子数据标志)、[[grasscutter-resource-execution-models]](三分法第 4 验证, 首证"周期重置"亦属①Lazy, 判据普适性增强)、notes/64(锻造点重置同 onTick 位); 正反对照 notes/62 厘清"static 该持配置不该持玩家态"判据; 发现"自引用 clamp"`min(x,MAX-x)`等级溢出 bug 复制扩散 2 处(候选新 bug 类); 印证 isPaid()=true 的私服全解锁哲学.**

---

**前置笔记**：
- notes/22 BattlePass 配置 - 本篇 runtime 与之合璧
- notes/41 事件总线 - WatcherTriggerType 4 套事件之一，本篇是其 fan-in 消费侧
- notes/30 Database - 持久化补全：内嵌 Player vs 独立 collection（BasePlayerDataManager）
- notes/62 CookingCompound - static 正反对照（配置 vs 玩家态）+ 私服全解锁同主题
- notes/64 Forging - TRIGGER_DO_FORGE 打点来源 + 锻造点重置同 onTick 位
- notes/57 Mail - 防重领状态机同思想
- notes/58 Shop - 不信任客户端（excel 名单核对）同哲学
- [[grasscutter-resource-execution-models]] - 三分法第 4 验证（周期重置亦①Lazy）

**关联文件**：
- `BattlePassManager.java`(383) - @Entity("battlepass") 独立 collection + 点数/等级/领奖
- `BattlePassSystem.java`(79) - BaseGameSystem，cachedTriggers 预索引 + triggerMission 扇入
- `BattlePassMission.java`(72) - @Entity 极简 id/progress/status，data @Transient 懒查
- `Player.java:1293-1324` - doDailyReset 懒每日/周重置（同位锻造点 300_000）
- `GameConstants.java:25-28` - BP 常量（满级 50/每级 1000/周 10000/买级 150）
- Bug 位点：`BattlePassManager.java:112`（addPointsDirectly 自引用 clamp）+ `:305`（buyLevels 同款）
- 扇入样本：Gacha:391 / Inventory:242,248 / Player:1313,1447 / EntityMonster:271 / Forging:190 / Resin:58 / Dungeon:270

**研究的源代码**: BattlePassManager 383 + BattlePassSystem 79 + BattlePassMission 72 全文 + Player doDailyReset + 9 扇入点 + GameConstants。
