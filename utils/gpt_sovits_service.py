# -*- coding: UTF-8 -*-
"""
GPT-SoVITS api_v2 托管服务（P1-1）

基于通用 ManagedProcess 的 GPT-SoVITS 特化：
- 命令：{gpt_sovits_python} api_v2.py -p {port} -a 127.0.0.1 -c {yaml}
  （Step 0 已验证：api_v2 无 -s/-g 参数，模型权重经 -c yaml 的绝对路径指定；
  runtime312-clone 环境已补齐依赖并可 10s 就绪、真实合成 200）
- E-1：绑定地址硬编码 127.0.0.1，不从 config 读取
- S7：就绪后发一条预热合成请求（复用 config gpt_sovits 节的参考音频参数），
  防止首条直播消息吃冷启动
- F11：gpt_sovits.hosted=false 时本服务不拉起，外部进程由启动器挂入状态条
"""
import json
import urllib.request
from typing import Optional

from utils.autolive_paths import AutolivePaths
from utils.config import Config
from utils.my_log import logger
from utils.service_orchestrator import DEFAULT_POLICIES, ManagedProcess, ServiceRegistry


def build_gpt_sovits_service(
    config: Config, paths: AutolivePaths, on_state_change=None
) -> Optional[ManagedProcess]:
    """
    构造（不启动）GPT-SoVITS 托管服务实例。

    config 键（gpt_sovits 节）：
    - hosted: 是否由系统托管拉起（默认 true，F11 逃逸口设 false）
    - api_ip_port: 对外服务地址（默认 http://127.0.0.1:9880）
    - tts_infer_yaml: api_v2 的 -c 配置文件（默认 AUTOlive/config/tts_infer_probe.yaml）

    返回 None 表示 hosted=false（外部进程模式，调用方仅注册状态条占位）。
    """
    hosted = config.get("gpt_sovits", "hosted")
    if hosted is None:
        hosted = True  # 默认 true（DX-2 裁决：config 新增托管开关，默认托管）
    if not hosted:
        logger.info("[gpt-sovits] gpt_sovits.hosted=false，跳过托管拉起（外部进程模式）")
        return None

    api_ip_port = config.get("gpt_sovits", "api_ip_port") or "http://127.0.0.1:9880"
    port = int(api_ip_port.rsplit(":", 1)[-1])
    yaml_path = config.get("gpt_sovits", "tts_infer_yaml") or paths.default_tts_infer_yaml()

    # policies 覆盖：audio_integration.policies 节（DX-3 键化）
    policies = dict(DEFAULT_POLICIES)
    policies["port"] = port
    custom = config.get("audio_integration", "policies") or {}
    policies.update({k: v for k, v in custom.items() if isinstance(v, (int, float))})

    command = [
        paths.gpt_sovits_python,
        "api_v2.py",
        "-p", str(port),
        "-a", "127.0.0.1",  # E-1：绑定地址硬编码回环，不从 config 读取
        "-c", yaml_path,
    ]

    def warmup(svc: ManagedProcess):
        """CEO-S7 预热：就绪后发一条最小合成请求，触发模型全链路加载。"""
        ref = config.get("gpt_sovits", "ref_audio_path") or ""
        prompt_text = config.get("gpt_sovits", "prompt_text") or ""
        prompt_lang = config.get("gpt_sovits", "prompt_language") or "zh"
        if not ref:
            logger.info("[gpt-sovits] 未配置参考音频，跳过预热")
            return
        body = json.dumps(
            {
                "text": "预热。",
                "text_lang": "zh",
                "ref_audio_path": ref.replace("\\", "/"),
                "prompt_text": prompt_text,
                "prompt_lang": prompt_lang,
                "streaming_mode": False,
            }
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/tts",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=180)
            logger.info("[gpt-sovits] 预热合成完成，模型已全链路加载")
        except Exception as e:
            # 预热失败不影响服务健康（首次真实请求会再次触发加载）
            logger.warning(f"[gpt-sovits] 预热请求失败（不阻塞）: {e}")

    svc = ManagedProcess(
        name="gpt-sovits",
        command=command,
        cwd=paths.gpt_sovits_root,
        env=paths.subprocess_env(),
        health_url=f"http://127.0.0.1:{port}/docs",
        port=port,
        policies=policies,
        on_state_change=on_state_change,
        warmup=warmup,
    )
    ServiceRegistry.instance().register(svc)
    return svc
