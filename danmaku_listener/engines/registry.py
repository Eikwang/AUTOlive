"""引擎注册表（阶段 6 全局收口）

serve 与 AUTOlive 集成的统一入口：按平台构造契约 v1 引擎。
阶段 1-5 引擎在此集中注册（三级覆盖：全局→平台→房间 由配置面承接，
本表只做引擎构造与默认路由）。

兜底触发语义（配置人工开关，默认全部关闭）：
① 长尾平台（不在六平台范围的临时需求）② 主路线失效期间 ③ 调试对照
"""

from typing import Dict, Optional

from danmaku_listener.engines.base import BaseEngine
from danmaku_listener.engines.protocol.bilibili import BilibiliProtocolEngine
from danmaku_listener.engines.protocol.douyin import DouyinWebProtocolEngine
from danmaku_listener.engines.protocol.live1688 import Live1688Engine
from danmaku_listener.engines.protocol.taobao import TaobaoWebProtocolEngine
from danmaku_listener.engines.protocol.douyu import DouyuProtocolEngine
from danmaku_listener.engines.protocol.huya import HuyaProtocolEngine
from danmaku_listener.engines.protocol.kuaishou import KuaishouProtocolEngine
from danmaku_listener.engines.protocol.meituan import MeituanPollEngine
from danmaku_listener.engines.protocol.xiaohongshu import XiaohongshuEngine
from danmaku_listener.engines.protocol.pdd import PDDProtocolEngine
from danmaku_listener.engines.protocol.jd import JDProtocolEngine
from danmaku_listener.engines.wechat_channels import WechatChannelsEngine
from danmaku_listener.persistence.room_state_store import RoomStateStore

PLATFORM_ENGINES = {
    "bilibili": BilibiliProtocolEngine,
    # ADR-001 修订（2026-09-29）：抖音原生 Web WS 直连（T0 冒烟 PASS）——
    # BarrageGrab 桥接方案已撤销，douyin_grab.py 已删除（2026-10-05 清理确认）
    "douyin": DouyinWebProtocolEngine,
    "douyu": DouyuProtocolEngine,
    "huya": HuyaProtocolEngine,
    "kuaishou": KuaishouProtocolEngine,
    "taobao": TaobaoWebProtocolEngine,
    "1688": Live1688Engine,
    "meituan": MeituanPollEngine,
    "xiaohongshu": XiaohongshuEngine,
    "pdd": PDDProtocolEngine,
    "jd": JDProtocolEngine,
    "wechat_channels": WechatChannelsEngine,
}

#: 引擎可用性 warnings（CEO-2：加房间响应透出，前端可见）
PLATFORM_WARNINGS = {
    "huya": "虎牙 Tars 协议直连（2026-09-28 实测打通：DANMU/GIFT）；礼物名暂为类型编号",
    "kuaishou": "快手 web 直播间已强制游客登录（2026-09 实测）——首次添加弹登录窗口，登录后自动监听",
    "douyin": "抖音 Web WS 直连（游客可收弹幕/进场/点赞/关注/房间统计）；礼物事件只推"
              "登录观众（2026-10-03 实证）——首次添加弹登录窗口扫码一次（cookie 持久化）",
    "bilibili": "B站 WS 直连——游客可收全部六类消息（2026-10-04 实证：弹幕/进场/"
                "点赞/礼物/关注/在线；uid=0 为正常游客路径）；登录态为可选增强。"
                "comet 服务端会定期轮换连接（ConnectionClosed 1000 属正常，自动重连）",
    "meituan": "美团 mapi HTTP 轮询直连（无需登录/浏览器）；live_id 场次级——下播失效，"
               "开播后重新复制直播间分享链接（支持 dpurl.cn 短链）；1-2s 轮询延迟。"
               "进场消息为协议边界（走 Pike WS 签名保护，轮询接口无此数据，2026-10-05 实证）",
    "xiaohongshu": "小红书受控页面 WS 帧拦截（观众侧无需登录）；帧结构按开源实现/"
                   "调研资料实现，待在播房间实测校准",
    "pdd": "拼多多受控页面 WS 帧拦截（页面自持连接，titan 四层解码）——"
           "2026-10-03 验收通过：入场/点赞/关注/弹幕/房间信息；需直播间"
           "分享链接，首次添加弹扫码登录",
    "jd": "京东直播独立站（zhibo.jd.com/liveroom，游客可看）受控页面 WS 拦截；"
          "页面自建 live-ws4 连接免 liveauth 签名——2026-10-03 验收通过："
          "入场/弹幕（点赞帧为聚合形态无昵称）",
    "wechat_channels": "后台页面/接口结构待实测校准（微信更新可能变更）",
}


def build_engine(platform: str, state_store: Optional[RoomStateStore] = None, **kwargs) -> BaseEngine:
    """按平台构造引擎实例（自动注入平台特有配置，如 B站登录 cookie）

    Raises:
        KeyError: 平台未注册（长尾平台走兜底注入引擎，见 NOT in scope 与兜底开关）
    """
    if platform not in PLATFORM_ENGINES:
        raise KeyError(
            f"平台 {platform!r} 未注册（已注册：{sorted(PLATFORM_ENGINES)}）；"
            "长尾平台使用兜底注入引擎（配置人工开关，默认关闭）"
        )
    # 平台特有配置注入（Settings 单源）
    if platform == "bilibili":
        try:
            from danmaku_listener.config.settings import get_settings
            kwargs.setdefault("cookie_file", get_settings().bilibili_cookie_file)
        except Exception:
            pass
    if platform == "kuaishou":
        # 登录态 storage_state（token 获取走登录态浏览器；登录闭环见 bridge）
        try:
            from danmaku_listener.engines.kuaishou_login import DEFAULT_STATE_PATH
            kwargs.setdefault("cookie_file", DEFAULT_STATE_PATH)
        except Exception:
            pass
    if platform == "douyin":
        # 登录 cookie（2026-10-03 实证：礼物事件只推登录观众——登录闭环
        # douyin_login 保存路径，与引擎 WS 握手/roomInit 注入衔接）
        try:
            from danmaku_listener.engines.douyin_login import DEFAULT_COOKIE_FILE
            kwargs.setdefault("cookie_file", DEFAULT_COOKIE_FILE)
        except Exception:
            pass
    if platform in ("1688", "xiaohongshu", "jd", "pdd", "wechat_channels"):
        # 受控页面引擎 cookie/profile 目录（2026-10-02 用户实测：AUTOlive serve
        # cwd=AUTOlive 根，相对 ./cookie 解析到空目录 → 拼多多登录限定弹幕
        # 缺失）——settings.cookie_dir 统一配置，部署方可指向共享登录态目录
        try:
            from danmaku_listener.config.settings import get_settings
            kwargs.setdefault("cookie_dir", get_settings().cookie_dir)
        except Exception:
            pass
    return PLATFORM_ENGINES[platform](state_store=state_store, **kwargs)
