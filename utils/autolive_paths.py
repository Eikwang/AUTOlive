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

# （S-2 py39 外部钉子已随 2026-11-07 迁移收尾退役——翻唱/训练冒烟统一项目内候选链）

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


# ----------------------------------------------------------------------
# 解释器副本优先候选链（T5，/autoplan main-20261106-173018）
# 顺序：config 覆盖键（按序、存在才采纳）→ 上次记忆 → 仓库副本 → 外部树兜底。
# 样板对齐 utils/edtalk_realtime/runtime.py resolve_interpreter（116-144）；
# 记忆文件独立（data/interpreter_memory.json，同款容错语义），edtalk 的
# data/edtalk_runtime.json 不动——Shape B（全量统一）登记 TODOS 二步收敛。
# ----------------------------------------------------------------------

_REPO_ROOT_FALLBACK = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _interpreter_memory_file() -> str:
    return os.path.join(_REPO_ROOT_FALLBACK, "data", "interpreter_memory.json")


def read_interpreter_memory() -> dict:
    """解释器记忆快照（缺失/损坏 → {}，不抛错——消费方降级候选链）。"""
    import json
    try:
        with open(_interpreter_memory_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_interpreter_memory(key: str, value: str) -> None:
    """服务拉起成功后记录实际使用的解释器（失败仅告警，不影响结果）。"""
    import json
    try:
        path = _interpreter_memory_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = read_interpreter_memory()
        data[key] = str(value)
        data["updated_at"] = Common().get_bj_time(0)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[interpreter] 记忆写入失败（不影响服务）：{e}")


def resolve_python_interpreter(config, *, keys, repo_rel: str,
                               external_default: str, memory_key: str) -> str:
    """解释器副本优先候选链（单函数参数化，A4 防第 4 处复制）。

    Args:
        config: 双形态 config（dict 或 Config）。
        keys: [(section, key), ...] config 覆盖键候选（按序，指向真实文件才采纳）。
        repo_rel: 仓库副本相对路径（如 runtime312/python.exe；不存在自然跳过）。
        external_default: 外部树兜底（EDTalk 兄弟安装/clone）。
        memory_key: data/interpreter_memory.json 的记忆键。

    Returns:
        第一个真实存在的绝对路径；全缺失返回空串（调用方三要素报错，不抛异常）。
    每次解析落 debug 日志（F-N2：防"静默回落外部路径"用户无感知）。
    """
    candidates = []
    for section, key in keys:
        v = config_get(config, section, key)
        if v and str(v).strip():
            candidates.append((f"config.{section}.{key}", str(v).strip()))
    mem = read_interpreter_memory().get(memory_key)
    if mem and str(mem).strip():
        candidates.append((f"memory[{memory_key}]", str(mem).strip()))
    try:
        from frontend.config.paths import get_base_path
        base = str(get_base_path())
    except Exception:
        base = _REPO_ROOT_FALLBACK
    candidates.append(("repo copy", os.path.join(base, repo_rel)))
    candidates.append(("external default", external_default))

    for source, cand in candidates:
        if cand and os.path.isfile(cand):
            logger.debug(f"[interpreter] {memory_key}: {source} → {cand}")
            return os.path.abspath(cand)
    logger.warning(
        f"[interpreter] {memory_key} 全候选缺失: "
        + "；".join(f"{s}={c}" for s, c in candidates))
    return ""


class KeyedConfigView:
    """dict → Config 变长键语义视图（2026-11-03 治理批次 erro6.log 实测修复）。

    UI 组件族（layout）全链持有页面 dict，而共享消费面（CoverQueue._policies、
    rvc_service build/ensure 的 hosted/http_port/policies 读取）按
    Config.get(*keys) 变长键语义设计——dict 直接传入会把节 dict 当值返回
    （dict.get 两参=default 语义，2026-10-16 config_get 同坑的进程内直调变体）。
    包装后 get(*keys) 走 config_get 逐键下钻，两形态同语义。"""

    def __init__(self, d):
        self._d = d

    def get(self, *keys, default=None):
        v = config_get(self._d, *keys)
        return v if v is not None else default


def as_keyed_config(config):
    """config 双形态归一：dict → KeyedConfigView；Config 对象原样透传。"""
    if isinstance(config, dict):
        return KeyedConfigView(config)
    return config


# 点歌模式歌曲音频根目录（/autoplan 2026-10-23 批次 R1：song_path 用户配置键
# 退役后的内部约定常量，经本函数锚定，不再暴露为用户配置参数）。
SONG_AUDIO_DIR_DEFAULT = "song"

# R13 迁移告警一次性标记（进程级，避免每歌曲解析刷屏）
_SONG_PATH_MIGRATION_WARNED = False


def song_audio_root(config=None, home: Optional[str] = None) -> str:
    """歌曲音频根目录（R1 内部约定）：autolive_home 锚定的 song/。
    config 双形态兼容（config_get）；仅接受 autolive_home 覆盖，无用户配置键。

    R13 一次性迁移：config choose_song 节若残留历史 song_path 键且非默认值，
    告警并采纳为本次运行根目录（提示用户将文件移至默认目录后删除该键）。
    """
    global _SONG_PATH_MIGRATION_WARNED
    root = None
    legacy = config_get(config, "choose_song", "song_path") if config is not None else None
    if legacy and str(legacy).strip() and str(legacy).strip() != SONG_AUDIO_DIR_DEFAULT:
        root = str(legacy).strip()
        if not _SONG_PATH_MIGRATION_WARNED:
            _SONG_PATH_MIGRATION_WARNED = True
            logger.warning(
                f"[choose_song] 检测到遗留 song_path 配置 '{legacy}'（本批已退役该用户配置键），"
                f"本次运行采纳为歌曲根目录；请将歌曲文件移至默认目录 '{SONG_AUDIO_DIR_DEFAULT}' 后删除该键")
    if root is None:
        base = home
        if not base and config is not None:
            base = config_get(config, "paths", "autolive_home")
        if not base:
            base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        root = os.path.join(base, SONG_AUDIO_DIR_DEFAULT)
    return os.path.abspath(root)


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
        # T5（2026-11-06 批次）：解释器走副本优先候选链——config paths 覆盖 →
        # 记忆 → 仓库副本（apps/gpt-sovits/runtime，X7 边界：clone 暂不复制，
        # 不存在自然跳过）→ runtime312-clone 外部兜底
        self.gpt_sovits_python: str = resolve_python_interpreter(
            config,
            keys=[("paths", KEY_GPT_SOVITS_PYTHON)],
            repo_rel=os.path.join("apps", "gpt-sovits", "runtime", "python.exe"),
            external_default=r"D:\AI\runtime312-clone\python.exe",
            memory_key="gpt_sovits_python",
        )
        # RVC 运行副本与训练/变声 runtime（S-1/S-2：默认 apps/rvc 副本+
        # runtime312 基准；T5+迁移批次（2026-11-06）：config paths 覆盖 → 记忆 →
        # 仓库根 runtime312（物理迁移产物）→ EDTalk 兄弟安装兜底（过渡期安全网）
        self.rvc_root: str = self._resolve(
            KEY_RVC_ROOT, os.path.join(self.autolive_home, "apps", "rvc"),
        )
        self.rvc_python: str = resolve_python_interpreter(
            config,
            keys=[("paths", KEY_RVC_PYTHON)],
            repo_rel=os.path.join("runtime312", "python.exe"),
            external_default=r"D:\AI\EDTalk\runtime312\python.exe",
            memory_key="rvc_python",
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


def ensure_config_backup(config_path: str = "config.json", backup_path: str = "config.json.bak") -> bool:
    """保证恢复出厂基线存在（/autoplan 批准批次 C6 项，TODOS #8）。

    factory（frontend/main.py）依赖 config.json.bak，仓库历史上无该文件时恢复出厂必失败。
    幂等：仅在备份不存在时从当前 config.json 固化一份"出厂基线"；已存在则保留
    （出厂语义=首次运行时的已知好配置，不随日常改动漂移）。双进程入口各自幂等调用，
    谁先启动谁固化。返回是否新建了备份。
    """
    import logging
    import shutil

    logger = logging.getLogger(__name__)
    if os.path.exists(backup_path):
        return False
    if not os.path.exists(config_path):
        logger.warning("config.json 不存在，跳过出厂基线固化")
        return False
    shutil.copy2(config_path, backup_path)
    logger.info(f"已固化恢复出厂基线：{backup_path}（首次运行快照）")
    return True
