from frontend.ui.components.config_helper import get_nested_value
"""
UI布局模块
负责创建主界面布局
"""
from nicegui import ui
from typing import Dict, Any, Optional


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
        /* 参数标题（表单 label） */
        .q-field .q-field__label {{
            font-size: var(--font-size-small);
            font-weight: var(--font-weight-normal);
            color: var(--text-secondary);
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

    def __init__(self, config: Dict[str, Any], save_callback=None):
        """
        初始化 Drawer 布局

        Args:
            config: 配置字典
            save_callback: 保存配置的回调函数
        """
        self.config = config
        self.save_callback = save_callback
        self.tabs = {}
        self.tab_panels = {}
        self.drawer = None
        self.content_area = None
        self.function_list = None
        self.config_page = None
        self.drawer_mini = config.get("webui", {}).get("drawer_mini", False)
        self.dark_mode = None
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
        self.system_status = "stopped"
        self.system_button_text = "运行系统"
        self.system_button_color = "var(--color-success)"
        self.tabs_mapping = {}
        self.theme_button = None

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
        # 保存状态到配置
        if "webui" not in self.config:
            self.config["webui"] = {}
        self.config["webui"]["drawer_mini"] = self.drawer_mini
        # 保存配置到文件
        self.save_config()

    def get_drawer_width(self) -> str:
        """获取导航栏宽度"""
        return '60px' if self.drawer_mini else '300px'

    def save_config(self):
        """保存配置到文件"""
        if self.save_callback:
            try:
                self.save_callback()
            except Exception as e:
                print(f"保存配置失败: {e}")

    def toggle_theme(self):
        """切换深色/浅色主题"""
        if self.dark_mode is None:
            self.dark_mode = ui.dark_mode()

        # NiceGUI 的 dark_mode 是一个布尔值
        self.dark_mode.value = not self.dark_mode.value

        # 更新按钮文本
        if hasattr(self, 'theme_button') and self.theme_button:
            self.theme_button.text = "深色模式" if self.dark_mode.value else "浅色模式"

        # 保存主题偏好
        new_theme = "dark" if self.dark_mode.value else "light"
        self.save_theme_preference(new_theme)

        # 显示切换通知
        ui.notify(
            position="top",
            type="info",
            message=f"已切换到{'深色' if self.dark_mode.value else '浅色'}模式"
        )

    def save_theme_preference(self, theme: str):
        """保存主题偏好"""
        # 简化实现，只保存 light 或 dark
        pass

    def load_theme_preference(self) -> str:
        """加载主题偏好"""
        return "dark" if self.dark_mode and self.dark_mode.value else "light"


    def update_system_status(self, status: str):
        """更新系统状态"""
        self.system_status = status
        if status == "running":
            self.system_button_text = "停止运行"
            self.system_button_color = "var(--color-danger)"
        elif status == "stopped":
            self.system_button_text = "运行系统"
            self.system_button_color = "var(--color-success)"

    def toggle_system(self):
        """切换系统运行/停止状态"""
        if self.system_status == "running":
            self.stop_system()
        else:
            self.start_system()

    def start_system(self):
        """启动系统"""
        if self.system_status != "running":
            self.update_system_status("running")

    def stop_system(self):
        """停止系统"""
        if self.system_status != "stopped":
            self.update_system_status("stopped")

    def is_system_running(self) -> bool:
        """检查系统是否运行"""
        return self.system_status == "running"

    def show_running_status_page(self):
        """显示运行状态页面"""
        if self.config_page is not None:
            self.config_page.clear()
            with self.config_page:
                ui.label("运行状态").style('font-size: var(--font-size-page-title); font-weight: var(--font-weight-title); color: var(--text-title); margin-bottom: 10px;')
                ui.label("管理系统运行状态").style('margin-bottom: 15px; color: var(--text-secondary);')

                with ui.card().style('margin-bottom: 10px; padding: 15px;'):
                    ui.label("系统状态").style('font-weight: var(--font-weight-title); margin-bottom: 5px; color: var(--text-title);')
                    ui.label(f"当前状态: {self.system_status}").style('margin-bottom: 10px;')

                    # 系统状态按钮
                    self.system_button = ui.button(
                        self.system_button_text,
                        on_click=self.toggle_system
                    ).style(f'background-color: {self.system_button_color};').classes('w-full')

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
                ui.label("AI Vtuber").style('''
                    font-size: var(--font-size-card-title);
                    font-weight: var(--font-weight-title);
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

            # 底部工具栏
            with ui.column().style('''
                width: 100%;
                padding: var(--spacing-md);
                margin-top: auto;
                border-top: 1px solid var(--border-color);
                background: var(--bg-page);
            '''):
                # 主题切换按钮
                self.theme_button = ui.button(
                    "深色模式" if self.dark_mode and self.dark_mode.value else "浅色模式",
                    on_click=self.toggle_theme
                ).props('icon=dark_mode flat').style('''
                    width: 100%;
                    justify-content: flex-start;
                    text-transform: none;
                    font-weight: var(--font-weight-emphasis);
                    margin: 2px 0;
                    border-radius: var(--radius-md);
                    transition: all var(--transition-fast);
                    color: var(--text-body);
                ''')

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

                with ui.row().style(f'''
                    width: 100%;
                    position: relative;
                    cursor: pointer;
                    padding: 8px 12px;
                    align-items: center;
                    background: {bg_color};
                    border-left: {border_left};
                    transition: all var(--transition-fast);
                ''').on('click', lambda e, g=group_name: self.select_group(g)):
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

        # 默认显示第一个功能的配置页面
        if functions:
            first_func = functions[0]
            if first_func.get("enabled", True):
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
                # 存储功能信息
                self.function_items[func["name"]] = {
                    "enabled": func.get("enabled", True),
                    "tab_key": func.get("tab_key", "")
                }

                # 功能列表项
                func_name = func["name"]
                is_enabled = func.get("enabled", True)
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

                    # 第一行：功能名称和启用开关
                    with ui.row().style('''
                        width: 100%;
                        align-items: center;
                        justify-content: space-between;
                    '''):
                        # 功能名称（可点击）
                        ui.label(func_name).style(f'''
                            font-size: var(--font-size-body);
                            font-weight: {name_weight};
                            color: {name_color};
                            flex: 1;
                            cursor: pointer;
                            transition: all var(--transition-fast);
                        ''').on('click', lambda e, name=func_name: self._on_function_click(name))

                        # 启用开关
                        ui.switch(
                            value=is_enabled,
                            on_change=lambda e, name=func_name: self.toggle_function(name, e.value)
                        ).style('transform: scale(0.7);')

                    # 第二行：功能描述（可选）
                    if func.get("description"):
                        ui.label(func["description"]).style('''
                            font-size: var(--font-size-small);
                            color: var(--text-hint);
                            margin-top: 4px;
                        ''')

    def _on_function_click(self, function_name: str):
        """点击功能项时的处理"""
        # 检查功能是否启用
        if function_name in self.function_items:
            func_info = self.function_items[function_name]
            if not func_info.get("enabled", True):
                ui.notify(
                    position="top",
                    type="warning",
                    message=f"功能 {function_name} 已禁用"
                )
                return

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

    def toggle_function(self, function_name: str, enabled: bool):
        """切换功能启用/禁用状态"""
        # 更新 function_items
        if function_name in self.function_items:
            self.function_items[function_name]["enabled"] = enabled

        # 更新配置
        function_groups = self.config.get("webui", {}).get("function_groups", [])
        for group in function_groups:
            for func in group.get("functions", []):
                if func["name"] == function_name:
                    func["enabled"] = enabled
                    # 显示状态切换通知
                    ui.notify(
                        position="top",
                        type="info",
                        message=f"功能 {function_name} 已{'启用' if enabled else '禁用'}"
                    )
                    return

    def show_config_page(self, function_name: str):
        """显示配置页面"""
        # 检查功能是否启用
        if function_name not in self.function_items:
            return

        func_info = self.function_items[function_name]
        if not func_info.get("enabled", True):
            # 功能禁用，不显示配置页面
            if self.config_page is not None:
                self.config_page.clear()
                with self.config_page:
                    ui.label("功能已禁用").style('''
                        text-align: center;
                        color: var(--text-hint);
                        padding: 40px;
                        font-size: var(--font-size-card-title);
                    ''')
            return

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

                # 内容区域
                with ui.column().style('''
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
            translate, serial, data_analysis, web,
            audio_play, web_captions_printer, log_config,
            filter_config, filter_forget, filter_dedup, filter_queue,
            blacklist, read_comment, thanks, schedule, idle_time_task,
            custom_cmd, trends_copywriting, sd_config, local_qa,
            choose_song, search_online, key_mapping, luoxi, database_config,
            system_settings, platform_config
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
            'show_card': lambda: system_settings.create_show_card_tab(self.config, theme_config, set_cb),
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
        def set_config(*keys, value):
            """设置配置值"""
            # 更新配置字典
            config = self.config
            for key in keys[:-1]:
                if key not in config:
                    config[key] = {}
                config = config[key]
            config[keys[-1]] = value

            # 保存配置到文件
            self.save_config()

            # 显示保存成功通知
            ui.notify(
                position="top",
                type="positive",
                message="配置已保存"
            )

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