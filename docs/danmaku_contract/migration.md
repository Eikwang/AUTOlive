# 迁移指南：DanmakuMessage（3 类）→ 统一消息契约 v1（九类）

> 适用对象：AUTOlive 及其他已消费旧版 `DanmakuMessage` 的代码。
> 兼容承诺：旧模型 `danmaku_listener.bus.message.DanmakuMessage` **保留且行为不变**；
> 新旧双向适配器在 `danmaku_listener.contract.legacy`。

## 字段映射

| 旧字段 | 新位置 | 说明 |
|---|---|---|
| platform | envelope.platform | 值域扩展（新增 huya/kuaishou/wechat_channels） |
| room_id | envelope.room_id | 不变 |
| user_name | payload.user_name | DANMU/GIFT/SOCIAL 等载荷内 |
| content | payload.content | DANMU/SUPER_CHAT |
| timestamp | envelope.timestamp | 语义不变（秒级） |
| message_type="normal" | type=DANMU（category=business） | — |
| message_type="gift" | type=GIFT + payload（GiftPayload） | gift_info 字段展开 |
| message_type="system" | SYSTEM_STATUS 子类型 | 旧 system 语义模糊：断线类 → GAP(approx=true)；其他按事件细分 |
| — | envelope.seq | **新增**：房间内单调序号（旧模型无） |
| — | envelope.msg_id | **新增**：平台原生消息 ID（去重键） |
| — | envelope.engine | **新增**：活跃引擎标识 |

## 迁移步骤

1. 消费侧改订阅统一消息流（WS 线格式见 [schema.md](schema.md)），
   按 `category` 分流：business → 业务处理；system → 状态处理。
2. 忽略未知 `type` 与未知字段（additive-only 契约，向前兼容）。
3. GIFT 消费代码从 `msg.gift_info` 改为 `msg.payload`（GiftPayload 字段见 Schema）。
4. 过渡期可用 `contract.legacy.unified_to_legacy()` 桥接旧处理函数；
   桥接仅覆盖 DANMU/GIFT，SYSTEM 类返回 None。
5. 弃用计划：旧模型保留至阶段 7 验收后另公告（弃用警告先行）。

## 配置迁移

| 旧配置（.env） | 新配置（TOML，四层优先级） | 说明 |
|---|---|---|
| LOG_LEVEL | [log] level | 环境变量覆盖仍支持 |
| PROXY_PORT | [proxy] port | — |
| MAX_ROOMS | [limits] max_rooms | — |
| COOKIE_DIR | [credentials] cookie_dir | — |
| DEDUP_WINDOW_SIZE | [bus] dedup_capacity + dedup_window_seconds | 拆分为有界结构双参数 |
| RECONNECT_* | [reconnect] fast_retries / base_delay / slow_cap_seconds | 新增慢速重试段 |

完整配置键与默认值见 `danmaku_listener/config/settings.py`（单源）与运维手册。
