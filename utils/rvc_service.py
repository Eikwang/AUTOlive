# -*- coding: utf-8 -*-
"""
RVC 变声服务 AUTOlive 侧启动器 + 控制客户端（P2-1）

- build_rvc_vc_service(): 构造 ManagedProcess（RVC runtime python 跑薄 adapter，
  注册进 ServiceRegistry 供状态条展示）
- RvcVcClient(): 控制面 HTTP 客户端（/switch /start /stop /status /devices），
  供前端 svc.py 与训练页调用
"""
import json
import os
import urllib.request
from typing import Optional

from utils.autolive_paths import AutolivePaths
from utils.config import Config
from utils.my_log import logger
from utils.service_orchestrator import DEFAULT_POLICIES, ManagedProcess, ServiceRegistry

HTTP_PORT_DEFAULT = 9391


def rvc_runtime_python(config: Config, paths: AutolivePaths) -> str:
    """RVC 自带 runtime python（config 可覆盖；缺省 RVC 树内 runtime/）。"""
    v = config.get("rvc_vc", "runtime_python")
    if v:
        return v
    rvc_root = config.get("rvc_vc", "rvc_root") or r"D:\AI\RVC20240604Nvidia"
    return os.path.join(rvc_root, "runtime", "python.exe")


def build_rvc_vc_service(
    config: Config, paths: AutolivePaths, on_state_change=None
) -> Optional[ManagedProcess]:
    """构造（不启动）RVC 变声服务。config rvc_vc.hosted 默认 true。"""
    hosted = config.get("rvc_vc", "hosted")
    if hosted is None:
        hosted = True
    if not hosted:
        logger.info("[rvc-vc] rvc_vc.hosted=false，跳过托管拉起")
        return None

    rvc_root = config.get("rvc_vc", "rvc_root") or r"D:\AI\RVC20240604Nvidia"
    http_port = int(config.get("rvc_vc", "http_port") or HTTP_PORT_DEFAULT)

    policies = dict(DEFAULT_POLICIES)
    # 控制面探活地址（HTTP 服务常驻，无模型时 /status 即 200）
    custom = config.get("audio_integration", "policies") or {}
    policies.update({k: v for k, v in custom.items() if isinstance(v, (int, float))})

    adapter = os.path.join(paths.autolive_home, "apps", "rvc", "rvc_adapter.py")
    command = [
        rvc_runtime_python(config, paths),
        adapter,
        "--autolive-home", paths.autolive_home,
        "--rvc-root", rvc_root,
        "--http-port", str(http_port),
    ]
    env = paths.subprocess_env({"RVC_ROOT": rvc_root, "RVC_VC_HTTP_PORT": str(http_port)})

    svc = ManagedProcess(
        name="rvc-vc",
        command=command,
        cwd=rvc_root,  # RVC 内部相对路径依赖（assets/hubert 等）
        env=env,
        health_url=f"http://127.0.0.1:{http_port}/status",
        port=http_port,
        policies=policies,
        on_state_change=on_state_change,
    )
    ServiceRegistry.instance().register(svc)
    return svc


class RvcVcClient:
    """控制面客户端（供前端/其它模块调用）。全部回环 HTTP，异常带三要素。"""

    def __init__(self, http_port: int = None, config: Config = None):
        if http_port is None:
            http_port = int(
                (config or Config(os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "config.json"))).get("rvc_vc", "http_port") or HTTP_PORT_DEFAULT
            )
        self.base = f"http://127.0.0.1:{http_port}"

    def _get(self, path: str, timeout: int = 5):
        return json.loads(urllib.request.urlopen(
            f"{self.base}{path}", timeout=timeout).read())

    def _post(self, path: str, body: dict, timeout: int = 120):
        req = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{e} {detail}") from e

    def status(self):
        return self._get("/status")

    def devices(self):
        return self._get("/devices")

    def switch_model(self, pth_path: str, index_path: str = None,
                     index_rate: float = None):
        """事务热切换：失败抛 RuntimeError（含 problem/cause/fix），旧模型继续。"""
        return self._post("/switch", {
            "pth_path": pth_path.replace("\\", "/"),
            "index_path": (index_path or "").replace("\\", "/") or None,
            "index_rate": index_rate,
        })

    def start_stream(self, input_device=None, output_device=None):
        return self._post("/start", {"input_device": input_device,
                                     "output_device": output_device})

    def stop_stream(self):
        return self._post("/stop", {})
