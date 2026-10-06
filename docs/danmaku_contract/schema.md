# 统一消息契约 v1 — 语义规范

> 单源：`danmaku_listener/contract/models.py`（pydantic）。
> 机读 Schema：[schema.json](schema.json)（由 `scripts/export_contract.py` 导出，
> CI 校验漂移）。上游类型对照：[mapping.md](mapping.md)。
> 旧模型迁移：[migration.md](migration.md)。

## 概览

九类消息 = 八类业务（对照 barrage-fly 统一协议）+ SYSTEM_STATUS（本系统扩展）。

| 类别 | 类型 | 说明 |
|---|---|---|
| business | DANMU | 普通弹幕（DANMU 为有意裁剪，对照 B 站上游 DANMU_MSG） |
| business | GIFT | 礼物 |
| business | SUPER_CHAT | 醒目留言 |
| business | ENTER_ROOM | 进入直播间 |
| business | LIKE | 点赞 |
| business | LIVE_STATUS_CHANGE | 直播状态变更（开播/下播，GAP 裁剪边界信号） |
| business | ROOM_STATS | 房间统计 |
| business | SOCIAL | 社交行为（关注/粉丝团/分享） |
| system | HEARTBEAT | 引擎存活心跳（10s/条，AUTOlive 3 周期失联判定） |
| system | ROOM_STATUS | 房间在线状态 |
| system | GAP | 消息缺口标记（必达） |
| system | NEEDS_LOGIN | 登录态需求（跨路线通用，含 interactive_login 交互载荷） |
| system | ENGINE_STATUS | 活跃引擎标识/引擎状态变更 |
| system | BACKPRESSURE | 背压丢弃计数（窗口级） |
| system | ROUTE_FAILED | 路线失效/降级告警 |
| system | RECOVERED | 慢速重试后恢复 |

## 信封字段（Envelope）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| contract_version | string | ✓ | 固定 "1.0.0"；additive-only 演进 |
| category | enum | ✓ | business / system——AUTOlive 按此分流 |
| type | string | ✓ | 上表类型值；必须与 payload.type 一致 |
| platform | string | ✓ | bilibili / douyu / huya / kuaishou / douyin / wechat_channels |
| room_id | string | ✓ | 平台原生房间 ID |
| seq | int ≥0 | ✓ | 房间内单调递增序号（顺序与缺口检测依据） |
| timestamp | int | ✓ | 平台消息时间戳（秒）；无平台时间戳时为引擎接收时刻（R3-4 口径，测量受平台时钟偏移影响，另有保守对照口径） |
| engine | string | ✓ | 活跃引擎标识，如 `protocol:bilibili` / `proxy:douyin` / `fallback:generic` |
| msg_id | string? | 平台提供时必填 | 跨路线语义去重键 |

## 投递语义（契约 v1）

1. **顺序**：单房间内按 seq 单调递增；跨房间无序。
2. **投递级别**：at-least-once + 去重窗口（时间淘汰 + 容量上限，溢出方向可配置）。
3. **背压分级**（Eng M）：GIFT/SUPER_CHAT/SOCIAL 队列优先；丢弃顺序默认
   LIKE → ENTER_ROOM → DANMU；任一窗口丢弃数 > 0 必发该房间的窗口 GAP。
4. **重连不重放**：断线窗口依赖 GAP 报知缺失段；AUTOlive 重启后去重窗口冷启动
   （接入指南详述）。
5. **GAP 语义**：断线/停机/丢弃窗口必达可见；按下播（LIVE_STATUS_CHANGE）裁剪，
   下播期间缺失不计 GAP；崩溃恢复的拆分边界为**尽力近似**（approx=true，
   live 状态持久化 + 平台状态 API 校准后仍不确定时置位）。
6. **契约演进**：v1 内 additive-only（禁删字段/禁改类型/新字段必须 optional）；
   消费者必须忽略未知字段与未知类型并计数上报（复用坏消息处置语义，
   阈值 → ROUTE_FAILED 由配置定义）。
