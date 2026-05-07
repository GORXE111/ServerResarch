# 02 · 任务系统设计

## TL;DR

> **数据驱动的状态机集合，用事件总线 + 倒排索引调度，配合 Lua 处理场景表演。**

整个任务引擎核心代码 ~2000 行 + 80 个 handler，但能驱动 5000+ 个剧情任务、上万小时内容。复杂度全在配表里，不在引擎里。

---

## 数据骨架（两级嵌套）

```
MainQuest (剧情线 - 顶层容器，自身只是元数据)
└── SubQuest[] (每一步都是独立状态机)
     ├─ 三组条件 (cond + LogicType组合):
     │   ├─ acceptCond[]   什么时候接   ← 用倒排索引扫描
     │   ├─ finishCond[]   什么时候完成 ← 订阅式
     │   └─ failCond[]     什么时候失败 ← 订阅式
     ├─ 三组动作 (exec[]):
     │   ├─ beginExec[]    接取时执行
     │   ├─ finishExec[]   完成时执行
     │   └─ failExec[]     失败时执行
     ├─ 资源/表演:
     │   ├─ guide / trialAvatarList / exclusiveNpcList
     │   ├─ gainItems[]
     │   └─ luaPath  ← 脚本钩子
     └─ 文本: descTextMapHash, stepDescTextMapHash
```

代码定义：
- `Grasscutter-Quests/src/main/java/emu/grasscutter/data/common/quest/MainQuestData.java:11`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/data/common/quest/SubQuestData.java:14`

---

## 状态机（小，但分两层）

| 实体 | 状态 |
|---|---|
| **SubQuest** (`QuestState`) | `UNSTARTED → UNFINISHED → FINISHED / FAILED` |
| **MainQuest** (`ParentQuestState`) | `NONE → FINISHED / FAILED / CANCELED` |

QuestState 枚举值（影响 acceptCond 中的 `[questId, state]` 解读）：
```
0 = NONE         3 = FINISHED
1 = UNSTARTED    4 = FAILED
2 = UNFINISHED   5 = CANCELED   6 = REWARDED
```

每个 SubQuest 是独立小状态机；MainQuest 只是它们的容器，自己几乎没逻辑——**完成只发生在某个 SubQuest 标记 `finishParent=true` 时**（`GameQuest.java:193`）。

---

## LogicType（条件组合）

`finishCondComb` / `acceptCondComb` / `failCondComb` 用枚举而非表达式树：

```java
LOGIC_NONE              不组合
LOGIC_AND               全部满足
LOGIC_OR                任一满足
LOGIC_NOT               全部不满足
LOGIC_A_AND_ETCOR       A 必须 + 后面任一
LOGIC_A_AND_B_AND_ETCOR A、B 必须 + 后面任一
LOGIC_A_OR_ETCAND       A 或 后面全部
LOGIC_A_OR_B_OR_ETCAND  A 或 B 或 后面全部
LOGIC_A_AND_B_OR_ETCAND (A 且 B) 或 后面全部
```

源码：`game/quest/enums/LogicType.java:10`

**为什么不用表达式树**：策划配表更简单（选枚举），引擎更快（无解析），代价是复杂逻辑要拆 SubQuest——反而**强迫策划线性化复杂度**，可读性更好。

---

## 触发器系统（QuestCond / QuestContent / QuestExec）

三套独立的枚举 + handler：

| 枚举 | 用途 | 使用位置 | 个数 |
|---|---|---|---|
| `QuestCond` | 接取条件 | `acceptCond[].type` | ~30 |
| `QuestContent` | 完成/失败条件 | `finishCond[].type` / `failCond[].type` | ~50 |
| `QuestExec` | 副作用 | `beginExec[]` / `finishExec[]` / `failExec[]` | ~50 |

每种类型对应一个 handler 类：
- `game/quest/conditions/Condition*.java`
- `game/quest/content/Content*.java`
- `game/quest/exec/Exec*.java`

### 注解驱动的 handler 注册

```java
@QuestValueContent(QUEST_CONTENT_KILL_MONSTER)
public class ContentKillMonster extends BaseContent {
    // 啥都不写！全部继承默认行为
}
```

启动时通过反射扫描 `emu.grasscutter.game.quest.{conditions,content,exec}` 包，按注解自动注册到 dispatch 表（`QuestSystem.java:41`）。**新增触发器只需要：枚举加一项 + 写 handler + 标注解**，不动核心代码。

`BaseContent.java:9` 提供默认实现：
- `isEvent`：比对 type 和首参数
- `updateProgress`：进度 +1
- `checkProgress`：currentProgress >= count

→ 这是为什么 `ContentKillMonster` 可以是空类。

---

## 接受 vs 完成：两种调度策略（精妙之处）

### 问题
原神有 5000+ 个 SubQuest。玩家每秒做无数个动作。如果每次都要遍历所有任务问"你关心吗？"——爆炸。

### 解法：QuestCond 用倒排索引，QuestContent 用线性遍历

```java
// QuestCond 路径 (QuestManager.java:367)
public void triggerEvent(QuestCond condType, ...) {
    val potentialQuests = GameData.getQuestDataByConditions(...);  // 倒排索引查
    potentialQuests.forEach(qd -> {
        if (wasSubQuestStarted(qd)) return;
        boolean shouldAccept = LogicType.calculate(qd.getAcceptCondComb(), accept);
        if (shouldAccept) addQuest(qd);
    });
}

// QuestContent 路径 (QuestManager.java:406)
public void triggerEvent(QuestContent condType, ...) {
    List<GameMainQuest> active = ...filter(未完成).toList();
    for (GameMainQuest mq : active) {
        mq.tryFailSubQuests(...);
        mq.tryFinishSubQuests(...);
    }
}
```

| | 接受用倒排 | 完成用遍历 |
|---|---|---|
| **候选集大小** | 5000+ 未启动任务 | 几十个活跃任务 |
| **每次代价** | O(1) 查表 → O(k) 验证 | O(active) 遍历 |
| **合理性** | 大集合必须靠索引 | 小集合直接扫更简单 |

**这是经典的"按数据分布定算法"**——给完成条件也建倒排索引会让索引维护成本超过遍历。

---

## 倒排索引（性能秘密）

```java
// 构建期 (ResourceLoader.java:629)
private static void addToCache(SubQuestData questData) {
    questData.getAcceptCond().forEach(cond -> {
        val key = cond.asKey();              // 类型 + 首参数 + 字符串参数
        cacheMap.computeIfAbsent(key, e -> new ArrayList<>())
                .add(questData);             // 哪些任务订阅了这个 key
    });
}

// 查询期 (GameData.java:350)
public static List<SubQuestData> getQuestDataByConditions(...) {
    return beginCondQuestMap.get(SubQuestData.questConditionKey(...));
}
```

### Key 的构造（`SubQuestData.java:52`）

```java
public static String questConditionKey(Enum<?> type, int firstParam, String paramsStr) {
    return type.name() + firstParam + (paramsStr != null ? paramsStr : "");
}
```

**注意**：key 只用 `type + 首参数`。例如 100102 要 `state==3`、100103 要 `state==4`，**两者共享同一个 key** `"QUEST_COND_STATE_EQUAL100101"` —— 第二个参数（具体状态值）由 handler 自己验证。

→ **宽匹配 + 二次精确验证**：索引体积小，但表达力不丢。

---

## 事件总线

业务系统不直接调任务代码，只投递事件：

```java
// QuestManager.java:351 -- 4 个公开接口
queueEvent(QuestCond condType, int... params)
queueEvent(QuestContent condType, int... params)
queueEvent(QuestCond condType, String paramStr, int... params)
queueEvent(QuestContent condType, String paramStr, int... params)
```

实现：**异步线程池**（`QuestManager.java:37` 4 线程，1000 队列）：

```java
public static final ExecutorService eventExecutor = new ThreadPoolExecutor(4, 4, ...);
```

举几个真实的事件投递点：

| 业务来源 | 投的事件 |
|---|---|
| `HandlerNpcTalkReq` | `QUEST_CONTENT_COMPLETE_TALK` |
| 击杀怪物（实体系统）| `QUEST_CONTENT_KILL_MONSTER` |
| 改任务变量（`GameMainQuest.java:143`）| `QUEST_COND_QUEST_VAR_EQUAL` 等 6 个 |
| 游戏时间变化（`QuestManager.java:145`）| `QUEST_CONTENT_GAME_TIME_TICK` |
| 玩家进入区域 | `QUEST_CONTENT_PLAYER_ENTER_REGION` |

**好处**：背包系统不需要知道任务系统的存在；任务系统也不需要知道背包系统。

---

## 任务变量（quest var）= 任务内的状态机扩展

每个 MainQuest 自带：
- `questVars[5]`（int 数组，默认 0）
- `timeVar[10]`

通过 `ExecSetQuestVar` / `ExecIncQuestVar` 改写。**关键**：变量变化会 fire 事件，让其他 SubQuest 能订阅：

```java
// GameMainQuest.java:141
private void triggerQuestVarAction(int index, int value) {
    questManager.queueEvent(QuestCond.QUEST_COND_QUEST_VAR_EQUAL, index, value);
    questManager.queueEvent(QuestContent.QUEST_CONTENT_QUEST_VAR_EQUAL, index, value);
    // ... > < 各 fire 一次
}
```

**这是分支剧情的实现机制**——
- "你支持谁？" → ExecSetQuestVar(0, 1) 或 (0, 2)
- 后续 SubQuest 订阅 `QUEST_COND_QUEST_VAR_EQUAL[0, 1]` 或 `[0, 2]`
- 玩家选择不同分支 → 接取不同 SubQuest

剧情分支不需要硬编码 if/else。

---

## Trigger（任务系统 ↔ Lua 脚本的桥）

某些 finishCond 类型是 `QUEST_CONTENT_TRIGGER_FIRE`——表示**完成判定外包给场景 Lua**：

```java
// GameQuest.java:83 启动时把 trigger 注册到场景
val triggerCond = questData.getFinishCond().stream()
    .filter(p -> p.getType() == QUEST_CONTENT_TRIGGER_FIRE).toList();
for (val cond : triggerCond) {
    TriggerExcelConfigData newTrigger = ...;
    triggerData.put(newTrigger.getTriggerName(), newTrigger);
    // Lua 那边触发后，走 QuestContent.QUEST_CONTENT_TRIGGER_FIRE 事件回任务系统
}
```

→ **场景级别的复杂表演**（机关谜题、连锁触发、Boss 战阶段）由 Lua 写，任务系统只接它们 fire 出来的"完成信号"。

---

## 给开发者的提炼

1. **二级状态机**：不要做单层任务列表，MainQuest/SubQuest 有清晰的边界
2. **触发器三件套**：cond + comb + exec，全部数据驱动
3. **事件总线 + 倒排索引**：是规模化的关键
4. **注解驱动 handler 注册**：新增触发器零修改核心代码
5. **任务变量做分支**：不要硬编码 if/else
6. **文本 hash 化**：所有可见文字用 64 位 hash 走 TextMap 查询，多语言切换零成本
