# 怪物系统全景剖析

> 第 32 篇：从配表到 AI 到死亡级联——把"怪物"这个东西的所有齿轮都拆开看一遍

---

## 0. 为什么这一篇重要

前 31 篇笔记里怪物零散地出现过：
- notes/16 战斗权威 提到 EntityMonster
- notes/19 副本系统 用 Challenge 检测 onMonsterDeath
- notes/14 SceneScript 用 spawn group 创建怪物
- notes/15 奖励系统 提到怪物掉落 → addItem

但**完整的"怪物生命周期"**没有专门梳理过。这一篇把以下问题一次回答：

1. 一个怪物从哪里来？怎么 spawn？
2. 它的 HP / 攻击 / 抗性怎么算出来？
3. 它的 AI / 技能在哪里跑？
4. 它怎么挨打的？伤害公式在哪？
5. 它死了之后**触发了什么**？（提示：7 件事）
6. 元素能量球怎么掉的？
7. 联机时它听谁的？
8. Boss / 精英 / 普通怪 有什么本质差别？

---

## 1. 怪物体系的三层数据 + 一个实体

```
                  [ MonsterExcel ]  ← 基础属性 (HP/攻/抗性)
                         +
                  [ MonsterCurve ]  ← 等级缩放系数表
                         +
                  [ MonsterAffix ]  ← 词条 (元素增强/无敌等)
                         +
                  [ ConfigEntityMonster ]  ← BinOut 能力/AI 配置
                         ↓
       --------------------------------------------
       |              EntityMonster              |  ← 运行时实体
       |  (服务端对象, 32 个字段)                  |
       --------------------------------------------
                         ↓
                  [ SceneGroup ]  ← Lua 脚本管理 spawn
                         ↓
            被 PacketSceneEntityAppearNotify 广播给客户端
```

每一层各管一块：
| 层 | 文件类型 | 数量 | 内容 |
|---|---|---|---|
| MonsterExcel | json 配表 | 1700+ 种 | 基础数值、词条引用、AI 名 |
| MonsterCurve | json 配表 | ~100 级 | 每级的成长系数 |
| MonsterAffix | json 配表 | ~200 个 | 能力词条（Pyro Boost / 史莱姆覆盖等）|
| ConfigEntityMonster | BinOutput | 1700+ 个 | 能力列表 + 移动配置 |
| EntityMonster | Java 运行时 | 实例化 | 真正在场景中跑的对象 |

---

## 2. MonsterData：基础属性表

`MonsterData.java` 来自 `ExcelBinOutput/MonsterExcelConfigData.json`：

```java
@ResourceType(name = "MonsterExcelConfigData.json", loadPriority = LoadPriority.LOW)
public class MonsterData extends GameResource {
    private int id;                          // 唯一 ID (如 21010101 = 丘丘人)
    private String monsterName;              // 内部名 (如 "Hili_Hili_Axe")
    private MonsterType type;                // 7 种分类
    private String serverScript;             // 服务端脚本钩子名
    private List<Integer> affix;             // 词条 ID 列表
    private String ai;                       // AI 配置文件名
    private int[] equips;                    // 装备的武器/盾
    private List<HpDrops> hpDrops;           // ★ HP 阈值掉落表
    private int killDropId;                  // 死亡掉落物 ID
    private int describeId;                  // 名字翻译 hash
    private int campID;                      // 阵营 (丘丘人/愚人众/魔物等)
    
    // === 11 个基础战斗属性 ===
    @SerializedName("hpBase")    private float baseHp;
    @SerializedName("attackBase") private float baseAttack;
    @SerializedName("defenseBase") private float baseDefense;
    private float fireSubHurt;       // 火元素抗性 (减伤百分比)
    private float elecSubHurt;
    private float grassSubHurt;
    private float waterSubHurt;
    private float windSubHurt;
    private float rockSubHurt;
    private float iceSubHurt;
    private float physicalSubHurt;
    
    private List<PropGrowCurve> propGrowCurves;  // 成长曲线引用
    private long nameTextMapHash;
}
```

**关键设计**：怪物属性**完全是配表驱动**——加一个新怪只要写 1 行 JSON。没有 hardcode。

### 2.1 11 个基础战斗属性

`definedFightProperties` Set：
```java
FIGHT_PROP_BASE_HP            // 基础 HP
FIGHT_PROP_BASE_ATTACK        // 基础攻击
FIGHT_PROP_BASE_DEFENSE       // 基础防御
FIGHT_PROP_PHYSICAL_SUB_HURT  // 物理抗性 (-X% 减伤)
FIGHT_PROP_FIRE_SUB_HURT      // 7 种元素抗性
FIGHT_PROP_ELEC_SUB_HURT
FIGHT_PROP_WATER_SUB_HURT
FIGHT_PROP_GRASS_SUB_HURT
FIGHT_PROP_WIND_SUB_HURT
FIGHT_PROP_ROCK_SUB_HURT
FIGHT_PROP_ICE_SUB_HURT
```

→ **7 种元素 + 物理 = 8 种抗性**。每只怪都有这 8 个值——丘丘人对火脆弱 = `fireSubHurt = -0.5`（25% 增伤），愚人众火使徒对火免疫 = `fireSubHurt = 0.999`。

### 2.2 HpDrops：阈值掉落机制

```java
@Getter
public class HpDrops {
    private int DropId;       // 掉什么
    private int HpPercent;    // 阈值百分比 (75/50/25 等)
}
```

**用途**：怪物 HP 经过阈值时**掉元素能量球**。
- 例：某 boss 的 hpDrops = `[(drop_A, 75), (drop_B, 50), (drop_C, 25)]`
- 打到 74% → 掉 drop_A
- 打到 49% → 掉 drop_B
- 打到 24% → 掉 drop_C

→ 这就是为什么打 boss "持续打就有元素能量球喷出来"——是**阈值机制**而非随机。具体见 §9。

---

## 3. MonsterType：7 种分类

`MonsterType.java`：
```java
public enum MonsterType {
    MONSTER_NONE          (0),
    MONSTER_ORDINARY      (1),   // 普通怪 (丘丘人, 史莱姆等)
    MONSTER_BOSS          (2),   // BOSS (无相, 巴巴托斯等)
    MONSTER_ENV_ANIMAL    (3),   // 环境动物 (松鼠, 狐狸)
    MONSTER_LITTLE_MONSTER(4),   // 小型生物 (蝴蝶, 蘑菇)
    MONSTER_FISH          (5),   // 鱼
    MONSTER_PARTNER       (6);   // 同伴 (战斗中的旅伴)
}
```

### 3.1 类型决定行为

不同类型走不同代码路径：

| Type | 能给经验？ | 给元素球？ | 任务计数？ | 副本触发？ |
|---|---|---|---|---|
| ORDINARY | ✓ | ✓ | ✓ | ✓ |
| BOSS | ✓ | ✓ | ✓ | ✓ |
| LITTLE_MONSTER | ✗ | ✗ | ✓ | ✓ |
| ENV_ANIMAL | ✗ | ✗ | ✗ | ✗ |
| FISH | ✗ | ✗ | ✗ | ✗ |
| PARTNER | ✗ | ✗ | ✗ | ✗ |

代码体现 (`EnergyManager.handleMonsterEnergyDrop`)：
```java
MonsterType type = monster.getMonsterData().getType();
if (type != MonsterType.MONSTER_ORDINARY && type != MonsterType.MONSTER_BOSS) {
    return;   // 只有普通怪和 boss 给元素球
}
```

`EnergyManager.handleAttackHit`：
```java
if (targetType != MonsterType.MONSTER_ORDINARY && targetType != MonsterType.MONSTER_BOSS) {
    return;   // 打动物/鱼不给玩家充能
}
```

### 3.2 ENV_ANIMAL 的特殊路径

环境动物（松鼠、鹌鹑、青蛙等）走**采集路径**而非战斗：
```java
// EntityMonster.onInteract()
EnvAnimalGatherConfigData gatherData = GameData.getEnvAnimalGatherConfigDataMap()
    .get(this.getMonsterData().getId());
if (gatherData == null) return;

player.getInventory().addItem(gatherData.getGatherItem(), ActionReason.SubfieldDrop);
this.getScene().killEntity(this);   // ← 抓 = 杀
```

→ 抓松鼠的逻辑：`onInteract` (玩家按 F) → addItem → killEntity。这就是为什么环境动物**抓了就消失**而不是"打死掉落"。

---

## 4. MonsterCurve：等级缩放

`MonsterCurveData.java`：
```java
@ResourceType(name = "MonsterCurveExcelConfigData.json")
public class MonsterCurveData extends GameResource {
    private int level;
    private CurveInfo[] curveInfos;
    private Map<String, Float> curveInfoMap;   // 运行时构建
    
    public float getMultByProp(String fightProp) {
        return curveInfoMap.getOrDefault(fightProp, 1f);
    }
}
```

### 4.1 曲线如何用

`EntityMonster.recalcStats()` 第 304-310 行：
```java
MonsterCurveData curve = GameData.getMonsterCurveDataMap().get(this.getLevel());
if (curve != null) {
    for (PropGrowCurve growCurve : data.getPropGrowCurves()) {
        FightProperty prop = FightProperty.getPropByName(growCurve.getType());
        // 当前属性 = 当前属性 × 曲线系数
        this.setFightProperty(prop, this.getFightProperty(prop) * curve.getMultByProp(growCurve.getGrowCurve()));
    }
}
```

### 4.2 一个真实例子

假设丘丘人 (id=21010101) 的 baseHp = 100, propGrowCurves 引用 `GROW_CURVE_HP`。

- Lv1 时：curve(1).GROW_CURVE_HP = 1.0 → HP = 100 × 1.0 = **100**
- Lv50 时：curve(50).GROW_CURVE_HP = 10.5 → HP = 100 × 10.5 = **1050**
- Lv95 时：curve(95).GROW_CURVE_HP = 50.2 → HP = 100 × 50.2 = **5020**

→ **同一个怪在不同世界等级下血量是几十倍差距**。这就是为什么 AR60 玩家进新区觉得"低等怪都那么硬"——因为它们其实是同一个 monsterId，只是等级不同。

### 4.3 多条曲线并行

`PropGrowCurve` 是个列表——每个属性可以有独立曲线：
```yaml
propGrowCurves:
  - type: FIGHT_PROP_BASE_HP, growCurve: GROW_CURVE_HP_S5
  - type: FIGHT_PROP_BASE_ATTACK, growCurve: GROW_CURVE_ATK_S5
  - type: FIGHT_PROP_BASE_DEFENSE, growCurve: GROW_CURVE_DEF_S5
```

不同曲线允许不同节奏（HP 涨得快、攻击涨得慢等），调平衡时灵活。

---

## 5. MonsterAffix：词条系统

`MonsterAffixData.java`（27 行，极简）：
```java
@ResourceType(name = "MonsterAffixExcelConfigData.json")
public class MonsterAffixData extends GameResource {
    private int id;
    @Getter private String affix;            // 词条名
    @Getter private String[] abilityName;    // 触发哪些能力
    @Getter private boolean isCommon;
    @Getter private boolean preAdd;          // ★ 是否在能力前置
    @Getter public String isLegal;
}
```

### 5.1 affix 的作用

词条 = **一组能力 (ability)** 挂在怪物身上：
- 「水史莱姆覆盖词条」→ 把丘丘人变成"覆盖水元素"的丘丘人
- 「精英怪光环」→ 加 buff
- 「BOSS 阶段二」→ 切换技能组

### 5.2 preAdd 的妙处

`EntityMonster.getAbilityData()` 第 134-169 行**严格按顺序**：

```java
// 1. preAdd=true 的词条能力 (优先)
for (val affix : affixes) {
    if (!affix.isPreAdd()) continue;
    abilityNames.addAll(Arrays.asList(affix.getAbilityName()));
}

// 2. 默认非人形移动能力
abilityNames.addAll(defaultAbilities.getNonHumanoidMoveAbilities());

// 3. ConfigEntityMonster 配置的能力
if (configEntityMonster.getAbilities() != null) {
    abilityNames.addAll(configEntityMonster.getAbilities()...);
}

// 4. 精英怪额外能力
if (monster.isElite()) {
    abilityNames.add(defaultAbilities.getMonterEliteAbilityName());
}

// 5. preAdd=false 的词条 (放最后)
for (val affix : affixes) {
    if (affix.isPreAdd()) continue;
    abilityNames.addAll(List.of(affix.getAbilityName()));
}

// 6. Scene 级别的能力
if (config.getMonsterAbilities() != null) {
    abilityNames.addAll(...);
}
```

→ **6 段拼装**得到一只怪的完整能力列表：preAdd → 默认移动 → 配置 → 精英 → postAdd → 场景。
→ 这套机制非常灵活——同一只丘丘人在不同场景能力组**不同**。

---

## 6. EntityMonster：服务端实体

`EntityMonster.java`（367 行）—— 真正在场景中跑的对象。

### 6.1 数据字段（32 个）

```java
public class EntityMonster extends GameEntity<CreateMonsterEntityConfig> 
    implements StringAbilityEntity {
    
    private final Int2FloatOpenHashMap fightProperties;  // 11+ 个 prop 的实时值
    private final Position position;                     // 当前坐标
    private final Position rotation;
    private final MonsterData monsterData;               // 配表引用
    private final ConfigEntityMonster configEntityMonster;
    private final Position bornPos;                      // 出生位置
    private final Position bornRot;
    private EntityWeapon weaponEntity;                   // 装备的武器实体 (有些怪带盾/枪)
    @Setter private int poseId;                          // 姿态 ID
    @Setter private int aiId;                            // AI 配置 ID
    private final int titleId;                           // 称号 (精英怪有"震怒的"前缀等)
    private final int specialNameId;
    private int weaponId;
    private List<Player> playerOnBattle;                 // ★ 谁在跟它打 (用于警戒系统)
}
```

### 6.2 实体 ID 分配

`EntityMonster.<init>()` 第 68 行：
```java
this.id = getWorld().getNextEntityId(EntityIdType.MONSTER);
```

→ `EntityIdType.MONSTER` 是一段专用 ID 范围。所有怪物 entity_id 都在这段——客户端能从 ID 立刻判断"这是怪物"。

### 6.3 武器实体的特殊性

```java
this.weaponId = config.getWeaponId();
if (weaponId > 0) {
    val weaponConfig = new CreateGadgetEntityConfig(weaponId);
    this.weaponEntity = new EntityWeapon(scene, weaponConfig);
    scene.getWeaponEntities().put(this.weaponEntity.getId(), this.weaponEntity);
}
```

→ 怪物的**武器是独立 entity**！丘丘人的斧头是 `EntityWeapon` 实例。这就是为什么：
- 玩家能"打飞怪物武器"——武器是独立物体
- 武器可以被磁吸（岩元素能力）
- 武器掉了怪物变拳头攻击

---

## 7. 怪物的"7 层属性"叠加

`EntityMonster.recalcStats()` 拆解：

```java
public void recalcStats() {
    MonsterData data = this.getMonsterData();
    
    // 保留 HP 百分比（升级/换怪时不能瞬间满血）
    float hpPercent = ... / MAX_HP;
    
    // Clear properties
    this.getFightPropertiesOpt().ifPresent(Int2FloatMap::clear);
    
    // ===== Layer 1: 基础数值 =====
    MonsterData.definedFightProperties.forEach(prop -> 
        this.setFightProperty(prop, data.getFightProperty(prop)));
    
    // ===== Layer 2: 等级曲线 =====
    MonsterCurveData curve = GameData.getMonsterCurveDataMap().get(this.getLevel());
    for (PropGrowCurve growCurve : data.getPropGrowCurves()) {
        this.setFightProperty(prop, current * curve.getMultByProp(...));
    }
    
    // ===== Layer 3-5: 复合属性叠加 (FlatBase + Base × (1 + Percent)) =====
    FightProperty.forEachCompoundProperty(c -> 
        this.setFightProperty(c.getResult(),
            this.getFightProperty(c.getFlat()) + 
            (this.getFightProperty(c.getBase()) * (1f + this.getFightProperty(c.getPercent())))));
    
    // ===== Layer 6: 维持 HP 比例 =====
    this.setFightProperty(FightProperty.FIGHT_PROP_CUR_HP, 
        this.getFightProperty(FightProperty.FIGHT_PROP_MAX_HP) * hpPercent);
    
    // ===== Layer 7: 运行时 Modifier (来自 Ability) =====
    // 通过 AbilityManager 实时改 fightProp (火附魔时改 fireSubHurt 等)
}
```

完整 7 层：
```
[Layer 1] MonsterData.baseHp/atk/抗性          ← 配表基础
[Layer 2] × MonsterCurve(level).系数            ← 等级缩放
[Layer 3] + FlatBase (词条加平 HP)              ← Affix 加成
[Layer 4] × (1 + Percent)                       ← Affix 百分比
[Layer 5] FightProperty 复合 (BASE → MAX_HP)    ← 公式计算
[Layer 6] CUR_HP = MAX_HP × hpPercent           ← 持续性维持
[Layer 7] Ability runtime modifier              ← 战斗中实时改 (附魔/buff)
```

→ 和 notes/24 Avatar 的"7 层属性叠加"模式**完全一致**。这是 grasscutter 的**通用计算范式**。

---

## 8. 生成机制：从哪里来

### 8.1 三条生成路径

```
[Path A] SceneScript Lua 脚本调 createMonster
    ↓
    SceneScriptManager.createMonster(SceneMonster)
    ↓
    new EntityMonster(...) → scene.addEntity()

[Path B] 副本 Tide (车轮战)
    ↓
    ScriptMonsterTideService.addMonsters()
    ↓
    while (monstersSpawned < spawnLimit) addNextMonster()

[Path C] Quest 触发 (任务系统主动 spawn)
    ↓
    finishExec.QUEST_EXEC_NOTIFY_GROUP_LUA → Lua 收到 → spawn
```

### 8.2 Tide 系统（车轮战）

`ScriptMonsterTideService.java`（144 行）—— 这是**副本的核心机制**：

```java
public ScriptMonsterTideService(SceneScriptManager mgr, int challengeIndex,
        SceneGroup group, List<Integer> ordersConfigId,
        int tideSize,         // ← 一共要刷多少只
        int spawnThreshold,   // ← 剩多少只时刷新
        int spawnLimit) {     // ← 同时存在上限
    
    // 监听怪物死亡 → 自动补充
    spawnService.addMonsterDeadListener(onMonsterDead);
    
    // 一开始刷满
    addMonsters();
}
```

`OnMonsterDead.onNotify()`：
```java
if (monstersSpawned.decrementAndGet() <= spawnThreshold) {
    addMonsters();   // ★ 触发补怪
}
val kills = monsterKillCount.incrementAndGet();
if (kills >= tideSize) {
    unload();        // 达到总数, 结束
}

// 告诉 Lua: "玩家已经打死 N 只"
sceneScriptManager.callEvent(new ScriptArgs(groupId, 
    EventType.EVENT_MONSTER_TIDE_DIE, kills));
```

**真实场景**：地灵龛挑战
```
tideSize=10, spawnThreshold=2, spawnLimit=3
↓
开局: 刷 3 只 (达到 spawnLimit)
打掉 1 只: 还剩 2, > threshold (2 不刷)
打掉 2 只: 还剩 1, < threshold → 补 2 只 → 共 3 只
打掉总共 10 只: unload, 挑战胜利
```

→ "永远不让你打完, 直到打够数"。

### 8.3 SceneGroup 配怪的灵活性

`SceneGroup` 是 Lua 脚本里描述的"一组场景元素"：
```lua
-- 来自 GenshinData/Scripts/Scene/.../scene_group_NNN.lua
monsters = {
    { config_id = 1001, monster_id = 21010101, pos = {x=10, y=5, z=20}, level = 30, ... },
    { config_id = 1002, monster_id = 21010102, pos = {...}, level = 30, ... },
}

suites = {
    { monsters = { 1001 }, gadgets = {} },   -- suite 1: 只有 1001
    { monsters = { 1001, 1002 }, gadgets = {} },  -- suite 2: 两只都刷
}
```

服务器调 `refresh_group_suite(group, suite_id)` → 切换刷哪批怪 → 同一场景可呈现不同战斗组合。

---

## 9. 战斗：damage 接收 + HP 改变 + 死亡判定

### 9.1 入口：客户端发来 damage 数字

回到 notes/16 提到的"客户端权威伤害"：

```
[客户端] 玩家按攻击键 → 本地伤害公式算出 amount = 5432.7
                       ↓
                  EvtBeingHitNotify { defenseId=monster_id, damage=5432.7 }
                       ↓
[服务器] HandlerEvtBeingHitNotify
                       ↓
                  scene.getEntityById(defenseId).damage(5432.7)
                       ↓
                  EntityMonster.damage(5432.7, killerId, attackType)
```

### 9.2 GameEntity.damage()

`GameEntity.java:180-217` —— **所有 entity 共用**的伤害逻辑：

```java
public void damage(float amount, int killerId, ElementType attackType) {
    if (!hasFightProperty(FightProperty.FIGHT_PROP_CUR_HP)) return;
    
    // 1. 触发可取消事件 (plugin 钩子)
    EntityDamageEvent event = new EntityDamageEvent(this, amount, attackType, ...);
    event.call();
    if (event.isCanceled()) return;
    
    // 2. 扣 HP (除非 lockHP)
    if (!lockHP || lockHP && curHp <= event.getDamage()) {
        this.addFightProperty(FightProperty.FIGHT_PROP_CUR_HP, -(event.getDamage()));
    }
    
    this.lastAttackType = attackType;
    
    // 3. 判定死亡
    boolean isDead = false;
    if (this.getFightProperty(FightProperty.FIGHT_PROP_CUR_HP) <= 0f) {
        this.setFightProperty(FightProperty.FIGHT_PROP_CUR_HP, 0f);
        isDead = true;
    }
    
    // 4. Lua 事件 (剧情触发: "击中 Boss 50% HP 触发对话")
    callLuaHPEvent(event);
    callAbilityBeHurt(event);
    
    // 5. 广播新 HP 给所有客户端
    this.getScene().broadcastPacket(new PacketEntityFightPropUpdateNotify(this, FightProperty.FIGHT_PROP_CUR_HP));
    
    // 6. 死了就杀
    if (isDead) {
        this.getScene().killEntity(this, killerId);
    }
}
```

### 9.3 EntityMonster.damage() override

`EntityMonster.java:220-237` 在父类基础上**加副作用**：

```java
@Override
public void damage(float amount, int killerId, ElementType attackType) {
    float hpBeforeDamage = this.getFightProperty(FightProperty.FIGHT_PROP_CUR_HP);
    
    super.damage(amount, killerId, attackType);   // ← 父类逻辑
    
    float hpAfterDamage = this.getFightProperty(FightProperty.FIGHT_PROP_CUR_HP);
    
    // ★ 副作用 1: 元素能量球掉落
    for (Player player : this.getScene().getPlayers()) {
        player.getEnergyManager().handleMonsterEnergyDrop(this, hpBeforeDamage, hpAfterDamage);
    }
    
    // ★ 副作用 2: 副本挑战触发 (例: "在 30 秒内造成 X 伤害")
    Optional.ofNullable(getScene()).map(Scene::getChallenge).ifPresent(c -> 
        c.onDamageMonsterOrShield(this, amount));
}
```

### 9.4 lockHP：剧情用的不死模式

注意 `lockHP` 字段：
```java
if (curHp != Float.POSITIVE_INFINITY && !lockHP || lockHP && curHp <= event.getDamage())
```

→ "锁血"模式：怪物不死，**除非伤害一击 ≥ 当前 HP**。
用途：
- 剧情战 boss "你打不死他，他自己倒下"
- 教程关卡防止玩家技不熟过不去
- 限时挑战预设结局

---

## 10. 元素能量球掉落（精彩的设计）

### 10.1 触发逻辑

`EnergyManager.handleMonsterEnergyDrop()` 第 303-330+ 行：

```java
public void handleMonsterEnergyDrop(EntityMonster monster, float hpBefore, float hpAfter) {
    // 只有普通怪/Boss 给球
    MonsterType type = monster.getMonsterData().getType();
    if (type != MonsterType.MONSTER_ORDINARY && type != MonsterType.MONSTER_BOSS) {
        return;
    }
    
    float maxHp = monster.getFightProperty(FightProperty.FIGHT_PROP_MAX_HP);
    float thresholdBefore = hpBefore / maxHp;
    float thresholdAfter  = hpAfter / maxHp;
    
    // 检查穿过哪些阈值
    for (HpDrops drop : monster.getMonsterData().getHpDrops()) {
        if (drop.getDropId() == 0) continue;
        
        // 当前血量阈值
        float threshold = drop.getHpPercent() / 100f;
        
        // 这次伤害**穿过**了这个阈值
        if (thresholdBefore > threshold && thresholdAfter <= threshold) {
            generateElemBallDrops(monster, drop.getDropId());
        }
    }
}
```

### 10.2 阈值机制的妙处

**为什么用"穿过阈值"而不是"按 HP 比例"**？
- ✓ **保证每个阈值至少触发一次** —— 即使一击秒杀也触发所有阈值
- ✓ **防止刷球** —— 不能在 75% 附近反复打来回刷
- ✓ **多人合作公平** —— 谁打到阈值都触发，谁补刀都行

**反例**：如果是"每损失 5% HP 给一个球"——玩家可以**反复打然后用治疗回血**，无限刷球。

### 10.3 元素球的 ID 系统

```java
private int getBallIdForElement(ElementType element) {
    return switch (element) {
        case Fire    -> 2017;
        case Water   -> 2018;
        case Grass   -> 2019;
        case Electric -> 2020;
        case Wind    -> 2021;
        case Ice     -> 2022;
        case Rock    -> 2023;
        default      -> 2024;   // 无元素粒子
    };
}
```

→ 每种元素一个专用 itemId（2017-2024）。

### 10.4 完整能量经济链

```
[1] 玩家打怪 → 怪 HP 减少
[2] EntityMonster.damage 触发 handleMonsterEnergyDrop
[3] 检测穿过阈值 → 生成 EntityItem (球)
[4] 球落地 → 客户端展示飞舞特效
[5] 玩家走近 → 自动拾取
[6] 拾取 → addEnergy(角色)
[7] 角色能量条增加 → 可以放大招
```

→ 这就是为什么打 boss 比打小怪给更多能量——boss 有多个 HpDrops 阈值，触发次数多。

---

## 11. 死亡：onDeath 的 7 件事

`EntityMonster.onDeath()` 第 250-288 行 —— 一只怪死了之后**触发 7 个副作用**：

```java
@Override
public void onDeath(int killerId) {
    super.onDeath(killerId);   // 触发 EntityDeathEvent + entityController.onDie
    
    var scene = this.getScene();
    var challenge = Optional.ofNullable(scene.getChallenge());
    var scriptManager = scene.getScriptManager();
    
    // 加入死亡列表 (持久化, 重连不复活)
    Optional.ofNullable(this.getSpawnEntry()).ifPresent(scene.getDeadSpawnedEntities()::add);
    
    // === 副作用 1: 挑战进度 ===
    challenge.ifPresent(c -> c.onMonsterDeath(this));
    
    if (scriptManager.isInit() && this.getGroupId() > 0) {
        // === 副作用 2: Tide 系统补怪 ===
        Optional.ofNullable(scriptManager.getScriptMonsterSpawnService())
            .ifPresent(s -> s.onMonsterDead(this));
        
        // === 副作用 3: Lua 通用事件 ===
        scriptManager.callEvent(new ScriptArgs(this.getGroupId(), 
            EventType.EVENT_ANY_MONSTER_DIE, this.getConfigId()));
    }
    
    // === 副作用 4: 战令任务 ===
    scene.getPlayers().forEach(p -> 
        p.getBattlePassManager().triggerMission(WatcherTriggerType.TRIGGER_MONSTER_DIE, 
            this.getMonsterId(), 1));
    
    // === 副作用 5: 任务进度 ===
    scene.getPlayers().forEach(p -> 
        p.getQuestManager().queueEvent(QuestContent.QUEST_CONTENT_MONSTER_DIE, this.getMonsterId()));
    scene.getPlayers().forEach(p -> 
        p.getQuestManager().queueEvent(QuestContent.QUEST_CONTENT_KILL_MONSTER, this.getMonsterId()));
    
    // === 副作用 6: 组内全清判定 ===
    if (scriptManager.isClearedGroupMonsters(this.getGroupId())) {
        scene.getPlayers().forEach(p -> 
            p.getQuestManager().queueEvent(QuestContent.QUEST_CONTENT_CLEAR_GROUP_MONSTER, 
                this.getGroupId()));
    }
    
    // 持久化到 SceneGroupInstance (玩家修改的场景元素)
    SceneGroupInstance groupInstance = scene.getScriptManager().getGroupInstanceById(...);
    if (groupInstance != null)
        groupInstance.getDeadEntities().add(getConfigId());
    
    // === 副作用 7: 副本通关条件 ===
    scene.triggerDungeonEvent(DungeonPassConditionType.DUNGEON_COND_KILL_GROUP_MONSTER, this.getGroupId());
    scene.triggerDungeonEvent(DungeonPassConditionType.DUNGEON_COND_KILL_TYPE_MONSTER, 
        this.getMonsterData().getType().getValue());
    scene.triggerDungeonEvent(DungeonPassConditionType.DUNGEON_COND_KILL_MONSTER, this.getMonsterId());
    
    // 封印之战 (须弥 boss 战)
    scene.getSealBattleManager().onKill(this);
}
```

→ **每只怪死**都触发 7+ 个子系统：挑战 / Tide / Lua / 战令 / 任务 ×2 / 全清 / 副本 ×3 / 封印。这是 grasscutter 的**事件中枢**。

→ 这就是为什么"打个史莱姆"也能完成"消灭 100 个史莱姆"任务——QuestManager 在每只死亡时收到事件。

---

## 12. Lua 事件钩子（5 个）

怪物相关的 Lua 事件：

| EventType | 触发点 | 用途 |
|---|---|---|
| `EVENT_ANY_MONSTER_LIVE` | onCreate | "刷出来通知 Lua" |
| `EVENT_ANY_MONSTER_DIE` | onDeath | "死了通知 Lua" |
| `EVENT_SPECIFIC_MONSTER_HP_CHANGE` | callLuaHPEvent | "HP 变了通知" |
| `EVENT_MONSTER_BATTLE` | HandlerMonsterAlertChangeNotify | "进战通知" |
| `EVENT_MONSTER_TIDE_DIE` | Tide.OnMonsterDead | "Tide 进度通知" |

### 12.1 真实使用例

**剧情触发**：
```lua
-- "打 boss 到 50% 时进入第二阶段"
function on_monster_hp_change(context, monster_id, percent)
    if monster_id == 12345 and percent <= 0.5 then
        change_group_variable(context, "phase", 2)
        spawn_extra_minions(context)
        play_dialog(context, 67890)
    end
end
```

**联动触发**：
```lua
-- "杀掉所有这组怪打开宝箱"
function on_any_monster_die(context, monster_id)
    if all_monsters_dead(context) then
        spawn_chest(context, chest_config_id)
    end
end
```

---

## 13. 联机权威：authorityPeerId

回到 §0 的核心问题：怪物 AI 在哪跑？

`EntityMonster.toProto()` 第 343 行：
```java
monsterInfo.setAuthorityPeerId(getWorld().getHostPeerId());
```

### 13.1 含义

每只怪物有一个 `authorityPeerId` 字段——指向**这只怪的 AI 跑在谁的客户端**。

```
[单机]
  authorityPeerId = host (= 你自己)
  → 你的客户端跑所有怪的 AI

[联机 4 人]
  authorityPeerId = host (=房主)
  → 房主客户端跑所有怪的 AI
  → 其他 3 个客户端只是观察者 (跟着 host 的同步包动)
```

### 13.2 影响

**房主网络好坏 = 所有人体感**：
- 房主延迟高 → 所有客户端看怪物 AI 卡顿
- 房主断线 → 怪物冻结（直到 host 切换）
- 房主退出 → host 转移到下一玩家，怪物 AI 切换执行机器

### 13.3 攻击面

**带来的问题**：
- 房主可以**通过 hack 让怪物 AI 异常**（如永远不攻击）
- 房主可以**让自己显示伪造伤害**（虽然 HP 是服务器扣的）

→ 这又印证 notes/16：实时同步性能 > 反作弊严密。

---

## 14. 警戒系统：HandlerMonsterAlertChangeNotify

```java
public class HandlerMonsterAlertChangeNotify extends TypedPacketHandler<MonsterAlertChangeNotify> {
    @Override
    public void handle(GameSession session, byte[] header, MonsterAlertChangeNotify req) {
        val player = session.getPlayer();
        if (req.isAlert() != 0) {
            for (var monsterId : req.getMonsterEntityList()) {
                val monster = (EntityMonster) player.getScene().getEntityById(monsterId);
                if (monster == null) continue;
                if (monster.getPlayerOnBattle().isEmpty()) {
                    // 第一次有玩家进战 → 触发 Lua 事件
                    monster.getScene().getScriptManager().callEvent(
                        new ScriptArgs(monster.getGroupId(), EventType.EVENT_MONSTER_BATTLE, monster.getConfigId()));
                }
                monster.getPlayerOnBattle().add(player);
            }
        }
    }
}
```

### 14.1 为什么客户端通知警戒

客户端**看见**玩家进入怪物视野范围（基于游戏内距离计算）→ 通知服务器"进战了"。

服务器只是**记录**`playerOnBattle` 列表并触发 Lua 事件——它**不主动**判断警戒。

→ 又一例：**感知逻辑在客户端**。

---

## 15. AI 配置：客户端为主

### 15.1 服务器只存 ID

```java
@Setter private int aiId;     // ← 服务器只存这个数字
@Setter private int poseId;   // ← 姿态 ID
```

### 15.2 AI 的真实数据在哪

```
ConfigEntityMonster.ai (字符串名)
    ↓ 对应客户端的
ConfigAI/<AI Name>.json (在客户端二进制里)
    ↓ 包含
- 视野范围
- 巡逻路径
- 技能 cooldown
- 攻击优先级
- 嘲讽规则
- 状态机 (待机 → 警戒 → 战斗 → 撤退)
```

**服务器完全不知道这些细节**——它只下发 aiId, 客户端按这个 ID 加载 AI 数据自己跑。

### 15.3 反向通知机制

服务器需要知道 AI 状态时**问客户端**：
- `EvtAiSyncSkillCdNotify` —— "我这只怪的技能 CD 同步"
- `EvtAiSyncCombatThreatInfoNotify` —— "我感知到了威胁"
- `MonsterAIConfigHashNotify` —— "我用的 AI 版本哈希"

`HandlerEvtAiSyncSkillCdNotify`：
```java
public void handle(GameSession session, byte[] header, EvtAiSyncSkillCdNotify req) {
    // Auto template   ← 服务器收到但不处理
}
```

→ **大部分 AI 同步包服务器都不处理**——服务器是个**记账员**而不是 AI 控制者。

---

## 16. 反作弊薄弱处汇总

通过本篇可以推出怪物系统的**所有攻击面**：

| 攻击 | 是否有效 | 原因 |
|---|---|---|
| 伪造一击秒杀 | ✓ 有效 | damage amount 来自客户端 |
| 伪造怪物死亡 | ✗ 无效 | HP 必须扣到 0 才认死 |
| 改怪物 HP | ✗ 无效 | 服务器内存独立维护 |
| 让怪物 AI 不攻击 | ✓ 有效 (房主) | AI 在客户端 |
| 飞天穿地避开怪物 | ✓ 有效 | 位置由 host 同步 |
| 刷元素球 | ✗ 无效 | 阈值机制防刷 |
| 跳过 boss 阶段 | ✗ 无效 | Lua 事件在服务器触发 |
| 伪造击杀任务 | ✗ 无效 | onDeath 在服务器算 |

**总结**：
- 输出端薄弱（伤害可伪造）
- 状态端坚固（HP / 死亡 / 任务进度服务器掌握）

→ 这是**经典的"承认绕过, 保住账本"**安全策略。

---

## 17. 关键收获

1. **三层数据 + 一个实体**：MonsterData (配表) + Curve (等级) + Affix (词条) + ConfigEntityMonster (能力) → EntityMonster (运行时)
2. **7 种 MonsterType** 各自走不同代码路径（环境动物 = 采集，普通/Boss = 能量球）
3. **7 层属性叠加**和 Avatar 一致：base × curve × affix × compound × hpPercent × runtime
4. **11 个 fight property**：HP/攻/防 + 8 种抗性
5. **HpDrops 阈值机制**：穿过 75/50/25 各掉球一次，防刷
6. **三条生成路径**：SceneScript / Tide 车轮战 / Quest 触发
7. **武器是独立 entity**：丘丘人的斧头是 EntityWeapon
8. **damage 接收来自客户端**：伤害数字客户端算，服务器只扣 HP
9. **onDeath 触发 7 件事**：挑战 / Tide / Lua / 战令 / 任务 ×2 / 全清 / 副本 ×3 / 封印
10. **Lua 5 个事件钩子**：LIVE / DIE / HP_CHANGE / BATTLE / TIDE_DIE
11. **AI 完全在客户端**：服务器只存 aiId, 客户端按 ID 跑 AI；联机时 host 客户端控制所有怪
12. **lockHP 不死模式**：剧情战常用
13. **反作弊取舍**：伤害可伪造（输出端），HP/任务/奖励保住（账本端）

---

## 18. 一句话总结

> **怪物系统 = 配表三件套 (Excel+Curve+Affix) + Binout 能力 + 运行时实体 EntityMonster；属性 7 层叠加；服务器掌账本 (HP+死亡+掉落+事件触发)，客户端跑大脑 (AI+伤害公式+警戒感知)；onDeath 一次触发 7+ 个子系统**

> **设计哲学：每个层都尽量做"被动",一切由数据驱动——加新怪只要写一行 JSON,改平衡只要改一个 curve;服务器是事件中枢,客户端是决策引擎.**

---

**前置笔记**：
- notes/16 战斗系统 - 混合权威模型
- notes/14 SceneScript - Lua 引擎与 spawn 机制
- notes/19 副本/挑战 - ChallengeFactory.onMonsterDeath
- notes/24 Avatar 升级 - 同样的"7 层属性"模式

**关联文件**：
- `EntityMonster.java`(367) - 主运行时实体
- `MonsterData.java`(119) - 基础配表
- `MonsterCurveData.java`(33) - 等级曲线
- `MonsterAffixData.java`(27) - 词条
- `MonsterType.java` - 7 种分类枚举
- `GameEntity.java`(380) - damage / onDeath 父类逻辑
- `EnergyManager.java`(303+) - 元素球阈值掉落
- `ScriptMonsterSpawnService.java`(38) - 生成监听器
- `ScriptMonsterTideService.java`(144) - 车轮战
- `HandlerMonsterAlertChangeNotify.java` - 警戒同步
- `HandlerEvtAiSyncSkillCdNotify.java` - AI 同步 (空实现)
- `HandlerCombatInvocationsNotify.java` - 伤害入口

**研究的源代码**: 1100+ 行怪物相关代码。
