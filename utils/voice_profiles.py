# -*- coding: utf-8 -*-
"""
声音档案 voice_profiles 数据层（P2-4）

职责：config.json `voice_profiles` 节的增删查改（原子写入，E-4 模式）。
- 档案 ID = 数据集目录名 slug 化、冲突加序号（DX-7）；改显示名不改 ID
- 结构（DX-6/H-1）：{id: {name, note, gpt_model, sovits_model,
  rvc_model, rvc_index, ref_audio, prompt_text, prompt_lang}}
- 训练页末步写入（P2-3）；TTS/变声/翻唱三端选择器读取（CEO 叙事）

单写者原则（E-4）：本模块是 voice_profiles 节唯一写者，tempfile+os.replace 原子替换。
"""
import json
import os
import re
import tempfile
from typing import Dict, Optional

from utils.my_log import logger

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
)

_PROFILE_FIELDS = (
    "name", "note", "gpt_model", "sovits_model",
    "rvc_model", "rvc_index", "ref_audio", "prompt_text", "prompt_lang",
)


def _slugify(name: str) -> str:
    """数据集目录名 → 安全 ID（保留中英文数字下划线，空格转下划线）。"""
    slug = re.sub(r"[^\w一-鿿-]+", "_", name.strip()).strip("_")
    return slug or "voice"


def load_profiles(config_path: str = CONFIG_PATH) -> Dict[str, dict]:
    """读取全部声音档案。文件缺失/损坏 → 返回空 dict（不抛，训练链可继续）。"""
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        profiles = data.get("voice_profiles")
        return profiles if isinstance(profiles, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"[voice_profiles] 读取失败（返回空，保留原文件不动）: {e}")
        return {}


def get_profile(profile_id: str, config_path: str = CONFIG_PATH) -> Optional[dict]:
    return load_profiles(config_path).get(profile_id)


def upsert_profile(profile: dict, config_path: str = CONFIG_PATH) -> str:
    """写入/更新档案，返回档案 ID。

    - 无 id 字段 → 由 name slug 生成，冲突追加序号（DX-7）
    - 有 id → 原位更新（改显示名不改 ID）
    """
    pid = profile.get("id") or ""
    if not pid:
        base = _slugify(profile.get("name") or "voice")
        profiles = load_profiles(config_path)
        pid, n = base, 1
        while pid in profiles:
            n += 1
            pid = f"{base}_{n}"
    clean = {k: profile.get(k, "") for k in _PROFILE_FIELDS}
    clean["name"] = clean["name"] or pid
    _atomic_update(config_path, lambda data: data.setdefault("voice_profiles", {}).update({pid: clean}))
    logger.info(f"[voice_profiles] 已写入档案 {pid}（{clean['name']}）")
    return pid


def delete_profile(profile_id: str, config_path: str = CONFIG_PATH) -> bool:
    profiles = load_profiles(config_path)
    if profile_id not in profiles:
        return False
    _atomic_update(config_path, lambda data: data.get("voice_profiles", {}).pop(profile_id, None))
    return True


def _atomic_update(config_path: str, mutate):
    """E-4：临时名+原子替换；只动 voice_profiles 节，其余字节结构重序列化。"""
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    mutate(data)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(config_path) or ".", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp, config_path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
