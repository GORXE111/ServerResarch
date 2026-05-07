# 17 · Codex 系统 · 寄生型图鉴的优雅实现

完成的任务、击杀的怪物、获得的武器、阅读的书籍都进图鉴——但服务器并没有一个"Codex 系统"独立存在。它是**寄生在 6 个其他子系统上的极薄抽象**。

> 核心代码：`game/player/PlayerCodex.java` (~130 行)、`data/excels/Codex*Data.java` (6 类)、`server/packet/send/PacketCodexData*Notify.java`  
> 数据：`BinOutput/CodexQuest/*.json` (274 个剧情存档)、`*CodexExcelConfigData.json` (6 类元数据)

---

## 1. PlayerCodex：8 个 Set/Map 而已

整个 Codex 在 server 端存的就这点东西（`PlayerCodex.java:18-30`）：

```java
@Entity
public class PlayerCodex {
    private Set<Integer>          unlockedWeapon;          // 武器 itemId 集合
    private Map<Integer, Integer> unlockedAnimal;          // monsterId → 击杀次数
    private Set<Integer>          unlockedMaterial;        // 材料/食物 itemId 集合
    private Set<Integer>          unlockedBook;            // 书籍 bookId
    private Set<Integer>          unlockedTip;             // 提示卡片 tipId
    private Set<Integer>          unlockedView;            // 观景点 viewId
    private Set<Integer>          unlockedReliquary;       // 圣遗物 itemId（标准化后）
    private Set<Integer>          unlockedReliquarySuitCodex;  // 圣遗物套装齐全标记
}
```

→ **8 个集合**，全部跟玩家存档绑定。**没有 unlockedQuest**——任务图鉴直接用 QuestManager 的状态。

> 注释里直接说："itemId is not codexId!" —— PlayerCodex 存原始 itemId，发包给客户端时再用映射表转成 codexId（发 [`PacketCodexDataFullNotify.java`](#) 时做）。

---

## 2. 6 类 Codex 元数据（独立配表）

每类图鉴各有自己的 Excel 配表，**Schema 简单**：

| 文件 | 字段（典型） | 解锁触发 |
|---|---|---|
| `WeaponCodexExcelConfigData.json` | `id`, `weaponId`, `sortOrder` | 获得任意带词条武器 |
| `MaterialCodexExcelConfigData.json` | `id`, `materialId`, `sortOrder` | 获得食物/工具/兑换品/角色升级材料/治疗物 |
| `AnimalCodexExcelConfigData.json` | `id`, `monsterId`, `countType (KILL/CAPTURE)`, `describeId` | 击杀或捕获指定怪物/动物 |
| `ReliquaryCodexExcelConfigData.json` | `suitId`, `cupId`, `headId`, ... 各部位 | 集齐 5 件圣遗物 |
| `ViewpointCodexExcelConfigData.json` | `id`, `cityId`, `groupId` | 走到对应观景点 |
| `QuestCodexExcelConfigData.json` | `id`, `parentQuestId`, `chapterId`, `sortOrder` | MainQuest 完成 |

→ 每类 codex 都把"游戏内现有数据（武器、怪物、任务）"映射到一个 `codexId` + 排序规则。**Codex 不创造新内容，只重组现有内容**。

---

## 3. 自动归档：6 个分散触发点

PlayerCodex **从来不被主动调用归档**——所有触发点都散在各个业务系统里。这是**寄生型设计**的精髓。

### 3.1 物品系统：背包加东西时

```java
// Inventory.java:333 (putItem)
private synchronized void putItem(GameItem item, InventoryTab tab) {
    this.player.getCodex().checkAddedItem(item);   // ★ 每个加道具操作都触发
    item.setOwner(this.player);
    ...
}
```

PlayerCodex 内部按 ItemType 分流：

```java
// PlayerCodex.java:52
public void checkAddedItem(GameItem item) {
    switch (itemData.getItemType()) {
        case ITEM_WEAPON -> {
            // 查武器图鉴表
            Optional.ofNullable(GameData.getCodexWeaponDataIdMap().get(itemId))
                .ifPresent(codexData -> {
                    if (this.getUnlockedWeapon().add(itemId)) {  // 首次添加
                        this.player.save();
                        this.player.sendPacket(new PacketCodexDataUpdateNotify(
                            CodexType.CODEX_WEAPON, codexData.getId()));
                    }
                });
        }
        case ITEM_MATERIAL -> {
            // 只算食物/工具/兑换/角色材料/恢复药 5 类
            switch (itemData.getMaterialType()) {
                case MATERIAL_FOOD, MATERIAL_WIDGET, MATERIAL_EXCHANGE, 
                     MATERIAL_AVATAR_MATERIAL, MATERIAL_NOTICE_ADD_HP -> {
                    Optional.ofNullable(GameData.getCodexMaterialDataIdMap().get(itemId))
                        .ifPresent(codexData -> {
                            if (this.getUnlockedMaterial().add(itemId)) {
                                this.player.save();
                                this.player.sendPacket(new PacketCodexDataUpdateNotify(
                                    CodexType.CODEX_MATERIAL, codexData.getId()));
                            }
                        });
                }
                default -> {}  // 矿石/史莱姆凝液这些不进图鉴
            }
        }
        case ITEM_RELIQUARY -> {
            val reliquaryId = (itemId/10) * 10;  // 标准化（剥离词条编号）
            if (this.getUnlockedReliquary().add(reliquaryId))
                checkUnlockedSuits(reliquaryId);   // 自动检查套装齐全
        }
    }
}
```

### 3.2 战斗系统：击杀生物时

```java
// Scene.java:427 (实体死亡处理)
if (target instanceof EntityMonster) {
    // 玩家直接击杀
    if (avatarAttacker != null) {
        avatarAttacker.getPlayer().getCodex().checkAnimal(target, 
            CodexAnimalData.CountType.CODEX_COUNT_TYPE_KILL);
    }
    // 召唤物击杀（如雷神电棒）
    if (gadgetAttacker != null) {
        gadgetAttacker.getOwner().getCodex().checkAnimal(target, 
            CodexAnimalData.CountType.CODEX_COUNT_TYPE_KILL);
    }
}
```

PlayerCodex.checkAnimal：

```java
public void checkAnimal(GameEntity target, CodexAnimalData.CountType countType) {
    val monsterId = ((EntityMonster) target).getMonsterData().getId();
    val codexAnimal = GameData.getCodexAnimalDataMap().get(monsterId);
    if (codexAnimal == null) return;
    if (codexAnimal.getCountType() != countType) return;   // 比如猎物只算 CAPTURE 不算 KILL
    
    this.getUnlockedAnimal().merge(monsterId, 1, (i, j) -> i + 1);  // 增加击杀计数
    player.save();
    sendPacket(new PacketCodexDataUpdateNotify(CodexType.CODEX_ANIMAL, monsterId));
}
```

注意：
- 是 **Map<monsterId, count>**，不是 Set——记录"击杀了多少次史莱姆"
- `countType` 决定算 KILL 还是 CAPTURE。**有些生物只算捕获**（兔子、松鼠、晶蝶），不算击杀

### 3.3 场景系统：进入观景点

```java
// GadgetViewPoint.java:25
public void onInteract(Player player) {
    val viewPoint = GameData.getCodexViewpointDataMap().get(this.viewpointId);
    if (viewPoint != null) {
        player.getCodex().checkUnlockedViewPoints(viewPoint);
    }
}
```

→ 玩家走到"观景点"机关位置触发，归档进 `unlockedView`。

### 3.4 物品使用：阅读书籍

```java
// ItemUseUnlockCodex.java
@ItemUseAction(name="ITEM_USE_UNLOCK_CODEX")
public class ItemUseUnlockCodex extends ItemUseAction {
    public boolean useItem(UseItemParams params) {
        int bookId = params.intOption(...);
        params.player.getCodex().checkBook(bookId);
        return true;
    }
}
```

→ 用书 → 触发 `ITEM_USE_UNLOCK_CODEX` action → 加入 `unlockedBook`。

### 3.5 任务系统：MainQuest 完成

```java
// GameMainQuest.java:202 (finish)
public void finish(boolean isManualFinish) {
    ...
    this.getOwner().getSession().send(new PacketFinishedParentQuestUpdateNotify(this));
    this.getOwner().getSession().send(new PacketCodexDataUpdateNotify(this));   // ★
    this.save();
    ...
}
```

→ MainQuest 完成 → 直接发 codex 更新通知。**服务器不存"已完成的任务集合"**——客户端拿到通知后自己根据 QuestManager 状态渲染。

### 3.6 圣遗物套装：自动级联检测

```java
// PlayerCodex.java:105
public void checkUnlockedSuits(int reliquaryId) {
    GameData.getCodexReliquaryArrayList().stream()
        .filter(x -> !this.getUnlockedReliquarySuitCodex().contains(x.getId()))   // 没解锁的
        .filter(x -> x.containsId(reliquaryId))                                    // 含此件的
        .filter(x -> this.getUnlockedReliquary().containsAll(x.getIds()))          // 5 件齐
        .forEach(x -> {
            this.getUnlockedReliquarySuitCodex().add(x.getId());
            this.player.save();
            sendPacket(new PacketCodexDataUpdateNotify(CodexType.CODEX_RELIQUARY, x.getId()));
        });
}
```

→ 每加一件圣遗物，**重新检查所有相关套装是否齐全**。齐全才算"套装图鉴"解锁。

---

## 4. 数据格式：BinOutput/CodexQuest 的剧情存档

每个 MainQuest 在 `BinOutput/CodexQuest/<id>.json` 有一份**精简的对话记录**（去混淆后的结构）：

```jsonc
{
    "id": 1000,
    "mainQuestTitle":   { "textHash": 492421553,    "type": "MainQuestTitle" },
    "mainQuestDesp":    { "textHash": 911637991,    "type": "MainQuestDesp" },
    "chapterTitle":     { "textHash": 67026961,     "type": "ChapterTitle" },
    "chapterNum":       { "textHash": 3223313530,   "type": "ChapterNum" },
    "subQuests": [
        {
            "subQuestTitle": { "textHash": 66312846, "type": "SubQuestTitle" }
        },
        {
            "subQuestTitle": { "textHash": 73951502, "type": "SubQuestTitle" },
            "dialogs": [
                {
                    "speakerType": 3,
                    "dialogType": "SingleDialog",
                    "showOption": 4,
                    "speakerName": { "textHash": 1356475093, "type": "SpeakerKnown" },
                    "options": [...]
                },
                ...
            ]
        }
    ]
}
```

→ **CodexQuest 不是对 BinOutput/Quest 的简单复制**——它是 **"提取出对话内容、压缩成可滚动的剧情阅读体验"** 的版本。

玩家在游戏里打开"任务图鉴 → 重看剧情"时，UI 用这份数据渲染整段对话流。**所以这份数据是"剧本回放"的源**。

---

## 5. PacketCodexDataFullNotify：登录时全量推送

玩家登录时，server 把所有已解锁的 codex 一次推过去（`PacketCodexDataFullNotify.java`）：

```java
List<Integer> weaponCodexIdList = unlockedWeapon.stream()
    .map(itemId -> CodexWeaponData.itemIdToCodexId.get(itemId))   // itemId → codexId 映射
    .toList();

List<Integer> animalCodexIdList = unlockedAnimal.keySet().stream()
    .map(monsterId -> CodexAnimalData.monsterIdToCodexId.get(monsterId))
    .toList();

// 类似处理 material / book / view / reliquary suit
```

→ **首次登录推全量，之后只发增量 PacketCodexDataUpdateNotify**。客户端本地维护"已解锁集合"，每次开图鉴 UI 时直接显示。

---

## 6. 设计精髓：寄生 vs 独立子系统

### Codex 不是独立系统的证据

| 你以为的 | 实际上 |
|---|---|
| Codex 系统有独立的 manager | 没有！PlayerCodex 是个数据持有者，没有 onTick / 主循环 |
| Codex 系统有独立的事件总线 | 没有！靠业务系统主动调用 `getCodex().checkXxx(...)` |
| Codex 系统有独立的 4 线程池 | 没有！全部在调用方的线程同步执行 |
| Codex 系统有专门的 packet 总线 | 只有 2 个 packet（Update + Full Notify），简单单向 |

### 寄生型 vs 独立型的取舍

| 维度 | 寄生型（Codex）| 独立型（Quest）|
|---|---|---|
| 实现复杂度 | 极低 | 高（事件总线 + 异步池）|
| 性能开销 | 直接耦合 | 异步隔离 |
| 可扩展性 | 加新触发点要改业务代码 | 加新 cond/exec 类型完全独立 |
| 适用场景 | **逻辑简单 + 触发点固定** | **逻辑复杂 + 大量分支** |

**Codex 的逻辑就是"unlock 一个东西，发个通知"**——简单到不需要任何独立架构。**强行抽象反而过度设计**。

→ 这印证了 Grasscutter 工程师的判断力：**该抽象的抽象（Quest），该简化的简化（Codex）**。

---

## 7. 完整流程示例：玩家走完整个 Caribert 章节

```
[玩家进入沙漠区域，路过观景点]
   GadgetViewPoint.onInteract
   → checkUnlockedViewPoints(viewpoint=12030004)
   → unlockedView.add(12030004) + sendPacket(...)
   📖 图鉴解锁: "晨曦山顶"

[玩家击杀沙漠中的丘丘人萨满]
   Scene 实体死亡处理
   → checkAnimal(monster=21010301, KILL)
   → unlockedAnimal[21010301] += 1
   📖 图鉴: "丘丘萨满 击杀次数: 1"

[玩家拾起一个圣遗物]
   Inventory.addItem(31811) ← 来歆余响 (沙漠遗珍 5 件套之一)
   → putItem → checkAddedItem(item)
   → unlockedReliquary.add(31810)  // 标准化为不带词条数的版本
   → checkUnlockedSuits(31810)
     → 套装 ID 15030 含 [31810, 31820, 31830, 31840, 31850]
     → 但当前只有 31810 → 套装未齐全 → 不解锁 suitCodex

[玩家开启一个宝箱获得新武器]
   Inventory.addItem(15405) ← 风鹰剑
   → checkAddedItem(item)
   → unlockedWeapon.add(15405) + sendPacket(...)
   📖 图鉴: "风鹰剑 已收录"

[玩家完成 MainQuest 3022 "识藏日"]
   GameMainQuest.finish()
   → sendPacket(new PacketCodexDataUpdateNotify(this))
   📖 任务图鉴: "识藏日 已完成"
   → 客户端 UI 现在可以打开"重看剧情"
   → 拉取 CodexQuest/3022.json 的存档
   → 按 subQuests + dialogs 渲染整段剧情
```

→ **每个动作触发对应的 codex 更新**，但服务器侧逻辑分散在各个业务系统里——耦合而不臃肿。

---

## 8. 给做大型 RPG 图鉴系统开发者的提炼

1. **不要为图鉴建独立子系统**——它就是个集合 + 通知器，没必要异步池/事件总线
2. **集合命名空间和原始 ID 命名空间分开**——`itemId vs codexId` 解耦让客户端 UI 排序自由
3. **触发点散落在业务系统里**——背包加道具、击杀怪物、走观景点各自调用 codex.checkXxx
4. **Map 而非 Set 用于"计数型"图鉴**（如击杀次数、捕获数）
5. **复合解锁要级联检查**（如圣遗物套装：每加一件检查所有相关套装）
6. **登录时全量 + 运行时增量**：减少同步开销
7. **图鉴元数据独立配表**：每类一个 Excel，互不干扰
8. **剧情图鉴是数据复用**：不重新存对话，而是预生成精简的"剧本回放"格式

---

## 9. 数据规模

* 274 个 MainQuest 的 Codex 剧本（占任务总数 ~12%——有些任务不进图鉴）
* 5079 个 NPC 中**只有"特殊生物"进图鉴**（Animal Codex 估计 100-200 个）
* 武器图鉴：~300 件（含所有星级）
* 圣遗物套装图鉴：~30 套
* 书籍：~200 本
* 观景点：~300 个

总和：~1500-2000 项可解锁内容，全部塞进 8 个 Set/Map，存储成本忽略不计。

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/player/PlayerCodex.java` (核心，130 行)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/data/excels/CodexQuestData.java` 等 6 个元数据类
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/send/PacketCodexDataUpdateNotify.java` (增量推送)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/send/PacketCodexDataFullNotify.java` (登录全量)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/inventory/Inventory.java:333` (背包触发点)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/world/Scene.java:427` (击杀触发点)
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/quest/GameMainQuest.java:202` (任务完成触发点)
- 数据：`GenshinData/BinOutput/CodexQuest/*.json` (274 剧本) + 各 `*CodexExcelConfigData.json`
