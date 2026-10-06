# 命名映射表（契约类型 ↔ 平台上游消息类型）

> `DANMU` 为**有意裁剪**（对照 B 站上游 `DANMU_MSG`），非笔误。
> 每平台适配器实现时在本表补齐实测上游类型名（以抓包为准，fixtures 交叉验证）。

| 契约类型 | B站 | 斗鱼 | 虎牙 | 快手 | 抖音 | 视频号（后台） |
|---|---|---|---|---|---|---|
| DANMU | DANMU_MSG | chatmsg | tig-speak / MessageNotice | WebPulledMessage(comment) | WebcastChatMessage | 后台弹幕流 |
| GIFT | SEND_TOP_GIFT / GIFT / COMBO_SEND | dgb | gg-broadcast / MessageGiftNotice | gift | WebcastGiftMessage | 后台礼物流 |
| SUPER_CHAT | SUPER_CHAT_MESSAGE | —（无原生） | —（无原生） | —（无原生） | WebcastRoomMessage(special) | — |
| ENTER_ROOM | ENTER / INTERACT_WORD | rss / bbed | udb-entry / MessageEnterRoomNotice | member | WebcastMemberMessage | 后台进房 |
| LIKE | LIKE_MSG | —（统计合并） | —（统计合并） | like | WebcastLikeMessage | 后台点赞 |
| LIVE_STATUS_CHANGE | LIVE / PREPARING / CLOSE | 平台状态接口 | 平台状态接口 | 平台状态接口 | WebcastControlMessage | 后台直播状态 |
| ROOM_STATS | ONLINE_RANK / STAT_MSG | 平台统计消息 | 平台统计消息 | viewerStat | WebcastRoomStatsMessage | 后台统计 |
| SOCIAL | USER_TOAST_MSG(follow) / GUARD_BUY | follow 融合于 chatmsg | MessageSocialNotice | social | WebcastSocialMessage | 后台关注 |

说明：
- `—` 表示平台无原生对应：适配器按契约语义映射到最近似类型或在平台特有字段
  （`docs/contract/schema.json` 的 platformExtensions）中标注不支持。
- 各列上游类型名以**实现期抓包实测**为准更新本表；参考项目
  （ordinaryroad-live-chat-client / blivedm / DouyinBarrageGrab）仅作结构对照。
- 平台特有字段（如抖音 Appid/EnterTipType、B 站徽章）放入对应载荷的 optional 字段，
  不新增消息类型（additive-only）。
- **抖音 GIFT.count 语义（2026-09-29）**：取 GiftMessage.totalCount 原样透传——
  **累计值**（连击期间持续增长），下游（AUTOlive 触发逻辑）勿按增量消费；
  groupCount/repeatCount/comboCount 不参与（连击合并 defer，见 TODOS）。
- **抖音粉丝团回退**：Web WS 路线 proto 的 FansclubMessage 字段语义未实测，
  无法可靠映射—— SOCIAL 暂不承载粉丝团事件（TODOS）。
