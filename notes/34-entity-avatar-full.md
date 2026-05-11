# EntityAvatar 玩家实体深度剖析

> 第 34 篇：完成"三大实体"三部曲 (Monster / Gadget / Avatar)。玩家角色在场景里的运行时镜像——和持久化 Avatar 是两个完全不同的东西。

---

## 0. 为什么这一篇重要

notes/24 讲了 **Avatar (持久化对象)** —— 7 层属性叠加 / 命座 / 武器 / 圣遗物。

但**玩家在场景里跑动战斗的那个东西**叫 **EntityAvatar**——是另一个对象。它和 Avatar 的关系类似"账号 vs 进程"：
- Avatar = 你硬盘里的角色档案
- EntityAvatar = 你登录后 RAM 里跑的实例

这一篇专门挖：
1. Avatar vs EntityAvatar **双层模型**为什么这么设计
2. EntityAvatar 在场景中的**生命周期** (上场/切换/死亡)
3. 它怎么和 **TeamManager.activeTeam** / **currentAvatarEntity** 串起来
4. 玩家位置/旋转**居然不在 entity 上**——为什么？
5. **元素能量** 怎么作为 fightProp 存
6. **5 个能力源**怎么拼装成 AbilityControlBlock
7. 与 Monster/Gadget 的**关键差异**
8. **TrialAvatar 试用角色** 的运行时机制

---

## 1. 双层模型：Avatar vs EntityAvatar

```
[持久化层]                       [运行时层]                   [场景层]
                                                              
Avatar (notes/24)               EntityAvatar                  Scene
  - id, name                      - id (场景内 entity_id)         - addEntity(entity)
  - level, exp                    - avatar: Avatar  ← 引用持久化对象
  - skillDepot                    - killedType, killedBy
  - constellation                 - weaponEntity        ── 引用 ──→  EntityWeapon
  - equips (武器/圣遗物)            - position (从 player 取)
  - fightProperties              - rotation (从 player 取)
  - guid (持久 ID)
       ↑                              ↓
       └─────────── 一对一 ────────────┘ (战斗中)
       
   存 MongoDB                    存内存 (登出消失)
```

### 1.1 为什么要双层

- **Avatar 持久化**：等级/装备/经验/命座/突破——这些**永久属于玩家**
- **EntityAvatar 临时**：HP/位置/旋转/能量/死亡状态/能力槽位——这些**只在世界中有意义**

**优势**：
- ✓ 切角色不重新查 DB（Avatar 已加载）
- ✓ 死亡只清 EntityAvatar，Avatar 数据不丢
- ✓ 进入新场景 EntityAvatar 重建，Avatar 不变
- ✓ 联机时**别人的角色**用同样的 Avatar 数据创建 EntityAvatar，简单

### 1.2 引用链

```java
Avatar.getPlayer() → Player          // 反向引用
EntityAvatar.getAvatar() → Avatar    // 持有持久对象
EntityAvatar.getPlayer() {           // 通过 Avatar 间接获取
    return this.avatar.getPlayer();
}
EntityAvatar.getPosition() {         // 实际从 Player 拿!
    return getPlayer().getPosition();
}
```

→ **位置不存在 EntityAvatar，存在 Player 上**！这是关键设计——见 §5。

---

## 2. EntityAvatar 字段（5 个核心）

`EntityAvatar.java` 318 行，但实际字段只有 5 个：

```java
public class EntityAvatar extends GameEntity {
    @Getter private final Avatar avatar;       // ← 持久化对象引用
    @Getter private PlayerDieType killedType;  // ← 死法（被怪/坠落/淹死）
    @Getter private int killedBy;              // ← 击杀者 entity ID
    // 没有 position!
    // 没有 rotation!  
    // 没有 fightProperties (走 avatar.getFightProperties())
}
```

**对比 EntityMonster (32 个字段)**：
- Monster 有自己的 position / rotation / fightProperties / aiId / poseId 等
- Avatar **全部委托给** `avatar` 引用 + `player`

这种"组合优于继承"的极简设计是 Avatar 的独特之处。

---

## 3. PlayerDieType：6 种死法

```java
public enum PlayerDieType {
    PLAYER_DIE_NONE,
    PLAYER_DIE_KILL_BY_MONSTER,    // 被怪打死
    PLAYER_DIE_KILL_BY_GEAR,       // 被陷阱/机关打死
    PLAYER_DIE_FALL,               // 摔死 (服务器算)
    PLAYER_DIE_DRAWN,              // 淹死
    PLAYER_DIE_GM,                 // GM 命令
    PLAYER_DIE_CLIMB_NO_STAMINA,   // 攀爬体力耗尽掉死
    ...
}
```

### 3.1 不同死法的处理

```java
@Override
public void onDeath(int killerId) {
    super.onDeath(killerId);
    this.killedType = PlayerDieType.PLAYER_DIE_KILL_BY_MONSTER;  // 默认
    this.killedBy = killerId;
    clearEnergy(ChangeEnergyReason.CHANGE_ENERGY_NONE);
}

public void onDeath(PlayerDieType dieType, int killerId) {   // 重载
    super.onDeath(killerId);
    this.killedType = dieType;        // ← 明确指定
    this.killedBy = killerId;
    clearEnergy(ChangeEnergyReason.CHANGE_ENERGY_NONE);
}
```

**特殊处理**：
- **PLAYER_DIE_FALL** —— 服务器算（不信任客户端的"我从悬崖摔死"）
- **PLAYER_DIE_DRAWN** —— 服务器检测水深 + 时间
- **PLAYER_DIE_GM** —— /kill 命令

→ notes/16 提到"摔伤伤害服务器算"——这里看到落地。客户端可以传"我摔了"，但**死亡判定在服务器**。

---

## 4. 上场机制：TeamManager → activeTeam

### 4.1 三种队伍模式

`TeamManager.java` 第 92-103 行：
```java
public TeamInfo getCurrentTeamInfo() {
    if (isUseTrialTeam())                  return getTrialAvatarTeam();   // ★ 试用角色
    if (getTemporaryTeamIndex() >= 0 && ...) return getTemporaryTeam().get(...); // ★ 深境螺旋
    if (getPlayer().isInMultiplayer())      return getMpTeam();             // ★ 联机
    return getCurrentSinglePlayerTeamInfo();                                // 默认单机
}
```

**4 套队伍体系并行存在**：
| 模式 | TeamInfo 数据源 | 用途 |
|---|---|---|
| 单机 | `teams[currentTeamId]` | 4 套队伍配置 |
| 联机 | `mpTeam` | 联机时的队伍 |
| 试用 | `trialAvatarTeam` | 剧情试用 / 活动试用 |
| 临时 | `temporaryTeam[index]` | 深境螺旋上下半 |

切换时同一个 TeamManager 路由到不同 TeamInfo——**Player 不知道 team 切了**，照样调 `getCurrentAvatarEntity()`。

### 4.2 activeTeam：4 个 EntityAvatar

```java
@Transient private final List<EntityAvatar> activeTeam;   // ← 上场的 4 个
```

- 单机：最多 4 人
- 联机：人数 / 玩家数（4 人组队每人 1 个）

`getMaxTeamSize()` (`TeamManager.java:136-145`)：
```java
if (getPlayer().isInMultiplayer()) {
    int teamSize = GAME_OPTIONS.avatarLimits.multiplayerTeam / getWorld().getPlayerCount();
    return Math.max(1, getPlayer().getWorld().getHost() == getPlayer() ?
        (int) Math.floor(teamSize) : (int) Math.ceil(teamSize));
}
return GAME_OPTIONS.avatarLimits.singlePlayerTeam;
```

→ **房主 floor / 其他玩家 ceil** —— 4 人联机时房主 1 个角色，其他人各 1 个；3 人时房主 1 个、其他人 1 或 2 个不均匀分。

### 4.3 当前角色：getCurrentAvatarEntity()

```java
public EntityAvatar getCurrentAvatarEntity() {
    if (getActiveTeam().isEmpty()) {
        val mainChar = player.getAvatars().getAvatars().get(player.getMainCharacterId());
        if (mainChar == null) return null;
        addAvatarToCurrentTeam(mainChar);   // 兜底: 加主角
    }
    if (getActiveTeam().isEmpty() || getCurrentCharacterIndex() < 0) return null;
    if (getCurrentCharacterIndex() >= getActiveTeam().size()) return getActiveTeam().get(0);
    return getActiveTeam().get(getCurrentCharacterIndex());
}
```

→ 这是 grasscutter 中**被调用最多的方法之一**——任何要"玩家正在控制的角色"都用它。

### 4.4 切角色

`TeamManager.java:427+`：
```java
EntityAvatar oldEntity = getCurrentAvatarEntity();
// ...
EntityAvatar newEntity = (index == -1) ? null : getActiveTeam().get(index);
```

切角色**不重建 EntityAvatar**——只改 `currentCharacterIndex`。所有 4 个 EntityAvatar **同时存在**于场景，只是"正在控制"的是其中一个。

→ 这就是为什么按 1234 切角色**瞬间响应**——它们都已经在场景里加载好了。

---

## 5. 位置不在 entity 上：所有角色共享 player 位置

```java
@Override
public Position getPosition() {
    return getPlayer().getPosition();   // ★ 从 Player 拿
}

@Override
public Position getRotation() {
    return getPlayer().getRotation();
}
```

### 5.1 为什么这样设计

**直觉**：每个角色应该有自己的位置吧？切角色应该看到原地变身？

**实际**：**玩家是一个"占位"，4 个角色共享这个占位**：
- 切角色时**位置不变** —— 因为是同一个 player.position
- 但**视觉上看着"另一个人"** —— 客户端切了模型
- 4 个 EntityAvatar 在**逻辑上同点**

**好处**：
- ✓ 切角色**零延迟** —— 不需要同步 4 个位置
- ✓ 联机视野同步**简单** —— 只发 1 个位置代表 4 个角色
- ✓ 大招特效**位置一致** —— 切人放大招在原地

**视觉处理**：
- 客户端**只渲染当前角色**
- 其他 3 个 EntityAvatar 在内存中**但不显示**
- 切换时客户端做"替换动画"

### 5.2 联机时的奇观

```
[联机 4 人]
玩家 A: position=(10, 0, 20), activeTeam=[迪卢克]
玩家 B: position=(50, 0, 60), activeTeam=[甘雨]
玩家 C: position=(30, 0, 40), activeTeam=[钟离]
玩家 D: position=(70, 0, 80), activeTeam=[万叶]
```

→ 共 4 个 EntityAvatar，分散在场景 4 个不同位置——但**每个 player 都只有 1 个位置**。

**注意**：如果某玩家有多个 EntityAvatar（多人队的小队），它们的位置仍然来自同一 player。

---

## 6. 战斗属性：从 Avatar.fightProperties 拿

```java
@Override
public Int2FloatMap getFightProperties() {
    return getAvatar().getFightProperties();   // ★ 委托给 Avatar
}
```

→ EntityAvatar **不存自己的 fightProperties**，全部走 Avatar。这意味着：
- 升级角色 → Avatar 重算 → EntityAvatar 自动看到新值
- 换装备 → Avatar 更新 fightProperties → 战斗实时生效
- 死亡 → Avatar HP 改成 0（实际改的是 fightProperty）→ EntityAvatar 看到

### 6.1 healing 路径

```java
@Override
public float heal(float amount, boolean mute) {
    if (!this.isAlive()) return 0f;   // 死人不能加血
    
    float healed = super.heal(amount, mute);   // 父类 GameEntity.heal
    //              ↑ 实际改 avatar.fightProperties[CUR_HP]
    
    if (healed > 0f) {
        getScene().broadcastPacket(
            new PacketEntityFightPropChangeReasonNotify(
                this, FightProperty.FIGHT_PROP_CUR_HP, healed, ...));
        //                                 ↑ 通知所有客户端
    }
    return healed;
}
```

**死人不能复活**是这里的硬约束——`!isAlive() return 0`。

---

## 7. 元素能量：作为 fightProp 存

### 7.1 设计

```java
public void addEnergy(float amount, PropChangeReason reason, boolean isFlat) {
    val elementType = this.getAvatar().getSkillDepot().getElementType();
    val curEnergyProp = elementType.getCurEnergyProp();  // ← 元素相关 prop
    val maxEnergyProp = elementType.getMaxEnergyProp();
    
    float curEnergy = this.getFightProperty(curEnergyProp);
    float maxEnergy = this.getFightProperty(maxEnergyProp);
    
    // 充能效率影响 (非平摊)
    if (!isFlat) {
        amount *= this.getFightProperty(FightProperty.FIGHT_PROP_CHARGE_EFFICIENCY);
    }
    
    float newEnergy = Math.min(curEnergy + amount, maxEnergy);
    
    if (newEnergy != curEnergy) {
        this.avatar.setCurrentEnergy(curEnergyProp, newEnergy);
        this.getScene().broadcastPacket(new PacketEntityFightPropChangeReasonNotify(...));
    }
}
```

### 7.2 8 种元素能量 prop

```
ElementType.Fire    → FIGHT_PROP_CUR_FIRE_ENERGY   / FIGHT_PROP_MAX_FIRE_ENERGY
ElementType.Water   → FIGHT_PROP_CUR_WATER_ENERGY  / FIGHT_PROP_MAX_WATER_ENERGY
ElementType.Grass   → FIGHT_PROP_CUR_GRASS_ENERGY  / FIGHT_PROP_MAX_GRASS_ENERGY
ElementType.Electric→ FIGHT_PROP_CUR_ELEC_ENERGY   / FIGHT_PROP_MAX_ELEC_ENERGY
ElementType.Wind    → FIGHT_PROP_CUR_WIND_ENERGY   / FIGHT_PROP_MAX_WIND_ENERGY
ElementType.Ice     → FIGHT_PROP_CUR_ICE_ENERGY    / FIGHT_PROP_MAX_ICE_ENERGY
ElementType.Rock    → FIGHT_PROP_CUR_ROCK_ENERGY   / FIGHT_PROP_MAX_ROCK_ENERGY
```

→ 每个元素**独立的能量条**，存在 fightProperties Map 里。

**优势**：
- 一个角色换元素 (旅行者) → 直接换 prop key
- 7 种元素能量并存 → 不需要类型推断
- max 和 cur 分开 → 充能效率影响 cur 不影响 max

### 7.3 充能效率 (CHARGE_EFFICIENCY)

```java
if (!isFlat) {
    amount *= this.getFightProperty(FightProperty.FIGHT_PROP_CHARGE_EFFICIENCY);
}
```

→ 圣遗物副词条 "元素充能效率 +6.5%" 直接乘在 amount 上。
→ 这就是为什么"充能流"配队需要堆 ER。

### 7.4 大招消耗

```java
public void clearEnergy(ChangeEnergyReason reason) {
    val curEnergyProp = this.getAvatar().getSkillDepot().getElementType().getCurEnergyProp();
    float curEnergy = this.getFightProperty(curEnergyProp);
    
    this.avatar.setCurrentEnergy(curEnergyProp, 0);   // 直接置零
    
    this.getScene().broadcastPacket(new PacketEntityFightPropUpdateNotify(this, curEnergyProp));
    
    if (reason == ChangeEnergyReason.CHANGE_ENERGY_SKILL_START) {
        this.getScene().broadcastPacket(new PacketEntityFightPropChangeReasonNotify(...));
    }
}
```

**注意**：放大招**直接清零** —— 不管你之前是 80 还是 200（满），都归 0。
所以"溢出能量"是浪费的 —— 这就是为什么充满了立刻放招。

---

## 8. AbilityControlBlock：5 个能力源

`EntityAvatar.getAbilityControlBlock()` 第 269-297 行：

```java
public AbilityControlBlock getAbilityControlBlock() {
    AvatarData data = this.getAvatar().getAvatarData();
    val embrioList = new ArrayList<AbilityEmbryo>();
    
    // ===== 来源 1: AvatarData 内置能力 =====
    val abilities = data.getAbilities();
    if (abilities != null) {
        embrioList.addAll(abilities.stream().map(id -> new AbilityEmbryo(...)).toList());
    }
    
    // ===== 来源 2: 默认能力 (奔跑/跳跃/攀爬) =====
    embrioList.addAll(Arrays.stream(GameConstants.DEFAULT_ABILITY_HASHES)
        .mapToObj(id -> new AbilityEmbryo(...)).toList());
    
    // ===== 来源 3: 队伍共鸣 (双火/双冰/4元素等) =====
    embrioList.addAll(this.getPlayer().getTeamManager().getTeamResonancesConfig()
        .stream().map(id -> new AbilityEmbryo(...)).toList());
    
    // ===== 来源 4: 技能组能力 (元素战技/爆发) =====
    AvatarSkillDepotData skillDepot = GameData.getAvatarSkillDepotDataMap().get(...);
    if (skillDepot != null && skillDepot.getAbilities() != null) {
        embrioList.addAll(skillDepot.getAbilities().stream().map(id -> ...).toList());
    }
    
    // ===== 来源 5: 装备额外能力 (圣遗物 4 件套 / 武器被动) =====
    if (this.getAvatar().getExtraAbilityEmbryos().size() > 0) {
        embrioList.addAll(this.getAvatar().getExtraAbilityEmbryos()
            .stream().map(id -> new AbilityEmbryo(..., Utils.abilityHash(id), ...)).toList());
    }
    
    abilityControlBlock.setAbilityEmbryoList(embrioList);
    return abilityControlBlock;
}
```

### 8.1 5 个能力源全图

| 来源 | 内容 | 例子 |
|---|---|---|
| 1. AvatarData abilities | 角色固有能力 | 命之座修改、被动天赋 |
| 2. DEFAULT_ABILITY_HASHES | 通用动作 | 奔跑、跳跃、攀爬、游泳 |
| 3. TeamResonances | 队伍共鸣 | 双火 +25% ATK / 双水 +25% HP |
| 4. SkillDepot abilities | 元素技能 | E 技能 + Q 技能 + 普攻 |
| 5. ExtraAbilityEmbryos | 装备被动 | 武器被动 + 圣遗物 4 件套 |

→ **5 路并入一个 ControlBlock**，发给客户端。客户端拿到后注册所有能力，能用任何一个触发。

### 8.2 共鸣的特殊性

`updateTeamResonances()` (`TeamManager.java:182-212`)：
```java
// 单元素 2+ 个 → 触发共鸣
elementCounts.object2IntEntrySet().stream()
    .filter(e -> e.getIntValue() >= 2)
    .map(Map.Entry::getKey)
    .filter(elementType -> elementType.getTeamResonanceId() != 0)
    .forEach(elementType -> {
        getTeamResonances().add(elementType.getTeamResonanceId());
        getTeamResonancesConfig().add(elementType.getConfigHash());
    });

// 4 种不同元素 → 元素共鸣 (其他万象/无相元素 +15%)
if (elementCounts.size() >= 4) {
    getTeamResonances().add(ElementType.Default.getTeamResonanceId());
    ...
}
```

**关键**：「全队 4 人**满员**」才触发共鸣（`activeTeam.size() < 4` 早返回）。

→ 这就是为什么"3 人队"打不开共鸣 buff——必须满 4 人。

---

## 9. 武器实体绑定

```java
public EntityAvatar(Scene scene, Avatar avatar) {
    super(scene);
    this.avatar = avatar;
    this.avatar.setCurrentEnergy();
    if (scene != null) {
        this.id = getScene().getWorld().getNextEntityId(EntityIdType.AVATAR);
        
        GameItem weapon = this.getAvatar().getWeapon();
        if (weapon != null) {
            if (!(weapon.getWeaponEntity() != null && weapon.getWeaponEntity().getScene() == scene)) {
                val weaponCreateConfig = new CreateGadgetEntityConfig(weapon.getItemData().getGadgetId());
                weapon.setWeaponEntity(new EntityWeapon(getPlayer().getScene(), weaponCreateConfig));
                scene.getWeaponEntities().put(weapon.getWeaponEntity().getId(), weapon.getWeaponEntity());
            }
        }
    }
}
```

### 9.1 EntityWeapon 是独立 entity

和怪物的武器一样（notes/32 §6.3），**玩家武器也是独立实体**：
- 自己的 entityId
- 在 scene.weaponEntities 注册
- 可以被磁吸/夺取（理论上）
- 武器特效/光效绑定到这个 entity

### 9.2 重用机制

```java
if (!(weapon.getWeaponEntity() != null && weapon.getWeaponEntity().getScene() == scene)) {
    // ↑ 当前 weaponEntity 不在本 scene → 创建新的
}
```

→ 同一武器**跨场景换 weaponEntity**——避免上一个场景的实体污染新场景。

---

## 10. TrialAvatar：试用角色

`TeamManager.java:352`：
```java
EntityAvatar trialEntity = new EntityAvatar(getPlayer().getScene(), trialAvatar);
```

### 10.1 试用机制

试用角色出现在：
- 剧情中"使用 XX 完成任务" → 自动加入队伍
- 角色 PV / 试用活动
- 限时活动「角色试用」

**和正式角色的区别**：
- 试用 Avatar 是**临时生成**（不在玩家 avatarStorage）
- TrialAvatarTeam 单独维护
- 退出试用后 EntityAvatar 销毁

### 10.2 切换试用模式

```java
@Transient @Setter private boolean useTrialTeam;
@Transient @Setter private TeamInfo trialAvatarTeam;
@Transient @Setter private int previousIndex = -1;  // ← 试用前的索引
```

进入试用：
1. 记录 `previousIndex = currentCharacterIndex`
2. 创建 `trialAvatarTeam`
3. `useTrialTeam = true`
4. `updateTeamEntities` 重建 activeTeam → 试用角色取代

退出：
1. `useTrialTeam = false`
2. `currentCharacterIndex = previousIndex`
3. `updateTeamEntities` 恢复原队

→ **既保留原队信息又能切换试用**——典型的"暂存恢复"模式。

---

## 11. 移动事件 + 反作弊机会

```java
@Override
public void move(Position newPosition, Position rotation) {
    PlayerMoveEvent event = new PlayerMoveEvent(
        this.getPlayer(), PlayerMoveEvent.MoveType.PLAYER,
        this.getPosition(), newPosition);
    event.call();   // ← plugin 可以拦截 / 修改 destination
    
    super.move(event.getDestination(), rotation);
}
```

### 11.1 PlayerMoveEvent 钩子

Plugin 可以监听移动事件：
- 反作弊检测（速度过快 / 飞天 / 穿墙）
- 边界检测（不让玩家走出地图）
- 记录玩家轨迹（运营分析）

```java
// Plugin 示例
@EventHandler
public void onPlayerMove(PlayerMoveEvent event) {
    Position from = event.getFrom();
    Position to = event.getDestination();
    float distance = from.computeDistance(to);
    
    if (distance > MAX_MOVE_PER_TICK) {
        event.setCancelled(true);   // 拒绝异常移动
    }
}
```

### 11.2 但默认无反作弊

Grasscutter 默认**不验证移动合法性**——玩家可以瞬移到任何坐标，没人拦着。
→ 这是私服的设计选择，公开运营时必须加。

---

## 12. 三大实体对比表

| 维度 | EntityAvatar | EntityMonster | EntityGadget |
|---|---|---|---|
| **代码行** | 318 | 367 | 310 |
| **运行时字段** | 5 (极简) | 32+ | 10 |
| **类型枚举** | 1 (PROT_ENTITY_AVATAR) | 7 MonsterType | 30+ EntityType |
| **位置存储** | 在 Player 上 (共享) | 在 entity 上 | 在 entity 上 |
| **属性存储** | 在 Avatar 上 (持久) | 在 entity 上 (临时) | 在 entity 上 (大多无) |
| **AI** | 无 (玩家手动) | 客户端 host 跑 | 无 (被动等交互) |
| **联机权威** | 各自的 owner | host 共控所有 | host (除 ClientGadget) |
| **能力来源** | 5 路汇总 | configEntityMonster + affix | configEntityGadget |
| **典型生命周期** | 玩家在线全程 | spawn → 战死 → 重生 | spawn → 交互 → 消失 |
| **持久化** | 部分 (Avatar 存) | 死亡列表 + group instance | 状态缓存 + group instance |
| **数量** | 1-4 × player_count | 5-50 | 100-500 |
| **核心交互** | move / skill / hit | damage | onInteract |

→ **三大实体哲学差异**：
- **Avatar**: "极简内核 + 委托外引用"——位置/属性都在外面
- **Monster**: "完整自治体"——所有状态都在身上
- **Gadget**: "策略模板"——Entity 是壳，Content 决定行为

---

## 13. 完整 toProto 数据流

`EntityAvatar.toProto()` 给客户端的内容：

```java
@Override
public SceneEntityInfo toProto() {
    val entityInfo = new SceneEntityInfo(ProtEntityType.PROT_ENTITY_AVATAR, getId());
    entityInfo.setAnimatorParaList(...);
    entityInfo.setEntityClientData(...);
    entityInfo.setEntityAuthorityInfo(authority);
    entityInfo.setLastMoveSceneTimeMs(...);    // 移动时间戳
    entityInfo.setLastMoveReliableSeq(...);    // 移动序列号
    entityInfo.setLifeState(this.getLifeState().getValue());
    entityInfo.setMotionInfo(this.getMotionInfo());
    this.addAllFightPropsToEntityInfo(entityInfo);   // 所有 fightProp
    
    // PROP_LEVEL
    entityInfo.setPropList(List.of(new PropPair(PROP_LEVEL.getId(), ...)));
    
    // SceneAvatarInfo (包含 talent / skillLevel / 圣遗物 / 武器)
    entityInfo.setEntity(new SceneEntityInfo.Entity.Avatar(this.getSceneAvatarInfo()));
    return entityInfo;
}
```

### 13.1 SceneAvatarInfo：客户端展示用

```java
public SceneAvatarInfo getSceneAvatarInfo() {
    val avatarInfo = new SceneAvatarInfo(player.getUid(), avatar.getAvatarId(), 
        avatar.getGuid(), player.getPeerId());
    avatarInfo.setTalentIdList(avatar.getTalentIdList());              // 命之座
    avatarInfo.setCoreProudSkillLevel(avatar.getCoreProudSkillLevel()); // 突破等级
    avatarInfo.setSkillLevelMap(avatar.getSkillLevelMap());            // 技能等级
    avatarInfo.setSkillDepotId(avatar.getSkillDepotId());
    avatarInfo.setInherentProudSkillList(avatar.getProudSkillList());  // 天赋
    avatarInfo.setProudSkillExtraLevelMap(avatar.getProudSkillBonusMap()); // 命座加天赋等级
    avatarInfo.setTeamResonanceList(player.getTeamManager().getTeamResonances());
    avatarInfo.setWearingFlycloakId(avatar.getFlyCloak());             // 风之翼
    avatarInfo.setCostumeId(avatar.getCostume());                       // 服装
    avatarInfo.setBornTime(avatar.getBornTime());                       // 创建时间
    avatarInfo.setWeaponSkinId(avatar.getWeaponSkin());                 // 武器皮肤
    
    // 装备
    val reliquaryList = new ArrayList<SceneReliquaryInfo>();
    val equipList = new ArrayList<Integer>();
    for (GameItem item : avatar.getEquips().values()) {
        if (item.getItemData().getEquipType() == EquipType.EQUIP_WEAPON) {
            var weapon = item.createSceneWeaponInfo();
            weapon.setWeaponSkinId(avatar.getWeaponSkin());
            avatarInfo.setWeapon(weapon);
        } else {
            reliquaryList.add(item.createSceneReliquaryInfo());   // 圣遗物
        }
        equipList.add(item.getItemId());
    }
    avatarInfo.setReliquaryList(reliquaryList);
    avatarInfo.setEquipIdList(equipList);
    return avatarInfo;
}
```

→ **客户端需要的所有信息**：等级 / 命之座 / 天赋 / 武器 / 圣遗物 / 服装 / 风之翼 / 共鸣 / 武器皮肤——一次性下发。

---

## 14. EntityNPC：第四类实体（附录）

NPC 是另一种简单实体：
```java
public class EntityNPC extends GameEntity<CreateNpcEntityConfig> {
    private final int npcId;
    private final int roomId;
    private final int questId;
    // 没有 fightProperties (NPC 不能打)
    // 没有 onInteract (走 Talk 系统)
}
```

NPC 和 Avatar/Monster/Gadget 平级，但**几乎没什么逻辑** —— 只是个"对话锚点"，所有交互走 Talk 系统 (notes/04)。

---

## 15. 关键收获

1. **双层模型**：Avatar (持久化) + EntityAvatar (运行时) —— 类似 "档案 + 进程"
2. **EntityAvatar 极简**：只有 5 个字段，全部委托给 avatar 引用
3. **位置在 Player 上**：4 个 EntityAvatar 共享 player.position —— 切角色零延迟，联机 host 同步简化
4. **4 套队伍并行**：单机 / 联机 / 试用 / 临时 (深境螺旋)
5. **联机队伍人数公式**：multiplayerTeam / playerCount，房主 floor 其他玩家 ceil
6. **6 种死法**：被怪 / 陷阱 / 摔死 / 淹死 / GM / 爬死
7. **元素能量作为 fightProp**：每元素一组 CUR/MAX，充能效率乘 amount
8. **大招清零**：满了立即放招否则浪费溢出
9. **5 个能力源**：AvatarData / Default / Resonance / SkillDepot / Equip → 一个 AbilityControlBlock
10. **共鸣需满 4 人**：单元素 2+ 或 全 4 元素 才触发
11. **武器是独立 entity**：EntityWeapon 跨场景重建
12. **TrialAvatar 试用机制**：暂存 previousIndex，退出恢复
13. **PlayerMoveEvent 钩子**：反作弊接口（默认无验证）
14. **SceneAvatarInfo 是客户端的"角色名片"**：等级/命座/天赋/装备/服装/共鸣全包

---

## 16. 一句话总结

> **EntityAvatar = 玩家角色在场景里的运行时镜像，5 字段极简委托给 Avatar (持久化)；4 个 EntityAvatar 共享 player.position 实现零延迟切角色；元素能量作为 fightProp 存，5 个能力源拼成 AbilityControlBlock；与 Monster (自治) 和 Gadget (策略模板) 形成"三大实体三种哲学"。**
> 
> **设计哲学：极简内核 + 大量外部引用——Avatar 数据复用、位置位置共享、属性委托，使切角色/换装备/进场景都不需要重建 EntityAvatar，只是引用关系变化。**

---

**三大实体三部曲完成**：
- notes/32 怪物系统全景 (EntityMonster - 自治体)
- notes/33 Gadget 系统全景 (EntityGadget - 策略模板)
- notes/34 EntityAvatar 玩家实体 (EntityAvatar - 极简委托) ← 本篇

**前置笔记**：
- notes/24 Avatar 升级系统 (持久化 Avatar 7 层属性)
- notes/16 战斗系统 (混合权威)
- notes/19 多人协作 (队伍人数公式)

**关联文件**：
- `EntityAvatar.java`(318) - 主实体类
- `Avatar.java`(notes/24) - 持久化对象
- `TeamManager.java`(607) - 队伍管理 + activeTeam
- `EntityWeapon.java` - 武器实体
- `EntityNPC.java`(76) - NPC 实体 (附录)
- `PlayerDieType.java` - 6 种死法枚举
- `ElementType.java` - 元素 → fightProp 映射

**研究的源代码**: 700+ 行 EntityAvatar + TeamManager 相关代码。
