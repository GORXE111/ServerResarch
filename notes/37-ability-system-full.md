# Ability 系统全景剖析

> 第 37 篇：被引用 10+ 次但从未真正打开的"黑盒" —— 命座/武器被动/圣遗物 4 件套/角色技能/元素反应/怪物词条**共享同一套引擎**。

---

## 0. 为什么这一篇重要

前面笔记里 Ability 系统**到处出现**但没专门解剖：
- notes/16 战斗：`AbilityManager` 是 4 大同构系统之一
- notes/24 Avatar：通过 `extraAbilityEmbryos` 加圣遗物 4 件套效果
- notes/27 架构模式：第 4 次"注解+反射"出现 `@AbilityAction`
- notes/32 怪物：通过 `MonsterAffix.abilityName` 给词条
- notes/34 EntityAvatar：5 路能力源拼成 `AbilityControlBlock`
- notes/36 战斗数学：`recalcStats` 通过 ability 加 fightProp

但**它到底是怎么工作的**？为什么角色技能 / 套装效果 / 怪物词条 **都走它**？这一篇打开这个核心黑盒。

---

## 1. Ability 是什么：游戏行为的统一表达

### 1.1 一个 Ability 描述一段行为

**例 1: 角色 E 技能**
```yaml
ability_name: "Avatar_Diluc_FireElementalShoot"
modifiers: [...]              # 触发的临时状态
abilitySpecials:              # 数值参数
  damage_ratio: 1.5
  cd: 12.0
mixins: [...]                 # 复合行为
```

**例 2: 圣遗物 4 件套**
```yaml
ability_name: "GrandConductor_4Set"
modifiers:
  add_atk:
    duration: 10
    add_props: {ATTACK_PERCENT: 0.25}
```

**例 3: 怪物词条**
```yaml
ability_name: "Affix_PyroSlime_Boost"
modifiers:
  - fire_immune: {FIRE_SUB_HURT: 0.999}
```

**所有这些**都是 Ability，都跑在同一引擎里。

### 1.2 Ability 数据来源

```
AbilityData (BinOutput/Ability/*.json)
   ↑
   - 角色 (AvatarData.abilities)
   - 角色技能 (SkillDepotData.abilities)
   - 武器精炼 (EquipAffix.openConfig)
   - 圣遗物套装 (ReliquarySet.openConfig)
   - 命之座 (AvatarTalent.openConfig)
   - 怪物词条 (MonsterAffix.abilityName)
   - 默认动作 (DEFAULT_ABILITY_HASHES: 跑/跳/爬/游)
```

→ **统一的数据格式**，统一的运行引擎。

---

## 2. AbilityData 数据结构

```java
public class AbilityData {
    public String abilityName;                       // 唯一标识
    public Map<String, AbilityModifier> modifiers;   // 修饰器（持续状态）
    public Map<String, Number> abilitySpecials;      // 数值参数 (damage ratio, cd, ...)
    public Map<String, AbilityMixinData> mixins;     // 混入行为
    public Map<Integer, AbilityModifierAction> localIdToAction;
    public Map<Integer, AbilityMixinData> localIdToMixin;
}
```

**4 个核心概念**：
| 概念 | 含义 |
|---|---|
| **abilitySpecials** | 数值参数（伤害倍率/CD/范围/能量）|
| **modifiers** | 临时修饰器（buff/debuff/护盾/无敌）|
| **actions** | 一次性行为（治疗/扣血/产生元素球/创建 gadget）|
| **mixins** | 混入行为（消耗体力/动画事件钩子）|

### 2.1 abilitySpecials 的用途

```java
public class Ability {
    @Getter private Object2FloatMap<String> abilitySpecials = new Object2FloatOpenHashMap<>();
    
    public Ability(AbilityData data, ...) {
        if (this.data.abilitySpecials != null)
            for (var entry : this.data.abilitySpecials.entrySet())
                abilitySpecials.put(entry.getKey(), entry.getValue().floatValue());
    }
}
```

`abilitySpecials` 是 ability **运行时可改的数值**：
- 客户端发 `ABILITY_META_OVERRIDE_PARAM` 改某个 special
- 例：迪卢克命座 6 把 `damage_ratio` 从 1.5 改成 2.0
- 例：玛拉妮的 ult 提升 5% 时把 `aoe_radius` 从 4 改成 5

→ 这就是为什么"命座 6 改伤害"在客户端能即时生效 —— 改的是 `abilitySpecials`，客户端公式按这个算。

### 2.2 modifiers：临时状态

最常见的概念：
```yaml
modifier_xxx:
  duration: 10        # 持续 10 秒
  stacking: Unique    # 只能存在一个
  modifierName: ...
  onAdded: [...]      # 添加时触发的 actions
  onRemoved: [...]    # 移除时触发的 actions
  onThinkInterval: [...]  # 周期性触发 (每 0.5s)
  add_props:          # 修改 fightProp
    FIRE_ADD_HURT: 0.15
```

→ **buff / debuff / 护盾 / 锁血都是 modifier**。

---

## 3. AbilityManager 架构（4 线程异步池）

`AbilityManager.java:48-61`：
```java
public static final ExecutorService eventExecutor;
static {
    eventExecutor = new ThreadPoolExecutor(4, 4,           // ★ 4 线程
        60, TimeUnit.SECONDS, new LinkedBlockingDeque<>(1000),
        r -> {
            Thread thread = new FastThreadLocalThread(r);  // ★ Netty 优化的线程
            thread.setUncaughtExceptionHandler((t, e) -> 
                logger.error("Uncaught exception", e));
            return thread;
        }, new ThreadPoolExecutor.AbortPolicy());
    
    registerHandlers();
}
```

**4 线程异步池** —— 这是 grasscutter 中**第 4 次出现**的 "4 线程异步同构架构"（参考 memory/project_grasscutter_pattern.md）：
- 同样的池架构: Quest / Scene Script / Ability / Activity 全部 4 线程
- 同样的异步执行 + 异常捕获

### 3.1 反射注册 Handler

```java
public static void registerHandlers() {
    var handlerClassesAction = Grasscutter.reflector.getSubTypesOf(AbilityActionHandler.class);
    
    for (var obj : handlerClassesAction) {
        if (obj.isAnnotationPresent(AbilityAction.class)) {
            AbilityModifierAction.Type abilityAction = obj.getAnnotation(AbilityAction.class).value();
            actionHandlers.put(abilityAction, obj.getDeclaredConstructor().newInstance());
        }
    }
    
    var handlerClassesMixin = Grasscutter.reflector.getSubTypesOf(AbilityMixinHandler.class);
    // 同样的反射注册 mixin handler
}
```

**这是 grasscutter 的第 9+ 次"注解+反射"模式**：
- `@AbilityAction(Type.ApplyModifier)` 标注一个 handler
- 反射扫描所有 `AbilityActionHandler` 子类
- 按注解类型注册到 `actionHandlers` map
- 加新 action 只要写一个新 class

### 3.2 actions 子目录全图（15 个）

```
actions/
├── AbilityAction.java                  ← @注解定义
├── AbilityActionHandler.java           ← 抽象基类
├── ActionApplyModifier.java            ← 加 modifier
├── ActionAvatarSkillStart.java         ← 角色技能启动
├── ActionChangeTag.java                ← 改实体标签
├── ActionCreateGadget.java             ← 创建 gadget
├── ActionCreateGadgetForEquip.java     ← 装备触发创建（如风套生成风场）
├── ActionExecuteGadgetLua.java         ← 让 gadget 执行 Lua
├── ActionGenerateElemBall.java         ← 生成元素球
├── ActionHealHP.java                   ← 治疗
├── ActionKillSelf.java                 ← 自杀
├── ActionLoseHP.java                   ← 扣血
├── ActionPredicated.java               ← 条件分支
├── ActionServerLuaCall.java            ← 调 Lua
├── ActionServerLuaTriggerEvent.java    ← 触发 Lua 事件
├── ActionSetGlobalValueToOverrideMap.java
└── ActionSetRandomOverrideMapValue.java
```

→ **15 种基础 action**，可以组合出复杂行为：
- "扣 HP 10% + 加 ATK 50% + 持续 10 秒" = LoseHP + ApplyModifier (10s ATK+50)
- "命中目标后产 3 个元素球" = GenerateElemBall + Predicated

### 3.3 9 种 AbilityInvokeEntry 处理

```java
public void onAbilityInvoke(AbilityInvokeEntry invoke) {
    if (invoke.getHead() != null && invoke.getHead().getLocalId() != 0) {
        this.handleServerInvoke(invoke);   // ← Server-side 触发的 ability
        return;
    }
    
    switch (invoke.getArgumentType()) {
        case ABILITY_META_OVERRIDE_PARAM        -> handleOverrideParam(invoke);
        case ABILITY_META_REINIT_OVERRIDEMAP    -> handleReinitOverrideMap(invoke);
        case ABILITY_META_MODIFIER_CHANGE       -> handleModifierChange(invoke);
        case ABILITY_MIXIN_COST_STAMINA         -> handleMixinCostStamina(invoke);
        case ABILITY_ACTION_GENERATE_ELEM_BALL  -> handleGenerateElemBall(invoke);
        case ABILITY_META_GLOBAL_FLOAT_VALUE    -> handleGlobalFloatValue(invoke);
        case ABILITY_META_MODIFIER_DURABILITY_CHANGE -> handleModifierDurabilityChange(invoke);
        case ABILITY_META_ADD_NEW_ABILITY       -> handleAddNewAbility(invoke);
        case ABILITY_META_TRIGGER_ELEMENT_REACTION -> handleTriggerElementReaction(invoke);
    }
}
```

**这是客户端 → 服务器的 ability 通信总入口**——每帧多个 invoke 通过 `CombatInvocationsNotify` 发来。

每种 invoke 类型的含义：

| InvokeType | 含义 | 触发例 |
|---|---|---|
| OVERRIDE_PARAM | 改某 ability 的某 special 数值 | 命座 6 升伤害倍率 |
| REINIT_OVERRIDEMAP | 重置一组 special 数值 | 切角色 / 触发新技能 |
| MODIFIER_CHANGE | 添加/移除 modifier | 加 buff / 解 debuff |
| MIXIN_COST_STAMINA | 体力消耗 | 冲刺/攀爬 |
| GENERATE_ELEM_BALL | 产生元素球 | 角色 E 技能 |
| GLOBAL_FLOAT_VALUE | 全局浮点值 | 七圣召唤?? |
| MODIFIER_DURABILITY_CHANGE | 修饰器耐久度 | 护盾被打 |
| ADD_NEW_ABILITY | 临时加一个 ability | 战令武器买入 |
| TRIGGER_ELEMENT_REACTION | 元素反应触发 | 火水蒸发等 |

---

## 4. Ability 实例化 + Hash

`Ability.java`：
```java
public class Ability {
    @Getter private AbilityData data;          // 配表数据
    @Getter private GameEntity<?> owner;       // 谁拥有这个 ability
    @Getter private Player playerOwner;
    @Getter private AbilityManager manager;
    
    @Getter private Map<String, AbilityModifierController> modifiers = new HashMap<>();
    @Getter private Object2FloatMap<String> abilitySpecials = new Object2FloatOpenHashMap<>();
    
    @Getter private int hash;
    
    public Ability(AbilityData data, GameEntity<?> owner, Player playerOwner) {
        this.data = data;
        this.owner = owner;
        this.manager = owner.getWorld().getHost().getAbilityManager();   // ← 总是 host 的 manager
        
        if (this.data.abilitySpecials != null)
            for (var entry : this.data.abilitySpecials.entrySet())
                abilitySpecials.put(entry.getKey(), entry.getValue().floatValue());
        
        this.playerOwner = playerOwner;
        hash = AbilityHash(data.abilityName);
        data.initialize();
    }
}
```

### 4.1 关键设计：所有 Ability 走 host 的 manager

```java
this.manager = owner.getWorld().getHost().getAbilityManager();
//                              ↑ host 不是 owner
```

**为什么**：
- 单机：host = 自己
- 联机：host = 房主
- 怪物的 ability 也走 host 的 manager（因为 AI 在 host 客户端）
- 所有 ability 计算**集中**在 host

→ 这就是为什么"联机房主网络差所有人都卡"——ability 系统集中在房主处理。

### 4.2 AbilityHash 算法

```java
public static int AbilityHash(String str) {
    long hash = 0;
    char[] asCharArray = str.toCharArray();
    for (int i = 0; i < str.length(); i++) {
        hash = ((asCharArray[i] + 131 * hash) & 0xFFFFFFFF);
    }
    return (int)hash;
}
```

经典的**多项式 hash**（131 是常用素数）：
- 把字符串 ability name 转 32-bit int
- 客户端和服务器**用同样算法**
- 网络上**传 hash 而非字符串**（省带宽）

例：`"Avatar_Diluc_FireElementalShoot"` → 某个 int 值。客户端打包时算这个 hash，服务器接收时反查 `GameData.getAbilityHashes()` 得到字符串。

### 4.3 客户端 / 服务器双向 hash 映射

```java
@Nullable
public static String getAbilityName(AbilityString abString) {
    if (abString.getType() instanceof AbilityString.Type.Str abStr) {
        return abStr.getValue();      // ← 直接字符串
    }
    if (abString.getType() instanceof AbilityString.Type.Hash abHash) {
        return GameData.getAbilityHashes().get((int)abHash.getValue());  // ← hash 反查
    }
    return null;
}
```

→ 网络协议支持**两种形式**：字符串或 hash。一般用 hash（4 字节 vs 30+ 字节）。

---

## 5. Modifier 系统

### 5.1 AbilityModifierController

```java
public class AbilityModifierController {
    @Getter private Ability ability;
    @Getter private AbilityData abilityData;
    @Getter private AbilityModifier modifierData;
    // (构造器存这 3 个)
}
```

简单的"3 字段"holder ——指向 ability + 它的元数据 + 当前 modifier 配置。

### 5.2 在 entity 上挂 modifier

`GameEntity` (notes/35 提到)：
```java
@Getter private Int2ObjectMap<AbilityModifierController> instancedModifiers;
```

每个 entity 维护一个 **modifier map**：
- key = `instancedModifierId` (客户端分配的 ID)
- value = `AbilityModifierController`

例：迪卢克的 E 给他自己一个"+30% ATK 8秒" buff：
- 客户端创建 modifier，分配 ID = 42
- 发 `ABILITY_META_MODIFIER_CHANGE { action=ADDED, modifierId=42 }`
- 服务器 entity.instancedModifiers.put(42, controller)
- 8 秒后：客户端发 `ABILITY_META_MODIFIER_CHANGE { action=REMOVED, modifierId=42 }`
- 服务器移除

### 5.3 handleModifierChange 流程

```java
private void handleModifierChange(AbilityInvokeEntry invoke) {
    var modChange = AbilityMetaModifierChange.parseBy(...);
    var head = invoke.getHead();
    
    if (head.getInstancedAbilityId() == 0 || head.getInstancedModifierId() > 2000) return;
    
    // serverbuff modifier (服务器主动加的)
    if (head.isServerbuffModifier()) {
        // TODO
        return;
    }
    
    var entity = this.player.getScene().getEntityById(invoke.getEntityId());
    
    if (modChange.getAction() == ModifierAction.ADDED) {
        // 找到 ability（可能在 target / 自己 / parent 上）
        AbilityData instancedAbilityData = ...;
        
        // 找到 modifier 配置
        var modifierData = (AbilityModifier) modifierArray[modChange.getModifierLocalId()];
        
        // 加到 entity
        entity.getInstancedModifiers().put(head.getInstancedModifierId(), 
            new AbilityModifierController(...));
    } else if (modChange.getAction() == ModifierAction.REMOVED) {
        entity.getInstancedModifiers().remove(head.getInstancedModifierId());
    }
}
```

**关键观察**：服务器对 modifier 的处理**很被动**——客户端说加就加，说减就减。

→ 这又印证 grasscutter 的**输出权威在客户端**设计。

### 5.4 serverbuff modifier（服务器主动加）

```java
if (head.isServerbuffModifier()) {
    // TODO
}
```

这是**预留接口**——服务器想主动给玩家加 buff（如 GM 命令、活动效果）。grasscutter 还没实现（"TODO"），但米哈游正服肯定用。

---

## 6. 元素反应处理（最有意思的部分）

```java
case ABILITY_META_TRIGGER_ELEMENT_REACTION -> this.handleTriggerElementReaction(invoke);
```

### 6.1 元素反应不在服务器算

**关键事实**：grasscutter **不计算元素反应**——客户端算完后告诉服务器"我触发了什么反应"。

`ElementReactionType` 枚举大致：
```
None
Burning (燃烧)
Vaporize (蒸发)
Melt (融化)
Overload (超载)
Frozen (冻结)
SuperConduct (超导)
ElectroCharged (感电)
Swirl (扩散)
Crystallize (结晶)
Bloom (绽放)
Burgeon (烈绽放)
HyperBloom (超绽放)
QuickenSpread/Aggravate (蔓激化/超激化)
```

每种反应**都有专门的 fightProp**（notes/36 §1.1 提到 3025-3046）：
- `FIGHT_PROP_ELEM_REACT_CRITICAL` (3025)
- `FIGHT_PROP_ELEM_REACT_CRITICAL_HURT` (3026)
- `FIGHT_PROP_ELEM_REACT_OVERGROW_CRITICAL` (3039)
- ...

→ 这些 prop 影响反应的暴击/暴伤。

### 6.2 服务器只是"知道"反应发生

服务器接收 `TRIGGER_ELEMENT_REACTION` 后：
- 记录到当前实体的状态
- 可能影响后续 fightProp（如超导减物抗）
- 可能触发 ability hook（如雷套 4 件套加电伤）

但**计算反应倍率的公式在客户端**——服务器没必要重算（重算等于让服务器跑战斗模拟）。

---

## 7. handleGenerateElemBall（实际生成元素球）

`ActionGenerateElemBall.java`：
```java
@AbilityAction(AbilityModifierAction.Type.GenerateElemBall)
public class ActionGenerateElemBall extends AbilityActionHandler {
    @Override
    public boolean execute(Ability ability, AbilityModifierAction action, byte[] abilityData, GameEntity<?> target) {
        GameEntity owner = ability.getOwner();
        
        // 解析客户端发来的数据
        AbilityActionGenerateElemBall generateElemBall = 
            AbilityActionGenerateElemBall.parseBy(abilityData, ...);
        
        // 检查规则: 这个场景是否允许产元素球
        if (action.dropType == DropType.LevelControl) {
            String levelEntityConfig = owner.getScene().getSceneData().getLevelEntityConfig();
            ConfigLevelEntity config = ...;
            if (config != null && config.getDropElemControlType().compareTo("None") == 0) {
                return true;   // ← 不让产 (深境螺旋等)
            }
        } else if (action.dropType == DropType.BigWorldOnly) {
            if (owner.getScene().getSceneData().getSceneType() != SceneType.SCENE_WORLD) {
                return true;   // ← 只大世界允许
            }
        }
        
        // 计算应该产几个
        var energy = action.baseEnergy.get(ability) * action.ratio.get(ability);
        var itemData = GameData.getItemDataMap().get(action.configID);
        var itemUse = itemData.getItemUse().get(0);
        
        double requiredEnergy;
        switch (itemUse.getUseOp()) {
            case ITEM_USE_ADD_ELEM_ENERGY: requiredEnergy = Integer.parseInt(itemUse.getUseParam()[1]); break;
            case ITEM_USE_ADD_ALL_ENERGY:  requiredEnergy = Integer.parseInt(itemUse.getUseParam()[0]); break;
        }
        
        var amountGenerated = (int) Math.ceil(energy / requiredEnergy);
        if (amountGenerated >= 21) return false;   // 安全上限
        
        // 创建 N 个 EntityItem (元素球)
        for (int i = 0; i < amountGenerated; i++) {
            val createConfig = new CreateGadgetEntityConfig(itemData, 1)
                .setPlayerOwner((owner instanceof EntityAvatar avatar) ? avatar.getPlayer() : null)
                .setBornPos(new Position(generateElemBall.getPos()))
                .setBornRot(new Position(generateElemBall.getRot()));
            EntityItem energyBall = new EntityItem(owner.getScene(), createConfig);
            owner.getScene().addEntity(energyBall);
        }
        return true;
    }
}
```

### 7.1 关键洞察：服务器算"产几个"

```java
var amountGenerated = (int) Math.ceil(energy / requiredEnergy);
```

→ "产生 N 个元素球"由服务器算的：
- ability 的 `baseEnergy` × `ratio` = 总能量
- 元素球的 `requiredEnergy` = 单球能量
- N = 总 / 单球（向上取整）

→ 客户端只告诉服务器"我放了 E 技能"，服务器**自己算应该产几个球**。

### 7.2 场景限制

```
DropType.LevelControl    → 受场景配置控制（深境螺旋禁产）
DropType.BigWorldOnly    → 只大世界允许
DropType.Forced          → 强制产生（默认）
```

→ 深境螺旋 / 副本里不产元素球——这是**服务器的判断**，客户端发来 invoke 但服务器拒绝。

---

## 8. handleAddNewAbility（动态加能力）

```java
case ABILITY_META_ADD_NEW_ABILITY -> this.handleAddNewAbility(invoke);
```

**用途**：临时给某 entity 加新 ability。
- 战令武器购买 → 加武器被动 ability
- 试用角色 → 加角色 ability
- 活动 buff → 加临时 ability
- 命座 6 解锁 → 加命座 ability

```java
public void handleAddNewAbility(AbilityInvokeEntry invoke) {
    var data = AbilityMetaAddAbility.parseBy(...);
    var entity = this.player.getScene().getEntityById(invoke.getEntityId());
    
    String abilityName = data.getAbilityName();
    AbilityData abilityData = GameData.getAbilityData(abilityName);
    Ability ability = new Ability(abilityData, entity, player);
    
    entity.getInstancedAbilities().add(ability);
}
```

→ entity 的 `instancedAbilities` 列表动态增长。

---

## 9. onSkillStart / onSkillEnd：大招无敌

```java
@Getter private boolean abilityInvulnerable = false;

public void onSkillStart(Player player, int skillId, int casterId) {
    if (player.getUid() != this.player.getUid()) return;
    if (player.getTeamManager().getCurrentAvatarEntity().getId() != casterId) return;
    
    var skillData = GameData.getAvatarSkillDataMap().get(skillId);
    if (skillData == null) return;
    
    // ★ 只有大招 (CostElemVal > 0) 才设无敌
    if (skillData.getCostElemVal() <= 0) return;
    
    this.abilityInvulnerable = true;
}

public void onSkillEnd(Player player) {
    if (player.getUid() != this.player.getUid()) return;
    if (!this.abilityInvulnerable) return;
    this.abilityInvulnerable = false;
}
```

**这就是大招无敌的实现**：
- 大招（消耗元素能量的技能）开始时 → `abilityInvulnerable = true`
- 在 `HandlerCombatInvocationsNotify` 中（notes/36）检查：
  ```java
  if (player.getAbilityManager().isAbilityInvulnerable()) break;
  ```
- 跳过伤害处理 = 大招期间不掉血
- 大招结束 → 恢复

→ 这是 grasscutter 实现"放大招期间无敌"的方式。

---

## 10. 5 路能力源汇总（回顾 notes/34）

EntityAvatar 的 `getAbilityControlBlock` 把 5 个来源聚合成一个**能力控制块**：

```
1. AvatarData.abilities                  ← 角色固有能力
2. DEFAULT_ABILITY_HASHES                ← 跑/跳/爬/游 (通用)
3. teamResonancesConfig                  ← 队伍共鸣 (元素相同/4 种元素)
4. SkillDepot.abilities                  ← 元素技能 E / Q
5. Avatar.extraAbilityEmbryos            ← 武器精炼 + 圣遗物套装 + 命座
```

→ 这就是为什么**装备改变 / 切角色 / 命座解锁**都重新发 `AbilityControlBlock`——5 路任何一路变化都要全发。

---

## 11. 与其他系统的关联

| 系统 | 通过 Ability 实现 |
|---|---|
| 角色 E/Q 技能 | SkillDepot.abilities |
| 命之座 | AvatarTalent.openConfig |
| 角色固有天赋 | ProudSkill.openConfig |
| 武器精炼 | EquipAffix.openConfig |
| 圣遗物 4 件套 | ReliquarySet.openConfig (steps 5 in recalcStats) |
| 怪物词条 | MonsterAffix.abilityName |
| 队伍共鸣 | TeamResonance.config |
| 元素反应 | ABILITY_META_TRIGGER_ELEMENT_REACTION |
| 临时 buff | ABILITY_META_MODIFIER_CHANGE (ADDED) |
| 大招无敌 | onSkillStart → abilityInvulnerable |
| 元素球产生 | ActionGenerateElemBall |
| 角色治疗（如琴大招）| ActionHealHP |
| 角色扣血（如胡桃 E）| ActionLoseHP |
| 创建临时 gadget（如温迪风场）| ActionCreateGadget |
| 触发 Lua 事件 | ActionServerLuaTriggerEvent |

→ **几乎所有"主动效果"都是 ability**。这是真正的"统一引擎"。

---

## 12. 设计模式总结

### 12.1 统一表达：Strategy Pattern

```
AbilityData (统一数据)
    ↓
Ability (统一实例)
    ↓
策略选择: actions[type] (15+) / mixins[type] / modifiers
```

每种行为有自己的 handler，但走同一个分发逻辑。

### 12.2 客户端发指令、服务器执行（部分）

| 行为 | 主语 |
|---|---|
| 触发反应 | 客户端通知 → 服务器记录 |
| 加 modifier | 客户端通知 → 服务器记录 |
| 减 modifier | 客户端通知 → 服务器移除 |
| 改 special 数值 | 客户端通知 → 服务器更新 |
| 生成元素球 | 客户端通知 + **服务器算数量** + spawn 实体 |
| 治疗 | 客户端发 → 服务器执行（改 HP）|
| 大招无敌 | 服务器自检测（onSkillStart）|
| 服务器主动 buff | 预留接口（TODO）|

→ **大部分 ability 客户端权威**，几个关键点（HP / 数量 / 无敌）服务器算。

### 12.3 数据驱动

加新角色 / 新武器 / 新套装：
- ✓ 写 BinOutput/Ability/Xxx.json
- ✓ 引用进 AvatarData / EquipAffix / ReliquarySet
- ✓ 不改一行 Java

→ 这是为什么原神**每个版本能加几十个新角色 / 武器**——全数据驱动。

---

## 13. 反作弊薄弱

Ability 系统是**反作弊重灾区**：

| 攻击 | 是否有效 | 原因 |
|---|---|---|
| 伪造 modifier（永久 +999% ATK）| ✓ 有效 | 客户端权威 |
| 伪造 abilitySpecials 改技能数值 | ✓ 有效 | OVERRIDE_PARAM 服务器照接 |
| 伪造大招无敌一直不结束 | ✓ 部分 | onSkillStart 可绕 |
| 伪造产 999 个元素球 | ✗ 部分 | 服务器有上限 21 |
| 伪造队伍共鸣 4 元素 | ✗ 无效 | TeamResonance 服务器算 |
| 伪造命座 6 | ✗ 无效 | TalentIdList 服务器存 |
| 伪造圣遗物 4 件套 | ✗ 无效 | setMap 服务器算 |

→ **"装备类"反作弊靠服务器属性计算守住，"运行时行为"几乎全靠客户端诚实**。

→ 米哈游正服必然加 anti-cheat 检测——能改 modifier 但客户端 dll 完整性校验会发现。

---

## 14. 关键收获

1. **Ability 是统一引擎**：角色技能/命座/武器/圣遗物/怪物词条/元素反应**全部走它**
2. **4 概念**：abilitySpecials (数值) + modifiers (临时状态) + actions (一次性行为) + mixins (混入)
3. **4 线程异步池 + 注解反射注册** —— 又一次同构架构
4. **15+ ActionHandler** 类型可组合出复杂行为
5. **9 种 AbilityInvokeEntry** 处理：OVERRIDE / REINIT / MODIFIER_CHANGE / COST_STAMINA / GENERATE_ELEM_BALL / GLOBAL_FLOAT / DURABILITY / ADD_ABILITY / ELEMENT_REACTION
6. **所有 Ability 走 host 的 manager**：联机时房主集中处理
7. **AbilityHash 多项式 hash (131)**：网络传 4 字节 hash 而非 30+ 字节字符串
8. **modifier 服务器被动接收**：客户端说加就加，说减就减
9. **生成元素球服务器算数量** + 场景限制（深境螺旋禁产）+ 上限 21
10. **大招无敌通过 abilityInvulnerable**：onSkillStart 时设 true，伤害包跳过
11. **5 路能力源**：AvatarData + Default + Resonance + SkillDepot + Equip
12. **几乎所有"主动效果"都是 ability**：治疗/扣血/创建/触发/改值
13. **数据驱动**：加角色/武器/套装只写 JSON，不改 Java
14. **反作弊薄弱**：客户端可伪造 modifier / specials，服务器只守"账本类"（装备/命座）

---

## 15. 一句话总结

> **Ability 是 grasscutter 中"行为的统一表达" —— 角色技能/命座/武器精炼/圣遗物 4 件套/怪物词条/元素反应/临时 buff 全部用同一种数据格式描述、同一个引擎执行。4 概念 (specials/modifiers/actions/mixins) × 15+ action handler × 9 种 invoke 处理 = 几乎所有"主动效果"。**
> 
> **设计哲学：数据驱动 + 客户端发指令 + 服务器记账。加新内容只写 JSON 不改代码——这是原神能 5 年保持每版本几十个新角色/武器/活动的根本机制。**

---

**前置笔记**：
- notes/16 战斗系统 - 提到 AbilityManager 是 4 同构系统之一
- notes/24 Avatar 升级 - extraAbilityEmbryos 5 路汇总
- notes/27 架构模式 - 第 4 次"注解+反射"
- notes/32-34 三大实体 - instancedAbilities / instancedModifiers
- notes/36 战斗数学 - 通过 ability 加 fightProp

**关联文件**：
- `AbilityManager.java`(530) - 主管理器 + 反射注册 + 9 种 invoke 处理
- `Ability.java`(74) - 实例对象 + Hash 算法
- `AbilityModifierController.java`(18) - modifier 控制器（简单 holder）
- `actions/AbilityAction.java` - 注解定义
- `actions/Action*.java` × 15 - 行为处理器
- `mixins/AbilityMixin.java` - 混入处理器
- `HandlerCombatInvocationsNotify.java` - invoke 入口

**研究的源代码**: 1200+ 行 ability 系统代码 + 15 个 action handler 子类。
