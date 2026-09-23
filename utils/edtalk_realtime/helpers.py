# -*- coding: utf-8 -*-
"""
EDTalk 集成全局判定帮助函数

统一"驱动类型 + enable 开关"双键判定（Eng A3：四处使用点共用一个函数，
杜绝条件漂移）：
- utils/audio/synthesis_manager.py（音频分流）
- utils/audio/playback_manager.py（跳过本地播放，两处）
- frontend/main.py start_programs()（服务拉起）
- frontend/ui/tabs/visual_body.py（状态矩阵渲染）
"""


def is_edtalk_active(config) -> bool:
    """判断 EDTalk 实时推理模式是否处于激活状态。

    Args:
        config: Settings/Config 配置对象（提供 .get(*keys) 接口）或普通 dict。

    Returns:
        True = 驱动类型为 edtalk_realtime 且 edtalk_realtime.enable 为真。
        此时：运行系统会拉起 realtime_serve；音频走"本地 TTS + 推送 EDTalk"。
    """
    try:
        if config.get("visual_body") != "edtalk_realtime":
            return False
        return bool(config.get("edtalk_realtime", "enable"))
    except Exception:
        return False


def is_edtalk_selected(config) -> bool:
    """判断是否选择了 edtalk_realtime 驱动（不论 enable）。

    用于 UI 警告渲染：选了 edtalk_realtime 但 enable=false 时，
    音频走本地播放（有声音、无数字人画面），需要常驻提示。
    """
    try:
        return config.get("visual_body") == "edtalk_realtime"
    except Exception:
        return False
