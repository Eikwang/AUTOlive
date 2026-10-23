# -*- coding: utf-8 -*-
"""
动作编排控制面路由（P2 管理页后端；沿 danmaku_api.py 模式）

挂载到 main.py 进程 FastAPI（WebServer._register_routes）：
- GET  /choreography/status   运行状态快照（webui 状态条 5s 轮询；F6 只读放行：
                              非回环来源也放行，避免局域网 webui 观察者把 403
                              误渲染为"编排器未装配"）
- POST /choreography/rules    规则热应用+持久化（结构校验→段名校验→安全换装→写节）
- POST /choreography/trigger  模拟触发测试事件（GAP-3；仅限本机调试——回环校验）

来源校验（SC-3 前提修正/native 发现12：main API 缺省绑定 0.0.0.0，web_server.py:307）：
状态变更端点（POST rules/trigger）统一回环校验（ipaddress.is_loopback，覆盖
127.0.0.0/8 与 ::1——Eng F6/DX 回环谓词裁决），非回环 403；远程编排控制官方
不支持，须自建反代+鉴权（RUNBOOK 声明）。

写者矩阵：choreography 节的唯一写者 = 本模块 _write_choreography_section
（tmp+os.replace 原子改写，沿 danmaku_consumer._write_config_section 模式）。
"""
import ipaddress
import json
import os
import threading
from typing import Callable, Optional

from fastapi import Request

from utils.my_log import logger

_CONFIG_LOCK = threading.Lock()

VALID_TRIGGER_TYPES = ("gift", "keyword", "song")
MAX_MATCH_VALUE_LEN = 500


def _config_path() -> str:
    """config.json 路径（沿 danmaku_consumer._config_path 语义：main 进程 cwd=仓库根）。"""
    return "config.json"


def _read_config_file() -> dict:
    with open(_config_path(), encoding="utf-8") as f:
        return json.load(f)


def _write_choreography_section(node: dict) -> bool:
    """原子改写 config.json 的 choreography 节（写者矩阵唯一写者）。

    mutator 语义简化为整节替换（管理页提交完整节点）；写失败返回 False。
    """
    with _CONFIG_LOCK:
        try:
            cfg = _read_config_file()
            cfg["choreography"] = node
            tmp = _config_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _config_path())
            return True
        except Exception:
            logger.error("[choreography] config.json 写回失败", exc_info=True)
            return False


def _choreographer():
    from utils.orchestration.choreographer import get_choreographer
    return get_choreographer()


def is_loopback_host(host: str) -> bool:
    """回环判定（Eng F6/DX 裁决）：ipaddress.is_loopback 覆盖 127.0.0.0/8 与 ::1，
    非裸字符串比较（可击穿 host=="127.0.0.1" 的朴素实现的 127.0.0.2 亦放行）。"""
    try:
        return ipaddress.ip_address(str(host or "")).is_loopback
    except ValueError:
        return False


def _require_loopback(request: Request) -> None:
    """状态变更端点回环校验依赖（非回环 403；三段式 detail）。"""
    client = getattr(request, "client", None)
    host = getattr(client, "host", "") if client is not None else ""
    if not is_loopback_host(host):
        logger.warning(f"[choreography] 非本机来源被拒：client={host or 'unknown'}")
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail={
            "code": "remote_forbidden",
            "problem": "非本机来源调用编排控制端点",
            "cause": f"client host={host or 'unknown'} 非回环地址"
                     f"（编排控制仅限本机：远程控制官方不支持，须自建反代+鉴权）",
            "fix": "在运行系统所在机器本机调用，或使用本机 webui 管理页"})


def register_choreography_routes(app, config) -> None:
    """把 /choreography/* 控制面路由挂到 main.py 进程 FastAPI 应用。"""
    from fastapi import Depends, HTTPException

    @app.get("/choreography/status")
    async def choreography_status():
        """运行状态快照（webui 状态条轮询）。未装配=503（页面显示未启用态）。

        F6 只读放行：无回环校验依赖——局域网 webui 观察者可看状态。
        """
        ch = _choreographer()
        if ch is None:
            raise HTTPException(status_code=503, detail={
                "problem": "编排器未装配", "cause": "主进程启动失败或版本未含编排模块",
                "fix": "查看主进程启动日志"})
        state = ch.get_state()
        # 配置面摘要（持久层读，与运行态区分）
        node = config.get("choreography") or {}
        state["configured_rules"] = len(node.get("rules") or [])
        state["persisted_enable"] = bool(node.get("enable", False))
        return state

    @app.post("/choreography/rules")
    async def choreography_rules(request: Request, node: dict = None,
                                 _guard: None = Depends(_require_loopback)):
        """规则热应用 + 持久化（管理页「应用」按钮；整节替换；回环校验）。

        Returns: {success, status, applied, enabled_rules, rejected, missing_all,
                  message, persisted, worker_started, ready}
        """
        if not isinstance(node, dict):
            raise HTTPException(status_code=422, detail={
                "problem": "请求体不是对象", "cause": "需提交完整 choreography 节",
                "fix": "以 config.example.json choreography 结构提交"})
        ch = _choreographer()
        if ch is None:
            raise HTTPException(status_code=503, detail={
                "problem": "编排器未装配", "cause": "主进程未运行编排模块",
                "fix": "重启运行系统后重试"})
        result = ch.apply_config(node)
        persisted = False
        if result.get("applied"):
            persisted = _write_choreography_section(node)
            if not persisted:
                logger.error("[choreography] 运行时已应用但持久化失败（重启丢失）")
        result["persisted"] = persisted
        result["success"] = bool(result.get("applied"))
        return result

    @app.post("/choreography/trigger")
    async def choreography_trigger(request: Request, payload: dict = None,
                                   _guard: None = Depends(_require_loopback)):
        """模拟触发测试事件（GAP-3；仅限本机调试——直播中会干扰实时编排状态）。

        载荷两形态（义务 F3 裁决）：
        - 管理页主形态 {"rule_id": ...}：端点从规则配置合成载荷（gift 规则
          total_price := rule.min_price 恰好满足门槛，下拉任一规则必可触发）；
        - 裸 API 形态 {"trigger_type", "match_value", 可选 "gift"}：gift 载荷
          白名单 gift_name/total_price/gift_num/unit_price；total_price 缺省 0
          （min_price>0 规则不命中=below_min_price 诚实门槛语义）。

        运行期拒绝/接受语义见 choreographer.decide_trigger reason 枚举
        （no_match/below_min_price/throttled{retry_after_s}/hold{retry_after_s}/
        in_progress/disabled + accepted/interrupted/requeued）。

        HTTP 语义：422 入参校验失败；503 编排器未装配；非回环 403；
        enable=false/worker 未启动 → 200 + accepted=false + reason=disabled
        （body 为权威失败字段——DX channel 裁决，客户端须检查 accepted）。
        """
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail={
                "problem": "请求体不是对象", "cause": "需提交模拟触发载荷",
                "fix": '提交 {"rule_id": ...} 或 {"trigger_type": "gift|keyword|song", "match_value": ...}'})
        if not payload.get("rule_id"):
            if str(payload.get("trigger_type", "") or "") not in VALID_TRIGGER_TYPES:
                raise HTTPException(status_code=422, detail={
                    "problem": "trigger_type 值域外", "cause": str(payload.get("trigger_type")),
                    "fix": "使用 gift|keyword|song，或提交 rule_id 形态"})
            if len(str(payload.get("match_value", "") or "")) > MAX_MATCH_VALUE_LEN:
                raise HTTPException(status_code=422, detail={
                    "problem": "match_value 超长", "cause": f">{MAX_MATCH_VALUE_LEN} 字符",
                    "fix": f"match_value 限制 {MAX_MATCH_VALUE_LEN} 字符内"})
        ch = _choreographer()
        if ch is None:
            raise HTTPException(status_code=503, detail={
                "problem": "编排器未装配", "cause": "主进程未运行编排模块",
                "fix": "重启运行系统后重试"})
        return ch.simulate_trigger(payload)
