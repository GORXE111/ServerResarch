# Dungeon 副本运行时深度剖析

> 第 48 篇：notes/19 讲了副本的"设计图"——这一篇填补**运行时引擎**：DungeonManager 322 行 + 8 通关条件 + 36 副本类型 + 17 ChallengeFactoryHandler + 8 Trigger 共 **72 个文件**支撑的 5 系统交汇点发动机。

---

## 0. 为什么这一篇重要

前 47 篇里 Dungeon 反复出现但 runtime 没专门挖：
- notes/19 副本系统：讲了"五系统交汇 + 14 ChallengeFactory"设计
- notes/32 怪物：onDeath 触发 `triggerDungeonEvent` (3 种)
- notes/33 Gadget：`scene.dungeonManager` 引用
- notes/35 Scene/World：副本不 callDrop
- notes/39 Drop 系统：`SCENE_DUNGEON` 跳过掉落

但**进入副本到结算的完整流水线**？挑战引擎怎么跑？通关条件怎么组合？这一篇打开。

---

## 1. Dungeon 体系全图

```
┌────────────────────────────────────────────────────────────────────┐
│  DungeonSystem (BaseGameSystem, 169 行)                              │
│  - enterDungeon / exitDungeon / restartDungeon                       │
│  - 反射注册 DungeonPassCondition handlers (@DungeonValue)            │
└────────────────────────────────┬──────────────────────────────────┘
                                 │ 创建
                                 ↓
┌────────────────────────────────────────────────────────────────────┐
│  DungeonManager (per Scene, 322 行)                                  │
│  - dungeonData + passConfigData                                      │
│  - finishedConditions[] 进度数组                                      │
│  - rewardedPlayers Set                                                │
│  - activeDungeonWayPoints (副本内传送点)                              │
│  - triggerEvent / finishDungeon / failDungeon                        │
└────────────────────────────────┬──────────────────────────────────┘
                                 │ 引用 (per Scene)
                                 ↓
┌────────────────────────────────────────────────────────────────────┐
│  WorldChallenge (per Challenge, scene.challenge)                     │
│  - ChallengeFactoryHandler 选择具体实现                                │
│  - 8 种 ChallengeTrigger 组合                                          │
│  - 17 个 Handler 覆盖 25+ ChallengeType                                │
└────────────────────────────────────────────────────────────────────┘
```

### 1.1 文件量

```
emu/grasscutter/game/dungeons/ 目录:
  72 个 Java 文件
  包括:
    - DungeonManager.java (322)
    - DungeonSystem.java (169)
    - 7 个 enum
    - 16 个 ChallengeFactoryHandler
    - 8 个 ChallengeTrigger
    - 14 个 BaseCondition (pass_condition)
    - SettleListener / DungeonResult / Entry 等
```

→ **副本系统是 grasscutter 中第 3 大子系统**（仅次于 Quest 1458 行 + Scene 1165 行）。

---

## 2. DungeonSystem：全局副本调度（BaseGameSystem）

`DungeonSystem.java`（169 行）—— 14 GameSystem 之一（notes/46）。

### 2.1 反射注册条件 Handler

```java
public DungeonSystem(GameServer server) {
    super(server);
    this.passCondHandlers = new Int2ObjectOpenHashMap<>();
    registerHandlers();
}

public void registerHandlers() {
    registerHandlers(this.passCondHandlers, BaseCondition.class.getPackageName(), DungeonBaseHandler.class);
}

public <T> void registerPacketHandler(Int2ObjectMap<T> map, Class<? extends T> handlerClass) {
    Optional.ofNullable(handlerClass.getAnnotation(DungeonValue.class))
        .map(DungeonValue::value)
        .ifPresent(value -> {
            map.put(value.ordinal(), handlerClass.getDeclaredConstructor().newInstance());
        });
}
```

→ **第 14 次"注解+反射+自动注册"模式** —— `@DungeonValue(DUNGEON_COND_XXX)` 标注 BaseCondition 子类。

### 2.2 enterDungeon 完整流程

```java
public boolean enterDungeon(Player player, int pointId, int dungeonId, DungeonSettleListener listener) {
    val data = GameData.getDungeonDataMap().get(dungeonId);
    if (data == null) return false;
    
    // 1. 找入口点
    final int realPointId = Optional.ofNullable(GameData.getDungeonEntriesMap().get(dungeonId))
        .map(DungeonEntries::getEntryPoint).map(PointData::getId).orElse(pointId);
    
    // 2. 记录退出信息（用于结算后传回原场景）
    player.getScene().setPrevScene(player.getSceneId());
    player.getDungeonExitInfo().setAll(player, dungeonId, realPointId);
    
    // 3. 切场景到副本场景
    if (player.getWorld().transferPlayerToScene(player, data.getSceneId(), data)) {
        // 4. 创建 DungeonManager + 注册结算监听器
        player.getScene().setDungeonManager(new DungeonManager(player.getScene(), data));
        player.getScene().addDungeonSettleObserver(listener);
    }
    
    player.getScene().setPrevScenePoint(realPointId);
    return true;
}
```

**4 步**：找入口 → 记录退出信息 → 切场景 → 创建 DungeonManager。

---

## 3. DungeonManager：副本核心运行时（322 行）

### 3.1 字段

```java
public class DungeonManager {
    @Getter private final Scene scene;                 // 反向引用
    @Getter private final DungeonData dungeonData;      // 配表数据
    @Getter private final DungeonPassConfigData passConfigData;   // 通关条件配置
    
    @Getter private final int[] finishedConditions;     // ★ 通关进度数组 (per cond)
    @Getter private final IntSet rewardedPlayers;       // 已领奖玩家 (防重领)
    private final Set<Integer> activeDungeonWayPoints;  // 副本内传送点
    private boolean ended;                              // 是否已结束
    private int newestWayPoint;                         // 最近激活的传送点
    @Getter private int startSceneTime;
    @Getter @Setter private int delayExitTaskId = -1;   // 退出延迟任务 ID
}
```

→ **8 个核心字段** —— 极简。

### 3.2 finishedConditions 数组的妙用

```java
this.finishedConditions = new int[this.passConfigData.getConds().size()];
```

→ 长度 = 通关条件数（通常 1-3 个）。每个槽位 0 或 1，记录某条件是否满足。

→ 后续 `LogicType.calculate(logicType, finishedConditions)` 用 AND/OR 组合 —— 类似 Quest 的 cond/content 设计。

---

## 4. DungeonPassConditionType（8 种通关条件）

```java
public enum DungeonPassConditionType implements IntValueEnum {
    DUNGEON_COND_NONE(0),
    DUNGEON_COND_KILL_MONSTER(3),            // ★ 杀指定怪
    DUNGEON_COND_KILL_GROUP_MONSTER(5),       // ★ 清空指定组怪
    DUNGEON_COND_KILL_TYPE_MONSTER(7),        // 杀指定类型怪
    DUNGEON_COND_FINISH_QUEST(9),             // 完成任务
    DUNGEON_COND_KILL_MONSTER_COUNT(11),      // 杀 N 个 (TODO)
    DUNGEON_COND_IN_TIME(13),                  // 限时
    DUNGEON_COND_FINISH_CHALLENGE(14),         // ★ 完成挑战
    DUNGEON_COND_END_MULTISTAGE_PLAY(15)       // 多阶段结束
}
```

→ **8 种通关条件**。注释 `// Missing triggers and tracking` / `// TODO` 表明 grasscutter 部分未完整实现。

### 4.1 triggerEvent 路由

```java
public void triggerEvent(DungeonPassConditionType conditionType, int... params) {
    if (this.ended) return;
    
    this.passConfigData.getConds().stream()
        .filter(cond -> cond.getCondType() == conditionType)
        .filter(cond -> getScene().getWorld().getServer().getDungeonSystem()
            .triggerCondition(cond, params))   // ★ 委托给 DungeonSystem
        .forEach(cond -> this.finishedConditions[this.passConfigData.getConds().indexOf(cond)] = 1);
    
    if (isFinishedSuccessfully()) finishDungeon();
}

public boolean isFinishedSuccessfully() {
    return LogicType.calculate(this.passConfigData.getLogicType(), this.finishedConditions);
    //               ↑ AND/OR 组合
}
```

→ 事件**先过 type 过滤 + DungeonSystem 判定** → 全部满足才 `finishDungeon`。

### 4.2 谁触发 triggerEvent

参见 notes/35 Scene.java + notes/32 EntityMonster.onDeath：
```java
// EntityMonster.onDeath
scene.triggerDungeonEvent(DUNGEON_COND_KILL_GROUP_MONSTER, this.getGroupId());
scene.triggerDungeonEvent(DUNGEON_COND_KILL_TYPE_MONSTER, this.getMonsterData().getType().getValue());
scene.triggerDungeonEvent(DUNGEON_COND_KILL_MONSTER, this.getMonsterId());

// Scene.killEntity
triggerDungeonEvent(DUNGEON_COND_KILL_MONSTER_COUNT, ++this.killedMonsterCount);
```

→ 怪物死亡是**最主要的通关触发器** —— 但还有 quest 完成、挑战完成等其他来源。

---

## 5. DungeonType（36 种副本类型）

```java
DUNGEON_NONE(false),                          // 占位
DUNGEON_PLOT(true),                            // 剧情副本
DUNGEON_FIGHT(true),                           // 战斗副本
DUNGEON_DAILY_FIGHT(false),                    // 每日委托副本
DUNGEON_WEEKLY_FIGHT(true),                    // 周本
DUNGEON_TOWER(false),                          // 深境螺旋
DUNGEON_BOSS(true),                            // boss 战
DUNGEON_ACTIVITY(false),                       // 活动
DUNGEON_EFFIGY(false),                          // 突破任务
DUNGEON_ELEMENT_CHALLENGE(true),                // 元素挑战
DUNGEON_THEATRE_MECHANICUS(false),              // 机关棋谭
DUNGEON_FLEUR_FAIR(false),                      // 风花节
DUNGEON_CHANNELLER_SLAB_LOOP(false),            // 须弥金字塔活动
DUNGEON_CHANNELLER_SLAB_ONE_OFF(false),
DUNGEON_BLITZ_RUSH(true),                       // 闪雷
DUNGEON_CHESS(false),                            // 棋类活动
DUNGEON_SUMO_COMBAT(false),                     // 相扑
DUNGEON_ROGUELIKE(false),                       // 幻想真境剧诗
DUNGEON_HACHI(false),                            // 八重宝箧
DUNGEON_POTION(false),
DUNGEON_MINI_ELDRITCH(false),
DUNGEON_UGC(false),
DUNGEON_GCG(false),                              // 七圣召唤
DUNGEON_CRYSTAL_LINK(false),
DUNGEON_IRODORI_CHESS(false),                    // 彩之祭典棋盘
DUNGEON_ROGUE_DIARY(false),
DUNGEON_DREAMLAND(false),
DUNGEON_SUMMER_V2(true),                         // 海岛 2.8
DUNGEON_MUQADAS_POTION(false),
DUNGEON_INSTABLE_SPRAY(false),
DUNGEON_WIND_FIELD(false),
DUNGEON_BIGWORLD_MIRROR(false),
DUNGEON_FUNGUS_FIGHTER_TRAINING(false),
DUNGEON_FUNGUS_FIGHTER_PLOT(false),
DUNGEON_EFFIGY_CHALLENGE_V2(false),
DUNGEON_CHAR_AMUSEMENT(false);
```

### 5.1 36 种 + countsToBattlepass 标记

每个 DungeonType 有 boolean `countsToBattlepass`：
- ✓ true（**只 8 个**）: PLOT / FIGHT / WEEKLY_FIGHT / BOSS / ELEMENT_CHALLENGE / BLITZ_RUSH / SUMMER_V2
- ✗ false: 其他活动副本不计战令

→ 战令任务 `TRIGGER_FINISH_DUNGEON` **只对核心副本类型生效** —— 避免活动副本刷战令。

### 5.2 活动副本占主流

观察 36 种：**正常游戏副本只 8 类**（PLOT/FIGHT/DAILY/WEEKLY/TOWER/BOSS/EFFIGY/ELEMENT），**剩下 28 种全是活动**。

→ 这就是为什么 grasscutter 的副本枚举**每个版本都增长** —— 米哈游不停加新玩法。

---

## 6. WorldChallenge + 17 ChallengeFactoryHandler

### 6.1 17 个 Handler 映射 25+ ChallengeType

```java
@ChallengeTypeValue(type = CHALLENGE_DIE_LESS_IN_TIME)
public class DieLessTimeChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = {CHALLENGE_ELEMENT_REACTION_COUNT, 
                              CHALLENGE_SWIRL_ELEMENT_REACTION_COUNT})
public class ElementReactionCountChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_FATHER_SUCC_IN_TIME)
public class FatherSuccessTimeChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_FREEZE_ENEMY_IN_TIME)
public class FreezeEnemyTimeChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_KILL_COUNT)
public class KillCountChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_KILL_COUNT_FROZEN_LESS)
public class KillCountFrozenLessChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_KILL_COUNT_GUARD_HP)
public class KillCountGuardChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_GUARD_HP)
public class KillCountGuardTimeChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = {CHALLENGE_KILL_COUNT_IN_TIME, CHALLENGE_KILL_COUNT_FAST})
public class KillCountTimeOrFastChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_KILL_MONSTER_IN_TIME)
public class KillMonsterTimeChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = {CHALLENGE_MONSTER_DAMAGE_COUNT, CHALLENGE_SHEILD_ABSORB_DAMAGE_COUNT})
public class MonsterOrShieldDamageCountChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_SURVIVE)
public class SurviveChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_TIME_FLY)
public class TimeFlyChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_TRIGGER2_AVOID_TRIGGER1)
public class Trigger2Trigger1ChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_TRIGGER_COUNT)
public class TriggerCountChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_TRIGGER_IN_TIME)
public class TriggerTimeChallengeFactoryHandler { ... }

@ChallengeTypeValue(type = CHALLENGE_TRIGGER_IN_TIME_FLY)
public class TriggerTimeFlyChallengeFactoryHandler { ... }
```

→ **17 Handler 覆盖 25+ ChallengeType**（某些 Handler 处理多个 type）。

### 6.2 挑战类型分类

| 分类 | Handler 数 | 例子 |
|---|---|---|
| **击杀类** | 6 | KillCount / KillCountInTime / KillMonsterInTime / KillCountFrozen / KillGuard 等 |
| **守护类** | 2 | KillCountGuardHP / GuardHP |
| **触发类** | 4 | TriggerCount / TriggerInTime / TriggerTimeFly / Trigger2AvoidTrigger1 |
| **生存类** | 1 | Survive (活到时间结束) |
| **特殊类** | 4 | DieLess / ElementReactionCount / FatherSuccTime / FreezeEnemy |
| **时间类** | 2 | TimeFly / MonsterDamageCount |

→ **17 种基础挑战**组合出**所有副本玩法** —— 数据驱动哲学的又一例。

### 6.3 8 个 ChallengeTrigger

```
trigger/
├── ChallengeTrigger.java           ← 抽象基类
├── DamageCountTrigger.java         ← 伤害累计
├── ElementReactionTrigger.java     ← 元素反应触发
├── FatherTrigger.java              ← 父挑战触发 (子挑战通知)
├── GuardTrigger.java               ← 守护对象的 HP
├── KillMonsterTrigger.java         ← 击杀计数
├── TimeTrigger.java                ← 时间流逝
└── TriggerGroupTriggerTrigger.java ← group trigger 触发
```

→ Handler **组合 Trigger 实现具体挑战**：
- `KillCountInTime` = KillMonsterTrigger + TimeTrigger
- `DamageCount` = DamageCountTrigger
- `Trigger2AvoidTrigger1` = 2 个 TriggerGroupTriggerTrigger 组合

→ 类似"积木组合"。

---

## 7. startDungeon / finishDungeon / failDungeon / quitDungeon

```java
public void startDungeon() {
    this.startSceneTime = this.scene.getSceneTimeSeconds();
    this.scene.getPlayers().forEach(p -> {
        p.getQuestManager().queueEvent(QuestContent.QUEST_CONTENT_ENTER_DUNGEON, this.dungeonData.getId());
        applyTrialTeam(p);   // ★ 应用试用角色 (剧情副本/元素挑战)
    });
}

public void finishDungeon() {
    notifyEndDungeon(true);
    endDungeon(BaseDungeonResult.DungeonEndReason.COMPLETED);
}

public void notifyEndDungeon(boolean successfully) {
    this.scene.getPlayers().forEach(p -> {
        // 1. Quest 触发器
        p.getQuestManager().queueEvent(
            successfully ? QUEST_CONTENT_FINISH_DUNGEON : QUEST_CONTENT_FAIL_DUNGEON,
            this.dungeonData.getId());
        
        // 2. 战令任务
        if (this.dungeonData.getType().isCountsToBattlepass() && successfully) {
            p.getBattlePassManager().triggerMission(WatcherTriggerType.TRIGGER_FINISH_DUNGEON);
        }
        
        // 3. 跳转下一副本 (链式副本)
        if (dungeonData.getPassJumpDungeon() > 0) {
            p.getServer().getDungeonSystem().enterDungeon(p, 0, dungeonData.getPassJumpDungeon());
        }
    });
    
    // 4. Lua 事件
    this.scene.getScriptManager().callEvent(
        new ScriptArgs(0, EventType.EVENT_DUNGEON_SETTLE, successfully ? 1 : 0));
}

public void quitDungeon() {
    notifyEndDungeon(false);
    endDungeon(BaseDungeonResult.DungeonEndReason.QUIT);
}

public void failDungeon() {
    notifyEndDungeon(false);
    endDungeon(BaseDungeonResult.DungeonEndReason.FAILED);
}
```

### 7.1 3 种结束原因

```java
public enum DungeonEndReason {
    COMPLETED,   // 通关
    FAILED,      // 失败 (挑战未完成)
    QUIT         // 主动退出
}
```

### 7.2 4 个 callback 链

```
[副本通关]
finishDungeon
   ↓
notifyEndDungeon(true):
   - QUEST_CONTENT_FINISH_DUNGEON (任务)
   - TRIGGER_FINISH_DUNGEON (战令, 仅 BP 类副本)
   - 链式副本跳转
   - EVENT_DUNGEON_SETTLE Lua 事件
endDungeon:
   - SettleListener.onDungeonSettle 触发
   - ended = true
```

### 7.3 链式副本（passJumpDungeon）

```java
if (dungeonData.getPassJumpDungeon() > 0) {
    p.getServer().getDungeonSystem().enterDungeon(p, 0, dungeonData.getPassJumpDungeon());
}
```

→ "**通关后自动进入下一副本**" —— 剧情副本常用（连续 3 关 boss 战之类）。

---

## 8. exitDungeon：智能离开

```java
public void exitDungeon(Player player, boolean isQuitImmediately) {
    val scene = player.getScene();
    if (scene == null || scene.getSceneType() != SceneType.SCENE_DUNGEON) return;
    
    val dungeonManager = scene.getDungeonManager();
    val dungeonData = dungeonManager.getDungeonData();
    
    int delayExitTime = -1;
    
    if (dungeonData != null && !dungeonManager.isFinishedSuccessfully() 
        && dungeonManager.getDelayExitTaskId() < 0) {
        // 失败的挑战
        val challenge = Optional.ofNullable(scene.getChallenge()).filter(WorldChallenge::inProgress);
        challenge.ifPresent(WorldChallenge::fail);
        
        if (challenge.isPresent()) {
            delayExitTime = dungeonData.getFailSettleCountdownTime();  // 失败结算延迟
            dungeonManager.failDungeon();
        } else {
            delayExitTime = dungeonData.getQuitSettleCountdownTime();   // 退出结算延迟
            dungeonManager.quitDungeon();
        }
    }
    
    // 深境螺旋特殊处理
    if (dungeonData.getType() == DungeonType.DUNGEON_TOWER) {
        player.getTowerManager().removeCurrentLevelBuff();
        player.getTowerManager().clearTeamOnExit();
        isQuitImmediately = true;
    }
    
    // 取消已有延迟任务
    if (dungeonManager.getDelayExitTaskId() > 0) {
        Grasscutter.getGameServer().getScheduler().cancelTask(...);
    }
    
    // 真正的传送任务
    final Runnable transferTask = () -> {
        scene.setPrevScene(scene.getId());
        player.getWorld().transferPlayerToScene(player,
            exitLoc.getSceneId(),
            exitLoc.getPos(),
            exitLoc.getRot());
    };
    
    // 立即/延迟执行
    if (isQuitImmediately) {
        transferTask.run();
    } else {
        int delayTaskId = Grasscutter.getGameServer().getScheduler()
            .scheduleDelayedTask(transferTask, delayExitTime);
        dungeonManager.setDelayExitTaskId(delayTaskId);
    }
}
```

### 8.1 退出延迟的设计

```
[副本失败]
   challenge.fail
   ↓
   failDungeon → notifyEndDungeon(false)
   ↓
   delayExitTime = dungeonData.getFailSettleCountdownTime()   ← 比如 15 秒
   ↓
   15 秒内玩家可看结算 / 重试 / 退出
   ↓
   15 秒后自动传回大世界
```

→ "**结算延迟**" 是常见游戏设计 —— 失败后让玩家看到统计，不立即赶走。

### 8.2 深境螺旋立即退

```java
if (dungeonData.getType() == DungeonType.DUNGEON_TOWER) {
    isQuitImmediately = true;
}
```

→ 深境螺旋的退出不延迟 —— 因为有自己的结算 UI。

---

## 9. getStatueDrops：领奖流程

```java
public boolean getStatueDrops(Player player, boolean useCondensed, int groupId) {
    // 1. 资格检查
    if (!isFinishedSuccessfully() || !hasRewards() || hasPlayerClaimedRewards(player))
        return false;
    
    // 2. 扣树脂
    if (!handleCost(player, useCondensed))
        return false;
    
    // 3. 滚奖励
    val rewards = rollRewards(useCondensed);
    
    // 4. 加入背包 + 通知
    player.getInventory().addItems(rewards, ActionReason.DungeonStatueDrop);
    player.sendPacket(new PacketGadgetAutoPickDropInfoNotify(rewards));
    
    // 5. 标记已领
    this.rewardedPlayers.add(player.getUid());
    
    // 6. Lua 通知
    this.scene.getScriptManager().callEvent(
        new ScriptArgs(groupId, EventType.EVENT_DUNGEON_REWARD_GET));
    
    // 7. 更新副本入口信息（次数等）
    player.getDungeonEntryManager().updateDungeonEntryInfo(this.dungeonData);
    
    return true;
}
```

### 9.1 树脂消耗（handleCost）

```java
public boolean handleCost(Player player, boolean useCondensed) {
    final int resinCost = this.dungeonData.getStatueCostCount() != 0 ? 
        this.dungeonData.getStatueCostCount() : 20;
    
    if (useCondensed) {
        if (resinCost != 20) return false;  // 浓缩树脂只能用在 20 树脂副本
        return player.getResinManager().useCondensedResin(1);
    } else if (this.dungeonData.getStatueCostID() == 106) {  // 106 = 体力
        return player.getResinManager().useResin(resinCost);
    }
    return true;
}
```

**3 种消耗方式**：
- 普通树脂 (20/40/60)
- **浓缩树脂 (1 个 = 2 次副本) —— 只能 20 树脂副本**
- 其他通货（少数活动副本）

### 9.2 rollRewards：奖励随机

```java
private List<GameItem> rollRewards(boolean useCondensed) {
    val rewards = new ArrayList<GameItem>();
    final int dungeonId = this.dungeonData.getId();
    
    // 优先用 DungeonDropData (per dungeon)
    if (GameData.getDungeonDropDataMap().containsKey(dungeonId)) {
        val dropEntries = GameData.getDungeonDropDataMap().get(dungeonId);
        
        for (val entry : dropEntries) {
            // 数量随机
            int amount = Utils.drawRandomListElement(candidateAmounts, entry.getProbabilities());
            
            // ★ 浓缩树脂双倍
            if (useCondensed) {
                amount += Utils.drawRandomListElement(candidateAmounts, entry.getProbabilities());
            }
            
            // ★ 联机双倍
            if (entry.isMpDouble() && this.getScene().getPlayerCount() > 1) {
                amount *= 2;
            }
            
            // 单/多物品决策
            if (entry.getItems().size() == 1) {
                rewards.add(new GameItem(entry.getItems().get(0), amount));   // 单物品直接 stack
            } else {
                for (int i = 0; i < amount; i++) {
                    int itemId = Utils.drawRandomListElement(entry.getItems(), entry.getItemProbabilities());
                    rewards.add(new GameItem(itemId, 1));   // 多物品逐个 roll
                }
            }
        }
    }
    // 否则 fallback 用 PreviewData
    else {
        Arrays.stream(this.dungeonData.getRewardPreviewData().getPreviewItems())
            .map(param -> new GameItem(param.getId(), Math.max(param.getCount(), 1)))
            .forEach(rewards::add);
    }
    
    return rewards;
}
```

### 9.3 浓缩树脂 + 联机的双倍机制

```java
if (useCondensed) amount += ...;          // ★ 浓缩树脂多滚一次
if (entry.isMpDouble() && playerCount > 1) amount *= 2;   // ★ 联机 ×2
```

**两个倍数可叠加** —— 浓缩树脂 + 联机 4 人 = 单倍 × 2 × 2 = **4 倍奖励**（如果 entry.mpDouble=true）。

→ 这就是"组队浓缩刷讨伐"高效的代码来源。

---

## 10. activateRespawnPoint：副本内传送点

```java
public boolean activateRespawnPoint(int pointId) {
    val respawnPoint = GameData.getScenePointEntryById(this.scene.getId(), pointId);
    if (respawnPoint == null) return false;
    
    this.scene.broadcastPacket(
        new PacketDungeonWayPointNotify(this.activeDungeonWayPoints.add(pointId), this.activeDungeonWayPoints));
    this.newestWayPoint = pointId;
    return true;
}

@Nullable
public Position getRespawnLocation() {
    if (newestWayPoint == 0) return null;
    return GameData.getScenePointEntryById(this.scene.getId(), this.newestWayPoint)
        .getPointData().getTransPosWithFallback();
}
```

### 10.1 副本内 waypoint 机制

```
[玩家进入副本]
   起始点 = 副本入口
   
[玩家激活传送点 A]
   activateRespawnPoint(A) → newestWayPoint = A
   
[玩家死亡 (notes/35)]
   Scene.respawnPlayer → 传送到 newestWayPoint = A
   
[激活传送点 B]
   newestWayPoint = B
   
[再死]
   重生回 B
```

→ "**死亡时重生最近的 waypoint**" —— 这就是为什么"副本里死了不会回起点"。

---

## 11. applyTrialTeam：试用角色

```java
private void applyTrialTeam(Player player) {
    if (this.dungeonData == null) return;
    
    switch (this.dungeonData.getType()) {
        // case DUNGEON_PLOT 在 quest exec 中处理
        
        case DUNGEON_ACTIVITY -> {
            switch (this.dungeonData.getPlayType()) {
                case DUNGEON_PLAY_TYPE_TRIAL_AVATAR ->
                    // 试用活动: 加载所有试用角色
                    player.getActivityManager().getActivityHandlerAs(...)
                        .map(TrialAvatarActivityHandler::getBattleAvatarsList)
                        .ifPresent(battleAvatars -> player.addTrialAvatarsForDungeon(
                            battleAvatars, GrantReason.GRANT_BY_TRIAL_AVATAR_ACTIVITY));
                
                case DUNGEON_PLAY_TYPE_MIST_TRIAL -> {}  // TODO
            }
        }
        
        case DUNGEON_ELEMENT_CHALLENGE ->
            // 元素挑战: 用指定试用角色
            Optional.ofNullable(GameData.getDungeonElementChallengeDataMap().get(dungeonId))
                .map(DungeonElementChallengeData::getTrialAvatarId)
                .ifPresent(trialAvatarId -> player.addTrialAvatarsForDungeon(
                    trialAvatarId, GrantReason.GRANT_BY_DUNGEON_ELEMENT_CHALLENGE));
    }
    
    if (player.getTeamManager().isUseTrialTeam()) {
        player.getTeamManager().updateTeamEntities(false);
    }
}
```

### 11.1 3 种试用副本

```
DUNGEON_PLOT     → quest exec 给试用角色 (剧情副本)
DUNGEON_ACTIVITY → TRIAL_AVATAR / MIST_TRIAL (活动副本)
DUNGEON_ELEMENT_CHALLENGE → 元素挑战指定角色
```

→ 试用机制走 notes/34 EntityAvatar 的 TrialAvatar 路径。

---

## 12. 5 系统交汇点

DungeonManager 是 grasscutter 中**最显著的 5 系统交汇点**：

```
┌─────────────────┐
│  Quest 系统      │ → QUEST_CONTENT_ENTER_DUNGEON / FINISH / FAIL
│  (notes/43)     │
└─────────────────┘
         ↕
┌─────────────────┐
│  Scene/World    │ → SCENE_DUNGEON / setDungeonManager
│  (notes/35)     │   / 不掉落 / 不 callDrop
└─────────────────┘
         ↕
┌─────────────────┐
│  Combat/Drop    │ → EntityMonster.onDeath → triggerDungeonEvent
│  (notes/32/39)  │
└─────────────────┘
         ↕
┌─────────────────┐
│  Multiplayer    │ → MP double rewards / playerCount > 1
│  (notes/18)     │
└─────────────────┘
         ↕
┌─────────────────┐
│  Reward (DropTable + Statue) → rollRewards / addItems / ActionReason.DungeonStatueDrop
│  (notes/15)     │
└─────────────────┘
```

→ DungeonManager 是这 5 个系统的**集合点**。

---

## 13. 完整副本时序

```
[玩家点副本入口]
   ↓ GadgetInteract
HandlerDungeonEntryInfoReq → 给客户端展示副本信息

[玩家确认进入]
   ↓ PlayerEnterDungeonReq
DungeonSystem.enterDungeon:
   1. 查 DungeonData
   2. 记录 PlayerDungeonExitInfo
   3. transferPlayerToScene (notes/35)
   4. new DungeonManager(scene, data)
   5. scene.addDungeonSettleObserver

[场景加载完成]
   ↓ EnterSceneDoneReq (notes/35)
DungeonManager.startDungeon:
   - QUEST_CONTENT_ENTER_DUNGEON 触发
   - applyTrialTeam (如有)
   - 开始计时 startSceneTime

[战斗中]
   - 怪物死 → triggerDungeonEvent (KILL_MONSTER/KILL_GROUP/KILL_TYPE)
   - 玩家触发 trigger → triggerDungeonEvent (FINISH_CHALLENGE)
   - WorldChallenge.onCheckTimeOut (notes/35)
   ↓
   finishedConditions[i] = 1 if 满足
   ↓
   isFinishedSuccessfully (LogicType AND/OR)?

[通关]
   finishDungeon → notifyEndDungeon(true):
     - QUEST_CONTENT_FINISH_DUNGEON
     - 战令 TRIGGER_FINISH_DUNGEON
     - 链式副本 enterDungeon(passJumpDungeon)
     - Lua EVENT_DUNGEON_SETTLE(1)
   endDungeon(COMPLETED) → SettleListener.onDungeonSettle

[玩家点击神像]
   GadgetInteract → BossChestInteractHandler.onInteract
   → dungeonManager.getStatueDrops(player, useCondensed, groupId):
     - handleCost (扣树脂)
     - rollRewards (滚奖励)
     - addItems (notes/38)
     - Lua EVENT_DUNGEON_REWARD_GET

[玩家退出]
   ↓ PlayerExitDungeonReq
DungeonSystem.exitDungeon:
   - failDungeon 或 quitDungeon (如未通关)
   - 延迟 transferPlayerToScene 回原场景

[失败]
   challenge.fail → DungeonManager.failDungeon:
     - QUEST_CONTENT_FAIL_DUNGEON
     - Lua EVENT_DUNGEON_SETTLE(0)
   延迟 N 秒后自动 exitDungeon
```

→ **完整副本生命周期 5-8 阶段**。

---

## 14. 设计模式总结

### 14.1 注解+反射注册（第 14 次！）

```
@DungeonValue(DUNGEON_COND_XXX) BaseCondition
@ChallengeTypeValue(type = CHALLENGE_XXX) ChallengeFactoryHandler
```

→ 14 BaseCondition + 17 ChallengeFactoryHandler 都用此模式。

### 14.2 finishedConditions[] + LogicType

```
Quest 用 finishProgressList[] + LogicType
DungeonManager 用 finishedConditions[] + LogicType
Activity 用类似机制
```

→ 三大子系统**共享"进度数组 + 逻辑组合"模式**。

### 14.3 5 系统中介

```
DungeonManager
   ├── 触发 Quest event
   ├── 触发 BattlePass mission
   ├── 触发 Lua event
   ├── 调 Scene.respawnPlayer
   ├── 调 Inventory.addItems
   └── 调 TeamManager (TrialAvatar)
```

→ DungeonManager 不存"自己的状态"，而是**调度其他系统**。

### 14.4 双倍叠加

```
useCondensed → 多滚一次
mpDouble + playerCount > 1 → 数量 × 2
```

→ 浓缩树脂 + 联机 = 4 倍奖励——简单的乘法叠加。

---

## 15. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 客户端发"我通关" | ✗ 服务器算 isFinishedSuccessfully |
| 篡改 finishedConditions | ✗ 服务器存 |
| 不扣树脂领奖 | ✗ handleCost 服务器验证 |
| 用浓缩树脂在 40 树脂副本 | ✗ resinCost != 20 拒绝 |
| 重复领奖 | ✗ rewardedPlayers Set 防 |
| 飞快通关（绕开挑战）| ✓ 可能 (位置/伤害在客户端) |

→ 副本系统**奖励侧反作弊较强**，但**通关路径侧弱**（依赖客户端的位置/伤害正确性）。

---

## 16. 关键收获

1. **72 个 Java 文件**支撑副本系统：grasscutter 第 3 大子系统
2. **DungeonSystem (BaseGameSystem) + DungeonManager (per Scene) 双层**
3. **第 14 次"注解+反射"模式**：`@DungeonValue` + `@ChallengeTypeValue`
4. **8 种 DungeonPassConditionType**：KILL_MONSTER / KILL_GROUP / KILL_TYPE / FINISH_QUEST / KILL_COUNT / IN_TIME / FINISH_CHALLENGE / END_MULTISTAGE
5. **36 种 DungeonType + countsToBattlepass 标记**：核心 8 类 + 活动 28 类
6. **17 个 ChallengeFactoryHandler 覆盖 25+ ChallengeType**
7. **8 个 ChallengeTrigger 积木**：Damage / ElementReaction / Father / Guard / KillMonster / Time / TriggerGroup
8. **enterDungeon 4 步**：找入口 → 记退出 → 切场景 → 创建 Manager
9. **startDungeon → 应用 TrialAvatar + 触发 QUEST_CONTENT_ENTER_DUNGEON**
10. **finishDungeon 4 触发**：Quest finish/fail / 战令 / 链式副本 / Lua 事件
11. **3 种结束**：COMPLETED / FAILED / QUIT
12. **延迟退出**：failSettleCountdownTime / quitSettleCountdownTime
13. **链式副本**：passJumpDungeon 自动进下一关
14. **getStatueDrops 7 步**：资格→扣树脂→滚奖励→入袋→标记→Lua→更新入口
15. **3 种树脂消耗**：普通 / 浓缩 (仅 20 树脂副本) / 其他通货
16. **双倍叠加**：useCondensed + mpDouble + playerCount > 1
17. **副本内 waypoint 重生**：activeDungeonWayPoints + newestWayPoint
18. **5 系统交汇点**：Quest + Scene + Combat + Multiplayer + Reward
19. **TOWER 立即退出**：深境螺旋有自己的结算 UI
20. **完整生命周期 5-8 阶段**：进入 → 加载 → 战斗 → 通关/失败 → 领奖 → 退出

---

## 17. 一句话总结

> **DungeonManager 副本运行时 = 5 系统 (Quest + Scene + Combat + Multiplayer + Reward) 的交汇点; 72 个文件 / DungeonSystem (BaseGameSystem) + DungeonManager (per Scene) 双层; 第 14 次注解反射 (DungeonValue + ChallengeTypeValue); 8 通关条件 × 36 副本类型 × 17 ChallengeFactoryHandler × 8 Trigger 积木 = 任意副本玩法; finishedConditions[] + LogicType 与 Quest 同构; 浓缩树脂 × 联机双倍 = 4 倍奖励; passJumpDungeon 链式副本; waypoint 死亡重生; 5-8 阶段完整生命周期.**
> 
> **设计哲学: 模块化"积木"——Handler/Trigger/Cond 可任意组合, 数据驱动 (DungeonData + DungeonPassConfigData) 加新副本零代码改动; 与 Quest 系统同构 (finishedConditions[] + LogicType + 注解反射); 是 grasscutter 中"五大系统协作"的最佳实例.**

---

**前置笔记**：
- notes/19 副本系统 - 设计层 (14 ChallengeFactory 等)
- notes/27 架构模式 - 注解反射模式
- notes/32 怪物 - onDeath 触发 DUNGEON_COND_KILL_*
- notes/35 Scene/World - SCENE_DUNGEON / 副本不掉落
- notes/39 Drop - 副本零掉落 (跳过 callDrop)
- notes/42 表演 - quest exec 给试用角色
- notes/43 Quest 引擎 - finishedConditions[] + LogicType 同构
- notes/46 GameServer - DungeonSystem 是 14 之一

**关联文件**：
- `DungeonManager.java`(322) - 核心运行时
- `DungeonSystem.java`(169) - 全局调度
- `DungeonPassConditionType.java` - 8 种通关条件
- `DungeonType.java` - 36 种副本类型
- `ChallengeType.java` - 25+ 挑战类型
- `factory/*.java` × 17 - ChallengeFactoryHandler
- `trigger/*.java` × 8 - ChallengeTrigger
- `pass_condition/*.java` × 14 - BaseCondition
- `settle_listeners/*` - SettleListener
- `dungeon_results/*` - DungeonResult

**研究的源代码**: 1500+ 行副本系统代码 + 72 个文件结构梳理。
