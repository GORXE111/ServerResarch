# 16 · Combat / Ability 系统 · 混合权威的具体落地

任务系统、Talk 系统、Scene Script 系统都是**离散事件驱动**的。Combat 系统是不同的——它是**实时高频数据流**，混合权威模型最复杂的地方。

> 代码：`game/entity/`、`game/ability/`、`game/managers/energy/`、`game/managers/stamina/`、`server/packet/recv/HandlerCombat*.java`、`HandlerAbility*.java`

---

## 1. Combat 系统的核心总线：`CombatInvocationsNotify`

客户端**每帧**把战斗相关事件批量打包发上来：

```java
// HandlerCombatInvocationsNotify.java:21
public void handle(GameSession session, byte[] header, CombatInvocationsNotify notif) {
    for (CombatInvokeEntry entry : notif.getInvokeList()) {
        switch (entry.getArgumentType()) {
            case COMBAT_EVT_BEING_HIT     -> handleBeingHit(...)
            case ENTITY_MOVE              -> handleMove(...)
            case COMBAT_ANIMATOR_PARAMETER_CHANGED -> handleAnimator(...)
            ...
        }
        // 转发给同房间其他玩家 (multiplayer)
        session.getPlayer().getCombatInvokeHandler().addEntry(entry.getForwardType(), entry);
    }
}
```

**这一个 packet 含数十种 event 类型**，是战斗数据的"firehose"。每个 entry 都会被：
1. **服务器自己处理一次**（应用 HP 变化、记录 attackResult、能量计算等）
2. **转发给同房间玩家**（联机同步）

类似的总线还有：
- `AbilityInvocationsNotify` - 技能调用（buff、modifier、元素反应、粒子生成）
- `EvtDoSkillSuccNotify` - 技能成功施放
- `EvtBeingHitNotify` - 受击事件单独通道

---

## 2. 混合权威：谁算什么（具体到代码）

回应 notes/01 提到的混合权威模型——这里给出**代码层面的明确分界**。

### 2.1 客户端算（服务器接收即应用）

```java
case COMBAT_EVT_BEING_HIT -> {
    val hitInfo = EvtBeingHitInfo.parseBy(...);
    val attackResult = hitInfo.getAttackResult();
    // ... anti-cheat check ...
    player.getAttackResults().add(attackResult);     // ← 接受客户端给的伤害结果
    player.getEnergyManager().handleAttackHit(hitInfo);
}
```

→ **客户端发"我被打了多少血"，服务器存下来**。**伤害值是客户端算的**——这就是为什么外挂能"一击秒杀"（修改本地 attackResult）但服务器无法事先察觉，要靠后置检查。

服务器接受的 attackResult 信息：
- attackerId（谁打的）
- defenderId（谁被打）
- damage（伤害值）
- elementType（元素类型）
- isCrit / isElementReaction 等元数据

### 2.2 服务器自己算

```java
// 摔伤是服务器算的!
private void handleFallOnGround(GameSession session, GameEntity entity, MotionState motionState) {
    if (cachedLandingSpeed < -23.5)  damageFactor = 0.33f;
    if (cachedLandingSpeed < -25)    damageFactor = 0.5f;
    if (cachedLandingSpeed < -26.5)  damageFactor = 0.66f;
    if (cachedLandingSpeed < -28)    damageFactor = 1f;     // 秒杀
    
    float damage = maxHP * damageFactor;
    float newHP = currentHP - damage;
    entity.setFightProperty(FightProperty.FIGHT_PROP_CUR_HP, newHP);
    entity.getWorld().broadcastPacket(new PacketEntityFightPropUpdateNotify(entity, FIGHT_PROP_CUR_HP));
    
    if (newHP == 0) {
        session.getPlayer().getStaminaManager().killAvatar(session, entity, PlayerDieType.PLAYER_DIE_FALL);
    }
}
```

→ **客户端只发"我现在 motionState=MOTION_LAND_SPEED, speed=-30"，服务器自己用阈值算伤害**。这是为什么开飞天外挂的玩家落地会"莫名其妙血掉光"。

### 2.3 服务器仲裁（接收 + 校验 + 应用）

```java
case COMBAT_EVT_BEING_HIT -> {
    Player player = session.getPlayer();
    // ★ 反作弊检查：玩家无敌时不应该被打
    if (attackResult.getAttackerId() != player.getTeamManager().getCurrentAvatarEntity().getId() &&
        player.getAbilityManager().isAbilityInvulnerable()) break;
    // 通过检查才应用
    player.getAttackResults().add(attackResult);
    ...
}
```

→ **客户端报告 + 服务器拒绝异常**。无敌期间被外部打 = 拒绝。

### 2.4 总结表

| 内容 | 算法在 | 服务器角色 |
|---|---|---|
| **伤害数值** | 客户端 | 接收+广播 |
| **元素反应类型** | 客户端 | 接收+广播 |
| **元素附着** | 客户端 | 不参与 |
| **角色面板属性**（攻击力、暴击等） | 客户端 | 仅最后一次 sync |
| **HP（实际血条）** | 服务器 | 权威 |
| **能量** | 服务器 | 权威（基于客户端报告的 BeingHit 计算）|
| **体力** | 服务器 | 权威 |
| **摔伤** | 服务器 | 完全自己算 |
| **死亡判定** | 服务器 | 看 HP 是否 0 |
| **buff/debuff modifier** | 客户端 | 接收+转发同步 |
| **元素粒子生成** | 服务器 | 权威 + 概率算法 |
| **怪物 HP** | 客户端报告 + 服务器存 | 权威存档 |

---

## 3. AbilityManager：技能 + buff 子系统

AbilityManager 处理所有"非物理伤害"的事件：

```java
// AbilityManager.java:131
public void onAbilityInvoke(AbilityInvokeEntry invoke) throws Exception {
    switch (invoke.getArgumentType()) {
        case ABILITY_META_OVERRIDE_PARAM         -> handleOverrideParam(invoke);
        case ABILITY_META_REINIT_OVERRIDEMAP     -> handleReinitOverrideMap(invoke);
        case ABILITY_META_MODIFIER_CHANGE        -> handleModifierChange(invoke);
        case ABILITY_MIXIN_COST_STAMINA          -> handleMixinCostStamina(invoke);
        case ABILITY_ACTION_GENERATE_ELEM_BALL   -> handleGenerateElemBall(invoke);
        case ABILITY_META_GLOBAL_FLOAT_VALUE     -> handleGlobalFloatValue(invoke);
        case ABILITY_META_MODIFIER_DURABILITY_CHANGE -> handleModifierDurabilityChange(invoke);
        case ABILITY_META_ADD_NEW_ABILITY        -> handleAddNewAbility(invoke);
        case ABILITY_META_TRIGGER_ELEMENT_REACTION -> handleTriggerElementReaction(invoke);
        default -> {}
    }
}
```

### 3.1 关键事件类型

| 类型 | 含义 |
|---|---|
| `ABILITY_META_MODIFIER_CHANGE` | buff/debuff 增删（如开元素爆发后的 30 秒攻击力提升） |
| `ABILITY_ACTION_GENERATE_ELEM_BALL` | 元素粒子生成（元素战技产物） |
| `ABILITY_META_TRIGGER_ELEMENT_REACTION` | 元素反应触发（蒸发/融化/超载/超导...） |
| `ABILITY_META_ADD_NEW_ABILITY` | 动态添加技能（命座解锁、武器被动激活） |
| `ABILITY_MIXIN_COST_STAMINA` | 体力消耗（冲刺、攀爬） |
| `ABILITY_META_OVERRIDE_PARAM` | 覆盖技能参数（如圣遗物套装效果改变倍率） |

### 3.2 Action handlers（注解注册，又一次同构）

```java
// AbilityManager.java:69
public static void registerHandlers() {
    var handlerClasses = Grasscutter.reflector.getSubTypesOf(AbilityActionHandler.class);
    for (var obj : handlerClasses) {
        if (obj.isAnnotationPresent(AbilityAction.class)) {
            AbilityModifierAction.Type abilityAction = obj.getAnnotation(AbilityAction.class).value();
            actionHandlers.put(abilityAction, obj.getDeclaredConstructor().newInstance());
        }
    }
}
```

→ **第 4 次发现注解驱动 handler 注册**！Quest / Scene / Talk / Ability 四个系统都用同一种模式。

### 3.3 已实现的 ability action

```
ActionApplyModifier              施加 modifier
ActionAvatarSkillStart           技能开始
ActionChangeTag                  改 tag
ActionCreateGadget               创造机关 (元素战技产物)
ActionCreateGadgetForEquip       创造装备相关机关
ActionExecuteGadgetLua           执行 gadget 的 Lua
ActionGenerateElemBall           生成元素粒子 (回能量用)
ActionHealHP                     恢复 HP (治疗技能)
ActionKillSelf                   自杀
ActionLoseHP                     掉血
ActionPredicated                 条件判定
ActionServerLuaCall              调用服务器 Lua
ActionServerLuaTriggerEvent      触发服务器 Lua 事件
ActionSetGlobalValueToOverrideMap  改变量
ActionSetRandomOverrideMapValue  随机改变量
```

注意 `ActionServerLuaTriggerEvent` —— 这是**Combat 系统主动喊 Scene Script 系统**的桥（fire `EVENT_LUA_NOTIFY`）。和我们 notes/08 看的 `NOTIFY_GROUP_LUA` 是反方向。

---

## 4. EnergyManager：元素能量的权威算法

能量（爆发条）是**100% 服务器权威**。客户端仅报告"我打中怪了"，服务器算"该回多少能量"。

### 4.1 怪物死亡掉落

```java
// EnergyManager.handleAttackHit 等
DataLoader.loadList("EnergyDrop.json", EnergyDropEntry.class).forEach(entry -> {
    energyDropData.put(entry.getDropId(), entry.getDropList());
});
```

每个怪物有 `dropId`，表里写着"血量到 X% 时掉落 Y 个能量球"。**这是能量经济的唯一正源**——客户端无法 spawn 能量球。

### 4.2 元素粒子从技能（角色 E）生成

```java
private int getBallCountForAvatar(int avatarId) {
    int count = 2;  // default 2 particles
    if (!skillParticleGenerationData.containsKey(avatarId)) {
        Grasscutter.getLogger().warn("No particle generation data for avatarId {} found.", avatarId);
    } else {
        int roll = ThreadLocalRandom.current().nextInt(0, 100);
        int percentageStack = 0;
        for (SkillParticleGenerationInfo info : skillParticleGenerationData.get(avatarId)) {
            int chance = info.getChance();
            percentageStack += chance;
            if (roll < percentageStack) {
                count = info.getValue();
                break;
            }
        }
    }
    return count;
}
```

**每个角色 E 技能粒子数是按概率表抽的**。比如雷电将军 E 可能有：
- 50% 概率 4 个粒子
- 30% 概率 3 个粒子
- 20% 概率 5 个粒子

→ **粒子数随机的设计**鼓励"多次释放求高概率高粒子"，不是固定数。

### 4.3 元素粒子的 ballId

```java
private int getBallIdForElement(ElementType element) {
    if (element == null) return 2024;  // colorless
    return switch (element) {
        case Fire    -> 2017;
        case Water   -> 2018;
        case Grass   -> 2019;
        case Electric -> 2020;
        case Wind    -> 2021;
        case Ice     -> 2022;
        case Rock    -> 2023;
    };
}
```

→ 元素粒子也是 EntityItem，遵守 inventory 那套（notes/15 看过）。但加给玩家时直接转成"角色能量"而不进背包。

---

## 5. 元素反应是客户端权威的具体证据

```java
case ABILITY_META_TRIGGER_ELEMENT_REACTION -> handleTriggerElementReaction(invoke);
```

`ABILITY_META_TRIGGER_ELEMENT_REACTION` 是**客户端发**给服务器，告知"我触发了元素反应"。服务器接收即转发给同房间玩家——**不参与反应类型判定**。

这印证了 KQM TCL 的实测：**元素附着/反应类型是客户端算**。这也是为什么：
- 高 ping 不影响反应（客户端立刻算）
- 但 buff 触发（如胡桃血量 < 50% 加成）会在高 ping 失效（buff 在客户端，但 HP 在服务器）

---

## 6. 反作弊 hooks（混合权威下的保险）

混合权威架构里，服务器虽然不计算伤害，但需要拒绝**明显异常**的客户端报告：

### 6.1 元素爆发期间无敌

```java
// HandlerCombatInvocationsNotify.java:38-41
if (attackResult.getAttackerId() != player.getTeamManager().getCurrentAvatarEntity().getId() &&
    player.getAbilityManager().isAbilityInvulnerable()) break;
```

→ 玩家正在元素爆发动画期间，`abilityInvulnerable=true`。如果客户端报告"我被外部打了"，**服务器拒绝**。

`abilityInvulnerable` 设置点（`AbilityManager.java:217`）：
```java
public void onSkillStart(Player player, int skillId, int casterId) {
    var skillData = GameData.getAvatarSkillDataMap().get(skillId);
    if (skillData.getCostElemVal() <= 0) return;   // 不是元素爆发，跳过
    this.abilityInvulnerable = true;
}
```

→ 只有**costElemVal > 0**（消耗能量）的技能才设置无敌——也就是元素爆发，不是元素战技。

### 6.2 摔伤的 200ms 时间窗

```java
// FALL_ON_GROUND 必须在 LAND_SPEED 之后 200ms 内到达
int maxDelay = 200;
long actualDelay = System.currentTimeMillis() - cachedLandingTimeMillisecond;
if (actualDelay > maxDelay) {
    return;  // discard - 这是 FIGHT (蓄力下落) 之后又来的 FALL_ON_GROUND, 不算摔
}
```

→ 处理具体的客户端 bug 模式："蓄力下落攻击 → 跟 NPC 对话后再发 FALL_ON_GROUND" 这种顺序异常会被丢弃。**这是踩过坑后才有的代码**。

### 6.3 GodMode 跳过摔伤

```java
if (session.getPlayer().inGodmode()) return;
```

→ GM 模式 / 测试账号跳过摔伤检查。**保留了开发后门**。

---

## 7. 4 系统架构同构（第 4 次出现）

| 子系统 | 事件总线 | Handler 注册 | 异步池 |
|---|---|---|---|
| Quest | `queueEvent(QuestCond/Content)` | `@QuestValueCond/Content/Exec` 注解 | 4 线程 ✓ |
| Scene Script | `callEvent(EventType)` | `getSubTypesOf(...) + scriptlib_handlers/` | 4 线程 ✓ |
| Talk | `NpcTalkReq` 客户端处理 | -（前端权威） | -（窄通道）|
| **Ability** | `onAbilityInvoke(AbilityInvokeEntry)` | `@AbilityAction` 注解 | **4 线程 ✓** |

**`registerHandlers` 几乎是同样的代码**：

```java
// AbilityManager.java:69 (vs QuestSystem.java:41 几乎一样)
var handlerClasses = Grasscutter.reflector.getSubTypesOf(AbilityActionHandler.class);
for (var obj : handlerClasses) {
    if (obj.isAnnotationPresent(AbilityAction.class)) {
        AbilityModifierAction.Type abilityAction = obj.getAnnotation(AbilityAction.class).value();
        actionHandlers.put(abilityAction, obj.getDeclaredConstructor().newInstance());
    }
}
```

→ "**4 线程异步池 + 反射 handler 注册 + 倒排 dispatch**" 是这套架构的**通用骨架**。一个团队/架构师把它复刻到 4 个子系统，证明它确实经过实战验证。

---

## 8. 实例：开元素爆发的端到端流程

设想玩家 A（雷电将军）放元素爆发，打到怪物 M：

```
[客户端 A]
   ① 玩家按 Q 键
   ② 客户端发 EvtDoSkillSuccNotify(skillId=10024, casterId=...)
   ③ 客户端发 AbilityInvocationsNotify[
        ABILITY_META_MODIFIER_CHANGE        ← 添加雷神 buff
        ABILITY_META_ADD_NEW_ABILITY        ← 加入"五雷剑诀"形态
        ABILITY_META_OVERRIDE_PARAM         ← 改攻击参数
   ]
   ④ 玩家挥剑攻击 M
   ⑤ 客户端发 CombatInvocationsNotify[
        COMBAT_EVT_BEING_HIT(defender=M, damage=12345, element=Electric, isCrit=true)
   ]

[服务器]
   收到 ②  →  HandlerEvtDoSkillSuccNotify
              → AbilityManager.onSkillStart(skillId=10024)
              → skillData.costElemVal=80 > 0 → 设 abilityInvulnerable=true
              
   收到 ③  →  HandlerAbilityInvocationsNotify (一个一个 invoke 处理)
              → executeAction(ApplyModifier)  ← 加 buff
              → 转发给同房玩家 (combatInvokeHandler)
   
   收到 ⑤  →  HandlerCombatInvocationsNotify
              → 反作弊: attackerId == 当前角色 ✓ (自己打的，不拒)
              → player.attackResults.add(...)  接受
              → energyManager.handleAttackHit(...) (元素附着/反应统计)
              → 怪物 M 的 HP -= damage
                  M.setFightProperty(FIGHT_PROP_CUR_HP, newHP)
                  broadcastPacket(PacketEntityFightPropUpdateNotify) ← 广播
              → 如果 M.HP <= 0 → EntityMonster.onDeath()
                  fire EVENT_ANY_MONSTER_DIE → 通知 Scene Script 系统
                  EnergyManager 计算掉落 (按 EnergyDrop.json)
                  drop 元素粒子 → 广播 EntityAppear
   
[客户端 B (同队)]
   通过 forward 收到 A 的所有 combat invoke
   → 本地播 A 的元素爆发动画
   → 本地播 M 受击效果
   → 本地看到 M 血条扣减 (基于 server 广播的 FightPropUpdate)
```

关键观察：
- **伤害值由 A 的客户端决定**（包含暴击判定、元素反应倍率等）
- **服务器只校验"是否合法攻击"**（无敌时跳过），不重算
- **HP 数值由服务器存**（全房间共享同一份）
- **能量回复 + 怪物掉落由服务器算**
- **B 看到的画面靠 A 的 invoke 转发 + server 的 HP 更新合成**

---

## 9. 几个有意思的 Handler 实现细节

### 9.1 Action·HealHP / LoseHP

`ActionHealHP` 和 `ActionLoseHP` 是技能里"治疗" / "扣血"的具体行为。技能配表里说"扣 HP 20%"，对应一条 ActionLoseHP 调用。

→ **HP 改动有数十种触发源**（普攻、技能、buff、元素反应、debuff、自杀、摔伤、毒、燃烧），每个都有自己的代码路径。这就是"为什么角色 HP 同步逻辑那么复杂"的来源。

### 9.2 Action·CreateGadget for elemental skill

```
雷神 E 钵笠护卫机关  → ActionCreateGadget 创建一个 EntityGadget
温迪 E 风域           → ActionCreateGadget 创建一个 EntityRegion (区域伤害)
枫原万叶 E 千早振     → ActionCreateGadget 创建附带状态的实体
```

→ "技能产物（机关/区域）"用统一的 EntityGadget 抽象。命中检测和持续效果由 gadget 自己处理。

### 9.3 Modifier 是什么

Modifier = **buff/debuff 实例**。每个 modifier 有：
- `instancedModifierId` (运行时唯一)
- `duration` (持续时间)
- `stackCount` (堆叠数)
- 关联到某个 ability 的具体 action

举例：
- 雷电将军 E 给队友的能量回复 buff
- 钟离 Q 的护盾
- 风套圣遗物的减抗
- 流浪者命座的属性提升

服务器只**记录** modifier 的存在和过期，**不算** modifier 对伤害的影响（那是客户端算）。

---

## 10. 数据规模感

* 角色技能 (`AvatarSkillData`)：~700+ 个技能
* 怪物 (`MonsterData`)：~700+ 怪物（含变种）
* 圣遗物词条 (`ReliquaryAffixData`)：~200+ 词条池
* AbilityModifier 类型：30+ action types
* Combat invoke 类型：30+ argumentType

总和：**Combat 系统比任务系统更大**。但因为大部分逻辑在客户端，Grasscutter 的服务器侧代码量反而比 Quest 少（因为不算伤害）。

---

## 11. 给做大型联机 RPG 战斗系统开发者的提炼

1. **战斗算法不要全放服务器**——延迟反馈会毁掉手感
2. **关键资源（HP/能量/体力/掉落）必须服务器权威**——否则外挂能修改本地直接乱改账号
3. **反作弊靠"关键时刻 sanity check"**——不要尝试重算所有伤害（开销巨大），只检查"无敌时被打"等异常 invariant
4. **客户端报告 + 服务器广播**的模式：A 报告事件 → 服务器转发给 B → B 看到 A 的动作。**A 是事件源，server 是 broker**
5. **摔伤这种连续物理量适合服务器算**——速度阈值是离散判断，客户端不太可能比服务器更准
6. **能量经济必须服务器权威**——否则商业模型破产（能量影响爆发频率，影响伤害产出，影响整个游戏循环）
7. **架构同构很重要**——4 个子系统都用 4 线程异步池 + 注解 handler，团队学习成本骤降
8. **预留 GodMode 后门**——开发期跳过限制，但要严格限制访问

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerCombatInvocationsNotify.java` (160+ 行)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerAbilityInvocationsNotify.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/ability/AbilityManager.java` (530 行)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/ability/actions/Action*.java` (15+ 个 action handlers)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/managers/energy/EnergyManager.java` (能量算法)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/entity/EntityMonster.java` (怪物 HP / 死亡 / 掉落)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/entity/EntityAvatar.java` (玩家角色实体)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/props/FightProperty.java` (战斗属性枚举)
