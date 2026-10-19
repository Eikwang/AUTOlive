# -*- coding: UTF-8 -*-
"""
异常消息脱敏（D1 DX 义务）

异常消息进入运行状态条环形缓冲前过滤敏感片段：
- URL query string（token 常藏在 ?key=value 中）
- Authorization 请求头（Bearer 凭证）

独立纯函数，便于单测（tests/test_status_bar.py）。
"""
import re

# URL query string：问号起、到空白为止（消息文本场景，非严格 RFC 解析）
_QUERY_RE = re.compile(r"\?[^ ]+")

# Authorization 头：冒号/等号 + 可选 Bearer 前缀 + 凭证 token（大小写不敏感）。
# 比任务书基线正则多吞可选的 "Bearer " 前缀——否则 "Authorization: Bearer <tok>"
# 只吃掉 "Bearer" 二字，token 本体泄漏，脱敏失去意义。
_AUTH_RE = re.compile(
    r"Authorization[:=]\s*(?:Bearer\s+)?\S+",
    re.IGNORECASE,
)


def redact_exception(msg: str) -> str:
    """过滤异常消息中的敏感片段（query string + Authorization 头）。

    Args:
        msg: 原始异常消息（非 str 输入先 str() 化）

    Returns:
        脱敏后的消息：query → '?***'，Authorization 值 → 'Authorization:***'
    """
    msg = str(msg)
    msg = _QUERY_RE.sub("?***", msg)
    msg = _AUTH_RE.sub("Authorization:***", msg)
    return msg
