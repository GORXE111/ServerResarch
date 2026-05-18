# BlossomManager 野外盈花营地系统深度剖析

> 第 66 篇：notes/65 `doDailyReset` 里两次出现 `getBlossomManager().dailyReset()` 未展开。本篇追完野外盈花营地（消耗树脂的野外限时战斗领奖点）全链，达成 **三分法第 5 次预测验证（首入探索域，且首次出现"每营地独立刷新时刻"）**，并**自我修正 notes/65** 的过度概括——发现 grasscutter per-player 数据其实有**三层持久化**，BlossomManager 是第三层（`transient` 不持久化、每登录从配置重建）的标本，附带由此产生的"重登重随机+丢进度"行为后果。串联 notes/50 树脂、notes/45/14 脚本、notes/53 多人四条线。

---

## 0. 为什么这一篇重要：验证 + 自我修正

两件事：
1. **预测验证**：盈花营地"每日（或自定义时刻）刷新一批"，预测属 [[grasscutter-resource-execution-models]] **①Lazy**。
2. **自我修正**：notes/65 断言"`BasePlayerDataManager` 是独立 collection 持久化的标志"。BlossomManager 同样 `extends BasePlayerDataManager` 却**根本不持久化**——这暴露 notes/65 的过度概括，本篇修正为**三层持久化模型**。

研究方法论上，第 2 点比第 1 点更有价值：分类法/论断要能被后续证据**证伪并精炼**。

---

## 1. 盈花系统全图

```
┌── 登录/onTick ─────────────────────────────────────────────┐
│ onPlayerLogin: blossomSchedule 空→buildBlossomSchedule()    │
│   从配置随机抽营地铺满地图 → notifyPlayerIcon (地图图标)    │
│ dailyReset (onTick, notes/65 同位): 逐营地 shouldReset 懒判  │
└────────────────────────┬───────────────────────────────────┘
                         ↓ 玩家野外触发挑战 (经 Lua ScriptLib)
┌── 脚本驱动状态机 (notes/45/14) ────────────────────────────┐
│ setBlossomState(groupId,state)  ← ScriptLib 调             │
│ addBlossomProgress(groupId)     ← 怪物死亡 ScriptLib 调     │
│   state: 0 loaded /1 spawned /2 started /3 finished         │
│   progress>=finishProgress → callEvent(BLOSSOM_PROGRESS_FINISH)│
└────────────────────────┬───────────────────────────────────┘
                         ↓ 挑战完成生成宝箱
┌── 领奖 (notes/50 树脂) ────────────────────────────────────┐
│ onReward: remainingUid 资格校验 → useResin / useCondensed  │
│   浓缩树脂(1)→奖励×2, 普通树脂(schedule.resin)→×1          │
│   addItems(ActionReason.OpenBlossomChest)                   │
│ buildNextCamp: 完成后链式生成"下一处"营地 (地脉移动)        │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 自我修正 notes/65：三层持久化模型

`Player.java:206`：
```java
@Getter private transient BlossomManager blossomManager;   // ★ transient!
// 构造器 / initManagers 里:
this.blossomManager = new BlossomManager(this);            // 每次 new, 不读库
```
+ `DatabaseHelper` 无 `saveBlossom`，`BlossomManager` 无 `@Id`/`save()` 覆写。

→ **Morphia 跳过 `transient` 字段** → BlossomManager **完全不持久化**，每登录从配置重建。其类上的 `@Entity` 注解形同**死注解（aspirational，被 transient 压制）**。

### 2.1 修正后的三层持久化模型（notes/30 补全）

| 层 | 机制 | 代表 | 笔记 |
|---|---|---|---|
| **A 内嵌 Player 文档** | `@Entity` 对象进 Player 的 Map/List 字段，随 `player.save()` | 合成台/锻造/派遣 | notes/62/64/59 |
| **B 独立 collection** | 自有 `@Id ObjectId` + `@Indexed ownerUid` + `DatabaseHelper.saveX` | BattlePass | notes/65 |
| **C 不持久化·配置重建** | Player 字段 `transient`，每登录 `new` 从 GameData 重算 | **Blossom** | **本篇** |

→ **notes/65 错在哪**：把"BattlePass 恰好是 BasePlayerDataManager + 独立 collection"误推为"BasePlayerDataManager ⇒ 独立 collection"。反例 BlossomManager 同基类却是 C 层。
→ **正确判据**：`BasePlayerDataManager` 仅表示"玩家子数据持有者，无自动持久化契约"。持久化层级由**三处独立决定**：① 类的 Morphia 注解（@Id/@Entity value）；② Player 引用该字段是否 `transient`；③ 是否存在对应 `DatabaseHelper.saveX`。三者组合才定层级，**不可由基类推断**。
→ 方法论教训：**单一样本归纳出的"标志/规律"必须用反例压力测试**——这正是 notes/62→63→64 预测验证、本篇证伪精炼的研究纪律。

### 2.2 C 层的行为后果（transient + 随机抽取）

`buildBlossomSchedule` 用 `Utils.drawRandomListElement` **随机抽**哪些营地铺在哪。又因 C 层不持久化、每登录重建：
→ **盈花营地布局每次登录重新随机**（官服是当日固定布局）——可观察的行为偏差。
→ **挑战进度 `schedule.progress` 重登即丢**（transient）——打到一半下线，上线营地重置。
→ 这不是"bug"而是 C 层持久化策略的**必然副作用**；grasscutter 取舍：盈花是可重复刷的野外内容，丢进度代价低，省去持久化复杂度。记录此因果链本身即考古价值。

---

## 3. 三分法第 5 次预测验证：懒刷新（探索域 + 每营地独立时刻）

`BlossomSchedule.shouldReset()`：
```java
boolean shouldReset(){
    return shouldReset(ZonedDateTime.now(ZONE_ID), getLastCycleZonedTime(), getRefreshHour());
}
private static boolean shouldReset(ZonedDateTime now, ZonedDateTime lastRefresh, LocalTime refreshHour){
    val todayRefreshHour = now.with(refreshHour);   // 今天的刷新时刻
    return lastRefresh == null || !lastRefresh.isEqual(todayRefreshHour) || lastRefresh.isBefore(todayRefreshHour);
}
```
被 `BlossomManager.dailyReset()` 在 `Player.onTick`（notes/65 同位）调用，逐营地过滤 `shouldReset`。

→ **零 cron/scheduler**：onTick 时比 `now` vs `lastCycledTime` vs 配置 `refreshHour`。完美 ①Lazy。
→ **第 5 次预测命中**（notes/62①/63 第0类/64①/65 周期重置①/本篇①），且：
  - **首入探索域**：前 4 次都是经济/制造/战令，本篇是野外世界事件——判据跨域普适。
  - **首现"每营地独立刷新时刻"**：`getRefreshHour` 按 `refreshId` 从 `BlossomRefreshData` 取，注释明确"some blossoms have different reset time"。比 notes/65 战令"全局每日 04:00"更细——**①Lazy 天然支持"每实例独立周期"**（每个 schedule 自带 lastCycledTime + refreshHour），这是 Lazy 相对 cron 的结构性优势：cron 要为每种周期建独立定时器，Lazy 只需各自存锚点、统一 onTick 比对。
→ 强化记忆判据：①Lazy 不仅适配"全局每日重置"，更优雅适配"每实例异构周期"。

---

## 4. 脚本驱动状态机（notes/45 Lua / notes/14 场景脚本线）

盈花挑战进度**不由网络包驱动，由 Lua ScriptLib 驱动**：

```java
public boolean setBlossomState(int groupId, int state) {        // ScriptLib 调
    schedule.setState(state);
    player.getScene().broadcastPacket(new PacketWorldOwnerBlossomScheduleInfoNotify(...));
}
public boolean addBlossomProgress(int groupId) {                 // 怪物死亡 ScriptLib 调
    schedule.addProgress();
    broadcastPacket(...);
    if (schedule.isFinished())
        scene.getScriptManager().callEvent(new ScriptArgs(groupId, EventType.EVENT_BLOSSOM_PROGRESS_FINISH));
}
```

→ 链路：场景 Lua（notes/14/45）检测怪物死亡 → `ScriptLib.addBlossomProgress` → 进度满 → **回调 Lua** `EVENT_BLOSSOM_PROGRESS_FINISH`（Lua 再生成宝箱）。
→ **Java↔Lua 双向**：Lua 推进度进 Java 状态机，Java 完成又 callEvent 回 Lua。印证 notes/45 "ScriptLib 是 Java/Lua 边界 façade"。
→ 状态机 `0 loaded /1 spawned /2 started /3 finished`，每次变更 `broadcastPacket`（多人可见）。

---

## 5. 树脂领奖（notes/50 lazy 树脂线）

```java
public boolean onReward(Player player, EntityBaseGadget gadget, boolean useCondensedResin) {
    if (scheduleOption.filter(s -> s.getRemainingUid().contains(player.getUid())).isEmpty()) return false;  // 资格校验
    val payable = useCondensedResin ? resinManager.useCondensedResin(1) : resinManager.useResin(schedule.getResin());
    if (!payable) return false;                                  // ★ 树脂不足正确 return (对比 notes/63/64 bug)
    player.getInventory().addItems(blossomRewards.getPreviewItems().stream()
        .map(r -> new GameItem(r.getItemId(), r.getCount() * (useCondensedResin ? 2 : 1)))   // 浓缩×2
        .toList(), ActionReason.OpenBlossomChest);
    schedule.getRemainingUid().remove(player.getUid());          // 防重领 (从资格集移除)
}
```

→ 接 notes/50 lazy 树脂：`useResin/useCondensedResin` 是消费侧触发点（树脂在此 lazy 结算）。
→ **浓缩树脂 1 个 = 普通树脂量 + 奖励×2**（还原官服地脉浓缩机制）。
→ **`payable` 失败正确 `return false`**——与 notes/63 烹饪、notes/64 锻造的"payItems 失败缺 return" [[grasscutter-payitems-missing-return]] 形成**又一正例**（盈花作者写对了资源失败短路）。可补充该记忆的对照样本计数（正例 2：合成台/盈花；反例 2：烹饪/锻造）。
→ 防重领靠从 `remainingUid` Set 移除（同 notes/57/65 状态化防重领思想，此处用"资格集合"实现）。

---

## 6. 多人协作语义（notes/53 多人线）

→ `remainingUid: Set<Integer>`：一个营地多名玩家可同时有领奖资格（co-op 一起打）。`getChestInfo` 把 `playersUid` 加入资格集。
→ **世界主机权威**：`PacketWorldOwnerBlossomScheduleInfoNotify`、注释 "Notify player's(not necessary world owner)" ——盈花调度挂在**世界主机**的 BlossomManager，访客看主机的营地。
→ `getWorldLevel()/getPlayerLevel()` 注释 `// maybe should get the owner's?` ——作者自承多人下"取访客还是主机等级"未拍板（潜在多人不一致，TODO 风格再现）。
→ 接 notes/53：盈花是"世界级共享内容"，与 notes/53 划分的"单人邀约 vs 真多人"中**真多人世界共享**一类一致。

---

## 7. 代码风格观察：超密集函数式流水线

`buildBlossomSchedule(refreshData)` 是一个 **~35 行单 stream 表达式**：嵌套 `Stream.ofNullable / filter ×7 / map / peek 副作用 / Utils.drawRandomListElement` + 两个外部 `appendedGroupId/appendedSectionId` 去重计数器。

→ 与本弧前述系统（合成台/锻造的命令式 `if-return`）风格**迥异**——盈花作者偏好重函数式 + `peek` 做副作用（抽中即记入去重表）。
→ `peek` 用于副作用是**反 Stream 契约的写法**（peek 本为调试），这里当"遍历即登记"用——可读性差但功能正确，典型 grasscutter "多人多风格拼接"代码生态（接 notes/53 命名陷阱、notes/61 [MovementManager] 遗留同类现象：**同仓库不同作者风格割裂**）。
→ 考古价值：识别"作者风格指纹"有助判断系统由谁/何时写，以及是否复用了别处骨架。

---

## 8. 链式地脉：buildNextCamp + 动态组加载（notes/14 GroupSuite 线）

```java
public void buildNextCamp(int groupId) {
    val schedule = this.blossomSchedule.remove(groupId);                  // 移除已完成
    ...getNextCampId... // 取配置链下一处, 若与现有重叠再取更下一处
    .forEach(newSchedule -> {
        this.blossomSchedule.put(newSchedule.getGroupId(), newSchedule);
        this.player.getScene().runWhenFinished(() -> {
            this.player.getScene().loadDynamicGroup(newSchedule.getGroupId());      // 动态组加载
            if (decorateGroupId != 0) scene.loadDynamicGroup(decorateGroupId);      // 装饰组
        });
    });
}
```

→ 盈花完成后**链式生成下一处**（地脉/盈花在地图上"移动"），带重叠回避。
→ `loadDynamicGroup` = notes/14 GroupSuite/动态组机制：营地实体不预生成，完成时**动态加载下一组**。`runWhenFinished` 保证场景就绪后再加载（时序安全）。
→ 印证 notes/14：grasscutter 实体按需 GroupSuite 加载，盈花是"运行时动态组"的鲜活用例。

---

## 9. 关键收获

1. **三分法第 5 次预测命中**：BlossomSchedule.shouldReset = now vs lastCycledTime vs refreshHour，零 cron，①Lazy
2. **首入探索域**：前 4 次经济/制造/战令，本篇野外世界事件——判据跨域普适
3. **首现"每营地独立刷新时刻"**：①Lazy 天然支持"每实例异构周期"（各存锚点统一 onTick 比对），结构性优于 cron——强化记忆判据
4. **★ 自我修正 notes/65**：`BasePlayerDataManager` 不是独立 collection 标志（反例 Blossom 同基类却不持久化）
5. **三层持久化模型**（修正/补全 notes/30/65）：A 内嵌 Player 文档 / B 独立 collection / C 不持久化配置重建
6. **持久化层级判据**：由"类 Morphia 注解 + Player 字段是否 transient + 有无 DatabaseHelper.saveX"三处组合定，不可由基类推断
7. **方法论**：单样本归纳的规律必须反例压力测试（本篇证伪精炼示范）
8. **C 层副作用**：transient + 随机抽取 → 营地布局每登录重随机 + 进度重登即丢（非 bug，是策略必然，记录因果链即考古价值）
9. **@Entity 死注解**：BlossomManager/BlossomSchedule 标 @Entity 但被 Player 字段 `transient` 压制，形同 aspirational 死注解
10. **脚本驱动状态机**（notes/45/14）：进度由 Lua ScriptLib 推（setBlossomState/addBlossomProgress），完成回调 Lua EVENT_BLOSSOM_PROGRESS_FINISH——Java↔Lua 双向
11. **状态机 0/1/2/3**（loaded/spawned/started/finished），每变更 broadcastPacket
12. **树脂领奖**（notes/50）：useResin/useCondensedResin 消费侧触发；浓缩树脂奖励×2
13. **payable 失败正确 return**：盈花是 [[grasscutter-payitems-missing-return]] 的**正例**（正 2 反 2）
14. **防重领靠资格集**：从 remainingUid Set 移除（状态化防重领）
15. **多人世界主机权威**（notes/53）：remainingUid 多人共享资格，调度挂世界主机，访客看主机营地
16. **多人等级歧义 TODO**：getWorldLevel/getPlayerLevel 注释自承"该取主机还是访客未定"
17. **代码风格指纹**：buildBlossomSchedule ~35 行单 stream + peek 副作用，与本弧命令式风格割裂——同仓库作者风格分裂（接 notes/53/61）
18. **链式地脉 buildNextCamp**：完成生成下一处营地，带重叠回避——地脉"移动"
19. **动态组加载**（notes/14）：loadDynamicGroup + runWhenFinished，营地按需 GroupSuite 加载，时序安全
20. **refreshCond 资格门控**：玩家等级 ≥/< / openState 决定营地是否对该玩家刷出（per-player 个性化世界内容）

---

## 10. 一句话总结

> **BlossomManager = 野外盈花营地（消耗树脂的限时战斗领奖点）—— onPlayerLogin 从配置随机抽营地铺地图，dailyReset 经 BlossomSchedule.shouldReset(now vs lastCycledTime vs 每营地 refreshHour) 懒判重置（零 cron，①Lazy）；挑战进度由场景 Lua ScriptLib 推进状态机(0/1/2/3) 完成回调 EVENT_BLOSSOM_PROGRESS_FINISH；onReward 经 remainingUid 资格校验 + useResin/useCondensedResin(浓缩奖励×2) 发 ActionReason.OpenBlossomChest；buildNextCamp 链式生成下一处营地经 loadDynamicGroup 动态加载.**
>
> **方法论意义: 三分法第 5 次预测验证（首入探索域 + 首现"每实例异构刷新周期"，强化"①Lazy 优雅支持异构周期"判据）；并自我修正 notes/65——`BasePlayerDataManager` 非独立 collection 标志，per-player 数据实为三层持久化（内嵌/独立 collection/transient 配置重建），层级由"Morphia 注解+字段 transient+DatabaseHelper.saveX"三处组合决定不可由基类推断；BlossomManager 是 C 层标本，其"重登重随机+丢进度"是 transient+随机抽取的策略必然；并补 [[grasscutter-payitems-missing-return]] 正例、印证 notes/45 Java↔Lua 双向、notes/14 动态组、notes/53 多人世界主机权威，揭示同仓库作者风格割裂的考古指纹.**

---

**前置笔记**：
- notes/65 BattlePass runtime - 本篇修正其"BasePlayerDataManager=独立collection"过度概括
- notes/30 Database - 三层持久化模型补全（内嵌/独立 collection/transient 重建）
- notes/50 ResinManager - useResin/useCondensedResin 消费侧（浓缩×2）
- notes/45 Lua 引擎 / notes/14 场景脚本 - ScriptLib 推进度 + callEvent 回调 + loadDynamicGroup 动态组
- notes/53 多人 - 世界主机权威 + remainingUid 多人共享资格 + 作者风格割裂同主题
- notes/61 Stamina - [MovementManager] 遗留 / 作者风格指纹同类现象
- [[grasscutter-resource-execution-models]] - 三分法第 5 验证（探索域+异构周期）
- [[grasscutter-payitems-missing-return]] - 盈花 payable 失败正确 return（正例）

**关联文件**：
- `BlossomManager.java`(296) - BasePlayerDataManager（transient 不持久化），schedule/spawnedChest ConcurrentHashMap
- `BlossomSchedule.java`(205) - @Entity（死注解）+ shouldReset 懒判 + 状态机 + create 工厂
- `Player.java:206` `transient BlossomManager`（C 层关键证据）/ `:1290,1314` dailyReset 同位 / `:1420` onPlayerLogin
- `BlossomRefreshData/BlossomGroupsData/BlossomSectionOrderData/BlossomOpenData/BlossomChestData`(excel)
- `BlossomScriptHandler.java` - ScriptLib↔Blossom 桥接（Lua 调 setBlossomState/addBlossomProgress）
- `ActionReason.OpenBlossomChest` - 经济审计入账

**研究的源代码**: BlossomManager 296 + BlossomSchedule 205 全文 + Player transient 字段/调用点 + BasePlayerDataManager + DatabaseHelper 缺 saveBlossom 佐证。
