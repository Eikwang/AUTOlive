from frontend.ui.components.config_helper import get_nested_value
"""
UI布局模块
负责创建主界面布局
"""
import asyncio

from nicegui import ui
from typing import Dict, Any, Optional

from frontend.config.enable_paths import ENABLE_PATHS
from frontend.ui.components.service_status import ServiceStatusStrip
from utils.my_log import logger


class UILayout:
    """UI布局管理器"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化UI布局

        Args:
            config: 配置字典
        """
        self.config = config
        self.tabs = {}
        self.tab_panels = {}

        # 注入全局样式
        self.inject_global_styles()

        # 获取主题配置 - 使用简化的浅色/深色模式
        from frontend.styles.themes import get_theme_manager
        theme_manager = get_theme_manager()
        self.theme_config = theme_manager.get_theme_config()

        # CSS样式
        self.tab_panel_css = self.theme_config.get("tab_panel", "")
        self.card_css = self.theme_config.get("card", "")
        self.button_bottom_css = self.theme_config.get("button_bottom", "")
        self.button_bottom_color = self.theme_config.get("button_bottom_color", "")
        self.button_internal_css = self.theme_config.get("button_internal", "")
        self.button_internal_color = self.theme_config.get("button_internal_color", "")
        self.switch_internal_css = self.theme_config.get("switch_internal", "")
        self.echart_css = self.theme_config.get("echart", "")
    
    def create_tabs(self) -> Dict[str, Any]:
        """
        创建标签页
        
        Returns:
            标签页字典
        """
        with ui.tabs().classes('w-full') as tabs:
            self.tabs = {
                'common_config': ui.tab('通用配置'),
                'llm': ui.tab('大语言模型'),
                'tts': ui.tab('文本转语音'),
                'svc': ui.tab('变声'),
                'visual_body': ui.tab('虚拟身体'),
                'copywriting': ui.tab('文案'),
                'talk': ui.tab('聊天'),
                'image_recognition': ui.tab('图像识别'),
                'integral': ui.tab('积分'),
                'assistant_anchor': ui.tab('助播'),
                'translate': ui.tab('翻译'),
                'serial': ui.tab('串口'),
                'data_analysis': ui.tab('数据分析'),
                'web': ui.tab('页面配置'),
            }
        return self.tabs
    
    def create_tab_panels(self) -> Dict[str, Any]:
        """
        创建标签页面板
        
        Returns:
            标签页面板字典
        """
        with ui.tab_panels(self.tabs, value=self.tabs['common_config']).classes('w-full'):
            for key, tab in self.tabs.items():
                with ui.tab_panel(tab).style(self.tab_panel_css):
                    self.tab_panels[key] = ui.column()
        
        return self.tab_panels
    
    def get_theme_css(self, element: str) -> str:
        """
        获取主题CSS
        
        Args:
            element: 元素名称
            
        Returns:
            CSS样式字符串
        """
        return self.theme_config.get(element, "")
    
    def create_card(self, title: str, content_func=None):
        """
        创建卡片
        
        Args:
            title: 卡片标题
            content_func: 内容创建函数
        """
        with ui.card().style(self.card_css):
            ui.label(title)
            if content_func:
                content_func()
    
    def create_button(self, text: str, on_click=None, style: str = "bottom"):
        """
        创建按钮
        
        Args:
            text: 按钮文本
            on_click: 点击事件处理函数
            style: 按钮样式（bottom/internal）
        """
        if style == "bottom":
            css = self.button_bottom_css
            color = self.button_bottom_color
        else:
            css = self.button_internal_css
            color = self.button_internal_color
        
        button = ui.button(text, on_click=on_click).style(css)
        if color:
            button.props(f'color={color}')
        return button
    
    def create_switch(self, label: str, value: bool = False, on_change=None):
        """
        创建开关

        Args:
            label: 标签文本
            value: 初始值
            on_change: 值变化事件处理函数
        """
        switch = ui.switch(label, value=value, on_change=on_change).style(self.switch_internal_css)
        return switch


class DrawerLayout:
    """Drawer 布局管理器 - 左侧导航栏 + 三级页面布局"""

    # 设计令牌统一定义在 frontend/styles/design_tokens.py（CSS 变量形式注入）

    def inject_global_styles(self):
        """注入全局 CSS 样式 - 设计令牌 + 统一基础元素样式"""
        # 设计令牌（CSS 变量：颜色/字体/间距/圆角/阴影，含深色模式覆盖）
        from frontend.styles.design_tokens import inject_design_tokens
        css = f"""
        {inject_design_tokens()}

        /* ============ 全局基础 ============ */
        body {{
            font-family: var(--font-family);
            font-size: var(--font-size-body);
            color: var(--text-body);
            line-height: 1.5;
            overflow: hidden;
        }}

        /* 隐藏滚动条但保留滚动功能 */
        .hide-scrollbar::-webkit-scrollbar {{
            display: none;
        }}
        .hide-scrollbar {{
            -ms-overflow-style: none;
            scrollbar-width: none;
        }}

        /* ============ 统一标题层级 ============ */
        /* 页面标题（三级页面顶部） */
        .page-title {{
            font-size: var(--font-size-page-title);
            font-weight: var(--font-weight-title);
            color: var(--text-title);
        }}
        /* 卡片标题 */
        .card-title {{
            font-size: var(--font-size-card-title);
            font-weight: var(--font-weight-title);
            color: var(--text-title);
            margin-bottom: var(--spacing-sm);
        }}
        /* 分组标题 */
        .section-title {{
            font-size: var(--font-size-section-title);
            font-weight: var(--font-weight-title);
            color: var(--text-title);
        }}
        /* 卡片内首个裸 label 视为卡片标题（ui.label 渲染为无类 div，
           row/column/field 等均带类名被 :not([class]) 排除） */
        .q-card > div:first-child:not([class]) {{
            font-size: var(--font-size-card-title);
            font-weight: var(--font-weight-title);
            color: var(--text-title);
            margin-bottom: var(--spacing-sm);
        }}
        /* 参数标题（表单 label） */
        .q-field .q-field__label {{
            font-size: var(--font-size-field-label);
            font-weight: var(--font-weight-normal);
            color: var(--text-secondary);
        }}

        /* ============ 配置区布局约束 ============ */
        /* 配置内容区内：row 允许换行 + 统一间距，避免输入框挤爆显示不完整 */
        .config-content .row {{
            flex-wrap: wrap;
            gap: var(--spacing-sm) var(--spacing-md);
        }}
        .config-content .row > * {{
            min-width: 0;
        }}
        /* 网格占满配置区宽度（config-content 为 flex column，子项默认内容宽） */
        .config-content .nicegui-grid {{
            width: 100%;
        }}
        /* 网格内纵向单元：设置项内部上下结构紧凑排列 */
        .config-content .nicegui-column {{
            gap: var(--spacing-xs);
        }}

        /* ============ 统一卡片 ============ */
        .q-card {{
            background: var(--bg-card);
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow-sm);
            transition: box-shadow var(--transition-normal);
        }}
        .q-card:hover {{
            box-shadow: var(--shadow-md);
        }}

        /* ============ 统一按钮 ============ */
        .q-btn {{
            border-radius: var(--radius-md);
            font-weight: var(--font-weight-emphasis);
            transition: all var(--transition-fast);
        }}

        /* ============ 系统启停控制（任务1/DG-2/DG-4/任务2.3）============ */
        /* 禁用 Quasar 默认中心涟漪与悬停浮层——悬停统一为整个按钮表面变化 */
        .sys-btn .q-ripple,
        .theme-btn .q-ripple {{
            display: none;
        }}
        .sys-btn::before, .sys-btn::after,
        .theme-btn::before, .theme-btn::after {{
            display: none !important;
        }}
        /* 彩色启停按钮：悬停 = 全表面亮度变化（免新增色值令牌，双主题安全） */
        .sys-btn-start:hover, .sys-btn-stop:hover, .sys-btn-restart:hover {{
            filter: brightness(0.88);
        }}
        /* 中性按钮（主题切换）：悬停 = 整按钮 --bg-hover 背景 */
        .theme-btn:hover {{
            background: var(--bg-hover) !important;
        }}
        /* 禁用态（DG-4）：Quasar 1.x 禁用类名为 .disabled（非 .q-btn--disabled）
           + 统一降透明度 + not-allowed */
        .sys-btn.disabled, .sys-btn.q-btn--disabled {{
            opacity: 0.45 !important;
            cursor: not-allowed;
        }}
        /* 复合按钮容器 */
        .sys-composite .q-btn {{
            min-width: 0;
        }}

        /* ============ 统一输入框 ============ */
        .q-field--standard .q-field__control {{
            border-radius: var(--radius-md);
        }}
        .q-field--outlined .q-field__control:before {{
            border-color: var(--border-color);
        }}

        /* ============ 响应式 ============ */
        @media (max-width: 768px) {{
            body {{
                font-size: 13px;
            }}
        }}
        """
        ui.add_head_html(f"<style>{css}</style>")

    def __init__(self, config: Dict[str, Any], save_callback=None, app=None, dark_mode=None):
        """
        初始化 Drawer 布局

        Args:
            config: 配置字典
            save_callback: 保存配置的回调函数
            app: AutoliveApp 实例（Optional，E1：None 时启停按钮降级为禁用态，
                 兼容测试构造点；生产装配处传入 self）
            dark_mode: 主进程的单例 ui.dark_mode() 实例（E-F4：禁再 new，
                 None 时 toggle_theme 内惰性创建兜底）
        """
        self.config = config
        self.save_callback = save_callback
        self.app = app
        self.dark_mode = dark_mode
        self.tabs = {}
        self.tab_panels = {}
        self.drawer = None
        self.content_area = None
        self.function_list = None
        self.config_page = None
        self.drawer_mini = config.get("webui", {}).get("drawer_mini", False)
        # self.dark_mode 已在签名块按注入实例赋值（E-F4 单实例，禁覆盖为 None）
        self.current_group = None
        self.current_function = None  # 当前选中的功能名称
        self.function_list_container = None
        self.function_items = {}
        self.config_page_container = None
        self.config_values = {}
        self.save_notification = None
        self.save_button_text = "保存"
        self.search_results = []
        self.search_keyword = ""
        self.search_results_container = None
        self.tabs_mapping = {}
        self.theme_button = None

        # 系统启停控制（任务1：running_flag 为唯一状态源，本类不保存状态副本）
        self.pending_flag = False       # E5：操作进行中标志（pending 窗口内全部禁用）
        self.pending_label = ""         # pending 期间状态条文案（停止中…/重启中…）
        self.control_section = None     # E9：折叠态隐藏的按钮簇容器（状态条+启停按钮）
        self.status_dot = None
        self.status_label = None
        self.start_button = None
        self.stop_button = None
        self.restart_button = None
        # 任务6/DG-B1：配置页关闭态状态条引用（实时同步用）
        self.config_status_label = None
        self._current_page_enable_path = None

        # 注入全局样式
        self.inject_global_styles()

    def create_drawer(self):
        """
        创建左侧导航栏

        Returns:
            ui.drawer 对象
        """
        self.drawer = ui.left_drawer(value=True, bordered=True)
        # 使用 style 设置宽度 - 简化样式，添加过渡动画
        self.drawer.style('''
            width: 300px;
            background: var(--bg-card);
            border-right: 1px solid var(--border-color);
            transition: width 0.3s ease;
            overflow-x: hidden;
        ''')
        return self.drawer

    def toggle_drawer(self):
        """切换导航栏折叠状态"""
        self.drawer_mini = not self.drawer_mini
        # 更新 drawer 宽度和样式
        if self.drawer is not None:
            width = self.get_drawer_width()
            self.drawer.style(f'''
                width: {width};
                background: var(--bg-card);
                border-right: 1px solid var(--border-color);
            ''')
        # E9：折叠态隐藏启停控制区（60px 下复合按钮与 44px 点击目标数学不成立）
        if self.control_section is not None:
            self.control_section.visible = not self.drawer_mini
        # 保存状态到配置
        if "webui" not in self.config:
            self.config["webui"] = {}
        self.config["webui"]["drawer_mini"] = self.drawer_mini
        # 保存配置到文件（E-F1：save_config 已透传异常，此处兜底可见反馈）
        try:
            self.save_config()
        except Exception as e:
            logger.error(f"折叠状态保存失败: {e}")
            ui.notify(position="top", type="negative", message="折叠状态保存失败")

    def get_drawer_width(self) -> str:
        """获取导航栏宽度"""
        return '60px' if self.drawer_mini else '300px'

    def save_config(self) -> bool:
        """保存配置到文件（E-F1：透传异常，不吞——调用方决定失败反馈）"""
        if self.save_callback:
            self.save_callback()
            return True
        return False

    def toggle_theme(self):
        """切换深色/浅色主题（任务7/DG-B8：切换即写 webui.dark_mode 持久化）"""
        if self.dark_mode is None:
            self.dark_mode = ui.dark_mode()

        # NiceGUI 的 dark_mode 是一个布尔值
        self.dark_mode.value = not self.dark_mode.value

        # 更新按钮文本与图标（随态切换）
        if hasattr(self, 'theme_button') and self.theme_button:
            self.theme_button.text = "深色模式" if self.dark_mode.value else "浅色模式"
            self.theme_button.props(
                f'icon={"light_mode" if self.dark_mode.value else "dark_mode"}'
            )

        # 任务7/DG-B8：持久化到 webui.dark_mode（写内存 + save_config 落盘；
        # 勿触 legacy webui.theme 段）；失败可见（负反馈），主题本身已切换
        try:
            if "webui" not in self.config:
                self.config["webui"] = {}
            self.config["webui"]["dark_mode"] = bool(self.dark_mode.value)
            self.save_config()
        except Exception as e:
            logger.error(f"主题偏好保存失败: {e}")
            ui.notify(position="top", type="negative", message="主题保存失败，重启后将恢复原主题")

        # 显示切换通知
        ui.notify(
            position="top",
            type="info",
            message=f"已切换到{'深色' if self.dark_mode.value else '浅色'}模式"
        )

    # save_theme_preference/load_theme_preference 已删除（任务7：持久化内联进
    # toggle_theme 写 webui.dark_mode；旧实现为 pass 空壳）


    # ============ 系统启停控制（任务1：接回 AutoliveApp 函数链）============
    # 空壳处置（R2 C-2）：原 update_system_status/toggle_system/start_system/
    # stop_system/show_running_status_page 仅翻转状态字符串、不调用后端，已删除。
    # running_flag（app 层）为唯一状态源；本类只持 pending_flag（E5）。

    def is_system_running(self) -> bool:
        """检查系统是否运行（直读 app.running_flag）"""
        return bool(self.app is not None and getattr(self.app, "running_flag", False))

    def _guard_app(self) -> bool:
        """app 引用守卫（注入缺失时给出可见反馈，不抛裸异常）"""
        if self.app is None:
            ui.notify(position="top", type="warning", message="系统控制不可用（未连接应用实例）")
            return False
        return True

    def _set_pending(self, pending: bool, label: str = ""):
        """pending 标志 + 即时 UI 刷新（E5：pending 期间 timer 跳过，状态由这里独占）"""
        self.pending_flag = pending
        self.pending_label = label
        self._update_control_ui()

    def _update_control_ui(self):
        """按钮/状态条唯一来源 = f(running_flag, pending_flag)（E5/DG-4/C1）"""
        running = self.is_system_running()
        pending = self.pending_flag

        # 状态条（DG-6/D4/DG-10：圆点+文字双通道，色盲安全；文案语义如实）
        if self.status_dot is not None and self.status_label is not None:
            if pending:
                dot_color = "var(--color-warning)"
                text = self.pending_label or "处理中…"
            elif running:
                dot_color = "var(--color-success)"
                text = "运行中"
            else:
                dot_color = "var(--text-hint)"
                text = "已停止"
            self.status_dot.style(
                f"width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; background: {dot_color};"
            )
            self.status_label.style(
                f"font-size: var(--font-size-body); color: {'var(--text-body)' if (running or pending) else 'var(--text-hint)'};"
            )
            self.status_label.text = text

        # 按钮可用态：pending 窗口全部禁用；running 决定启动 vs 停止/重启
        if self.start_button is not None:
            if not running and not pending:
                self.start_button.enable()
            else:
                self.start_button.disable()
        if self.stop_button is not None:
            if running and not pending:
                self.stop_button.enable()
            else:
                self.stop_button.disable()
        if self.restart_button is not None:
            if running and not pending:
                self.restart_button.enable()
            else:
                self.restart_button.disable()

    def _sync_from_flag(self):
        """ui.timer 回调（C1）：同步 running_flag 外部变化；pending 期间跳过（E5）"""
        try:
            if self.pending_flag:
                return
            self._update_control_ui()
        except Exception as e:
            logger.error(f"启停状态同步失败: {e}")

    def _execute_start(self):
        """启动系统（免确认；api 静默通道 + UI 单条结果 notify——F12）"""
        if not self._guard_app():
            return
        self._set_pending(True, "启动中…")
        try:
            result = self.app.run_external_program(type="api") or {}
            if result.get("code") == 200:
                ui.notify(position="top", type="positive", message="程序开始运行")
            else:
                ui.notify(position="top", type="negative",
                          message=result.get("msg", "运行失败"))
        except Exception as e:
            logger.error(f"启动系统失败: {e}")
            ui.notify(position="top", type="negative", message=f"启动失败：{e}")
        finally:
            self._set_pending(False)
            self._update_control_ui()

    def _confirm_stop(self):
        """停止确认对话框（C2/DG-5：文案区分后果，确认键用 --color-danger）"""
        if not self._guard_app():
            return
        with ui.dialog() as dialog, ui.card().style("min-width: 320px;"):
            ui.label("确认停止").style(
                "font-size: var(--font-size-card-title); font-weight: var(--font-weight-title); color: var(--text-title); margin-bottom: 4px;")
            ui.label("将结束当前运行，直播中断").style("color: var(--text-body);")
            with ui.row().style("justify-content: flex-end; width: 100%; gap: 8px; margin-top: 8px;"):
                ui.button("取消", on_click=dialog.close, color=None).props("flat no-caps").style(
                    "color: var(--text-body);")
                ui.button("确认停止", on_click=lambda: self._do_stop(dialog), color=None).props(
                    "unelevated no-caps").style(
                    "background: var(--color-danger); color: var(--text-on-primary);")
        dialog.open()

    def _do_stop(self, dialog):
        dialog.close()
        self._set_pending(True, "停止中…")
        try:
            result = self.app.stop_external_program(type="api") or {}
            code = result.get("code")
            if code == 200:
                ui.notify(position="top", type="positive", message="程序已停止")
            elif code == 0:
                ui.notify(position="top", type="warning", message="程序未在运行")
            else:
                ui.notify(position="top", type="negative",
                          message=result.get("msg", "停止失败"))
        except Exception as e:
            logger.error(f"停止系统失败: {e}")
            ui.notify(position="top", type="negative", message=f"停止失败：{e}")
        finally:
            self._set_pending(False)
            self._update_control_ui()

    def _confirm_restart(self):
        """重启确认对话框（C2/DG-5）"""
        if not self._guard_app():
            return
        with ui.dialog() as dialog, ui.card().style("min-width: 320px;"):
            ui.label("确认重启").style(
                "font-size: var(--font-size-card-title); font-weight: var(--font-weight-title); color: var(--text-title); margin-bottom: 4px;")
            ui.label("将先停止再拉起，短暂中断").style("color: var(--text-body);")
            with ui.row().style("justify-content: flex-end; width: 100%; gap: 8px; margin-top: 8px;"):
                ui.button("取消", on_click=dialog.close, color=None).props("flat no-caps").style(
                    "color: var(--text-body);")
                ui.button("确认重启", on_click=lambda: self._do_restart(dialog), color=None).props(
                    "unelevated no-caps").style(
                    "background: var(--color-success); color: var(--text-on-primary);")
        dialog.open()

    async def _do_restart(self, dialog):
        """系统级重启（D-02：stop + 间隔 + run，控制台 webui 自身不重启）"""
        dialog.close()
        self._set_pending(True, "重启中…")
        try:
            stop_result = self.app.stop_external_program(type="api") or {}
            if stop_result.get("code") == -1:
                ui.notify(position="top", type="negative",
                          message=stop_result.get("msg", "停止失败"))
                return
            # E4：停止与拉起之间留 1.5s——声卡/串口/弹幕连接释放存在滞后，
            # 背靠背 stop+run 会让新进程抢不到设备（且失败被静默）。
            await asyncio.sleep(1.5)
            run_result = self.app.run_external_program(type="api") or {}
            if run_result.get("code") == 200:
                ui.notify(position="top", type="positive", message="已重启")
            else:
                ui.notify(position="top", type="negative",
                          message=run_result.get("msg", "重启失败"))
        except Exception as e:
            logger.error(f"重启系统失败: {e}")
            ui.notify(position="top", type="negative", message=f"重启失败：{e}")
        finally:
            self._set_pending(False)
            self._update_control_ui()

    def _create_control_section(self):
        """构建启停控制区（任务1：状态条 + 启动系统 + 停止&重启复合按钮）

        E1：app 为 None 时按钮全部禁用（降级绑定，兼容测试构造点）。
        """
        self.control_section = ui.column().style("width: 100%; padding: 0; gap: 8px;")
        with self.control_section:
            # 状态条（DG-6：按钮簇正上方独立全宽）
            with ui.row().style("width: 100%; align-items: center; gap: 8px; padding: 0 2px;"):
                self.status_dot = ui.element("div").style(
                    "width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; background: var(--text-hint);")
                self.status_label = ui.label("已停止").style(
                    "font-size: var(--font-size-body); color: var(--text-hint);")
                with self.status_label:
                    ui.tooltip("进程已拉起，无退出监控（v1 语义）")

            # 启动系统（绿色主按钮；DG-11：min-height 44px）
            # remove bg-primary：NiceGUI 默认色类带 !important，会压过 inline 背景
            self.start_button = ui.button("启动系统", on_click=self._execute_start, color=None).props(
                "unelevated no-caps").classes("sys-btn sys-btn-start").style(
                """width: 100%; min-height: 44px;
                   background: var(--color-success); color: var(--text-on-primary);
                   border-radius: var(--radius-md);
                   font-weight: var(--font-weight-emphasis);""")

            # 停止&重启复合按钮（DG-2：一体圆角容器 + 1px 中缝，两半独立 hover）
            with ui.row().classes("sys-composite").style(
                "width: 100%; gap: 0; padding: 0; flex-wrap: nowrap; align-items: stretch; "
                "border-radius: var(--radius-md); overflow: hidden;"):
                self.stop_button = ui.button("停止", on_click=self._confirm_stop, color=None).props(
                    "unelevated no-caps").classes("sys-btn sys-btn-stop").style(
                    """flex: 1; min-height: 44px; border-radius: 0;
                       background: var(--color-danger); color: var(--text-on-primary);
                       font-weight: var(--font-weight-emphasis);""")
                ui.element("div").style(
                    "width: 1px; background: var(--divider-color); flex-shrink: 0; align-self: stretch;")
                self.restart_button = ui.button("重启", on_click=self._confirm_restart, color=None).props(
                    "unelevated no-caps").classes("sys-btn sys-btn-restart").style(
                    """flex: 1; min-height: 44px; border-radius: 0;
                       background: var(--color-success); color: var(--text-on-primary);
                       font-weight: var(--font-weight-emphasis);""")

        # E9：折叠态隐藏（60px 下复合按钮与 44px 点击目标数学不成立）
        self.control_section.visible = not self.drawer_mini
        self._update_control_ui()

    def migrate_tabs(self):
        """迁移现有 tabs 功能到新布局"""
        # 清空现有数据
        self.function_items = {}
        self.tabs_mapping = {}

        # 遍历所有功能分组
        function_groups = self.config.get("webui", {}).get("function_groups", [])
        for group in function_groups:
            for func in group.get("functions", []):
                func_name = func["name"]
                tab_key = func.get("tab_key", "")
                enabled = func.get("enabled", True)

                # 添加到 function_items
                self.function_items[func_name] = {
                    "enabled": enabled,
                    "tab_key": tab_key
                }

                # 添加到 tabs_mapping
                if tab_key:
                    self.tabs_mapping[tab_key] = func_name

    def validate_integration(self) -> bool:
        """验证集成是否有效"""
        # 检查必需的配置项
        function_groups = self.config.get("webui", {}).get("function_groups", [])
        config_items = self.config.get("webui", {}).get("config_items", {})

        # 功能分组不能为空
        if not function_groups:
            return False

        # 配置项不能为空
        if not config_items:
            return False

        return True

    def get_integration_status(self) -> dict:
        """获取集成状态"""
        function_groups = self.config.get("webui", {}).get("function_groups", [])
        config_items = self.config.get("webui", {}).get("config_items", {})

        # 计算功能数量
        functions_count = sum(len(group.get("functions", [])) for group in function_groups)

        return {
            "valid": self.validate_integration(),
            "function_groups_count": len(function_groups),
            "config_items_count": len(config_items),
            "functions_count": functions_count
        }

    def create_navigation_groups(self):
        """创建导航栏功能分组"""
        function_groups = self.config.get("webui", {}).get("function_groups", [])

        # 添加标题
        with self.drawer:
            # 标题区域
            with ui.row().style('''
                width: 100%;
                padding: var(--spacing-md);
                border-bottom: 1px solid var(--border-color);
                align-items: center;
                background: var(--color-primary);
            '''):
                ui.label("AUTOlive").style('''
                    font-size: var(--font-size-banner);
                    font-weight: var(--font-weight-heavy);
                    letter-spacing: 0.12em;
                    text-transform: uppercase;
                    color: var(--text-on-primary);
                ''')

            # 全局搜索框
            self.global_search_input = ui.input(
                placeholder="搜索功能...",
                on_change=lambda e: self._on_global_search(e.value)
            ).props('outlined dense clearable').style('''
                width: calc(100% - 24px);
                margin: 12px 12px 8px 12px;
            ''')

            # 分组导航容器
            self.group_nav_container = ui.column().classes('hide-scrollbar').style('''
                width: 100%;
                flex: 1;
                overflow-y: auto;
                overflow-x: hidden;
                padding: 8px 0;
            ''')

            # 渲染分组导航
            self._render_group_navigation(function_groups)

            # 底部工具栏（任务1：启停控制区置于主题按钮上方；border-top = DG-3 与列表分隔）
            with ui.column().style('''
                width: 100%;
                padding: var(--spacing-md);
                margin-top: auto;
                border-top: 1px solid var(--border-color);
                background: var(--bg-page);
                gap: 8px;
            '''):
                # 服务状态常驻条（P1-3：托管服务胶囊区，D3 侧边栏底部固定，
                # 异常优先排序+置红顶部横幅；数据源 ServiceRegistry，启动后自动出现）
                self.service_strip_container = ui.column().style('width:100%;gap:4px;')
                self._service_strip = ServiceStatusStrip()
                self._service_strip.mount(self.service_strip_container)

                # DanmakuListener 扫码登录入口 + system 事件通知（整合计划 G4/G5：
                # NEEDS_LOGIN 队列呈现/QR 安全渲染/恢复自动关闭；事件环 → ui.notify）
                try:
                    from frontend.ui.components.danmaku_login import DanmakuLoginDialog, DanmakuEventNotifier
                    self._danmaku_login_container = ui.column().style('width:100%;gap:4px;')
                    self._danmaku_login = DanmakuLoginDialog()
                    self._danmaku_login.mount(self._danmaku_login_container)
                    self._danmaku_notifier = DanmakuEventNotifier()
                    self._danmaku_notifier.mount(self._danmaku_login_container)
                except Exception:
                    from utils.my_log import logger
                    logger.error("DanmakuListener 登录/通知组件挂载失败（不阻断）", exc_info=True)

                # 启停控制区（状态条 + 启动系统 + 停止&重启复合按钮）
                self._create_control_section()

                # 主题切换按钮（任务2.3：flat 改 unelevated，悬停高亮整个按钮）
                self.theme_button = ui.button(
                    "深色模式" if self.dark_mode and self.dark_mode.value else "浅色模式",
                    on_click=self.toggle_theme, color=None
                ).props('icon=dark_mode unelevated no-caps').classes('theme-btn').style('''
                    width: 100%;
                    justify-content: flex-start;
                    text-transform: none;
                    font-weight: var(--font-weight-emphasis);
                    margin: 2px 0;
                    border-radius: var(--radius-md);
                    transition: all var(--transition-fast);
                    color: var(--text-body);
                    background: transparent;
                    min-height: 40px;
                ''')

        # 启停状态定时同步（C1/E5：2s 轮询 running_flag 外部变化，pending 期间跳过）
        ui.timer(2.0, self._sync_from_flag)

    def _render_group_navigation(self, function_groups: list):
        """渲染分组导航"""
        if self.group_nav_container is None:
            return

        self.group_nav_container.clear()

        with self.group_nav_container:
            for group in function_groups:
                group_name = group["name"]
                icon = group.get("icon", "folder")
                functions = group.get("functions", [])
                is_selected = (self.current_group == group_name)

                # 分组按钮 - 选中状态样式
                bg_color = 'var(--bg-selected)' if is_selected else 'transparent'
                border_left = '3px solid var(--color-primary)' if is_selected else '3px solid transparent'
                icon_color = 'var(--color-primary)' if is_selected else 'var(--text-hint)'
                text_color = 'var(--text-title)' if is_selected else 'var(--text-body)'
                text_weight = 'var(--font-weight-title)' if is_selected else 'var(--font-weight-emphasis)'

                group_row = ui.row().style(f'''
                    width: 100%;
                    position: relative;
                    cursor: pointer;
                    padding: 12px 16px;
                    align-items: center;
                    background: {bg_color};
                    border-left: {border_left};
                    transition: all var(--transition-fast);
                ''')
                group_row.on('click', lambda e, g=group_name: self.select_group(g))
                # 任务3：一级菜单补 hover 并与二级统一（--bg-hover，仅未选中项生效）
                def make_group_hover(selected):
                    def on_enter(e):
                        if not selected:
                            e.sender.style('background-color: var(--bg-hover)')
                    def on_leave(e):
                        if not selected:
                            e.sender.style('background-color: transparent')
                    return on_enter, on_leave
                enter_h, leave_h = make_group_hover(is_selected)
                group_row.on('mouseenter', enter_h)
                group_row.on('mouseleave', leave_h)
                with group_row:
                    ui.icon(icon).style(f'''
                        margin-right: 8px;
                        color: {icon_color};
                        font-size: 18px;
                        transition: color var(--transition-fast);
                    ''')
                    ui.label(group_name).style(f'''
                        font-size: var(--font-size-body);
                        font-weight: {text_weight};
                        color: {text_color};
                        flex: 1;
                        white-space: nowrap;
                        overflow: hidden;
                        text-overflow: ellipsis;
                        transition: all var(--transition-fast);
                    ''')
                    # 功能数量
                    if functions:
                        badge_color = 'primary' if is_selected else 'grey-5'
                        ui.badge(str(len(functions))).props(f'color={badge_color} rounded dense').style('''
                            font-size: var(--font-size-tiny);
                            min-width: 20px;
                            height: 18px;
                        ''')

                # 分割线
                ui.separator().style('margin: 4px 12px; background-color: var(--divider-color);')

    def _on_global_search(self, keyword: str):
        """全局搜索处理"""
        if not keyword:
            # 搜索为空时，恢复显示当前分组的功能列表
            if self.current_group:
                self.show_function_list(self.current_group)
            return

        # 搜索所有分组中的功能
        keyword_lower = keyword.lower()
        all_results = []

        function_groups = self.config.get("webui", {}).get("function_groups", [])
        for group in function_groups:
            for func in group.get("functions", []):
                if keyword_lower in func["name"].lower():
                    all_results.append({
                        "name": func["name"],
                        "group": group["name"],
                        "tab_key": func.get("tab_key", ""),
                        "enabled": func.get("enabled", True),
                        "type": "function"
                    })

        # 搜索配置项注册表
        try:
            from frontend.utils.config_registry import get_config_registry
            registry = get_config_registry()
            config_results = registry.search(keyword)

            # 获取 tab_key 到功能名称的映射
            tab_key_to_name = {}
            tab_key_to_group = {}
            for group in function_groups:
                for func in group.get("functions", []):
                    tab_key_to_name[func.get("tab_key", "")] = func["name"]
                    tab_key_to_group[func.get("tab_key", "")] = group["name"]

            for item in config_results:
                tab_key = item["tab_key"]
                func_name = tab_key_to_name.get(tab_key, tab_key)
                group_name = tab_key_to_group.get(tab_key, "未知")

                # 避免重复（如果功能名称已经在结果中）
                if not any(r["tab_key"] == tab_key and r["type"] == "function" for r in all_results):
                    all_results.append({
                        "name": item["label"],
                        "group": group_name,
                        "tab_key": tab_key,
                        "enabled": True,
                        "type": "config",
                        "parent_func": func_name,
                        "tooltip": item.get("tooltip", ""),
                        "config_keys": item.get("config_keys", ())
                    })
        except Exception:
            pass

        # 显示搜索结果
        self._show_search_results(all_results, keyword)

    def _show_search_results(self, results: list, keyword: str):
        """显示搜索结果"""
        if self.function_list is None:
            return

        self.function_list.clear()
        self.function_items = {}

        with self.function_list:
            # 搜索标题
            with ui.row().style('''
                width: 100%;
                padding: var(--spacing-md);
                border-bottom: 1px solid var(--border-color);
                align-items: center;
            '''):
                ui.icon('search').style('margin-right: 8px; color: var(--color-primary);')
                ui.label(f'搜索结果: "{keyword}"').style('''
                    font-size: var(--font-size-card-title);
                    font-weight: var(--font-weight-title);
                    color: var(--text-title);
                ''')

            # 搜索结果列表
            if not results:
                ui.label("未找到匹配的功能或配置项").style('''
                    text-align: center;
                    color: var(--text-hint);
                    padding: 40px;
                    font-size: var(--font-size-body);
                ''')
            else:
                # 分离功能项和配置项
                function_results = [r for r in results if r.get("type") == "function"]
                config_results = [r for r in results if r.get("type") == "config"]

                # 显示功能项
                if function_results:
                    with ui.row().style('''
                        width: 100%;
                        padding: 8px 16px;
                        background: var(--bg-page);
                        border-bottom: 1px solid var(--border-color);
                    '''):
                        ui.label('功能模块').style('font-size: var(--font-size-small); color: var(--text-secondary); font-weight: var(--font-weight-title);')

                    for result in function_results:
                        func_name = result["name"]
                        group_name = result["group"]

                        # 存储功能信息
                        self.function_items[func_name] = {
                            "enabled": result.get("enabled", True),
                            "tab_key": result.get("tab_key", "")
                        }

                        # 结果项
                        with ui.column().style('''
                            width: 100%;
                            cursor: pointer;
                            padding: 12px 16px;
                            border-bottom: 1px solid var(--divider-color);
                        ''').on('click', lambda e, name=func_name, group=group_name: self._on_search_result_click(name, group)):
                            with ui.row().style('align-items: center; gap: 8px;'):
                                ui.icon('folder').style('color: var(--color-primary); font-size: 18px;')
                                ui.label(func_name).style('''
                                    font-size: var(--font-size-body);
                                    font-weight: var(--font-weight-emphasis);
                                    color: var(--text-body);
                                ''')
                            ui.label(f'{group_name}').style('''
                                font-size: var(--font-size-small);
                                color: var(--text-hint);
                                margin-top: 4px;
                                margin-left: 26px;
                            ''')

                # 显示配置项
                if config_results:
                    with ui.row().style('''
                        width: 100%;
                        padding: 8px 16px;
                        background: var(--bg-page);
                        border-bottom: 1px solid var(--border-color);
                    '''):
                        ui.label('配置参数').style('font-size: var(--font-size-small); color: var(--text-secondary); font-weight: var(--font-weight-title);')

                    for result in config_results:
                        label = result["name"]
                        group_name = result["group"]
                        parent_func = result.get("parent_func", "")
                        tooltip = result.get("tooltip", "")
                        tab_key = result.get("tab_key", "")

                        # 结果项
                        with ui.column().style('''
                            width: 100%;
                            cursor: pointer;
                            padding: 10px 16px;
                            border-bottom: 1px solid var(--divider-color);
                        ''').on('click', lambda e, name=parent_func, group=group_name: self._on_search_result_click(name, group)):
                            with ui.row().style('align-items: center; gap: 8px;'):
                                ui.icon('settings').style('color: var(--text-hint); font-size: 16px;')
                                ui.label(label).style('''
                                    font-size: var(--font-size-small);
                                    font-weight: var(--font-weight-emphasis);
                                    color: var(--text-body);
                                ''')
                            with ui.row().style('margin-left: 24px; margin-top: 2px; gap: 4px;'):
                                ui.label(f'所在模块: {parent_func}').style('font-size: var(--font-size-tiny); color: var(--color-primary);')
                                ui.label(f'· {group_name}').style('font-size: var(--font-size-tiny); color: var(--text-hint);')
                            if tooltip:
                                ui.label(tooltip[:60] + ('...' if len(tooltip) > 60 else '')).style('''
                                    font-size: var(--font-size-tiny);
                                    color: var(--text-hint);
                                    margin-left: 24px;
                                    margin-top: 2px;
                                ''')

    def _on_search_result_click(self, function_name: str, group_name: str):
        """点击搜索结果"""
        # 先选择分组
        self.current_group = group_name
        # 显示功能列表
        self.show_function_list(group_name)
        # 显示配置页面
        self.show_config_page(function_name)

    def select_group(self, group_name: str):
        """选择功能分组"""
        self.current_group = group_name
        self.current_function = None  # 重置当前选中的功能

        # 重新渲染分组导航以更新选中状态
        function_groups = self.config.get("webui", {}).get("function_groups", [])
        self._render_group_navigation(function_groups)

        # 显示加载状态
        if self.function_list is not None:
            self.function_list.clear()
            with self.function_list:
                ui.spinner(size='lg', color='primary').style('''
                    position: absolute;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                ''')
                ui.label("加载中...").style('''
                    position: absolute;
                    top: 60%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    color: var(--text-secondary);
                    font-size: var(--font-size-small);
                ''')

        # 模拟加载延迟后显示功能列表
        ui.timer(0.3, lambda: self._show_function_list_deferred(group_name), once=True)

    def _show_function_list_deferred(self, group_name: str):
        """延迟显示功能列表"""
        self.show_function_list(group_name)

    def get_group_functions(self, group_name: str) -> list:
        """获取分组下的功能列表"""
        function_groups = self.config.get("webui", {}).get("function_groups", [])
        for group in function_groups:
            if group["name"] == group_name:
                return group.get("functions", [])
        return []

    def show_function_list(self, group_name: str):
        """显示功能列表 - 单列列表布局"""
        # 获取功能列表
        functions = self.get_group_functions(group_name)

        # 清空功能项
        self.function_items = {}

        # 将容器放置到 splitter.before 中
        if self.function_list is not None:
            self.function_list.clear()
            with self.function_list:
                # 分组标题
                with ui.row().style('''
                    width: 100%;
                    padding: 12px 16px;
                    border-bottom: 1px solid var(--border-color);
                    align-items: center;
                    background: var(--bg-page);
                '''):
                    ui.label(group_name).style('''
                        font-size: var(--font-size-section-title);
                        font-weight: var(--font-weight-title);
                        color: var(--text-title);
                    ''')

                # 功能列表容器（单列列表）
                self.function_list_container = ui.column().classes('hide-scrollbar').style('''
                    width: 100%;
                    flex: 1;
                    overflow-y: auto;
                ''')

                # 渲染功能列表
                self._render_function_list(group_name, functions)

        # 默认显示第一个功能的配置页面（DG-B9：拦截移除——关闭的功能同样可达）
        if functions:
            first_func = functions[0]
            self.current_function = first_func["name"]
            self._render_function_list(group_name, functions)
            self.show_config_page(first_func["name"])

    def _render_function_list(self, group_name: str, functions: list, filter_keyword: str = ""):
        """渲染功能列表 - 单列列表布局"""
        if self.function_list_container is None:
            return

        self.function_list_container.clear()

        with self.function_list_container:
            # 过滤功能列表
            filtered_functions = functions
            if filter_keyword:
                keyword_lower = filter_keyword.lower()
                filtered_functions = [
                    f for f in functions
                    if keyword_lower in f["name"].lower()
                ]

            if not filtered_functions:
                ui.label("未找到匹配的功能").style('''
                    text-align: center;
                    color: var(--text-hint);
                    padding: 32px;
                    font-size: var(--font-size-body);
                ''')
                return

            # 当前选中的功能名称（用于高亮）
            current_selected = self.current_group

            # 渲染功能列表项
            for idx, func in enumerate(filtered_functions):
                # 存储功能信息（任务6：enabled 随迁移退役，仅存 tab_key）
                self.function_items[func["name"]] = {
                    "tab_key": func.get("tab_key", "")
                }

                # 功能列表项
                func_name = func["name"]
                is_selected = (self.current_function == func_name)

                # 选中状态样式
                item_bg = 'var(--bg-selected)' if is_selected else 'transparent'
                item_border_left = '3px solid var(--color-primary)' if is_selected else '3px solid transparent'
                name_color = 'var(--text-title)' if is_selected else 'var(--text-body)'
                name_weight = 'var(--font-weight-title)' if is_selected else 'var(--font-weight-emphasis)'

                # 列表项容器
                with ui.column().style(f'''
                    width: 100%;
                    cursor: pointer;
                    padding: 12px 16px;
                    border-bottom: 1px solid var(--divider-color);
                    border-left: {item_border_left};
                    background: {item_bg};
                    transition: all var(--transition-fast);
                ''') as list_item:
                    # 悬停效果 - 只对未选中项生效
                    def make_hover_handler(selected):
                        def on_mouse_enter(e):
                            if not selected:
                                e.sender.style('background-color: var(--bg-hover)')
                        def on_mouse_leave(e):
                            if not selected:
                                e.sender.style('background-color: transparent')
                        return on_mouse_enter, on_mouse_leave
                    enter_handler, leave_handler = make_hover_handler(is_selected)
                    list_item.on('mouseenter', enter_handler)
                    list_item.on('mouseleave', leave_handler)
                    # 任务5：点击热区上移到整个列表项容器（原实现只绑在功能名 label 上，
                    # 点 padding 空白区/描述行不生效）。启用开关经 js_handler stop 冒泡，
                    # 避免开关点击误触发行选中（保护 toggle_function 语义）。
                    list_item.on('click', lambda e, name=func_name: self._on_function_click(name))

                    # 第一行：功能名称和启用开关（任务6：仅"有启用"功能渲染开关，
                    # 直绑业务路径；无启用功能不渲染——D-B1；min-height 统一行高）
                    with ui.row().style('''
                        width: 100%;
                        align-items: center;
                        justify-content: space-between;
                        min-height: 36px;
                    '''):
                        # 功能名称（点击行为已上移到容器，跟随容器热区）
                        ui.label(func_name).style(f'''
                            font-size: var(--font-size-body);
                            font-weight: {name_weight};
                            color: {name_color};
                            flex: 1;
                            transition: all var(--transition-fast);
                        ''')

                        # 启用开关（D-B1/E-B2/DG-B7：绑业务路径，初值逐字一致，
                        # toast 替换式，aria-label，scale 0.85；stop 冒泡保留）
                        enable_path = ENABLE_PATHS.get(func.get("tab_key", ""))
                        if enable_path:
                            ui.switch(
                                value=bool(get_nested_value(self.config, *enable_path)),
                                on_change=lambda e, name=func_name, path=enable_path: self._on_enable_switch(name, path, e)
                            ).style('transform: scale(0.85);').props(
                                f'aria-label={func_name}'
                            ).on('click', js_handler='(e) => e.stopPropagation()')

                    # 第二行：功能描述（可选）
                    if func.get("description"):
                        ui.label(func["description"]).style('''
                            font-size: var(--font-size-small);
                            color: var(--text-hint);
                            margin-top: 4px;
                        ''')

    def _on_function_click(self, function_name: str):
        """点击功能项时的处理"""
        # 设置当前选中的功能
        self.current_function = function_name

        # 重新渲染功能列表以更新选中状态
        if self.current_group:
            functions = self.get_group_functions(self.current_group)
            self._render_function_list(self.current_group, functions)

        # 显示配置页面
        self.show_config_page(function_name)

    def filter_functions(self, group_name: str, keyword: str):
        """过滤功能列表"""
        functions = self.get_group_functions(group_name)
        self._render_function_list(group_name, functions, keyword)

    def _on_enable_switch(self, func_name: str, path: tuple, e):
        """二级菜单启用开关（任务6/D-B1）：写业务路径，失败双回滚（F1/F2）。

        幂等护栏：e.value == config 当前值时直接返回——回滚赋值会再触发本
        处理器，此时两者相等，自然终止，无循环无重复 toast。
        """
        current = get_nested_value(self.config, *path)
        if bool(e.value) == bool(current):
            return

        set_cb = self._create_set_config_callback()
        ok = set_cb(*path, value=bool(e.value), _notify=False)
        if ok:
            ui.notify(position="top", type="positive",
                      message=f"已{'开启' if e.value else '关闭'} {func_name}")
            # DG-B1：当前配置页状态条实时同步
            if (self.config_status_label is not None
                    and self._current_page_enable_path == path):
                self.config_status_label.set_visibility(not bool(e.value))
        else:
            # UI 回滚（内存字典已由 set_config 恢复旧值）；回滚赋值若再触发
            # 本处理器，命中幂等护栏自然返回
            e.sender.value = bool(current)

    def show_config_page(self, function_name: str):
        """显示配置页面"""

        # 直接渲染配置页面
        self.render_config_page(function_name)

    def _render_config_page_deferred(self, function_name: str):
        """延迟渲染配置页面"""
        try:
            self.render_config_page(function_name)
        except Exception as e:
            # 显示错误状态
            if self.config_page is not None:
                self.config_page.clear()
                with self.config_page:
                    ui.icon('error', color='red').style('''
                        font-size: 48px;
                        margin-bottom: 16px;
                    ''')
                    ui.label("加载配置失败").style('''
                        font-size: var(--font-size-card-title);
                        font-weight: var(--font-weight-title);
                        color: var(--color-danger);
                        margin-bottom: 8px;
                    ''')
                    ui.label(str(e)).style('''
                        font-size: var(--font-size-body);
                        color: var(--text-secondary);
                        margin-bottom: 16px;
                    ''')
                    ui.button(
                        "重试",
                        on_click=lambda: self.show_config_page(function_name)
                    ).style('''
                        background-color: var(--color-primary);
                        color: var(--text-on-primary);
                    ''')

    def render_config_page(self, function_name: str):
        """渲染配置页面 - 调用实际的tab模块函数"""
        # 清除之前的内容
        if self.config_page is not None:
            self.config_page.clear()

        # 获取 tab_key
        tab_key = self.function_items.get(function_name, {}).get("tab_key", function_name)

        # 将配置页面放置到 config_page 中
        if self.config_page is not None:
            self.config_page.clear()
            with self.config_page:
                # 页面标题（统一层级：页面标题）
                with ui.row().style('''
                    width: 100%;
                    padding: var(--spacing-md) 20px;
                    border-bottom: 1px solid var(--border-color);
                    align-items: center;
                    background: var(--bg-card);
                '''):
                    ui.label(function_name).style('''
                        font-size: var(--font-size-page-title);
                        font-weight: var(--font-weight-title);
                        color: var(--text-title);
                    ''')

                # DG-B1 关闭态状态条（仅"有启用开关"的功能渲染；实时同步引用
                # self.config_status_label，由 _on_enable_switch 定点更新）
                enable_path = ENABLE_PATHS.get(tab_key)
                self._current_page_enable_path = enable_path
                self.config_status_label = None
                if enable_path:
                    is_on = bool(get_nested_value(self.config, *enable_path))
                    with ui.row().style('''
                        width: 100%;
                        padding: 8px 20px;
                        align-items: center;
                        gap: 8px;
                        background: var(--bg-page);
                        border-bottom: 1px solid var(--divider-color);
                    ''') as status_row:
                        ui.icon('info').style(
                            'font-size: 16px; color: var(--color-warning);')
                        self.config_status_label = ui.label(
                            "该功能当前已关闭（左侧列表开关），参数修改保存但暂不生效"
                        ).style('font-size: var(--font-size-small); color: var(--color-warning);')
                    status_row.set_visibility(not is_on)

                # 内容区域
                with ui.column().classes('config-content').style('''
                    width: 100%;
                    padding: 20px;
                    overflow-y: auto;
                    flex: 1;
                '''):
                    # 根据 tab_key 调用对应的 tab 模块函数
                    self._render_tab_content(tab_key)

    def _render_tab_content(self, tab_key: str):
        """根据 tab_key 渲染对应的 tab 内容"""
        # 导入 tab 模块
        from frontend.ui.tabs import (
            common_config, llm, tts, svc, visual_body, copywriting,
            talk, image_recognition, integral, assistant_anchor,
            translate, serial, data_analysis,
            audio_play, web_captions_printer, log_config,
            filter_config, filter_forget, filter_dedup, filter_queue,
            blacklist, read_comment, thanks, schedule, idle_time_task,
            custom_cmd, trends_copywriting, sd_config, local_qa,
            choose_song, search_online, key_mapping, luoxi, database_config,
            system_settings, platform_config, train_streamer,
            train_voice
        )

        # 获取主题配置 - 使用简化的浅色/深色模式
        from frontend.styles.themes import get_theme_manager
        theme_manager = get_theme_manager()
        theme_config = theme_manager.get_theme_config()

        set_cb = self._create_set_config_callback()

        # 定义 tab_key 到模块函数的映射
        tab_mapping = {
            # 基础功能
            'common_config': lambda: common_config.create_common_config_tab(self.config, theme_config, set_cb),
            'platform_config': lambda: platform_config.create_platform_config_tab(self.config, theme_config, set_cb),
            'audio_play': lambda: audio_play.create_audio_play_tab(self.config, theme_config, set_cb),
            'web_captions_printer': lambda: web_captions_printer.create_web_captions_printer_tab(self.config, theme_config, set_cb),
            'log_config': lambda: log_config.create_log_tab(self.config, theme_config, set_cb),
            # AI对话
            'llm': lambda: llm.create_llm_tab(self.config, theme_config, set_cb),
            'tts': lambda: tts.create_tts_tab(self.config, theme_config, set_cb),
            'svc': lambda: svc.create_svc_tab(self.config, theme_config, set_cb),
            'talk': lambda: talk.create_talk_tab(self.config, theme_config, set_cb),
            'image_recognition': lambda: image_recognition.create_image_recognition_tab(self.config, theme_config, set_cb),
            'trends_copywriting': lambda: trends_copywriting.create_trends_copywriting_tab(self.config, theme_config, set_cb),
            'sd_config': lambda: sd_config.create_sd_config_tab(self.config, theme_config, set_cb),
            # 互动设置
            'integral': lambda: integral.create_integral_tab(self.config, theme_config, set_cb),
            'assistant_anchor': lambda: assistant_anchor.create_assistant_anchor_tab(self.config, theme_config, set_cb),
            'translate': lambda: translate.create_translate_tab(self.config, theme_config, set_cb),
            'serial': lambda: serial.create_serial_tab(self.config, theme_config, set_cb),
            'filter_config': lambda: filter_config.create_filter_config_tab(self.config, theme_config, set_cb),
            'filter_forget': lambda: filter_forget.create_filter_forget_tab(self.config, theme_config, set_cb),
            'filter_dedup': lambda: filter_dedup.create_filter_dedup_tab(self.config, theme_config, set_cb),
            'filter_queue': lambda: filter_queue.create_filter_queue_tab(self.config, theme_config, set_cb),
            'blacklist': lambda: blacklist.create_blacklist_tab(self.config, theme_config, set_cb),
            'read_comment': lambda: read_comment.create_read_comment_tab(self.config, theme_config, set_cb),
            'thanks': lambda: thanks.create_thanks_tab(self.config, theme_config, set_cb),
            'schedule': lambda: schedule.create_schedule_tab(self.config, theme_config, set_cb),
            'idle_time_task': lambda: idle_time_task.create_idle_time_task_tab(self.config, theme_config, set_cb),
            'custom_cmd': lambda: custom_cmd.create_custom_cmd_tab(self.config, theme_config, set_cb),
            # 高级功能
            'visual_body': lambda: visual_body.create_visual_body_tab(self.config, theme_config, set_cb),
            'train_streamer': lambda: train_streamer.create_train_streamer_tab(self.config, theme_config, set_cb),
            'train_voice': lambda: train_voice.create_train_voice_tab(self.config, theme_config, set_cb),
            'copywriting': lambda: copywriting.create_copywriting_tab(self.config, theme_config, set_cb),
            'data_analysis': lambda: data_analysis.create_data_analysis_tab(self.config, theme_config, set_cb),
            'local_qa': lambda: local_qa.create_local_qa_tab(self.config, theme_config, set_cb),
            'choose_song': lambda: choose_song.create_choose_song_tab(self.config, theme_config, set_cb),
            'search_online': lambda: search_online.create_search_online_tab(self.config, theme_config, set_cb),
            'key_mapping': lambda: key_mapping.create_key_mapping_tab(self.config, theme_config, set_cb),
            'luoxi': lambda: luoxi.create_luoxi_tab(self.config, theme_config, set_cb),
            'database_config': lambda: database_config.create_database_tab(self.config, theme_config, set_cb),
            # 系统设置
            'webui_config': lambda: system_settings.create_webui_config_tab(self.config, theme_config, set_cb),
            'local_dir_endpoint': lambda: system_settings.create_local_dir_endpoint_tab(self.config, theme_config, set_cb),
            'config_template': lambda: system_settings.create_config_template_tab(self.config, theme_config, set_cb),
            'account_manage': lambda: system_settings.create_account_manage_tab(self.config, theme_config, set_cb),
            'trends_config': lambda: system_settings.create_trends_config_tab(self.config, theme_config, set_cb),
            'abnormal_alarm': lambda: system_settings.create_abnormal_alarm_tab(self.config, theme_config, set_cb),
            'coordination_program': lambda: system_settings.create_coordination_program_tab(self.config, theme_config, set_cb),
        }

        # 渲染对应的 tab 内容
        if tab_key in tab_mapping:
            try:
                tab_mapping[tab_key]()
            except Exception as e:
                ui.label(f"加载配置失败: {str(e)}").style('''
                    color: var(--color-danger);
                    padding: 20px;
                    text-align: center;
                ''')
                ui.button(
                    "重试",
                    on_click=lambda: self._render_tab_content(tab_key)
                ).classes('q-mt-md')
        else:
            # 没有对应的 tab 模块，显示提示信息
            ui.label(f"功能 {tab_key} 暂无配置页面").style('''
                text-align: center;
                color: var(--text-hint);
                padding: 40px;
                font-size: var(--font-size-card-title);
            ''')

    def _create_set_config_callback(self):
        """创建配置回调函数"""
        def set_config(*keys, value, _notify=True):
            """设置配置值并落盘。

            E-F1/F6：save 失败时回滚内存字典旧值并返回 False（异常不再吞掉）；
            成功时默认弹「配置已保存」，开关类调用方传 _notify=False 自定义文案。
            """
            config = self.config
            for key in keys[:-1]:
                if key not in config:
                    config[key] = {}
                config = config[key]

            old_value = config.get(keys[-1])
            config[keys[-1]] = value
            try:
                self.save_config()
            except Exception as e:
                # 双回滚之一：内存字典恢复旧值（UI 回滚由调用方负责）
                config[keys[-1]] = old_value
                logger.error(f"配置保存失败 ({'->'.join(keys)}): {e}")
                ui.notify(position="top", type="negative", message="配置保存失败，请查看日志")
                return False

            if _notify:
                ui.notify(
                    position="top",
                    type="positive",
                    message="配置已保存"
                )
            return True

        return set_config

    def create_content_area(self):
        """
        创建内容区

        Returns:
            ui.row 对象
        """
        with ui.row().style('''
            width: 100%;
            height: calc(100vh - 60px);
            overflow: hidden;
        ''') as content_row:
            # 二级菜单（功能列表）
            self.function_list = ui.column().classes('hide-scrollbar').style('''
                background: var(--bg-card);
                border-right: 1px solid var(--border-color);
                overflow-y: auto;
                overflow-x: hidden;
                width: 300px;
                min-width: 300px;
                max-width: 300px;
                flex-shrink: 0;
                height: 100%;
            ''')

            # 三级菜单（配置页面）
            self.config_page = ui.column().classes('hide-scrollbar').style('''
                background: var(--bg-page);
                overflow-y: auto;
                flex: 1;
                min-width: 0;
                height: 100%;
            ''')

        self.content_area = content_row
        return content_row

    def create_function_list(self, functions: list = None):
        """
        创建功能列表

        Args:
            functions: 功能列表

        Returns:
            ui.column 对象
        """
        if self.function_list is None:
            self.function_list = ui.column()

        return self.function_list

    def create_config_page(self, config_items: list = None):
        """
        创建配置页面

        Args:
            config_items: 配置项列表

        Returns:
            ui.column 对象
        """
        if self.config_page is None:
            self.config_page = ui.column()

        return self.config_page