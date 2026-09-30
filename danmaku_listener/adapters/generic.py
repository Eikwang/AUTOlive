"""通用平台适配器

基于浏览器 DOM 监听的通用弹幕提取适配器。
支持动态加载 JS 监听脚本，解析 DOM Mutation 数据为标准消息格式。
"""

import json
from typing import Any, Callable, Coroutine, Dict, Optional

from loguru import logger

from danmaku_listener.adapters.base import BaseAdapter
from danmaku_listener.bus.message import DanmakuMessage, GiftInfo


class GenericAdapter(BaseAdapter):
    """通用平台适配器

    适用于浏览器模式下的弹幕监听。通过 JS 脚本监听 DOM 变化，
    将提取到的弹幕数据通过 expose_function 回调传回 Python 端解析。

    支持功能：
    - 默认 MutationObserver 脚本
    - 按平台注册自定义监听脚本
    - 按消息类型注册自定义解析器
    - 解析 JSON 字符串和字典格式的数据

    Attributes:
        _scripts: platform -> JS 脚本映射
        _custom_parsers: type -> 自定义解析函数映射
    """

    def __init__(self):
        """初始化通用适配器"""
        self._scripts: Dict[str, str] = {}
        self._custom_parsers: Dict[str, Callable] = {}

    @property
    def platform(self) -> str:
        """返回平台标识"""
        return "generic"

    def can_parse(self, data: Any) -> bool:
        """判断是否能处理该数据

        支持的数据格式：
        - 包含 type 或 content 字段的字典
        - JSON 字符串（解析后包含 type 或 content）

        Args:
            data: 待检测的数据

        Returns:
            True 如果适配器能处理此数据
        """
        if isinstance(data, dict):
            return "type" in data or "content" in data

        if isinstance(data, str):
            try:
                parsed = json.loads(data)
                return isinstance(parsed, dict) and ("type" in parsed or "content" in parsed)
            except (json.JSONDecodeError, ValueError):
                return False

        return False

    async def parse(
        self, raw_data: Any, context: Optional[Dict] = None
    ) -> Optional[DanmakuMessage]:
        """解析原始数据为标准消息格式

        支持字典和 JSON 字符串两种输入格式。
        优先使用自定义解析器，回退到默认解析逻辑。

        Args:
            raw_data: 原始数据（字典或 JSON 字符串）
            context: 可选的上下文信息（如 platform, room_id）

        Returns:
            DanmakuMessage 实例，如果解析失败返回 None
        """
        try:
            data = self._normalize_data(raw_data)
            if data is None:
                return None

            # 提取消息类型
            msg_type = data.get("type", "dom_mutation")

            # 优先使用自定义解析器（自定义解析器可能使用不同的字段名）
            if msg_type in self._custom_parsers:
                result = await self._custom_parsers[msg_type](data, context)
                if result is not None:
                    return result
                # 自定义解析器返回 None，回退到默认解析

            # 默认解析：检查 content 字段
            content = data.get("content", "")
            if not content or not content.strip():
                return None

            platform = (context or {}).get("platform", "generic")
            room_id = (context or {}).get("room_id", "")
            user_name = data.get("user", "anonymous")

            # 默认解析逻辑
            return self._default_parse(data, content, msg_type, platform, room_id, user_name)

        except Exception as e:
            logger.error(f"Error parsing generic message: {e}")
            return None

    def _normalize_data(self, raw_data: Any) -> Optional[Dict]:
        """将原始数据标准化为字典

        Args:
            raw_data: 原始数据

        Returns:
            字典格式的数据，无法转换时返回 None
        """
        if isinstance(raw_data, dict):
            return raw_data

        if isinstance(raw_data, str):
            try:
                parsed = json.loads(raw_data)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    def _default_parse(
        self,
        data: Dict,
        content: str,
        msg_type: str,
        platform: str,
        room_id: str,
        user_name: str,
    ) -> DanmakuMessage:
        """默认解析逻辑

        Args:
            data: 原始数据字典
            content: 弹幕内容
            msg_type: 消息类型
            platform: 平台标识
            room_id: 房间 ID
            user_name: 用户名

        Returns:
            DanmakuMessage 实例
        """
        if msg_type == "gift":
            gift_info = GiftInfo(
                user_name=user_name,
                gift_name=data.get("gift_name", ""),
                gift_count=int(data.get("gift_count", 1)),
                gift_value=int(data.get("gift_value", 0)) if "gift_value" in data else None,
            )
            return DanmakuMessage(
                platform=platform,
                room_id=room_id,
                user_name=user_name,
                content=content,
                timestamp=0,
                message_type="gift",
                gift_info=gift_info,
            )
        elif msg_type == "system":
            return DanmakuMessage(
                platform=platform,
                room_id=room_id,
                user_name=user_name,
                content=content,
                timestamp=0,
                message_type="system",
            )
        else:
            # dom_mutation 和其他类型都作为普通弹幕
            return DanmakuMessage(
                platform=platform,
                room_id=room_id,
                user_name=user_name,
                content=content,
                timestamp=0,
                message_type="normal",
            )

    def get_script(self, platform: Optional[str] = None) -> str:
        """获取监听脚本

        如果指定了平台且该平台有注册的自定义脚本，返回自定义脚本。
        否则返回默认的 MutationObserver 脚本。

        Args:
            platform: 可选的平台标识

        Returns:
            JavaScript 代码字符串
        """
        if platform and platform in self._scripts:
            return self._scripts[platform]
        return self._default_script()

    def register_script(self, platform: str, script: str) -> None:
        """为指定平台注册自定义监听脚本

        Args:
            platform: 平台标识
            script: JavaScript 代码
        """
        self._scripts[platform] = script
        logger.debug(f"Registered custom script for platform: {platform}")

    def register_parser(
        self, msg_type: str, parser: Callable[[Dict, Optional[Dict]], Coroutine[Any, Any, Optional[DanmakuMessage]]]
    ) -> None:
        """为指定消息类型注册自定义解析器

        自定义解析器签名为 async def parser(data: dict, context: dict | None) -> DanmakuMessage | None。
        如果解析器返回 None，回退到默认解析逻辑。

        Args:
            msg_type: 消息类型标识
            parser: 异步解析函数
        """
        self._custom_parsers[msg_type] = parser
        logger.debug(f"Registered custom parser for type: {msg_type}")

    async def inject_to_engine(self, engine: Any, room_id: str, platform: Optional[str] = None) -> None:
        """将监听脚本注入到 BrowserEngine

        Args:
            engine: BrowserEngine 实例
            room_id: 房间 ID
            platform: 可选的平台标识（决定使用哪个脚本）
        """
        script = self.get_script(platform)
        await engine._inject_script(room_id, script)
        logger.debug(f"Injected script for room: {room_id}, platform: {platform or 'default'}")

    async def expose_callback_to_engine(self, engine: Any, room_id: str) -> None:
        """将 onDanmaku 回调暴露到 BrowserEngine

        Args:
            engine: BrowserEngine 实例
            room_id: 房间 ID
        """
        async def on_danmaku_callback(json_str: str) -> None:
            """JS 端调用 onDanmaku 时触发的回调"""
            try:
                data = json.loads(json_str)
                message = await self.parse(data, {"room_id": room_id})
                if message:
                    await engine._emit_message({
                        "platform": message.platform,
                        "room_id": message.room_id,
                        "raw_data": data,
                        "msg_id": f"{room_id}_{id(data)}",
                    })
            except Exception as e:
                logger.error(f"Error in onDanmaku callback: {e}")

        await engine._expose_callback(room_id, "onDanmaku", on_danmaku_callback)
        logger.debug(f"Exposed onDanmaku callback for room: {room_id}")

    @staticmethod
    def _default_script() -> str:
        """返回默认的 MutationObserver 监听脚本

        Returns:
            JavaScript 代码字符串
        """
        return """
        // DanmakuListener GenericAdapter monitoring script
        // Observes DOM mutations and forwards danmaku data via onDanmaku callback
        (function() {
            const target = document.body;
            const observer = new MutationObserver(function(mutations) {
                mutations.forEach(function(mutation) {
                    mutation.addedNodes.forEach(function(node) {
                        if (node.nodeType === Node.ELEMENT_NODE) {
                            const text = node.textContent?.trim();
                            if (text && typeof onDanmaku === 'function') {
                                onDanmaku(JSON.stringify({
                                    type: 'dom_mutation',
                                    content: text,
                                    element: node.tagName
                                }));
                            }
                        }
                    });
                });
            });
            observer.observe(target, { childList: true, subtree: true });
        })();
        """
