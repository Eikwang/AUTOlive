"""B站平台适配器

解析B站直播 WebSocket 消息，转换为标准格式。

B站消息格式参考：
- DANMU_MSG: 普通弹幕 (info[1]=内容, info[2]=昵称)
- SEND_GIFT: 礼物消息 (data.uname, data.giftName, data.num, data.total_coin)
- INTERACT_WORD: 进房消息 (data.uname)
- SUPER_CHAT_MESSAGE: 醒目留言 (data.user_info.uname, data.message, data.price)
- GUARD_BUY: 上舰消息 (data.username, data.gift_name, data.num, data.price)
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from danmaku_listener.adapters.base import BaseAdapter
from danmaku_listener.bus.message import DanmakuMessage, GiftInfo


# B站消息类型映射
BILIBILI_CMD_MAP = {
    "DANMU_MSG": "danmu",              # 普通弹幕
    "SEND_GIFT": "gift",               # 礼物
    "INTERACT_WORD": "enter",          # 进房
    "SUPER_CHAT_MESSAGE": "super_chat", # 醒目留言
    "GUARD_BUY": "guard_buy",          # 上舰
    "WELCOME": "welcome",              # 欢迎老爷
}


class BilibiliAdapter(BaseAdapter):
    """B站平台适配器

    解析B站直播 WebSocket 消息中的 cmd 字段，提取弹幕、礼物等信息。
    """

    @property
    def platform(self) -> str:
        """返回平台标识"""
        return "bilibili"

    def can_parse(self, data: Any) -> bool:
        """判断是否能处理该数据

        支持包含 cmd 字段的字典数据。

        Args:
            data: 待检测的数据

        Returns:
            True 如果适配器能处理此数据
        """
        if not isinstance(data, dict):
            return False

        return "cmd" in data

    async def parse(
        self, raw_data: Any, context: Optional[Dict] = None
    ) -> Optional[DanmakuMessage]:
        """解析原始数据为标准消息格式

        Args:
            raw_data: 原始数据（包含 cmd 和相关字段的字典）
            context: 可选的上下文信息（如 platform, room_id）

        Returns:
            DanmakuMessage 实例，如果解析失败返回 None
        """
        try:
            if not self.can_parse(raw_data):
                return None

            cmd = raw_data.get("cmd", "")
            msg_type = BILIBILI_CMD_MAP.get(cmd, "")

            if not msg_type:
                return None

            platform = (context or {}).get("platform", "bilibili")
            room_id = (context or {}).get("room_id", "")

            if msg_type == "danmu":
                return self._parse_danmu(raw_data, platform, room_id)
            elif msg_type == "gift":
                return self._parse_gift(raw_data, platform, room_id)
            elif msg_type == "enter":
                return self._parse_enter(raw_data, platform, room_id)
            elif msg_type == "super_chat":
                return self._parse_super_chat(raw_data, platform, room_id)
            elif msg_type == "guard_buy":
                return self._parse_guard_buy(raw_data, platform, room_id)
            else:
                return None

        except Exception as e:
            logger.error(f"Error parsing bilibili message: {e}")
            return None

    def _parse_danmu(
        self, data: Dict, platform: str, room_id: str
    ) -> Optional[DanmakuMessage]:
        """解析普通弹幕消息

        B站弹幕格式: info = [[...], "内容", "昵称", ...]

        Args:
            data: 原始数据
            platform: 平台标识
            room_id: 房间 ID

        Returns:
            DanmakuMessage 实例
        """
        info: List = data.get("info", [])
        if not info or len(info) < 3:
            return None

        content = str(info[1] or "").strip()
        if not content:
            return None

        user_name = str(info[2] or "anonymous")
        # info[0][4] 是时间戳（毫秒）
        timestamp = 0
        try:
            if info[0] and len(info[0]) > 4:
                timestamp = int(info[0][4]) // 1000
        except (IndexError, TypeError, ValueError):
            pass

        return DanmakuMessage(
            platform=platform,
            room_id=room_id,
            user_name=user_name,
            content=content,
            timestamp=timestamp,
            message_type="normal",
        )

    def _parse_gift(
        self, data: Dict, platform: str, room_id: str
    ) -> Optional[DanmakuMessage]:
        """解析礼物消息

        Args:
            data: 原始数据
            platform: 平台标识
            room_id: 房间 ID

        Returns:
            DanmakuMessage 实例
        """
        gift_data = data.get("data", {})
        if not gift_data:
            return None

        user_name = str(gift_data.get("uname", "") or "anonymous")
        gift_name = str(gift_data.get("giftName", "") or "未知礼物")
        gift_count = int(gift_data.get("num", 1) or 1)
        gift_value = int(gift_data.get("total_coin", 0) or 0)
        # context 中的 room_id 优先，否则用 data 中的
        if not room_id:
            room_id = str(gift_data.get("roomid", ""))

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
            content=f"送出{gift_name} x{gift_count}",
            timestamp=0,
            message_type="gift",
            gift_info=gift_info,
        )

    def _parse_enter(
        self, data: Dict, platform: str, room_id: str
    ) -> Optional[DanmakuMessage]:
        """解析进房消息

        Args:
            data: 原始数据
            platform: 平台标识
            room_id: 房间 ID

        Returns:
            DanmakuMessage 实例
        """
        enter_data = data.get("data", {})
        user_name = str(enter_data.get("uname", "") or "anonymous")

        return DanmakuMessage(
            platform=platform,
            room_id=room_id,
            user_name=user_name,
            content=f"{user_name} 进入直播间",
            timestamp=0,
            message_type="system",
        )

    def _parse_super_chat(
        self, data: Dict, platform: str, room_id: str
    ) -> Optional[DanmakuMessage]:
        """解析醒目留言消息

        Args:
            data: 原始数据
            platform: 平台标识
            room_id: 房间 ID

        Returns:
            DanmakuMessage 实例
        """
        sc_data = data.get("data", {})
        if not sc_data:
            return None

        user_info = sc_data.get("user_info", {})
        user_name = str(user_info.get("uname", "") or "anonymous")
        content = str(sc_data.get("message", "") or "").strip()
        if not content:
            return None

        return DanmakuMessage(
            platform=platform,
            room_id=room_id,
            user_name=user_name,
            content=content,
            timestamp=0,
            message_type="normal",
        )

    def _parse_guard_buy(
        self, data: Dict, platform: str, room_id: str
    ) -> Optional[DanmakuMessage]:
        """解析上舰消息

        Args:
            data: 原始数据
            platform: 平台标识
            room_id: 房间 ID

        Returns:
            DanmakuMessage 实例
        """
        guard_data = data.get("data", {})
        if not guard_data:
            return None

        user_name = str(guard_data.get("username", "") or "anonymous")
        gift_name = str(guard_data.get("gift_name", "") or "舰长")
        gift_count = int(guard_data.get("num", 1) or 1)
        gift_value = int(guard_data.get("price", 0) or 0)

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
            content=f"购买{gift_name} x{gift_count}",
            timestamp=0,
            message_type="gift",
            gift_info=gift_info,
        )
