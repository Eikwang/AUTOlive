# -*- coding: utf-8 -*-
"""
二级菜单启用开关映射（batch2 任务6 / D-B1）

tab_key → 该功能"启用"开关的业务 config 路径（tuple 逐级键）。
来源：switch-scout 普查（2026-09-23）+ batch2 三轮审查逐条实测核验。
语义：开 = 运行系统执行该功能逻辑；关 = 跳过（按事件读取类实时生效，
调度门控类重启系统后生效——见计划 E-B1 生效时点表）。

不在本表中的 tab_key = 无页面级启用开关（二级菜单不渲染开关）。
"""
from typing import Dict, Tuple

ENABLE_PATHS: Dict[str, Tuple[str, ...]] = {
    "audio_play": ("play_audio", "enable"),
    "web_captions_printer": ("web_captions_printer", "enable"),
    "log_config": ("captions", "enable"),
    "svc": ("so_vits_svc", "enable"),
    "image_recognition": ("image_recognition", "enable"),
    "trends_copywriting": ("trends_copywriting", "enable"),
    "sd_config": ("sd", "enable"),
    "filter_dedup": ("filter", "limited_time_deduplication", "enable"),
    "blacklist": ("filter", "blacklist", "enable"),
    "read_comment": ("read_comment", "enable"),
    "idle_time_task": ("idle_time_task", "enable"),
    "custom_cmd": ("custom_cmd", "enable"),
    "assistant_anchor": ("assistant_anchor", "enable"),
    "integral": ("integral", "enable"),
    "translate": ("translate", "enable"),
    "choose_song": ("choose_song", "enable"),
    "search_online": ("search_online", "enable"),
    "key_mapping": ("key_mapping", "enable"),
    "luoxi": ("luoxi_project", "Live_Comment_Assistant", "enable"),
    "local_dir_endpoint": ("webui", "local_dir_to_endpoint", "enable"),
    "trends_config": ("trends_config", "enable"),
}


def validate_enable_paths(config: dict) -> list:
    """校验所有路径都能在 config 中解析（F11：漂移时启动即响）。

    Returns:
        缺失路径的描述列表（空列表 = 全部通过）。注意：路径"存在但值为
        False"是合法状态；只有键链断掉才算缺失。
    """
    missing = []
    for tab_key, path in ENABLE_PATHS.items():
        node = config
        for key in path[:-1]:
            if not isinstance(node, dict) or key not in node:
                missing.append(f"{tab_key} → {'.'.join(path)}（中间键 {key} 缺失）")
                break
            node = node[key]
        else:
            if isinstance(node, dict) and path[-1] not in node:
                missing.append(f"{tab_key} → {'.'.join(path)}（叶子键缺失）")
    return missing
