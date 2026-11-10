# -*- coding: UTF-8 -*-
"""
画面设置审查批次守卫测试（2026-10-31）

覆盖：
1. F1 前端安装函数 get_base_path 导入修复（原本地 _install_unity_capture
   引用未导入名 → NameError，摄像头缺失态下安装功能必崩）
2. F2 启动链虚拟摄像头检测+自动安装（任务书：进入系统后检测是否安装了
   摄像头——画面输出的基本支持，若没有安装则执行安装）
3. F3 帧参数域守卫：启动链 clamp 预检（服务端 config.py 对越界值直接
   ValueError 拉起即崩，任务书"预填充帧数需小于缓冲帧数"）+ 前端安全整数
   解析（裸 int(e.value or N) 非数字输入抛 ValueError 崩 on_change 不落盘）
4. F4 角色素材目录退役（素材增删由『动作编排』页接管，目录为内部量默认 wy）
5. install_unity_capture 共享安装函数行为（名称白名单/安装件校验/regsvr32
   参数列表传值/非零返回码）
"""

from pathlib import Path

import pytest

from utils.edtalk_realtime.helpers import install_unity_capture

_FRONTEND_TAB = Path(__file__).resolve().parent.parent / "frontend" / "ui" / "tabs" / "visual_body.py"
_FRONTEND_MAIN = Path(__file__).resolve().parent.parent / "frontend" / "main.py"


# ================= F5 共享安装函数（helpers.install_unity_capture） =================

def test_install_rejects_shell_metacharacters():
    """名称白名单拒绝 shell 元字符（Eng F-E4）。"""
    for bad in ("a'b", 'c"d', "x;y", "p|q", "r&s", "t<u", "v>w", "w$x", "%s", "a\nb"):
        with pytest.raises(ValueError):
            install_unity_capture(bad, "nowhere")


def test_install_accepts_chinese_name_then_fails_on_missing_dll(tmp_path):
    """默认中文名『AUTOlive 数字人』合法（原正则拒绝中文与默认值矛盾）；
    名称校验通过后落在安装件缺失（tmp 假 base_path 无 dll）。"""
    with pytest.raises(FileNotFoundError):
        install_unity_capture("AUTOlive 数字人", tmp_path)


def test_install_builds_regsvr32_arglist(tmp_path, monkeypatch):
    """安装=参数列表传值（powershell 列表 args，无 shell 拼接），命令串含
    regsvr32 / 静默 / 名称注入三要素。"""
    dll = tmp_path / "edtalk" / "UnityCapture-master" / "Install" / "UnityCaptureFilter64.dll"
    dll.parent.parent.mkdir(parents=True)
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ")

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    install_unity_capture("AUTOlive 数字人", tmp_path)
    args = captured["args"]
    assert isinstance(args, list) and args[0] == "powershell"
    assert "regsvr32" in " ".join(args)
    assert "UnityCaptureFilter64.dll" in " ".join(args)
    assert "UnityCaptureName=AUTOlive 数字人" in " ".join(args)
    assert captured["kwargs"].get("shell") in (None, False)


def test_install_raises_on_nonzero_regsvr32(tmp_path, monkeypatch):
    """regsvr32 非零（含用户拒绝 UAC）→ RuntimeError 带问题规格文案。"""
    dll = tmp_path / "edtalk" / "UnityCapture-master" / "Install" / "UnityCaptureFilter64.dll"
    dll.parent.parent.mkdir(parents=True)
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ")

    def fake_run(args, **kwargs):
        class R:
            returncode = 1
            stderr = "denied"

        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    with pytest.raises(RuntimeError):
        install_unity_capture("cam", tmp_path)


# ================= F1/F3/F4 前端源码守卫 =================

def test_frontend_imports_get_base_path():
    """F1 守卫：安装路径引用的 get_base_path 必须有导入（原 NameError）。"""
    src = _FRONTEND_TAB.read_text(encoding="utf-8")
    assert "from frontend.config.paths import get_base_path" in src


def test_frontend_uses_shared_install_helper():
    """F1/F2 守卫：前端安装走 helpers 共享函数（与启动链同源）。"""
    src = _FRONTEND_TAB.read_text(encoding="utf-8")
    assert "install_unity_capture, name, str(get_base_path())" in src
    assert "def _install_unity_capture" not in src


def test_frontend_character_dir_retired():
    """F4 守卫：角色素材目录**自由文本配置项**退役（编排页接管素材管理）。
    2026-11-09 用户设计反转：画面设置新增「数字人角色」**受控下拉**
    （选择器=训练部署角色的受控切换，管理面 validate_switch 校验）——
    自由输入框形态仍退役（用户不再手填路径），选择器写 character_dir 合法。"""
    src = _FRONTEND_TAB.read_text(encoding="utf-8")
    assert "label='角色素材目录'" not in src, "自由文本配置项保持退役"
    assert "数字人角色" in src, "受控角色选择器（用户设计 2026-11-09）应存在"
    assert "validate_switch" in src, "切换须过管理面校验（数据包完整性）"


def test_frontend_safe_int_parsing():
    """F3 守卫：三帧参数 on_change 走 _int_or（裸 int(e.value or N) 退役）。"""
    src = _FRONTEND_TAB.read_text(encoding="utf-8")
    assert "int(e.value or" not in src
    for key in ("target_fps", "buffer_frames", "preroll_frames"):
        assert f'value=_int_or(e.value' in src  # 逐键 on_change 均安全解析
        assert key in src


# ================= F2/F3/F4 启动链源码守卫 =================

def test_startup_chain_camera_detect_and_install():
    """F2 守卫：_start_edtalk_realtime 启动前检测摄像头，未安装自动执行安装
    （默认名与前端一致）。"""
    src = _FRONTEND_MAIN.read_text(encoding="utf-8")
    assert "cam_ok, cam_name = query_unity_capture()" in src
    assert 'install_unity_capture("AUTOlive 数字人", str(get_base_path()))' in src


def test_startup_chain_frame_params_clamped():
    """F3 守卫：buffer∈[1,300] / preroll∈[0,buffer] 启动链 clamp 预检，
    启动参数取 clamp 后值（服务端越界 ValueError 拉起即崩不再可达）。"""
    src = _FRONTEND_MAIN.read_text(encoding="utf-8")
    assert "buffer_frames = min(max(raw_buffer, 1), 300)" in src
    assert "preroll_frames = min(max(raw_preroll, 0), buffer_frames)" in src
    assert '"--buffer-frames", str(buffer_frames)' in src
    assert '"--preroll-frames", str(preroll_frames)' in src


def test_startup_chain_character_dir_retired_key_registered():
    """F4 守卫：character_dir 退役键登记（存量键一次性告知），启动参数指向
    tuber 角色库（2026-11-10 架构改造：键值=当前启用角色，服务加载
    tuber/{角色} 数据包）。"""
    src = _FRONTEND_MAIN.read_text(encoding="utf-8")
    assert '(("edtalk_realtime", "character_dir")' in src
    assert '"--character-dir", os.path.join(str(get_base_path()), "tuber",' in src
