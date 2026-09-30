"""config.local.toml 持久化存储（S2：设置面板后端）

- TOML 键映射表（round2 L1，与 load_toml_overrides 嵌套规则一致）：
  ws_port→[ws]port、web_port→[web]port、bus_*→[bus]*；其余平铺键
- 写入用 tomlkit（保留注释与键序；平铺键必须位于首个节头之前——round3 C-3）
- 读写均为 fail-open：损坏文件按空配置处理
"""

import os
from typing import Any, Dict, Optional

import tomlkit
from loguru import logger

CONFIG_FILE_NAME = "config.local.toml"

#: Settings 字段名 → TOML 位置（None=顶层平铺键）
FIELD_TO_TOML: Dict[str, Optional[tuple]] = {
    "ws_port": ("ws", "port"),
    "web_port": ("web", "port"),
    "bus_ring_capacity": ("bus", "ring_capacity"),
    "bus_dedup_window_seconds": ("bus", "dedup_window_seconds"),
    "max_rooms": None,
    "fast_retry_max": None,
    "slow_retry_cap_seconds": None,
    "session_lifetime_seconds": None,
}

#: 白名单（可经 /api/config 修改的字段）
CONFIG_WHITELIST = list(FIELD_TO_TOML.keys())


def default_config_path() -> str:
    """config.local.toml 默认路径（仓库根/工作目录）"""
    return os.path.join(os.getcwd(), CONFIG_FILE_NAME)


def load_overrides(path: str) -> Dict[str, Any]:
    """读取 config.local.toml → Settings 字段名→值（白名单内）"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as fh:
            doc = tomlkit.load(fh)
    except Exception as e:
        logger.warning(f"config.local.toml 解析失败，按空配置处理: {e}")
        return {}

    overrides: Dict[str, Any] = {}
    for field, toml_loc in FIELD_TO_TOML.items():
        try:
            if toml_loc is None:
                if field in doc:
                    overrides[field] = doc[field]
            else:
                section, key = toml_loc
                if section in doc and key in doc[section]:
                    overrides[field] = doc[section][key]
        except Exception:
            continue
    return overrides


def save_fields(path: str, fields: Dict[str, Any]) -> None:
    """把 Settings 字段值写入 config.local.toml（保留既有注释/未知键）

    平铺键位置陷阱（round3 C-3）：TOML 顶层裸键必须位于首个节头之前，
    否则被解析进前节。使用 tomlkit 保留结构：已有键原位更新，新平铺键
    insert 到文档最前，节键走节内赋值。
    """
    doc = tomlkit.document()
    if os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                doc = tomlkit.load(fh)
        except Exception as e:
            logger.warning(f"config.local.toml 读取失败，重建文件: {e}")
            doc = tomlkit.document()

    flat_updates: Dict[str, Any] = {}
    for field, value in fields.items():
        toml_loc = FIELD_TO_TOML.get(field)
        if toml_loc is None:
            if field in doc:
                doc[field] = value  # 已有平铺键：原位更新
            else:
                flat_updates[field] = value  # 新平铺键：稍后插到最前
        else:
            section, key = toml_loc
            if section not in doc:
                doc[section] = tomlkit.table()
            doc[section][key] = value

    # 新平铺键必须位于首个节头之前（TOML 作用域）：通过 body 重建实现
    if flat_updates:
        import tomlkit.items as tk_items
        new_doc = tomlkit.document()
        for key, value in flat_updates.items():
            new_doc[key] = value
        for k, v in doc.body:
            if isinstance(k, tk_items.Key):
                new_doc.append(k, v)
            else:
                new_doc.append(v)  # 表头/表体作为整体元素
        doc = new_doc

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(tomlkit.dumps(doc))
    os.replace(tmp, path)  # 原子替换
    logger.info(f"config.local.toml saved: {list(fields.keys())}")
