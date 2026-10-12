"""快手/斗鱼瞬态注入 sender（AutoDanmu T6/X1——M0 探针配方固化，common.py DOM_PLATFORMS 对照）

配方来源（M0 探针卡片 found 字段实证，2026-10-07）：
- 快手：storage_state 注入（cookie/kuaishou_storage_state.json）→ live.kuaishou.com
  → `textarea` + `text=发送`（候选 input[placeholder*=弹幕] 实测不可见）
- 斗鱼：cookie 注入（cookie/douyu_login_cookies.json，acf_* R19 验证）→ douyu.com
  → 通用 input[placeholder] + `.ChatSend-button`（.ChatSend-input UI 变更后不可见）
登录失效：PLATFORM_REJECTED + NEEDS_LOGIN 同款 fix_hint（DX-D1 CLI 重登）
"""
from __future__ import annotations

from danmaku_listener.senders.dom_transient import DomTransientSender


class KuaishouStateSender(DomTransientSender):
    """快手 storage_state 注入 sender（无 profile，天然无锁需求）"""

    platform = "kuaishou"
    room_url_template = "https://live.kuaishou.com/u/{room_id}"
    login_hint = "重新登录：python -m danmaku_listener.engines.kuaishou_login [room_id]（DX-D1）"
    input_selectors = ("textarea", "input[placeholder*=弹幕]", "div[contenteditable=true]")
    button_selectors = ('button:has-text("发送")', 'text=发送')
    cookie_file = "cookie/kuaishou_storage_state.json"
    storage_state_mode = True
    home_url = "https://live.kuaishou.com/"


class DouyuCookieSender(DomTransientSender):
    """斗鱼 cookie 注入 sender（acf_* 会话充分——R19 验证）"""

    platform = "douyu"
    room_url_template = "https://www.douyu.com/{room_id}"
    login_hint = "重新登录：python -m danmaku_listener.engines.send_login douyu [room_id]（DX-D1）"
    input_selectors = (".ChatSend-txt",  # 2026-10-08 新前端 live-next：输入面=contenteditable DIV
                       "input[placeholder*=说]", "input[placeholder*=未拥有]",
                       "input[placeholder*=弹幕]", "#js-player-input",
                       "input[placeholder]")  # 泛匹配降级最后（防搜索框误选——T6 实测；
                                              # 新前端改版后 input 系全失效会命中搜索框——
                                              # .ChatSend-txt 优先即为此）
    button_selectors = (".ChatSend-button", 'button:has-text("发送")', "text=发送")
    cookie_file = "cookie/douyu_login_cookies.json"
    storage_state_mode = False
    home_url = "https://www.douyu.com/"
