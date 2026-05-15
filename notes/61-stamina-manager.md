# StaminaManager 体力系统深度剖析

> 第 61 篇：notes/60 EnergyManager 末尾埋的对照线索。两者同为 `BasePlayerManager` 战斗/移动资源，但**执行模型截然相反**——Energy 是"客户端事件→服务端累计"，Stamina 是 **真实 200ms `Timer` 主动轮询 MotionState 状态机**。这揭示 grasscutter 的**第三种资源执行模型**，与 notes/50/57/58/59 的 lazy、notes/60 的事件累计三足鼎立。

---

## 0. 为什么这一篇重要

体力（冲刺/攀爬/游泳/飞行/划船）是原神探索循环的命脉。前面散落引用：
- notes/35 战斗：客户端权威——但体力**溺水致死是服务端权威 kill**（反例）
- notes/60 Energy：约定了"同为 BasePlayerManager，对照写"
- notes/50/57/58/59：lazy evaluation 模式 4 连——本篇是**第三种执行模型**的硬证据

本篇要回答：体力怎么算的？为什么不用 lazy？715 行里那张巨大的 MotionState 表是什么？溺水死亡为何能绕过客户端权威？

---

## 1. 体力系统全图

```
┌── 客户端持续上报 ──────────────────────────────────────────┐
│ CombatInvocationsNotify (EntityMoveInfo)                    │
│   → handleCombatInvocationsNotify                           │
│   → 缓存 session/entity, 更新 currentState/currentCoordinates│
│   → startSustainedStaminaHandler()  ★ 启动 200ms Timer      │
│   → handleImmediateStamina (状态切换瞬时扣)                  │
│ EvtDoSkillSuccNotify → handleEvtDoSkillSuccNotify (技能扣)   │
│ VehicleInteractReq → handleVehicleInteractReq (上下载具)     │
│ AbilityMixin → handleMixinCostStamina (天赋位移/重击持续)    │
└────────────────────────┬───────────────────────────────────┘
                         │
                         ↓ 每 200ms 一拍
┌── SustainedStaminaHandler (TimerTask) ★ 主动轮询核心 ───────┐
│ 1. isPlayerMoving (坐标差判定)                              │
│ 2. currentState → 10 大类 (CLIMB/DASH/FLY/RUN/SKIFF/...)    │
│ 3. 各类 getXxxConsumption → Consumption{type, amount}       │
│    amount < 0 = 消耗, amount > 0 = 恢复                      │
│ 4. 食物/天赋/共鸣 减免系数                                   │
│ 5. 恢复延迟 5 拍 (1 秒, 还原官服)                            │
│ 6. updateStaminaRelative → setStamina                       │
└────────────────────────┬───────────────────────────────────┘
                         ↓
┌── 账本 PlayerProperty.PROP_CUR_PERSIST_STAMINA ────────────┐
│ 角色体力 (player property) / 载具体力 (vehicleStamina 字段)  │
│ before/afterUpdateStaminaListeners 扩展钩子                  │
│ 溺水: stamina<10 且非 SWIM_IDLE → killAvatar(DIE_DRAWN) ★    │
└─────────────────────────────────────────────────────────────┘
```

→ **715 行**——grasscutter 最复杂的 BasePlayerManager 之一，全靠一个 200ms `Timer` 驱动。

---

## 2. 三种执行模型的确立（核心架构发现）

| 模型 | 代表系统 | 机制 | 笔记 |
|---|---|---|---|
| **① Lazy 懒计算** | Resin/Mail/Shop/Expedition | 无定时器，操作时一次性算 | notes/50/57/58/59 |
| **② 事件累计** | Energy 能量 | 客户端报事件，服务端逐事件累计账本 | notes/60 |
| **③ 主动轮询** | **Stamina 体力** | 真实 `Timer` 每 200ms 拍一次状态机 | **本篇** |

→ Stamina **必须**用主动轮询：体力是**连续时间积分**（每秒攀爬扣固定值），lazy 无法"被动"知道玩家正在持续攀爬。Energy 是离散事件（命中/拾取），事件累计即可。
→ **这解释了 grasscutter 为何不强求统一执行模型**——资源的"时间性质"决定模型：离散事件→事件累计，时间积分→主动轮询，状态查询→lazy。
→ 与 [[grasscutter-lazy-evaluation-时间系统模式]] 记忆形成互补：lazy 是偏好但非教条，连续型资源仍用 Timer。

---

## 3. 启动/停止：真实 java.util.Timer

```java
public void startSustainedStaminaHandler() {
    if (!player.isPaused() && sustainedStaminaHandlerTimer == null) {
        sustainedStaminaHandlerTimer = new Timer();
        sustainedStaminaHandlerTimer.scheduleAtFixedRate(new SustainedStaminaHandler(), 0, 200);  // 200ms
    }
}
public void stopSustainedStaminaHandler() {
    if (sustainedStaminaHandlerTimer != null) {
        sustainedStaminaHandlerTimer.cancel();
        sustainedStaminaHandlerTimer = null;
    }
}
```

→ **每个玩家一个独立 Timer**（懒启动：首次移动上报时才 new）。
→ `player.isPaused()` 暂停（单机暂停）时不启动 —— 与 lazy 系统"无定时器"形成最强反差。
→ 注意日志前缀写 `[MovementManager]` —— 历史上从 MovementManager 拆出，命名遗留（类似 notes/53 Coop 命名陷阱）。

---

## 4. MotionState 巨型分类表（10 大类）

`StaminaManager` 顶部一张静态 `Map<String, Set<MotionState>>` 把约 60 个 `MotionState` 归 10 类：

| 类别 | 含义 | 体力 |
|---|---|---|
| CLIMB | 攀爬（移动才扣，静止不扣不回）| 扣 |
| DASH | 闪避冲刺 | 扣 |
| FLY | 滑翔（POWERED_FLY 风场免费）| 扣/特例 |
| RUN | 疾跑 | 扣 |
| SKIFF | 浪船（用**载具**体力池！）| 扣（载具）|
| STANDBY | 待机 | 回 |
| SWIM | 游泳/游泳冲刺（含溺水判定）| 扣 |
| WALK | 行走 | 回 |
| OTHER | FIGHT/CLIMB_JUMP/NOTIFY 等混合 | 视情况 |
| NOCOST_NORECOVER | 滑步等 | 0 |
| IGNORE | 蹲伏/爬梯/瀑布等约 18 态 | 无影响 |

→ 代码注释极其考究，标注每个态是 `sustained`(持续) / `immediate`(瞬时) / `recover`(恢复) / `NOT OBSERVED`(抓包没见过)。
→ 这是**逆向工程的活化石**——开发者靠抓包观察客户端发什么 MotionState，反推体力规则。
→ `SKIFF` 类切 `isCharacterStamina = false` → 用 `vehicleStamina` 字段而非 player property（**双体力池**）。

---

## 5. SustainedStaminaHandler：每 200ms 一拍

```java
public void run() {
    boolean moving = isPlayerMoving();
    if (moving || curStamina < maxStamina || curVehicleStamina < maxVehicle) {
        Consumption consumption;
        if      (CLIMB.contains(currentState))  consumption = getClimbConsumption();
        else if (DASH.contains(currentState))   consumption = getDashConsumption();
        else if (FLY.contains(currentState))    consumption = getFlyConsumption();
        else if (RUN.contains(currentState))    consumption = new Consumption(RUN);
        else if (SKIFF.contains(currentState)) { consumption = getSkiffConsumption(); isCharacterStamina = false; }
        else if (STANDBY.contains(currentState)) consumption = new Consumption(STANDBY);
        else if (SWIM.contains(currentState))   consumption = getSwimConsumptions();
        else if (WALK.contains(currentState))   consumption = new Consumption(WALK);
        else if (NOCOST_NORECOVER...)           consumption = new Consumption();
        else if (OTHER...)                      consumption = getOtherConsumptions();
        else return;  // IGNORE

        // 风共鸣 10301: 消耗 ×0.85
        if (consumption.amount < 0 && teamResonances.contains(10301)) consumption.amount *= 0.85f;

        // 恢复延迟 5 拍 = 1 秒 (还原官服)
        if (consumption.amount < 0) staminaRecoverDelay = 0;
        if (consumption.amount > 0 && type != POWERED_FLY && type != POWERED_SKIFF) {
            if (staminaRecoverDelay < 5) { staminaRecoverDelay++; consumption.amount = 0; }
        }
        updateStaminaRelative(cachedSession, consumption, isCharacterStamina);
    }
    previousState = currentState;
    previousCoordinates = currentCoordinates.clone();
}
```

要点：
1. **空转优化**：满体力且不移动 → 直接 return（不算）。这是 lazy 思想的局部渗透（即使在 Timer 模型里也省算）。
2. **符号约定**：`amount < 0` = 消耗，`amount > 0` = 恢复。`updateStaminaRelative` 里 `newStamina = cur + amount`。
3. **风共鸣 0.85**：队伍含「疾风」共鸣（teamResonance 10301）冲刺/攀爬消耗 ×0.85（还原真实机制）。
4. **恢复延迟 1 秒**：停止消耗后等 5 拍（5×200ms）才开始回体力，注释明确 "as official server does"。`POWERED_*`（风场/激流）立即回（安柏滑翔考核需要）。
5. **isPlayerMoving 用坐标差**：`|Δx|>0.3 || |Δy|>0.2 || |Δz|>0.3` —— 阈值经验值，防抖。

---

## 6. 瞬时消耗 vs 持续消耗

体力扣分两条路径：

### 6.1 瞬时（状态切换那一下）

`handleImmediateStamina(session, MotionState)`：
```java
if (previousState == currentState) return;   // 防重复扣
switch (motionState) {
    case MOTION_CLIMB         -> CLIMB_START   // 起步攀爬
    case MOTION_DASH_BEFORE_SHAKE -> SPRINT    // 起跑冲刺
    case MOTION_CLIMB_JUMP    -> CLIMB_JUMP    // 攀爬跳
    case MOTION_SWIM_DASH     -> SWIM_DASH_START
}
```
→ 客户端上报状态**变化**触发一次性扣（如攀爬起手扣一笔）。

### 6.2 持续（Timer 每拍）

`SustainedStaminaHandler` 每 200ms 按当前状态持续扣（如攀爬中每拍扣）。

→ **瞬时（事件驱动）+ 持续（Timer 轮询）双路径** —— 单一系统里**两种执行模型并存**，进一步说明 grasscutter "按资源时间性质选模型"。

---

## 7. 武器类型重击/技能耗体力（数据内嵌）

`getFightConsumption(skillId)` 按当前角色武器类型分派：

| 武器 | 重击/技能体力 | 备注 |
|---|---|---|
| 双手剑 CLAYMORE | -1333 | 注释 `4000/3` —— 每拍扣，3 拍≈一次重击 |
| 法器 CATALYST | -5000 | 法器重击耗体力最高 |
| 长柄 POLE | -2500 | |
| 单手剑 SWORD | -2000 | 特例 skill 10421 → -2500 |
| 弓 BOW | **+500** | 注释 "bow skills actually recovers stamina" 弓蓄力不耗反"回"（实为不扣）|
| 天赋位移 10013/10413 | 首拍 -1000，后续 -500 | 神里/绫人风格位移 |

→ 角色特例硬编码（如双手剑 skill 10571/10532 免费、10160 带天赋 162101 减半）。
→ 大量 `// TODO` —— 这是**未完成的逆向**，作者坦承"普攻/重击难区分"。
→ 双手剑特殊：`handleEvtDoSkillSuccNotify` 显式排除，留给 `handleMixinCostStamina`（AbilityMixin 触发）—— 跨 notes/16 Ability Mixin 协作。

---

## 8. 溺水致死：服务端权威 kill（notes/35 反例）

```java
private void handleDrowning() {
    int stamina = getCurrentCharacterStamina();
    if (stamina < 10) {
        if (currentState != MotionState.MOTION_SWIM_IDLE) {
            killAvatar(cachedSession, cachedEntity, PlayerDieType.PLAYER_DIE_DRAWN);
        }
    }
}

public void killAvatar(GameSession session, GameEntity entity, PlayerDieType dieType) {
    session.send(new PacketAvatarLifeStateChangeNotify(currentAvatar, LIFE_DEAD, dieType));
    session.send(new PacketLifeStateChangeNotify(entity, LIFE_DEAD, dieType));
    entity.setFightProperty(FIGHT_PROP_CUR_HP, 0);
    entity.getWorld().broadcastPacket(new PacketEntityFightPropUpdateNotify(entity, FIGHT_PROP_CUR_HP));
    entity.getWorld().broadcastPacket(new PacketLifeStateChangeNotify(0, entity, LIFE_DEAD));
    player.getScene().removeEntity(entity);
    ((EntityAvatar) entity).onDeath(dieType, 0);
}
```

→ **重大发现**：notes/35 说战斗伤害/死亡是**客户端权威**（客户端报 HP）。但**溺水死亡是服务端权威**——服务端在 SWIM 体力轮询里检测到体力<10 且仍在游（非 SWIM_IDLE 浮水），**主动**把角色 HP 设 0、广播死亡、移除实体、调 onDeath。
→ 为什么溺水能例外？因为体力是服务端独算的（Timer 轮询），服务端**确知**体力耗尽，无需信任客户端。
→ 这印证 notes/35 主题的精确边界：**凡服务端有独立账本的资源（能量/体力），其衍生后果（溺水死）就能服务端权威**；凡依赖客户端模拟的（战斗伤害），才客户端权威。

---

## 9. 扩展钩子：before/after UpdateStamina Listener

```java
registerBeforeUpdateStaminaListener(name, listener);  // 可改写/拦截消耗
registerAfterUpdateStaminaListener(name, listener);   // 消耗后回调
```

→ `updateStaminaRelative/Absolute` 里遍历 before 监听器，监听器可**否决**本次更新（return 原值）。
→ 这是给**插件系统（notes/47）/ AbilityMixin** 预留的扩展点 —— 与 grasscutter 处处可见的"注册式扩展"一脉相承（[[grasscutter-同构架构模式]]）。

---

## 10. 双开关 + 双体力池

```java
// setStamina 开头
if (!GAME_OPTIONS.staminaUsage || player.getUnlimitedStamina()) {
    newStamina = getMaxCharacterStamina();   // 强制满
}
```
→ 全局 `GAME_OPTIONS.staminaUsage` + 玩家级 `getUnlimitedStamina()` 双开关——与 notes/60 Energy 的 `energyUsage` 双开关同构。
→ **双体力池**：角色体力 = `PlayerProperty.PROP_CUR_PERSIST_STAMINA`（持久化进 player），载具体力 = `vehicleStamina` 内存字段（不持久化，下船重置）。上船时两池都重置满（防出水即溺水）。

---

## 11. 与 Energy 的全面对照（notes/60）

| 维度 | EnergyManager (notes/60) | StaminaManager (本篇) |
|---|---|---|
| 执行模型 | 事件累计（命中/拾取触发）| 主动轮询（200ms Timer）|
| 资源时间性质 | 离散事件 | 连续时间积分 |
| 产能/恢复 | 概率递增 pity / 元素球 | 停手延迟 1 秒匀速回 |
| 客户端角色 | 报战斗事件，服务端算数值 | 报 MotionState，服务端算积分 |
| 账本位置 | EntityAvatar FightProperty | Player PROP_CUR_PERSIST_STAMINA + vehicle 字段 |
| 致死能力 | 无（能量空只是放不出大招）| **有（溺水服务端 kill）** |
| 减免来源 | 充能效率 CHARGE_EFFICIENCY | 食物/天赋/风共鸣 ×0.85 |
| 双开关 | energyUsage（全局+玩家）| staminaUsage（全局+玩家）|
| 数据驱动 | JSON（粒子/掉球）| 几乎全硬编码（武器/角色特例 + 大量 TODO）|
| 完成度 | 较完整（1:1 复刻公式）| 较多 TODO（逆向未完）|

→ 同为 `BasePlayerManager`、同有双开关、同"客户端报事件服务端算"，但**因资源时间性质不同走了相反执行模型**——这是理解 grasscutter 资源架构的关键对照。

---

## 12. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 改客户端伪报 MotionState=IGNORE 类骗免体力 | ✓ 有效（服务端信任 MotionState）|
| 体力为 0 仍冲刺 | ✓ 有效（不阻止动作，只扣到 0 封底）|
| 篡改体力 property | ✗ 服务端 Timer 独算账本 |
| 绕过溺水死 | ✗ 服务端权威 kill（除非伪报 SWIM_IDLE）|
| 体力溢出/负数 | ✗ updateStamina 钳 [0, max] |

→ 体力**反作弊偏弱**（信任客户端 MotionState 流，不阻止 0 体力动作），属 grasscutter 私服一贯取舍；但**溺水死是服务端权威**，比 Energy 多一道服务端硬约束。

---

## 13. 关键收获

1. **StaminaManager 715 行 = BasePlayerManager**，靠真实 `java.util.Timer` 每 200ms 驱动
2. **第三种执行模型确立**：lazy（Resin 等）/ 事件累计（Energy）/ **主动轮询（Stamina）**
3. **模型由资源时间性质决定**：连续时间积分必须 Timer 轮询，lazy/事件无法表达
4. **grasscutter 不强求统一执行模型** —— lazy 是偏好非教条
5. **每玩家独立 Timer**，懒启动（首次移动才 new），`isPaused()` 暂停不启动
6. **MotionState 巨型分类表**：约 60 态归 10 类，注释标 sustained/immediate/recover/NOT OBSERVED
7. **逆向工程活化石**：靠抓包反推体力规则，大量 `// TODO` 坦承未完成
8. **双体力池**：角色体力（player property 持久化）vs 载具体力（内存字段）
9. **瞬时 + 持续双路径**：状态切换事件扣一次 + Timer 每拍持续扣（单系统两模型并存）
10. **符号约定**：amount<0 消耗 / amount>0 恢复，`newStamina = cur + amount` 钳 [0,max]
11. **空转优化**：满体力且不动直接 return（Timer 模型里的局部 lazy 思想）
12. **恢复延迟 1 秒**（5×200ms）还原官服，POWERED_* 立即回（滑翔考核）
13. **风共鸣 10301 消耗 ×0.85**，食物/天赋减免（多为 TODO 桩）
14. **武器类型重击耗体力内嵌**：法器-5000/双手剑-1333(4000/3)/长柄-2500/单手剑-2000/弓+500
15. **★ 溺水致死 = 服务端权威 kill**（notes/35 客户端权威主题的精确边界反例）
16. **服务端权威边界规律**：有独立账本的资源（能量/体力）其后果可服务端权威，依赖客户端模拟的（战斗伤害）才客户端权威
17. **before/after Listener 扩展钩子**，before 可否决更新（插件/Mixin 预留）
18. **双开关**：GAME_OPTIONS.staminaUsage（全局）+ getUnlimitedStamina（玩家），关闭强制满
19. **命名遗留**：日志写 [MovementManager]，从 MovementManager 拆出（命名陷阱，类比 notes/53）
20. **与 Energy 全面对照**：同 BasePlayerManager/双开关/客户端报事件，因资源时间性质走相反模型

---

## 14. 一句话总结

> **StaminaManager (715 行 BasePlayerManager) = 体力连续积分系统 —— 每玩家独立 java.util.Timer 每 200ms 轮询客户端上报的 MotionState（约 60 态归 10 类），按攀爬/冲刺/游泳/飞行/划船/重击算 Consumption（amount<0 消耗 amount>0 恢复，钳 [0,max]），停手延迟 1 秒匀速回、风共鸣 ×0.85；双体力池（角色 property 持久化 + 载具内存）；溺水(体力<10 且非浮水)触发服务端权威 killAvatar(DIE_DRAWN).**
>
> **架构本质: 确立 grasscutter 第三种资源执行模型——主动 Timer 轮询，与 lazy（Resin/Mail/Shop/Expedition）、事件累计（Energy）三足鼎立；模型由资源"时间性质"决定（连续积分→轮询，离散事件→累计，状态查询→lazy），证明 lazy 是偏好而非教条；溺水服务端 kill 划清 notes/35 客户端权威的精确边界——有独立账本的资源其衍生后果可服务端权威.**

---

**前置笔记**：
- notes/35 战斗 - 客户端权威主题（本篇溺水死是精确边界反例）
- notes/60 EnergyManager - 同 BasePlayerManager 战斗资源，事件累计 vs 本篇主动轮询全面对照
- notes/50/57/58/59 - lazy evaluation 模式（本篇是第三种执行模型对照）
- notes/16 Ability - Mixin 触发 handleMixinCostStamina（双手剑重击）
- notes/47 Plugin/Event - before/after Listener 扩展钩子去向
- notes/53 Coop 命名陷阱 - [MovementManager] 命名遗留同类现象

**关联文件**：
- `StaminaManager.java`(715) - 体力轮询核心
- `Consumption.java` / `ConsumptionType.java` - 消耗描述（type+amount）
- `BeforeUpdateStaminaListener` / `AfterUpdateStaminaListener` - 扩展钩子接口
- 调用点：`HandlerCombatInvocationsNotify`(移动上报) / `HandlerEvtDoSkillSuccNotify:20`(技能) / `HandlerVehicleInteractReq`(载具) / AbilityMixin(handleMixinCostStamina)
- `PlayerProperty.PROP_CUR_PERSIST_STAMINA` - 角色体力账本

**研究的源代码**: StaminaManager 715 行全文 + 调用点 + Energy 对照（notes/60）。
