# 19 · Dungeon / Challenge 系统 · 五大子系统的交汇点

副本（Dungeon）是把之前研究的 Quest / Scene Script / Combat / Multiplayer / Reward **五个系统在一个独立场景里捏合起来**的实战平台。理解 Dungeon 等于理解整个游戏循环。

> 核心代码：`game/dungeons/DungeonManager.java`（322 行）+ `DungeonSystem.java`（200+ 行）+ `challenge/`（14 种 challenge factory + 触发器）

---

## 1. 整体架构

```
DungeonSystem (全局, 单例)
  ├── passCondHandlers  通过条件 handler 注册表（注解驱动）
  └── enterDungeon / restartDungeon / exitDungeon

DungeonManager (per Scene 实例)
  ├── scene + dungeonData + passConfigData
  ├── finishedConditions[]   通过条件进度数组
  ├── rewardedPlayers Set    已领奖玩家集合（防重）
  ├── activeDungeonWayPoints 已激活的复活点
  └── ended flag

WorldChallenge (副本内子挑战)
  ├── 14 种 ChallengeFactory（KillCount / Survive / Freeze 等）
  ├── 触发器机制
  └── 成功/失败 事件
```

→ 关系：**一个 Dungeon 一个 Manager**，里面可以有**多个 Challenge**。Challenge 是"副本内的小目标"（比如"30 秒内杀完所有怪"）。

---

## 2. 副本进入流程（端到端）

```java
// DungeonSystem.enterDungeon (DungeonSystem.java:69)
public boolean enterDungeon(Player player, int pointId, int dungeonId, ...) {
    val data = GameData.getDungeonDataMap().get(dungeonId);
    if (data == null) return false;
    
    // 1. 记下来源场景（用于退出时返回）
    player.getScene().setPrevScene(player.getSceneId());
    player.getDungeonExitInfo().setAll(player, dungeonId, realPointId);
    
    // 2. 传送到副本 Scene
    if (player.getWorld().transferPlayerToScene(player, data.getSceneId(), data)) {
        // 3. 创建 DungeonManager 绑定到新 Scene
        player.getScene().setDungeonManager(new DungeonManager(player.getScene(), data));
        player.getScene().addDungeonSettleObserver(dungeonSettleListeners);
    }
    
    return true;
}
```

→ 关键：**副本 = 临时新建的 Scene + DungeonManager**。退出时这个 Scene 整个销毁。

### 启动后的事件链（DungeonManager.startDungeon）

```java
public void startDungeon() {
    this.startSceneTime = this.scene.getSceneTimeSeconds();
    this.scene.getPlayers().forEach(p -> {
        // ★ 触发任务事件
        p.getQuestManager().queueEvent(QuestContent.QUEST_CONTENT_ENTER_DUNGEON, dungeonId);
        // ★ 添加试用角色（如有）
        applyTrialTeam(p);
    });
}
```

→ 进副本会 fire `QUEST_CONTENT_ENTER_DUNGEON`（任务系统订阅了这个事件）+ 添加试用角色。

---

## 3. 试用角色机制：Trial Avatar

```java
// DungeonManager.applyTrialTeam (DungeonManager.java:220)
private void applyTrialTeam(Player player) {
    switch (this.dungeonData.getType()) {
        // case DUNGEON_PLOT: 由 quest exec 控制（QUEST_EXEC_GRANT_TRIAL_AVATAR）
        
        case DUNGEON_ACTIVITY -> {
            switch (this.dungeonData.getPlayType()) {
                case DUNGEON_PLAY_TYPE_TRIAL_AVATAR ->
                    // 试用角色活动 (如"试用风原万叶")
                    player.getActivityManager()
                        .getActivityHandlerAs(NEW_ACTIVITY_TRIAL_AVATAR, TrialAvatarActivityHandler.class)
                        .map(TrialAvatarActivityHandler::getBattleAvatarsList)
                        .ifPresent(battleAvatars -> player.addTrialAvatarsForDungeon(
                            battleAvatars, GrantReason.GRANT_BY_TRIAL_AVATAR_ACTIVITY));
                
                case DUNGEON_PLAY_TYPE_MIST_TRIAL -> {} // TODO 雾海试炼
            }
        }
        case DUNGEON_ELEMENT_CHALLENGE ->
            // 元素挑战副本 (如"用雷元素打七天神像")
            Optional.ofNullable(GameData.getDungeonElementChallengeDataMap().get(getDungeonData().getId()))
                .map(DungeonElementChallengeData::getTrialAvatarId)
                .ifPresent(trialAvatarId -> player.addTrialAvatarsForDungeon(
                    trialAvatarId, GrantReason.GRANT_BY_DUNGEON_ELEMENT_CHALLENGE));
    }
    
    if (player.getTeamManager().isUseTrialTeam()) {
        player.getTeamManager().updateTeamEntities(false);
    }
}
```

**4 种试用角色来源**：
1. `DUNGEON_PLOT` — 剧情副本：由任务的 `QUEST_EXEC_GRANT_TRIAL_AVATAR` 控制
2. `DUNGEON_ACTIVITY → TRIAL_AVATAR` — 活动试用
3. `DUNGEON_ELEMENT_CHALLENGE` — 元素挑战指定角色
4. `DUNGEON_PLAY_TYPE_MIST_TRIAL` — 雾海试炼（未实现）

→ **Trial Avatar 是 server-authoritative**：玩家不能自己 spawn 一个临时角色。每个 trial 都有 `GrantReason`（审计追溯）。

---

## 4. 通过条件系统（DungeonPassCondition）

通过条件用与 Quest 系统**完全相同的模式**：cond + LogicType + 进度数组。

### 4.1 数据结构

```java
// DungeonPassConfigData
{
    "id": ...,
    "logicType": "LOGIC_AND",   // 或 LOGIC_OR
    "conds": [
        { "condType": "DUNGEON_COND_KILL_TMP_MONSTER", "params": [...] },
        { "condType": "DUNGEON_COND_FINISH_QUEST",     "params": [302207] },
        { "condType": "DUNGEON_COND_TIME_LESS_THAN",   "params": [600] },
    ]
}
```

### 4.2 触发流程

```java
// DungeonManager.triggerEvent (DungeonManager.java:68)
public void triggerEvent(DungeonPassConditionType conditionType, int... params) {
    if (this.ended) return;
    
    // 找匹配的 cond 并执行 handler
    this.passConfigData.getConds().stream()
        .filter(cond -> cond.getCondType() == conditionType)
        .filter(cond -> getScene().getWorld().getServer()
            .getDungeonSystem().triggerCondition(cond, params))
        .forEach(cond -> this.finishedConditions[
            this.passConfigData.getConds().indexOf(cond)] = 1);
    
    if (isFinishedSuccessfully()) finishDungeon();
}

public boolean isFinishedSuccessfully() {
    return LogicType.calculate(this.passConfigData.getLogicType(), this.finishedConditions);
}
```

→ **和 SubQuest 的 finishCond 系统一模一样**：
- 多个 condition
- LogicType (AND/OR) 组合
- 进度数组（int[]，0/1 表示满足）
- 任何条件满足时 fire `triggerEvent`

### 4.3 条件类型

```java
// DungeonPassConditionType (enum)
DUNGEON_COND_FINISH_QUEST           // 任务完成
DUNGEON_COND_KILL_MONSTER_BY_GROUP  // 杀完某 group 怪物
DUNGEON_COND_KILL_TMP_MONSTER       // 杀临时怪
DUNGEON_COND_TIME_LESS_THAN         // 时间限制
DUNGEON_COND_GADGET_STATE_CHANGE    // 机关状态
DUNGEON_COND_FINISH_CHALLENGE       // 完成 Challenge
DUNGEON_COND_AVATAR_DIE_NUM         // 死亡次数（限制）
... (~20 种)
```

→ **Dungeon 通过条件 = Quest finishCond 的子集 + 副本特有项**。

### 4.4 注解驱动注册（第 5 次出现）

```java
// DungeonSystem.registerHandlers (DungeonSystem.java:39)
public void registerHandlers() {
    registerHandlers(this.passCondHandlers, BaseCondition.class.getPackageName(), DungeonBaseHandler.class);
}

public <T> void registerPacketHandler(Int2ObjectMap<T> map, Class<? extends T> handlerClass) {
    Optional.ofNullable(handlerClass.getAnnotation(DungeonValue.class))
        .map(DungeonValue::value)
        .ifPresent(value -> map.put(value.ordinal(), handlerClass.getDeclaredConstructor().newInstance()));
}
```

`@DungeonValue` 注解 + `BaseCondition` 抽象基类 + 子类自动注册——**和 `@QuestValueCond` / `@QuestValueContent` / `@AbilityAction` / `@QuestValueExec` 完全同构**。

---

## 5. Challenge 系统：副本内的子挑战

每个副本可以包含多个 Challenge（"30 秒内杀够 10 个"、"全程不死"等）。

### 5.1 14 种 Challenge Factory

```
KillCountChallengeFactoryHandler              杀够 N 个
KillCountTimeOrFastChallengeFactoryHandler    时限内杀够（越快越好）
KillCountFrozenLessChallengeFactoryHandler    杀怪同时少冰冻
KillCountGuardChallengeFactoryHandler         杀怪同时护卫
KillCountGuardTimeChallengeFactoryHandler     护卫 + 时限
KillMonsterTimeChallengeFactoryHandler        时限内杀完所有
DieLessTimeChallengeFactoryHandler            少死亡时限
SurviveChallengeFactoryHandler                存活
TimeFlyChallengeFactoryHandler                时间飞逝相关
FreezeEnemyTimeChallengeFactoryHandler        冻结时间
ElementReactionCountChallengeFactoryHandler   元素反应次数
MonsterOrShieldDamageCountChallengeFactoryHandler 伤害量
TriggerCountChallengeFactoryHandler           触发器次数
Trigger2Trigger1ChallengeFactoryHandler       复合触发器
FatherSuccessTimeChallengeFactoryHandler      父挑战时间链
```

### 5.2 工厂模式实例（KillCountChallengeFactoryHandler）

```java
@ChallengeTypeValue(type = CHALLENGE_KILL_COUNT)
public class KillCountChallengeFactoryHandler extends ChallengeFactoryHandler {
    /**
     * Build a new challenge
     * @param params: [groupId, goal, unused1, unused2]
     * ActiveChallenge with 1, 1, 241033003, 15, 0, 0
     */
    @Override
    public WorldChallenge build(ChallengeType type, ChallengeInfo header, 
                                List<Integer> params, ChallengeScoreInfo scoreInfo, 
                                Scene scene, SceneGroup group) {
        val realGroup = scene.getScriptManager().getGroupById(params.get(0));
        return new WorldChallenge(
            scene, realGroup,
            header,
            List.of(params.get(1)),                                       // 目标计数
            buildChallengeTrigger(List.of(new KillMonsterTrigger(1, params.get(1)))),
            scoreInfo
        );
    }
}
```

→ 每个 ChallengeType 对应一个 Factory，**Lua 脚本通过 `ActiveChallenge` API 调用**（详见 notes/14）。Factory 接 params 创建 `WorldChallenge` 实例 + 触发器。

### 5.3 Lua 端调用示例

```lua
-- (客户端 Lua 中) 副本里某 trigger 的 action
ScriptLib.ActiveChallenge(
    context, 
    1,                  -- challengeIndex (在副本内序号)
    CHALLENGE_KILL_COUNT,  -- type
    241033003,         -- groupId
    15,                -- goal (杀 15 个)
    0, 0               -- unused
)
```

→ 服务器收到 `ActiveChallenge` → 找对应 Factory → build WorldChallenge → 注册到 scene → 监听 `EVENT_ANY_MONSTER_DIE` 等事件 → 计数到目标后 fire `EVENT_CHALLENGE_SUCCESS`。

---

## 6. 完成 / 失败 / 退出 流程

### 6.1 完成（finishDungeon）

```java
// DungeonManager.java:256
public void finishDungeon() {
    notifyEndDungeon(true);
    endDungeon(BaseDungeonResult.DungeonEndReason.COMPLETED);
}

// notifyEndDungeon
public void notifyEndDungeon(boolean successfully) {
    this.scene.getPlayers().forEach(p -> {
        // 1. 通知任务系统
        p.getQuestManager().queueEvent(successfully ?
                QuestContent.QUEST_CONTENT_FINISH_DUNGEON : 
                QuestContent.QUEST_CONTENT_FAIL_DUNGEON,
            this.dungeonData.getId());
        
        // 2. 通知战令系统
        if (this.dungeonData.getType().isCountsToBattlepass() && successfully) {
            p.getBattlePassManager().triggerMission(WatcherTriggerType.TRIGGER_FINISH_DUNGEON);
        }
        
        // 3. 链式副本：自动进入下一副本
        if (dungeonData.getPassJumpDungeon() > 0) {
            p.getServer().getDungeonSystem().enterDungeon(p, 0, dungeonData.getPassJumpDungeon());
        }
    });
    
    // 4. 通知场景脚本
    this.scene.getScriptManager().callEvent(
        new ScriptArgs(0, EventType.EVENT_DUNGEON_SETTLE, successfully ? 1 : 0));
}
```

→ **一个完成事件触发 4 个子系统响应**：Quest / BattlePass / 链式副本 / Scene Script。这就是为什么 Dungeon 是"系统交汇点"。

### 6.2 失败（failDungeon）

```java
public void failDungeon() {
    notifyEndDungeon(false);   // 同样通知，但 fire FAIL_DUNGEON
    endDungeon(BaseDungeonResult.DungeonEndReason.FAILED);
}
```

任务系统订阅 `QUEST_CONTENT_FAIL_DUNGEON`（corpus 里出现 406 次）——很多任务用"副本失败"作为 failCond。

### 6.3 主动退出（quitDungeon）

```java
public void quitDungeon() {
    notifyEndDungeon(false);   // 也算失败
    endDungeon(BaseDungeonResult.DungeonEndReason.QUIT);
}
```

注意：**主动退出 = 失败**，会触发 fail cond。这是为什么"退出副本"会让某些任务失败的原因。

### 6.4 重启（restartDungeon）

```java
// DungeonSystem.restartDungeon
public void restartDungeon(Player player, DungeonSettleListener listener) {
    val scene = player.getScene();
    if (scene == null || scene.getDungeonManager() == null) return;
    
    scene.getScriptManager().onDestroy();    // 销毁所有 group/trigger
    scene.getWorld().deregisterScene(scene);  // 注销 scene
    enterDungeon(player, 0, dungeonData.getId(), listener);   // 重新进
}
```

→ **重启 = 销毁 scene + 重新进入**。所有 group / monster / trigger / variable **完全重置**。注意 `onDestroy` 触发 trigger 反注册，避免泄漏。

---

## 7. 奖励发放：DungeonDrop

副本通关后，由 `BasicDungeonSettleListener.onDungeonSettle` 触发奖励发放：

```java
// 简化逻辑（getRewards in DungeonManager.java:179）
public List<GameItem> getRewards(int dungeonId, Player player) {
    val dropEntries = GameData.getDungeonDropDataMap().get(dungeonId);
    if (dropEntries != null) {
        for (DropEntry entry : dropEntries) {
            int amount = entry.getAmount();
            if (entry.getItems().size() == 1) {
                rewards.add(new GameItem(entry.getItems().get(0), amount));
            } else {
                // 按概率随机一个
                for (int i = 0; i < amount; i++) {
                    int itemId = Utils.drawRandomListElement(
                        entry.getItems(), entry.getItemProbabilities());
                    rewards.add(new GameItem(itemId, 1));
                }
            }
        }
    } else {
        // 没有 DropData → 回退到 RewardPreview
        Arrays.stream(this.dungeonData.getRewardPreviewData().getPreviewItems())
            .map(param -> new GameItem(param.getId(), Math.max(param.getCount(), 1)))
            .forEach(rewards::add);
    }
    return rewards;
}
```

特点：
- **随机化**：多种可能道具按概率抽
- **降级 fallback**：没 DropData 用 RewardPreview（这就是 notes/15 提到的"客户端预览数据"作为运行时 fallback）
- **rewardedPlayers Set 防重**：每个 player UID 只领一次

发奖时调 `inventory.addItem(items, ActionReason.DungeonPass)`（notes/15 的 100+ ActionReason 之一）。

---

## 8. DungeonType 全分类（不同副本玩法）

```java
DUNGEON_PLOT                    剧情副本（如须弥章主线副本）
DUNGEON_ACTIVITY                活动副本
DUNGEON_ELEMENT_CHALLENGE       元素挑战
DUNGEON_DAILY_FIGHT             日常材料副本
DUNGEON_BOSS                    周本（武器/角色突破材料）
DUNGEON_HOMEWORLD               尘歌壶（玩家自定义副本）
DUNGEON_LEY_LINE                地脉花（每日刷新材料）
DUNGEON_ABYSS                   深境螺旋（PvE 高难挑战）
DUNGEON_TOWER                   月之塔（活动深渊）
DUNGEON_BLITZ_RUSH              闪电突袭活动
DUNGEON_THEATRE                 七圣召唤剧院
... 更多
```

每种 type 决定：
- 是否进 BattlePass（`isCountsToBattlepass()`）
- 试用角色机制
- 失败后如何处理（重做 / 退出）
- 奖励是否每日重置 / 周限制

---

## 9. 多人副本特殊处理

副本支持多人（host 邀请 guest 一起打）。区别于单人副本：

1. **`rewardedPlayers` 是 Set**：每个 player uid 只领一次奖（即使多人通关也每人各一份）
2. **`getWeeklyBossUidInfo`**：周本树脂折扣信息按 uid 维度记录，每人独立
3. **kill 计数共享**：scene 的 `killedMonsterCount` 是房间共享的（一队人合打一只怪算一次）
4. **失败/退出**：任何人主动退出**只影响自己**（被传送回大世界），副本对剩余玩家继续

注意 notes/18 看过的 `MAIN_COOP_*` 系列任务——专门为多人副本设计的剧情任务（如周本剧情）。

---

## 10. 复活点系统（WayPoint）

```java
// DungeonManager.activateRespawnPoint (DungeonManager.java:87)
public boolean activateRespawnPoint(int pointId) {
    val respawnPoint = GameData.getScenePointEntryById(this.scene.getId(), pointId);
    if (respawnPoint == null) return false;
    
    this.scene.broadcastPacket(new PacketDungeonWayPointNotify(
        this.activeDungeonWayPoints.add(pointId), 
        this.activeDungeonWayPoints));
    this.newestWayPoint = pointId;
    return true;
}

public Position getRespawnLocation() {
    if(newestWayPoint == 0) return null;
    return GameData.getScenePointEntryById(scene.getId(), newestWayPoint)
        .getPointData().getTransPosWithFallback();
}
```

→ 副本里玩家死亡可以复活在最近激活的 waypoint。**和大世界的"七天神像复活"区别**：副本 waypoint 是临时的，副本结束就消失。

---

## 11. 完整流程示例：进入须弥章一个剧情副本

```
[玩家在主任务剧情中触发"进入图书馆副本"]
   ↓ 任务系统 finishExec:
QUEST_EXEC_NOTIFY_GROUP_LUA  (notes/08)
   ↓
Lua 脚本调 ScriptLib.EnterDungeon(dungeonId)
   ↓
DungeonSystem.enterDungeon(player, 0, dungeonId)
   ↓
   1. setPrevScene (记原场景，退出时返回)
   2. transferPlayerToScene (新建 dungeon Scene)
   3. new DungeonManager(scene, data)
   4. addDungeonSettleObserver (监听结算)
   ↓
DungeonManager.startDungeon()
   for each player:
     - fire QUEST_CONTENT_ENTER_DUNGEON  (任务系统订阅)
     - applyTrialTeam (DUNGEON_PLOT 类型 → 任务给试用角色)
   ↓
[玩家在副本里打怪/解谜]
   ↓ Lua trigger 通过 ScriptLib.ActiveChallenge 启动 KillCountChallenge
   ↓ 玩家杀够数量 → fire EVENT_CHALLENGE_SUCCESS
   ↓ DungeonManager.triggerEvent(DUNGEON_COND_FINISH_CHALLENGE)
   ↓ finishedConditions[i] = 1
   ↓ LogicType.calculate (LOGIC_AND, [1,...]) → true
finishDungeon()
   ↓
notifyEndDungeon(true):
   for each player:
     - fire QUEST_CONTENT_FINISH_DUNGEON  (任务系统：本步通过)
     - BattlePass.TRIGGER_FINISH_DUNGEON  (战令进度)
     - 检查 passJumpDungeon → 如有，自动进入下一副本
   - Scene.callEvent(EVENT_DUNGEON_SETTLE, success=1)  (脚本系统)
   ↓
endDungeon(COMPLETED)
   - DungeonSettleListener.onDungeonSettle:
     - 算奖励 (DungeonDrop 表 + 概率随机)
     - inventory.addItem(rewards, ActionReason.DungeonPass)
     - 客户端弹结算 UI
     - rewardedPlayers.add(uid)  (防重领)
   ↓
[退出 → transferPlayerToScene 回原场景]
   - removeTrialAvatars
   - cleanUpScene
   - scene.deregister
```

→ 一次副本通关**触发 4 个系统的连锁反应**：Quest, BattlePass, Scene Script, Reward。Dungeon 是它们之间的协调中心。

---

## 12. 关键设计经验

### 12.1 Scene 临时化是隔离副本的关键

每次进副本 = **新建 Scene**。退出 = **销毁 Scene**。这种"用完即扔"模式避免了：
- 副本里的怪物泄漏到大世界
- 副本里的 trigger 在退出后还在跑
- 副本变量影响 group state

代价：**进出副本都有 transfer 动画**（loading 时间）——但换来彻底的隔离。

### 12.2 通过条件用与 Quest 同构架构（5+ 处架构同构）

```
Quest 系统:        finishCond + LogicType + finishProgress[]
Dungeon 系统:      passConfigData.conds + LogicType + finishedConditions[]
Battle Pass:       missions + 各种 trigger
Scene Script:      triggers + 状态
Combat Ability:    actions + modifiers
```

**"cond + LogicType + 进度数组"** 在 5+ 个子系统里复用。证明这是**经过验证的好抽象**。

### 12.3 Challenge 是副本的"模块化目标"

副本本身是个容器，Challenge 是里面的一个个具体目标：
- 副本通关条件 (DungeonPassCondition) — 总目标
- Challenge — 子目标 / 评分项

→ 这是"主任务/支线"模式的副本对应物。**层次化的目标系统**。

### 12.4 Trial Avatar 是数据驱动的"临时角色"

服务器掌握 Trial Avatar 的"配方"（哪个副本给哪些角色），客户端只看到结果。**绝对的服务器权威**——玩家不能 hack 出永久试用角色。

### 12.5 副本的失败 = 任务的失败

`QUEST_CONTENT_FAIL_DUNGEON` 出现 406 次（corpus 数据）——证明**很多任务用副本失败作为分支条件**。Dungeon 不只是娱乐，是任务剧本的一部分。

---

## 13. 给做 RPG 副本系统开发者的提炼

1. **副本 = 临时 Scene**，进出销毁——彻底隔离
2. **通过条件复用主系统的 cond/LogicType 抽象**——不要为副本另起炉灶
3. **Challenge 用工厂模式 + 注解注册**——14 种 challenge 用同一种结构
4. **Trial Avatar 是服务器配方**——客户端只看结果，防止角色作弊
5. **副本失败 = 通用事件**——让任务系统能订阅
6. **`PassJumpDungeon` 链式副本**——多阶段战斗用配表表达，不写代码
7. **多人副本奖励按 uid 防重**——`rewardedPlayers Set` 简洁有效
8. **WayPoint 临时复活点**——副本独立于大世界的复活体系

---

## 14. 数据规模感

* 副本类型 (`DungeonType`)：~20 种
* DungeonData 总数：~700 个副本（含活动副本）
* DungeonPassConditionType：~20 种通过条件
* ChallengeType：~14 种工厂
* 复活点 (WayPoint)：每副本 0-5 个
* 奖励掉落表：每副本 1-N 项

代码规模：
- `DungeonManager.java`：322 行
- `DungeonSystem.java`：200+ 行
- `challenge/`：14 个 factory + 触发器 ~500 行
- 总核心 ~1500 行（不含数据类和 listeners）

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/dungeons/DungeonManager.java` (322 行)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/dungeons/DungeonSystem.java` (200+ 行)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/dungeons/challenge/factory/` (14 个 ChallengeFactory)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/dungeons/pass_condition/` (20+ 个 PassCondition handler)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerPlayerEnterDungeonReq.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerDungeonPlayerDieReq.java`
- 数据：`GenshinData/ExcelBinOutput/DungeonExcelConfigData.json` + `DungeonPassConfigData.json` + `DungeonDropExcelConfigData.json`
