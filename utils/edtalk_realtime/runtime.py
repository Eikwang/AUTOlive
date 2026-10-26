# -*- coding: utf-8 -*-
"""EDTalk 运行时状态（2026-10-26 配置面收敛批次）。

把「端口 / 解释器」从用户配置降为系统内部运行时量：
- 端口：webui 拉起时优先 8000，被其他服务占用则自动扫描切换；实际端口写入
  运行时文件，全部消费方（EDTalkClient / 素材管理 / 前端控制区 / 编排页）
  经 resolve_api_base() 统一解析——杜绝"配置端口与实际监听端口漂移"。
- 解释器：自动探测 + 记忆。解析顺序 = 旧配置键（存量兼容，存在才用）→
  运行时文件记忆（上次实际使用）→ 仓库副本 edtalk/runtime312 →
  兄弟安装 D:\\AI\\EDTalk\\runtime312。解析结果回写运行时文件。

运行时文件：data/edtalk_runtime.json（get_base_path()/data，与素材暂存区
同盘同根；每次拉起成功后重写，读取方对缺失/损坏一律降级默认值）。
"""
import json
import os
import socket
import time
from urllib.parse import urlparse

from utils.my_log import logger

DEFAULT_PORT = 8000
_PORT_SCAN_END = 8100  # 8000 被占时向后扫描的上界（不含）
_DEFAULT_INTERPRETER = "D:\\AI\\EDTalk\\runtime312\\python.exe"


def _runtime_file() -> str:
    from frontend.config.paths import get_base_path
    return os.path.join(str(get_base_path()), "data", "edtalk_runtime.json")


def read_runtime() -> dict:
    """运行时状态快照（缺失/损坏 → {}，不抛错——消费方降级默认值）。"""
    try:
        with open(_runtime_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_runtime(port: int, interpreter: str) -> None:
    """拉起成功后记录实际端口与解释器（失败仅告警，不影响拉起结果）。"""
    try:
        path = _runtime_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"port": int(port), "interpreter": str(interpreter),
                       "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[edtalk_runtime] 运行时状态写入失败（不影响服务）：{e}")


# ----------------------------------------------------------------------
# 端口：探测 / 扫描 / 解析
# ----------------------------------------------------------------------

def probe_identity(port: int, timeout: float = 2.0) -> str:
    """探测 127.0.0.1:port 上的占用者身份。

    Returns: "realtime_serve"（/status 含 available_segments）|
             "other"（200 但身份不符）| "free"（连接失败）。
    """
    try:
        import requests
        resp = requests.get(f"http://127.0.0.1:{port}/status", timeout=timeout)
        if resp.status_code == 200:
            return "realtime_serve" if "available_segments" in resp.text else "other"
        return "other"
    except Exception:
        return "free"


def _tcp_free(port: int) -> bool:
    """TCP bind 试探（SO_REUSEADDR 关闭——真实反映端口占用）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_port(start: int = DEFAULT_PORT + 1) -> int:
    """从 start 起找第一个可绑定端口（无可用 → ValueError，三段式由调用方包装）。"""
    for port in range(start, _PORT_SCAN_END):
        if _tcp_free(port):
            return port
    raise ValueError(
        f"problem=无可用端口；cause={start}~{_PORT_SCAN_END - 1} 均被占用；"
        "fix=释放端口后重试")


def resolve_api_base(config_data: dict = None) -> str:
    """控制面基址统一解析（全消费方共用：客户端/素材/前端/编排页）。

    优先级：运行时文件（每次拉起刷新，最权威）→ 旧配置 api_ip_port
    （存量兼容/测试注入）→ 默认回环。端口自动切换后旧配置自然失效。
    """
    port = read_runtime().get("port")
    if isinstance(port, int) and 0 < port < 65536:
        return f"http://127.0.0.1:{port}"
    legacy = str(((config_data or {}).get("edtalk_realtime", {}) or {})
                 .get("api_ip_port", "") or "").strip()
    if legacy.startswith("http"):
        return legacy
    return f"http://127.0.0.1:{DEFAULT_PORT}"


# ----------------------------------------------------------------------
# 解释器：自动探测 + 记忆
# ----------------------------------------------------------------------

def resolve_interpreter(config_data: dict = None) -> str:
    """EDTalk runtime312 解释器解析（配置面已收敛，详见模块 docstring）。

    返回第一个真实存在的候选；全缺失返回空串（调用方三段式报错）。
    """
    candidates = []
    # 1) 旧配置键（存量兼容：已配置且存在才采纳，不再从默认表注入）
    legacy = str(((config_data or {}).get("edtalk_realtime", {}) or {})
                 .get("interpreter_path", "") or "").strip()
    if legacy:
        candidates.append(legacy)
    # 2) 上次实际使用（记忆）
    remembered = str(read_runtime().get("interpreter", "") or "").strip()
    if remembered:
        candidates.append(remembered)
    # 3) 仓库副本（copy_edtalk 若复制了完整 runtime312）
    # 4) 兄弟安装（copy_edtalk 源）
    try:
        from frontend.config.paths import get_base_path
        candidates.append(os.path.join(
            str(get_base_path()), "edtalk", "runtime312", "python.exe"))
    except Exception:
        pass
    candidates.append(_DEFAULT_INTERPRETER)

    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    return ""


def parse_api_port(api_base: str) -> int:
    """从基址提取端口（健壮性小工具，探测/日志用）。"""
    try:
        return int(urlparse(api_base).port or DEFAULT_PORT)
    except Exception:
        return DEFAULT_PORT
