"""淘宝/1688 通用 mtop H5 API 客户端框架（阿里系统一网关）

mtop 协议要点（TaobaoLiveWebFetcher 参考实现对照，2026-09-29）：
- 网关：https://h5api.m.{domain}/h5/{api}/{version}/
- 签名：md5("{token}&{t}&{appKey}&{data_str}")，token=_m_h5_tk cookie 前段
- 响应：jsonp 包裹（mtopjsonpN(...)）——剥壳取 JSON
- 凭证：浏览器打开直播间页面产生 _m_h5_tk/_m_h5_tk_enc（设备级 token，游客可用）
- 30s 无消息 → 凭证过期/风控 → 重新取凭证

线程模型：requests.Session 同步调用（run_in_executor 中执行）；Playwright 凭证
提取用 async API（凭证阶段与轮询阶段分离，凭证过期触发整轮重建——契约 O）。
"""

import hashlib
import json
import random
import re
import time
from typing import Any, Dict, Optional, Tuple

import requests
from loguru import logger


class MtopError(RuntimeError):
    """mtop 请求/签名错误"""


def make_sign(m_h5_tk: str, t: str, app_key: str, data_str: str) -> str:
    """mtop H5 签名：md5("{token}&{t}&{appKey}&{data_str}")"""
    token = m_h5_tk.split("_", 1)[0] if m_h5_tk else ""
    s = f"{token}&{t}&{app_key}&{data_str}"
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def strip_jsonp(text: str) -> str:
    """剥掉 mtopjsonpN(...) 包裹"""
    start = text.find("(")
    end = text.rfind(")")
    if start == -1 or end == -1 or end <= start:
        raise MtopError(f"jsonp 剥壳失败: {text[:80]!r}")
    return text[start + 1 : end]


class MtopClient:
    """mtop H5 GET 客户端（jsonp；凭证由调用方注入 session cookies）"""

    def __init__(self, domain: str, user_agent: str, session: Optional[requests.Session] = None):
        self._gateway = f"https://h5api.m.{domain}/h5/"
        self._ua = user_agent
        self._session = session or requests.Session()

    def get(self, api: str, version: str, app_key: str, data: Dict[str, Any],
            m_h5_tk: str, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """GET mtop 接口（jsonp），返回剥壳 JSON"""
        t = str(int(time.time() * 1000))
        data_str = json.dumps(data, separators=(",", ":"))
        sign = make_sign(m_h5_tk, t, app_key, data_str)
        params = {
            "jsv": "2.7.2", "appKey": app_key, "t": t, "sign": sign,
            "api": api, "v": version,
            "preventFallback": "true", "type": "jsonp", "dataType": "jsonp",
            "callback": f"mtopjsonp{random.randint(1, 100)}", "data": data_str,
        }
        headers = {"User-Agent": self._ua}
        if extra_headers:
            headers.update(extra_headers)
        url = f"{self._gateway}{api}/{version}/"
        resp = self._session.get(url, params=params, headers=headers, timeout=10)
        text = resp.text
        try:
            return json.loads(strip_jsonp(text))
        except (MtopError, json.JSONDecodeError) as e:
            raise MtopError(f"{api} 响应解析失败: {e}: {text[:80]!r}") from e

    @staticmethod
    def ret_ok(result: Dict[str, Any]) -> bool:
        """mtop 通用返回码判断（RET_OK=[\"SUCCESS::调用成功\"]）"""
        ret = result.get("ret") or []
        return any("SUCCESS" in r for r in ret) if ret else False

    @staticmethod
    def ret_fail_reason(result: Dict[str, Any]) -> str:
        ret = result.get("ret") or []
        return "; ".join(ret) if ret else "unknown"


class MtopCredential:
    """凭证：_m_h5_tk/_m_h5_tk_enc cookies（由 Playwright 凭证阶段提取）"""

    def __init__(self, cookies: Dict[str, str]):
        self.cookies = cookies

    @property
    def m_h5_tk(self) -> str:
        return self.cookies.get("_m_h5_tk", "")


def extract_live_id(room_spec: str) -> str:
    """房间参数归一：liveId 数字或完整 URL"""
    if "taobao.com" in room_spec or "tmall.com" in room_spec:
        m = re.search(r"liveId=(\d+)", room_spec)
        if m:
            return m.group(1)
    m = re.match(r"^(\d{6,20})$", room_spec.strip())
    if m:
        return m.group(1)
    raise ValueError(f"无法解析淘宝直播间 ID: {room_spec!r}")


def parse_base64_mixed_message(base64_data: str) -> Tuple[list, bytes]:
    """powermsg 消息体解析：base64 → protobuf+JSON 混合字节流 → 扫描提取 JSON 对象

    （TaobaoLiveWebFetcher.ProtobufMessageParser.parse_base64_message 同构——
    0x7B 起、括号计数、字符串内转义感知）
    """
    import base64

    decoded = base64.b64decode(base64_data)
    json_objects = []
    pos = 0
    while pos < len(decoded):
        json_start = -1
        for i in range(pos, len(decoded)):
            if decoded[i] == 0x7B:  # '{'
                json_start = i
                break
        if json_start == -1:
            break
        brace = 0
        in_string = False
        escaped = False
        json_end = -1
        for i in range(json_start, len(decoded)):
            ch = chr(decoded[i]) if decoded[i] < 128 else "?"
            if not in_string:
                if ch == "{":
                    brace += 1
                elif ch == "}":
                    brace -= 1
                elif ch == '"':
                    in_string = True
            else:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            if brace == 0 and i > json_start:
                json_end = i
                break
        if json_end == -1:
            break
        try:
            json_objects.append(json.loads(decoded[json_start : json_end + 1].decode("utf-8", errors="ignore")))
        except json.JSONDecodeError:
            pass
        pos = json_end + 1
    return json_objects, decoded
