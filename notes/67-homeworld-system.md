# HomeWorld 尘歌壶系统深度剖析

> 第 67 篇：迄今最大未挖系统，用 [[grasscutter-resource-execution-models]] 三分法**压力测试**。结论——**大特性系统不是单一执行模型，而是按子系统分解、各自归类的复合体**。这是三分法适用粒度的关键精炼：分类对象是"承载资源的子系统"，不是"功能系统"。附带抓到一个**完成校验被整段注释 → 瞬间白嫖家具的可利用 bug**，并补 [[grasscutter-payitems-missing-return]] 第 3 个正例、印证 notes/66 三层持久化 B 层。

---

## 0. 为什么用 HomeWorld 压力测试三分法

前 5 次预测验证（notes/62-66）对象都是**单一职责小系统**（一个合成台、一个锻造、一个盈花）。一个分类法的边界考验是：**面对庞大复合系统会不会失效？** 尘歌壶含家具制造 / 布局编辑 / 等级舒适度 / BGM / 进出权限 / 场景跳转……是检验三分法**适用粒度**的最佳样本。

> **事前预测**：HomeWorld 不会是单一模型。家具制造（有时间）→①Lazy；布局编辑（无时间）→第 0 类；整体是**复合体**。

读码：**预测命中**，且发现三分法的正确用法是**先把大系统拆成承载资源的子系统，再逐个归类**。

---

## 1. 尘歌壶系统全图（按子系统分解）

```
┌── GameHome @Entity("homes") 独立 collection (notes/66 B 层) ─┐
│ @Id id / @Indexed(unique) ownerUid / @Transient player      │
│ level / exp / furnitureMakeSlotItemList / sceneMap /         │
│ unlockedHomeBgmList / enterHomeOption                        │
│ save()→DatabaseHelper.saveHome  getByUid: 库无则 create      │
└──────┬──────────────────┬───────────────────┬───────────────┘
       │                  │                   │
  子系统A 家具制造      子系统B 布局编辑      子系统C BGM/权限
  ①Lazy(锚点)         第0类同步CRUD         第0类同步事务
  beginTime+durTime   HomeSceneItem.update  unlockedHomeBgmList Set
  ★完成校验被注释!     客户端全量覆盖+save   addUnlockedHomeBgm+save
       │                  │
  startMake/take       HandlerHomeUpdateArrangementInfoReq
  (FurnitureManager)   (homeScene.update→save→ack)
```

→ **一个 GameHome 文档 + 多个职能子系统**，每个子系统**各自的执行模型**。三分法不在"尘歌壶"层面套，而在"家具制造/布局编辑"层面分别套。

---

## 2. 持久化：印证 notes/66 三层模型 B 层

```java
@Entity(value = "homes", useDiscriminator = false)
public class GameHome {
    @Id String id;
    @Indexed(options = @IndexOptions(unique = true)) long ownerUid;   // 唯一索引
    @Transient Player player;                                          // 玩家引用不持久化
    public void save() { DatabaseHelper.saveHome(this); }              // 独立保存
    public static GameHome getByUid(uid) {                             // 库无则 create
        var home = DatabaseHelper.getHomeByUid(uid);
        return home == null ? GameHome.create(uid) : home;
    }
}
```

→ **B 层独立 collection**（notes/66 三层模型）：自有 `@Id` + `@Indexed(unique) ownerUid` + `DatabaseHelper.saveHome` + `getHomeByUid`，独立于 Player 文档。
→ 与 notes/65 BattlePass（`@Entity("battlepass")`）同款 B 层。**再次印证 notes/66 修正**：持久化层级由 Morphia 注解组合决定，与基类无关（GameHome 连 BasePlayerXxx 都不是，纯 POJO + Morphia）。
→ `@Transient player` + `getByUid` 库无则 create：玩家引用瞬态、文档惰性创建——B 层标准形态。

---

## 3. 子系统 A：家具制造 = ①Lazy 锚点，但**校验被废弃**（degenerate ①）

### 3.1 锚点存储（标准 ①Lazy）

`FurnitureMakeSlotItem`：
```java
@Entity @Data
public class FurnitureMakeSlotItem {
    @Id int index;
    int makeId; int avatarId;
    int beginTime;   // ★ 开始锚点
    int durTime;     // ★ 时长
}
```
`FurnitureManager.startMake`：
```java
if (makeData == null) { send(CONFIG_ERROR); return; }                       // ✅ 校验+return
if (levelData.getFurnitureMakeSlotCount() <= slotList.size()) { send(SLOT_FULL); return; }  // ✅ 槽位
if (!player.getInventory().payItems(makeData.getMaterialItems())) {
    send(RET_HOME_FURNITURE_COUNT_NOT_ENOUGH); return;                       // ✅ payItems 失败有 return!
}
var slot = FurnitureMakeSlotItem.of()
    .beginTime(Utils.getCurrentSeconds())     // 记 now
    .durTime(makeData.getMakeTime()).build(); // 记时长
slotList.add(slot); home.save();
```
→ `beginTime + durTime` 锚点 = 与 Compound `(now-startTime)/costTime`、Forge `(now-startTime)/forgeTime`、Expedition `now-startTime>=...` **同族 ①Lazy 设计**。**三分法预测命中**（家具制造子系统 = ①Lazy）。

### 3.2 ★ Bug：完成时间校验被整段注释 → 瞬间白嫖

`FurnitureManager.take`：
```java
// pay the speedup item
if (isFastFinish && !player.getInventory().payItem(107013,1)) { send(UNFINISH); return; }  // ✅

// check if player can take
//        if (slotItem.get().getBeginTime() + slotItem.get().getDurTime() >= Utils.getCurrentSeconds() && !isFastFinish) {
//            player.getSession().send(...RET_FURNITURE_MAKE_UNFINISH_VALUE...);
//            return;
//        }                                                              // ❌ 整段注释掉!

player.getInventory().addItem(makeData.getFurnitureItemID(), makeData.getCount());  // 无条件发货
slotList.remove(slotItem.get()); home.save();
```

→ **①Lazy 模型完整搭好（存了 beginTime/durTime），但唯一的懒判定被整段注释** → **家具制造瞬间完成**：`startMake` 后立刻 `take` 即得家具，制造计时器形同虚设。
→ 这是一种**"退化的 ①"（degenerate ①Lazy）**：锚点齐全、消费侧本应懒算却被禁用。三分法分类仍是 ①（设计意图），但**实现层面已劣化为第 0 类即时事务**。这是三分法压力测试的重要发现：**"设计模型"与"实际生效模型"可能因 bug/注释而背离**，分类要标注"设计 vs 实际"。
→ 可利用性：跳过尘歌壶家具制造时间（数小时）。属经济/进度漏洞，但尘歌壶为个人空间、家具非强经济物，危害低于 notes/63/64 的无料白嫖。
→ 旁注：被注释的条件 `beginTime+durTime >= now` 用 `>=`（应 `>`，边界差一秒），且作者可能因这个 off-by 误判而**直接注释掉了事**——"功能优先"风格的又一标本（接 notes/61/64）。

### 3.3 payItems 正例（补 [[grasscutter-payitems-missing-return]]）

`startMake` 的 `payItems` 失败**有 `return`**（§3.1）；`take` 的加速道具 `payItem` 失败**有 `return`**。
→ Furniture 是该 bug 类的**第 3 个正例**（正：合成台/盈花/家具；反：烹饪/锻造）。说明"缺 return"非全仓通病，而是**特定 handler 复制扩散**——更精确的结论：正确率约 3/5，需逐 handler 审计而非假设全错或全对。

---

## 4. 子系统 B：布局编辑 = 第 0 类同步事务（无时间维度）

`HandlerHomeUpdateArrangementInfoReq`：
```java
var homeScene = player.getHome().getHomeSceneItem(player.getSceneId());
homeScene.update(req.getSceneArrangementInfo());   // 客户端全量覆盖
player.getHome().save();
session.send(new PacketHomeUpdateArrangementInfoRsp());
```
`HomeSceneItem.update`：
```java
for (var blockItem : arrangementInfo.getBlockArrangementInfoList()) {
    var block = this.blockItems.get(blockItem.getBlockId());
    if (block == null) { warn; continue; }
    block.update(blockItem); this.blockItems.put(...);
}
this.bornPos = ...; this.djinnPos = ...; this.homeBgmId = ...; this.mainHouse = ...; this.tmpVersion = ...;
```

→ **纯第 0 类同步事务**：客户端发完整布局 → 服务端覆盖 `blockItems/bornPos/djinnPos/mainHouse` → save → ack。**无任何时间维度**，与 notes/63 烹饪、notes/58 Shop 买卖同类（单 handler 内 输入→落库→响应 闭环）。
→ **`calComfort()` = 纯派生函数**（`blockItems.values().stream().mapToInt(calComfort).sum()`）——按需现算的舒适度，**无持久化时间态**，类同 notes/62 Compound `getOutputCount` 但**不含时间**（纯聚合）。这是"第 0 类里的纯函数派生"子形态。
→ **完全信任客户端**：服务端**不校验**客户端发来的布局合法性（家具位置/数量/是否拥有），直接覆盖落库。违反 notes/58 "不信任客户端"原则——但尘歌壶是个人空间，恶意构造仅自损，**风险-收益权衡下的有意取舍**（与 notes/35 客户端权威边界一致：影响仅自身的状态可放权客户端）。

---

## 5. 子系统 C：BGM/权限 = 第 0 类（集合型事务）

```java
public boolean addUnlockedHomeBgm(int homeBgmId) {
    if (!getUnlockedHomeBgmList().add(homeBgmId)) return false;   // Set 去重
    player.sendPacket(new PacketHomeNewUnlockedBgmIdListNotify(homeBgmId));
    save();
    return true;
}
public Set<Integer> getUnlockedHomeBgmList() {
    if (this.unlockedHomeBgmList == null) this.unlockedHomeBgmList = new HashSet<>();
    if (this.unlockedHomeBgmList.addAll(getDefaultUnlockedHomeBgmIds())) save();  // 懒补默认
    return this.unlockedHomeBgmList;
}
```
→ BGM 解锁 = 第 0 类集合事务（Set add + save）。`getUnlockedHomeBgmList` 惰性补默认（首次访问合并 default，类同 notes/63 烹饪 `addDefaultUnlocked` 模式）。`enterHomeOption`（好友进入权限）同为简单标量事务。

---

## 6. 三分法压力测试结论：分类粒度 = 子系统

| 子系统 | 三分法 | 时间维度 | 实际生效 |
|---|---|---|---|
| 家具制造 | ①Lazy（设计）| 有（beginTime/durTime）| **退化为即时**（校验注释）|
| 布局编辑 | 第 0 类 | 无 | 第 0 类（同步 CRUD）|
| 舒适度 calComfort | 第 0 类·纯派生 | 无 | 按需聚合 |
| BGM/权限 | 第 0 类 | 无 | 集合事务 |

→ **关键方法论产出**：三分法的分类对象是**"承载资源的子系统"**，不是"功能系统"。大系统（尘歌壶/活动/角色养成）必然是**多子系统复合体**，正确用法是先分解再逐个归类。
→ 新增维度 **"设计模型 vs 实际生效模型"**：家具制造设计是①Lazy，因校验被注释**实际退化第 0 类**。审计时必须区分二者（bug/注释/降级会使实际背离设计）。
→ 这强化而非削弱三分法：它在子系统粒度依然 100% 适用（6+1 次验证无一失败），只是大系统需先分解。

---

## 7. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| **startMake 后立即 take 跳过制造时间** | **✓ 有效（§3.2 完成校验被注释）** |
| 伪造布局（非法家具/位置/未拥有）| ✓ 有效但仅自损（服务端不校验，个人空间，有意取舍）|
| 篡改 makeId 制造未解锁家具 | ⚠ makeData 配置校验有，但不查是否已解锁配方 |
| 槽位溢出 | ✗ getFurnitureMakeSlotCount 校验 + return |
| 无料制造 | ✗ startMake payItems 失败有 return（正例）|
| 篡改 home level/exp | ✗ 独立 collection 服务端账本 |

→ 个人空间类系统**有意弱校验布局**（自损无害），但 §3.2 完成校验注释是**真实进度漏洞**（非设计意图，是 bug）。

---

## 8. 关键收获

1. **三分法压力测试通过**：大系统按子系统分解后逐个归类，6+1 次验证零失败
2. **关键方法论产出**：三分法分类对象 = "承载资源的子系统"，非"功能系统"；大系统是复合体，先分解再归类
3. **新增维度"设计模型 vs 实际生效模型"**：家具制造设计①Lazy，校验被注释 → 实际退化第 0 类
4. **GameHome = B 层独立 collection**（`@Entity("homes")`+@Id+@Indexed unique ownerUid+saveHome），印证 notes/66 三层持久化（且 GameHome 非 BasePlayerXxx，纯 POJO+Morphia → 再证"持久化层级由注解组合定，与基类无关"）
5. **家具制造 = ①Lazy 锚点**：beginTime+durTime，与 Compound/Forge/Expedition 同族公式
6. **★ Bug：take() 完成时间校验整段注释 → 家具瞬间白嫖**（degenerate ①，可跳过数小时制造）
7. 被注释条件本身 off-by-one（`>=` 应 `>`），疑因边界 bug 被作者"注释了事"（功能优先风格，接 notes/61/64）
8. **Furniture 是 payItems-missing-return 第 3 正例**（正：合成台/盈花/家具，反：烹饪/锻造）→ 结论精炼为"~3/5 正确，须逐 handler 审计"
9. **布局编辑 = 第 0 类同步事务**：客户端全量覆盖 blockItems/bornPos/... → save → ack，无时间维度
10. **calComfort = 第 0 类纯派生函数**：按需聚合 blockItems 舒适度，无时间态（"纯函数派生"子形态）
11. **布局完全信任客户端**：服务端不校验布局合法性（违 notes/58，但个人空间自损无害，notes/35 客户端权威边界一致——影响仅自身可放权）
12. **BGM/权限 = 第 0 类集合事务**：Set add + save，惰性补默认（同 notes/63 addDefaultUnlocked 模式）
13. **getHomeSceneItem 惰性初始化**：库无则 parseFrom(HomeworldDefaultSaveData)（同 notes/66 配置重建思想，但此处持久化非 transient）
14. **startMake 多重前置校验齐全**：config/槽位/payItems 各有 return，唯独 take 完成校验缺失（前严后松，接 notes/64 cancelForge 反差观察）
15. **onOwnerLogin 推 6 个 Notify**：basicInfo/compInfo/comfort/arrangeCount/markPoint/bgm——登录态全量下发（第 0 类系统典型）
16. **home level 驱动容量**：getLevelData().getFurnitureMakeSlotCount() 等，等级=能力闸（exp 机制本文件未见，疑由 quest/reward 外部驱动）
17. **尘歌壶 = 多子系统复合**：制造(①退化)/布局(第0)/舒适(第0派生)/BGM(第0)/权限(第0)——以第 0 类为主、①Lazy 为辅
18. **持久化粒度**：每次布局/制造/BGM 变更都 `home.save()` 整文档——高频写独立 collection（同 notes/65 BattlePass 高频 save 拆 collection 动因）
19. **三分法韧性**：压力测试不仅未失效，反而精炼出"子系统粒度 + 设计/实际二维"，分类法更鲁棒
20. **HomeWorld 收官探索域**：继 notes/66 盈花后第 2 个探索/生活域系统，三分法跨域覆盖再扩

---

## 9. 一句话总结

> **HomeWorld 尘歌壶 = 多子系统复合体（GameHome @Entity("homes") B 层独立 collection）—— 家具制造子系统是 ①Lazy 锚点(beginTime+durTime, 与 Compound/Forge 同族)但 take() 完成校验被整段注释致退化为即时白嫖(可跳过数小时制造)；布局编辑是第 0 类同步事务(客户端全量覆盖 HomeSceneItem→save→ack, 完全信任客户端因个人空间自损无害)；calComfort 第 0 类纯派生、BGM/权限第 0 类集合事务；每次变更整文档 home.save().**
>
> **方法论意义: [[grasscutter-resource-execution-models]] 三分法压力测试通过并精炼——分类对象是"承载资源的子系统"非"功能系统"，大系统必为复合体须先分解再逐个归类(6+1 验证零失败)；新增"设计模型 vs 实际生效模型"维度(家具制造设计①因 bug 退化第 0)；印证 notes/66 三层持久化 B 层且 GameHome 纯 POJO 再证持久化层级由 Morphia 注解组合定与基类无关；补 [[grasscutter-payitems-missing-return]] 第 3 正例(精炼为 ~3/5 须逐 handler 审计)；揭示"完成校验被注释"这一新 bug 形态(设计/实际背离).**

---

**前置笔记**：
- notes/66 Blossom - 三层持久化模型（本篇 GameHome 印证 B 层 + 纯 POJO 再证与基类无关）
- notes/65 BattlePass - 同 B 层独立 collection + 高频 save 拆 collection 动因
- notes/62/64 Compound/Forge - 家具制造 ①Lazy 同族锚点公式（本篇是其"退化"变体）
- notes/63 Cooking - 第 0 类同步事务 + addDefaultUnlocked 惰性补默认同款
- notes/58 Shop - "不信任客户端"原则（本篇布局编辑有意违反的边界讨论）
- notes/35 战斗 - 客户端权威边界（影响仅自身状态可放权，本篇布局一致）
- notes/61/64 - "功能优先"风格 / 前严后松校验反差同类现象
- [[grasscutter-resource-execution-models]] - 三分法压力测试 + 粒度精炼
- [[grasscutter-payitems-missing-return]] - 第 3 个正例

**关联文件**：
- `GameHome.java`(127) - @Entity("homes") B 层 + sceneMap/furnitureMakeSlotItemList/bgm
- `FurnitureMakeSlotItem.java`(33) - @Entity beginTime+durTime ①Lazy 锚点
- `FurnitureManager.java`(139) - BasePlayerManager，startMake/take（★take 完成校验注释 :120-123）
- `HomeSceneItem.java`(96) - 布局：blockItems/bornPos/calComfort 纯派生
- `HandlerHomeUpdateArrangementInfoReq.java` - 第 0 类布局 CRUD（client→update→save→ack）
- `HomeBlockItem/HomeFurnitureItem/HomeNPCItem/HomeAnimalItem` - 布局子项
- `DatabaseHelper.saveHome/getHomeByUid` - B 层独立读写
- Bug 位点：`FurnitureManager.java:120-123`（完成时间校验整段注释）

**研究的源代码**: GameHome 127 + FurnitureMakeSlotItem 33 + FurnitureManager 139 + HomeSceneItem 96 全文 + 布局 handler + 持久化佐证。
