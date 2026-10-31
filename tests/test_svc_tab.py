# -*- coding: utf-8 -*-
"""变声标签页（svc.py）源级回归守卫。

2026-10-30 修复批次（用户实测报错）：
1. set_config_callback 位置传参 TypeError（签名 *keys, value 为 keyword-only）——
   设备选择/参数热更三处崩点；
2. _do_switch 调用签名不匹配（传 config+m，定义仅收 model）；
3. 启动 HTTP 500「未加载模型：先 /switch 再 /start」——/start 前自动预切 RVC 模型。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_root(rel: str) -> str:
    with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
        return f.read()


class TestVoiceChangerIndependence:
    """变声独立化（2026-10-30 用户裁决）：变声是独立流程，不依赖系统主进程。

    - 配置：进入前端即可（页面配置不依赖服务在线）；
    - 服务拉起：rvc_vc.enabled=true 时前端进程直接托管（进前端即拉起），
      启动系统时主系统同样拉起（探活防双实例）；false 时双方都不拉起。
    """

    def test_rvc_vc_alive_exists(self):
        from utils.rvc_service import rvc_vc_alive  # noqa: F401
        import inspect
        sig = inspect.signature(rvc_vc_alive)
        assert "timeout" in sig.parameters

    def test_main_system_gated_by_enabled(self):
        """main.py 核心：enabled=false 不拉起（启动系统不同时启动变声）。"""
        src = _read_root("main.py")
        seg = src[src.find("# RVC 变声服务（P2-1）"):src.find("# DanmakuListener 弹幕监听")]
        assert 'self.config.get("rvc_vc", "enabled")' in seg, "主系统拉起须读开关"
        assert seg.index('if bool(self.config.get("rvc_vc", "enabled")):') < \
            seg.index("build_rvc_vc_service(")
        assert "rvc_vc_alive" in seg, "拉起前探活防双实例"

    def test_frontend_hosts_service(self):
        """前端进程托管：进入前端即拉起（不等启动系统）。"""
        src = _read_root("frontend/main.py")
        seg = src[src.find("# 变声服务前端托管"):src.find('logger.info("AUTOlive 应用程序初始化完成")')]
        assert 'get_nested_value(self.config._config, "rvc_vc", "enabled")' in seg
        assert "build_rvc_vc_service" in seg
        assert "rvc_vc_alive" in seg
        assert "webui-rvc-vc" in seg

    def test_offline_hint_updated(self):
        """svc.py 离线提示不再指向「启动系统」（前端托管语义）。"""
        src = _src()
        assert "点侧边栏「启动系统」后约 10-20 秒自动就绪" not in src
        assert "前端已托管拉起" in src
        assert "变声未启用" in src


def _src():
    with open(os.path.join(_REPO, "frontend", "ui", "tabs", "svc.py"),
              encoding="utf-8") as f:
        return f.read()


class TestSetConfigCallbackWiring:
    """set_config_callback 调用形态守卫（keyword-only value 参数）。"""

    def test_no_positional_value_calls(self):
        src = _src()
        # 崩点形态 1：参数热更（_apply_param）
        assert 'set_config_callback("rvc_vc", key, v)' not in src
        # 崩点形态 2/3：设备选择下拉
        assert '"input_device", e.value)' not in src
        assert '"output_device", e.value)' not in src

    def test_keyword_form_present(self):
        src = _src()
        assert 'set_config_callback("rvc_vc", key, value=v)' in src
        assert '"input_device", value=e.value)' in src
        assert '"output_device", value=e.value)' in src

    def test_select_built_without_value(self):
        """2026-10-30 三次修复（页面加载即「加载配置失败: Invalid value」）：
        select 构建时 options 为空，value 设保存的设备名 → 不在 options 中，
        Quasar 抛 Invalid value 中断整个 tab 构建。构建期不得设 value，
        保存值在 _refresh_devices 填充 options 后校验回填。"""
        src = _src()
        build_seg = src[src.find("# ---- D-3：设备选择"):src.find("# ---- D-4：启停按钮四态")]
        assert "ui.select([], value=" not in build_seg, \
            "构建期设 value 会因空 options 抛 Invalid value"
        assert "_saved_in_dev in in_sel.options" in src
        assert "in_sel.options[0]" in src, "保存设备拔出时输入侧回落第一个可用设备"


class TestDoSwitchSignature:
    """_do_switch 调用/定义签名匹配。"""

    def test_call_matches_definition(self):
        src = _src()
        assert "_do_switch(config," not in src, "定义仅收 model，调用传两参会 TypeError"
        assert "on_click=lambda m=m: _do_switch(m)" in src


class TestAutoSwitchBeforeStart:
    """/start 前置条件自愈：模型未加载时自动预切（修启动 HTTP 500）。"""

    def test_start_checks_and_prefetches_model(self):
        src = _src()
        start_seg = src[src.find("def _start(in_name, out_name):"):src.find("# ---- A-7 修复")]
        assert "client.status()" in start_seg, "启动前须查状态"
        assert 'not (st.get("model") or {}).get("pth")' in start_seg
        # 预切顺序：switch_model 先于 start_stream
        assert start_seg.index("client.switch_model(") < start_seg.index(
            "client.start_stream(")
        # 无模型行动指引（不裸 500）
        assert "无可用 RVC 模型" in start_seg

    def test_default_model_prefers_rvc_profiles(self):
        """任务书：声音模型使用 RVC 管线训练结果——档案（rvc_model）优先于目录扫描。"""
        src = _src()
        pick_seg = src[src.find("def _pick_default_model("):src.find("def _start(")]
        assert pick_seg.index("rvc_model") < pick_seg.index("models[0]"), \
            "档案优先序回归"

    def test_update_rebuild_badge_derefs_container(self):
        """2026-10-30 四次修复（启动成功却误报失败）：_update_rebuild_badge 对
        list 容器漏写 [0] → AttributeError 被 _start except 捕获误报「启动失败」
        （变声实际已成功）。容器访问必须解引用。"""
        src = _src()
        badge_seg = src[src.find("def _update_rebuild_badge():"):src.find("_rebuild_badge: list = []")]
        assert "_rebuild_badge[0].set_visibility" in badge_seg
        assert "_rebuild_badge.set_visibility" not in badge_seg

    def test_start_thread_created_on_click_only(self):
        """启动线程必须在 _toggle_stream 内（点击时）创建——2026-10-30 二次修复：
        Thread 曾错位到函数体层级，页面加载即跑且 in_name/out_name 未定义
        → NameError 静默失败（用户实测「依然无法启动」根因）。"""
        src = _src()
        toggle_seg = src[src.find("def _toggle_stream("):src.find("def _pick_default_model(")]
        assert "Thread(target=_start, args=(in_name, out_name)" in toggle_seg
        # 设备名显式传参（_start 签名收参，不再裸引用闭包变量）
        assert "def _start(in_name, out_name):" in src
        # 全文件不再有函数体层级的裸启动线程
        assert "Thread(target=_start, daemon=True).start()" not in src
