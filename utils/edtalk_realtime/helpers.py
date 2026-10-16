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


# UnityCapture 服务类 CLSID（64 位构建末字节 0x10、32 位 0x20；多设备按末字节
# 偏移，本项目固定 cap 0）。regsvr32 安装把摄像头名写进该 CLSID 的默认值
# （提权写 HKLM、免提权写 HKCU），不产生 Software\UnityCapture 键。
_UNITY_CAPTURE_SERVICE_CLSIDS = (
    "{5C2CD55C-92AD-4999-8666-912BD3E70010}",
    "{5C2CD55C-92AD-4999-8666-912BD3E70020}",
)

try:
    import winreg as _winreg
except ImportError:  # 非 Windows 平台：无虚拟摄像头概念，检测恒为未安装
    _winreg = None


def query_unity_capture():
    """检测 UnityCapture 虚拟摄像头安装状态，返回 (installed, 摄像头名)。

    注册机制（UnityCaptureFilter.cpp RegisterFilters）：regsvr32
    /i:UnityCaptureName=名字 经命令行解析写进服务 CLSID 默认值，并在
    DirectShow 摄像头类别下注册 Instance；DllInstall 为空实现——不存在
    HKCU\\Software\\UnityCapture\\FilterName（旧实现查此处，永远 False）。

    Returns:
        (True, 名字) 任一 UnityCapture 服务 CLSID 已注册；否则 (False, "")。
    """
    if _winreg is None:
        return False, ""
    for hive in (_winreg.HKEY_LOCAL_MACHINE, _winreg.HKEY_CURRENT_USER):
        for clsid in _UNITY_CAPTURE_SERVICE_CLSIDS:
            try:
                with _winreg.OpenKey(hive, "Software\\Classes\\CLSID\\" + clsid) as key:
                    name, _ = _winreg.QueryValueEx(key, "")
            except OSError:
                continue
            if str(name or "").strip():
                return True, str(name)
    return False, ""
