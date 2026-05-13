# GameData / 资源加载体系深度剖析

> 第 45 篇：50+ 次引用的 `GameData.getXxxMap()` 终于揭秘 —— 160+ 静态 Map 字段、111 个 Excel 配表、4 级 LoadPriority 优先级、并行加载 + 反射注册——是所有 Manager 的"数据底座"。

---

## 0. 为什么这一篇重要

前 44 篇笔记里 `GameData.xxx` 出现 **100+ 次**：
- `GameData.getMonsterDataMap().get(id)` (notes/32)
- `GameData.getItemDataMap().get(itemId)` (notes/38)
- `GameData.getAvatarDataMap()` (notes/24)
- `GameData.getMainQuestDataMap()` (notes/02/43)
- `GameData.getDropTableExcelConfigDataMap()` (notes/39)

但**它怎么加载？什么时候？谁调？文件结构什么样？**——这一篇统一回答。

---

## 1. 三层数据结构

```
┌────────────────────────────────────────────────────────────────┐
│  GameData (静态聚合, 361 行)                                     │
│  - 160+ 静态 Map 字段 (Int2ObjectMap / Map)                      │
│  - @Getter 暴露给所有业务                                         │
│  - getMapByResourceDef(class) ← 反射反向查找                      │
└────────────────────────┬───────────────────────────────────────┘
                         │ 装载内容
                         ↓
┌────────────────────────────────────────────────────────────────┐
│  GameResource (基类, 12 行)                                      │
│  - public int getId()                                            │
│  - public void onLoad()  ← 反序列化后回调                         │
│  - 200+ 子类 (xxxData / xxxConfigData)                            │
└────────────────────────┬───────────────────────────────────────┘
                         │ 通过注解描述
                         ↓
┌────────────────────────────────────────────────────────────────┐
│  @ResourceType 注解 + ResourceLoader (1031 行)                   │
│  - name="xxxExcelConfigData.json"                                │
│  - loadPriority=HIGHEST/HIGH/NORMAL/LOW/LOWEST                   │
│  - Reflections 扫描所有 GameResource 子类                          │
│  - 并行加载 + json/tsj/tsv 多格式                                  │
└────────────────────────────────────────────────────────────────┘
```

→ **总计 ~1400 行**支撑所有静态数据。

---

## 2. GameData：160+ 静态 Map 的"大型字典"

`GameData.java` 361 行 —— **70% 是字段声明**：

```java
public class GameData {
    // BinOutputs (运行时配置)
    @Getter private static final Int2ObjectMap<HomeworldDefaultSaveData> homeworldDefaultSaveData;
    @Getter private static final Int2ObjectMap<String> abilityHashes;
    @Getter private static final Map<String, AbilityData> abilityDataMap;
    @Getter private static final Map<String, TalentData> talents;
    @Getter private static final Map<String, ConfigEntityAvatar> avatarConfigData;
    @Getter private static final Map<String, ConfigEntityGadget> gadgetConfigData;
    @Getter private static final Map<String, ConfigEntityMonster> monsterConfigData;
    // ... 30+ binout maps
    
    // ExcelConfigs (Excel 配表)
    @Getter private static final Int2ObjectMap<ActivityCondExcelConfigData> activityCondExcelConfigDataMap;
    @Getter private static final Int2ObjectMap<AvatarData> avatarDataMap;
    @Getter private static final Int2ObjectMap<AvatarSkillData> avatarSkillDataMap;
    @Getter private static final Int2ObjectMap<MonsterData> monsterDataMap;
    @Getter private static final Int2ObjectMap<ItemData> itemDataMap;
    // ... 110+ excel maps
    
    // Server (服务端补丁数据)
    @Getter private static final Int2ObjectMap<DungeonDropEntry> dungeonDropDataMap;
    // ... 20+ server-side maps
}
```

### 2.1 Map 类型选择

```java
Int2ObjectMap<X>           // ★ 主流 - 按 int ID 索引 (fastutil 高性能)
Int2ObjectLinkedOpenHashMap // 需要保持插入顺序的（如角色列表）
Map<String, X>              // 按字符串名索引（ability/talent name）
ArrayList<X>                // 偶尔的列表（如 codexReliquaryArrayList）
```

**为什么大量用 fastutil**：
- `HashMap<Integer, X>` 每个 key 都装箱（Integer 对象）→ 内存+GC 压力
- `Int2ObjectMap` 原生 int → **节省 30-50% 内存**
- 几十万个对象时差距显著

### 2.2 主流 Map 统计

通过 `grep -c "@Getter private static final.*Map\|private static final.*Map"` 得 **160 个 Map** 字段。

按内容粗略分类：
| 类别 | Map 数量 | 例子 |
|---|---|---|
| Activity 系列 | 15+ | activityCondExcelConfigData / activityWatcherData |
| Avatar 系列 | 12+ | avatarData / avatarSkillData / avatarTalentData |
| Quest 系列 | 8+ | mainQuestData / questDataMap / questsKeys |
| Battle/Combat | 8+ | monsterData / monsterCurveData / monsterAffixData |
| Inventory 系列 | 10+ | itemData / equipAffixData / reliquaryAffixData |
| Scene 系列 | 12+ | sceneData / sceneTagData / sceneRouteData |
| Codex 系列 | 6+ | codexAnimalData / codexMaterialData |
| Dungeon 系列 | 10+ | dungeonData / dungeonChallengeConfigData |
| Tower 系列 | 4+ | towerLevelData / towerFloorData |
| Home 系列 | 10+ | homeWorldBgmData / homeWorldLevelData |
| 其他 | 60+ | ... |

→ **160+ Map** = grasscutter 中**最大的全局状态**——但全是**只读**（启动后不变）。

---

## 3. @ResourceType 注解：数据驱动加载

`ResourceType.java`（38 行）：
```java
@Retention(RetentionPolicy.RUNTIME)
public @interface ResourceType {
    String[] name();                                              // 文件名 (可多个)
    LoadPriority loadPriority() default LoadPriority.NORMAL;       // 加载顺序
    
    public enum LoadPriority {
        HIGHEST (4),   // ★ 第一批
        HIGH    (3),
        NORMAL  (2),
        LOW     (1),
        LOWEST  (0);   // ★ 最后一批
    }
}
```

### 3.1 注解使用样例

```java
@ResourceType(name = "MonsterExcelConfigData.json", loadPriority = LoadPriority.LOW)
public class MonsterData extends GameResource { ... }

@ResourceType(name = "AvatarExcelConfigData.json")  // 默认 NORMAL
public class AvatarData extends GameResource { ... }

@ResourceType(name = "ItemExcelConfigData.json")
public class ItemData extends GameResource { ... }
```

→ 加新配表：写一个 `extends GameResource` + 加注解 + 加字段到 GameData = **零改动加载逻辑**。

### 3.2 LoadPriority 的实际作用

某些资源**有依赖关系**：
- `MonsterData` 引用 `MonsterAffixData` —— Affix 必须先加载（HIGH）
- `AvatarData` 引用 `AvatarSkillDepotData` —— 技能组必须先加载
- `WeaponData` 引用 `WeaponCurveData` —— 曲线必须先加载

LoadPriority 解决依赖：
```
HIGHEST → HIGH → NORMAL → LOW → LOWEST
   ↓             ↓         ↓
基础元数据  → 引用方  → 派生数据
```

### 3.3 110 个 @ResourceType Excel 文件

```bash
$ ls data/excels/*.java | wc -l
111
```

**110+ Excel 配表** 各自有专属 GameResource 子类——这就是为什么 ExcelBinOutput 目录里有几百个 JSON 文件，每个对应一个 Data 类。

---

## 4. ResourceLoader.loadAll()：启动序列

`ResourceLoader.loadAll()` 第 111-156 行 —— **30 步加载流水线**：

```java
public static void loadAll() {
    if (loadedAll) return;
    
    logger.info("Loading resources...");
    
    // === 第 1 批: 配置基础 ===
    loadConfigData();              // 1. 配置数据
    
    // === 第 2 批: Ability 基础 ===
    loadAbilityEmbryos();           // 2. 能力胚胎
    loadTalents();                  // 3. 天赋
    loadOpenConfig();               // 4. open config
    loadAbilityModifiers();         // 5. 能力修饰器
    loadAbilityGroups();            // 6. 能力组
    
    // === 第 3 批: Excel 资源（最大批量）===
    loadResources(true);            // 7. 110+ 个 ExcelConfigData (并行!)
    
    // === 第 4 批: GameDepot 后处理 ===
    GameDepot.load();               // 8. 武器/圣遗物随机词条池
    
    // === 第 5 批: 场景/任务 ===
    loadSceneRoutes();              // 9. 场景路径
    loadScenePointArrays();         // 10. 场景点阵列
    loadSpawnData();                // 11. spawn 数据 (R-Tree 索引)
    loadQuests();                   // 12. 任务表 (2360 个 MainQuest)
    loadScriptSceneData();          // 13. Lua 场景脚本
    loadDungeonDrops();             // 14. 副本掉落
    loadScenePoints();              // 15. 场景点
    loadSceneWeatherAreas();        // 16. 天气区域
    loadDungeonEntryAndExitPoints();// 17. 副本出入口
    
    // === 第 6 批: 玩家初始数据 ===
    loadHomeworldDefaultSaveData(); // 18. 默认家园布局
    loadNpcBornData();              // 19. NPC 出生表 (per scene)
    loadBlossomResources();         // 20. 凋零之缘配置
    cacheTalentLevelSets();         // 21. 缓存天赋等级集
    
    // === 第 7 批: 高级特性 ===
    loadConfigLevelEntityData();    // 22. 场景实体配置
    loadScriptData();               // 23. 脚本数据
    loadGadgetMappings();           // 24. gadget 映射
    loadSubfieldMappings();         // 25. 子字段映射
    loadWeatherMappings();          // 26. 天气映射
    loadMonsterMappings();          // 27. 怪物映射
    loadActivityCondGroups();       // 28. 活动条件组
    loadCustomActivityData();       // 29. 自定义活动数据
    loadTrialAvatarCustomData();    // 30. 试用角色数据
    loadGlobalCombatConfig();       // 31. 全局战斗配置
    
    EntityControllerScriptManager.load();  // 32. Entity controller 脚本
    
    loadedAll = true;
}
```

### 4.1 30 步流水线观察

- **依赖驱动**：能力基础 → Excel → 场景 → 任务 → 高级特性
- **每步独立**：30 个 load 方法各管一摊
- **可重入保护**：`if (loadedAll) return` 防止重复加载

### 4.2 启动耗时（粗估）

```
ExcelConfigs (110+)        : 2-5 秒 (并行加速)
BinOutputs (configs)        : 1-3 秒
Quests (2360 mainQuest)    : 3-8 秒
SpawnData (R-Tree 索引)    : 1-2 秒
Scripts (Lua 解析)         : 5-15 秒 (大头)
TextMap                    : 2-5 秒 (notes/11)
```

→ **总启动 15-30 秒**，主要在 Lua + Quest + TextMap。

---

## 5. loadResources：并行加载 110+ 文件

`ResourceLoader.loadResources()` 第 162-187 行：
```java
public static void loadResources(boolean doReload) {
    long startTime = System.nanoTime();
    val errors = new ConcurrentLinkedQueue<Pair<String, Exception>>();
    
    // ★ 按 LoadPriority 分批
    getResourceDefClassesPrioritySets().forEach(classes -> {
        classes.stream()
            .parallel().unordered()    // ★ 同批内并行
            .forEach(c -> {
                val type = c.getAnnotation(ResourceType.class);
                if (type == null) return;
                
                val map = GameData.getMapByResourceDef(c);   // ★ 反射找到目标 Map
                if (map == null) return;
                
                try {
                    loadFromResource(c, type, map, doReload);
                } catch (Exception e) {
                    errors.add(Pair.of(Arrays.toString(type.name()), e));
                }
            });
    });
    
    errors.forEach(pair -> logger.error("Error loading: " + pair.left(), pair.right()));
    
    long ns = System.nanoTime() - startTime;
    logger.debug("Loading resources took {}ms", ns/1000000);
}
```

### 5.1 并行加载策略

```
Phase 1 (HIGHEST): 全部并行加载这一批
   ↓ 全部完成
Phase 2 (HIGH):    全部并行加载这一批
   ↓ 全部完成
Phase 3 (NORMAL):  全部并行加载这一批 (最多)
   ↓ 全部完成
Phase 4 (LOW):     全部并行
   ↓
Phase 5 (LOWEST):  全部并行
```

**性能优势**：
- ✓ 同级别 30 个文件 **并行**加载（核心数倍速度）
- ✓ 跨级别 **串行**（保证依赖）
- ✓ 错误收集到 `ConcurrentLinkedQueue` 不打断其他文件

### 5.2 错误并行收集

```java
val errors = new ConcurrentLinkedQueue<>();  // Logger in a parallel stream will deadlock
```

**注释解释了为什么**：
- 并行流里直接 `logger.error` 可能死锁（logger 加锁）
- 用并发队列收集 → 串行处理时打印

→ 经典踩坑：**并行流不能调用同步 logger**。

---

## 6. loadFromResource：单文件加载

```java
protected static <T> void loadFromResource(Class<T> c, Path filename, Int2ObjectMap map) throws Exception {
    val results = switch (FileUtils.getFileExtension(filename)) {
        case "json" -> JsonUtils.loadToList(filename, c);
        case "tsj"  -> TsvUtils.loadTsjToListSetField(filename, c);
        case "tsv"  -> TsvUtils.loadTsvToListSetField(filename, c);
        default     -> null;
    };
    if (results == null) return;
    
    results.forEach(o -> {
        GameResource res = (GameResource) o;
        res.onLoad();              // ★ 反序列化后回调
        map.put(res.getId(), res); // ★ 按 id 入索引
    });
}
```

### 6.1 3 种文件格式

| 格式 | 用途 | 例子 |
|---|---|---|
| **json** | 通用，可读性好 | `MonsterExcelConfigData.json` |
| **tsj** | mihoyo 自定义"tabbed JSON"（更紧凑）| 新版本配表 |
| **tsv** | tab-separated values | 极简配表 |

→ grasscutter 同时支持 3 种，**优先 json**（社区维护的），**fallback tsj/tsv**。

### 6.2 onLoad 回调钩子

```java
res.onLoad();
```

GameResource 子类可以**重写 onLoad** 做后处理：

```java
// MonsterData.onLoad()
@Override
public void onLoad() {
    for (int id : this.equips) {
        GadgetData gadget = GameData.getGadgetDataMap().get(id);
        if (gadget != null && gadget.getItemJsonName().equals("Default_MonsterWeapon")) {
            this.weaponId = id;   // ★ 缓存武器 ID
        }
    }
    this.describeData = GameData.getMonsterDescribeDataMap().get(this.getDescribeId());
    // ... 更多关联查找
}
```

**用途**：
- 解析后查找关联数据并缓存（避免运行时反复查）
- 索引扁平化（如 `equips[]` 中提取武器）
- 数据校验（缺失字段告警）

---

## 7. GameData.getMapByResourceDef：反射定位 Map

```java
// GameData.java
public static Int2ObjectMap<?> getMapByResourceDef(Class<?> resourceDefinition) {
    // ★ 按字段类型反射定位 Map
    for (Field f : GameData.class.getDeclaredFields()) {
        val type = f.getGenericType();
        if (type instanceof ParameterizedType pt) {
            val typeArgs = pt.getActualTypeArguments();
            if (typeArgs.length > 0 && typeArgs[typeArgs.length-1].equals(resourceDefinition)) {
                f.setAccessible(true);
                return (Int2ObjectMap<?>) f.get(null);
            }
        }
    }
    return null;
}
```

→ ResourceLoader 收到 `MonsterData.class` → 反射扫描 GameData 字段 → 找到 `Int2ObjectMap<MonsterData> monsterDataMap` → 返回引用。

→ **零代码加新 Map**：
1. GameData 加字段 `private static final Int2ObjectMap<NewData> newDataMap`
2. NewData 加 `@ResourceType(name = "NewExcelConfigData.json")`
3. **完成** —— ResourceLoader 自动发现并加载

---

## 8. DataLoader vs ResourceLoader：两个加载器的区别

```
┌─────────────────────────────────────────────────────────────┐
│  DataLoader (138 行) — 通用文件加载                          │
│  - 加载 /data/ 目录的自定义 JSON                              │
│  - 用于:                                                     │
│    - Drop.json (notes/39)                                    │
│    - EnergyDrop.json (notes/36)                              │
│    - SkillParticleGeneration.json                            │
│    - GameAnnouncement.json (notes/31)                        │
│  - fallback 到 jar 内 defaults/data/                         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  ResourceLoader (1031 行) — 主资源加载                       │
│  - 加载 /resources/ExcelBinOutput/ 的官方配表                 │
│  - 反射扫描 GameResource 子类                                  │
│  - 并行 + LoadPriority + 多格式                                │
│  - 写入 GameData 全局 Map                                      │
└─────────────────────────────────────────────────────────────┘
```

**关键区别**：
- DataLoader = **手动调用**加载特定文件
- ResourceLoader = **自动扫描**加载全部 @ResourceType

---

## 9. fallback 机制

```java
public static InputStream load(String resourcePath, boolean useFallback) {
    Path path = useFallback
        ? FileUtils.getDataPath(resourcePath)         // 1. 先看 /data/
        : FileUtils.getDataUserPath(resourcePath);    // 2. 否则用户目录
    if (Files.exists(path)) {
        return Files.newInputStream(path);
    }
    return null;   // 找不到
}
```

**3 级 fallback**：
1. `/data/` 优先（用户自定义）
2. `/data-user/` 次之（用户特定）
3. jar 内 `defaults/data/` 最后（默认）

→ 用户可**覆盖**默认资源 —— 改活动配置、调奖励等不用动 jar。

---

## 10. GameDepot：后处理"装备词条池"

`GameDepot.load()` 加载完资源后**第 8 步**调用：

```java
public class GameDepot {
    public static void load() {
        // 把武器副词条按 rank 分桶
        // 把圣遗物主词条按 slot+rank 分桶
        // 用于运行时随机词条
    }
    
    public static Map<Integer, List<ReliquaryAffixData>> getReliquaryAffixDataMap();
    public static List<ReliquaryAffixData> getRandomRelicAffixListByDepot(...);
    public static Map<Integer, SpawnGroupEntry> getSpawnLists();
    // ...
}
```

→ **Excel 加载是"原始数据"，GameDepot 是"使用前的预处理"**。

例：圣遗物副词条加成
- Excel: 1500+ 条 affix 配置
- GameDepot 后处理: 按 rank/slot 分桶 → "5 星头部副词条池"
- 运行时: 随机抽 4 个 → 圣遗物副词条

---

## 11. 加载性能数字（启动观察）

| 阶段 | 时间 |
|---|---|
| loadConfigData | < 100 ms |
| loadAbilityEmbryos / Talents / OpenConfig | 200-500 ms |
| **loadResources (110+ Excel)** | **2-5 秒 (并行)** |
| GameDepot.load | < 200 ms |
| loadSpawnData (R-Tree) | 1-2 秒 |
| **loadQuests (2360 mainQuest)** | **3-8 秒** |
| **loadScriptSceneData (Lua)** | **5-15 秒** (大头) |
| loadNpcBornData | 1-2 秒 |
| 其他 (zone/blossom/etc) | 1-3 秒 |
| **总计** | **15-30 秒** |

→ **Lua 解析是最慢的**——几千个 group.lua 要逐个 parse。

---

## 12. 内存占用（粗估）

```
ItemData × 2000+         : ~10 MB
MonsterData × 1700+      : ~20 MB
AvatarData × 70+         : ~5 MB
SubQuestData × 20893     : ~200 MB (最大!)
SceneData × 100+         : ~5 MB
GadgetData × 5000+       : ~30 MB
... + Lua AST            : ~500 MB (Lua 内存大头)
... + TextMap            : ~100 MB (大量字符串)

总计 ~1 GB 静态数据
```

→ grasscutter 一启动**就占 1-2 GB**内存——主要是静态资源。

---

## 13. 完整启动时序（GameServer.start → 处理客户端）

```
[GameServer.<init>]
   ↓
[ResourceLoader.loadAll()]                  ← ★ 这一篇主角
   ├── 30 步加载流水线
   ├── 110+ Excel ConfigData
   ├── 200+ BinOut 配置
   ├── 2360 MainQuest
   ├── 几千个 Lua 脚本
   └── 总计 15-30 秒
   ↓
[DatabaseManager.initialize()]              ← notes/30
   ↓
[GameSystem 注册]
   - QuestSystem  ← notes/43, 反射注册 190+ handler
   - DungeonSystem
   - DropSystem  ← notes/39
   - InventorySystem
   - WorldDataSystem
   ↓
[GameServerPacketHandler]                   ← notes/29, 反射注册 600+ packet
   ↓
[KCP listener 开始]                          ← 网络层就绪
   ↓
[等待客户端连接]
```

→ **GameData 是所有 System 的依赖** —— 必须**最先**完成加载。

---

## 14. 数据驱动哲学的极致

### 14.1 加新内容的零代码改动路径

**加新角色**（如新版本"克洛琳德"）：
1. Excel 文件加一行 `AvatarExcelConfigData.json`
2. **完成** —— ResourceLoader 自动加载，AvatarData 自动有新条目

**加新怪物**：
1. `MonsterExcelConfigData.json` 加行
2. `MonsterCurveExcelConfigData.json` 加曲线
3. `MonsterAffixExcelConfigData.json` 加词条
4. `Drop.json` 配掉落（DataLoader）
5. **完成** —— 不改一行 Java

**加新活动**：
1. `ActivityCondExcelConfigData.json` / `ActivityWatcherData.json` 加配置
2. Lua 脚本写活动逻辑
3. **完成**

→ 这是为什么**原神能 5 年来每版本几十个新内容**——代码框架完全是**数据驱动**。

### 14.2 反射的代价 vs 收益

**代价**：
- ✗ 启动慢（反射扫描类 + 多文件加载）
- ✗ 错误延迟（运行时才发现配表问题）
- ✗ IDE 不能直接追踪"哪个 Map 装哪种 Data"

**收益**：
- ✓ 加新数据零代码改动
- ✓ Excel + Java 解耦（策划改表不需要改代码）
- ✓ 整体一致性（所有数据走同一加载逻辑）

→ MMO 服务器**普遍这样设计**——收益远大于代价。

---

## 15. 关键收获

1. **GameData 160+ 静态 Map 字段**：是 grasscutter 中**最大的全局状态**
2. **Int2ObjectMap (fastutil) 主导**：避免 Integer 装箱，节省 30-50% 内存
3. **@ResourceType 注解 + LoadPriority** 实现数据驱动加载
4. **5 级 LoadPriority**：HIGHEST/HIGH/NORMAL/LOW/LOWEST 解决依赖顺序
5. **111 个 Excel 配表** + 30+ BinOutput + 2360 MainQuest + 几千 Lua
6. **loadAll 30 步流水线**：基础 → Excel → 场景 → 任务 → 高级
7. **并行加载（parallel + unordered）**：同优先级文件并行，跨级别串行
8. **错误并发收集**：避免 logger 在并行流死锁
9. **3 种文件格式 (json/tsj/tsv)**：优先 json
10. **onLoad() 反序列化回调**：缓存关联数据 / 数据校验
11. **getMapByResourceDef 反射定位**：Class → 对应 GameData Map 字段
12. **DataLoader vs ResourceLoader**：手动加载 vs 自动扫描，两套并存
13. **3 级 fallback**：`/data/` → `/data-user/` → jar 内 `defaults/data/`
14. **GameDepot 后处理**：原始 Excel → 运行时可用的"词条池"
15. **启动 15-30 秒**：Lua 解析是大头
16. **内存 ~1 GB**：主要是 SubQuestData (20893 条) + Lua AST + TextMap
17. **数据驱动哲学**：加新内容零代码改动——5 年来每版本几十个新内容的根本

---

## 16. 一句话总结

> **GameData = grasscutter 中最大的全局状态——160+ 静态 Map 字段、111 个 Excel 配表、4 级 LoadPriority 优先级、并行加载 + 反射注册、30 步启动流水线、@ResourceType 注解驱动、Int2ObjectMap (fastutil) 节省 30-50% 内存。所有 Manager 的"数据底座"，每次 `GameData.getXxxMap().get(id)` 都是 O(1) 哈希查找。**
> 
> **设计哲学: 数据驱动到极致——Excel 配表 + 注解描述 + 反射加载 + 优先级依赖 → 加新角色/怪物/活动 0 代码改动。这是 MMO 服务器的标准范式, grasscutter 把它做到了 110+ 配表共享同一套加载机制.**

---

**前置笔记**：
- notes/02 任务系统 - SubQuestData 来自 GameData
- notes/24 Avatar 升级 - AvatarData / AvatarCurveData
- notes/32 怪物 - MonsterData / MonsterCurveData / MonsterAffixData
- notes/38 Inventory - ItemData
- notes/39 Drop 系统 - DropTableExcelConfigData
- notes/27 架构模式 - 第 13+ 次"注解+反射"
- notes/30 持久化 - 加载完 GameData 才 initialize DB

**关联文件**：
- `GameData.java`(361) - 160+ 静态 Map
- `ResourceLoader.java`(1031) - 主加载器
- `DataLoader.java`(138) - 通用文件加载
- `ResourceType.java`(38) - 注解定义
- `GameResource.java`(12) - 基类
- `GameDepot.java`(70) - 后处理
- `data/excels/` × 111 文件 - Excel 数据类
- `data/binout/` × 30+ - BinOutput 配置类

**研究的源代码**: 1400+ 行加载体系核心 + 111 个 @ResourceType 文件 + 160+ Map 字段统计。
