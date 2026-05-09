# 23 · HomeWorld / 尘歌壶系统 · UGC 范式与玩家自定义场景

之前 22 篇笔记里所有 **Scene 都是服务器预定义**的（地图、副本、活动场景）。HomeWorld 是**玩家自定义场景**——这是**第一个真正的 UGC（User-Generated Content）系统**。

> 核心代码：`game/home/`（7 个文件，~498 行）+ 12 个 packet handler  
> 完全不同于 Multiplayer：MP 是"进别人的 World"，HomeWorld 是"进别人的家"——独立于 World 体系。

---

## 1. 整体架构

```
GameHome (per Player, persisted to MongoDB, 独立 entity)
  ├── ownerUid (unique index)
  ├── level / exp                                家园等级
  ├── enterHomeOption                            访问权限策略
  ├── unlockedHomeBgmList                        已解锁 BGM
  ├── furnitureMakeSlotItemList                  制作中的家具列表
  └── sceneMap: ConcurrentHashMap<sceneId, HomeSceneItem>
                                                  一个玩家可有多个尘歌壶
                                                  (不同地形主题: 草原/海岛/山地)

HomeSceneItem (一个尘歌壶场景)
  ├── sceneId                                    realmId = 2000 + currentRealmId
  ├── blockItems: Map<blockId, HomeBlockItem>    场景内分块
  ├── bornPos / bornRot / djinnPos               出生点 / 阿圆位置
  ├── homeBgmId                                  当前 BGM
  ├── mainHouse: HomeFurnitureItem               主屋
  └── tmpVersion

HomeBlockItem (一块自定义区域)
  ├── blockId
  ├── unlocked                                   是否已解锁该地块
  ├── deployFurnitureList: List<HomeFurnitureItem>   玩家放置的家具
  ├── persistentFurnitureList                    永久家具（如阿圆台子）
  ├── deployAnimalList: List<HomeAnimalItem>     放置的动物
  └── deployNPCList: List<HomeNPCItem>           放置的角色 NPC

HomeFurnitureItem
  ├── furnitureId                                家具 itemId
  ├── pos: Position                              3D 位置
  ├── rot: Position                              旋转
  └── comfort                                    舒适度贡献
```

→ **三层嵌套**：Home → Scene → Block → Furniture/Animal/NPC。每个 Block 是 100×100 米左右的"画布"，玩家拖放家具构造场景。

---

## 2. 完全独立于 World 系统

回顾 notes/18 Multiplayer：World 是**联机房间**容器。HomeWorld 是另一套：

| 维度 | World/MP | HomeWorld |
|---|---|---|
| 容器 | World instance（运行时）| GameHome entity（持久化）|
| 多人 | host + 3 guest 同 world | 玩家间独立访问 |
| 持久化 | 玩家退出销毁 | 永久保存（MongoDB）|
| Scene 来源 | 服务器配表（Sumeru/Mondstadt 等）| **玩家自定义** |
| 场景 ID 范围 | 3, 5, 7, 8 等普通场景 id | **2000+ 段** (`realmId = 2000 + currentRealmId`) |

→ **HomeWorld 用 sceneId 2000+ 段单独标识**——server 看到 sceneId ≥ 2000 就知道是某玩家的家园场景。

---

## 3. 进入家园流程（包含跨账号访问）

```java
// HandlerTryEnterHomeReq.java
public void handle(GameSession session, byte[] header, TryEnterHomeReq req) {
    val targetPlayer = session.getServer().getPlayerByUid(req.getTargetUid(), true);
    
    // 访问别人家
    if (req.getTargetUid() != session.getPlayer().getUid() && targetPlayer != null) {
        val targetHome = GameHome.getByUid(req.getTargetUid());
        
        // ★ 三档权限策略
        switch (FriendEnterHomeOption.values()[targetHome.getEnterHomeOption()]) {
            case FRIEND_ENTER_HOME_OPTION_NEED_CONFIRM -> {
                if (targetPlayer.isOnline()) break;   // 在线才能问
                session.send(new PacketTryEnterHomeRsp(RET_HOME_OWNER_OFFLINE, ...));
            }
            case FRIEND_ENTER_HOME_OPTION_REFUSE ->
                session.send(new PacketTryEnterHomeRsp(RET_HOME_HOME_REFUSE_GUEST_ENTER, ...));
            case FRIEND_ENTER_HOME_OPTION_DIRECT -> 
                session.send(new PacketTryEnterHomeRsp());
        }
        return;
    }
    
    // 进自己家
    final int realmId = 2000 + session.getPlayer().getCurrentRealmId();
    val home = session.getPlayer().getHome();
    
    home.getHomeSceneItem(realmId);   // 首次进入会创建默认布局
    home.save();
    
    session.getPlayer().getWorld().transferPlayerToScene(
        session.getPlayer(), realmId, TeleportType.WAYPOINT, null, null);
    
    if (result) session.send(new PacketTryEnterHomeRsp(req.getTargetUid()));
}
```

### 三档访问权限

```java
enum FriendEnterHomeOption {
    FRIEND_ENTER_HOME_OPTION_NEED_CONFIRM,   // 需要房主确认（房主在线时弹窗）
    FRIEND_ENTER_HOME_OPTION_REFUSE,          // 拒绝所有人
    FRIEND_ENTER_HOME_OPTION_DIRECT,          // 直接进入
}
```

→ **三档比 MP 简单**（MP 必须发邀请 + 房主同意）。HomeWorld 的"直接进入"模式让**离线玩家的家也能被参观**——这是社交价值。

### 进入是 Scene 切换（不是 World 切换）

注意代码：`transferPlayerToScene(player, realmId, ...)`。

→ 进入家园**只是切 scene，不切 World**。玩家自己仍在自己的 World，scene 切到 realmId（2000+ 段）。**这意味着家园的玩家不是 MP 状态**——单人在自己的 World 内访问家园。

---

## 4. UGC 范式：玩家在 Block 里拖放家具

### 玩家提交布局（HandlerHomeUpdateArrangementInfoReq）

客户端在编辑模式下拖拽家具，结束时发整个布局给服务器：

```java
// HomeSceneItem.update (上面已看)
public void update(HomeSceneArrangementInfo arrangementInfo) {
    for (var blockItem : arrangementInfo.getBlockArrangementInfoList()) {
        var block = this.blockItems.get(blockItem.getBlockId());
        if (block == null) continue;
        block.update(blockItem);
        this.blockItems.put(blockItem.getBlockId(), block);
    }
    
    this.bornPos = new Position(arrangementInfo.getBornPos());
    this.bornRot = new Position(arrangementInfo.getBornRot());
    this.djinnPos = new Position(arrangementInfo.getDjinnPos());
    this.homeBgmId = arrangementInfo.getBgmId();
    this.mainHouse = HomeFurnitureItem.parseFrom(arrangementInfo.getMainHouse());
}
```

→ **整个 scene 的布局一次性提交并替换**。每个家具的 itemId + Position + Rotation 都是客户端算好后上报。

### 服务器侧的逻辑

```java
// HomeBlockItem.update
public void update(HomeBlockArrangementInfo proto) {
    this.deployFurnitureList = proto.getDeployFurnitureList().stream()
        .map(HomeFurnitureItem::parseFrom).toList();
    this.deployAnimalList = proto.getDeployAnimalList().stream()
        .map(HomeAnimalItem::parseFrom).toList();
    this.deployNPCList = proto.getDeployNpcList().stream()
        .map(HomeNPCItem::parseFrom).toList();
}
```

→ **服务器只是保存提交的布局**。客户端是 3D 编辑器，服务器是存储/同步层。**典型的"客户端编辑 + 服务器存储"分工**。

---

## 5. 舒适度系统：家具加成

```java
// HomeBlockItem.calComfort
public int calComfort() {
    return this.deployFurnitureList.stream()
        .mapToInt(HomeFurnitureItem::getComfort)
        .sum();
}
```

每件家具 `HomeFurnitureItem.getComfort()` 贡献舒适度。所有 block 的舒适度累加 = 整个家园的舒适度。

**舒适度的玩法价值**：
- 解锁更多家具上限
- 影响洞天宝钱产出（家园专用货币）
- 触发"洞天百宝"奖励

→ 这是**用家具收藏激励玩家氪金/做活动**的设计。

---

## 6. 默认布局 + 首次访问

```java
// GameHome.getHomeSceneItem
public HomeSceneItem getHomeSceneItem(int sceneId) {
    return sceneMap.computeIfAbsent(sceneId, e -> {
        var defaultItem = GameData.getHomeworldDefaultSaveData().get(sceneId);
        if (defaultItem != null) {
            Grasscutter.getLogger().info("Set player {} home {} to initial setting", ownerUid, sceneId);
            return HomeSceneItem.parseFrom(defaultItem, sceneId);
        }
        return null;
    });
}
```

→ 玩家**首次进入**某 realmId 时，从配表 `HomeworldDefaultSaveData` 加载默认布局。这是为什么"新玩家拿到尘歌壶就有现成的房子"。

```java
// HomeSceneItem.parseFrom
public static HomeSceneItem parseFrom(HomeworldDefaultSaveData defaultItem, int sceneId) {
    return HomeSceneItem.of()
        .sceneId(sceneId)
        .blockItems(defaultItem.getHomeBlockLists().stream()
            .map(HomeBlockItem::parseFrom)
            .collect(Collectors.toMap(HomeBlockItem::getBlockId, y -> y)))
        .bornPos(defaultItem.getBornPos())
        ...
        .build();
}
```

→ **配表数据** → **运行时实例**。配表里的家具/动物/NPC 配置被复制成玩家私有数据。**之后玩家自己改不影响配表**——纯写时拷贝。

---

## 7. BGM 解锁系统（图鉴式）

```java
// GameHome.addUnlockedHomeBgm
public boolean addUnlockedHomeBgm(int homeBgmId) {
    if (!getUnlockedHomeBgmList().add(homeBgmId)) return false;
    
    var player = this.getPlayer();
    player.sendPacket(new PacketHomeNewUnlockedBgmIdListNotify(homeBgmId));
    player.sendPacket(new PacketHomeAllUnlockedBgmIdListNotify(player));
    save();
    return true;
}
```

→ BGM 用 `Set<Integer>` 管理，加入即解锁通知客户端。**和 notes/17 Codex 系统的"集合 + 通知"模式同构**。

```java
// 默认 BGM 自动加入
private Set<Integer> getDefaultUnlockedHomeBgmIds() {
    return GameData.getHomeWorldBgmDataMap().int2ObjectEntrySet().stream()
        .filter(e -> e.getValue().isDefaultUnlock())
        .map(Int2ObjectMap.Entry::getIntKey)
        .collect(Collectors.toUnmodifiableSet());
}
```

→ 配表的 `isDefaultUnlock` 字段决定哪些 BGM 默认解锁。**配表驱动**。

---

## 8. 12 个 Packet Handler 的职责

```
HandlerTryEnterHomeReq               进入家园（含权限检查）
HandlerHomeSceneInitFinishReq        客户端场景加载完成
HandlerHomeSceneJumpReq              在不同 realm 之间切换
HandlerGetPlayerHomeCompInfoReq      获取家园基础信息
HandlerHomeGetBasicInfoReq           获取等级/exp/解锁状态
HandlerHomeGetArrangementInfoReq     获取布局
HandlerHomeUpdateArrangementInfoReq  ★ 提交新布局（编辑模式结束）
HandlerHomeChangeEditModeReq         切换编辑模式
HandlerHomeEnterEditModeFinishReq    编辑结束
HandlerHomeChooseModuleReq           选择主屋模块
HandlerHomeChangeBgmReq              切换 BGM
HandlerSetFriendEnterHomeOptionReq   设置访问权限
```

→ 12 个 handler，但**80% 都是 "Get/Set" 简单读写**。真正的复杂逻辑只在 `HandlerHomeUpdateArrangementInfoReq`（提交布局）和 `HandlerTryEnterHomeReq`（权限）。

---

## 9. 与其他系统的连接点

### 9.1 与 Inventory（家具是物品）
- 家具本质是 `HomeFurnitureItem` (itemType=ITEM_FURNITURE)
- 抽家具/做家具产出 → Inventory.addItem
- 放置 = "使用"家具（从背包取出放进 Block）
- 拆除 = 退回背包

### 9.2 与 Quest（家园任务）
- 某些主线任务在尘歌壶 scene 进行
- realmId 2xxx 是合法的 SceneId 之一
- 任务的 SubQuest 可以触发 HOMEWORLD scene 进入

### 9.3 与 Multiplayer
- HomeWorld **不是 MP**——是单人 scene 切换
- 但客户端 UI 表现像"造访"
- 房主在线时走"NEED_CONFIRM"
- 房主离线时仍可访问（DIRECT 模式）——这是 HomeWorld 独有的**异步社交**

### 9.4 与 Codex（解锁记录）
- 家具种类多达上千个
- 每收到新家具 → Inventory.addItem → checkAddedItem → Codex unlocks furniture entry
- BGM 解锁单独管理（独立于 Codex）

---

## 10. 完整生命周期：玩家拿到尘歌壶到造访朋友

```
[玩家首次得到尘歌壶（主线任务给）]
   ↓
玩家点 UI "进入尘歌壶"
   ↓
HandlerTryEnterHomeReq(targetUid = self)
   ↓
realmId = 2000 + currentRealmId (e.g. 2001 = 罗浮洞)
   ↓
home.getHomeSceneItem(2001):
    sceneMap 不存在 → 从 HomeworldDefaultSaveData 加载默认布局
    创建 HomeSceneItem with default blockItems / bornPos
    save to DB
   ↓
World.transferPlayerToScene(player, 2001, WAYPOINT)
   ↓
[玩家在自己尘歌壶里，看到默认家具]
   ↓
玩家点 "编辑模式"
HandlerHomeChangeEditModeReq → 客户端进入编辑 UI
   ↓
玩家拖放家具/动物/NPC（纯客户端 3D 编辑）
   ↓
玩家点 "保存退出"
HandlerHomeUpdateArrangementInfoReq(arrangementInfo with all blocks)
   ↓
HomeSceneItem.update(arrangementInfo):
    for each block: block.update(...)
    更新 bornPos / homeBgmId / mainHouse
    save() to DB
   ↓
HandlerHomeEnterEditModeFinishReq → 客户端退出编辑模式
   ↓
[玩家继续在自己的家园游玩]

[玩家想造访朋友的家]
   ↓
朋友 UID 列表里点 "造访" → HandlerTryEnterHomeReq(targetUid = friend.uid)
   ↓
查 friend 的 GameHome.enterHomeOption:
    DIRECT → 立刻进入
    NEED_CONFIRM + 朋友在线 → 弹窗给朋友确认
    NEED_CONFIRM + 朋友离线 → RET_HOME_OWNER_OFFLINE
    REFUSE → RET_HOME_HOME_REFUSE_GUEST_ENTER
   ↓
玩家进入朋友的 realmId scene（仍在自己的 World）
   ↓
[玩家看到朋友的家具布局，可走动但不能编辑]
```

---

## 11. 关键设计经验

### 11.1 UGC = 客户端编辑 + 服务器存储
- **客户端做 3D 编辑器**：摆位、旋转、对齐都在客户端
- **服务器只存数据**：HomeBlockArrangementInfo proto 一次性提交
- **不需要服务器算碰撞 / 物理**：玩家自己的家不用担心物理穿模

### 11.2 sceneId 分段管理
- 普通场景：3, 5, 7, 8...
- 家园场景：2000+ 段
- 副本场景：副本 id
- **sceneId 一眼看出场景类型** —— 简化路由逻辑

### 11.3 默认布局 + 写时拷贝
- 配表存默认布局
- 玩家首次访问 → 复制成私有数据
- **配表更新不影响已有玩家**（之前的版本固化）

### 11.4 异步社交：离线访问
- HomeWorld 的关键差异：朋友离线也能造访（DIRECT 模式）
- MP 必须双方都在线
- **HomeWorld 是"半同步"社交**——更轻量、更易传播

### 11.5 三档权限的 UX 取舍
- DIRECT：完全开放，人气最高
- NEED_CONFIRM：精选访客
- REFUSE：完全封闭

→ 类似社交媒体的"公开/好友/私密"三档——简单清晰。

---

## 12. 反作弊点

```java
1. 进入权限检查
   - DIRECT 直接放行
   - NEED_CONFIRM + 离线 → 拒绝
   - REFUSE → 拒绝

2. 提交布局必须是 home owner
   (server 隐式校验：updateArrangementInfo 只对 player.getHome() 操作)

3. 家具 itemId 必须存在于 player 背包
   (家具放置时 inventory 检查)

4. blockId 必须 unlocked
   if (block == null) continue

5. realmId 范围限制 (>=2000)

6. 跨 realm 切换需检查权限
```

→ HomeWorld 反作弊压力低（玩家自己的家不影响别人经济），但仍有最基本的权限和数据校验。

---

## 13. 给做 UGC 系统开发者的提炼

1. **客户端做编辑器，服务器做存储**——3D 编辑算力大不要上服务器
2. **数据用 proto/embedded entity**——HomeSceneItem 嵌入 GameHome，一次保存
3. **写时拷贝默认布局**——配表更新不影响已有用户数据
4. **sceneId 分段管理**——不同类型场景用 id 段区分
5. **三档访问权限**够用，别过度设计
6. **异步社交（离线访问）是 UGC 价值核心**——别让"双方在线才能互动"
7. **UGC 创造的物品本身仍受经济系统约束**——家具走 Inventory + ActionReason
8. **舒适度类"派生数值"用累加**——每件家具贡献，简单清晰
9. **配置里的 isDefaultUnlock 字段**——决定首次解锁状态，玩家无需"领取"
10. **持久化用独立 entity**（GameHome.entity = "homes"）——不要塞进 Player

---

## 14. 数据规模感

* 家园等级：1..N
* sceneId 范围：2000+ (尘歌壶) ~ 2100+ (扩展地形)
* Block 数：每尘歌壶 ~10 个 block
* 每 block 家具上限：~100 件（按等级）
* 总家具种类：~1500 件（含活动限定）
* 玩家可拥有多个尘歌壶（不同地形）

代码规模：
- `GameHome.java`：126 行（核心实体）
- `HomeSceneItem.java`：96 行
- `HomeBlockItem.java`：86 行
- `HomeFurnitureItem.java`：81 行
- `HomeNPCItem.java`：39 行
- `HomeAnimalItem.java`：37 行
- `FurnitureMakeSlotItem.java`：33 行
- 总核心：**498 行 + 12 个简单 handler ≈ 800 行** = 整个 UGC 范式

---

## 15. HomeWorld 在 22 篇笔记的"系统全景图"中的位置

```
[流程型] Quest (剧情) → Talk (对话) → Codex (归档)
                          ↓
[战斗] Combat / Ability → Dungeon (副本) → Scene Script (场景)
                              ↓
[经济] Inventory + 8 ItemType + ActionReason → Reward → Mail
                              ↓
[社交]  Multiplayer (实时)  ←  HomeWorld (异步) ←  Friend
                              ↓
[运营]  Activity (限时) + BattlePass (长期) + Gacha (商业)
                              ↓
[UGC]   HomeWorld ← 玩家自定义场景的唯一系统
```

→ HomeWorld 是**从"消费内容"到"创造内容"的过渡点**。这个范式在很多游戏延伸出极大商业价值（如 Roblox 的 UGC 模式）。

---

## 参考代码位

- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/home/GameHome.java`（126 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/home/HomeSceneItem.java`（96 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/home/HomeBlockItem.java`（86 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/game/home/HomeFurnitureItem.java`（81 行）
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerTryEnterHomeReq.java`
- `Grasscutter-Quests/src/main/java/emu/grasscutter/server/packet/recv/HandlerHomeUpdateArrangementInfoReq.java`
- 配置：`HomeworldDefaultSaveData`（默认布局），`HomeWorldLevelData`，`HomeWorldBgmData`
