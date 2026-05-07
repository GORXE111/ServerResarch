# 01 · 原神服务端整体架构

## TL;DR

原神客户端**不是哑终端**。它是**混合权威架构**——客户端做即时反馈，服务器做事实仲裁。

| 谁负责 | 内容 |
|---|---|
| **服务器权威** | 任务进度、HP、能量、体力、伤害结算、背包、货币、抽卡、邮件 |
| **客户端权威** | 元素反应/Aura、面板属性（除 HP）、移动预测、特效/伤害数字预算 |

**判定标准**：会写入存档 → 服务器；只用于即时反馈 → 客户端。

> 来源：[KeqingMains TCL: Client and Server](https://library.keqingmains.com/combat-mechanics/damage/other/client-and-server)

---

## 通信协议

- **Protobuf over KCP**（基于 UDP 的可靠传输）
- 每个客户端动作对应一个 `*Req` packet，每个服务器响应对应一个 `*Rsp` 或 `*Notify`
- KCP 的选择是为了**低延迟可靠传输**（比 TCP 快，比 UDP 可靠）

参考 Grasscutter 实现：
- 入口：`server/packet/recv/Handler*Req.java`（共 228 个 Handler）
- 出口：`server/packet/send/Packet*.java`（共 388 种下行包）

---

## 服务器子系统总览

```
emu/grasscutter/game/
├── activity/      活动系统（海岛、风花节...）
├── battlepass/    战令系统
├── chat/          聊天
├── city/          城市等级、声望
├── combine/       合成、烹饪
├── drop/          掉落表
├── dungeons/      秘境、深渊
├── entity/        世界中所有实体（角色、怪物、Gadget）
├── expedition/    派遣
├── friends/       好友系统
├── gacha/         抽卡（核心商业逻辑，必须服务器权威）
├── home/          尘歌壶
├── inventory/     背包
├── mail/          邮件
├── quest/         任务系统 ← 本仓库重点
├── shop/          商店
├── tower/         深境螺旋
└── world/         世界/场景管理（视野、玩家联机）
```

每个子系统都是 **`BaseGameSystem` 的子类**，挂在 `GameServer` 上。系统之间**不直接调用**，通过事件总线（`QuestManager.queueEvent` / `BattlePass.triggerMission`）解耦。

---

## 客户端权威的"代价"——外挂泛滥

服务器对**移动**只做"反远距离瞬移"的粗粒度校验：两次坐标上报间隔太大就拒绝。所以：

- 飞天、加速、瞬移、穿墙等外挂**普遍存在**且能成功运行
- 真正的反作弊靠 **mhyprot2 内核驱动**（客户端侧）做内存保护、检测注入

**这是米哈游的取舍**：能联机但允许单机外挂；商业核心（货币/抽卡/进度）绝对不可破。

---

## 高延迟时的现象（最能证明谁是权威）

KQM 实测出来的高 ping 表现：

| 现象 | 说明 |
|---|---|
| Buff 正常生效，但**胡桃血量低于 50% 的被动失效** | Buff 在客户端，但触发条件依赖服务器同步的 HP |
| **能量不生成、不消耗**，恢复连接后批量补回 | 能量在服务器 |
| **伤害暂停结算**，玩家和敌人都"无敌" | 伤害是服务器仲裁 |
| **治疗失效**，恢复后无视阈值一次性应用 | HP 是服务器权威 |
| **体力完全不消耗** | 体力是服务器算 |
| 元素反应正常进行 | 反应是客户端算的 |

---

## 数据持久化

- **数据库**：MongoDB（通过 [Morphia](https://morphia.dev/) ORM）
- **关键实体**：`Player`、`GameMainQuest`（任务存档）、`Inventory`、`AvatarStorage`
- **存档粒度**：每次任务状态变化都触发 `save()`；玩家整体在 onTick 周期性 save

```java
// GameQuest.java
public void save() {
    getMainQuest().save();      // 委托给 MainQuest
}

// GameMainQuest.java
@Entity(value = "quests", useDiscriminator = false)  // Morphia 注解
public class GameMainQuest { ... }
```

---

## 给大型 MMO/在线游戏开发者的启示

1. **不要让客户端绝对哑终端**——延迟反馈会让游戏手感崩
2. **不要让客户端绝对权威**——经济/进度系统会被外挂拆穿
3. **划清边界的标准是"是否需要持久化"**——不是"是否复杂"
4. **预测 + 校验**模式：客户端做乐观更新，服务器返回最终状态做对账
5. **包数量 ≈ 业务复杂度**：原神 228 + 388 ≈ 600 种 packet 类型，匹配它的内容体量

---

## 参考代码位

- 服务器入口：`Grasscutter-Quests/src/main/java/emu/grasscutter/server/game/GameServer.java`
- Packet 处理基类：`Grasscutter-Quests/src/main/java/emu/grasscutter/net/packet/`
- 玩家会话：`Grasscutter-Quests/src/main/java/emu/grasscutter/server/game/GameSession.java`
