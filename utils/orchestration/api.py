# -*- coding: utf-8 -*-
"""
动作编排控制面路由（P2 管理页后端；沿 danmaku_api.py 模式）

挂载到 main.py 进程 FastAPI（WebServer._register_routes）：
- GET  /choreography/status  运行状态快照（webui 状态条 5s 轮询）
- POST /choreography/rules   规则热应用+持久化（结构校验→段名校验→安全换装→写节）

写者矩阵：choreography 节的唯一写者 = 本模块 _write_choreography_section
（tmp+os.replace 原子改写，沿 danmaku_consumer._write_config_section 模式）。
"""
import json
import os
import threading
from typing import Callable, Optional

from utils.my_log import logger

_CONFIG_LOCK = threading.Lock()


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


def register_choreography_routes(app, config) -> None:
    """把 /choreography/* 控制面路由挂到 main.py 进程 FastAPI 应用。"""
    from fastapi import HTTPException

    @app.get("/choreography/status")
    async def choreography_status():
        """运行状态快照（webui 状态条轮询）。未装配=503（页面显示未启用态）。"""
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
    async def choreography_rules(node: dict = None):
        """规则热应用 + 持久化（管理页「应用」按钮；整节替换）。

        Returns: {success, status, applied, enabled_rules, rejected, missing_all,
                  message, persisted}
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
