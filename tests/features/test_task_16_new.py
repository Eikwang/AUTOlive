# -*- coding: UTF-8 -*-
"""
Task-16: 系统启停控制（任务1 重写版，E6/E10 处置）
原文件断言已删除的空壳方法（toggle_system/start_system/stop_system 仅翻状态
字符串）与已废弃的硬编码色值（"#f44336"/"#4caf50"，代码早已 token 化），本文件
自 token 化改造（b5ec314）起即为红灯，不能作回归基线——按计划改写为新函数链
断言：running_flag 唯一状态源 + app 可选注入 + 空壳移除验证。
纯逻辑测试：伪造 app、零真实子进程、零 UI 依赖（E6 测试分层）。
"""

import pytest


class _FakeApp:
    """伪造 AIVtuberApp：只提供启停控制读取的 running_flag"""

    def __init__(self, running: bool = False):
        self.running_flag = running


def _make_layout(app=None):
    from frontend.ui.layout import DrawerLayout
    config = {"webui": {"theme": {"choose": "default"}}}
    return DrawerLayout(config, app=app)


class TestSystemRunStop:
    """系统启停：running_flag 唯一状态源（任务1/E1）"""

    def test_app_reference_is_optional(self):
        """E1：不传 app 可正常构造（向后兼容既有 117 处测试构造点）"""
        layout = _make_layout(app=None)
        assert layout.app is None

    def test_app_reference_is_stored(self):
        """E1：传入 app 引用被保存，供启停按钮调用函数链"""
        fake = _FakeApp(running=True)
        layout = _make_layout(app=fake)
        assert layout.app is fake

    def test_is_system_running_false_without_app(self):
        """app 未注入（或 None）时 is_system_running 返回 False"""
        layout = _make_layout(app=None)
        assert layout.is_system_running() is False

    def test_is_system_running_reads_running_flag(self):
        """is_system_running 直读 app.running_flag（唯一状态源）"""
        layout = _make_layout(app=_FakeApp(running=False))
        assert layout.is_system_running() is False
        layout.app.running_flag = True
        assert layout.is_system_running() is True

    def test_running_flag_change_is_visible_immediately(self):
        """外部改变 running_flag 后状态立即可读（C1：timer 负责同步 UI）"""
        fake = _FakeApp(running=False)
        layout = _make_layout(app=fake)
        assert layout.is_system_running() is False
        fake.running_flag = True  # 模拟外部变化（如 /sys_cmd 或 auto_run）
        assert layout.is_system_running() is True

    def test_pending_flag_starts_false(self):
        """E5：pending 标志初始为 False（操作窗口外不受影响）"""
        layout = _make_layout(app=_FakeApp(running=False))
        assert layout.pending_flag is False

    def test_update_control_ui_safe_without_ui_elements(self):
        """_update_control_ui 在 UI 元素未构建时安全（按钮/状态条为 None 跳过）"""
        layout = _make_layout(app=_FakeApp(running=False))
        layout._update_control_ui()  # 不应抛异常
        layout._set_pending(True, "重启中…")  # E5：pending 赋值 + 安全刷新
        assert layout.pending_flag is True
        assert layout.pending_label == "重启中…"
        layout._set_pending(False)

    def test_old_empty_shells_removed(self):
        """空壳处置（R2 C-2）：假实现方法已删除，全仓库无两套启停状态源"""
        from frontend.ui.layout import DrawerLayout
        for gone in ("toggle_system", "start_system", "stop_system",
                     "update_system_status", "show_running_status_page"):
            assert not hasattr(DrawerLayout, gone), f"空壳方法未删除: {gone}"

    def test_control_section_state_vars_removed(self):
        """空壳状态变量（system_status/system_button_text/system_button_color）已移除"""
        layout = _make_layout(app=None)
        for gone in ("system_status", "system_button_text", "system_button_color"):
            assert not hasattr(layout, gone), f"空壳状态未删除: {gone}"

    def test_restart_uses_system_level_semantics(self):
        """D-02：重启 = 系统级（stop+run），非 webui 自重启——
        DrawerLayout 不应引入 restart_application（os.execl 属 /sys_cmd 语义）"""
        from frontend.ui.layout import DrawerLayout
        assert not hasattr(DrawerLayout, "restart_application")
