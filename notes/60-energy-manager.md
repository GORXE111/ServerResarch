# EnergyManager 元素能量系统深度剖析

> 第 60 篇：notes/16 Ability / notes/35 战斗 / notes/36 EntityAvatar 都擦过"元素能量"但从未挖通。本篇追完**完整产能-消耗-拾取经济链**：4 大产能来源 + 大招清空 + 元素球拾取转化（含真实原神同元素/异元素 × 前台/后台系数）—— **客户端权威 / 服务端账本主题（notes/35）的又一硬核样本**。

---

## 0. 为什么这一篇重要

元素能量（充能）是原神战斗循环的命脉：攒能 → 放大招 → 清空 → 再攒。grasscutter 把它实现为 **EnergyManager（401 行，BasePlayerManager）+ EntityAvatar 能量账本 + ItemUseAddEnergy 拾取转化**三段链。

之前散落的引用：
- notes/16 Ability：`ABILITY_ACTION_GENERATE_ELEM_BALL` 没追下文
- notes/35 战斗：客户端发 `EvtBeingHitInfo`，没说能量怎么算
- notes/36 EntityAvatar：`FIGHT_PROP_CUR_ELEM_ENERGY` 一笔带过

本篇统一接通，并验证 grasscutter 在战斗经济上的**"信任客户端事件、服务端管账本"**架构哲学。

---

## 1. 能量系统全图

```
┌──── 产能 4 来源 ────────────────────────────────────────────┐
│ ① 元素战技 → ABILITY_ACTION_GENERATE_ELEM_BALL              │
│      AbilityManager:151 → EnergyManager.handleGenerateElemBall│
│      → 按 avatarId 掷骰定粒子数 + 按元素定 ballId → 生成 EntityItem│
│ ② 普攻/重击 → 客户端 EvtBeingHitInfo                         │
│      HandlerCombatInvocationsNotify:45 → handleAttackHit     │
│      → 武器类型概率递增模型 → addEnergy(1.0)                  │
│ ③ 怪物受击/死亡掉球 → EntityMonster:232                       │
│      → handleMonsterEnergyDrop → HP 阈值穿越 → 生成 EntityItem│
│ ④ 任务/GM/被动 → ExecAddCurAvatarEnergy / refillTeamEnergy   │
└────────────────────────┬────────────────────────────────────┘
                         │ 元素球 = EntityItem 落在场景
                         ↓ 角色走过去拾取 (客户端判定)
┌──── 拾取转化 ItemUseAddEnergy ──────────────────────────────┐
│ 同元素=elemEnergy / 异元素=otherEnergy                       │
│ × 前台 1.0 / 后台 ratio(2→0.8 3→0.7 ≥4→0.6)                  │
│ × FIGHT_PROP_CHARGE_EFFICIENCY (充能效率)                     │
│ → EntityAvatar.addEnergy(amount, PROP_CHANGE_ENERGY_BALL)    │
└────────────────────────┬────────────────────────────────────┘
                         ↓ 账本: Math.min(cur+amount, max)
┌──── 消耗 ───────────────────────────────────────────────────┐
│ 放大招成功 → HandlerEvtDoSkillSuccNotify:21                   │
│   → handleEvtDoSkillSuccNotify → handleBurstCast             │
│   → skillId == energySkill → clearEnergy(SKILL_START)        │
│ 角色死亡 → EntityAvatar.onDeath → clearEnergy(NONE)          │
└─────────────────────────────────────────────────────────────┘
```

→ **产能 4 路 + 拾取转化 + 2 路清空** —— 完整经济闭环。

---

## 2. 产能 ①：元素战技生成粒子

`EnergyManager.handleGenerateElemBall(AbilityInvokeEntry)`：

```java
val action = AbilityActionGenerateElemBall.parseBy(invoke.getAbilityData(), version);
int itemId = 2024;   // 默认无色粒子
int amount = 2;       // 默认 2 颗

Optional<EntityAvatar> avatarEntity = getCastingAvatarEntityForEnergy(invoke.getEntityId());
if (avatarEntity.isPresent()) {
    Avatar avatar = avatarEntity.get().getAvatar();
    int avatarId = avatar.getAvatarId();
    amount = getBallCountForAvatar(avatarId);              // 掷骰定数量
    ElementType element = avatar.getSkillDepot().getElementType();
    itemId = getBallIdForElement(element);                 // 元素定 ballId
}
for (int i = 0; i < amount; i++) generateElemBall(itemId, pos, 1);
```

### 2.1 粒子数量 = 概率表掷骰（数据驱动）

```java
private int getBallCountForAvatar(int avatarId) {
    int count = 2;   // 默认
    int roll = ThreadLocalRandom.current().nextInt(0, 100);
    int percentageStack = 0;
    for (SkillParticleGenerationInfo info : skillParticleGenerationData.get(avatarId)) {
        percentageStack += info.getChance();
        if (roll < percentageStack) { count = info.getValue(); break; }
    }
    return count;
}
```

→ 数据源 `SkillParticleGeneration.json`（`initialize()` 静态加载）。
→ 例如某角色：50% 出 3 颗 / 30% 出 4 颗 / 20% 出 5 颗 —— **概率累加桶**。
→ 还原真实原神"战技粒子数随机"机制。

### 2.2 ballId = 元素映射（硬编码 switch）

```java
case Fire->2017  Water->2018  Grass->2019  Electric->2020
     Wind->2021  Ice->2022    Rock->2023   default->2024(无色)
```

→ 2017~2024 是粒子物品 ID。无元素 → 2024 无色粒子。

### 2.3 施法者溯源（EntityClientGadget owner 链）

```java
private Optional<EntityAvatar> getCastingAvatarEntityForEnergy(int invokeEntityId) {
    GameEntity entity = scene.getEntityById(invokeEntityId);
    int avatarEntityId = (!(entity instanceof EntityClientGadget g))
        ? invokeEntityId : g.getOriginalOwnerEntityId();   // ★ 沿 owner 链回溯
    return teamManager.getActiveTeam().stream()
        .filter(c -> c.getId() == avatarEntityId).findFirst();
}
```

→ **关键设计**：技能可能由"客户端小物件"（EntityClientGadget，如雷神的雷罚恶曜之眼）触发，需沿 `originalOwnerEntityId` 回溯到真正施法角色。
→ 角色被切换走时 entity 为 null → 回退用 invokeEntityId 直接当 avatar。

---

## 3. 产能 ②：普攻/重击的概率递增模型（pity 思想）

`handleAttackHit(EvtBeingHitInfo)`：客户端发"被击中"事件，服务端判定攒能。

```java
// 必须是当前出战角色打的
Optional<EntityAvatar> attacker = getCastingAvatarEntityForEnergy(attackRes.getAttackerId());
if (attacker.isEmpty() || currentAvatarEntity.getId() != attacker.get().getId()) return;
// 目标必须是真敌人 (ORDINARY/BOSS, 排除野猪鸟类)
if (targetType != MONSTER_ORDINARY && targetType != MONSTER_BOSS) return;
// ability == null 才认为是普攻/重击 (有 ability 的当作技能, 不产能)
if (ability == null) return;
generateEnergyForNormalAndCharged(attacker.get());
```

### 3.1 武器类型概率递增

```java
private void generateEnergyForNormalAndCharged(EntityAvatar avatar) {
    WeaponType wt = avatar.getAvatar().getAvatarData().getWeaponType();
    if (!avatarNormalProbabilities.containsKey(avatar))
        avatarNormalProbabilities.put(avatar, wt.getEnergyGainInitialProbability());

    int prob = avatarNormalProbabilities.getInt(avatar);
    int roll = ThreadLocalRandom.current().nextInt(0, 100);
    if (roll < prob) {                                  // 中 → 加 1 能量, 概率重置
        avatar.addEnergy(1.0f, PROP_CHANGE_ABILITY, true);
        avatarNormalProbabilities.put(avatar, wt.getEnergyGainInitialProbability());
    } else {                                            // 不中 → 概率累加
        avatarNormalProbabilities.put(avatar, prob + wt.getEnergyGainIncreaseProbability());
    }
}
```

### 3.2 WeaponType 概率表（还原真实机制）

| 武器 | 初始概率 | 每次递增 |
|---|---|---|
| 单手剑 SWORD_ONE_HAND | 10 | 5 |
| 法器 CATALYST | 0 | 10 |
| 双手剑 CLAYMORE | 0 | 10 |
| 弓 BOW | 0 | 5 |
| 长柄 POLE | 0 | 4 |

→ **这是一种 pity（保底）模型**：连续不出能量，概率单调上升，直到必出后重置。
→ 与 notes/52 Gacha 的 pity、notes/57 邮件无关——但**"概率递增直到触发"思想在 grasscutter 反复出现**（产能、抽卡、掉落）。
→ `isFlat=true` → 普攻产能不吃充能效率（真实原神普攻产能也是固定）。

---

## 4. 产能 ③：怪物受击/死亡掉球（HP 阈值穿越）

`handleMonsterEnergyDrop(monster, hpBefore, hpAfter)`（EntityMonster:232 调用）：

```java
float maxHp = monster.getFightProperty(FIGHT_PROP_MAX_HP);
float before = hpBefore / maxHp;
float after  = hpAfter  / maxHp;
for (HpDrops drop : monster.getMonsterData().getHpDrops()) {
    float threshold = drop.getHpPercent() / 100.0f;
    if (threshold < before && threshold >= after)        // ★ 这次伤害"穿过"了阈值
        generateElemBallDrops(monster, drop.getDropId());
}
if (hpAfter <= 0 && killDropId != 0)                      // 击杀掉球
    generateElemBallDrops(monster, killDropId);
```

→ **HP 阈值穿越检测**：一次伤害把怪 HP 从 80% 打到 30%，则穿过的 50%/40% 等阈值都触发掉球。
→ 数据源 `EnergyDrop.json`（dropId → List\<ballId, count\>）。
→ 同样**只对 ORDINARY/BOSS** 生效（野生动物不掉能量球）。

---

## 5. 拾取转化：ItemUseAddEnergy（真实原神系数还原）

元素球落地是 `EntityItem`（场景实体）。角色走过去拾取（客户端判定碰撞），物品 use action 走 `ItemUseAddEnergy`：

```java
case ITEM_USE_TARGET_CUR_TEAM -> {
    var team = teamManager.getActiveTeam();
    final float offFieldRatio = switch (team.size()) {   // ★ 后台衰减
        case 2 -> 0.8f;  case 3 -> 0.7f;  default -> 0.6f;
    };
    int cur = teamManager.getCurrentCharacterIndex();
    for (int i = 0; i < team.size(); i++) {
        Avatar a = team.get(i).getAvatar();
        if (i == cur) addEnergy(a, params.count);                 // 前台全额
        else          addEnergy(a, params.count * offFieldRatio); // 后台衰减
    }
}

private boolean addEnergy(Avatar avatar, float multiplier) {
    float energy = getAddEnergy(avatar.getSkillDepot()) * multiplier;
    avatar.getAsEntity().addEnergy(energy, PROP_CHANGE_ENERGY_BALL);
}
```

### 5.1 同元素 / 异元素（ItemUseAddElemEnergy）

```java
public float getAddEnergy(ElementType avatarElement) {
    return (avatarElement == this.element) ? this.elemEnergy : this.otherEnergy;
}
```

→ 配置三参数 `[元素, 同元素能量, 异元素能量]`。
→ **完美还原真实原神**：同元素粒子给本队角色更多能量、异元素给更少。
→ 叠加 5.1 后台系数 → 与官服"前台同元素 3x、后台异元素更低"机制一致。
→ `ItemUseAddAllEnergy`：无色粒子，无视元素，固定值。

### 5.2 拾取永远消耗

```java
yield true;  // Always consume elem balls
```

→ 注释明确：能量球拾取**必定消耗**（不会因满能而留在地上）。

---

## 6. 账本：EntityAvatar.addEnergy / clearEnergy

能量真正的"账"在 `EntityAvatar`（FightProperty）。

```java
public void addEnergy(float amount, PropChangeReason reason, boolean isFlat) {
    val elem = avatar.getSkillDepot().getElementType();
    float cur = getFightProperty(elem.getCurEnergyProp());
    float max = getFightProperty(elem.getMaxEnergyProp());
    if (!isFlat)                                          // ★ 非固定 → 吃充能效率
        amount *= getFightProperty(FIGHT_PROP_CHARGE_EFFICIENCY);
    float newEnergy = Math.min(cur + amount, max);        // ★ 封顶, 不溢出
    if (newEnergy != cur) {
        avatar.setCurrentEnergy(curEnergyProp, newEnergy);
        scene.broadcastPacket(new PacketEntityFightPropChangeReasonNotify(...));
    }
}

public void clearEnergy(ChangeEnergyReason reason) {
    avatar.setCurrentEnergy(curEnergyProp, 0);            // 清零
    scene.broadcastPacket(new PacketEntityFightPropUpdateNotify(...));
    if (reason == CHANGE_ENERGY_SKILL_START)              // 大招清空才发"变化原因"
        scene.broadcastPacket(new PacketEntityFightPropChangeReasonNotify(this, prop, -curEnergy, reason));
}
```

要点：
1. **充能效率缩放**：`isFlat=false` 时 `amount *= CHARGE_EFFICIENCY`（圣遗物/武器加成）。普攻产能/任务发能用 `isFlat=true` 不吃效率。
2. **封顶不溢出**：`Math.min(cur+amount, max)` —— 满能浪费。
3. **元素决定能量槽**：不同元素角色用不同 `FightProperty`（curEnergyProp/maxEnergyProp 按 ElementType）。
4. **广播而非单播**：`scene.broadcastPacket` —— 多人时队友能看到你的能量变化（notes/35 多人广播）。

---

## 7. 消耗：大招清空 + 死亡清空

### 7.1 大招

`HandlerEvtDoSkillSuccNotify:21 → handleEvtDoSkillSuccNotify → handleBurstCast`：

```java
private void handleBurstCast(Avatar avatar, int skillId) {
    if (!GAME_OPTIONS.energyUsage || !this.energyUsage) return;   // 双开关
    if (skillId == avatar.getSkillDepot().getEnergySkill())        // 是大招技能
        avatar.getAsEntity().clearEnergy(CHANGE_ENERGY_SKILL_START);
}
```

→ **客户端报告"技能成功"，服务端事后清能量** —— 典型 notes/35 客户端权威 + 服务端账本。
→ 服务端**不校验能量是否够**就让放（信任客户端）—— 私服取舍；正服会校验 `cur >= max`。

### 7.2 死亡

`EntityAvatar.onDeath → clearEnergy(CHANGE_ENERGY_NONE)` —— 角色倒下能量清零（还原真实机制）。

---

## 8. 双开关 + GM

```java
private boolean energyUsage = GAME_OPTIONS.energyUsage;   // 全局 + 玩家级双开关

public void setEnergyUsage(boolean v) {
    this.energyUsage = v;
    if (!v) refillTeamEnergy(PROP_CHANGE_GM, true);        // 关闭=立即充满全队
}
```

→ `SetPropCommand` 的 `UNLIMITED_ENERGY` GM 项 → 关闭能量消耗 + 全队充满。
→ `refillTeamEnergy`：给每个角色加 `getEnergySkillData().getCostElemVal()`（恰好一发大招的量）。
→ `ExecAddCurAvatarEnergy`（notes/43 Quest exec）：任务奖励"充满当前角色能量"。

---

## 9. 完整时序：放一发大招的能量循环

```
[元素战技] 客户端 AbilityInvoke ABILITY_ACTION_GENERATE_ELEM_BALL
  → AbilityManager:151 → handleGenerateElemBall
  → getBallCountForAvatar 掷骰=3 颗, element=Electric → ballId=2020
  → 3× generateElemBall → 3 个 EntityItem 落地

[拾取] 角色走过 (客户端碰撞) → ItemUseAddElemEnergy
  → 同元素 elemEnergy=? 异元素 otherEnergy=?
  → 前台×1.0 / 后台×0.7(3人队)
  → EntityAvatar.addEnergy ×CHARGE_EFFICIENCY → Math.min(cur+x, max)
  → broadcastPacket FightPropChangeReason

[普攻补能] 客户端 EvtBeingHitInfo → HandlerCombatInvocationsNotify:45
  → handleAttackHit → ability==null & 当前角色 & 真敌人
  → generateEnergyForNormalAndCharged: roll<prob → +1.0(isFlat) 概率重置
                                       否则 prob += 武器递增值

[能量满] cur == max

[放大招] 客户端 EvtDoSkillSucc → HandlerEvtDoSkillSuccNotify:21
  → handleEvtDoSkillSuccNotify → handleBurstCast
  → skillId == energySkill → clearEnergy(SKILL_START)
  → broadcast: 能量归 0, 变化原因 = SKILL_START

[循环] 回到攒能
```

---

## 10. 设计模式总结

### 10.1 客户端权威 / 服务端账本（notes/35 主题硬核延续）

| 客户端报告 | 服务端账本动作 |
|---|---|
| AbilityInvoke 生成粒子 | 掷骰定数量+元素，生成 EntityItem |
| EvtBeingHit 普攻命中 | 概率递增模型判定 +1 能量 |
| 怪物 HP 变化 | 阈值穿越算掉球 |
| EvtDoSkillSucc 大招成功 | clearEnergy（不校验是否够）|

→ 服务端**信任客户端战斗事件**，但**能量数值账本服务端独算**（概率、封顶、充能效率、同异元素系数）。

### 10.2 概率递增 pity 模型

→ 普攻产能 = "连续不出→概率单调升→必出后重置"，与 Gacha pity 同思想，grasscutter 反复用。

### 10.3 数据驱动

→ `SkillParticleGeneration.json`（粒子数概率桶）+ `EnergyDrop.json`（怪物掉球）+ AvatarSkillData（大招耗能）+ WeaponType 表（普攻概率）—— 4 处配置驱动，零硬编码数值（除 ballId switch）。

### 10.4 owner 链溯源

→ `EntityClientGadget.getOriginalOwnerEntityId()` 回溯真实施法者 —— 处理"召唤物触发能量"（雷神眼、菲谢尔等）。

### 10.5 还原真实机制的深度

→ 同元素/异元素 × 前台/后台系数 × 充能效率 × 武器概率表 —— grasscutter 这一块**几乎 1:1 复刻官服公式**（参考代码注释引的 keqingmains 文档）。

---

## 11. 反作弊视角

| 攻击 | 是否有效 |
|---|---|
| 改客户端连发 EvtBeingHit 刷能量 | ✓ 部分（每次仍走概率，且限当前角色+真敌人）|
| 能量不满强发大招 | ✓ 有效（handleBurstCast 不校验 cur>=max）|
| 伪造 GenerateElemBall 刷粒子 | ✓ 有效（信任 AbilityInvoke）|
| 改充能效率 FightProperty | ✗ 服务端属性账本 |
| 能量值溢出 | ✗ Math.min 封顶 |

→ 能量系统**反作弊偏弱**（信任客户端战斗事件流），属 grasscutter 私服一贯取舍；正服在大招处必校验 `cur == max`。

---

## 12. 关键收获

1. **EnergyManager 401 行 = BasePlayerManager**（非 GameSystem）—— 玩家级而非全局
2. **产能 4 来源**：元素战技粒子 / 普攻概率 / 怪物掉球 / 任务-GM
3. **粒子数 = 概率累加桶**（SkillParticleGeneration.json，掷 0~100）
4. **ballId 元素 switch**：Fire 2017…Rock 2023，无色 2024
5. **施法者沿 EntityClientGadget owner 链回溯**（处理召唤物产能）
6. **普攻产能 = 武器类型概率递增 pity**：初始概率 + 每次递增，中则 +1 重置
7. **WeaponType 内嵌概率表**：单手剑(10,5) 法器/双手剑(0,10) 弓(0,5) 长柄(0,4)
8. **isFlat=true 普攻产能不吃充能效率**（还原真机制）
9. **怪物掉球 = HP 阈值穿越检测** + 击杀掉球（EnergyDrop.json）
10. **元素球 = EntityItem 落地**，拾取走 ItemUseAddEnergy
11. **拾取系数**：同元素 elemEnergy / 异元素 otherEnergy × 前台 1.0 / 后台(2→0.8 3→0.7 ≥4→0.6)
12. **账本 EntityAvatar.addEnergy**：×CHARGE_EFFICIENCY（非 flat）→ Math.min(cur+x, max) 封顶
13. **元素决定能量槽**：curEnergyProp/maxEnergyProp 按 ElementType 取
14. **能量变化 broadcastPacket**（多人队友可见，notes/35）
15. **大招清空**：handleBurstCast skillId==energySkill → clearEnergy(SKILL_START)，**不校验能量是否够**
16. **死亡清空**：onDeath → clearEnergy(NONE)
17. **双开关**：GAME_OPTIONS.energyUsage（全局）+ player.energyUsage（玩家），关闭即全队充满
18. **客户端权威/服务端账本主题硬核样本**：信任战斗事件，独算能量数值
19. **概率递增 pity 思想跨系统复用**（产能/Gacha/掉落）
20. **几乎 1:1 复刻官服能量公式**（代码注释引 keqingmains）

---

## 13. 一句话总结

> **EnergyManager (401 行 BasePlayerManager) = 元素能量经济链 —— 产能 4 路 (元素战技概率粒子 / 普攻武器类型概率递增 pity / 怪物 HP 阈值穿越掉球 / 任务-GM); 元素球落地为 EntityItem, 拾取经 ItemUseAddEnergy 按"同/异元素 × 前台/后台系数 × 充能效率"转化, EntityAvatar.addEnergy 用 Math.min 封顶记账; 大招成功客户端报告→服务端 clearEnergy 清零 (不校验是否够) + 死亡清零.**
>
> **架构本质: notes/35 "客户端权威 / 服务端账本"主题的硬核样本——信任客户端战斗事件流, 但能量数值 (概率/封顶/充能效率/同异元素系数) 全服务端独算; 概率递增 pity 思想与 Gacha 跨系统同源; 几乎 1:1 复刻官服能量公式.**

---

**前置笔记**：
- notes/16 Ability - ABILITY_ACTION_GENERATE_ELEM_BALL 的下文
- notes/35 战斗 - 客户端权威/服务端账本主题（本篇硬核样本）
- notes/36 EntityAvatar - FightProperty 能量槽
- notes/43 Quest exec - ExecAddCurAvatarEnergy
- notes/52 Gacha - pity 概率递增思想同源
- notes/61 StaminaManager（待写）- 同为 BasePlayerManager 战斗资源，对照

**关联文件**：
- `EnergyManager.java`(401) - 产能/拾取调度核心
- `EntityAvatar.java:145-202` - addEnergy/clearEnergy 账本
- `WeaponType.java` - 武器类型概率表
- `ItemUseAddEnergy.java` + `ItemUseAddElemEnergy.java` + `ItemUseAddAllEnergy.java` - 拾取转化
- 调用点：`HandlerCombatInvocationsNotify:45`(普攻) / `EntityMonster:232`(掉球) / `AbilityManager:151`(粒子) / `HandlerEvtDoSkillSuccNotify:21`(大招) / `ExecAddCurAvatarEnergy:16`(任务)
- 数据：`SkillParticleGeneration.json` / `EnergyDrop.json`

**研究的源代码**: EnergyManager 401 行 + EntityAvatar 能量账本 58 行 + ItemUseAddEnergy 链 3 文件 + WeaponType + 5 处调用点。
