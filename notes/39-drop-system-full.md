# Drop 掉落系统深度剖析

> 第 39 篇：怪物 `onDeath` 到 `Inventory.addItem` 之间这条**随机抽奖管道** —— 我们说了 10+ 次"打死怪掉东西"但从未真正打开它。

---

## 0. 为什么这一篇重要

前面笔记里 Drop 这个概念被反复提到但浅尝辄止：
- notes/32 怪物：`EntityMonster.onDeath` 触发 7 件事中包括"掉落"
- notes/15 经济系统：怪物掉落是经济输入之一
- notes/19 副本系统：副本 boss 掉武器原型
- notes/33 Gadget：宝箱 + 矿物 → 生成 EntityItem 落地
- notes/35 Scene：`Scene.killEntity` 触发 `dropSystem.callDrop`
- notes/38 Inventory：`addItem(item, ActionReason.SubfieldDrop)` 是物品入袋点

但**到底怎么随机？权重怎么定？share 是什么？落地动画哪来的？**这一篇专攻。

---

## 1. 两套 Drop 系统并存

Grasscutter 有**两套独立的掉落系统**：

```
[System A] DropSystem (game/drop/)         ← 怪物专用
   - 配表: Drop.json (per monsterId)
   - 入口: Scene.killEntity → callDrop(monster)
   - 用途: 普通怪物 / boss / 精英怪掉落

[System B] dropSubfield (GameEntity)       ← 通用
   - 配表: DropTableExcelConfigData (Excel)
   - 入口: 主动调用 entity.dropSubfield("name")
   - 用途: 采集物 / 宝箱 / 自然产物 (花/草/矿)
```

**为什么两套**：
- **A** 是 grasscutter 早期为怪物掉落写的，配表很简单（itemId+权重+数量）
- **B** 是用 mihoyo **正版 DropTable Excel** 配表，但覆盖少

→ 加新内容时**优先 A**（配表 Drop.json 简单）；从 mihoyo 数据继承时用 **B**。

---

## 2. DropSystem 核心架构

`DropSystem.java`（112 行 + 55+16 行的 Data/Info） —— 极简但精炼。

### 2.1 数据结构

```java
public class DropSystem extends BaseGameSystem {
    private final Int2ObjectMap<List<DropData>> dropData;   // monsterId → List<掉落条目>
}
```

→ **一个 map** —— 按 monsterId 索引。每只怪可能有 N 条掉落配置。

### 2.2 加载（启动时）

```java
public synchronized void load() {
    getDropData().clear();
    List<DropInfo> banners = DataLoader.loadList("Drop.json", DropInfo.class);
    
    for (DropInfo di : banners) {
        getDropData().put(di.getMonsterId(), di.getDropDataList());
    }
}
```

`Drop.json` 长这样：
```json
[
  {
    "monsterId": 21010101,    // 丘丘人
    "dropDataList": [
      { "itemId": 100086, "minWeight": 1, "maxWeight": 5000, "minCount": 1, "maxCount": 3, "share": true },
      { "itemId": 113001, "minWeight": 5001, "maxWeight": 7000, "minCount": 1, "maxCount": 1, "share": false }
    ]
  },
  {
    "monsterId": 22010401,    // 深渊法师
    "dropDataList": [
      { "itemId": 112002, "minWeight": 1, "maxWeight": 10000, "minCount": 2, "maxCount": 5, "share": false, "give": true }
    ]
  }
]
```

→ 这是**一对多**关系：1 怪 → N 个 DropData 条目。

### 2.3 DropData 字段（7 个）

```java
public class DropData {
    private int minWeight;   // 权重区间下限
    private int maxWeight;   // 权重区间上限
    private int itemId;      // 物品 ID
    private int minCount;    // 数量下限
    private int maxCount;    // 数量上限
    private boolean share;   // 共享掉落 (联机所有人看到)
    private boolean give;    // 直接进背包 (不落地)
}
```

**每条 DropData 是独立的概率事件**——可叠加。

---

## 3. 权重随机算法

`DropSystem.processDrop()`：
```java
private void processDrop(DropData dd, EntityMonster em, Player gp) {
    int target = Utils.randomRange(1, 10000);   // ★ 掷 1d10000
    
    if (target >= dd.getMinWeight() && target < dd.getMaxWeight()) {
        // ★ 命中! 掉落
        ItemData itemData = GameData.getItemDataMap().get(dd.getItemId());
        int num = Utils.randomRange(dd.getMinCount(), dd.getMaxCount());
        
        if (itemData == null) return;
        
        if (itemData.isEquip()) {
            // 装备类: 每件一个独立实体
            for (int i = 0; i < num; i++) {
                float range = (2.5f + (.05f * num));
                Position pos = em.getPosition().nearby2d(range).addY(3f);
                addDropEntity(dd, em.getScene(), itemData, pos, num, gp);
            }
        } else {
            // 材料: 一个实体多个数量
            Position pos = em.getPosition().clone().addY(3f);
            addDropEntity(dd, em.getScene(), itemData, pos, num, gp);
        }
    }
}
```

### 3.1 概率计算

每条 DropData 独立掷骰**1-10000**：
- minWeight=1, maxWeight=5000 → 50% 命中
- minWeight=5001, maxWeight=6000 → 10% 命中
- minWeight=9000, maxWeight=10000 → 10% 命中
- minWeight=1, maxWeight=10000 → 100% 命中

```
[范围] 1 ──────|━━━━━━━|─────|━━|──────|━━|── 10000
        ↑       ↑       ↑     ↑          ↑
        条目1   条目2   未配  条目3      条目4
        50%     10%           5%        10%
        命中    命中           命中      命中
```

**关键**：每个条目**独立掷骰**——可以全部命中也可以全部 miss。

### 3.2 数量随机

```java
int num = Utils.randomRange(dd.getMinCount(), dd.getMaxCount());
```

随机一个数量 (含两端)：
- minCount=2, maxCount=5 → 随机 2/3/4/5 之一

### 3.3 期望计算（玩家视角）

某条目 itemId=104010, minWeight=1, maxWeight=3000, minCount=1, maxCount=3：
- 命中概率 = 30%
- 命中时数量期望 = (1+2+3+3)/4 = 2.25 (注意 randomRange 含两端，所以 4 个值)
- **每只怪的期望掉落** = 0.30 × 2.25 = **0.675 个**

→ 配表设计者用这套数学**控制掉落经济**。

---

## 4. share vs give：4 种组合

DropData 有两个 boolean，组合出 4 种行为：

| share | give | 行为 | 用例 |
|---|---|---|---|
| false | false | **个人 + 落地** | 普通怪物掉装备（每个玩家看到自己的）|
| true | false | **共享 + 落地** | 普通怪物掉材料（公共）|
| false | true | **个人 + 直接进背包** | 任务怪掉道具（确定的）|
| true | true | **公共 + 全员入背包** | 联机 boss 给所有玩家|

### 4.1 share=false 的细微差别

```java
public void callDrop(EntityMonster em) {
    if (getDropData().containsKey(id)) {
        for (DropData dd : getDropData().get(id)) {
            if (dd.isShare())
                processDrop(dd, em, null);          // ★ 一次 (全员)
            else {
                for (Player gp : em.getScene().getPlayers()) {
                    processDrop(dd, em, gp);        // ★ 每个玩家独立掷骰
                }
            }
        }
    }
}
```

**重要**：
- share=true 一次掷骰，所有玩家看到同样掉落
- share=false **每个玩家独立掷骰** —— 4 个玩家 = 4 次随机！

→ **联机玩家个人运气独立**——这是"为什么我没出武器但队友出了"的根本原因。

### 4.2 give=true 直接进背包

```java
private void addDropEntity(DropData dd, Scene dropScene, ItemData itemData, Position pos, int num, Player target) {
    if (!dd.isGive() && (itemData.getItemType() != ItemType.ITEM_VIRTUAL || itemData.getGadgetId() != 0)) {
        // ★ 路径 A: 生成 EntityItem 落地
        val createConfig = new CreateGadgetEntityConfig(itemData, num)
            .setBornPos(pos)
            .setPlayerOwner(target)
            .setShareItem(dd.isShare());
        EntityItem entity = new EntityItem(dropScene, createConfig);
        
        if (!dd.isShare())
            dropScene.addEntityToSingleClient(target, entity);   // 只给一人显示
        else
            dropScene.addEntity(entity);                          // 全员显示
    } else {
        // ★ 路径 B: 跳过落地, 直接 inventory
        if (target != null) {
            target.getInventory().addItem(new GameItem(itemData, num), 
                ActionReason.SubfieldDrop, true);
        } else {
            // share + give 组合: 给所有玩家
            dropScene.getPlayers().forEach(x -> 
                x.getInventory().addItem(new GameItem(itemData, num), 
                    ActionReason.SubfieldDrop, true));
        }
    }
}
```

### 4.3 路径 A vs B：玩家体感差异

**A 路径（落地）**：
- 客户端看到飞出来的物品 + 自动吸附音效
- 必须走近拾取
- 离怪物太远可能错过
- 有"满满当当一堆战利品"的视觉

**B 路径（直接进背包）**：
- 没有动画，右上角弹"获得 XXX"
- 必拿到（不会错过）
- 适合任务关键道具（"必须给"）
- 联机 boss 奖励一般是这种

→ "给"和"落"的选择是**游戏感设计**，不是技术限制。

### 4.4 ITEM_VIRTUAL 的特殊处理

```java
if (!dd.isGive() && (itemData.getItemType() != ItemType.ITEM_VIRTUAL || itemData.getGadgetId() != 0))
```

**条件细读**：
- give=false 且 (不是虚拟 OR 有 gadgetId) → 落地
- 否则 → 进背包

→ **纯虚拟物品（摩拉/原石）没 gadgetId 时不能落地** —— 摩拉是数字，没法做实体。
→ 但**摩拉有时候能看到飞出来** —— 因为它配了 gadgetId（"摩拉袋"实体），落地的是袋子，捡起来转成数字。

---

## 5. EntityItem 落地动画

```java
val createConfig = new CreateGadgetEntityConfig(itemData, num)
    .setBornPos(pos)              // ★ 落地位置
    .setPlayerOwner(target)        // 拥有者 (null=公共)
    .setShareItem(dd.isShare());
```

### 5.1 装备类：每件一个独立实体

```java
if (itemData.isEquip()) {
    for (int i = 0; i < num; i++) {
        float range = (2.5f + (.05f * num));
        Position pos = em.getPosition().nearby2d(range).addY(3f);
        //              ↑ 在怪物 2.5+ 范围随机散开, Y+3 抛物线起点
        addDropEntity(dd, em.getScene(), itemData, pos, num, gp);
    }
} else {
    // 材料: 一个实体, num 个数量
    Position pos = em.getPosition().clone().addY(3f);
    addDropEntity(dd, em.getScene(), itemData, pos, num, gp);
}
```

**为什么装备每件一实体**：
- 玩家**看到 3 把武器各自飞出来**（更有"丰收感"）
- 每件可单独捡起 / 鉴定

**材料合一**：
- 5 个史莱姆凝液 = 1 个袋子（5）
- 减少 entity 数量节省网络

### 5.2 nearby2d + Y+3：抛物线起点

```java
em.getPosition().nearby2d(range).addY(3f)
```

- `nearby2d(range)` —— 在水平 XZ 平面随机偏移 `range` 距离
- `addY(3f)` —— 抬高 3 米（约角色身高）

→ 客户端看到的"物品飞出来"：从怪物身上 3 米高处弹出，落地散开。这就是经典的**抛物线掉落动画**。

### 5.3 range 公式

```java
float range = (2.5f + (.05f * num));
```

掉 5 件 → range = 2.75；掉 20 件 → range = 3.5
→ **数量多时散得更开**，避免堆叠在一起视觉混乱。

---

## 6. 完整调用链：怪物死 → 物品到手

```
[1] EntityMonster (HP=0) → Scene.killEntity(monster)
    ↓
[2] Scene.killEntity():
    if (target instanceof EntityMonster monster) {
        if (getSceneType() != SceneType.SCENE_DUNGEON && attacker != null) {
            getWorld().getServer().getDropSystem().callDrop(monster);
            //                                              ↑ ★ 这里入口
        }
    }
    
[3] DropSystem.callDrop(monster):
    for (DropData dd : dropData[monsterId]):
        if (dd.isShare):
            processDrop(dd, monster, null)      // 共享掉落
        else:
            for player in scene.players:
                processDrop(dd, monster, player) // 个人掉落
                
[4] processDrop:
    掷 1-10000 → 命中 weight 区间?
    ↓ 是
    随机数量 num = [minCount, maxCount]
    ↓
    装备 → 每件 nearby2d + addY(3) → addDropEntity
    材料 → 单实体 + addY(3) → addDropEntity
    
[5] addDropEntity:
    give=false → 创建 EntityItem 落地
        ├─ share=true: scene.addEntity (全员显示)
        └─ share=false: scene.addEntityToSingleClient (个人显示)
    give=true → 直接 inventory.addItem (SubfieldDrop)
    
[6] 玩家走近拾取:
    EntityItem.onPickup → inventory.addItem(SubfieldDrop)
    EntityItem.killEntity (从场景移除)
    
[7] Inventory.addItem 触发钩子:
    - 战令: TRIGGER_OBTAIN_MATERIAL_NUM
    - 任务: QUEST_CONTENT_OBTAIN_ITEM × 2
    - 任务: QUEST_COND_PACK_HAVE_ITEM
```

---

## 7. 关键检查点

### 7.1 副本不掉落（关键！）

```java
if (getSceneType() != SceneType.SCENE_DUNGEON && attacker != null) {
    getWorld().getServer().getDropSystem().callDrop(monster);
}
```

→ **副本里怪物死了不调用 callDrop** —— 副本奖励**只由通关结算给**，避免双重发放。

副本掉落路径：
```
副本 boss 死 → DungeonManager.onDungeonFinish → 给配置好的固定奖励
```

→ 这就是为什么"副本里打的怪不掉东西"的原因。

### 7.2 attacker != null

```java
if (... && attacker != null) callDrop(monster);
```

→ **无人击杀**（如自然消失/Lua 强制 kill）**不掉落**。
→ 防止"用 GM 命令杀光全图 → 一键满背包"。

---

## 8. dropSubfield 通用系统（路径 B）

`GameEntity.dropSubfield()` 是**通用工具方法**，可被任何 entity 调用：

```java
public boolean dropSubfield(String subfieldName) {
    var subfieldMapping = GameData.getSubfieldMappingMap().get(getEntityTypeId());
    if (subfieldMapping == null) return false;
    
    for (var entry : subfieldMapping.getSubfields()) {
        if (entry.getSubfieldName().compareTo(subfieldName) == 0) {
            return dropSubfieldItem(entry.getDrop_id());
        }
    }
    return false;
}

public boolean dropSubfieldItem(int dropId) {
    var drop = GameData.getDropSubfieldMappingMap().get(dropId);
    var dropTableEntry = GameData.getDropTableExcelConfigDataMap().get(drop.getItemId());
    
    Int2ObjectMap<Integer> itemsToDrop = new Int2ObjectOpenHashMap<>();
    
    switch (dropTableEntry.getRandomType()) {
        case 0: // select one (按权重选一个)
            int weightCount = 总权重;
            int randomValue = random.nextInt(weightCount);
            // 命中区间则选中
            for (entry : dropVec) {
                if (randomValue in [...]) {
                    itemsToDrop.put(entry.itemId, countRange);
                }
            }
            break;
        case 1: // select various (每个独立掷骰)
            for (entry : dropVec) {
                if (entry.getWeight() < random.nextInt(10000)) {
                    itemsToDrop.put(entry.itemId, countRange);
                }
            }
            break;
    }
    
    // 生成 EntityItem
    for (entry : itemsToDrop) {
        val itemData = GameData.getItemDataMap().get(entry.getIntKey());
        val createConfig = new CreateGadgetEntityConfig(itemData, entry.getValue())
            .setBornPos(getPosition().nearby2d(1f).addY(0.5f));
        EntityItem item = new EntityItem(scene, createConfig);
        scene.addEntity(item);
    }
}
```

### 8.1 DropTableExcelConfigData 字段

```java
@Data
public class DropTableExcelConfigData {
    private int id;
    private int randomType;       // 0=互斥 / 1=独立
    private int dropLevel;        // 等级限制
    private DropVectorEntry[] dropVec;
    private int nodeType;
    private boolean fallToGround; // 是否落地
    private int sourceType;
    private int everydayLimit;    // ★ 每日上限
    private int historyLimit;     // ★ 历史上限
    private int activityLimit;    // ★ 活动上限
    
    @Data
    public class DropVectorEntry {
        private int itemId;
        private String countRange;   // "1;3" → 1-3
        private int weight;
    }
}
```

### 8.2 两种 randomType

**Type 0: select one（互斥）**
```
所有条目竞争权重总和
掷骰 1 ~ totalWeight 选一个
适用: 强弱锁掉落 (出金就不出银)
```

**Type 1: select various（独立）**
```
每个条目独立判断 weight < random(10000)
适用: 通用材料 (每种独立概率)
```

### 8.3 DropTable 的 3 种限制

```java
private int everydayLimit;    // 每日最多掉 N 个
private int historyLimit;     // 历史总共最多 N 个 (永久)
private int activityLimit;    // 本次活动最多 N 个
```

→ **防止刷怪暴富**：某些珍稀物品有日限/永久限。

注意：**grasscutter 没实现限制检查** —— 字段有但没生效（开源私服宽松）。米哈游正服肯定有。

---

## 9. 谁会调 dropSubfield

```bash
grep -rn "dropSubfield" src/
```

主要调用者：
- **GadgetGatherObject.dropItems** (notes/33) —— 矿物/水晶飞出来
- **EntityScene** —— 砍树掉木材
- **某些怪物的 onDeath** —— 特殊 boss 用 DropTable 而非 Drop.json
- **Lua 脚本** —— spawn_drop 函数

→ `dropSubfield` 是"通用掉落工具"，主要服务于**非怪物来源**。

---

## 10. 关键对比表：DropSystem vs DropTable

| 维度 | DropSystem (A) | DropTable (B) |
|---|---|---|
| 配表 | Drop.json (自定义) | DropTableExcelConfigData (mihoyo Excel) |
| 入口 | Scene.killEntity → callDrop | entity.dropSubfield(name) |
| 触发条件 | 怪物自动调用 | 主动手动调用 |
| 权重范围 | 1-10000 | 1-totalWeight 或 1-10000 |
| 随机类型 | 1 种（独立掷骰）| 2 种（互斥 / 独立）|
| 数量随机 | minCount-maxCount | countRange "x;y" |
| share/give | ✓ 显式控制 | 默认共享落地 |
| 限制 | 无 | everydayLimit/historyLimit/activityLimit 字段（未实现）|
| 主要用途 | 普通怪物掉落 | 采集物 / 自然产物 |

→ **两套系统互补**：A 简单粗暴覆盖怪物，B 精细可控覆盖采集/活动。

---

## 11. 反作弊视角

### 11.1 服务器掌控的部分

- ✓ 怪物 HP=0 才触发 callDrop
- ✓ 副本不掉（避免双重发放）
- ✓ attacker=null 不掉（避免 GM 暴富）
- ✓ Inventory.addItem 服务器执行（不能直接发"加 999 摩拉"）

### 11.2 客户端无法干预

```
[客户端] 我打的伤害 = 9999     (可以伪造)
       ↓
[服务器] HP -= 9999
       ↓
[服务器] HP <= 0 → killEntity
       ↓
[服务器] callDrop (服务器算)
       ↓
[服务器] 掉落进背包 (服务器算)
```

→ **只能加速通关（伪造伤害），不能多拿掉落**（数量服务器算）。

### 11.3 唯一薄弱点：拾取范围

`EntityItem` 落地后玩家可拾取——客户端发"我拾取了"包：
- 理论上可以伪造**远距离拾取**
- 但 EntityItem 是 share=false 单玩家可见的话，**别人无法伪造他的物品**
- 服务器接收拾取包做粗略距离校验

---

## 12. 设计模式总结

### 12.1 数据驱动随机

```
DropData (配表 7 字段)
   ↓
processDrop (10 行随机逻辑)
   ↓
addDropEntity (8 行实体化逻辑)
```

加新怪掉落：**只改 Drop.json，不改一行 Java**。

### 12.2 路径分离：落地 vs 直接

```
普通怪物 → 落地 (玩家感) 
任务怪/Boss → 直接进背包 (确定性)
联机共享 → 共享 EntityItem (公平)
个人专属 → addEntityToSingleClient (隔离)
```

### 12.3 联机独立掷骰

```
share=false 时, 每个玩家独立掷骰
4 玩家联机 = 4 倍掉落机会 (但每人独立)
```

→ 这就是"联机能多拿东西"的底层机制。

---

## 13. 关键收获

1. **两套并存的掉落系统**：DropSystem (怪物专用) + dropSubfield (通用)
2. **DropSystem 极简**：112 行代码 + Drop.json 配表 = 完整怪物掉落
3. **DropData 7 字段**：minWeight / maxWeight / itemId / minCount / maxCount / share / give
4. **权重 1-10000 独立掷骰**：每条 DropData 独立命中
5. **数量随机 [minCount, maxCount]**：含两端
6. **4 种 share × give 组合**：个人落地 / 共享落地 / 个人直接 / 共享直接
7. **联机 share=false 每个玩家独立掷骰**：4 人 = 4 倍机会
8. **装备每件一实体**：nearby2d(2.5+.05*num) 散开
9. **材料合一实体**：减少网络包数
10. **抛物线起点**：addY(3) 让物品从 3 米高弹出
11. **副本不掉**：SceneType.SCENE_DUNGEON 跳过 callDrop
12. **attacker=null 不掉**：防 GM/Lua 暴富
13. **DropTable 2 种 randomType**：互斥 (强弱锁) / 独立 (通用)
14. **DropTable 3 种 limit 字段**：日限 / 永久 / 活动（grasscutter 未实现）
15. **客户端无法伪造数量**：掉落计算在服务器

---

## 14. 一句话总结

> **Drop 系统 = onDeath → addItem 的随机抽奖管道。DropSystem (A) 用 Drop.json 给怪物配掉落，权重 1-10000 独立掷骰，share×give 四种组合控制落地/直接、个人/共享；dropSubfield (B) 用 DropTable Excel 配采集/活动，支持互斥 vs 独立两种 randomType。**
> 
> **设计哲学：数据驱动随机 + 路径分离 (落地 vs 直接) + 联机独立掷骰 (玩家运气独立) + 副本零掉落避免双发——既保证经济可控又保留"打怪开宝箱"的游戏感。**

---

**前置笔记**：
- notes/32 怪物系统 - EntityMonster.onDeath 触发掉落
- notes/33 Gadget 系统 - GadgetGatherObject.dropItems 调 dropSubfield
- notes/35 Scene/World - Scene.killEntity 入口
- notes/38 Inventory - addItem(ActionReason.SubfieldDrop)

**关联文件**：
- `DropSystem.java`(112) - 怪物掉落核心
- `DropData.java`(55) - 7 字段配置
- `DropInfo.java`(16) - monsterId + 列表
- `DropTableExcelConfigData.java`(24) - DropTable 配表
- `DropSubfieldMapping.java`(10) - subfield 映射
- `GameEntity.dropSubfield/dropSubfieldItem` (在 GameEntity.java:287-348)
- `EntityItem.java` - 落地实体

**研究的源代码**: 217 行 Drop 系统代码 + GameEntity 的 dropSubfield 部分。
