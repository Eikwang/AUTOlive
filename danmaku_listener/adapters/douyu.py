"""斗鱼平台适配器

解析斗鱼直播 WebSocket/HTTP 消息，转换为标准格式。

斗鱼消息格式参考：
- chatmsg: 普通弹幕 (nn=昵称, txt=内容, rid=房间号, ct=时间)
- dgb: 礼物消息 (nn=昵称, gfn=礼物名, gfcnt=数量, hits=价值)
- uenter: 进房消息 (nn=昵称)
- frank: 点赞消息 (nn=昵称)
"""

from typing import Any, Dict, Optional

from loguru import logger

from danmaku_listener.adapters.base import BaseAdapter
from danmaku_listener.bus.message import DanmakuMessage, GiftInfo


# 斗鱼消息类型映射
DOUYU_MSG_TYPE_MAP = {
    "chatmsg": "chat",       # 普通弹幕
    "dgb": "gift",           # 礼物
    "uenter": "enter",       # 进房
    "frank": "like",         # 点赞
    "rss": "room_stats",     # 房间统计
    "blackres": "blacklist", # 黑名单
}


class DouyuAdapter(BaseAdapter):
    """斗鱼平台适配器

    解析斗鱼直播消息中的 JSON payload，提取弹幕、礼物等信息。
    支持斗鱼 WebSocket 协议的 type 字段和 HTTP API 的 method 字段。
    """

    @property
    def platform(self) -> str:
        """返回平台标识"""
        return "douyu"

    def can_parse(self, data: Any) -> bool:
        """判断是否能处理该数据

        支持包含 type 或 method 字段的字典数据。

        Args:
            data: 待检测的数据

        Returns:
            True 如果适配器能处理此数据
        """
        if not isinstance(data, dict):
            return False

        return "type" in data or "method" in data

    async def parse(
        self, raw_data: Any, context: Optional[Dict] = None
    ) -> Optional[DanmakuMessage]:
        """解析原始数据为标准消息格式

        Args:
            raw_data: 原始数据（包含 type/method 和相关字段的字典）
            context: 可选的上下文信息（如 platform, room_id）

        Returns:
            DanmakuMessage 实例，如果解析失败返回 None
        """
        try:
            if not self.can_parse(raw_data):
                return None

            # 统一消息类型字段
            msg_type_str = raw_data.get("type") or raw_data.get("method", "")
            msg_type = DOUYU_MSG_TYPE_MAP.get(msg_type_str, "")

            if not msg_type:
                return None

            # 提取通用字段
            user_name = str(raw_data.get("nn", "") or "anonymous")
            room_id = (context or {}).get("room_id", "") or str(raw_data.get("rid", ""))
            platform = (context or {}).get("platform", "douyu")
            timestamp = int(raw_data.get("ct", 0)) or 0

            if msg_type == "chat":
                content = str(raw_data.get("txt", "") or "").strip()
                if not content:
                    return None
                return DanmakuMessage(
                    platform=platform,
                    room_id=room_id,
                    user_name=user_name,
                    content=content,
                    timestamp=timestamp,
                    message_type="normal",
                )

            elif msg_type == "gift":
                gift_name = str(raw_data.get("gfn", "") or "未知礼物")
                gift_count = int(raw_data.get("gfcnt", 1) or 1)
                gift_value = int(raw_data.get("hits", 0) or 0)
                content = str(raw_data.get("txt", "") or f"送出{gift_name}")

                gift_info = GiftInfo(
                    user_name=user_name,
                    gift_name=gift_name,
                    gift_count=gift_count,
                    gift_value=gift_value,
                )
                return DanmakuMessage(
                    platform=platform,
                    room_id=room_id,
                    user_name=user_name,
                    content=content,
                    timestamp=timestamp,
                    message_type="gift",
                    gift_info=gift_info,
                )

            elif msg_type == "enter":
                return DanmakuMessage(
                    platform=platform,
                    room_id=room_id,
                    user_name=user_name,
                    content=f"{user_name} 进入直播间",
                    timestamp=timestamp,
                    message_type="system",
                )

            elif msg_type == "like":
                return DanmakuMessage(
                    platform=platform,
                    room_id=room_id,
                    user_name=user_name,
                    content=f"{user_name} 点赞",
                    timestamp=timestamp,
                    message_type="system",
                )

            else:
                return None

        except Exception as e:
            logger.error(f"Error parsing douyu message: {e}")
            return None
