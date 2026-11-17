"""音频条目优先级单一判定（2026-11-05 编排优化批次 T2/e2）。

priority_override 是编排音频通道的显式权重载体（O7 编排插队=500）。
有效权重判定收敛到本模块——playback_manager / queue_manager /
builtin_play_center 三处统一调用（防三份拷贝漂移，eng Section 1 红旗 6
「一事两法」根治点）。

2026-11-15 批次：999 特殊机制（暂停/续播/让位/单实例）已整体退役，
reread_top_priority 改映射 500（保持最高顺位，脱离 999 域）；有效权重
即纯排序权重，barge-in 打断/泛化让位语义见 playback_manager 与
queue_manager。

判定语义：
- 有效权重 = priority_override（可整型化时）优先，否则 filter.priority_mapping
  按条目 type 取值；均不可得 = None（未识别）。
"""

from __future__ import annotations


def entry_priority(item, priority_mapping):
    """条目有效权重：priority_override 优先于类型映射表；不可得返回 None。

    与 queue_manager._entry_priority / builtin_play_center._item_priority 的
    既有语义逐点保真：override 存在但不可整型化时回落类型映射；未识别 type
    返回 None（队尾语义由各调用方承担）。
    """
    ov = item.get("priority_override") if isinstance(item, dict) else None
    if ov is not None:
        try:
            return int(ov)
        except (TypeError, ValueError):
            pass
    if not isinstance(priority_mapping, dict):
        return None
    p = priority_mapping.get(item.get("type") if isinstance(item, dict) else None)
    if p is None:
        return None
    try:
        return int(p)
    except (TypeError, ValueError):
        return None
