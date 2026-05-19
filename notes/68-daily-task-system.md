# DailyTaskManager 每日委托系统深度剖析

> 第 68 篇：针对性补强 [[grasscutter-resource-execution-models]] 三分法**最薄弱环节——模型②事件累计**（此前仅 notes/60 Energy 单例验证）。结论：模型②有**两种实现子形态**——②a 自包含累计器（Energy）、②b **委托累计器**（每日委托：薄 var 存储 + 全权委托 Quest cond/exec 引擎判完成）。同时 **三连印证 notes/66 持久化修正**（DailyTask=A 层内嵌，与 BattlePass=B、Blossom=C 同为 BasePlayerDataManager 却三层皆有），并接通 notes/43 Quest 引擎复用线。

---

## 0. 为什么挖这个：补分类法弱项

三分法 6 次验证里，模型②（事件累计）只有 notes/60 Energy 一个样本——**单点验证不足以确立一类**。每日委托（"今日委托"4 个随机任务）直觉上是"做事件 → 攒进度 → 完成领奖"，正是模型②候选。

> **事前预测**：每日委托是复合体——日刷新 → ①Lazy；进度累计 → ②事件累计；完成标记 → 第 0 类。重点验证②是否成立、以何种形态实现。

读码：预测命中，且发现②的**第二种实现形态**，使模型②从"单例"升为"有内部结构的成熟分类"。

---

## 1. 每日委托系统全图（复合三模型）

```
┌── ①Lazy 日刷新闸门 (notes/65 doDailyReset 同位) ───────────┐
│ Player.doDailyReset (onTick, currentDate vs lastResetDate)  │
│   → dailyTaskManager.randomizeTasks()                       │
│     清 finishedCurrentTasks → 删旧 quest(hack) →             │
│     按 unlockedCities 过滤 → shuffle → 取 4 个 →             │
│     queueEvent(QUEST_COND_DAILY_TASK_START)                  │
└────────────────────────┬───────────────────────────────────┘
                         ↓ 玩家做委托 (击杀/收集/对话…)
┌── ②b 委托累计器 (薄存储 + 委托 Quest 引擎) ────────────────┐
│ Quest exec 触发: ExecIncDailyTaskVar/SetDailyTaskVar/Dec    │
│   → DailyTaskManager.incTaskVar/setTaskVar (taskVars Map 累计)│
│   → triggerTaskVarAction:                                   │
│      player.save()                                          │
│      queueEvent(QUEST_COND_DAILY_TASK_VAR_EQ/GT/LT)  ★委托  │
│      PacketTaskVarNotify                                     │
│   → Quest 条件 ConditionDailyTaskVarEq/Gt/Lt 评估推进 quest  │
└────────────────────────┬───────────────────────────────────┘
                         ↓ quest 完成回调
┌── 第 0 类 完成标记 ────────────────────────────────────────┐
│ finishTask(taskId): finishedCurrentTasks.add +              │
│   legendaryKeyDailyTasks++ → PROP_PLAYER_LEGENDARY_DAILY_TASK_NUM│
│   PacketDailyTaskProgressNotify                             │
└─────────────────────────────────────────────────────────────┘
```

→ **三模型复合**（印证 notes/67"大系统按子系统分解"）：日刷新①Lazy / 进度②b累计 / 完成第0类。

---

## 2. 模型②的两种实现子形态（核心产出）

| 子形态 | 累计器 | 完成判定 | 代表 | 笔记 |
|---|---|---|---|---|
| **②a 自包含** | 系统自有累计逻辑（Energy pity 概率 + addEnergy 账本）| 系统自判（cur≥max）| Energy | notes/60 |
| **②b 委托** | 薄 var 存储（`taskVars: Map<taskId, List<Integer>>`）| **全权委托 Quest cond/exec 引擎** | 每日委托 | **本篇** |

→ **②a**：Energy 自己掷骰、自己记账、自己判满，是"重逻辑累计器"。
→ **②b**：DailyTask **没有独立进度引擎**——`taskVars` 只是个"键值计数袋"，累计后 `queueEvent(QUEST_COND_DAILY_TASK_VAR_EQ/GT/LT)` 把判定**整体甩给 Quest 条件系统**（notes/43）。委托方只管"加数 + 广播"，"加到多少算完成"由 Quest 配置的 cond 决定。
→ **方法论意义**：模型②不是单一实现，而是"累计"语义下的**自包含 vs 委托**二分。这把②从 notes/60 的单例升级为**有内部结构的成熟分类**——正是补强分类法弱项的目标达成。
→ 推广判据：见到"累计变量 + queueEvent/触发外部条件引擎"即②b；见到"系统自有阈值判定逻辑"即②a。

---

## 3. ①Lazy 日刷新闸门：randomizeTasks（notes/65 同位）

```java
// Player.doDailyReset (onTick, currentDate vs lastResetDate 懒判, 无 cron)
this.dailyTaskManager.randomizeTasks();
```
```java
public void randomizeTasks() {
    finishedCurrentTasks.clear();
    // hack: 删旧 currentTasks 关联的 quest —— 遍历所有主线×子任务×acceptCond
    this.player.getQuestManager().getMainQuests().values().forEach(mQuest ->
        mQuest.getChildQuests().values().forEach(sQuest ->
            sQuest.getQuestData().getAcceptCond().stream()
                .filter(cond -> cond.getType() == QuestCond.QUEST_COND_DAILY_TASK_START
                        && currentTasks.contains(cond.getParam()[0]))
                .forEach(cond -> mQuest.delete())));
    // 过滤(已解锁城市) → shuffle → 取 ≤4
    var taskList = new ArrayList<>(GameData.getDailyTaskDataMap().values().stream()
        .filter(t -> cityFilter == 0 || t.getCityId() == cityFilter)
        .filter(t -> unlockedCities.contains(t.getCityId())).toList());
    Collections.shuffle(taskList);
    this.currentTasks = taskList.subList(0, Math.min(4, taskList.size()))
        .stream().map(DailyTaskData::getId).toList();
    this.currentTasks.forEach(t -> player.getQuestManager()
        .queueEvent(QuestCond.QUEST_COND_DAILY_TASK_START, t));
}
```

→ **刷新调度本身是 ①Lazy**：不在 DailyTaskManager 里建 cron，靠 `Player.doDailyReset` 的 `currentDate vs lastResetDate` 懒闸门（notes/65/66 同款）触发。`randomizeTasks` 内部是纯事务（shuffle + subList）。
→ 三分法第 7 次跨系统印证①Lazy（日刷新调度）。

### 3.1 代码异味："hack" 删旧 quest（functionality-first 线）

注释自承 `//hack: remove old currentTasks from the quest list` + 类头 TODO `Removing old currentTasks is hacky and requires a relog to not look glitchy`。
→ **O(主线数 × 子任务数 × acceptCond)** 全量扫描 + `mQuest.delete()` 整条主线删除——粗暴且需重登才不显示错乱。
→ 接 notes/61/64/67 "功能优先"风格线：类头大段 TODO（"commission rewards 谁触发？随机应每池选一个？"）坦承系统半成品。**考古价值**：每日委托是 grasscutter 明确未完工系统，分析其行为要带"未完成"前提。

---

## 4. ②b 委托累计：taskVars + triggerTaskVarAction

```java
public void incTaskVar(int taskId, int index, int value) {
    val oldValue = getTaskVar(taskId, taskId);          // ★ 注意 bug: 传 taskId 当 index (见 §6)
    getVarList(taskId, index).set(index, oldValue + value);
    triggerTaskVarAction(taskId, index, oldValue + value);
}
private void triggerTaskVarAction(int taskId, int index, int value) {
    this.player.save();                                  // A 层: 经 player.save 持久化
    var qm = this.player.getQuestManager();
    qm.queueEvent(QuestCond.QUEST_COND_DAILY_TASK_VAR_EQ, taskId, index, value);  // ★ 委托
    qm.queueEvent(QuestCond.QUEST_COND_DAILY_TASK_VAR_GT, taskId, index, value);
    qm.queueEvent(QuestCond.QUEST_COND_DAILY_TASK_VAR_LT, taskId, index, value);
    this.player.sendPacket(new PacketTaskVarNotify(this.player));
}
```

驱动来源（grep 实证）：`ExecIncDailyTaskVar / ExecSetDailyTaskVar / ExecDecDailyTaskVar / ExecNotifyDailyTask`——全是 **Quest exec 动作**（notes/43）。即：场景 Lua/任务脚本（notes/45/14）→ Quest exec → `dailyTaskManager.incTaskVar` → 累计 → `queueEvent` → Quest 条件 `ConditionDailyTaskVarEq/Gt/Lt` 评估 → quest 推进 → 完成 → `finishTask`。

→ **闭环不出 Quest 引擎**：exec 写入累计、cond 读出判定，DailyTaskManager 只是中间"计数袋 + 广播器"。这是 ②b 的本质——**累计与判定解耦，判定外包**。
→ `getVarList` 惰性补 0 到目标 index（`while(list.size()<=index) list.add(0)`）——稀疏变量袋的常见技巧。

---

## 5. 持久化：A 层内嵌，三连印证 notes/66 修正

```java
@Entity
public class DailyTaskManager extends BasePlayerDataManager { ... }
// Player.java:177
@Getter private DailyTaskManager dailyTaskManager;     // ★ 非 transient
// 持久化: triggerTaskVarAction 里 this.player.save()  (无 DatabaseHelper.saveDailyTask)
```

→ **A 层（内嵌 Player 文档）**：`@Entity` 但无 `@Id`/无独立 collection/无 `DatabaseHelper.saveX`，Player 字段**非 transient**，随 `player.save()` 落盘。
→ **三连印证 notes/66 修正**：`BasePlayerDataManager` 三个子类落在三个不同持久化层——

| 类 | 基类 | 持久化层 | 笔记 |
|---|---|---|---|
| BattlePassManager | BasePlayerDataManager | **B 独立 collection** | notes/65 |
| BlossomManager | BasePlayerDataManager | **C transient 重建** | notes/66 |
| **DailyTaskManager** | BasePlayerDataManager | **A 内嵌 Player** | **本篇** |

→ **铁证**：持久化层级**绝不能由基类 `BasePlayerDataManager` 推断**，必须看"`@Id`/`@Entity(value=)` + Player 字段 transient 与否 + 有无 `DatabaseHelper.saveX`"三处组合。notes/65 的过度概括至此被三个不同层的同基类样本彻底证伪并定型。

---

## 6. 发现的 Bug：incTaskVar 索引参数错位

```java
public void incTaskVar(int taskId, int index, int value) {
    val oldValue = getTaskVar(taskId, taskId);   // ❌ 第二参应是 index, 却传了 taskId
    getVarList(taskId, index).set(index, oldValue + value);
    ...
}
public int getTaskVar(int taskId, int index) { return getVarList(taskId, index).get(index); }
```

→ `incTaskVar` 读旧值时调 `getTaskVar(taskId, taskId)`——**第二个参数把 `taskId` 当成了 `index`**（应传 `index`）。
→ 后果：读旧值用的 index = taskId（通常远大于真实 index），`getVarList` 惰性补 0 到 `taskId` 长度后 `.get(taskId)` 取到 0 → **incTaskVar 实际退化为"oldValue 恒取错位值（多为 0）"**，累计可能从错误基准开始（视 index 与 taskId 关系，多数情况丢失既有进度）。
→ 性质：**参数错位逻辑 bug**（非 payItems 类）。与 setTaskVar 对比：`setTaskVar` 用 `getVarList(taskId, index)` 正确，唯 `incTaskVar` 读旧值错位。属"功能优先未充分测试"的又一标本（每日委托系统类头已自承半成品）。
→ 不立即记忆为新 bug 类（单实例参数错位），但记入 [[grasscutter-resource-execution-models]] 关联的"②b 委托累计实现易错点"观察。

---

## 7. 与 Quest 引擎复用线（notes/43 + 同构架构记忆）

每日委托**不自建进度引擎**，全程复用 Quest 的 `cond + exec` 三件套：
- 条件：`QUEST_COND_DAILY_TASK_START / DAILY_TASK_VAR_EQ / GT / LT`（4 个 QuestCond）
- 执行：`ExecIncDailyTaskVar / SetDailyTaskVar / DecDailyTaskVar / NotifyDailyTask`（4 个 QuestExec）

→ 印证 [[grasscutter-同构架构模式]] 记忆的"**Quest cond/exec 三件套被 5+ 系统复用**"论断——每日委托是又一复用实例（继 Dungeon/Activity/Challenge/SceneTrigger 之后）。
→ 架构含义：grasscutter **不为"每日委托"建独立任务引擎**，而把它**降维成 Quest 配置 + 一个计数袋**。这是模型②b 的架构动机：**复用通用条件引擎 > 自建累计判定**。与 notes/41 "WatcherTriggerType fan-in" 互补——一个用 Quest cond 委托，一个用事件总线扇入，都是"不自建、复用通用机制"的 grasscutter 哲学。

---

## 8. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 客户端伪报委托进度 | ⚠ 进度经 Quest exec（多由 Lua/服务端逻辑触发），客户端难直接注入 var |
| 重复 finishTask 刷 legendaryKey | ✗ `if(!finishedCurrentTasks.contains)` 去重 |
| 篡改 taskVars 持久值 | ✗ A 层服务端账本（player.save）|
| incTaskVar 索引错位刷进度 | ⚠ §6 是 bug 但偏"丢进度"非"刷进度"，可利用性低 |
| 跨城市做未解锁委托 | ✗ randomizeTasks 按 unlockedCities 过滤 |

→ 反作弊尚可（去重 + 服务端账本 + 城市过滤），主要问题是 §6 参数错位 bug（自损型，非套利）+ 系统半成品。

---

## 9. 关键收获

1. **补强分类法弱项达成**：模型②从 notes/60 单例升为**②a 自包含 / ②b 委托** 二分的成熟分类
2. **②b 委托累计器**：薄 var 存储（taskVars Map）+ 全权委托 Quest cond/exec 引擎判完成（累计与判定解耦）
3. **②a vs ②b 判据**：见"累计变量 + queueEvent/触发外部条件引擎"=②b；见"系统自有阈值判定逻辑"=②a
4. **每日委托=三模型复合**（印证 notes/67）：日刷新①Lazy / 进度②b / 完成标记第0类
5. **日刷新调度①Lazy**：靠 Player.doDailyReset 懒闸门（notes/65/66 同位），randomizeTasks 内部纯事务（shuffle+subList≤4）
6. **★ 三连印证 notes/66**：BasePlayerDataManager 三子类落三层（BattlePass=B/Blossom=C/DailyTask=A）→ 持久化层级绝不可由基类推断，铁证定型
7. **DailyTask=A 层内嵌**：@Entity 无@Id、字段非 transient、经 player.save、无 DatabaseHelper.saveX
8. **复用 Quest cond/exec 三件套**：4 QuestCond + 4 QuestExec，印证 [[grasscutter-同构架构模式]]"三件套 5+ 系统复用"
9. **架构哲学**：grasscutter 不为每日委托建独立引擎，降维成 Quest 配置+计数袋（"复用通用机制>自建"，与 notes/41 fan-in 互补）
10. **★ Bug：incTaskVar 读旧值 `getTaskVar(taskId, taskId)` 参数错位**（应传 index），致累计基准错位/丢进度
11. **代码异味**：randomizeTasks "hack" 删旧 quest——O(主线×子任务×cond) 全扫 + mQuest.delete()，注释自承需重登
12. **functionality-first 线延续**：类头大段 TODO 自承半成品（commission 奖励无触发/随机池逻辑未做），接 notes/61/64/67
13. **getVarList 惰性补 0**：稀疏变量袋 `while(size<=index) add(0)` 技巧
14. **finishTask → PROP_PLAYER_LEGENDARY_DAILY_TASK_NUM**：传说委托钥匙计数（玩家属性账本）
15. **cityFilter + unlockedCities 双过滤**：委托按已解锁城市随机，checkForCityUnlock 由 quest 解锁城市
16. **taskLevel = 1+(playerLevel-1)/5**：委托等级随玩家等级，决定奖励档（getScoreRewardId）
17. **第 7 次①Lazy 跨系统印证**（日刷新调度），分类法稳定性持续累积
18. **②b 易错点观察**：累计与判定解耦虽利于复用，但参数传递（taskId/index）跨边界易错（§6 实证）
19. **onPlayerLogin 推 DailyTaskDataNotify+TaskVarNotify**：登录态全量下发（复合系统典型）
20. **每日委托是 grasscutter 明确半成品**：分析其行为须带"未完工"前提（类头 TODO 实证）

---

## 10. 一句话总结

> **DailyTaskManager 每日委托 = 三模型复合体 —— 日刷新靠 Player.doDailyReset 懒闸门①Lazy(randomizeTasks 内部 shuffle+取4 纯事务)；进度是模型②b"委托累计器"(taskVars 薄计数袋 + triggerTaskVarAction 经 queueEvent(QUEST_COND_DAILY_TASK_VAR_EQ/GT/LT) 全权委托 Quest cond/exec 引擎判完成，累计与判定解耦)；finishTask 第0类完成标记(去重+PROP_LEGENDARY_DAILY_TASK_NUM)；A 层内嵌 player.save 持久化.**
>
> **方法论意义: 针对性补强 [[grasscutter-resource-execution-models]] 最弱环——模型②由 notes/60 单例升为"②a 自包含(Energy 自掷骰自判) vs ②b 委托(DailyTask 薄存储+Quest 引擎判)"成熟二分；三连印证 notes/66 持久化修正(BasePlayerDataManager 三子类 BattlePass=B/Blossom=C/DailyTask=A 三层皆有，铁证持久化层级不可由基类推断)；印证 [[grasscutter-同构架构模式]] Quest cond/exec 三件套 5+ 系统复用(每日委托降维成 Quest 配置+计数袋, 体现"复用通用机制>自建"哲学)；抓到 incTaskVar 参数错位 bug + 延续 functionality-first 半成品线.**

---

**前置笔记**：
- notes/60 EnergyManager - 模型②a 自包含累计器（本篇②b 委托与之对照确立二分）
- notes/65 BattlePass / notes/66 Blossom - 持久化 B/C 层（本篇 A 层三连印证修正）
- notes/67 HomeWorld - "大系统按子系统分解"（本篇复合三模型再印证）
- notes/43 Quest 运行时 - cond/exec 引擎（每日委托全权委托其判定）
- notes/41 事件总线 - fan-in（与本篇"Quest cond 委托"同属"复用通用机制"哲学）
- notes/45/14 Lua/场景脚本 - Quest exec 的触发源头
- notes/61/64/67 - functionality-first 半成品/hack 风格线
- [[grasscutter-resource-execution-models]] - 模型②补强（②a/②b 二分）
- [[grasscutter-同构架构模式]] - Quest cond/exec 三件套复用再证

**关联文件**：
- `DailyTaskManager.java`(291) - BasePlayerDataManager(A 层)，randomizeTasks/incTaskVar/finishTask/triggerTaskVarAction
- `Player.java:177` 非 transient 字段 / `:1324` doDailyReset→randomizeTasks / `:520` updateTaskLevel
- `ConditionDailyTaskStart/VarEq/VarGt/VarLt` - Quest 条件侧（②b 判定委托终点）
- `ExecIncDailyTaskVar/SetDailyTaskVar/DecDailyTaskVar/NotifyDailyTask` - Quest 执行侧（②b 累计驱动源）
- `DailyTaskData/DailyTaskLevelData/DailyTaskRewardData`(excel) + `CityTaskOpenData`
- Bug 位点：`DailyTaskManager.java:228`（incTaskVar `getTaskVar(taskId, taskId)` 参数错位）

**研究的源代码**: DailyTaskManager 291 行全文 + Player 持久化/调用点 + Quest cond/exec 驱动实证 + 持久化三层对照。
