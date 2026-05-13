# Tower / 深境螺旋系统深度剖析

> 第 51 篇：grasscutter 中提到 20+ 次但从未真正打开的核心 endgame 系统——**双层架构 (TowerSystem + TowerManager) × 12 层 × 3 室 × 双队 × 上下半场 × 3 类 Buff × 月度循环**。

---

## 0. 为什么这一篇重要

前 50 篇里 Tower 反复出现但 runtime 没专门挖：
- notes/19 副本：`DUNGEON_TOWER` 类型
- notes/40 Player Manager：`towerManager` 是 25 之一
- notes/48 副本运行时：TOWER 立即退出（无延迟）+ `removeCurrentLevelBuff` / `clearTeamOnExit`
- notes/41 事件总线：`TRIGGER_FINISH_TOWER_LEVEL` / `TRIGGER_DONE_TOWER_*`

但**深境螺旋怎么工作？双队怎么切换？星数怎么算？月度怎么循环？**——这一篇统一回答。

---

## 1. Tower 系统文件结构（10 个）

```
emu/grasscutter/game/tower/
├── TowerSystem.java         (89)   ← 全局调度 (BaseGameSystem)
├── TowerManager.java        (393)  ← per Player
├── TowerData.java           (~200) ← 持久化数据
├── TowerScheduleConfig.java        ← TowerSchedule.json 配置
├── TowerMonthlyBriefInfo.java      ← 月度简报
├── TowerFloorRecordInfo.java       ← 楼层记录
├── TowerLevelRecordInfo.java       ← 层记录
├── TowerTeamInfo.java       (51)   ← 双队配置
├── TowerCondType.java       (8)    ← 3 种通关条件
└── TowerBuffLastingType.java (8)   ← 3 种 Buff 持续类型
```

→ **889 行**支撑深境螺旋系统。

---

## 2. 深境螺旋的层级结构

```
深境螺旋 (Spiral Abyss)
   ├── 入口楼层 (1-8 floor)         ← 永久, 玩家自己练
   │   ├── 室 1 (Chamber/Level 1)
   │   ├── 室 2
   │   └── 室 3
   └── 周期楼层 (9-12 floor)        ← 每月重置, 高难度
       ├── 室 1
       ├── 室 2  
       └── 室 3
```

**每个 Chamber/Level**：
- **上半场** (UpperPart) — 用队伍 1 (4 人)
- **下半场** (LowerPart) — 用队伍 2 (4 人)
- **3 星制** — 满足 3 个 cond 得 3 星

→ **12 楼 × 3 室 × 2 队 = 72 战斗场景**，单月最多 **36 星**（12-9 楼）。

---

## 3. TowerSystem：全局月度调度（89 行）

`TowerSystem.java` — 14 GameSystem 之一（notes/46）：

```java
@Getter
public class TowerSystem extends BaseGameSystem {
    private final static TowerScheduleConfig towerScheduleConfig;
    
    static {
        towerScheduleConfig = DataLoader.loadClass("TowerSchedule.json", TowerScheduleConfig.class);
    }
    
    public int getScheduleId() {
        return towerScheduleConfig.getScheduleId();
    }
    
    public int getScheduleStartDate() { ... }   // 月初
    public int getScheduleChangeDate() { ... }  // 月底重置
    
    public List<Integer> getEntranceFloor() {       // 1-8 楼
        return getCurrentTowerScheduleData().getEntranceFloorId();
    }
    
    public List<Integer> getScheduleFloors() {      // 9-12 楼 (当月配置)
        return getCurrentTowerScheduleData().getSchedules().stream()
            .map(TowerScheduleData.ScheduleDetail::getFloorList).flatMap(List::stream).toList();
    }
    
    public List<Integer> getAllFloors() {            // 1-12 楼
        return Stream.of(getEntranceFloor(), getScheduleFloors()).flatMap(Collection::stream).toList();
    }
    
    public int getNextFloorId(int floorId) {
        // 在 allFloors 中找下一楼
        val allFloors = getAllFloors();
        return IntStream.range(0, allFloors.size() - 1)
            .filter(i -> floorId == allFloors.get(i))
            .mapToObj(i -> allFloors.get(i + 1))
            .findFirst().orElse(0);
    }
    
    public int getLastEntranceFloor() {              // 第 8 楼
        return getEntranceFloor().stream().reduce((first, second) -> second).orElse(0);
    }
    
    public int getFirstScheduleFloor() {              // 第 9 楼
        return getNextFloorId(getLastEntranceFloor());
    }
}
```

### 3.1 TowerScheduleConfig.json

```json
{
  "scheduleId": 2025_01_001,
  "scheduleStartTime": "2025-01-01 04:00:00",
  "nextScheduleChangeTime": "2025-02-01 04:00:00"
}
```

→ **每月 1 号 4 点重置**——和正服一致。

### 3.2 入口 vs 周期 双引擎

```
入口楼层 (entrance) = 1-8 楼
  - 永久数据
  - 玩家随便玩
  - 不重置

周期楼层 (schedule) = 9-12 楼
  - 每月重置 (scheduleId 改变)
  - 怪物配置每月变 (TowerSchedule.json)
  - 玩家进入需要 6 星 (8 楼)
```

---

## 4. TowerManager：每玩家深境引擎（393 行）

### 4.1 onLogin 月度刷新

```java
public void onLogin() {
    // 1. 兜底解锁 1 楼
    if (getRecordMap().isEmpty()) {
        getRecordMap().put(1, TowerFloorRecordInfo.create(1001));
    }
    
    // 2. ★ 检查新月度 schedule
    if (getTowerSystem().getScheduleId() != getTowerData().getScheduleId()) {
        getTowerData().startNewSchedule(getTowerSystem().getScheduleId());
        
        // 3. 如果玩家 8 楼达 6 星 → 解锁 9 楼
        if (canEnterScheduleFloor()) {
            getRecordMap().put(9, TowerFloorRecordInfo.create(getTowerSystem().getFirstScheduleFloor()));
        }
    }
}
```

→ 玩家上线 → 检测月度变化 → 清空高楼记录 → 重新生成 9 楼入口。

### 4.2 canEnterScheduleFloor：6 星门槛

```java
private static final int STAR_COUNT_TO_UNLOCK_SCHEDULE_FLOOR = 6;

public boolean canEnterScheduleFloor() {
    return getRecordMap().values().stream()
        .filter(record -> record.getStarCount() >= STAR_COUNT_TO_UNLOCK_SCHEDULE_FLOOR)
        .anyMatch(record -> record.getFloorId() == getTowerSystem().getLastEntranceFloor());
}
```

→ **必须在 8 楼拿到 6 星** 才能进 9 楼。
→ 9 楼大门是"前哨"——筛选有实力的玩家上 9-12 楼。

---

## 5. 双队配置（TowerTeam × 2）

```java
public boolean teamSelect(int floorId, List<TowerTeam> towerTeamInfo) {
    val floorData = GameData.getTowerFloorDataMap().get(floorId);
    if (floorData == null) return false;
    
    // 初始化当层记录 + 持有双队信息
    getTowerData().initCurLevelRecord(towerTeamInfo, this.player, floorData);
    return true;
}
```

### 5.1 队伍数据存哪里

```java
public class TowerData {
    private final List<TowerTeamInfo> teamOnHold = new ArrayList<>();   // 双队
    // ...
}
```

→ `teamOnHold` 长度 = 2（上半队 + 下半队）。

### 5.2 TowerTeamInfo

```java
public class TowerTeamInfo {
    private List<TowerAvatar> avatars;       // 实际持久化的 avatar
    private List<TowerAvatar> tempAvatars;    // 临时副本
    
    public static TowerTeamInfo create(TowerTeam towerTeam, Player player) { ... }
    public void update() { ... }
    public void copy() { ... }
    public TowerTeam toProto() { ... }
}
```

→ **TowerAvatar vs Avatar 区别**：
- 普通玩家 Avatar：与玩家共享数据
- TowerAvatar：**独立深境数据**（独立等级 / 独立武器 / 独立圣遗物）
- 这就是为什么"开启深境前可调整角色，不影响大世界"。

### 5.3 上下半场切换：mirrorTeamSetUp

```java
public void mirrorTeamSetUp(int teamId) {
    getTowerData().setUpperPart(false);   // ★ 切下半
    this.player.sendPacket(new PacketTowerMiddleLevelChangeTeamNotify());
    
    // 重新传送 (Lua 触发, 跳过 UI)
    val teleportProps = TeleportProperties.builder()
        .sceneId(this.player.getSceneId())
        .enterReason(EnterReason.LuaSkipUi)          // ★ 跳过黑屏 UI
        .enterType(EnterType.ENTER_GOTO)
        .dungeonId(...)
        .isSkipUi(true)
        .build();
    
    this.player.getWorld().transferPlayerToScene(this.player, teleportProps);
    rebuildAndUseTeam(teamId);   // ★ 切到 team[1]
}
```

→ "**打完上半场 → 进下半队**" 由 Lua 脚本主动调用 `mirrorTeamSetUp`。
→ `EnterReason.LuaSkipUi` + `isSkipUi=true` 让客户端**无缝切换**（不弹出"加载中"）。

---

## 6. rebuildAndUseTeam：临时队伍 vs 临时复制

```java
private void rebuildAndUseTeam(int teamId) {
    if (this.player.getTeamManager().getTemporaryTeam().isEmpty()) {
        // 第一次进塔: 创建临时 TowerAvatar 副本
        val guidListList = getTowerData().getTeamOnHold().stream()
            .peek(TowerTeamInfo::copy)                          // 复制持久数据
            .map(TowerTeamInfo::getTempAvatars)
            .peek(avatarList -> avatarList.stream()
                .filter(this.player.getAvatars()::addAvatar)     // 加入玩家 avatar (临时)
                .peek(TowerAvatar::equipItems)
                .forEach(avatar -> this.player.sendPacket(new PacketAvatarAddNotify(avatar, false))))
            .map(avatarList -> avatarList.stream().map(TowerAvatar::getGuid).toList()).toList();
        
        getTowerData().updateTowerTeamStats();
        this.player.getTeamManager().setupTemporaryTeam(guidListList);
    } else {
        // 续玩: 只重建 guid 映射
        if (teamId == 0) {
            val guidListList = getTowerData().getTeamOnHold().stream()
                .map(TowerTeamInfo::getTempAvatars)
                .map(avatarList -> avatarList.stream().map(TowerAvatar::getGuid).toList()).toList();
            this.player.getTeamManager().setupTemporaryTeam(guidListList);
        }
    }
    this.player.getTeamManager().useTemporaryTeam(teamId);
}
```

### 6.1 临时副本设计

```
[首次进塔]
   getTeamOnHold() → 拿到 TowerTeamInfo × 2
   copy() → 创建 tempAvatars (新副本, 不影响持久 avatars)
   addAvatar(tempAvatar) → 临时加入玩家
   PacketAvatarAddNotify → 客户端展示

[切换上下半场]
   teamId 改变 → useTemporaryTeam(newTeamId)
   tempAvatars 共享 → 不重建

[退出深境]
   clearTeamOnExit:
     PacketAvatarDelNotify (删除所有临时 avatar)
     player.getAvatars().removeAvatarByGuid (清理引用)
```

→ **临时副本**让玩家可以"在深境用未升级的角色"——但**不影响大世界角色**。

---

## 7. enterLevel：进入某一室

```java
public void enterLevel(int enterPointId) {
    val levelData = getCurLevelData();
    
    // 判断 restart vs new
    val isRestart = Optional.ofNullable(this.player.getScene().getDungeonManager())
        .map(DungeonManager::getDungeonData).map(DungeonData::getId)
        .filter(id -> id == levelData.getDungeonId())
        .isPresent();
    
    if (isRestart) {
        // 重开当层 → 回上半场
        getTowerData().setUpperPart(true);
        clearTeamOnExit();
        this.player.getServer().getDungeonSystem().restartDungeon(this.player, TOWER_DUNGEON_SETTLE_LISTENER);
    } else {
        // 新进
        this.player.getServer().getDungeonSystem().enterDungeon(
            this.player, enterPointId, levelData.getDungeonId(), TOWER_DUNGEON_SETTLE_LISTENER);
    }
    
    rebuildAndUseTeam(getTowerData().getTeamIndex());
    notifyCurRecordChange();
    
    // 应用记录的 buff
    getTowerData().getTowerBuffs().values().forEach(this::notifyAddBuffs);
    
    this.player.getSession().send(new PacketTowerEnterLevelRsp(getTowerData()));
    
    // ★ 进塔后 stop 技能 (准备阶段)
    this.player.getSession().send(new PacketCanUseSkillNotify(false));
    
    // 通知星数条件
    this.player.getSession().send(new PacketTowerLevelStarCondNotify(getTowerData()));
}
```

### 7.1 与 DungeonManager 集成

```java
this.player.getServer().getDungeonSystem().enterDungeon(
    this.player, enterPointId, levelData.getDungeonId(), TOWER_DUNGEON_SETTLE_LISTENER);
```

→ Tower **复用副本系统**（notes/48）—— 但用 `TowerDungeonSettleListener` 而非 `BasicDungeonSettleListener`。

→ Tower 是**副本系统的"加壳"**：底层用 Dungeon，上层加深境特有逻辑。

### 7.2 进塔自动停止技能

```java
this.player.getSession().send(new PacketCanUseSkillNotify(false));
```

→ 玩家进塔时**自动暂停技能**——给"展示队伍/星数条件"的 UI 时间。

---

## 8. calculateStar：星数计算（3 cond × 上下半场）

```java
public int calculateStar(DungeonManager manager, WorldChallenge challenge) {
    val levelData = getCurLevelData();
    
    int stars = (int) levelData.getConds().stream()
        .map(c -> getTowerData().isUpperPart() ? c.getUpperHalfCond() : c.getLowerHalfCond())
        //                                       ↑ 上半场用 upper，下半场用 lower
        .filter(c -> switch (c.getTowerCondType()) {
            case TOWER_COND_CHALLENGE_LEFT_TIME_MORE_THAN ->
                // 时间剩余 >= 目标
                Optional.ofNullable(challenge.getChallengeTriggers().get(TimeTrigger.class))
                    .filter(tt -> challenge.getStartedAt() + tt.getGoal().get() 
                                  - challenge.getFinishedTime() >= c.getTargetLeftTime())
                    .isPresent();
            case TOWER_COND_LEFT_HP_GREATER_THAN ->
                // 守护对象 HP >= 目标百分比
                challenge.getGroupId() == c.getGroupId() &&
                Optional.ofNullable(challenge.getChallengeTriggers().get(GuardTrigger.class))
                    .filter(gt -> gt.getGoal().get() == c.getConfigId())
                    .filter(gt -> ((GuardTrigger) gt).getLastSendPercent() >= c.getTargetHpPercentage())
                    .isPresent();
            default -> false;
        }).count();
    
    tryRemoveBuffs();
    
    if (manager.isFinishedSuccessfully()) {
        getTowerData().updateFloorRecord(stars);   // ★ 仅当成绩更好才更新
        notifyFloorChange(getCurFloorRecordInfo());
    } else {
        removeCurrentLevelBuff();
        getTowerData().setUpperPart(true);          // 失败回上半
    }
    
    return stars;
}
```

### 8.1 3 种 TowerCondType

```java
public enum TowerCondType {
    TOWER_COND_NONE,
    TOWER_COND_FINISH_TIME_LESS_THAN,            // 限时通关
    TOWER_COND_LEFT_HP_GREATER_THAN,              // 守护对象血量
    TOWER_COND_CHALLENGE_LEFT_TIME_MORE_THAN     // ★ 剩余时间 (主流)
}
```

→ **几乎所有深境星数都是"限时"** —— 这就是"打得快有 3 星，打得慢只有 1 星"的机制。

### 8.2 上下半各 3 个 cond

```java
c.getUpperHalfCond()    // 上半队的 3 个 cond
c.getLowerHalfCond()    // 下半队的 3 个 cond
```

→ **上下半独立计算星数** —— 加起来 = 这层的星数（实际上是各自满足的 cond 数）。

→ 1 室通常 3 星上限（3 cond × 1 = 3）or 6 星（3 × 2 半场，但实际是叠加）。

### 8.3 仅当成绩更好才更新

```java
getTowerData().updateFloorRecord(stars);   // 内部: 如果 new > old 才更新
```

→ 玩家多次重打 —— **只保留最高星数**。

---

## 9. Buff 系统（3 种持续类型）

```java
public enum TowerBuffLastingType {
    TOWER_BUFF_LASTING_FLOOR,      // 持续整个楼层
    TOWER_BUFF_LASTING_IMMEDIATE,  // 即时 (一次性 trigger)
    TOWER_BUFF_LASTING_LEVEL       // 持续当前层 (3 间内)
}
```

### 9.1 应用 / 移除流程

```java
public void addBuffs(int towerBuffId) {
    getTowerData().getTowerBuffs().put(getCurrentLevelIndex(), towerBuffId);
    notifyAddBuffs(towerBuffId);
}

public void removeCurrentLevelBuff() {
    getTowerData().getTowerBuffs().remove(getCurrentLevelIndex());
}

private void tryRemoveBuffs() {
    val towerBuffList = getTowerData().getTowerBuffs().values().stream()
        .map(towerBuffId -> GameData.getTowerBuffDataMap().get(towerBuffId.intValue()))
        .filter(buffData ->
            buffData.getLastingType() == TOWER_BUFF_LASTING_LEVEL ||
            buffData.getLastingType() == TOWER_BUFF_LASTING_IMMEDIATE)
        .map(TowerBuffData::getId).toList();
    
    // 移除 LEVEL 和 IMMEDIATE 类的
    towerBuffList.forEach(getTowerData().getTowerBuffs()::remove);
    towerBuffList.stream().forEach(towerBuffData ->
        this.player.getBuffManager().removeBuff(towerBuffData.getBuffId()));
}
```

### 9.2 "**地脉异常**" 实现

```
进塔展示 banner: "本周地脉异常 - 远古龙蜥之灵"
玩家选择 buff (在塔内)
addBuffs → PlayerBuffManager.addBuff
战斗中持续生效
通关后 tryRemoveBuffs:
  - LEVEL: 移除 (进下一层)
  - IMMEDIATE: 移除 (一次性触发后)
  - FLOOR: 保留 (整个楼层)
```

→ 3 种持续类型对应不同 buff 设计：
- LASTING_FLOOR — 整楼层 buff（如"火元素 +30%"）
- LASTING_LEVEL — 单层 buff（如"本层击败重击 +50%"）
- LASTING_IMMEDIATE — 一次触发（如"开局所有怪 -50% HP"）

---

## 10. updateNextLevel：层切换

```java
public void updateNextLevel(DungeonManager manager) {
    // 仅当通关 + 下半场结束才推进
    if (manager == null || !manager.isFinishedSuccessfully() || getTowerData().isUpperPart()) return;
    
    if (hasNextLevel()) {
        // 同层下一室
        getTowerData().syncToNextLevel(getNextLevelId());
        getTowerData().updateTowerTeamStats();
    } else {
        // 跨楼层
        Optional.ofNullable(GameData.getTowerFloorDataMap().get(getNextFloorId()))
            .ifPresent(floorData -> getRecordMap().putIfAbsent(
                floorData.getFloorIndex(), TowerFloorRecordInfo.create(floorData.getId())));
        resetCurRecord();
    }
    notifyCurRecordChange();
}
```

### 10.1 层级跳转逻辑

```
完成第 N 楼第 M 室下半场
   ↓
hasNextLevel?  (M < 3)
   ├── 是 → 进入第 N 楼第 M+1 室 (syncToNextLevel)
   └── 否 → 跨楼: 解锁第 N+1 楼第 1 室 (resetCurRecord)
```

→ **下半场结束才推进** —— 上半场只是中间状态。

---

## 11. 奖励系统：双轨

### 11.1 First Pass Reward（初次通关）

```java
public List<GameItem> giveFirstPassReward(DungeonManager manager) {
    if (!manager.isFinishedSuccessfully() || getCurLevelRecordInfo().isReceivedFirstPassReward()) 
        return List.of();
    
    val rewardItems = ...
        .map(rewardData -> rewardData.getFirstPassRewardByStarCount(getCurFloorRecordInfo().getStarCount()))
        ...
    
    if (!rewardItems.isEmpty()) {
        getCurLevelRecordInfo().setReceivedFirstPassReward(true);
        this.player.getInventory().addItems(rewardItems, ActionReason.TowerFirstPassReward);
    }
    return rewardItems;
}
```

→ **首次通关给 1 次奖励**，按当时星数发放 —— 后续重打不再给。

### 11.2 Star Bounty（星数赏金）

```java
public boolean getStarReward(int floorId) {
    val floorData = GameData.getTowerFloorDataMap().get(floorId);
    val recordInfo = getRecordMap().get(floorData.getFloorIndex());
    
    val rewardIds = recordInfo.getPassedLevelMap().keySet().stream().parallel()
        .map(integer -> GameData.getTowerLevelDataMap().get(integer.intValue()))
        .map(TowerLevelData::getLevelIndex)
        .map(levelIndex -> GameData.getTowerRewardData(levelIndex, floorData.getFloorIndex()))
        .map(rewardData -> rewardData.getStarRewardsByStarCount(recordInfo.getStarCount()))
        .flatMap(List::stream)
        .filter(rewardId -> !recordInfo.getReceivedStarBounty().contains(rewardId))
        .distinct().toList();
    
    val rewardItems = rewardIds.stream().parallel()
        .map(rewardId -> GameData.getRewardDataMap().get(rewardId.intValue()))
        .map(RewardData::getRewardItemList).flatMap(List::stream)
        .collect(Collectors.toMap(ItemParamData::getItemId, ItemParamData::getItemCount, Integer::sum))
        .entrySet().stream().map(e -> new GameItem(e.getKey(), e.getValue()))
        .toList();
    
    if (!rewardItems.isEmpty()) {
        recordInfo.onGetReward(rewardIds);
        this.player.getInventory().addItems(rewardItems, ActionReason.TowerFloorStarReward);
    }
    return !rewardItems.isEmpty();
}
```

→ **星数赏金**：玩家积累星数到一定数量可领取（类似战令）。
→ `receivedStarBounty` Set 防重领。

### 11.3 双奖励 ActionReason

```
ActionReason.TowerFirstPassReward     // 初次通关 (per chamber)
ActionReason.TowerFloorStarReward      // 星数赏金 (per floor)
```

→ ActionReason 190+ 中两个专属（notes/38）。

---

## 12. 完整时序：一次深境挑战

```
[月初 4 点]
TowerScheduleConfig 重置:
   - 新 scheduleId
   - 9-12 楼新怪物配置
   - 新地脉异常

[玩家上线]
TowerManager.onLogin:
   1. 检测 scheduleId 变化
   2. startNewSchedule (清 9-12 楼记录)
   3. 如果 8 楼 ≥ 6 星 → 解锁 9 楼

[玩家进入深境界面]
   展示 TowerCurLevelRecord:
     - 当前楼层 / 室号
     - 星数
     - 双队配置
     - Buff 选项

[玩家选队]
teamSelect(floorId, [team1, team2]):
   TowerData.initCurLevelRecord (持有双队)
   
[玩家选地脉异常 buff]
addBuffs(towerBuffId):
   towerBuffs[currentLevelIndex] = buffId
   PlayerBuffManager.addBuff

[进入第 N 室]
enterLevel(enterPointId):
   DungeonSystem.enterDungeon (复用副本系统)
   rebuildAndUseTeam (临时队伍)
   PacketCanUseSkillNotify(false) (停技能)
   
[战斗中 - 上半场]
   isUpperPart = true
   用 team 1
   WorldChallenge 跑 (TimeTrigger / GuardTrigger 等)
   
[上半场完成]
Lua 触发 mirrorTeamSetUp(teamId=1):
   setUpperPart(false)
   切场景 (LuaSkipUi, 无缝)
   rebuildAndUseTeam(1) → 用 team 2
   
[战斗中 - 下半场]
   isUpperPart = false
   用 team 2
   
[下半场完成 (challenge.success)]
TowerDungeonSettleListener.onDungeonSettle:
   calculateStar(manager, challenge):
     遍历 3 cond × 上下半各
     count 满足的 cond
     stars = 0-6 (但通常 0-3)
   updateFloorRecord (新成绩 > 旧 → 更新)
   tryRemoveBuffs (移除 LEVEL/IMMEDIATE)
   notifyFloorChange
   
[领取奖励]
giveFirstPassReward (初次通关):
   按当前 star count 给奖励
   ActionReason.TowerFirstPassReward
   
玩家手动领星数赏金:
getStarReward(floorId):
   找出累积星数对应的奖励
   过滤已领的 (receivedStarBounty)
   addItems
   ActionReason.TowerFloorStarReward

[失败]
calculateStar:
   manager.isFinishedSuccessfully = false
   removeCurrentLevelBuff
   setUpperPart(true) (回上半重来)
   
[退出]
exitDungeon (notes/48):
   if (DUNGEON_TOWER) {
     removeCurrentLevelBuff
     clearTeamOnExit:
       PacketAvatarDelNotify (删除临时 TowerAvatar)
       cleanTemporaryTeam
     isQuitImmediately = true (不等延迟)
   }
   transferPlayerToScene 回原场景
```

→ **完整深境挑战 ≈ 10 阶段**。

---

## 13. 与 DungeonManager 的关系（呼应 notes/48）

| 维度 | Tower | Dungeon |
|---|---|---|
| 入口 | TowerManager.enterLevel | DungeonSystem.enterDungeon |
| 底层引擎 | 复用 DungeonSystem | 自己 |
| 监听器 | TowerDungeonSettleListener | BasicDungeonSettleListener |
| 临时队伍 | 双队 (上下半) | 试用角色 (notes/48) |
| 退出 | 立即 (无延迟) | failSettleCountdownTime |
| 奖励 | TowerFirstPassReward + TowerFloorStarReward | DungeonStatueDrop |
| Buff | 3 类持续 + addBuffs/removeBuffs | 无 |
| 持久化 | TowerData (per Player) | DungeonManager (per Scene, 临时) |
| 时间维度 | 月度循环 | 每日重置 |
| 星数 | 1-3 stars per chamber | 通关与否 (binary) |

→ Tower 是 Dungeon 的"**扩展层**" —— 但有自己的星数、双队、月度循环。

---

## 14. 与其他系统的联动

### 14.1 联动 BuffManager (notes/40)

```java
private void notifyAddBuffs(int towerBuffId) {
    Optional.ofNullable(GameData.getTowerBuffDataMap().get(towerBuffId))
        .ifPresent(towerBuffData -> this.player.getBuffManager().addBuff(towerBuffData.getBuffId()));
}
```

→ Tower buff → PlayerBuffManager.addBuff —— Tower 是 25 Manager 之一但**联动另一个 Manager** 实现 buff。

### 14.2 联动战令 (notes/22)

```java
// notes/48 副本完成时
if (this.dungeonData.getType().isCountsToBattlepass() && successfully) {
    p.getBattlePassManager().triggerMission(WatcherTriggerType.TRIGGER_FINISH_DUNGEON);
}
```

但 `DUNGEON_TOWER.countsToBattlepass = false`（notes/48）—— 深境**不计普通副本数**。

但有 Tower 专属 watcher：
- `TRIGGER_FINISH_TOWER_LEVEL` (304)
- `TRIGGER_DONE_TOWER_GADGET_UNHURT` (311)
- `TRIGGER_DONE_TOWER_STARS` (312)
- `TRIGGER_DONE_TOWER_UNHURT` (313)
- `TRIGGER_TOWER_STARS_NUM` (318)

→ **5 个 Tower 专属 WatcherTriggerType**（notes/41）—— 战令任务监听这些。

### 14.3 联动 TeamManager (notes/34)

```java
this.player.getTeamManager().setupTemporaryTeam(guidListList);
this.player.getTeamManager().useTemporaryTeam(teamId);
this.player.getTeamManager().cleanTemporaryTeam();
```

→ Tower 用 TeamManager 的"临时队伍"机制（notes/34 §4.1）。

---

## 15. 设计模式总结

### 15.1 双层架构（System + Manager）

```
TowerSystem (全局, 月度调度)
   ↓
TowerManager (per Player, 玩家进度)
```

→ 与 DungeonSystem + DungeonManager 一致（notes/48）。

### 15.2 持久化分离

```
TowerData (player 持久化)
TowerFloorRecordInfo (per floor)
TowerLevelRecordInfo (per chamber)
TowerTeamInfo (per team, 双队)
TowerMonthlyBriefInfo (月度简报)
```

→ **嵌套数据结构**：玩家 → 楼层 → 室 → 队伍。

### 15.3 临时副本机制

```
TowerAvatar = Avatar 的临时副本
addAvatar 加入临时 + clearTeamOnExit 时删除
```

→ 让玩家可在塔内调整角色而**不影响大世界**。

### 15.4 复用 + 加壳

```
Tower 复用 DungeonSystem
但用 TowerDungeonSettleListener 加深境特有结算逻辑
```

→ 经典的"装饰器模式"。

### 15.5 月度循环

```
scheduleId 改变 → onLogin 检测 → 重置高楼记录
```

→ 全自动月度刷新——无需手动重置。

---

## 16. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 客户端发"我 3 星了" | ✗ 服务器算 cond |
| 篡改 TowerData | ✗ 服务器存 |
| 跳过上半场进下半 | ✗ Lua 触发 |
| 重领星数赏金 | ✗ receivedStarBounty Set 防 |
| 重领首通奖励 | ✗ isReceivedFirstPassReward 防 |
| 用大世界 buff 进塔 | ✗ tryRemoveBuffs 清除 |
| 8 楼没 6 星进 9 楼 | ✗ canEnterScheduleFloor 检查 |

→ Tower 反作弊极强 —— 奖励、星数、进度全在服务器。

---

## 17. 关键收获

1. **Tower 是 endgame 核心**：12 楼 × 3 室 × 2 队 = 72 战斗场景，月度重置
2. **双层架构**：TowerSystem (89 行, 月度) + TowerManager (393 行, per Player) = 482 行核心
3. **10 个文件**支撑深境
4. **入口楼层 (1-8) + 周期楼层 (9-12)**：前者永久，后者每月重置
5. **6 星门槛**：8 楼 ≥ 6 星才能进 9 楼 (STAR_COUNT_TO_UNLOCK_SCHEDULE_FLOOR)
6. **双队配置**：teamOnHold[2] 上下半场各一队
7. **TowerAvatar 临时副本**：不影响大世界角色
8. **mirrorTeamSetUp 无缝切队**：Lua 触发 + LuaSkipUi (跳过黑屏)
9. **上下半场独立**：isUpperPart 标记 + 各 3 cond
10. **3 种 TowerCondType**：FINISH_TIME / LEFT_HP / CHALLENGE_LEFT_TIME (主流)
11. **3 种 TowerBuffLastingType**：FLOOR (整楼) / LEVEL (单层) / IMMEDIATE (一次性)
12. **星数永远取最高**：updateFloorRecord 只在 new > old 时更新
13. **双奖励系统**：FirstPassReward (初通) + StarBounty (累计赏金)
14. **进塔自动停技能**：PacketCanUseSkillNotify(false) 给 UI 时间
15. **复用 DungeonSystem**：Tower 用 TowerDungeonSettleListener 加壳
16. **5 个 Tower 专属 WatcherTriggerType** (notes/41)：FINISH_TOWER_LEVEL / DONE_TOWER_STARS / DONE_TOWER_UNHURT / TOWER_STARS_NUM
17. **月度自动刷新**：onLogin 检测 scheduleId 变化 → startNewSchedule
18. **TOWER 立即退出**（notes/48）：不走 delayExitTime
19. **3 种持续类型 Buff 移除策略**：完成层 → 移除 LEVEL/IMMEDIATE / 保留 FLOOR
20. **反作弊极强**：服务器全权计算

---

## 18. 一句话总结

> **Tower / 深境螺旋 = 双层架构 (TowerSystem 89 + TowerManager 393 = 482 行核心) × 12 楼 × 3 室 × 双队 × 上下半场 × 3 类持续 Buff × 月度循环; 入口楼层 (1-8) 永久 + 周期楼层 (9-12) 月度重置 + 6 星门槛进 9 楼; TowerAvatar 临时副本不影响大世界; 复用 DungeonSystem + TowerDungeonSettleListener 加壳; 双奖励 (FirstPass + StarBounty); 5 个 Tower 专属 WatcherTriggerType; 进塔无延迟退出 + 自动停技能.**
> 
> **设计哲学: 月度循环 + 上下半场 + 双队 + 临时副本 = 高 replayability; 复用副本引擎 + 加壳特有逻辑 = 代码复用; 临时数据不影响持久 = 玩家自由实验; 服务器算星数 + 防重领 = 反作弊. 这是 grasscutter 中"endgame 系统"的标准范式.**

---

**前置笔记**：
- notes/19 副本设计 - DUNGEON_TOWER 类型
- notes/34 EntityAvatar - TemporaryTeam 临时队伍机制
- notes/40 Player Manager - towerManager + buffManager 联动
- notes/41 事件总线 - 5 个 TRIGGER_DONE_TOWER_* 专属 watcher
- notes/48 副本运行时 - DungeonSystem 复用 + TOWER 立即退出

**关联文件**：
- `TowerSystem.java`(89) - 全局月度调度
- `TowerManager.java`(393) - per Player 运行时
- `TowerData.java`(~200) - 持久化
- `TowerScheduleConfig.java` - TowerSchedule.json
- `TowerFloorRecordInfo.java` / `TowerLevelRecordInfo.java` - 记录
- `TowerTeamInfo.java`(51) - 双队
- `TowerMonthlyBriefInfo.java` - 月度简报
- `TowerCondType.java`(8) - 3 种通关条件
- `TowerBuffLastingType.java`(8) - 3 种 Buff 持续
- `TowerDungeonSettleListener.java` (notes/48) - 深境结算监听

**研究的源代码**: 889 行 Tower 核心 + DungeonSystem 复用 + 5 WatcherTriggerType 引用。
