"""平台标识解析工具

提供直播间规格字符串的解析功能，格式为 "platform:room_id"。
另含直播间链接识别（2026-10-05 R2 选项式添加：用户可粘贴链接）。
"""

import re
from dataclasses import dataclass
from typing import Optional

# 支持的平台类型
PlatformType = str  # 使用字符串字面量联合类型更灵活

# 已知平台列表
SUPPORTED_PLATFORMS = ["douyin", "douyu", "bilibili", "kuaishou", "huya", "wechat_channels", "taobao", "1688", "meituan", "xiaohongshu", "pdd", "jd"]

# 直播间链接特征表（2026-10-05 R2：链接自动识别；顺序即匹配优先级）
# 仅收录链接结构稳定可正则提取的平台；其余平台（淘宝/拼多多/小红书/美团/
# 快手/视频号）链接形态多样，用户直接粘贴链接由所选平台引擎侧处理
LINK_PATTERNS = [
    ("douyin", re.compile(r"live\.douyin\.com/(\d+)")),
    ("douyu", re.compile(r"douyu\.com/(\d+)")),
    ("bilibili", re.compile(r"live\.bilibili\.com/(\d+)")),
    ("huya", re.compile(r"huya\.com/(\d+)")),
    ("jd", re.compile(r"zhibo\.jd\.com/liveroom\?[^\s]*liveId=(\d+)")),
    ("1688", re.compile(r"live\.1688\.com/zb/play\.html\?[^\s]*feedId=(\d+)")),
]


@dataclass(frozen=True)
class RoomSpec:
    """直播间规格

    Attributes:
        platform: 平台标识
        room_id: 房间 ID
    """

    platform: str
    room_id: str


def parse_room_spec(spec: str) -> RoomSpec:
    """解析直播间规格字符串

    Args:
        spec: 格式为 "platform:room_id" 的字符串

    Returns:
        RoomSpec 实例

    Raises:
        ValueError: 如果格式不正确或平台不支持
    """
    if not spec or ":" not in spec:
        raise ValueError(f"Invalid room spec format: {spec}. Expected 'platform:room_id'")

    parts = spec.split(":", 1)
    platform = parts[0].strip().lower()
    room_id = parts[1].strip()

    if not platform:
        raise ValueError("Platform cannot be empty")

    if not room_id:
        raise ValueError("Room ID cannot be empty")

    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}. Supported: {SUPPORTED_PLATFORMS}")

    return RoomSpec(platform=platform, room_id=room_id)


def extract_from_link(text: str) -> Optional[RoomSpec]:
    """从直播间链接中识别平台并提取房间号（2026-10-05 R2）

    Args:
        text: 用户输入（可能为直播间链接）

    Returns:
        RoomSpec 实例；链接形态未识别时返回 None（调用方提示手填房间号）
    """
    if not text:
        return None
    for platform, pattern in LINK_PATTERNS:
        m = pattern.search(text)
        if m:
            return RoomSpec(platform=platform, room_id=m.group(1))
    return None


def validate_platform(platform: str) -> bool:
    """验证平台是否受支持

    Args:
        platform: 平台标识

    Returns:
        True 如果平台受支持
    """
    return platform.lower() in SUPPORTED_PLATFORMS


def get_default_engine(platform: str) -> str:
    """获取平台的默认引擎类型（旧 listener 管线）

    抖音已切换原生 Web WS 协议引擎（2026-09-29，registry 路由）；
    mitmproxy 代理路线整体移除——旧管线剩余平台一律浏览器模式。

    Args:
        platform: 平台标识

    Returns:
        "browser"（旧管线仅剩浏览器模式）
    """
    return "browser"