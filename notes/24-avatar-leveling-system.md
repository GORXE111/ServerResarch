# 24 · Avatar 升级 / Talent / Constellation 系统 · 7 层属性叠加

角色培养是 RPG 数值核心。本笔记拆解 `Avatar` 类的 953 行代码 + `InventorySystem` 升级方法 200+ 行，重点是 **`recalcStats()` 的 7 层属性叠加算法**——理解角色面板是怎么算出来的。

> 核心代码：`game/avatar/Avatar.java`（953 行）+ `AvatarStorage.java`（185 行）+ `InventorySystem.java` 升级方法

---

## 1. 整体架构

```
AvatarStorage (per Player)
  └── Map<avatarId, Avatar>    玩家所有角色
  
Avatar (single character, persisted)
  ├── 身份: avatarId / guid
  ├── 等级: level (1-90) / exp / promoteLevel (0-6)
  ├── 技能: skillLevelMap / proudSkillList / talentIdList (命座)
  ├── 装备: weaponGuid / artifactGuids[5]
  ├── 元素: skillDepotId / elementType
  ├── 数值: fightProperties: Map<FightPropId, value>   ★ 计算结果
  ├── 好感: fetterLevel (0-10) / fetterExp / fetters
  └── 外观: costumeId / flycloakId / nameCardId

升级方法 (InventorySystem):
  ├── upgradeAvatar(itemId, count)         用经验书 (101 / 104003 / 104012 / 104013)
  ├── upgradeAvatar(avatar, expGain)       直接给经验 (虚拟物品 101)
  ├── promoteAvatar(guid)                  突破 (level == maxLevel 时)
  ├── upgradeAvatarFetterLevel(avatar)     好感度升级
  ├── upgradeAvatarSkill(guid, skillId)    升级单个技能
  └── unlockAvatarConstellation(guid)      解锁命座
```

---

## 2. 等级 / 突破 / 技能 / 命座 四维成长

### 2.1 等级系统

```
level: 1..90
promoteLevel: 0..6 (突破等级)
等级上限按 promoteLevel 解锁:
  promoteLevel=0  →  level 上限 20
  promoteLevel=1  →  level 上限 40
  promoteLevel=2  →  level 上限 50
  promoteLevel=3  →  level 上限 60
  promoteLevel=4  →  level 上限 70
  promoteLevel=5  →  level 上限 80
  promoteLevel=6  →  level 上限 90
```

→ **必须先突破才能继续升级**——形成"升级 → 突破 → 升级"的循环节奏。每次突破需要：
- 角色对应的元素材料（如雷神之瞳）
- BOSS 掉落材料（如雷电将军周本掉落）
- 当地特产（如绯樱绣球）
- 摩拉（按等级递增）

### 2.2 promoteAvatar 流程

```java
// InventorySystem.promoteAvatar (line 496)
public void promoteAvatar(Player player, long guid) {
    Avatar avatar = player.getAvatars().getAvatarByGuid(guid);
    int nextPromoteLevel = avatar.getPromoteLevel() + 1;
    AvatarPromoteData currentPromoteData = GameData.getAvatarPromoteData(promoteId, currentLevel);
    AvatarPromoteData nextPromoteData = GameData.getAvatarPromoteData(promoteId, nextLevel);
    
    // ★ 反作弊：必须达到当前突破等级的上限
    if (avatar.getLevel() != currentPromoteData.getUnlockMaxLevel()) return;
    
    // 扣材料 + 摩拉
    ItemParamData[] costs = nextPromoteData.getCostItems();
    if (nextPromoteData.getCoinCost() > 0) {
        costs = Arrays.copyOf(costs, costs.length + 1);
        costs[costs.length-1] = new ItemParamData(202, nextPromoteData.getCoinCost());  // 摩拉
    }
    if (!player.getInventory().payItems(costs)) return;
    
    // 突破升级
    avatar.setPromoteLevel(nextPromoteLevel);
    
    // ★ 解锁固有技能 (如雷神二段攻击 / 千手百眼旗)
    Optional.ofNullable(GameData.getAvatarSkillDepotDataMap().get(skillDepotId))
        .map(AvatarSkillDepotData::getInherentProudSkillOpens)
        .ifPresent(d -> d.stream()
            .filter(openData -> openData.getNeedAvatarPromoteLevel() == newLevel)
            .mapToInt(openData -> openData.getProudSkillGroupId() * 100 + 1)
            .forEach(proudSkillId -> avatar.getProudSkillList().add(proudSkillId)));
    
    avatar.recalcStats(true);  // ★ 重算属性
    avatar.save();
}
```

→ **突破解锁 inherent proud skill**——在特定 promoteLevel 自动解锁固有技能（如雷神 1 突破解锁额外攻击）。

### 2.3 经验书系统（upgradeAvatar）

```java
public void upgradeAvatar(Player player, long guid, int itemId, int count) {
    // 经验书 itemId: 104001 (流浪者) / 104002 (冒险家) / 104003 (大英雄)
    // 经验值不同
    var actions = data.getItemUseActions();
    for (var action : actions) {
        if (action.getItemUseOp() == ItemUseOp.ITEM_USE_ADD_EXP) {
            expGain += ((ItemUseAddExp) action).getExp() * count;
        }
    }
    
    // ★ 经验消耗摩拉: 1 exp = 0.2 mora
    int moraCost = expGain / 5;
    ItemParamData[] costItems = {new ItemParamData(itemId, count), new ItemParamData(202, moraCost)};
    if (!player.getInventory().payItems(costItems)) return;
    
    upgradeAvatar(player, avatar, promoteData, expGain);
}
```

**经验书三档**：
- 流浪者的经验（小）：1000 exp
- 冒险家的经验（中）：5000 exp
- 大英雄的经验（大）：20000 exp

经验比例：**1 exp = 0.2 摩拉**——这是双重消耗设计，保证摩拉永远稀缺。

### 2.4 经验加级循环

```java
public void upgradeAvatar(Player player, Avatar avatar, AvatarPromoteData promoteData, int expGain) {
    int maxLevel = promoteData.getUnlockMaxLevel();   // 当前突破等级上限
    int level = avatar.getLevel();
    int exp = avatar.getExp();
    int reqExp = GameData.getAvatarLevelExpRequired(level);
    
    // 循环加经验直到 expGain 用完或满级
    while (expGain > 0 && reqExp > 0 && level < maxLevel) {
        int toGain = Math.min(expGain, reqExp - exp);   // 不超过当前等级所需
        exp += toGain;
        expGain -= toGain;
        
        if (exp >= reqExp) {
            // 升级！
            exp = 0;
            level += 1;
            reqExp = GameData.getAvatarLevelExpRequired(level);
        }
    }
    
    avatar.setLevel(level);
    avatar.setExp(exp);
    avatar.recalcStats();   // ★ 每次升级都重算属性
    avatar.save();
}
```

→ **每升一级查 `getAvatarLevelExpRequired(level)` 取下一级所需 exp**。配表里有完整的经验曲线。

---

## 3. recalcStats() 的 7 层属性叠加（核心）

每次属性变化（升级/突破/换装备/换武器）都调 `recalcStats()`：

```java
public void recalcStats(boolean forceSendAbilityChange) {
    var data = this.getAvatarData();
    var promoteData = GameData.getAvatarPromoteData(promoteId, this.getPromoteLevel());
    
    // 保留 HP 百分比（升级后 HP 上限变 → 按比例恢复）
    float hpPercent = curHp / maxHp;
    float currentEnergy = ... ;
    
    // 清空所有属性
    this.getFightProperties().clear();
    
    // ─── 第 1 层：基础属性（按等级曲线）───
    this.setFightProperty(BASE_HP,      data.getBaseHp(level));
    this.setFightProperty(BASE_ATTACK,  data.getBaseAttack(level));
    this.setFightProperty(BASE_DEFENSE, data.getBaseDefense(level));
    this.setFightProperty(CRITICAL,        data.getBaseCritical());     // 5% 默认暴击
    this.setFightProperty(CRITICAL_HURT,   data.getBaseCriticalHurt()); // 50% 默认暴伤
    this.setFightProperty(CHARGE_EFFICIENCY, 1f);                       // 100% 充能
    
    // ─── 第 2 层：突破加成 ───
    if (promoteData != null) {
        for (FightPropData prop : promoteData.getAddProps()) {
            this.addFightProperty(prop.getProp(), prop.getValue());
        }
    }
    
    // ─── 第 3 层：圣遗物主词条 ───
    for (int slotId = 1; slotId <= 5; slotId++) {
        GameItem equip = this.getEquipBySlot(slotId);
        if (equip == null) continue;
        
        ReliquaryMainPropData mainPropData = GameData.getReliquaryMainPropDataMap().get(equip.getMainPropId());
        ReliquaryLevelData levelData = GameData.getRelicLevelData(rank, equip.getLevel());
        this.addFightProperty(mainPropData.getFightProp(), 
            levelData.getPropValue(mainPropData.getFightProp()));
    }
    
    // ─── 第 4 层：圣遗物副词条 ───
    for (int slotId = 1; slotId <= 5; slotId++) {
        for (int appendPropId : equip.getAppendPropIdList()) {
            ReliquaryAffixData affixData = GameData.getReliquaryAffixDataMap().get(appendPropId);
            this.addFightProperty(affixData.getFightProp(), affixData.getPropValue());
        }
    }
    
    // ─── 第 5 层：圣遗物套装效果（2 件套 / 4 件套）───
    setMap.forEach((setId, amount) -> {
        ReliquarySetData setData = ...;
        for (int setIndex = 0; setIndex < setNeedNum.length; setIndex++) {
            if (amount < setNeedNum[setIndex]) break;
            int affixId = setData.getEquipAffixId() * 10 + setIndex;
            EquipAffixData affix = GameData.getEquipAffixDataMap().get(affixId);
            for (FightPropData prop : affix.getAddProps()) {
                this.addFightProperty(prop.getProp(), prop.getValue());
            }
            this.addToExtraAbilityEmbryos(affix.getOpenConfig(), true);  // 套装技能
        }
    });
    
    // ─── 第 6 层：武器属性（曲线 + 突破）───
    GameItem weapon = this.getWeapon();
    if (weapon != null) {
        WeaponCurveData curveData = GameData.getWeaponCurveDataMap().get(weapon.getLevel());
        for (WeaponProperty wp : weapon.getItemData().getWeaponProperties()) {
            this.addFightProperty(wp.getPropType(), 
                wp.getInitValue() * curveData.getMultByProp(wp.getType()));
        }
        // 武器突破属性
        WeaponPromoteData wepPromoteData = GameData.getWeaponPromoteData(...);
        for (FightPropData prop : wepPromoteData.getAddProps()) {
            this.addFightProperty(prop.getProp(), prop.getValue());
        }
    }
    
    // ─── 第 7 层：武器精炼词条（被动技能）───
    for (int af : weapon.getAffixes()) {
        int affixId = af * 10 + weapon.getRefinement();
        EquipAffixData affix = GameData.getEquipAffixDataMap().get(affixId);
        // 精炼属性 + 精炼技能 (如祭礼剑的"重置技能 CD")
        ...
    }
    
    // 计算 HP 上限 + 当前 HP（按 hpPercent 恢复）
    setCurrentEnergy(currentEnergy);
    avatar.setCurHp(maxHp * hpPercent);
}
```

### 7 层叠加总结

```
1. 基础属性（按 level 查曲线）
   ↓ +
2. 突破属性（promoteData.addProps）
   ↓ +
3. 圣遗物主词条（5 件 × 等级×品质）
   ↓ +
4. 圣遗物副词条（5 件 × N 个 affix）
   ↓ +
5. 圣遗物套装效果（2/4 件套）
   ↓ +
6. 武器属性（基础值 × 等级曲线 + 突破属性）
   ↓ +
7. 武器精炼词条（精炼等级 1-5）
   ↓
最终面板属性
```

→ **每次任何变动（升级/换圣遗物/换武器/突破/精炼）都触发整套重算**。这是为什么 `recalcStats()` 频繁被调用。

---

## 4. AvatarPromoteData 配表结构

```jsonc
// 雷电将军 1 突破 (avatarPromoteId=1052, promoteLevel=1)
{
    "avatarPromoteId": 1052,
    "promoteLevel": 1,
    "unlockMaxLevel": 40,            // 突破后等级上限
    "coinCost": 20000,               // 摩拉消耗
    "costItems": [                   // 材料列表
        { "id": 113023, "count": 3 },   // 雷霆之嗣（雷神之瞳）
        { "id": 104326, "count": 3 },   // 绯樱绣球
        { "id": 113001, "count": 3 }    // 教导（武装）等
    ],
    "addProps": [                    // 突破后属性加成
        { "propType": "FIGHT_PROP_BASE_ATTACK", "value": 18.7 },
        { "propType": "FIGHT_PROP_ELEC_ADD_HURT", "value": 0.0 }
    ]
}
```

→ **每个角色 / 每个突破等级独立配表**——给运营/策划灵活度。

---

## 5. 技能升级（Talent）

### 5.1 三种技能槽

```
普通攻击 (Normal Attack)     → talent slot 0
元素战技 (E)                  → talent slot 1
元素爆发 (Q)                  → talent slot 2
```

每个技能有独立等级（1-15），需要"天赋书 + BOSS 掉落"升级。

### 5.2 ProudSkill 系统

每个技能背后是一个 `ProudSkillGroupId`：

```java
// proudSkillId = proudSkillGroupId * 100 + level
proudSkillId 525_03 = ProudSkill 525, level 3
```

升级流程：
```java
// avatar.upgradeSkill (line 674 - deprecated, but real impl 在 Avatar.java)
public void upgradeSkill(int skillId) {
    int currentLevel = skillLevelMap.get(skillId);
    int nextLevel = currentLevel + 1;
    
    // 扣材料 (3 阶天赋书 + 周本材料 + 摩拉)
    ProudSkillData proudSkillData = GameData.getProudSkillDataMap().get(
        proudSkillGroupId * 100 + nextLevel);
    
    if (!player.getInventory().payItems(proudSkillData.getCostItems())) return;
    if (currentLevel + 1 > 10) {  // 10 级以上需要"皇冠"
        // 额外消耗皇冠
    }
    
    skillLevelMap.put(skillId, nextLevel);
    avatar.recalcStats();
}
```

### 5.3 命之座加成

```java
// 命座解锁后修改技能等级 cap
// C3 命座 = E 技能等级 +3 (上限从 10 升到 13)
// C5 命座 = Q 技能等级 +3
// 命座效果配在 talentIdList 里
```

`Avatar.recalcStats()` 末尾会检查命座数量并应用对应 buff。

---

## 6. Constellation 命之座系统

### 6.1 解锁机制

每抽到重复角色（命座物品）：
```java
// notes/21 Gacha 看过：
constItemId = avatarId + 100   // 命座物品 itemId
```

玩家手动消耗命座物品：
```java
public void unlockConstellation() {
    // 检查是否有未解锁命座 (C0 → C1, C1 → C2, ... C5 → C6)
    int currentTalentLevel = talentIdList.size();
    if (currentTalentLevel >= 6) return;  // C6 满命
    
    // 扣命座物品
    if (!inventory.payItem(constItemId, 1)) return;
    
    // 解锁下一命座
    int nextTalentId = avatarId * 10 + currentTalentLevel + 1;
    talentIdList.add(nextTalentId);
    avatar.recalcStats();
}
```

### 6.2 6 个命座的设计模式

```
C1: 数值小幅 buff (如 +25% 暴击伤害)
C2: 中等 buff / 新机制 (如 攻击 +20%)
C3: 技能等级 +3 (E 或 Q, 角色定的)
C4: 数值大幅 buff
C5: 技能等级 +3 (剩下那个)
C6: 大变身 (重塑技能机制)
```

→ **C3/C5 是"技能等级 +3"**，其他是属性/被动 buff。**C6 经常重塑角色玩法**（如雷神 C6 加大招攻击数）。

---

## 7. Fetter Level（好感度等级）

```java
public void upgradeAvatarFetterLevel(Player player, Avatar avatar, int expGain) {
    int maxLevel = 10;
    int level = avatar.getFetterLevel();
    int exp = avatar.getFetterExp();
    int reqExp = GameData.getAvatarFetterLevelExpRequired(level);
    
    while (expGain > 0 && reqExp > 0 && level < maxLevel) {
        int toGain = Math.min(expGain, reqExp - exp);
        exp += toGain;
        expGain -= toGain;
        if (exp >= reqExp) {
            exp = 0;
            level += 1;
            reqExp = GameData.getAvatarFetterLevelExpRequired(level);
        }
    }
    
    avatar.setFetterLevel(level);
    avatar.setFetterExp(exp);
}
```

**好感度来源**：
- 队伍中战斗（被动获得，notes/15 看过 itemId 105 直接给）
- 完成角色传说任务
- 完成角色邀约事件
- **联机时翻倍**（notes/15 提过）

**好感度奖励**：
- Lv4：解锁角色资料
- Lv6：解锁角色故事
- Lv10：解锁角色名片 + 故事章节

---

## 8. AvatarStorage 持久化

```java
// AvatarStorage.java
@Entity
public class AvatarStorage extends BasePlayerDataManager {
    private final Map<Long, Avatar> avatars;       // guid → Avatar
    private final Map<Integer, Avatar> avatarsId;  // avatarId → Avatar
    
    public Avatar getAvatarByGuid(long guid) { return avatars.get(guid); }
    public Avatar getAvatarById(int id) { return avatarsId.get(id); }
    
    public boolean addAvatar(Avatar avatar) {
        if (avatars.containsKey(avatar.getGuid())) return false;
        avatars.put(avatar.getGuid(), avatar);
        avatarsId.put(avatar.getAvatarId(), avatar);
        avatar.save();
        return true;
    }
    
    // 服务器启动时反序列化所有 Avatar
    public void loadFromDatabase() {
        DatabaseHelper.getAllAvatars(getPlayer().getUid())
            .forEach(this::addAvatar);
    }
}
```

→ **每个 Avatar 是独立的 MongoDB document**——便于按 uid 查询所有角色。`guid` 是运行时唯一 id，`avatarId` 是配表 id。

---

## 9. 完整流程示例：玩家把雷电将军从 Lv1 练到 Lv90

```
[玩家抽到雷电将军 (avatarId=10000052)]
   ↓
gachaItem.setGachaItemNew(true)
inventory.addItem(avatarCard) → AvatarStorage.addAvatar(雷神)
雷神 Avatar 实例化:
   level=1, promoteLevel=0, exp=0
   skillLevelMap={normalAttack: 1, E: 1, Q: 1}
   talentIdList=[] (C0)
   recalcStats() → 计算 Lv1 面板属性

[喂经验书]
HandlerAvatarUpgradeReq → InventorySystem.upgradeAvatar(itemId=104003, count=10)
   ↓
expGain = 20000 × 10 = 200000
moraCost = 200000 / 5 = 40000 摩拉
inventory.payItems([经验书 ×10, 摩拉 ×40000])
   ↓
循环加经验 (level 1 → level 20, exp 用完后停)
   每升一级 reqExp 不同
   达到 maxLevel=20 (promoteLevel=0 上限) 后停止

[突破第 1 次]
HandlerAvatarPromoteReq → InventorySystem.promoteAvatar()
   检查 level == 20 ✓
   扣材料 (雷霆之嗣 ×1 + 教导 ×3 + 绯樱绣球 ×3 + 摩拉 ×20000)
   promoteLevel = 1, maxLevel 解锁到 40
   recalcStats() → 加突破属性
   解锁 inherent proud skill (如有)

[继续重复 升级 → 突破]
   level 1→20, 突破 0→1
   level 20→40, 突破 1→2
   level 40→50, 突破 2→3
   level 50→60, 突破 3→4
   level 60→70, 突破 4→5
   level 70→80, 突破 5→6
   level 80→90 (满级)

[升级技能]
HandlerAvatarSkillUpgradeReq → upgradeSkill(skillId=10024)
   扣材料 (天赋书 + 雷神周本掉落 + 摩拉)
   skillLevelMap[10024] += 1
   recalcStats()

[抽到第 2 个雷神 → 解锁 C1]
inventory.addItem(雷神命座物品 itemId=10000052+100)
玩家手动点 "解锁命之座"
unlockAvatarConstellation():
   talentIdList.add(C1)
   recalcStats()  ← C1 buff 生效

[联机战斗 → 好感度涨]
notes/15: itemId 105 (companionship exp) 进背包
addVirtualItem(105, count, 联机时×2)
upgradeAvatarFetterLevel() → fetterLevel + 1
解锁 Lv4 资料 / Lv6 故事 / Lv10 名片
```

---

## 10. 关键设计经验

### 10.1 "升级 → 突破" 双层节奏

不让玩家"无脑刷经验"——每 10/20 级有一个突破门槛，需要：
- 不同副本（角色周本 vs 元素本）
- 当地特产（限制玩家走访世界）
- 摩拉（全游戏通用消耗）

→ **强制玩家做多种内容**才能毕业一个角色，提升留存。

### 10.2 7 层属性叠加 = 数值复杂度的极致

```
基础 + 突破 + 圣遗物主词条 + 圣遗物副词条 + 套装 + 武器 + 武器精炼
```

→ **数值 build 空间巨大**——同一个角色有无数玩法。这是为什么"理论组"（KQM 等）能写出大量攻略。

### 10.3 一次重算所有属性（不增量）

```java
this.getFightProperties().clear();   // 清空
// 重新累加 7 层
```

→ **每次小变动整体重算**。代码简单，但每次换圣遗物都跑完整算法。**因为属性少（~30 个），全量重算 < 1ms**。

### 10.4 配表驱动到极致

每个角色每个突破等级一行配表：
- `avatarPromoteId × promoteLevel`
- `avatarSkillId × level`（每技能每级独立）
- `proudSkillGroupId × level`（被动天赋）
- `equipAffixId × level`（圣遗物套装效果）
- `weaponPromoteId × promoteLevel`（武器突破）

→ **几乎所有数值都来自配表查找**。代码只是"按 id 取值并叠加"。

### 10.5 命座系统的"零成本付费转化"

抽到重复角色 → 命座物品。**对玩家来说"重复角色不浪费"**——降低抽卡焦虑。**对游戏来说每个命座都是"再抽一次"的动机**——商业转化。

---

## 11. 反作弊点

```java
1. 突破前必须 level == maxLevel (反作弊跳级)
2. 升级前 expGain > 0 (防止恶意 0 经验请求)
3. 经验书 itemId 必须有 ITEM_USE_ADD_EXP action (防止用错物品当经验)
4. 摩拉/材料必须够 (payItems 有原子性)
5. 命座物品 itemId 校验 (avatarId+100 严格)
6. 技能升级有等级 cap (不能超过 10/13)
7. recalcStats 总在服务器算 (客户端只显示)
```

→ **数值是绝对服务器权威**。客户端面板显示只是"取最新一次同步"。

---

## 12. 给做 RPG 角色培养系统开发者的提炼

1. **升级 + 突破双层节奏**——避免无脑刷
2. **属性按曲线表查**（不要硬编码每级数值）
3. **每次变动整体重算属性**——代码简单，性能够用
4. **配表驱动一切**——每个角色 × 每个等级独立配置
5. **多源材料**（副本 + 特产 + 摩拉）——强制内容多样
6. **命座/精炼用"重复获取"**转化——降低抽卡焦虑
7. **被动技能（proud skill）和主动技能分开**——架构更清晰
8. **服务器绝对权威**——客户端只是显示器
9. **好感度独立周期**——区别于战力成长
10. **持久化按"实体粒度"**（每个 Avatar 一个 doc）——便于查询

---

## 13. 数据规模感

* 角色数：~80 个（含限定）
* 每角色 promote 等级：6
* 每角色技能数：5 个（普攻/E/Q + 2 个被动）
* 每技能等级：1-15（含命座加成）
* 圣遗物部位：5
* 圣遗物副词条池：~200 个
* 武器精炼等级：1-5
* 好感度上限：10

代码规模：
- `Avatar.java`：953 行（核心逻辑 + recalcStats）
- `AvatarStorage.java`：185 行（容器）
- `InventorySystem.java` 升级方法：~200 行
- 各种 PacketHandler：~20 个文件
- 总核心：~1500 行 + 配表查询逻辑

---

## 14. 与之前系统的连接

| 之前笔记 | 连接点 |
|---|---|
| notes/15 经济 | itemId 101=角色经验, 105=好感度, 104003-13 经验书 |
| notes/16 Combat | recalcStats 输出 FightProperty 直接被 Combat 用 |
| notes/19 Dungeon | TrialAvatar 临时角色机制 |
| notes/21 Gacha | gachaItem.setGachaItemNew + avatarId+100 命座物品 |
| notes/18 MP | 联机时好感度翻倍 |

→ Avatar 系统是**经济、战斗、副本、抽卡、联机的交汇点**——和 Dungeon（notes/19）一样是多系统粘合剂。

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/avatar/Avatar.java`（953 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/avatar/AvatarStorage.java`（185 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/systems/InventorySystem.java`（升级方法 line 496-690）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/data/excels/AvatarPromoteData.java`（突破配表）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/data/excels/AvatarSkillDepotData.java`（技能配表）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/data/excels/ProudSkillData.java`（天赋数值）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerAvatarUpgradeReq.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerAvatarPromoteReq.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerAvatarSkillUpgradeReq.java`
