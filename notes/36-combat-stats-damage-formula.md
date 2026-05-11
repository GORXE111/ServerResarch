# 战斗状态与伤害计算深度专题

> 第 36 篇：把 notes/16 (战斗权威) 和 notes/24 (Avatar) 中浅尝的**fightProperty / 伤害公式 / 摔伤 / 体力**全部展开 —— 这是支撑所有 PVE 战斗的数学引擎。

---

## 0. 引言

前面笔记里反复出现的"fightProperty"是什么？怎么算？谁掌握？
- notes/16 说"客户端算伤害，服务器记账"——具体怎么算？
- notes/24 说"Avatar 7 层属性叠加"——具体哪 7 层？
- notes/32 说"怪物有 11 个基础属性"——和角色的有啥不同？
- notes/34 说"元素能量作为 fightProp 存"——FightProperty 总共多少个？

这一篇专攻这些数学问题：
1. FightProperty 130+ 个枚举的**完整分类全图**
2. **CompoundProperty 三层叠加公式**：FlatBase + Base × (1 + Percent) = Result
3. **Avatar.recalcStats() 9 步全流程**（最详细的属性算法）
4. **客户端 AttackResult** 数据结构
5. **摔伤公式**（服务器算！4 档：33%/50%/66%/100% MAX_HP）
6. **元素能量经济完整链**
7. **Stamina 7 类动作 + 食物/天赋减耗**
8. **客户端 vs 服务器权威**总结表
9. **反作弊薄弱地图**

---

## 1. FightProperty：130+ 个属性枚举

`FightProperty.java`（284 行） —— 这是 grasscutter 中**最庞大的枚举**之一。

### 1.1 ID 段位划分

属性按 ID 范围**分段**：

```
   1-13     基础属性 (HP/ATK/DEF + 百分比 + 速度)
  20-27     战斗修饰 (暴击/暴伤/充能/治疗等)
  28-32     特殊 (元素精通 / 物理伤害 / 防御穿透)
  40-46     7 种元素加伤 (FIRE/ELEC/WATER/GRASS/WIND/ROCK/ICE _ADD_HURT)
     47     爆头加伤
  50-56     7 种元素抗性 (_SUB_HURT)
  60-67     状态抗性 (冻结/眩晕/迟缓 + 缩短)
  70-76     7 种元素能量上限 (MAX_*_ENERGY)
  80-81     CD 减少 / 护盾消耗减少
1000-1006   7 种元素能量当前值 (CUR_*_ENERGY)
   1010     当前 HP
2000-2003   当前 MAX_HP / ATK / DEF / SPEED (复合值)
3000-3024   "NONEXTRA" 系列 (不含临时 buff 的纯净版本)
3025-3046   元素反应暴击/暴伤 (10+ 反应类型)
```

→ **总共 130+ 个枚举值**，每段都有特定语义。

### 1.2 三大类属性

**A. 平铺属性 (flatProps)**：表示**绝对值**
```java
private static final List<FightProperty> flatProps = Arrays.asList(
    FIGHT_PROP_BASE_HP, FIGHT_PROP_HP, FIGHT_PROP_BASE_ATTACK, FIGHT_PROP_ATTACK,
    FIGHT_PROP_BASE_DEFENSE, FIGHT_PROP_DEFENSE, FIGHT_PROP_HEALED_ADD,
    FIGHT_PROP_CUR_FIRE_ENERGY ... FIGHT_PROP_CUR_ROCK_ENERGY,
    FIGHT_PROP_CUR_HP, FIGHT_PROP_MAX_HP, FIGHT_PROP_CUR_ATTACK, FIGHT_PROP_CUR_DEFENSE
);
```
→ 这些是**数字本身**（如 +500 HP）。

**B. 百分比属性 (其余)**：值是**比例**（0.05 = 5%）
- `FIGHT_PROP_CRITICAL` —— 暴击率 0.5 = 50%
- `FIGHT_PROP_FIRE_ADD_HURT` —— 火伤 +50% = 0.5
- `FIGHT_PROP_CHARGE_EFFICIENCY` —— 充能效率 1.0 = 100%

**C. 复合属性 (CompoundProperty)**：由其他 prop 算出来
- `FIGHT_PROP_MAX_HP` ← BASE_HP + HP_PERCENT × BASE_HP + HP (flat)
- `FIGHT_PROP_CUR_ATTACK` ← BASE_ATTACK × ...
- `FIGHT_PROP_CUR_DEFENSE` ← BASE_DEFENSE × ...

### 1.3 速记表（圣遗物玩家最常见）

```java
shortNameMap.put("hp",       FIGHT_PROP_HP);
shortNameMap.put("atk",      FIGHT_PROP_ATTACK);
shortNameMap.put("def",      FIGHT_PROP_DEFENSE);
shortNameMap.put("hp%",      FIGHT_PROP_HP_PERCENT);
shortNameMap.put("atk%",     FIGHT_PROP_ATTACK_PERCENT);
shortNameMap.put("def%",     FIGHT_PROP_DEFENSE_PERCENT);
shortNameMap.put("em",       FIGHT_PROP_ELEMENT_MASTERY);  // 元素精通
shortNameMap.put("er",       FIGHT_PROP_CHARGE_EFFICIENCY); // 充能效率
shortNameMap.put("cr",       FIGHT_PROP_CRITICAL);          // 暴击率
shortNameMap.put("cd",       FIGHT_PROP_CRITICAL_HURT);     // 暴伤
shortNameMap.put("pyro%",    FIGHT_PROP_FIRE_ADD_HURT);
shortNameMap.put("phys%",    FIGHT_PROP_PHYSICAL_ADD_HURT);
// ... 7 种元素 ADD/SUB
```

→ 这就是命令行 `/giveart` 用的速记符。

---

## 2. CompoundProperty：三层叠加公式

### 2.1 三层叠加的核心

```java
private static Map<FightProperty, CompoundProperty> compoundProperties = Map.ofEntries(
    entry(FIGHT_PROP_MAX_HP,      new CompoundProperty(MAX_HP, BASE_HP, HP_PERCENT, HP)),
    entry(FIGHT_PROP_CUR_ATTACK,  new CompoundProperty(CUR_ATTACK, BASE_ATTACK, ATTACK_PERCENT, ATTACK)),
    entry(FIGHT_PROP_CUR_DEFENSE, new CompoundProperty(CUR_DEFENSE, BASE_DEFENSE, DEFENSE_PERCENT, DEFENSE))
);
```

每个复合属性 = **3 个分量**：
- `result` —— 最终值（如 MAX_HP）
- `base` —— 基础值（如 BASE_HP，来自配表 + 突破）
- `percent` —— 百分比加成（如 HP_PERCENT，来自圣遗物副词条 + 武器精炼）
- `flat` —— 平加值（如 HP，来自圣遗物副词条 + 武器主词条）

### 2.2 公式

`Avatar.recalcStats()` 第 586-587 行：
```java
FightProperty.forEachCompoundProperty(c -> this.setFightProperty(c.getResult(),
    this.getFightProperty(c.getFlat())                                           // 平加
    + (this.getFightProperty(c.getBase()) * (1f + this.getFightProperty(c.getPercent())))));
//        基础                              ×              百分比加成
```

数学表达：
```
MAX_HP    = HP_flat       + BASE_HP    × (1 + HP_PERCENT)
CUR_ATK   = ATK_flat      + BASE_ATK   × (1 + ATK_PERCENT)
CUR_DEF   = DEF_flat      + BASE_DEF   × (1 + DEF_PERCENT)
```

### 2.3 真实例子

假设迪卢克满级 80 + 80 突破：
- `BASE_HP = 12981` （配表 + 突破）
- `HP_PERCENT = 0.466` （圣遗物副词条 6×7.8%）
- `HP = 4780` （圣遗物副词条 4×1195）

```
MAX_HP = 4780 + 12981 × (1 + 0.466)
       = 4780 + 12981 × 1.466
       = 4780 + 19030
       = 23810
```

→ 这就是为什么"百分比加成 → 收益更大"——它放大 BASE 而非 flat。

### 2.4 4 件套 / 2 件套对面板的影响

圣遗物 4 件套（4*7.8% HP%）= +31.2% HP%：
- 不堆 HP% 时 4 套 = 仅 +31.2% × BASE_HP
- 堆 HP% 圣遗物 4 套 + 主词条 HP% (46.6%) = +77.8%
- 收益是 **HP%相加然后乘 BASE**，不是各乘各的

→ 4 件套效果**和副词条 HP% 加和后才放大** —— 这是原神战斗的核心数学。

---

## 3. Avatar.recalcStats() 完整 9 步

`Avatar.java:404-600` 是**最复杂的方法之一**。完整流程：

### 步骤 1：准备 / 保留状态
```java
var data = this.getAvatarData();
var promoteData = GameData.getAvatarPromoteData(...);
var setMap = new Int2IntOpenHashMap();

// 保留 HP 百分比（避免重算后满血/空血跳变）
float hpPercent = MAX_HP > 0 ? CUR_HP / MAX_HP : 1f;

// 保留当前元素能量
float currentEnergy = (skillDepot != null) ? 
    this.getFightProperty(skillDepot.getElementType().getCurEnergyProp()) : 0f;
```

### 步骤 2：清空 + 设基础属性
```java
this.getFightProperties().clear();

this.setFightProperty(FIGHT_PROP_BASE_HP,         data.getBaseHp(this.getLevel()));        // 等级曲线
this.setFightProperty(FIGHT_PROP_BASE_ATTACK,     data.getBaseAttack(this.getLevel()));
this.setFightProperty(FIGHT_PROP_BASE_DEFENSE,    data.getBaseDefense(this.getLevel()));
this.setFightProperty(FIGHT_PROP_CRITICAL,        data.getBaseCritical());                  // 5%
this.setFightProperty(FIGHT_PROP_CRITICAL_HURT,   data.getBaseCriticalHurt());              // 50%
this.setFightProperty(FIGHT_PROP_CHARGE_EFFICIENCY, 1f);                                    // 100%
```

→ 这设的是"角色固有底子"——5% 暴击 / 50% 暴伤 / 100% 充能。

### 步骤 3：突破属性 (PromoteData)
```java
if (promoteData != null) {
    for (FightPropData fightPropData : promoteData.getAddProps()) {
        this.addFightProperty(fightPropData.getProp(), fightPropData.getValue());
    }
}
```
→ 6 段突破每段给的"+24% ATK"、"+19.4% Pyro DMG"等。

### 步骤 4：圣遗物 5 件（主词条 + 副词条 + 套装计数）
```java
for (int slotId = 1; slotId <= 5; slotId++) {
    GameItem equip = this.getEquipBySlot(slotId);
    if (equip == null) continue;
    
    // 4.1 主词条
    ReliquaryMainPropData mainPropData = ...;
    ReliquaryLevelData levelData = GameData.getRelicLevelData(rankLevel, level);
    this.addFightProperty(mainPropData.getFightProp(), levelData.getPropValue(...));
    
    // 4.2 副词条 (最多 4 条)
    for (int appendPropId : equip.getAppendPropIdList()) {
        ReliquaryAffixData affixData = GameData.getReliquaryAffixDataMap().get(appendPropId);
        this.addFightProperty(affixData.getFightProp(), affixData.getPropValue());
    }
    
    // 4.3 计数同套装
    if (equip.getItemData().getSetId() > 0) {
        setMap.addTo(equip.getItemData().getSetId(), 1);
    }
}
```

### 步骤 5：套装效果（2 件套 / 4 件套）
```java
setMap.forEach((setId, amount) -> {
    ReliquarySetData setData = GameData.getReliquarySetDataMap().get(setId);
    if (setData == null) return;
    
    val setNeedNum = setData.getSetNeedNum();   // 通常是 [2, 4]
    for (int setIndex = 0; setIndex < setNeedNum.length; setIndex++) {
        if (amount < setNeedNum[setIndex]) break;
        
        int affixId = (setData.getEquipAffixId() * 10) + setIndex;
        EquipAffixData affix = GameData.getEquipAffixDataMap().get(affixId);
        
        // 加 fight prop
        for (FightPropData prop : affix.getAddProps()) {
            this.addFightProperty(prop.getProp(), prop.getValue());
        }
        // 加额外能力 (4 件套触发的 buff 通过 ability 实现)
        this.addToExtraAbilityEmbryos(affix.getOpenConfig(), true);
    }
});
```

### 步骤 6：武器（曲线 + 突破 + 精炼）
```java
GameItem weapon = this.getWeapon();
if (weapon != null) {
    // 6.1 等级曲线 (主词条/副词条按曲线插值)
    WeaponCurveData curveData = GameData.getWeaponCurveDataMap().get(weapon.getLevel());
    for (WeaponProperty weaponProperty : weapon.getItemData().getWeaponProperties()) {
        this.addFightProperty(weaponProperty.getPropType(),
            weaponProperty.getInitValue() * curveData.getMultByProp(weaponProperty.getType()));
    }
    
    // 6.2 武器突破 (5 段)
    WeaponPromoteData wepPromoteData = ...;
    for (FightPropData prop : wepPromoteData.getAddProps()) {
        this.addFightProperty(prop.getProp(), prop.getValue());
    }
    
    // 6.3 精炼 (1-5 阶, 影响武器被动效果)
    for (int af : weapon.getAffixes()) {
        int affixId = (af * 10) + weapon.getRefinement();
        EquipAffixData affix = ...;
        for (FightPropData prop : affix.getAddProps()) {
            this.addFightProperty(prop.getProp(), prop.getValue());
        }
        this.addToExtraAbilityEmbryos(affix.getOpenConfig(), true);
    }
}
```

### 步骤 7：固有天赋 (Proud Skills)
```java
this.getProudSkillList().clear();
for (InherentProudSkillOpens openData : skillDepot.getInherentProudSkillOpens()) {
    if (openData.getNeedAvatarPromoteLevel() <= this.getPromoteLevel()) {
        int proudSkillId = (openData.getProudSkillGroupId() * 100) + 1;
        this.getProudSkillList().add(proudSkillId);
    }
}

for (int proudSkillId : this.getProudSkillList()) {
    ProudSkillData proudSkillData = ...;
    for (FightPropData prop : proudSkillData.getAddProps()) {
        this.addFightProperty(prop.getProp(), prop.getValue());
    }
    this.addToExtraAbilityEmbryos(proudSkillData.getOpenConfig());
}
```
→ "突破 4 加 +0.96% 暴击" 这类。

### 步骤 8：命之座 (Constellations)
```java
this.getTalentIdList().intStream()
    .mapToObj(GameData.getAvatarTalentDataMap()::get)
    .filter(Objects::nonNull)
    .map(AvatarTalentData::getOpenConfig)
    .filter(Objects::nonNull)
    .forEach(this::addToExtraAbilityEmbryos);
```
→ 6 个命座，每个加额外 ability（不直接加 prop，通过 ability 影响）。

### 步骤 9：复合属性计算 + HP 恢复
```java
// 复合属性 (MAX_HP / CUR_ATK / CUR_DEF) 一次性算
FightProperty.forEachCompoundProperty(c -> this.setFightProperty(c.getResult(),
    this.getFightProperty(c.getFlat()) + 
    (this.getFightProperty(c.getBase()) * (1f + this.getFightProperty(c.getPercent())))));

// fightPropOverrides (GM 命令设置的强制覆盖)
this.fightProperties.putAll(this.fightPropOverrides);

// 按原 HP 百分比恢复
this.setFightProperty(CUR_HP, MAX_HP * hpPercent);
```

### 9 层叠加全图

```
[Layer 1]  AvatarData.getBaseXxx(level)         ← 等级曲线基础
[Layer 2]  + PromoteData.addProps               ← 6 段突破
[Layer 3]  + ReliquaryMainProp × 5              ← 圣遗物主词条
[Layer 4]  + ReliquaryAffix × 20                ← 圣遗物副词条 (5 × 4)
[Layer 5]  + ReliquarySetBonus (2套/4套)        ← 套装效果
[Layer 6]  + WeaponCurve(level)                 ← 武器主副词条按等级
[Layer 7]  + WeaponPromote + Refinement         ← 武器突破 + 精炼
[Layer 8]  + InherentProudSkill                 ← 固有天赋 (突破解锁)
[Layer 9]  + AvatarTalent (命座)                ← 通过 ability 加
+ Compound (MAX_HP / CUR_ATK / CUR_DEF 计算)
+ Runtime override (临时 buff/debuff)
```

→ notes/24 说的 7 层其实**是 9 层**——加上 InherentProudSkill 和 Constellation 才完整。

---

## 4. Monster 属性 vs Avatar 属性对比

| 维度 | EntityMonster | EntityAvatar |
|---|---|---|
| 基础属性 | 11 个 (HP/ATK/DEF + 8 抗性) | 5 个 (HP/ATK/DEF + 暴击 + 暴伤) |
| 等级缩放 | MonsterCurve 1 表 | AvatarBaseValueCurve 1 表 + Promote 6 段 |
| 装备 | weaponId (单件) | 5 圣遗物 + 1 武器 |
| 词条 | MonsterAffix N 个 | 圣遗物副词条 20 条 |
| 精炼 | 无 | 武器精炼 1-5 阶 |
| 突破 | 无 | 6 段 |
| 天赋 | 无 | 8 项 (3 主天赋 + 5 固有) |
| 命座 | 无 | 6 项 |
| 套装 | 无 | 5 件套 |
| 元素能量 | 无（怪物没大招）| 7 种元素能量条 |
| 充能效率 | 无 | 影响能量恢复 |
| 公共属性 | MAX_HP / CUR_HP | MAX_HP / CUR_HP + 7 元素能量 |
| 抗性 | 8 种 SUB_HURT | 8 种 SUB_HURT (默认 0) |
| 加伤 | 通过 affix | 7 元素 + 物理 + 元素反应 |
| Layer 数 | 7 层 | 9 层 |

→ **角色比怪物复杂 5 倍**，但**核心叠加公式一致**（base × curve + flat + percent）。

---

## 5. 客户端 AttackResult：伤害数字怎么来

### 5.1 客户端发送格式

```java
// HandlerCombatInvocationsNotify (notes/16 提到的)
case COMBAT_EVT_BEING_HIT -> {
    val hitInfo = EvtBeingHitInfo.parseBy(entry.getCombatData(), session.getVersion());
    val attackResult = hitInfo.getAttackResult();
    // attackResult.getAttackerId()
    // attackResult.getDefenseId()
    // attackResult.getDamage()      ← ★ 客户端算的伤害
    // attackResult.getDamageShield() ← 护盾承伤
    // attackResult.getElementType() ← 元素类型
    // attackResult.getAbilityIdentifier() ← 触发的能力 ID
}
```

### 5.2 服务器处理

```java
// 无敌检查
if (attackResult.getAttackerId() != player.getTeamManager().getCurrentAvatarEntity().getId() &&
    player.getAbilityManager().isAbilityInvulnerable()) break;

// 记录伤害 (后续路由)
player.getAttackResults().add(attackResult);

// 触发能量经济
player.getEnergyManager().handleAttackHit(hitInfo);
```

### 5.3 客户端的"伤害公式"

虽然在客户端，但**主流公式**是：

```
DMG = (Talent_DMG × ATK + Talent_Flat) × Crit_Mult × DMG_Bonus_Mult × Element_Reaction_Mult × DEF_Mult × RES_Mult

Crit_Mult        = (1 + CRIT_HURT) if 命中暴击, else 1
DMG_Bonus_Mult   = (1 + ADD_HURT_total + ELEM_ADD_HURT + PHYSICAL_ADD_HURT)
DEF_Mult         = Lv_atk / (Lv_atk + Lv_def × (1 - DEF_IGNORE) - DEF_DELTA)
RES_Mult         = 
    if RES < 0      : 1 - RES/2
    if RES < 0.75   : 1 - RES
    else            : 1 / (4×RES + 1)
Element_Reaction_Mult = (取决于反应类型, 见下)
```

→ 这套公式在客户端跑（性能要求），但**所有变量都用 fightProperty**——服务器和客户端共享同一组 prop。

### 5.4 元素反应（不展开数学）

12+ 种反应，每种公式不同：
- **蒸发** (Pyro+Hydro / Hydro+Pyro): ×1.5 or ×2.0
- **融化** (Pyro+Cryo / Cryo+Pyro): ×1.5 or ×2.0  
- **超载** (Pyro+Electro): 固定伤害公式
- **超导** (Cryo+Electro): 减 40% 物理抗性
- **感电** (Hydro+Electro): 持续伤害
- **结晶** (Geo + 任意): 生成护盾
- **燃烧** (Pyro+Dendro): 持续 Pyro 伤害
- **绽放** (Hydro+Dendro): 生成 草原核
- **激化** (Electro+Dendro): 增加 Electro/Dendro 伤害
- ...

每种反应都有自己的 ELEM_REACT_CRITICAL / HURT 属性 (`FightProperty` 3025-3046)。

---

## 6. 摔伤公式：服务器**亲自**算

`HandlerCombatInvocationsNotify.handleFallOnGround()` 是少数**服务器算伤害**的地方：

```java
private void handleFallOnGround(GameSession session, GameEntity entity, MotionState motionState) {
    if (session.getPlayer().inGodmode()) return;
    
    // 200ms 时间窗 (防止伪造)
    int maxDelay = 200;
    long actualDelay = System.currentTimeMillis() - cachedLandingTimeMillisecond;
    if (actualDelay > maxDelay) return;
    
    float currentHP = entity.getFightProperty(CUR_HP);
    float maxHP = entity.getFightProperty(MAX_HP);
    float damageFactor = 0;
    
    // ★ 4 档摔伤
    if (cachedLandingSpeed < -23.5) damageFactor = 0.33f;   // 33% MAX_HP
    if (cachedLandingSpeed < -25)   damageFactor = 0.5f;    // 50%
    if (cachedLandingSpeed < -26.5) damageFactor = 0.66f;   // 66%
    if (cachedLandingSpeed < -28)   damageFactor = 1f;      // 100% (一击毙命)
    
    float damage = maxHP * damageFactor;
    float newHP = currentHP - damage;
    if (newHP < 0) newHP = 0;
    
    entity.setFightProperty(CUR_HP, newHP);
    entity.getWorld().broadcastPacket(new PacketEntityFightPropUpdateNotify(entity, CUR_HP));
    
    if (newHP == 0) {
        session.getPlayer().getStaminaManager().killAvatar(session, entity, PlayerDieType.PLAYER_DIE_FALL);
    }
    cachedLandingSpeed = 0;
}
```

### 6.1 为什么摔伤服务器算

- ✗ 怪物伤害可伪造（输出端） → 客户端权威
- ✓ 摔伤**绝对不能伪造** → 服务器权威
- ✓ 摔伤公式简单，服务器算得起
- ✓ 防"作弊不死"

### 6.2 4 档阈值

| LandingSpeed (Y 速度) | 摔伤 |
|---|---|
| > -23.5 | 0% (无伤害) |
| -23.5 ~ -25 | 33% MAX_HP |
| -25 ~ -26.5 | 50% MAX_HP |
| -26.5 ~ -28 | 66% MAX_HP |
| < -28 | 100% MAX_HP (秒杀) |

→ 这就是"摔了三段不死，再摔一下死了"的精确机制。

### 6.3 200ms 窗口防作弊

```java
if (actualDelay > maxDelay) return;   // ← 超过 200ms 丢弃
```

**为什么需要**：
- 玩家跳下悬崖 → 客户端发 `MOTION_LAND_SPEED` 通知落地速度
- 200ms 内应该收到 `MOTION_FALL_ON_GROUND`（着地）
- 如果作弊客户端**只发 LAND_SPEED 不发 FALL_ON_GROUND** → 200ms 后丢弃，不扣血

→ 这是 grasscutter 少有的"主动反作弊"。

---

## 7. 元素能量经济：完整链

回顾 §1.1 的能量段：

```
FIGHT_PROP_MAX_FIRE_ENERGY (70)    ... MAX_ROCK_ENERGY (76)    ← 7 种元素上限
FIGHT_PROP_CUR_FIRE_ENERGY (1000)  ... CUR_ROCK_ENERGY (1006)  ← 7 种元素当前
FIGHT_PROP_CHARGE_EFFICIENCY (23)                              ← 充能效率
```

### 7.1 完整能量流（5 路）

```
[Source 1] 自动 NA/CA 充能 (notes/16)
   生效条件: 当前角色普通攻击命中怪物
   代码: EnergyManager.generateEnergyForNormalAndCharged
   概率: 由 weaponType 决定 (单手剑 70%, 双手剑 80%, etc.)
   
[Source 2] 元素技能命中产元素球
   生效条件: 角色 E 技能命中怪物
   代码: EnergyManager.handleGenerateElemBall
   数量: 大多数角色 2-3 个 (按 SkillParticleGeneration.json)
   
[Source 3] 怪物死亡 HpDrops 阈值掉球 (notes/32)
   生效条件: 怪物 HP 穿过配置的阈值 (75/50/25 等)
   代码: EnergyManager.handleMonsterEnergyDrop
   位置: 怪物处生成 EntityItem (元素球)
   
[Source 4] 武器被动 (西风系列等)
   生效条件: 武器精炼触发
   代码: 通过 ability 加 energy
   
[Source 5] 圣遗物 (绝缘四件套等)
   生效条件: 套装条件
   代码: 通过 ability 加 energy
```

### 7.2 充能效率作用点

```java
public void addEnergy(float amount, PropChangeReason reason, boolean isFlat) {
    if (!isFlat) {
        amount *= this.getFightProperty(FightProperty.FIGHT_PROP_CHARGE_EFFICIENCY);
    }
    // ...
}
```

→ 所有"非平加"能量都乘 ER。
→ 这就是为什么"充能流"配队需要堆 ER 200%+。

### 7.3 大招消耗

```java
public void clearEnergy(ChangeEnergyReason reason) {
    this.avatar.setCurrentEnergy(curEnergyProp, 0);   // 直接置零
}
```

→ 满了立刻放，否则超过 MAX 的部分**不存（capped 在 max）**。

---

## 8. Stamina 体力系统

### 8.1 动作分类 (8 大类)

`StaminaManager` 把所有 `MotionState`（80+ 种）**归到 8 类**：

| 类别 | 状态 | 行为 |
|---|---|---|
| CLIMB | 攀爬 | 持续消耗 |
| DASH | 冲刺 | 持续消耗 |
| FLY | 飞行 | 持续消耗 |
| SWIM | 游泳 | 持续消耗 |
| SKIFF | 浪船 | 浪船体力另算 |
| RUN | 跑步 | **不消耗**（恢复） |
| WALK | 走 | 不消耗（恢复） |
| STANDBY | 待机 | 不消耗（恢复） |
| OTHER | 跳/拔刀等 | 一次性扣 |

### 8.2 食物减耗 (`*FoodReductionMap`)

```java
private static final HashMap<Integer, Float> ClimbFoodReductionMap = new HashMap<>() {{
    put(0, 0.8f); // 攀爬食物减 20%
}};
private static final HashMap<Integer, Float> DashFoodReductionMap = ...   // 冲刺食物减
private static final HashMap<Integer, Float> FlyFoodReductionMap = ...    // 飞行食物减
private static final HashMap<Integer, Float> SwimFoodReductionMap = ...   // 游泳食物减
```

→ 阿贝多 / 莫娜 / 香菱 等做的"减体力"食物作用机制。

### 8.3 天赋减耗 (`*TalentReductionMap`)

```java
private static final HashMap<Integer, Float> ClimbTalentReductionMap = new HashMap<>() {{
    put(262301, 0.8f);   // ★ 阿贝多的固有天赋 1: 攀爬体力消耗 -20%
}};
```

→ 角色固有天赋的"减体力"效果用这个表。

### 8.4 体力上限

```java
public final static int GlobalCharacterMaximumStamina = PlayerProperty.PROP_MAX_STAMINA.getMax();
public final static int GlobalVehicleMaxStamina = PlayerProperty.PROP_MAX_STAMINA.getMax();
```

体力上限**不是 fightProperty**——是 PlayerProperty（属性体系跟战斗分开）：
- `PROP_CUR_STAMINA` - 当前
- `PROP_MAX_STAMINA` - 上限
- `PROP_PERSIST_STAMINA` - 永久增加（声望奖励等）

### 8.5 客户端检测 + 服务器记账

体力消耗类似伤害：
- **客户端检测玩家动作** → 计算消耗
- **客户端发包通知服务器** → 服务器更新 PlayerProperty
- 服务器做合理性检查（不能超过 max / 不能负数）

---

## 9. 客户端 vs 服务器 权威：完整边界表

| 计算项 | 谁算 | 理由 |
|---|---|---|
| **HP 余额** | 服务器 | 反作弊底线 |
| **伤害数字** | 客户端 | 性能（实时）|
| **暴击判定** | 客户端 | 性能 |
| **元素反应** | 客户端 | 性能 |
| **角色属性 (recalcStats)** | 服务器 | 装备 / 圣遗物在服务器 |
| **怪物属性 (recalcStats)** | 服务器 | 怪物 spawn 时算 |
| **摔伤** | **服务器** | 反作弊（公式简单）|
| **淹死** | 服务器 | 反作弊 |
| **能量经济** | 服务器 | 影响大招循环 |
| **位置/旋转** | 客户端 (host) | 实时性 |
| **AI 决策** | 客户端 (host) | 性能 |
| **AI 状态机** | 客户端 (host) | 性能 |
| **技能释放** | 客户端 | 性能 |
| **技能 CD** | 客户端 | 性能（服务器只验证粗略合理性）|
| **任务进度** | 服务器 | 反作弊 |
| **物品获取** | 服务器 | 反作弊（addItem 入口）|
| **死亡判定** | 服务器 (HP=0 时) | 反作弊 |
| **复活** | 服务器 | 反作弊 |
| **体力消耗** | 客户端检测 + 服务器记账 | 混合 |
| **传送** | 服务器 (验证条件) | 反作弊 |

→ **规律**：账本类（HP/进度/物品/死亡）服务器掌握 / 实时类（伤害/AI/技能/位置）客户端运行。

---

## 10. 反作弊薄弱地图

### 10.1 可被作弊的点

| 攻击向量 | 是否有效 | 原因 |
|---|---|---|
| 伪造一击 999999 伤害 | ✓ 有效 | 客户端发 damage 数字 |
| 伪造暴击触发 | ✓ 有效 | 客户端判定 |
| 修改元素反应倍率 | ✓ 有效 | 客户端公式 |
| 飞天遁地 | ✓ 有效 | 位置由 host 同步 |
| 无视摔伤 | ✗ 部分有效 | 200ms 窗口防, 但可被绕 |
| 改 fightProp（如 MAX_HP）| ✗ 无效 | 服务器存 |
| 给自己加圣遗物 | ✗ 无效 | inventory.addItem 在服务器 |
| 伪造任务完成 | ✗ 无效 | queueEvent 在服务器 |
| 伪造击杀奖励 | ✗ 无效 | onDeath 在服务器 |
| 跳过 boss 阶段 | ✗ 无效 | Lua 事件服务器算 |
| 直接编辑 DB | ✓ 完全有效 | 需服务器访问权限 |

### 10.2 grasscutter 的设计取舍

grasscutter 作为**开源私服**：
- ✓ 主要保护"账本"（反作弊底线）
- ✗ 不保护"输出"（输出端能伪造）
- ✗ 没有 anti-cheat 客户端检测
- ✗ 没有"异常伤害"检测（如 1 帧打出 boss 100% HP）

**米哈游正服**肯定有的（但 grasscutter 没）：
- 伤害合理性 (DMG 不超过 ATK × 系数)
- 暴击触发频率检测
- 位置插值校验
- 客户端反调试 (Trojan, mhyprot 等)
- 异常崩溃日志收集
- 服务端"行为画像"机器学习

---

## 11. 关键收获

1. **FightProperty 130+ 个** 按 ID 段分类：基础 / 修饰 / 元素加伤 / 元素抗性 / 元素能量 / 元素反应 / NONEXTRA
2. **CompoundProperty 公式**：`Result = Flat + Base × (1 + Percent)` —— 是原神所有伤害的核心
3. **百分比"加和后才乘"**：4 件套 + 副词条 HP% 加起来再乘 BASE_HP，不是各乘各的
4. **Avatar.recalcStats 9 层叠加**：基础 → 突破 → 圣遗物主 → 圣遗物副 → 套装 → 武器曲线 → 武器精炼 → 固有天赋 → 命座 → 复合公式
5. **角色比怪物复杂 5 倍**：但**核心公式一致** (base × curve + flat + percent)
6. **客户端发 AttackResult**：服务器只验证无敌 + 加到 attackResults + 触发能量经济
7. **摔伤服务器算**：4 档 33%/50%/66%/100% MAX_HP，**Y 速度阈值** -23.5/-25/-26.5/-28
8. **200ms 时间窗防作弊**：MOTION_LAND_SPEED + MOTION_FALL_ON_GROUND 必须连续到达
9. **元素能量 5 路来源**：NA/CA + 元素球 + HpDrops + 武器被动 + 圣遗物
10. **充能效率乘所有非平加能量**
11. **Stamina 8 类动作 + 食物/天赋减耗表**
12. **PlayerProperty 与 FightProperty 分离**：体力走 PlayerProperty
13. **权威边界**：账本（HP/进度/物品）服务器掌握 / 实时（伤害/AI/技能）客户端运行
14. **反作弊薄弱**：grasscutter 主要靠"账本"防作弊，输出端可伪造

---

## 12. 一句话总结

> **战斗数学引擎 = 130+ FightProperty + CompoundProperty 三层公式 (Flat + Base × (1+Percent))；Avatar.recalcStats 9 层叠加 (基础/突破/圣遗物 4 项/武器 3 项/天赋/命座); 怪物 7 层简化; 摔伤是少数服务器亲自算的伤害 (4 档 + 200ms 反作弊窗); 客户端发 AttackResult, 服务器只记账; 元素能量 5 路来源 + ER 倍率 + 大招清零无溢出.**
> 
> **设计哲学: 把"账本"和"输出"分离——账本 (HP/属性/进度) 服务器掌握做反作弊底线, 输出 (伤害/AI/暴击/反应) 客户端跑求实时性能, 极少数高价值反作弊点 (摔伤) 服务器亲自算.**

---

**前置笔记**：
- notes/16 战斗系统 - 混合权威总览
- notes/24 Avatar 升级 - 装备 / 突破 / 命座
- notes/32 怪物系统 - 7 层属性 + HpDrops 阈值
- notes/34 EntityAvatar - 元素能量作 fightProp

**关联文件**：
- `FightProperty.java`(284) - 130+ 属性枚举
- `Avatar.java`(953, 第 400-600 行) - recalcStats 9 步
- `EntityMonster.java`(367) - 怪物属性叠加
- `HandlerCombatInvocationsNotify.java`(163) - 战斗包入口 + 摔伤
- `EnergyManager.java`(303+) - 元素能量经济
- `StaminaManager.java`(150+) - 体力 8 类动作

**研究的源代码**: 1500+ 行属性 / 伤害 / 体力相关代码。
