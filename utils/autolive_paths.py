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


class AutolivePaths:
    """外部路径统一解析器。所有路径先取 config `paths` 节，缺省回落本机布局。"""

    def __init__(self, config: Config):
        self.config = config
        # AUTOlive 根目录：config 未配置时以仓库根（utils/ 的上一级）兜底
        self.autolive_home: str = self._resolve(
            KEY_AUTOLIVE_HOME,
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        )
        # GPT-SoVITS 项目根与专用 runtime（Step 0 验证：runtime312-clone 可启动 api_v2）
        self.gpt_sovits_root: str = self._resolve(
            KEY_GPT_SOVITS_ROOT, r"D:\AI\GPT-SoVITS"
        )
        self.gpt_sovits_python: str = self._resolve(
            KEY_GPT_SOVITS_PYTHON, r"D:\AI\runtime312-clone\python.exe"
        )

    def _resolve(self, key: str, default: str) -> str:
        """从 config paths 节读值；空值/缺键回落默认并记日志。"""
        value = self.config.get("paths", key)
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
        """构造托管子进程环境：继承当前环境 + PYTHONPATH 注入 + 附加变量。"""
        env = os.environ.copy()
        entries = self.gpt_sovits_pythonpath_entries()
        if entries:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = os.pathsep.join(
                filter(None, [existing] + entries)
            )
        if extra:
            env.update(extra)
        return env

    def default_tts_infer_yaml(self) -> str:
        """AUTOlive 管理的 tts_infer yaml 缺省位置（Step 0 探活已验证可用）。"""
        return os.path.join(self.autolive_home, "config", "tts_infer_probe.yaml")
