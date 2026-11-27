# -*- coding: UTF-8 -*-
"""统一资源路径注册中心（发行改造阶段 A1，2026-11-22）。

职责：所有模型/资产/运行时目录与子服务解释器的**唯一解析入口**。
- 目录布局统一收敛到 ``models/`` 根（发行包与开发机同构）：
    models/gpt_sovits/   GPT-SoVITS 底模（物理实体；apps 副本内经 junction 回指）
    models/rvc/          RVC 变声模型
    models/asr/          SenseVoice + VAD（原 models/iic 更名）
    models/uvr5/         MDXNet 等人声分离模型（原 models/mdxnet 更名）
    models/edtalk/       EDTalk 底模权重（原 edtalk/ckpts；edtalk/ckpts 为 junction 回指）
    models/tuber/{角色}/ 数字人角色包（原根级 tuber/ 平移）
- 解释器候选链收编三处分叉（EDTalk/fastrag 链、GPT-SoVITS 链、RVC/翻唱链），
  utils/autolive_paths.py 与 utils/edtalk_realtime/runtime.py 改为薄委托。

覆盖项语义：config.json ``paths`` 节同名键（非空才采纳）优先于默认布局；
``autolive_home`` 键整体平移资源根（frozen 下默认即 exe 目录）。

约束：本模块不得在 import 期做任何 IO/配置读取（全部惰性）；对
frontend.config.paths.get_base_path 的引用一律函数内延迟 import（主系统
进程 import frontend 包，webui/Nuitka exe 亦如此——保持既有单向依赖：
paths.py（阶段 C）反向薄委托本模块时经函数级 import 避免环）。
"""
import json
import os
from pathlib import Path
from typing import Optional

from utils.my_log import logger

# 兜底外部树（过渡期安全网：源已迁出后自然失效，发行包内永不命中）
_EXTERNAL_EDTALK_RUNTIME = r"D:\AI\EDTalk\runtime312\python.exe"
_EXTERNAL_RUNTIME312_CLONE = r"D:\AI\runtime312-clone\python.exe"


# ----------------------------------------------------------------------
# 基根解析
# ----------------------------------------------------------------------

def home() -> Path:
    """资源根目录：config paths.autolive_home 覆盖 → get_base_path()。

    frozen（Nuitka standalone）下 get_base_path() = exe 所在目录，
    发行包内 models/、song/ 等均锚定于此。"""
    base = Path(_base())
    ov = _paths_overrides().get("autolive_home")
    if ov and str(ov).strip():
        return Path(str(ov).strip()).resolve()
    return base


def _base() -> Path:
    try:
        from frontend.config.paths import get_base_path
        return Path(get_base_path()).resolve()
    except Exception:
        # 独立脚本/主系统早期阶段：仓库根兜底（utils/ 的上一级）
        return Path(__file__).resolve().parent.parent


# config.json paths 节覆盖读取（mtime 缓存：同一文件版本只解析一次）
_OVERRIDE_CACHE: dict = {"mtime": None, "data": {}}


def _paths_overrides() -> dict:
    p = _base() / "config.json"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = None
    if _OVERRIDE_CACHE["mtime"] != mtime:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            _OVERRIDE_CACHE["data"] = data.get("paths") or {}
        except Exception:
            _OVERRIDE_CACHE["data"] = {}
        _OVERRIDE_CACHE["mtime"] = mtime
    return _OVERRIDE_CACHE["data"]


def _resolve_dir(override_key: str, default: Path) -> Path:
    """paths 节覆盖键优先（非空才采纳），否则默认布局。"""
    ov = _paths_overrides().get(override_key)
    if ov and str(ov).strip():
        return Path(str(ov).strip()).resolve()
    return default


# ----------------------------------------------------------------------
# 模型资产目录（统一 models/ 根）
# ----------------------------------------------------------------------

def models_root() -> Path:
    """模型资产根：models/（用户可直接管理）。"""
    return _resolve_dir("models_root", home() / "models")


def gsv_pretrained_dir() -> Path:
    """GPT-SoVITS 底模目录：models/gpt_sovits/。

    物理实体在本目录；apps/gpt-sovits/GPT_SoVITS/pretrained_models 为
    目录 junction 回指（GPT-SoVITS 上游脚本的相对路径约定不受影响）。"""
    return _resolve_dir("gpt_sovits_models", models_root() / "gpt_sovits")


def rvc_models_dir() -> Path:
    """RVC 变声模型目录：models/rvc/。"""
    return _resolve_dir("rvc_models", models_root() / "rvc")


def asr_models_dir() -> Path:
    """ASR 模型目录：models/asr/（SenseVoice + VAD，原 models/iic）。"""
    return _resolve_dir("asr_models", models_root() / "asr")


def uvr5_models_dir() -> Path:
    """人声分离（MDXNet）模型目录：models/uvr5/（原 models/mdxnet）。"""
    return _resolve_dir("uvr5_models", models_root() / "uvr5")


def edtalk_ckpts_dir() -> Path:
    """EDTalk 底模权重目录：models/edtalk/（原 edtalk/ckpts）。

    edtalk/ckpts 为目录 junction 回指——EDTalk 训练/推理脚本内部
    "ckpts/Audio2Lip.pt" 等相对路径约定不受迁移影响。"""
    return _resolve_dir("edtalk_ckpts", models_root() / "edtalk")


def tuber_root(role: Optional[str] = None) -> Path:
    """数字人角色库根：models/tuber/；带 role 时为角色包目录 models/tuber/{role}。"""
    root = _resolve_dir("tuber_root", models_root() / "tuber")
    return root / str(role) if role else root


# ----------------------------------------------------------------------
# 其余资源目录（不迁移，仅注册）
# ----------------------------------------------------------------------

def apps_root(name: Optional[str] = None) -> Path:
    """外部工程副本根：apps/（gpt-sovits、rvc、aicovergen）；带 name 为子目录。"""
    root = home() / "apps"
    return root / str(name) if name else root


def edtalk_dir() -> Path:
    """EDTalk 运行副本根：edtalk/（realtime_serve、ckpt_models 权威副本所在）。"""
    return home() / "edtalk"


def song_audio_root() -> Path:
    """歌曲音频根目录：song/（点歌模式内部约定，R1 无用户配置键）。"""
    return _resolve_dir("song_root", home() / "song")


def log_dir() -> Path:
    """日志目录：log/（自动创建）。"""
    d = home() / "log"
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_dir() -> Path:
    """输出目录：out/（自动创建）。"""
    d = home() / "out"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    """运行时状态目录：data/（解释器记忆、edtalk_runtime 等）。"""
    d = home() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ----------------------------------------------------------------------
# 解释器候选链（三链收编：Shape B 全量统一）
# ----------------------------------------------------------------------

def _repo_root_fallback() -> str:
    return str(_base())


def read_interpreter_memory() -> dict:
    """解释器记忆快照（data/interpreter_memory.json；缺失/损坏 → {}，不抛错）。"""
    try:
        f = data_dir() / "interpreter_memory.json"
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_interpreter_memory(key: str, value: str) -> None:
    """服务拉起成功后记录实际使用的解释器（失败仅告警，不影响结果）。"""
    try:
        f = data_dir() / "interpreter_memory.json"
        data = read_interpreter_memory()
        data[key] = str(value)
        from utils.common import Common
        data["updated_at"] = Common().get_bj_time(0)
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[interpreter] 记忆写入失败（不影响服务）：{e}")


def _config_get(config, *keys):
    """双形态 config 读取（dict 逐键下钻 / Config 变长键 get）——与
    utils/autolive_paths.config_get 同语义，注册中心自带避免反向依赖。"""
    if config is None:
        return None
    if isinstance(config, dict):
        node = config
        for k in keys:
            if not isinstance(node, dict):
                return None
            node = node.get(k)
        return node
    return config.get(*keys)


def _pick_first(candidates) -> str:
    """候选链求值：第一个真实存在的绝对路径；全缺失返回空串并告警。"""
    for source, cand in candidates:
        if cand and os.path.isfile(cand):
            logger.debug(f"[interpreter] {source} → {cand}")
            return os.path.abspath(cand)
    logger.warning(
        "[interpreter] 全候选缺失: "
        + "；".join(f"{s}={c}" for s, c in candidates))
    return ""


def runtime_python(config=None) -> str:
    """runtime312 解释器链（EDTalk/fastrag/主系统共用）。

    候选序：旧配置键（edtalk_realtime.interpreter_path 存量，存在才采纳）→
    data/edtalk_runtime.json 记忆（上次实际使用）→ <home>/runtime312/python.exe
    → EDTalk 兄弟安装兜底（过渡期）。"""
    candidates = []
    legacy = _config_get(config, "edtalk_realtime", "interpreter_path")
    if legacy and str(legacy).strip():
        candidates.append(("config.edtalk_realtime.interpreter_path", str(legacy).strip()))
    try:
        rt = json.loads((data_dir() / "edtalk_runtime.json").read_text(encoding="utf-8"))
        remembered = str(rt.get("interpreter", "") or "").strip()
    except Exception:
        remembered = ""
    if remembered:
        candidates.append(("memory[edtalk_runtime]", remembered))
    candidates.append(("repo copy", str(home() / "runtime312" / "python.exe")))
    candidates.append(("external default", _EXTERNAL_EDTALK_RUNTIME))
    return _pick_first(candidates)


def gpt_sovits_python(config=None) -> str:
    """GPT-SoVITS 解释器链：config paths.gpt_sovits_python →
    data/interpreter_memory.json[gpt_sovits_python] →
    <home>/apps/gpt-sovits/runtime/python.exe（发行包内为首启 junction →
    runtime312）→ <home>/runtime312/python.exe（2026-11-22 新增终级回退：
    runtime312 已补齐 GSV 全部依赖 opencc/py-key-value-aio，实测等价
    runtime312-clone）→ runtime312-clone 外部兜底（过渡期）。"""
    candidates = []
    ov = _config_get(config, "paths", "gpt_sovits_python")
    if ov and str(ov).strip():
        candidates.append(("config.paths.gpt_sovits_python", str(ov).strip()))
    mem = read_interpreter_memory().get("gpt_sovits_python")
    if mem and str(mem).strip():
        candidates.append(("memory[gpt_sovits_python]", str(mem).strip()))
    candidates.append(("repo copy", str(apps_root("gpt-sovits") / "runtime" / "python.exe")))
    candidates.append(("runtime312 fallback", str(home() / "runtime312" / "python.exe")))
    candidates.append(("external default", _EXTERNAL_RUNTIME312_CLONE))
    return _pick_first(candidates)


def rvc_python(config=None) -> str:
    """RVC/翻唱解释器链：config paths.rvc_python →
    data/interpreter_memory.json[rvc_python] → <home>/runtime312/python.exe →
    EDTalk 兄弟安装兜底（过渡期）。"""
    candidates = []
    ov = _config_get(config, "paths", "rvc_python")
    if ov and str(ov).strip():
        candidates.append(("config.paths.rvc_python", str(ov).strip()))
    mem = read_interpreter_memory().get("rvc_python")
    if mem and str(mem).strip():
        candidates.append(("memory[rvc_python]", str(mem).strip()))
    candidates.append(("repo copy", str(home() / "runtime312" / "python.exe")))
    candidates.append(("external default", _EXTERNAL_EDTALK_RUNTIME))
    return _pick_first(candidates)
