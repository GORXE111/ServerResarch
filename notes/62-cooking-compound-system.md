# CookingCompoundManager 合成台系统深度剖析

> 第 62 篇：notes/61 末尾建立"资源执行模型三分法"后的**第一个预测性验证**——研究前**先预测**合成台（磨粉/煮药/浓缩树脂等时间锁制造）属模型①Lazy，再读代码证实。结果：**完全命中**，且发现一个优雅的"滑动 startTime 串行队列"懒算技巧 + 一个**全局静态字段串号 bug**。

---

## 0. 为什么这一篇重要：从"归纳"到"预测"

notes/50-61 把执行模型从经验归纳成 [[grasscutter-resource-execution-models]] 三分法。一个分类法的价值在于**可预测**。本篇是检验：

> **事前预测**：合成台是"投料 → 等 N 秒/个 → 取成品"的时间锁制造，属**状态查询型**资源（产出量可由 `now` 反推），故应是**模型①Lazy**——无 Timer，handler/login 时按 `currentTime` 一次性算。

读完代码：**预测 100% 命中**。这把三分法从"事后总结"升格为"研究方法论"。

---

## 1. 合成台系统全图

```
┌── 3 Handler ───────────────────────────────────────────────┐
│ GetCompoundDataReq      → 打开界面, 拉队列状态             │
│ PlayerCompoundMaterialReq → 投料 (扣原料, 入队)            │
│ TakeCompoundOutputReq   → 取成品 (按 groupId)             │
└────────────────────────┬───────────────────────────────────┘
                         ↓ CookingCompoundManager (143 行 BasePlayerManager)
┌── 配置 (static, initialize 一次) ──────────────────────────┐
│ defaultUnlockedCompounds: Set<配方id>                       │
│ compoundGroups: Map<groupId, Set<配方id>>                   │
│ unlocked: Set<配方id>  ★ static 非玩家绑定 (TODO bug)       │
└────────────────────────┬───────────────────────────────────┘
                         ↓ per Player 持久化
┌── Player.activeCookCompounds: Map<compoundId, ActiveCookCompoundData> @Entity ┐
│ ActiveCookCompoundData{ compoundId, costTime, totalCount, startTime } │
│ ★ 全部产出靠 getOutputCount(currentTime) 懒算, 零 Timer      │
└─────────────────────────────────────────────────────────────┘
```

→ **143 + 57 行**——又一个极简 BasePlayerManager（对比 notes/61 Stamina 715 行的轮询巨兽，反差强烈）。

---

## 2. 核心懒算：ActiveCookCompoundData（57 行，模型①教科书）

```java
@Entity @AllArgsConstructor
public class ActiveCookCompoundData {
    private int compoundId;
    private int costTime;     // 每个成品耗时(秒)
    private int totalCount;   // 队列总数(含已成+待成)
    private int startTime;    // 起算时间戳

    public int getOutputCount(int currentTime) {        // ★ 已完成数 = 纯函数
        int cnt = (currentTime - startTime) / costTime;
        return Math.min(cnt, totalCount);
    }
    public int getWaitCount(int currentTime) {
        return totalCount - getOutputCount(currentTime);
    }
    public int getOutputTime(int currentTime) {         // 下一个成品时间戳, 全好返 0
        int cnt = getOutputCount(currentTime);
        return (cnt == totalCount) ? 0 : startTime + (cnt + 1) * costTime;
    }
}
```

→ **没有任何 Timer/onTick**——`getOutputCount` 是 `f(currentTime)` 纯函数：`(now - startTime) / costTime`。
→ 与 [[grasscutter-resource-execution-models]] 模型①完全一致，公式形态同 notes/59 Expedition（`now - startTime >= ...`）的同族。
→ **预测命中**：时间锁制造 = 状态查询型 = lazy。三分法**作为预测工具有效**。

### 2.1 优雅技巧：滑动 startTime 维护串行队列

合成台是**串行流水线**（一次做一个，做完下一个）。难点：投料/取件后如何不丢"半成品进度"？答案是**移动 startTime**：

```java
public void addCompound(int count, int currentTime) {
    // 若队列已全部做完才追加 → 重置 startTime（从现在开始算新的）
    if (getOutputCount(currentTime) == totalCount)
        startTime = currentTime - totalCount * costTime;   // 让旧的仍算"已完成"
    totalCount += count;
}

public int takeCompound(int currentTime) {
    int count = getOutputCount(currentTime);   // 取走所有已完成
    startTime += costTime * count;             // ★ startTime 前移, 保留未完成进度
    totalCount -= count;
    return count;
}
```

→ `takeCompound` 取走 N 个成品后，把 `startTime += costTime * N`——**等价于"假装这 N 个从未占用时间"**，剩余未完成项的进度（`now - newStartTime`）精确保留。
→ `addCompound` 在队列已空时把 `startTime` 回拨 `totalCount*costTime`，使刚追加的料"立即开始计时"而非等旧的（旧的已 0）。
→ **这是 lazy 模型维护串行队列的标准技巧**：不存"每个 item 的状态"，只存一个 `startTime` 锚点，靠算术滑动。比 Timer 每秒推进队列省 N 倍开销。

---

## 3. 投料：handlePlayerCompoundMaterialReq（4 道校验）

```java
CompoundData compound = GameData.getCompoundDataMap().get(id);
// ① 配方是否解锁
if (!unlocked.contains(id)) { sendPacket(RET_FAIL); return; }
// ② 队列是否满
if (active.containsKey(id) && active.get(id).getTotalCount() + count > compound.getQueueSize()) {
    sendPacket(RET_COMPOUND_QUEUE_FULL); return;
}
// ③ 扣原料 (原子, notes/38)
if (!player.getInventory().payItems(compound.getInputVec(), count)) {
    sendPacket(RET_ITEM_COUNT_NOT_ENOUGH); return;
}
// ④ 入队 (新建 or addCompound)
if (active.containsKey(id)) active.get(id).addCompound(count, currentTime);
else active.put(id, new ActiveCookCompoundData(id, compound.getCostTime(), count, currentTime));
```

→ **校验顺序讲究**：解锁→队列容量→`payItems` 原子扣料（notes/38 Inventory，失败即回滚）→入队。
→ 服务端重查 `GameData.getCompoundDataMap()`（配方耗时/原料/产物），**不信任客户端**——与 notes/58 Shop 同哲学。
→ 但**漏校验 count 上界/负数**（客户端传 count）——理论上传超大 count 仍走 payItems（料不够会失败兜底，但 count 为负？payItems 行为未防）。属 grasscutter 私服一贯弱反作弊。

---

## 4. 取件：handleTakeCompoundOutputReq（groupId 而非 compoundId）

```java
// 客户端不传 compound_id, 只传 group_id（一组配方共用一个产出口）
int groupId = req.getCompoundGroupId();
for (int id : compoundGroups.get(groupId)) {           // 遍历组内所有配方
    if (!active.containsKey(id)) continue;
    int quantity = active.get(id).takeCompound(now);   // 懒算取走
    if (active.get(id).getTotalCount() == 0) active.remove(id);  // 空队清理
    // 累加产物 (同 itemId 合并)
    for (ItemParamData i : GameData.getCompoundDataMap().get(id).getOutputVec()) { ... }
}
if (success) player.getInventory().addItems(allRewards.values(), ActionReason.Compound);
else sendPacket(RET_COMPOUND_NOT_FINISH);
```

→ **关键设计**：客户端只发 `groupId`，服务端展开到组内所有配方逐个 `takeCompound`——一次取件清空整组已完成。
→ `ActionReason.Compound` 入账（notes/38 经济审计 190+ ActionReason 之一）。
→ 一个未完成也没有 → `RET_COMPOUND_NOT_FINISH`。

---

## 5. 持久化 + 登录推送（仍是 lazy）

```java
// Player.java:152
@Getter private Map<Integer, ActiveCookCompoundData> activeCookCompounds;  // @Entity 嵌入 Player 文档

public void onPlayerLogin() {
    player.sendPacket(new PacketCompoundDataNotify(unlocked, getCompoundQueueData()));
}
private List<CompoundQueueData> getCompoundQueueData() {
    int currentTime = Utils.getCurrentSeconds();
    for (var item : player.getActiveCookCompounds().values()) {
        data.setOutputCount(item.getOutputCount(currentTime));   // ★ 登录时才算
        data.setOutputTime(item.getOutputTime(currentTime));
        ...
    }
}
```

→ `ActiveCookCompoundData` 是 `@Entity`，随 `Player.activeCookCompounds` Map 嵌入 Player 文档（notes/30 Morphia embedded，同 notes/59 Expedition 模式）。
→ **登录推送 = 懒算快照**：离线期间合成台"继续生产"（startTime 已存），登录时 `getOutputCount(now)` 一次算出离线产出——**离线收益，零 Timer**，与 notes/59 Expedition 离线收益机制同源。

---

## 6. 发现的 Bug：static unlocked 全局串号

```java
private static Set<Integer> unlocked;   // ★ static! 非玩家绑定
// 注释自承: //TODO:bind it to player
public static void initialize() {
    unlocked = new HashSet<>(defaultUnlockedCompounds);
    if (compoundGroups.containsKey(3)) unlocked.addAll(compoundGroups.get(3));  // 鱼类配方全解锁(钓鱼未实现)
}
```

→ **`unlocked` 是 static**——**所有玩家共享同一份解锁集合**。代码注释 3 处 `//TODO:bind it to player` 自承未完成。
→ 后果：A 玩家解锁某配方，B 玩家也"被解锁"（实际此处只读 default，无运行时 unlock 入口，影响被掩盖；但若实现解锁逻辑会立即串号）。
→ 这是 grasscutter "**功能优先、正确性 TODO**"开发风格的又一标本（对比 notes/61 Stamina 满屏 TODO）。研究私服代码要**警惕 static 持有玩家态**。

---

## 7. 与三分法其他系统的对照（验证表）

| 维度 | Compound 合成台（本篇）| Expedition（notes/59）| Stamina（notes/61）|
|---|---|---|---|
| 三分法归类 | ①Lazy | ①Lazy | ③主动轮询 |
| 资源时间性质 | 状态查询（产出可反推）| 状态查询 | 连续积分 |
| 核心公式 | `(now-startTime)/costTime` | `now-startTime>=hourTime*3600` | 每 200ms Timer 拍 |
| Timer? | 无 | 无 | 有(java.util.Timer) |
| 持久化 | @Entity 嵌 Player Map | @Entity 嵌 Player Map | Player property |
| 离线收益 | 有(登录懒算) | 有(登录懒算) | 无(暂停停 Timer) |
| 触发懒算点 | 3 Handler + onLogin | onLogin/周期 | —(轮询不需要) |

→ **预测验证成功**：仅凭"资源时间性质=状态查询"就正确预言了 Compound 落①Lazy，且与 Expedition 公式同族。**三分法可作研究前的方向预判工具**。

---

## 8. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 改客户端传超大 count 投料 | ✗ payItems 原料不足兜底失败 |
| 传负数 count | ⚠ 未显式防（payItems 行为依赖，潜在隐患）|
| 篡改 startTime/产出数 | ✗ 服务端 @Entity 账本 + currentTime 服务端取 |
| 未完成强行取件 | ✗ takeCompound 取 `getOutputCount(now)`，未到=0 |
| 改客户端伪 groupId | ⚠ `compoundGroups.get(groupId)` 可能 NPE（未判 null）|

→ 数值账本服务端权威（startTime/now 服务端），但**输入校验薄**（count 上界、groupId 合法性未防）——典型 grasscutter 取舍。

---

## 9. 关键收获

1. **本篇是三分法的预测性验证**：研究前预测①Lazy，代码 100% 命中——三分法升格为研究方法论
2. **CookingCompoundManager 143 行 BasePlayerManager**，无 Timer/onTick
3. **ActiveCookCompoundData 57 行 = 模型①教科书**：`getOutputCount = (now-startTime)/costTime` 纯函数
4. **滑动 startTime 串行队列技巧**：takeCompound `startTime += costTime*count`，addCompound 空队回拨——单锚点算术滑动维护流水线，省 N 倍 Timer 开销
5. **公式与 Expedition(notes/59) 同族**：都是 `now - startTime` 形态的 lazy
6. **@Entity 嵌 Player.activeCookCompounds Map**（notes/30，同 notes/59 持久化模式）
7. **离线收益靠登录懒算**：onPlayerLogin → getCompoundQueueData(now) 一次算出离线产出
8. **投料 4 校验**：解锁→队列满→payItems 原子扣料(notes/38)→入队
9. **服务端重查 GameData 配方**，不信任客户端（同 notes/58 Shop 哲学）
10. **取件按 groupId 不按 compoundId**：服务端展开组内所有配方逐个 takeCompound
11. **ActionReason.Compound 入账**（notes/38 经济审计）
12. **★ Bug：static unlocked 全局串号**（注释 3 处 //TODO:bind it to player）——警惕私服 static 持玩家态
13. **极简(143+57 行) vs Stamina(715 行) 反差**：执行模型简单度 ∝ 资源时间性质（状态查询<连续积分）
14. **弱反作弊点**：count 上界/负数、groupId 合法性未校验（NPE 风险）
15. **"功能优先正确性 TODO"风格标本**（同 notes/61 Stamina）

---

## 10. 一句话总结

> **CookingCompoundManager (143 行 BasePlayerManager) + ActiveCookCompoundData (57 行 @Entity) = 时间锁串行制造系统 —— 产出全靠 `getOutputCount(now)=(now-startTime)/costTime` 纯函数懒算，零 Timer；投料 payItems 原子扣料入队、取件按 groupId 展开 takeCompound 并 `startTime+=costTime*count` 滑动锚点保留未完成进度；@Entity 嵌 Player Map 持久化，离线收益靠登录懒算；static unlocked 全局串号 bug.**
>
> **方法论意义: 本篇是 [[grasscutter-resource-execution-models]] 三分法的首个预测性验证——仅凭"资源时间性质=状态查询型"就事前正确预言落模型①Lazy 且公式与 Expedition 同族，证明三分法不仅能事后归纳还能事前预判研究方向；并示范 lazy 模型用"单 startTime 锚点算术滑动"优雅维护串行队列、用静态字段持玩家态的私服典型 bug.**

---

**前置笔记**：
- notes/61 StaminaManager - 建立资源执行模型三分法（本篇首次预测验证）
- notes/59 ExpeditionSystem - 同①Lazy，`now-startTime` 公式同族 + 离线收益 + @Entity Map 持久化
- notes/58 ShopSystem - "服务端重查配置不信任客户端"同哲学
- notes/38 Inventory - payItems 原子扣料 + ActionReason.Compound 经济审计
- notes/30 Database - @Entity Morphia embedded 嵌入 Player 文档

**关联文件**：
- `CookingCompoundManager.java`(143) - 3 Handler 调度 + static 配置
- `ActiveCookCompoundData.java`(57) - @Entity lazy 懒算核心 + 滑动 startTime
- `Player.java:152` - activeCookCompounds Map @Entity 持久化
- Handler：`HandlerGetCompoundDataReq` / `HandlerPlayerCompoundMaterialReq` / `HandlerTakeCompoundOutputReq`
- `CompoundData`(excel) - 配方：inputVec/outputVec/costTime/queueSize/groupId

**研究的源代码**: CookingCompoundManager 143 行 + ActiveCookCompoundData 57 行全文 + Player 持久化 + 3 handler 接线 + 三分法预测对照。
