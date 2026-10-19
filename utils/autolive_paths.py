# -*- coding: UTF-8 -*-
"""
AUTOlive 路径解析模块（P1-2）

职责：
1. 统一解析外部项目路径（GPT-SoVITS 等），消除 D:\\AI 硬编码——所有外部路径
   经 config `paths` 节配置，缺省值仅作本机兜底。
2. 为托管子进程计算 PYTHONPATH 注入清单（users.pth 收编：原由
   runtime312-clone/Lib/site-packages/users.pth 注入的 6 条 GPT-SoVITS 路径，
   改由启动器按本模块计算后以 env 注入，users.pth 不再被依赖）。

三层配置关系（H-1）：paths（外部路径）→ audio_integration.policies（行为参数）
→ voice_profiles（声音档案数据节）。本模块只负责 paths 层。
"""
import os
from typing import Dict, List, Optional

from utils.common import Common
from utils.my_log import logger
from utils.config import Config


# config `paths` 节的键名（DX-6：键名固定，示例片段见 docs/开发环境搭建.md）
KEY_AUTOLIVE_HOME = "autolive_home"
KEY_GPT_SOVITS_ROOT = "gpt_sovits_root"
KEY_GPT_SOVITS_PYTHON = "gpt_sovits_python"
KEY_RVC_ROOT = "rvc_root"
KEY_RVC_PYTHON = "rvc_python"
KEY_AICOVERGEN_ROOT = "aicovergen_root"

# S-2 显式钉住（/autoplan 2026-10-14 outside 发现 3）：翻唱子进程解释器默认=
# 外部树 py3.9 嵌入式 runtime（AICoverGen 依赖全齐的唯一 runtime），禁止从
# rvc_root 派生（apps/rvc 根翻转后派生值指向不存在的副本内 runtime）。
_RVC_COVER_PYTHON_DEFAULT = r"D:\AI\RVC20240604Nvidia\runtime\python.exe"

# users.pth 收编（CEO-P1-2）：GPT-SoVITS 子进程需要的 PYTHONPATH 注入清单，
# 相对 gpt_sovits_root。与原 users.pth 的 6 条路径一一对应。
_GPT_SOVITS_PYTHONPATH_SUBDIRS = [
    "",  # GPT-SoVITS 根目录本身
    "GPT_SoVITS",
    "GPT_SoVITS/BigVGAN",
    "tools",
    "tools/asr",
    "tools/uvr5",
]


def config_get(config, *keys):
    """双形态 config 读取（2026-10-16 实测修复）：前端进程持有 dict、主进程持有
    Config——dict.get 的第二参数是"默认值"而非"第二键"，dict.get("paths",
    "rvc_python") 会把整个 paths 节当值返回，str(dict) 被拼进路径后全部资产
    预检误报缺失。dict 走逐键下钻，Config 走变长键 get(*keys)，两形态同语义。"""
    if isinstance(config, dict):
        node = config
        for k in keys:
            if not isinstance(node, dict):
                return None
            node = node.get(k)
        return node
    return config.get(*keys)


class AutolivePaths:
    """外部路径统一解析器。所有路径先取 config `paths` 节，缺省回落本机布局。"""

    def __init__(self, config: Config):
        self.config = config
        # AUTOlive 根目录：config 未配置时以仓库根（utils/ 的上一级）兜底
        self.autolive_home: str = self._resolve(
            KEY_AUTOLIVE_HOME,
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        )
        # GPT-SoVITS 运行副本与专用 runtime（S-1：默认 apps/ 唯一副本，EDTalk/apps 范式；
        # config 可覆盖回外部树，junction 资产由 copy 脚本建立）
        self.gpt_sovits_root: str = self._resolve(
            KEY_GPT_SOVITS_ROOT,
            os.path.join(self.autolive_home, "apps", "gpt-sovits"),
        )
        self.gpt_sovits_python: str = self._resolve(
            KEY_GPT_SOVITS_PYTHON, r"D:\AI\runtime312-clone\python.exe"
        )
        # RVC 运行副本与训练/变声 runtime（S-1/S-2：默认 apps/rvc 副本+
        # EDTalk\runtime312 基准——RVC fork 本地改造全针对该环境，py39 嵌入式
        # 已被 fork 弃用；config 可覆盖回外部树）
        self.rvc_root: str = self._resolve(
            KEY_RVC_ROOT, os.path.join(self.autolive_home, "apps", "rvc"),
        )
        self.rvc_python: str = self._resolve(
            KEY_RVC_PYTHON, r"D:\AI\EDTalk\runtime312\python.exe",
        )
        # AICoverGen 运行副本根（C-4 收敛：cover_worker 的模型共享目录基根）
        self.aicovergen_root: str = self._resolve(
            KEY_AICOVERGEN_ROOT, os.path.join(self.autolive_home, "apps", "aicovergen"),
        )

    def _resolve(self, key: str, default: str) -> str:
        """从 config paths 节读值；空值/缺键回落默认并记日志。"""
        value = config_get(self.config, "paths", key)
        if not value or not str(value).strip():
            logger.info(f"[paths] 未配置 paths.{key}，使用默认值: {default}")
            return default
        return os.path.abspath(str(value).strip())

    def gpt_sovits_pythonpath_entries(self) -> List[str]:
        """计算 GPT-SoVITS 子进程的 PYTHONPATH 注入清单（users.pth 收编）。"""
        entries = []
        for sub in _GPT_SOVITS_PYTHONPATH_SUBDIRS:
            p = os.path.join(self.gpt_sovits_root, sub)
            if os.path.isdir(p):
                entries.append(p)
            else:
                logger.warning(f"[paths] PYTHONPATH 注入目录不存在，跳过: {p}")
        return entries

    def subprocess_env(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """构造托管子进程环境：继承当前环境 + PYTHONPATH 注入 + 上游训练约定变量 + 附加变量。

        上游 env 约定（对齐 D:\\AI\\GPT-SoVITS\\开始训练.bat）：HF 镜像/缓存指向副本内
        hf_download（S-1 后零外部树写入）、UTF-8、torch 2.9+ DDP 兼容开关。
        """
        env = os.environ.copy()
        entries = self.gpt_sovits_pythonpath_entries()
        if entries:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = os.pathsep.join(
                filter(None, [existing] + entries)
            )
        hf_home = os.path.join(self.gpt_sovits_root, "hf_download")
        tf_cache = os.path.join(self.gpt_sovits_root, "tf_download")
        os.makedirs(hf_home, exist_ok=True)
        env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        env["HF_HOME"] = hf_home
        env["TRANSFORMERS_CACHE"] = tf_cache
        env["PYTHONUTF8"] = "1"
        env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
        if extra:
            env.update(extra)
        return env

    def default_tts_infer_yaml(self) -> str:
        """AUTOlive 管理的 tts_infer yaml 缺省位置（Step 0 探活已验证可用）。"""
        return os.path.join(self.autolive_home, "config", "tts_infer_probe.yaml")
