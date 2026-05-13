# 表演系统与对话配置深度剖析

> 第 42 篇：Cutscene / Plot / Storyboard / Talk —— 这是 grasscutter 中**最反直觉**的子系统：服务器极简，剧情演出**完全在客户端**。

---

## 0. 为什么这一篇重要

notes/04 (Talk and Lua Bridge) + notes/08 (Talk 11019 分支例) + notes/12 (NPC Dialog 翻译) 触及了对话系统的**数据层**，但**演出 / 表演 / Cutscene** 这一块从未专门解剖。

关键问题：
1. 玩家按 F 跟 NPC 聊天，**服务器实际做了什么**？
2. 黑屏 → cutscene 演出谁控制？服务器还是客户端？
3. 主线剧情的"3 选项分支"在哪里实现？
4. NPC 的台词在哪存？谁推送？
5. 玩家点击对话选项后，剧情怎么继续？

**剧透**：服务器对剧情演出**几乎不干预** —— 只在关键节点记录"已完成"。这是混合权威设计走到极致的样本。

---

## 1. 整体架构：3 层职责分离

```
┌──────────────────────────────────────────────────────┐
│          客户端 (Unity, 完全控制)                       │
│  - TalkExcelConfigData 配表 (台词/选项/演员/动作)        │
│  - DialogExcelConfigData 配表 (对话节点树)              │
│  - Cutscene 资源 (动画/镜头/音效)                       │
│  - 选项 UI 渲染                                         │
│  - 镜头切换 / 角色走位 / 台词浮现                        │
└──────────────────────────────────────────────────────┘
                    ↓ ↑ (极少通信)
┌──────────────────────────────────────────────────────┐
│          服务器 (Java, 极简记录)                        │
│  - 记录 "talkId 已完成"                                 │
│  - 触发 3 个 Quest 事件                                 │
│  - 下发 cutsceneId / talkId / dialogId (仅 ID)         │
│  - 不解析台词 / 不维护对话树 / 不处理选项               │
└──────────────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────────────┐
│        Lua 桥 (剧情驱动)                                │
│  - 服务器 Lua spawn 触发 talk                          │
│  - Lua 收 talk 完成回调推进剧情                          │
└──────────────────────────────────────────────────────┘
```

→ **服务器是"记账员"，客户端是"导演"，Lua 是"中介"**。

---

## 2. HandlerNpcTalkReq：服务器极简 50 行

`HandlerNpcTalkReq.java` —— 整个对话系统的**服务器入口**：

```java
public class HandlerNpcTalkReq extends TypedPacketHandler<NpcTalkReq> {
    @Override
    public void handle(GameSession session, byte[] header, NpcTalkReq req) {
        int talkId = req.getTalkId();
        int mainQuestId = GameData.getQuestTalkMap().getOrDefault(talkId, talkId / 100);
        // ★ talkId 除以 100 = mainQuestId (talkId 命名约定)
        
        val mainQuestData = GameData.getMainQuestDataMap().get(mainQuestId);
        val questManager = session.getPlayer().getQuestManager();
        
        if (mainQuestData != null) {
            // 找到对应 quest 的 talk 配置 (可能没有, 就用空的)
            var talkForQuest = new TalkData(talkId, "");
            if (mainQuestData.getTalks() != null) {
                val talks = mainQuestData.getTalks().stream()
                    .filter(p -> p.getId() == talkId).toList();
                if (talks.size() > 0) talkForQuest = talks.get(0);
            }
            
            // ★ 记录"已完成此 talk"
            val mainQuest = questManager.getMainQuestByTalkId(talkId);
            if (mainQuest != null) {
                mainQuest.getTalks().put(talkId, talkForQuest);
            }
        }
        
        // ★ 触发 3 个事件 (Quest 系统监听)
        questManager.queueEvent(QuestContent.QUEST_CONTENT_COMPLETE_ANY_TALK, talkId, 0, 0);
        questManager.queueEvent(QuestContent.QUEST_CONTENT_COMPLETE_TALK, talkId, 0);
        questManager.queueEvent(QuestCond.QUEST_COND_COMPLETE_TALK, talkId, 0);
        
        // 回包
        session.send(new PacketNpcTalkRsp(req.getNpcEntityId(), req.getTalkId(), req.getEntityId()));
    }
}
```

### 2.1 服务器只做 4 件事

1. **解析 talkId → mainQuestId**（按命名约定）
2. **记录 talk 已完成**（写入 `mainQuest.talks` map）
3. **触发 3 个事件**（让 Quest 系统检测进度）
4. **回 ACK 包**（让客户端知道服务器收到了）

**它不做**：
- ✗ 解析台词内容
- ✗ 验证玩家是否到 NPC 旁边
- ✗ 校验对话选项
- ✗ 推进剧情
- ✗ 触发 cutscene

→ **服务器完全信任客户端**："你说聊完了就聊完了"。

### 2.2 talkId 命名约定

```
talkId = mainQuestId × 100 + sequence
   ↑ 这就是 talkId / 100 = mainQuestId
   
例: mainQuest 30220 → talkId 3022001, 3022002, 3022003...
```

**fallback 映射**：`GameData.getQuestTalkMap()` 处理不符合规约的（如活动 talk）。

### 2.3 触发的 3 个事件

```java
QUEST_CONTENT_COMPLETE_ANY_TALK    // ★ "完成任意一个 talk (列表中)"
QUEST_CONTENT_COMPLETE_TALK         // ★ "完成特定 talk"
QUEST_COND_COMPLETE_TALK            // ★ "如果完成了某 talk 则解锁..."
```

→ Quest 系统通过这 3 个事件检测剧情进度。**Inventory 触发 4 事件 / 怪物死触发 7 事件**——talk 也是同级别复杂的事件源。

---

## 3. MainQuestData.TalkData：超简 2 字段

```java
@Data @Entity
public static class TalkData {
    private int id;            // talkId
    private String heroTalk;   // 主角的回应文本
    
    public TalkData() {}
    public TalkData(int id, String heroTalk) {
        this.id = id;
        this.heroTalk = heroTalk;
    }
}
```

**服务器对 Talk 的认知就这 2 个字段**：
- `id` —— talkId
- `heroTalk` —— 主角说的话（一段 String）

→ **NPC 的台词在哪？** 不在这。在客户端的 `TalkExcelConfigData.json`（mihoyo 配表）。
→ **对话选项在哪？** 不在这。在客户端配表。
→ **演员动作 / 镜头 / 音乐在哪？** 不在这。在客户端 Cutscene 资源。

→ 服务器只记录"**主角说了啥**"——为了后续如果剧本需要回显（"你之前说过 XXX"）能取回。

---

## 4. PacketCutsceneBeginNotify：12 行下发"剧情 ID"

```java
public class PacketCutsceneBeginNotify extends BaseTypedPacket<CutSceneBeginNotify> {
    public PacketCutsceneBeginNotify(int cutsceneId) {
        super(new CutSceneBeginNotify());
        proto.setCutsceneId(cutsceneId);
    }
}
```

**整个 Cutscene 通信**只下发 1 个 int！

```
[服务器] → cutsceneId = 401012001
[客户端] 加载 Cutscene_401012001 资源 (动画/台词/镜头)
[客户端] 开始播放
[客户端] 播放完毕 → 发 (一般是 PostEnterSceneReq 或 QuestContent.FINISH_PLOT)
[服务器] 标记"剧情完成"
```

→ 服务器**完全不知道 cutscene 内容**——它只知道 "请客户端播放编号 401012001 的影片"。

### 4.1 CutsceneCommand：GM 触发剧情

```java
@Command(label = "cutscene", aliases = {"c"})
public final class CutsceneCommand implements CommandHandler {
    @Override
    public void execute(Player sender, Player targetPlayer, List<String> args) {
        val cutSceneId = Integer.parseInt(args.get(0));
        targetPlayer.sendPacket(new PacketCutsceneBeginNotify(cutSceneId));
    }
}
```

→ `/cutscene 401012001` 命令即可让任何玩家**立即播放任意剧情**。
→ 客户端**没有"权限校验"** —— 你 GM 发什么它就放什么。

→ 这就是为什么 grasscutter 私服里**任意剧情可以反复看** —— 服务器随便发，客户端无脑放。

---

## 5. ContentFinishPlot：客户端权威的"剧情完成"

```java
/**
 * This is triggered by the client
 */
@QuestValueContent(QUEST_CONTENT_FINISH_PLOT)
public class ContentFinishPlot extends BaseContent {
    // params[0] plot ID
}
```

**注释说明**：`This is triggered by the client`。

→ 这是 grasscutter 中**第二个明确的"客户端权威"事件**（第一个是 talk）：
- 客户端播完 cutscene
- 客户端发 `AddQuestContentProgressReq { contentType=QUEST_CONTENT_FINISH_PLOT, param0=plotId }`
- 服务器记录"plot 完成"
- 触发任务进度

→ 服务器**不知道何时 cutscene 结束** —— 完全靠客户端通知。

### 5.1 信任问题

```
[攻击者] 修改客户端, 不放剧情直接发 FINISH_PLOT
[服务器] 信任并触发任务进度
[结果] 跳过所有剧情 → 任务进度照常推进
```

→ **私服跳剧情漏洞**：grasscutter 不防这个，但米哈游正服肯定有时间校验（"cutscene 至少播 30 秒才算完成"）。

---

## 6. ContentCompleteTalk vs ContentCompleteAnyTalk

### 6.1 ContentCompleteTalk（单 talk）

```java
@QuestValueContent(QUEST_CONTENT_COMPLETE_TALK)
public class ContentCompleteTalk extends BaseContent {
    @Override
    public int initialCheck(GameQuest quest, SubQuestData questData, QuestContentCondition condition) {
        val talkId = condition.getParam()[0];
        val checkMainQuest = quest.getOwner().getQuestManager().getMainQuestByTalkId(talkId);
        if (checkMainQuest == null || checkMainQuest.getParentQuestId() != questData.getMainId()) {
            return 0;
        }
        val talkData = checkMainQuest.getTalks().get(talkId);
        return talkData != null ? 1 : 0;
    }
}
```

→ 检查"`talkId` 是否在 `mainQuest.talks` map 里"——简单的存在性判断。

### 6.2 ContentCompleteAnyTalk（任一 talk）

```java
@QuestValueContent(QUEST_CONTENT_COMPLETE_ANY_TALK)
public class ContentCompleteAnyTalk extends BaseContent {
    @Override
    public int initialCheck(...) {
        val conditionTalk = Arrays.stream(condition.getParamString().split(","))
            .mapToInt(Integer::parseInt).toArray();
        // ★ 检查任意一个匹配
        return Arrays.stream(conditionTalk).anyMatch(talkId -> {
            val checkMainQuest = ...;
            val talkData = checkMainQuest.getTalks().get(talkId);
            return talkData != null;
        }) ? 1 : 0;
    }
}
```

→ 支持 `"3022001,3022002,3022003"` 字符串——任一完成即算。

### 6.3 剧情分支的关键

这就是 notes/09 "Talk 11019 分支选项实例"的底层支持：
```
[主线分支选择]
  → 选 A → 完成 talkId=1101901
  → 选 B → 完成 talkId=1101902
  → 选 C → 完成 talkId=1101903
  
[下一步任务的 finishCond]
  type: QUEST_CONTENT_COMPLETE_ANY_TALK
  paramString: "1101901,1101902,1101903"
  ↑ 任一完成就推进
```

→ **"3 选项汇合"** 的实现：3 个 talkId 用 `ContentCompleteAnyTalk` 监听，任一触发就推进。
→ 这是**简单又有效**的剧情分支设计。

---

## 7. ConditionCompleteTalk（条件版）

```java
@QuestValueCond(QUEST_COND_COMPLETE_TALK)
public class ConditionCompleteTalk extends BaseCondition {
    @Override
    public boolean execute(Player owner, SubQuestData questData, QuestAcceptCondition condition, ...) {
        val requiredTalkId = condition.getParam()[0];
        val eventTalkId = params[0];
        
        if (requiredTalkId == eventTalkId) return true;
        
        // 兜底: 检查历史完成记录
        val checkMainQuest = owner.getQuestManager().getMainQuestByTalkId(requiredTalkId);
        if (checkMainQuest == null) return false;
        val talkData = checkMainQuest.getTalks().get(requiredTalkId);
        return talkData != null || checkMainQuest.getChildQuestById(requiredTalkId) != null;
    }
}
```

**双层判定**：
1. **快路径**：当前事件就是要求的 talkId → true
2. **慢路径**：查历史记录 `mainQuest.talks` 看是否已完成

→ "做完 talkId=3022001 才能接 X 任务" 这种**前置依赖**的实现。

---

## 8. 30+ ExecXxx：剧情触发器

`quest/exec/` 目录有 **30+ 个执行器**，是任务系统**主动改变世界**的工具。按类型分：

### 8.1 数值操作类

```java
ExecAddCurAvatarEnergy      // 给当前角色加能量
ExecAddQuestProgress         // 加任务进度
ExecIncQuestVar              // 任务变量 +1
ExecDecQuestVar              // 任务变量 -1
ExecIncQuestGlobalVar        // 全局任务变量 +1
ExecDecQuestGlobalVar
ExecIncDailyTaskVar
ExecDecDailyTaskVar
ExecInitTimeVar              // 初始化时间变量
ExecClearTimeVar             // 清空
```

### 8.2 物品操作类

```java
ExecDelPackItem              // 删背包物品
ExecDelPackItemBatch         // 批量删
ExecDelAllSpecificPackItem   // 删所有指定物品
```

→ 任务"上交 5 个琉璃袋"完成时，**Exec 系统帮玩家扣除物品**。

### 8.3 角色/队伍操作类

```java
ExecGrantTrialAvatar         // 给试用角色 (剧情专用)
ExecRemoveTrialAvatar        // 收回试用角色
ExecChangeAvatarElement      // 改角色元素 (旅行者切元素)
ExecChangeSkillDepot         // 改技能组
ExecActiveItemGiving         // 激活"提交物品"任务
```

### 8.4 场景操作类

```java
ExecAddSceneTag              // 加场景 tag
ExecDelSceneTag              // 删 tag
ExecChangeSceneLevelTag      // 改场景等级 tag
ExecModifyWeatherArea        // 改天气
ExecLockPoint                // 锁锚点（剧情期间不让传送）
```

### 8.5 Lua 联动类

```java
ExecNotifyGroupLua           // ★ 通知 Lua "任务进入新阶段"
ExecRefreshGroupMonster      // 刷新组怪
ExecRefreshGroupSuite        // 切换组 suite (notes/14)
ExecRefreshGroupSuiteRandom  // 随机刷
ExecRegisterDynamicGroup     // 注册动态组 (Blossom 等)
```

### 8.6 任务系统操作

```java
ExecRollbackParentQuest      // 回滚主任务 (savepoint)
ExecNotifyDailyTask          // 通知每日委托
```

### 8.7 这些 Exec 谁调用？

```
[Quest 完成] mainQuest.finish()
    ↓ 遍历 subQuest.finishExec[]
    ↓
[QuestExec 调度] questExec.exec(player)
    ↓ 按 @QuestValueExec 注解找对应 handler
    ↓
[Exec 类] e.g. ExecNotifyGroupLua.exec(...)
    ↓
[Lua 收到] EVENT_LUA_NOTIFY → 推进场景剧情
```

→ Exec 是**任务系统的"输出端"** —— Content/Cond 监听事件，Exec 主动改变世界。

---

## 9. 完整对话流程时序图

把所有组件串起来——**一次主线对话**的完整时序：

```
[阶段 1: 玩家触发]
玩家走到 NPC 旁边 → 看到对话气泡
点击 F → 客户端弹对话框
    ↓
[阶段 2: 客户端解析配表]
客户端读 TalkExcelConfigData[talkId=3022001]
   - npcId, performType (普通/剧情)
   - 关联 DialogExcelConfigData[dialogId=...]
   
客户端读 DialogExcelConfigData (递归对话节点树)
   - 节点 1: NPC 说 textHash_A
   - 节点 2: 选项 [a, b, c]
   - 节点 3a: 玩家选 a → 跳节点 100
   - 节点 100: NPC 回 textHash_B
   - 节点 200: talk 结束
   
[阶段 3: 客户端演出]
渲染对话框 + 浮现台词
玩家点选项 (a)
切换镜头, 播放音效, NPC 动作
循环直到 dialog 结束

[阶段 4: 服务器通知]
客户端 → NpcTalkReq { talkId: 3022001 }
    ↓
HandlerNpcTalkReq:
   1. 记录 mainQuest.talks.put(3022001, talkData)
   2. 触发 3 个事件:
      - QUEST_CONTENT_COMPLETE_ANY_TALK
      - QUEST_CONTENT_COMPLETE_TALK
      - QUEST_COND_COMPLETE_TALK
   3. 回 PacketNpcTalkRsp

[阶段 5: Quest 进度更新]
QuestManager.queueEvent 处理:
   遍历所有 active subQuest
   if subQuest.finishCond 包含 QUEST_CONTENT_COMPLETE_TALK
      and condition.param[0] == 3022001:
         subQuest progress = 1
         if all finishCond done:
            subQuest.finish()
            
[阶段 6: SubQuest 完成连锁]
subQuest.finish():
   遍历 finishExec:
      - ExecNotifyGroupLua(groupId=210101, varKey=1)
        ↓ 通知 Lua
   遍历 successExec:
      - ExecAddQuestProgress (推下一个 subQuest 到 ACCEPTED)
   遍历 rewardId:
      - addItem(reward) 给奖励
      
[阶段 7: Lua 收到 NOTIFY]
Scene script:
   on_lua_notify(context, varKey):
      if varKey == 1:
         spawn_next_group_monsters(context)  -- 召唤下波敌人
         show_dialog(...)                     -- 触发下一段对话
         play_cutscene_id(401012001)          -- 播放过场动画
            ↓
            服务器 → PacketCutsceneBeginNotify(401012001)
            ↓
            客户端加载 + 播放

[阶段 8: Cutscene 完成]
客户端 cutscene 播完
    ↓
AddQuestContentProgressReq { contentType=QUEST_CONTENT_FINISH_PLOT, param0=plotId }
    ↓
ContentFinishPlot 触发 → 下一阶段任务继续
```

→ **8 阶段, 4 次客户端 → 服务器, 1 次服务器 → 客户端, 完整剧情演出 1 分钟**。

---

## 10. 表演系统的客户端配表（推断）

虽然 grasscutter 不解析这些，但配表存在客户端二进制里：

### 10.1 TalkExcelConfigData

```yaml
# 推断结构 (mihoyo 真实配表)
talkId: 3022001
type: 1                  # 普通 / 剧情 / 系统
nextTalks: [3022002]     # 下一个 talk
npcId: 11005              # 哪个 NPC
performCfg: cfg_xxx       # 表演配置
showCondition: [...]      # 显示条件 (任务进度)
priority: 100              # 多个 talk 时的优先级
initDialog: 30220001      # 起始 dialog 节点
```

### 10.2 DialogExcelConfigData

```yaml
# 对话节点 (树结构)
dialogId: 30220001
nextDialogs: [30220002, 30220003]  # 多个 = 选项分支
talkRole: 1                          # 1=NPC, 2=玩家
talkContentHash: 1234567890           # textMap key
talkAssetPath: ...                    # 语音文件
talkAudioName: ...
talkShowType: 0
```

→ **递归形成对话树**：每个节点指向多个 next，形成 DAG。

### 10.3 CutsceneExcel / StoryboardExcel

```yaml
cutsceneId: 401012001
clipPath: Cinematic/Quest/Q3022/cs001.usm
duration: 30.5
endType: black_fade
```

→ **完全是客户端资源** —— `.usm` 是 mihoyo 用的视频格式。

---

## 11. 客户端权威的根本原因

为什么剧情演出**完全在客户端**？

### 11.1 性能考虑

```
[假设服务器算演出]
镜头每帧 1 个变换矩阵
30 fps × 1 cutscene = 几千个矩阵
× 几百玩家 = 几十万矩阵每秒
```

→ 服务器算演出**网络爆炸 + CPU 浪费**。

### 11.2 演出资源在客户端

```
.usm 视频 / .ani 动画 / .bnk 音频 全部打包在客户端
服务器没有这些资源, 没法"播放"
```

### 11.3 玩家体验

```
[模拟"服务器算"]
玩家网络抖一下 → 镜头卡顿 → 体验差

[当前"客户端算"]
本地播放 → 流畅
```

### 11.4 反作弊 trade-off

```
[反作弊代价]
玩家 mod 跳过剧情 (爽)
玩家伪造 FINISH_PLOT 直接领奖 (作弊)
```

→ grasscutter **接受这个代价** —— 反正私服。米哈游正服可能加时间校验。

---

## 12. 表演 vs 互动：4 种 talk 模式

观察 talk 系统支持的 4 种模式：

| 模式 | 触发 | 服务器 | 客户端 |
|---|---|---|---|
| **常规对话** | F 跟 NPC 聊 | 记录 talkId 完成 | 渲染台词 + 选项 |
| **剧情对话** | 任务推进 | 触发 talk 启动 | 播放表演 + 锁住玩家 |
| **过场剧情** | Cutscene | 下发 cutsceneId | 播放 .usm 视频 |
| **气泡对话** | 路过 NPC | 无 | 自动浮现一句话 |

→ 4 种模式**走同一套机制** (NpcTalkReq + PacketCutsceneBeginNotify)，区别只在客户端**配表 type** + **演出资源**。

---

## 13. Lua 与剧情的双向桥

参见 notes/08 Talk-Lua 桥：

```
[Lua → 任务推进]
Lua: notify_group_lua(varKey=1)
    ↓
ExecNotifyGroupLua 等价
    ↓
ContentLuaNotify 触发任务进度

[任务推进 → Lua]
SubQuest.finish()
    ↓
finishExec: ExecNotifyGroupLua(groupId, varKey)
    ↓
Lua: on_lua_notify(context, varKey)
    ↓
spawn_monsters / show_dialog / play_cutscene
```

→ **Lua 和 Quest 互为驱动**：任何一方完成都能触发另一方，形成"剧情状态机"。

---

## 14. 设计模式总结

### 14.1 客户端权威 + 服务器记账

```
客户端: 全权处理演出
服务器: 只在关键节点 (talk/plot 完成) 记录
```

→ 性能优 + 体验流畅 + 反作弊 trade-off

### 14.2 命名约定 talkId/100=mainQuestId

```
3022001 ÷ 100 = 30220 (mainQuestId)
```

→ 无需查表即可关联 talk 到 quest，简化代码。

### 14.3 ContentCompleteAnyTalk 字符串 paramString

```
"3022001,3022002,3022003" → "任一完成"
```

→ 用 paramString 表达列表 —— 简单但有效。

### 14.4 30+ ExecXxx 注解反射

```
@QuestValueExec(QUEST_EXEC_NOTIFY_GROUP_LUA)
```

→ 又一次"注解 + 反射 + 自动注册"模式（grasscutter 第 10+ 次）。

---

## 15. 关键收获

1. **服务器极简，客户端完全控制演出** —— 整个对话系统**服务端代码不到 200 行**
2. **HandlerNpcTalkReq 50 行**：记录 talkId + 触发 3 事件 + 回 ACK
3. **TalkData 2 字段**：id + heroTalk（主角的话）
4. **PacketCutsceneBeginNotify 12 行**：只下发 cutsceneId
5. **ContentFinishPlot 明确标注 "client triggered"** —— 完成剧情段也是客户端权威
6. **talkId/100 = mainQuestId 命名约定**
7. **ContentCompleteAnyTalk paramString "id1,id2,id3"** —— 支持多 talk 任一完成
8. **30+ ExecXxx 任务输出端**：数值 / 物品 / 角色 / 场景 / Lua 五类
9. **/cutscene 命令 GM 触发** —— 任意 cutsceneId 可播
10. **私服跳剧情漏洞**：客户端可伪造 FINISH_PLOT 跳过演出
11. **4 种 talk 模式**：常规 / 剧情 / 过场 / 气泡——共享同一套机制
12. **完整对话流程 8 阶段**：玩家触发 → 客户端解析 → 客户端演出 → 服务器记录 → Quest 进度 → SubQuest 完成 → Lua 通知 → Cutscene 播放
13. **Lua 与 Quest 双向驱动**：ExecNotifyGroupLua + EventType.EVENT_LUA_NOTIFY 形成回路
14. **客户端配表才是大头**：TalkExcel / DialogExcel / CutsceneExcel / StoryboardExcel **都在客户端**

---

## 16. 一句话总结

> **表演系统 = "客户端完全控制演出, 服务器只在关键节点记账"的极端混合权威设计 —— HandlerNpcTalkReq 50 行 + PacketCutsceneBeginNotify 12 行就完成了整个剧情系统的服务端。服务器只知道 "talkId 完成 / plotId 完成", 不知道 NPC 说了啥, 不知道剧情演了啥, 不知道玩家选了啥。**
> 
> **设计哲学: 演出资源在客户端 (动画/音效/视频) + 性能要求高 (每帧镜头矩阵) + 玩家体验要流畅 → 必然客户端权威; 反作弊 trade-off (玩家可跳剧情) 在私服可接受; 实际复杂度 100% 在客户端 TalkExcel/DialogExcel/CutsceneExcel 配表里, 服务端只是"配表 ID 的传递员"。**

---

**前置笔记**：
- notes/04 Talk 对话系统 - 客户端权威设计基础
- notes/08 Talk-Lua 桥 - 双向沟通
- notes/09 Talk 11019 对话分支实例 - 3 选项汇合
- notes/12 NPC + Dialog 翻译 - 真实台词浮现
- notes/13 Mermaid 流程图 + 剧情脚本重构
- notes/41 事件总线 - QUEST_CONTENT_COMPLETE_TALK 3 种事件

**关联文件**：
- `HandlerNpcTalkReq.java`(50) - 服务器入口
- `PacketCutsceneBeginNotify.java`(12) - 下发 cutsceneId
- `PacketNpcTalkRsp.java` - talk ACK
- `MainQuestData.TalkData` - 2 字段 talk 元数据
- `ContentCompleteTalk.java`(25) - 完成检查
- `ContentCompleteAnyTalk.java`(53) - 任一完成
- `ContentFinishPlot.java`(13) - 剧情段完成
- `ConditionCompleteTalk.java`(36) - 条件检查
- `CutsceneCommand.java`(29) - GM 触发
- `quest/exec/` - 30+ ExecXxx 任务输出端
- (客户端) TalkExcelConfigData / DialogExcelConfigData / CutsceneExcel - 演出配表

**研究的源代码**: 200+ 行服务端剧情系统 + 30+ ExecXxx 文件名扫描。
