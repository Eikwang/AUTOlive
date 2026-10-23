# -*- coding: UTF-8 -*-
"""
变声页签「服务就绪自愈」守卫（2026-10-23 排障批）

实测根因：rvc-vc 服务随主系统 main.py 托管（webui.auto_run=false 时不自动
拉起，点「启动系统」后约 10s 初始化），svc 页签先于服务就绪打开 → 首次设备
枚举 10061 一次性失败后错误常驻、下拉恒空——服务就绪也无自愈（唯一出路手动
「刷新设备」），用户感知「变声功能无法使用」。

修复：_poll 探测服务上线自动补枚举（loaded/pending 双守卫）+就绪清错+服务下线
复位 loaded+失败文案给「启动系统」行动指引。本守卫以源级断言锁定自愈接线不被
回退（T8 漂移守卫同款手法；行为级验收=实播：服务未就绪开页看指引 → 点启动
系统 → 设备自动填充、错误自动消失）。
"""
import re
from pathlib import Path

SVC_TAB = Path(__file__).resolve().parents[2] / "frontend" / "ui" / "tabs" / "svc.py"


def _svc_source() -> str:
    return SVC_TAB.read_text(encoding="utf-8")


class TestSvcTabServiceRecovery:
    """svc.py 自愈接线源级守卫（防回退到「一次性枚举失败+常驻错误」旧态）。"""

    def test_enum_failure_message_has_start_guidance(self):
        """枚举失败文案必须给「启动系统」行动指引（不许裸报 WinError 10061）"""
        src = _svc_source()
        assert "设备枚举失败（服务未就绪？）" not in src, \
            "旧裸报文案已退役；若有意回退须同步删除本守卫"
        assert "请点侧边栏「启动系统」" in src

    def test_poll_unready_message_has_start_guidance(self):
        """状态轮询未就绪文案同样给行动指引（旧文案无出路提示）"""
        src = _svc_source()
        assert "服务未就绪（rvc-vc 未运行或端口不符）" not in src, \
            "旧文案已退役；若有意回退须同步删除本守卫"
        assert "「启动系统」后约 10-20 秒自动就绪" in src

    def test_auto_reenumeration_wiring(self):
        """_poll 成功路径须含补枚举守卫（loaded/pending 双条件），失败路径复位 loaded"""
        src = _svc_source()
        assert '_devices_state = {"loaded": False, "pending": False}' in src
        assert re.search(
            r'if not _devices_state\["loaded"\] and not _devices_state\["pending"\]:\s*\n'
            r'\s*background_tasks\.create\(_refresh_devices\(\)\)', src), \
            "poll 成功路径缺自动补枚举（自愈主链路）"
        assert '_devices_state["loaded"] = False' in src, \
            "poll except 分支须复位 loaded（服务下线→下次上线重新枚举）"

    def test_success_clears_error_and_marks_loaded(self):
        """枚举成功须置 loaded=True 并紧随清错误条（陈旧 10061 错误自动消失）"""
        src = _svc_source()
        assert re.search(
            r'_devices_state\["loaded"\] = True\s*\n\s*_clear_error\(\)', src), \
            "成功路径缺 loaded 置位+清错（自愈闭环断点）"

    def test_pending_guard_in_refresh(self):
        """_refresh_devices 入口 pending 防并发（首次枚举与补枚举不双发）"""
        src = _svc_source()
        assert re.search(
            r'async def _refresh_devices\(\):.*?'
            r'if _devices_state\["pending"\]:\s*\n\s*return', src, re.S), \
            "枚举入口缺 pending 防并发守卫"
