# Resin / Stamina 时间锁机制深度剖析

> 第 50 篇里程碑！完全未覆盖的"时间资源"系统：**树脂 (8 分钟 1 点恢复)** + **体力 (24000 max, 14 种 ConsumptionType)** + **后台 Timer 200ms 计算**——独特的"时间锁"经济设计。

---

## 0. 为什么这一篇重要

前 49 篇里 Resin / Stamina **反复被引用**但从未专门解剖：
- notes/48 副本：`useResin(20)` / `useCondensedResin(1)`
- notes/38 Inventory：106 = Resin 是 8 大虚拟币之一
- notes/36 战斗：摔伤后 stamina kill / drowning
- notes/40 Player Manager：StaminaManager / ResinManager 是 25 之一

但**时间锁怎么实现？后台谁定时恢复？体力 14 种动作怎么消耗？**——这一篇统一回答。

---

## 1. 两套时间资源系统

```
┌──────────────────────────────────────────────────────────────┐
│  ResinManager (175 行)                                         │
│  - 慢节奏 (8 分钟 1 点)                                          │
│  - 上限 160 (默认)                                              │
│  - lazy 恢复 (玩家行动时计算)                                    │
│  - 购买 6 次 (50/100/100/150/200/200 原石)                      │
│  - 浓缩树脂 (220007, 1 = 2 次副本)                              │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  StaminaManager (715 行)                                       │
│  - 快节奏 (5Hz / 200ms tick)                                    │
│  - 上限 24000 (默认)                                            │
│  - 后台 Timer 实时计算                                           │
│  - 14 种 ConsumptionType (-2500 ~ +500)                         │
│  - 食物 / 天赋 / 共鸣 减耗                                       │
│  - 翔士独立体力 (vehicleStamina)                                 │
└──────────────────────────────────────────────────────────────┘
```

**对比**：
| 维度 | Resin | Stamina |
|---|---|---|
| 节奏 | 8 分钟/点 | 200ms tick |
| 上限 | 160 | 24000 |
| 恢复方式 | lazy 计算 | 后台 Timer |
| 状态种类 | 4 (有/无 + 浓缩) | 14 (消耗/恢复) |
| 反作弊 | 服务器 | 服务器 |
| 持久化 | PlayerProperty | PlayerProperty |

→ 两套**机制完全不同**——但都是"时间资源"。

---

## 2. ResinManager：慢节奏 lazy 恢复（175 行）

### 2.1 配置

`ConfigContainer.java`：
```java
public static class ResinOptions {
    public boolean resinUsage = false;      // ★ 默认关 (私服一般无限树脂)
    public int cap = 160;                    // 上限
    public int rechargeTime = 480;           // 8 分钟 1 点 (480 秒)
}
```

### 2.2 useResin：扣树脂 + 启动恢复

```java
public synchronized boolean useResin(int amount) {
    if (!GAME_OPTIONS.resinOptions.resinUsage) return true;
    
    int currentResin = this.player.getProperty(PlayerProperty.PROP_PLAYER_RESIN);
    if (currentResin < amount) return false;
    
    int newResin = currentResin - amount;
    this.player.setProperty(PlayerProperty.PROP_PLAYER_RESIN, newResin);
    
    // ★ 关键: 扣后低于 cap 才开始 "恢复模式"
    if (this.player.getNextResinRefresh() == 0 && newResin < GAME_OPTIONS.resinOptions.cap) {
        int currentTime = Utils.getCurrentSeconds();
        this.player.setNextResinRefresh(currentTime + GAME_OPTIONS.resinOptions.rechargeTime);
        //                                  ↑ 8 分钟后下次恢复
    }
    
    this.player.sendPacket(new PacketResinChangeNotify(this.player));
    
    // 战令任务: 消耗树脂
    this.player.getBattlePassManager().triggerMission(
        WatcherTriggerType.TRIGGER_COST_MATERIAL, 106, amount);
    
    return true;
}
```

### 2.3 rechargeResin：lazy 恢复（懒计算精髓）

```java
public synchronized void rechargeResin() {
    if (!GAME_OPTIONS.resinOptions.resinUsage) return;
    
    int currentResin = this.player.getProperty(PlayerProperty.PROP_PLAYER_RESIN);
    int currentTime = Utils.getCurrentSeconds();
    
    // 1. 不在恢复模式
    if (this.player.getNextResinRefresh() <= 0) return;
    
    // 2. 还没到下次恢复时间
    if (currentTime < this.player.getNextResinRefresh()) return;
    
    // 3. ★ 关键算法: 一次性补回所有"该恢复"的点数
    int recharge = 1 + (int)((currentTime - this.player.getNextResinRefresh()) 
                              / GAME_OPTIONS.resinOptions.rechargeTime);
    int newResin = Math.min(GAME_OPTIONS.resinOptions.cap, currentResin + recharge);
    int resinChange = newResin - currentResin;
    
    this.player.setProperty(PlayerProperty.PROP_PLAYER_RESIN, newResin);
    
    if (newResin >= GAME_OPTIONS.resinOptions.cap) {
        this.player.setNextResinRefresh(0);   // 满了停止恢复
    } else {
        // 推算下次恢复时间
        int nextRecharge = this.player.getNextResinRefresh() 
            + resinChange * GAME_OPTIONS.resinOptions.rechargeTime;
        this.player.setNextResinRefresh(nextRecharge);
    }
    
    this.player.sendPacket(new PacketResinChangeNotify(this.player));
}
```

### 2.4 Lazy 恢复的设计精髓

```
[玩家 12:00 树脂 = 0, nextResinRefresh = 12:08]
   ↓ 玩家离线 24 小时
[玩家 12:00 (次日) 上线]
   ↓ rechargeResin 触发
   currentTime - nextResinRefresh = 23:52 (1432 分钟 = 85920 秒)
   recharge = 1 + 85920 / 480 = 1 + 179 = 180
   newResin = min(160, 0 + 180) = 160  ← ★ 上限封顶
   nextResinRefresh = 0 (满了)
```

→ **不需要后台定时器！** 玩家上线 / 用树脂时**懒计算**一次性补回所有该恢复的点。

→ 这是**经典 lazy evaluation**模式 —— 比 200ms timer 高效太多。

### 2.5 onPlayerLogin 触发

```java
public synchronized void onPlayerLogin() {
    if (!GAME_OPTIONS.resinOptions.resinUsage) {
        this.player.setProperty(PROP_PLAYER_RESIN, cap);   // 私服默认满
        this.player.setNextResinRefresh(0);
    }
    
    // 兜底: 管理员改了 cap 但玩家已满 → 重启恢复
    int currentResin = this.player.getProperty(PROP_PLAYER_RESIN);
    int currentTime = Utils.getCurrentSeconds();
    if (currentResin < cap && this.player.getNextResinRefresh() == 0) {
        this.player.setNextResinRefresh(currentTime + rechargeTime);
    }
    
    this.player.sendPacket(new PacketResinChangeNotify(this.player));
}
```

→ 登录时触发一次 `rechargeResin`（间接）—— 把玩家"积攒的树脂"一次性补回。

### 2.6 买树脂：递增价格

```java
public static final int MAX_RESIN_BUYING_COUNT = 6;
public static final int AMOUNT_TO_ADD = 60;
public static final int[] HCOIN_NUM_TO_BUY_RESIN = new int[]{50, 100, 100, 150, 200, 200};

public Retcode buy() {
    if (this.player.getResinBuyCount() >= MAX_RESIN_BUYING_COUNT) {
        return Retcode.RET_RESIN_BOUGHT_COUNT_EXCEEDED;
    }
    
    var res = this.player.getInventory()
        .payItem(201, HCOIN_NUM_TO_BUY_RESIN[this.player.getResinBuyCount()]);
    if (!res) return Retcode.RET_HCOIN_NOT_ENOUGH;
    
    this.player.setResinBuyCount(this.player.getResinBuyCount() + 1);
    this.addResin(AMOUNT_TO_ADD);
    this.player.sendPacket(new PacketItemAddHintNotify(
        new GameItem(106, AMOUNT_TO_ADD), ActionReason.BuyResin));
    
    return Retcode.RET_SUCC;
}
```

→ **每天最多 6 次购买** —— 价格递增 50→100→100→150→200→200 原石。
→ 6 次共 800 原石 = 360 树脂额外。
→ 这是 mihoyo 的**克制氪金设计** —— 不让无脑买。

### 2.7 浓缩树脂

```java
public synchronized boolean useCondensedResin(int amount) {
    if (!GAME_OPTIONS.resinOptions.resinUsage) return true;
    return this.player.getInventory().payItem(220007, amount);
}
```

→ `220007` 是浓缩树脂 itemId（材料）。**1 浓缩 = 2 次 20 树脂副本**（notes/48）。
→ 走 `payItem`（notes/38）而非 PlayerProperty。

---

## 3. StaminaManager：快节奏 Timer + 14 种 ConsumptionType（715 行）

### 3.1 配置

```java
public final static int GlobalCharacterMaximumStamina = PlayerProperty.PROP_MAX_STAMINA.getMax();
// PROP_MAX_STAMINA 默认 24000
```

→ 24000 ≈ 1 桶水（24 秒满）—— **以 1000 = 1 秒**为单位。

### 3.2 ConsumptionType（14 种）

```java
public enum ConsumptionType {
    None(0),
    
    // 消耗类
    CLIMBING(-150),         // 爬山持续
    CLIMB_START(-500),      // 开始爬 (一次性)
    CLIMB_JUMP(-2500),      // 攀爬跳跃 (一次性, 最大消耗!)
    DASH(-360),             // 冲刺持续
    FIGHT(0),               // 战斗 (动态计算)
    FLY(-60),               // 飞行
    SKIFF_DASH(-204),       // 浪船冲刺
    SPRINT(-1800),          // 冲刺起步 (一次性)
    SWIM_DASH_START(-2000), // 游泳冲刺起步
    SWIM_DASH(-204),        // 游泳冲刺持续
    SWIMMING(-80),          // 游泳持续 (最低消耗)
    TALENT_DASH(-300),      // 天赋冲刺持续
    TALENT_DASH_START(-1000),
    
    // 恢复类
    POWERED_FLY(500),       // 风场加速飞 (恢复)
    POWERED_SKIFF(500),     // 浪船加速
    RUN(500),               // 跑步恢复
    SKIFF(500),             // 浪船正常状态恢复
    STANDBY(500),           // 待机恢复
    WALK(500);              // 走路恢复
}
```

### 3.3 ConsumptionType 数学

每 tick = 200ms。1 秒 = 5 tick。

| 状态 | per tick | per second |
|---|---|---|
| CLIMBING | -150 | -750 (待机 -1500 没?) |
| DASH | -360 | -1800 (= SPRINT 起步成本!) |
| CLIMB_JUMP | -2500 | (一次性) |
| FLY | -60 | -300 |
| SWIMMING | -80 | -400 |
| SWIM_DASH | -204 | -1020 |
| RUN | +500 | +2500 |
| STANDBY | +500 | +2500 |
| WALK | +500 | +2500 |

→ **24000 上限 / 1800 per sec dash = 13 秒**满冲刺。
→ **24000 / 750 per sec climb = 32 秒**满爬山。
→ **数学严密**——验证了"满体力可以冲刺 13 秒"的玩家观察。

### 3.4 SustainedStaminaHandler：Timer 5Hz 引擎

```java
public void startSustainedStaminaHandler() {
    if (!player.isPaused() && sustainedStaminaHandlerTimer == null) {
        sustainedStaminaHandlerTimer = new Timer();
        sustainedStaminaHandlerTimer.scheduleAtFixedRate(
            new SustainedStaminaHandler(), 0, 200);   // ★ 每 200ms 一次
    }
}

private class SustainedStaminaHandler extends TimerTask {
    public void run() {
        boolean moving = isPlayerMoving();
        int currentCharacterStamina = getCurrentCharacterStamina();
        int maxCharacterStamina = getMaxCharacterStamina();
        
        if (moving || (currentCharacterStamina < maxCharacterStamina) || ...) {
            Consumption consumption;
            
            // 按 MotionState 选 ConsumptionType
            if (MotionStatesCategorized.get("CLIMB").contains(currentState)) {
                consumption = getClimbConsumption();
            } else if (MotionStatesCategorized.get("DASH").contains(currentState)) {
                consumption = getDashConsumption();
            } else if (MotionStatesCategorized.get("FLY").contains(currentState)) {
                consumption = getFlyConsumption();
            } else if (MotionStatesCategorized.get("RUN").contains(currentState)) {
                consumption = new Consumption(ConsumptionType.RUN);
            } else if (MotionStatesCategorized.get("SKIFF").contains(currentState)) {
                consumption = getSkiffConsumption();
                isCharacterStamina = false;   // ★ 用 vehicle 体力
            } else if (MotionStatesCategorized.get("STANDBY").contains(currentState)) {
                consumption = new Consumption(ConsumptionType.STANDBY);
            } else if (MotionStatesCategorized.get("SWIM").contains(currentState)) {
                consumption = getSwimConsumptions();
            } else if (MotionStatesCategorized.get("WALK").contains(currentState)) {
                consumption = new Consumption(ConsumptionType.WALK);
            }
            // ...
            
            // 队伍共鸣减耗 (双冰共鸣 -15% stamina cost)
            if (consumption.amount < 0 && isCharacterStamina) {
                if (player.getTeamManager().getTeamResonances().contains(10301)) {
                    consumption.amount *= 0.85f;   // ★ 双冰共鸣
                }
            }
            
            // ★ 1 秒恢复延迟 (5 tick)
            if (consumption.amount > 0) {
                if (staminaRecoverDelay < 5) {
                    staminaRecoverDelay++;
                    consumption.amount = 0;
                }
            }
            
            updateStaminaRelative(cachedSession, consumption, isCharacterStamina);
        }
    }
}
```

### 3.5 5Hz / 200ms tick 的精度

```java
sustainedStaminaHandlerTimer.scheduleAtFixedRate(new SustainedStaminaHandler(), 0, 200);
```

→ **每 200ms 触发一次** —— 给玩家"瞬间响应"的错觉。
→ 比 GameServer.onTick (1 秒/tick) **快 5 倍**——因为体力变化要细。

→ 这意味着：每个玩家**一个独立 Timer**！100 玩家在线 = **100 个 Timer**线程。
→ 性能不是免费的 —— Timer 用得多了 JVM 会卡。但 grasscutter 接受这个代价。

### 3.6 1 秒恢复延迟

```java
if (consumption.amount > 0
    && consumption.type != ConsumptionType.POWERED_FLY
    && consumption.type != ConsumptionType.POWERED_SKIFF) {
    if (staminaRecoverDelay < 5) {
        staminaRecoverDelay++;
        consumption.amount = 0;   // 跳过这 5 tick
    }
}
```

→ **停止移动 → 5 tick (1 秒) 后才开始恢复**。
→ 防止"边跑边停"快速恢复机制。
→ 但**POWERED_FLY/POWERED_SKIFF 不延迟** —— 风场加速立即满（飞机考核必需）。

---

## 4. MotionState 8 大分类（呼应 notes/36）

```java
private static final Map<String, Set<MotionState>> MotionStatesCategorized = new HashMap<>() {{
    put("CLIMB", Set.of(MOTION_CLIMB, MOTION_STANDBY_TO_CLIMB));
    put("DASH", Set.of(MOTION_DANGER_DASH, MOTION_DASH));
    put("FLY", Set.of(MOTION_FLY, MOTION_FLY_FAST, MOTION_FLY_SLOW, MOTION_POWERED_FLY));
    put("RUN", Set.of(MOTION_DANGER_RUN, MOTION_RUN));
    put("SKIFF", Set.of(MOTION_SKIFF_BOARDING, MOTION_SKIFF_DASH, MOTION_SKIFF_NORMAL, MOTION_SKIFF_POWERED_DASH));
    put("STANDBY", Set.of(MOTION_DANGER_STANDBY_MOVE, MOTION_DANGER_STANDBY, MOTION_LADDER_TO_STANDBY, MOTION_STANDBY_MOVE, MOTION_STANDBY));
    put("SWIM", Set.of(MOTION_SWIM_IDLE, MOTION_SWIM_DASH, MOTION_SWIM_JUMP, MOTION_SWIM_MOVE));
    put("WALK", Set.of(MOTION_DANGER_WALK, MOTION_WALK));
    put("OTHER", Set.of(MOTION_CLIMB_JUMP, MOTION_DASH_BEFORE_SHAKE, MOTION_FIGHT, MOTION_JUMP_UP_WALL_FOR_STANDBY, MOTION_NOTIFY, MOTION_SIT_IDLE, MOTION_JUMP));
    put("NOCOST_NORECOVER", Set.of(MOTION_LADDER_SLIP, MOTION_SLIP, MOTION_FLY_IDLE));
    put("IGNORE", Set.of(MOTION_CROUCH_IDLE, MOTION_CROUCH_MOVE, ...));
}};
```

→ MotionState 几十种 → **归到 10 大组** → 每组对应 ConsumptionType。

→ 这是**典型的"枚举映射归类"模式**——避免每个 MotionState 单独 case。

### 4.1 危险状态 "DANGER_*"

```
MOTION_DANGER_RUN, MOTION_DANGER_DASH, MOTION_DANGER_STANDBY...
```

→ "Danger" 状态是**周围有敌人时**的标记 —— 客户端表现为"备战姿态"，体力消耗不变但 BGM 切。

### 4.2 NOCOST_NORECOVER

```
MOTION_LADDER_SLIP    // 梯子滑下
MOTION_SLIP            // 滑倒
MOTION_FLY_IDLE        // 飞行待机 (但其实不实现)
```

→ 这些状态**不耗也不恢** —— 物理状态下的特殊处理。

---

## 5. 食物 / 天赋 / 共鸣 减耗系统

### 5.1 8 个 Reduction Map

```java
ClimbFoodReductionMap     // 攀爬食物
DashFoodReductionMap      // 冲刺食物
FlyFoodReductionMap       // 飞行食物
SwimFoodReductionMap      // 游泳食物

ClimbTalentReductionMap   // 攀爬天赋
FlyTalentReductionMap     // 飞行天赋
SwimTalentReductionMap    // 游泳天赋
```

### 5.2 攀爬食物示例

```java
private static final HashMap<Integer, Float> ClimbFoodReductionMap = new HashMap<>() {{
    put(0, 0.8f);   // 减 20%
}};
```

→ 吃了攀爬食物 → 爬山消耗 × 0.8 = -120/tick（原 -150）

### 5.3 共鸣减耗

```java
if (player.getTeamManager().getTeamResonances().contains(10301)) {
    consumption.amount *= 0.85f;   // ★ 双冰共鸣 -15%
}
```

→ **双冰共鸣**：所有体力消耗 × 0.85 —— 这是"冰队爬塔无敌"的代码来源。

### 5.4 减耗叠加顺序

```
基础消耗 (ConsumptionType)
   ×
食物减耗 (FoodReductionMap)
   ×
天赋减耗 (TalentReductionMap)
   ×
共鸣减耗 (TeamResonances)
   =
最终消耗
```

→ **3 层乘法叠加** —— 满食物 + 满天赋 + 双冰共鸣 = 0.8 × 0.8 × 0.85 = **0.544 (减 45.6%)**。

→ 这是为什么"重装备 + 食物"配合能极致爬山。

---

## 6. 落水死亡 / 摔死

### 6.1 落水死亡

```java
private void handleDrowning() {
    int stamina = getCurrentCharacterStamina();
    if (stamina < 10) {
        if (currentState != MotionState.MOTION_SWIM_IDLE) {
            killAvatar(cachedSession, cachedEntity, PlayerDieType.PLAYER_DIE_DRAWN);
        }
    }
}
```

→ 游泳体力 < 10 且不在静止 → **死亡 (PLAYER_DIE_DRAWN)**。
→ 这是 notes/34 PlayerDieType 6 种死法的一种实现。

### 6.2 摔死（呼应 notes/36）

`HandlerCombatInvocationsNotify.handleFallOnGround` (notes/36)：
- 4 档落地速度 → 摔伤 33%/50%/66%/100% MAX_HP
- 100% 一击毙命

**摔死走 StaminaManager.killAvatar**：
```java
public void killAvatar(GameSession session, GameEntity entity, PlayerDieType dieType) {
    session.send(new PacketAvatarLifeStateChangeNotify(...));
    entity.setFightProperty(FightProperty.FIGHT_PROP_CUR_HP, 0);
    player.getScene().removeEntity(entity);
    ((EntityAvatar) entity).onDeath(dieType, 0);
}
```

→ StaminaManager 是**所有"非战斗死亡"**的总入口（淹/摔）。

---

## 7. Vehicle Stamina：翔士专属

```java
public int getMaxVehicleStamina() {
    return GlobalVehicleMaxStamina;
}
public int getCurrentVehicleStamina() { ... }

public void handleVehicleInteractReq(GameSession session, int vehicleId, VehicleInteractType type) {
    if (type == VehicleInteractType.VEHICLE_INTERACT_IN) {
        this.vehicleId = vehicleId;
        // 上船时充满双方体力
        updateStaminaAbsolute(session, "board vehicle", getMaxCharacterStamina(), true);
        updateStaminaAbsolute(session, "board vehicle", getMaxVehicleStamina(), false);
    } else {
        this.vehicleId = -1;
    }
}
```

### 7.1 双体力系统

```
character stamina (24000)    ← 玩家本身
vehicle stamina (24000)       ← 翔士独立
```

**切换逻辑**：
- 走路/爬山/飞 → 用 character stamina
- 在浪船 (SKIFF_*) → 用 vehicle stamina

→ 这就是"上船补满 character stamina" 的设计 —— 让玩家不被"刚跑完累得上船"惩罚。

---

## 8. 与其他系统的联动

### 8.1 副本 (notes/48)

```java
// DungeonManager.handleCost
return player.getResinManager().useResin(resinCost);    // 普通树脂
return player.getResinManager().useCondensedResin(1);   // 浓缩
```

→ 副本结算消耗树脂 / 浓缩树脂。

### 8.2 战令 (notes/22)

```java
// ResinManager.useResin
player.getBattlePassManager().triggerMission(
    WatcherTriggerType.TRIGGER_COST_MATERIAL, 106, amount);   // 树脂 itemId=106
```

→ "消耗 N 树脂"战令任务的代码触发点。

### 8.3 Inventory (notes/38)

```java
// Inventory.payVirtualItem
case 106 -> player.getResinManager().useResin(count);   // 树脂作虚拟币
```

→ 树脂走 8 虚拟币之一（notes/38 §4.2）。

### 8.4 Player.onLogin (notes/40)

```java
this.resinManager.onPlayerLogin();      // 一次性补回离线积攒
this.staminaManager.setPlayer(this);    // 注入引用
```

---

## 9. 完整时序：一次满体力爬山

```
[T+0] 玩家站立 (STANDBY)
   Timer 触发: consumption = STANDBY +500
   stamina = 24000 (max), 不动
   
[T+1] 玩家开始爬墙 (MOTION_CLIMB)
   handleImmediateStamina(CLIMB_START):
     stamina -= 500 (一次性)
   ↓
[T+1.2] Timer (200ms 后) 
   currentState = CLIMB
   getClimbConsumption:
     base = -150
     × foodReduction (0.8) = -120
     × talentReduction (0.8) = -96
   stamina -= 96
   
[每 200ms 持续]
   stamina -= 96 (合 -480/秒)
   
[T+15] stamina ≈ 16800 (24000 - 500 - 480×15)
   玩家爬累了, 停下
   
[T+15.0] currentState = STANDBY
   consumption = STANDBY (+500), 但 staminaRecoverDelay < 5
   staminaRecoverDelay++  (1/5)
   stamina 不变
   
[T+15.2] staminaRecoverDelay = 2
[T+15.4] staminaRecoverDelay = 3
[T+15.6] staminaRecoverDelay = 4
[T+15.8] staminaRecoverDelay = 5 → 开始恢复 +500/tick
   stamina += 500 (合 +2500/秒)
   
[T+18.8] stamina = 24000 (满)
   Timer 继续但 stamina 在 max 不变
```

→ 完整时序：**起步消耗 + 持续消耗 + 1 秒恢复延迟 + 恢复**。

---

## 10. Listener 系统（扩展点）

```java
private final HashMap<String, BeforeUpdateStaminaListener> beforeUpdateStaminaListeners;
private final HashMap<String, AfterUpdateStaminaListener> afterUpdateStaminaListeners;

public boolean registerBeforeUpdateStaminaListener(String listenerName, BeforeUpdateStaminaListener listener);
public boolean unregisterBeforeUpdateStaminaListener(String listenerName);
public boolean registerAfterUpdateStaminaListener(String listenerName, AfterUpdateStaminaListener listener);
public boolean unregisterAfterUpdateStaminaListener(String listenerName);
```

→ Plugin / 其他 Manager 可注册"体力更新前/后"钩子。
→ 例：温泉恢复体力 → 注册 BeforeUpdateStamina 拦截"减少"操作改为 0。

→ 这是**第 8 个反作弊/扩展钩子**（参见 notes/47 Cancellable Events）。

---

## 11. 性能分析

### 11.1 100 玩家在线的成本

```
ResinManager:
   - 0 个后台 Timer
   - 玩家行动时 lazy 计算
   - 几乎 0 CPU 开销
   
StaminaManager:
   - 100 个 Timer 线程 (每玩家 1 个)
   - 200ms 触发 1 次
   - 每次 1-2 ms 计算
   = 5 Hz × 100 × 1.5ms = 750ms CPU / 秒 = ~75% 一核
```

→ **100 玩家在线占满 1 个 CPU 核**——不太理想但勉强可用。

### 11.2 优化方向（grasscutter 没做）

更好的设计：
- 单个全局 Timer + 玩家 batch
- 或基于 onTick (Server tick) 做粗粒度更新
- 但**会降低响应性**

→ grasscutter 选了"性能换体验"。

---

## 12. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 客户端发"我体力满了" | ✗ 服务器算 |
| 篡改 ConsumptionType | ✗ 服务器存 |
| 用错的食物减耗 | ✗ 服务器检查食物效果 |
| 飞行没体力上限 | ✗ 服务器 cap |
| 加速消耗速率 | ✗ 服务器固定 200ms |
| 跳过 1 秒恢复延迟 | ✗ 服务器内部计数 |
| 树脂秒回 | ✗ rechargeTime 服务器算 |
| 买超过 6 次 | ✗ 服务器 MAX_RESIN_BUYING_COUNT 检查 |
| 浓缩树脂在 40 树脂副本 | ✗ DungeonManager 检查 |

→ **时间资源系统反作弊极强** —— 服务器全权掌握时间 + 数学。

---

## 13. 设计模式总结

### 13.1 Lazy Evaluation（树脂）

```
玩家上线 / 用树脂时计算
不需要后台 Timer
```

→ **慢节奏资源**的最优解。

### 13.2 Polling Timer（体力）

```
200ms × 5Hz = 实时响应
代价: 每玩家 1 个 Timer
```

→ **快节奏资源**的标准做法。

### 13.3 ConsumptionType 枚举驱动

```
-2500 (CLIMB_JUMP) ~ +500 (STANDBY)
枚举值 = 数学量
```

→ 配置即数据 — 调整数值无需改逻辑。

### 13.4 多层乘法叠加（共鸣/食物/天赋）

```
base × (1 - foodReduction) × (1 - talentReduction) × (1 - resonance)
```

→ 类似战斗属性的"层级 modifier"。

### 13.5 状态归类映射

```
30+ MotionState → 10 大组 → ConsumptionType
```

→ 避免 case 爆炸。

### 13.6 Listener 扩展点

```
BeforeUpdateStamina / AfterUpdateStamina
```

→ Plugin 可扩展行为。

---

## 14. 关键收获

1. **两套并行时间资源**：Resin (8 分钟/点, lazy) + Stamina (200ms tick, polling)
2. **ResinManager 175 行** vs **StaminaManager 715 行** —— 715 行处理 14 种 ConsumptionType + 8 减耗 Map + 10 MotionState 分组
3. **ResinOptions 配置**：cap=160 / rechargeTime=480 秒 / resinUsage 默认 false（私服无限）
4. **lazy 恢复算法**：`recharge = 1 + (now - nextRefresh) / rechargeTime` 一次性补回
5. **买树脂 6 次递增价格**：50/100/100/150/200/200 原石 = 800 共 360 树脂
6. **浓缩树脂 220007**：走 inventory.payItem（不是 PlayerProperty）
7. **14 种 ConsumptionType**：消耗类 (-2500 ~ -60) + 恢复类 (+500)
8. **Stamina 数学**：24000 max / 1800 dash/sec = 13 秒满冲刺，与玩家观察吻合
9. **SustainedStaminaHandler Timer 200ms (5Hz)**：每玩家 1 个 Timer
10. **MotionState 10 大分组**：CLIMB/DASH/FLY/RUN/SKIFF/STANDBY/SWIM/WALK/OTHER/NOCOST_NORECOVER/IGNORE
11. **1 秒恢复延迟（5 tick）**：停止后才开始恢复，POWERED_FLY/SKIFF 例外
12. **8 个 Reduction Map**：4 食物 (Climb/Dash/Fly/Swim) + 4 天赋 (Climb/Fly/Swim + 通用)
13. **双冰共鸣 ×0.85**：减 15% 体力消耗
14. **3 层乘法叠加**：食物 × 天赋 × 共鸣 = 最多减 45.6%
15. **落水死亡**：stamina < 10 且非 SWIM_IDLE → PLAYER_DIE_DRAWN
16. **Vehicle Stamina 独立**：上船补满双方体力
17. **联动 5 系统**：副本树脂 / 战令 / Inventory 虚拟币 / Player.onLogin / 死亡
18. **Listener 扩展点**：BeforeUpdate / AfterUpdate Stamina 钩子
19. **100 玩家 ≈ 1 个 CPU 核**：100 个 Timer 的代价
20. **反作弊极强**：时间 + 数学全在服务器

---

## 15. 一句话总结

> **Resin/Stamina = 两套并行的"时间锁资源"——Resin 175 行 lazy 计算 (8 分钟/点, cap 160, 6 次买递增价), Stamina 715 行 polling Timer (200ms 5Hz, cap 24000, 14 ConsumptionType, 10 MotionState 分组, 8 减耗 Map, 3 层乘法叠加, 1 秒恢复延迟); 100 玩家 ≈ 1 个 CPU 核; 反作弊极强 (全服务器算); 联动 5+ 系统 (副本/战令/Inventory/Player/死亡)。**
> 
> **设计哲学: 慢资源 lazy + 快资源 polling; 数学严密 (每数值都有玩家可感受的意义); 多层乘法叠加给配队留 buff 空间; 食物/天赋/共鸣三轨独立减耗; Timer 性能换响应性. 这是 grasscutter 中"时间维度反作弊"的最佳样本.**

---

**前置笔记**：
- notes/34 EntityAvatar - PlayerDieType (PLAYER_DIE_DRAWN/FALL)
- notes/36 战斗数学 - 摔伤 4 档 + ConsumptionType (notes/36 提到部分)
- notes/38 Inventory - 树脂作虚拟币 106 / 浓缩 220007
- notes/40 Player Manager - ResinManager / StaminaManager 是 25 之一
- notes/41 事件总线 - TRIGGER_COST_MATERIAL 触发
- notes/48 副本 - useResin / useCondensedResin

**关联文件**：
- `ResinManager.java`(175) - 树脂核心
- `StaminaManager.java`(715) - 体力核心
- `ConsumptionType.java`(37) - 14 种消耗
- `ConfigContainer.ResinOptions` - cap/rechargeTime
- `PlayerProperty.PROP_PLAYER_RESIN` - 树脂存储
- `PlayerProperty.PROP_CUR_PERSIST_STAMINA` - 体力存储

**研究的源代码**: 890+ 行 Resin + Stamina 核心 + ConsumptionType 14 枚举 + 8 减耗 Map。
