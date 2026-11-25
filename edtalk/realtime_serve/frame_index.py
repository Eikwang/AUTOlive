# -*- coding: utf-8 -*-
"""段显示帧序映射（2026-11-24 pingpong 批次，纯函数单源）。

从 render_loop 抽出：本模块零第三方重依赖（cv2/torch 之外的可测纯逻辑），
服务端渲染循环与守卫测试共用单源。"""


def pingpong_frame_idx(pos: int, total: int) -> int:
    """帧级 pingpong 三角波映射（2026-11-24 批次）。

    显示序位置 pos（单调递增）→ 当前应显示的帧序号：
      周期 period = 2*total − 2（端点不重复：0…total-1 正放、total-2…1 倒放）；
      p = pos % period；p < total → 正放段 idx=p；否则倒放段 idx=period−p。
    序列示例（total=5）：0,1,2,3,4,3,2,1,0,1,2,…——正放↔倒放交界同帧衔接、
    周期回绕处 1→0 自然连续，首尾边界零跳变（对比 % total 锯齿折返的尾帧
    跳回首帧硬切=用户实测「A-B 循环」生硬感）。

    口型不受影响：口型系数由音频帧锁定单独驱动（_next_lip_frame），本映射
    只改身体帧选取——唱歌/说话期间倒放的是肢体动作，嘴型照常正确。

    total≤2（周期不成立）退 pos % total；pos 负值（理论不达，显示序时钟
    单调）防御性取模归一。
    """
    if total <= 2:
        return max(0, pos) % max(1, total)
    period = 2 * total - 2
    p = pos % period
    if p < 0:
        p += period
    return p if p < total else period - p
