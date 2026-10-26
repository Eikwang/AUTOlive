# -*- coding: UTF-8 -*-
"""
异常报警通知模块（2026-10-26 报警重构批次）

统一报警/通知出口：
- alarm_error(module, message)：出错/崩溃报警（六模块开关门控 + 60s 模块级冷却防风暴）
- alarm_notice(module, message)：进度通知（训练完成等一次性事件，不冷却）
- send_test()：WebUI 测试按钮入口，向所有已配置渠道同步发测试消息

通告渠道（docs/飞书WEBHOOK.txt、微信WEBHOOK.txt、钉钉WEBHOOK.txt）：
- 飞书   POST {"msg_type":"text","content":{"text":...}}，成功 code==0
- 企业微信 POST {"msgtype":"text","text":{"content":...}}，成功 errcode==0
- 钉钉   POST {"msgtype":"text","text":{"content":...}}，成功 errcode==0；
        机器人安全设置为"加签"时拼 timestamp+sign（HMAC-SHA256(base64)）

设计约束：
- 配置读 config.json（abnormal_alarm.modules / abnormal_alarm.webhook），mtime 缓存
  热生效——编排器/EDTalk 客户端等拿不到全量 config 的调用点零接线接入
- 发送走后台守护线程（5s 连接/10s 读取超时），绝不阻塞直播主流程；
  发送失败仅记日志，不重试不抛出
- 报警类消息 60s 模块级冷却（LLM 故障 + 弹幕风暴场景每条都报会刷屏），
  通知类不冷却（训练完成是一次性事件）
"""

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.parse

import requests

from utils.my_log import logger

# ---------- 六个报警模块（与 config abnormal_alarm.modules 键一致） ----------

MODULE_LLM = "llm"
MODULE_DIGITAL_HUMAN = "digital_human"
MODULE_TTS = "tts"
MODULE_ORCHESTRATION = "orchestration"
MODULE_TRAIN_STREAMER = "train_streamer"
MODULE_TRAIN_VOICE = "train_voice"

MODULE_LABELS = {
    MODULE_LLM: "LLM服务",
    MODULE_DIGITAL_HUMAN: "数字人服务",
    MODULE_TTS: "TTS生成",
    MODULE_ORCHESTRATION: "动作编排",
    MODULE_TRAIN_STREAMER: "训练主播",
    MODULE_TRAIN_VOICE: "训练声音",
}

# 报警类消息模块级冷却（秒）
_COOLDOWN_S = 60.0

# ---------- 配置读取（config.json 惰性 mtime 缓存） ----------

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

_config_lock = threading.Lock()
_config_cache = {"mtime": None, "data": {}}
_config_override = None  # 测试注入（dict）；非 None 时优先


def set_config_path(path: str) -> None:
    """覆盖 config.json 路径（特殊部署/测试用）。"""
    global _CONFIG_PATH
    with _config_lock:
        _CONFIG_PATH = path
        _config_cache["mtime"] = None
        _config_cache["data"] = {}


def _load_config() -> dict:
    """读 config.json（mtime 变化才重读；读失败保留旧缓存）。"""
    global _config_override
    if _config_override is not None:
        return _config_override
    with _config_lock:
        try:
            mtime = os.path.getmtime(_CONFIG_PATH)
        except OSError:
            return _config_cache["data"]  # 文件不存在→保留缓存（通常为空）
        if mtime != _config_cache["mtime"]:
            try:
                with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                    _config_cache["data"] = json.load(f) or {}
                _config_cache["mtime"] = mtime
            except Exception as e:
                logger.warning(f"报警模块读取 config.json 失败（沿用旧缓存）：{e}")
        return _config_cache["data"]


def _alarm_cfg() -> tuple:
    """返回 (modules 开关 dict, webhook 配置 dict)。"""
    aa = (_load_config() or {}).get("abnormal_alarm") or {}
    return aa.get("modules") or {}, aa.get("webhook") or {}


def _module_enabled(module: str) -> bool:
    """模块开关（缺省=开，向后兼容存量无 modules 节的配置）。"""
    modules, _ = _alarm_cfg()
    v = modules.get(module)
    return True if v is None else bool(v)


# ---------- 消息构造 ----------

def _compose(kind: str, module: str, message: str, fix_hint: str = "") -> str:
    """组装推送文本：【AUTOlive·报警/通知】模块 + 时间 + 内容 + 指引。"""
    label = MODULE_LABELS.get(module, module)
    lines = [
        f"【AUTOlive·{kind}】{label}",
        f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"内容：{message}",
    ]
    if fix_hint:
        lines.append(f"指引：{fix_hint}")
    return "\n".join(lines)


# ---------- 三平台载荷与响应校验（纯函数，便于测试） ----------

def feishu_payload(text: str) -> dict:
    return {"msg_type": "text", "content": {"text": text}}


def wecom_payload(text: str) -> dict:
    return {"msgtype": "text", "text": {"content": text}}


def dingtalk_payload(text: str) -> dict:
    return {"msgtype": "text", "text": {"content": text}}


def dingtalk_signed_url(url: str, secret: str) -> str:
    """钉钉加签：sign=base64(hmac_sha256(secret, "{timestamp}\\n{secret}")) 后 URL 编码。"""
    ts = str(round(time.time() * 1000))
    sign_str = f"{ts}\n{secret}"
    sign = base64.b64encode(
        hmac.new(secret.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256).digest())
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"


def check_response(channel: str, resp) -> tuple:
    """校验平台响应。返回 (ok, detail)。

    - 飞书：JSON code==0（StatusCode/StatusMessage 为冗余字段不采用）
    - 企业微信/钉钉：JSON errcode==0
    """
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        return False, "响应非 JSON"
    if channel == "feishu":
        return (data.get("code") == 0,
                f"code={data.get('code')} msg={data.get('msg')}")
    return (data.get("errcode") == 0,
            f"errcode={data.get('errcode')} errmsg={data.get('errmsg')}")


_PAYLOAD_BUILDERS = {
    "feishu": feishu_payload,
    "wecom": wecom_payload,
    "dingtalk": dingtalk_payload,
}


# ---------- 发送 ----------

def _send(channel: str, url: str, secret: str, text: str) -> tuple:
    """POST 单渠道。返回 (ok, detail)；任何异常只记日志不外抛。"""
    try:
        payload = _PAYLOAD_BUILDERS[channel](text)
        if channel == "dingtalk" and secret:
            url = dingtalk_signed_url(url, secret)
        resp = requests.post(url, json=payload, timeout=(5, 10))
        ok, detail = check_response(channel, resp)
        if ok:
            logger.info(f"报警 WEBHOOK 推送成功（{channel}）")
        else:
            logger.warning(f"报警 WEBHOOK 推送失败（{channel}）：{detail}")
        return ok, detail
    except Exception as e:
        logger.warning(f"报警 WEBHOOK 推送异常（{channel}）:{type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {e}"


def _targets() -> list:
    """已配置的推送目标 [(channel, url, secret)]，按飞书→企业微信→钉钉固定序。"""
    _, webhook = _alarm_cfg()
    targets = []
    feishu = (webhook.get("feishu_url") or "").strip()
    wecom = (webhook.get("wecom_url") or "").strip()
    dingtalk = (webhook.get("dingtalk_url") or "").strip()
    dingtalk_secret = (webhook.get("dingtalk_secret") or "").strip()
    if feishu:
        targets.append(("feishu", feishu, ""))
    if wecom:
        targets.append(("wecom", wecom, ""))
    if dingtalk:
        targets.append(("dingtalk", dingtalk, dingtalk_secret))
    return targets


def _dispatch(text: str) -> int:
    """向全部已配置渠道异步推送（每渠道一个守护线程）。返回目标数。"""
    targets = _targets()
    for channel, url, secret in targets:
        threading.Thread(
            target=_send, args=(channel, url, secret, text), daemon=True,
            name=f"alarm-webhook-{channel}").start()
    return len(targets)


# ---------- 对外入口 ----------

_cooldown_lock = threading.Lock()
_last_error_ts: dict = {}  # module -> time.monotonic()


def alarm_error(module: str, message: str, fix_hint: str = "") -> bool:
    """出错/崩溃报警（六模块开关门控 + 60s 模块级冷却）。

    Returns:
        bool: True=已接受并派发（或无渠道静默）；False=开关关闭/冷却中跳过
    """
    if not _module_enabled(module):
        return False
    now = time.monotonic()
    with _cooldown_lock:
        last = _last_error_ts.get(module)
        if last is not None and now - last < _COOLDOWN_S:
            return False
        _last_error_ts[module] = now
    text = _compose("报警", module, message, fix_hint)
    logger.warning(f"【异常报警-{MODULE_LABELS.get(module, module)}】{message}")
    _dispatch(text)
    return True


def alarm_notice(module: str, message: str) -> bool:
    """进度通知（训练完成等一次性事件；开关门控同报警，无冷却）。"""
    if not _module_enabled(module):
        return False
    text = _compose("通知", module, message)
    logger.info(f"【进度通知-{MODULE_LABELS.get(module, module)}】{message}")
    _dispatch(text)
    return True


def send_test() -> dict:
    """WebUI 测试按钮：同步向所有已配置渠道发测试消息。

    Returns:
        {channel: (ok, detail)}；未配置任何渠道时返回 {}
    """
    targets = _targets()
    results = {}
    for channel, url, secret in targets:
        results[channel] = _send(
            channel, url, secret,
            "【AUTOlive·测试】异常报警 WEBHOOK 通告测试消息——收到即代表该渠道已接通")
    return results


def _reset_for_tests() -> None:
    """测试复位：配置注入/缓存/冷却状态全部清空。"""
    global _config_override
    with _config_lock:
        _config_override = None
        _config_cache["mtime"] = None
        _config_cache["data"] = {}
    with _cooldown_lock:
        _last_error_ts.clear()


def set_config_dict(cfg: dict) -> None:
    """测试注入配置 dict（优先于 config.json）。"""
    global _config_override
    with _config_lock:
        _config_override = cfg or {}
