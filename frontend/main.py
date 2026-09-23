from frontend.ui.components.config_helper import get_nested_value
"""
AI Vtuber 主入口文件
模块化的WebUI应用程序
"""
import sys
import os
import signal
import subprocess
import traceback
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from nicegui import ui, app

# 导入配置模块
from frontend.config.settings import init_config, get_config
from frontend.config.paths import get_base_path, get_config_path, get_log_dir, get_output_dir

# 导入工具模块
from frontend.utils.helpers import SystemCommand
from frontend.utils.common import textarea_data_change

# 导入UI模块
from frontend.ui.components import (
    NavigationTabs,
    ControlButtons,
    LoginForm,
    ThemeManager,
    SearchFilter,
    ExpansionPanel,
    FormField
)

# 导入新的 DrawerLayout
from frontend.ui.layout import DrawerLayout

# 导入标签页模块
from frontend.ui.tabs import (
    common_config,
    llm,
    tts,
    svc,
    visual_body,
    copywriting,
    talk,
    image_recognition,
    integral,
    assistant_anchor,
    translate,
    serial,
    data_analysis,
    web,
    audio_play,
    web_captions_printer,
    log_config,
    filter_config,
    filter_forget,
    filter_dedup,
    filter_queue,
    blacklist,
    read_comment,
    thanks,
    schedule,
    idle_time_task,
    custom_cmd,
    trends_copywriting,
    sd_config,
    local_qa,
    choose_song,
    search_online,
    key_mapping,
    luoxi,
    database_config,
    system_settings,
    platform_config
)

# 导入样式模块
from frontend.styles.themes import init_theme_manager, get_theme_manager

# 导入日志模块
from utils.my_log import logger


class AIVtuberApp:
    """AI Vtuber 应用程序类"""
    
    def __init__(self):
        """初始化应用程序"""
        # 初始化基础路径
        self.base_path = get_base_path()
        
        # 初始化配置（必须在 _init_directories 之前）
        config_path = get_config_path()
        self.config = init_config(str(config_path))
        
        # 初始化目录
        self._init_directories()
        
        # 初始化主题管理器
        self.theme_manager = init_theme_manager(self.config._config)
        
        # 初始化UI组件
        self.navigation_tabs = NavigationTabs(self.config._config)
        self.control_buttons = ControlButtons(self.config._config)
        self.login_form = LoginForm(self.config._config)
        self.theme_manager_ui = ThemeManager(self.config._config)

        # 状态变量
        self.running_flag = False
        self.user_info = None

        # 存储运行的子进程
        self.my_subprocesses = {}

        # 暗夜模式
        self.dark_mode = ui.dark_mode()

        # 初始化Audio类（与原文件 webui-bak.py L141 一致）
        try:
            from utils.audio import Audio
            self.audio = Audio(str(get_config_path()), type=2)
            logger.info("Audio初始化完成")
        except Exception as e:
            logger.warning(f"Audio初始化失败: {e}")
            self.audio = None

        # 初始化数据分析模块（与原文件 webui-bak.py L4939-4941 一致）
        try:
            from utils.data_analysis import Data_Analysis
            self.data_analysis = Data_Analysis(str(get_config_path()))
            logger.info("数据分析模块初始化完成")
        except Exception as e:
            logger.warning(f"数据分析模块初始化失败: {e}")
            self.data_analysis = None

        logger.info("AI Vtuber 应用程序初始化完成")
    
    def _init_directories(self):
        """初始化必要的目录"""
        # 日志目录
        log_dir = get_log_dir()
        logger.debug(f"日志目录: {log_dir}")
        
        # 输出目录
        output_dir = get_output_dir()
        logger.debug(f"输出目录: {output_dir}")
        
        # 初始化静态文件目录
        if get_nested_value(self.config._config, "webui", "local_dir_to_endpoint", "enable"):
            for tmp in get_nested_value(self.config._config, "webui", "local_dir_to_endpoint", "config"):
                app.add_static_files(tmp['url_path'], tmp['local_dir'])
        
        logger.debug("项目相关文件夹初始化完成")
    
    def set_config(self, *keys, value):
        """
        设置配置值
        
        Args:
            *keys: 配置键路径
            value: 配置值
        """
        self.config.set(*keys, value=value)
        self.config.save()
    
    def save_config(self):
        """保存配置到文件（与原文件 webui-bak.py L2710-2759 一致）"""
        try:
            # 从UI组件读取配置值
            from frontend.ui.tabs.common_config import save_common_config
            config_data = save_common_config(self.config._config)

            # 更新配置
            self.config._config.update(config_data)

            # 保存到文件
            self.config.save()
            ui.notify(position="top", type="positive", message="配置保存成功")
            logger.info("配置保存成功")
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"配置保存失败: {e}")
            logger.error(f"配置保存失败: {e}")
    
    def start_programs(self):
        """根据配置启动所有程序（与原文件 webui-bak.py L211-243 一致）"""
        config_data = self.config._config

        # 启动协同程序
        for program in config_data.get("coordination_program", []):
            if not program.get("enable", False):
                continue

            name = program.get("name", "")
            executable = program.get("executable", "")
            parameters = program.get("parameters", [])
            app_path = parameters[0] if parameters else ""

            # 从 app.py 的路径中提取目录
            app_dir = os.path.dirname(app_path) if app_path else "."

            # 使用 Python 解释器路径和 app.py 路径构建命令
            cmd = [executable, app_path]

            logger.info(f"运行程序: {name} 位于: {app_dir}")

            try:
                # 在 app.py 文件所在的目录中启动程序
                process = subprocess.Popen(cmd, cwd=app_dir, shell=True)
                self.my_subprocesses[name] = process
            except Exception as e:
                logger.error(f"启动程序 {name} 失败: {e}")

        # 启动主程序 main.py（E3：用 webui 自身解释器，避免 PATH 解析到错误 python——
        # 定时任务/其它 shell 启动 webui 时尤其危险；原实现沿用 webui-bak 的 "python" 硬编码）
        name = "main"
        base_path = str(get_base_path())
        try:
            # 根据操作系统的不同，微调参数
            if sys.platform not in ['win32']:
                process = subprocess.Popen([sys.executable, "main.py"], cwd=base_path, shell=False)
            else:
                process = subprocess.Popen([sys.executable, "main.py"], cwd=base_path, shell=True)
            self.my_subprocesses[name] = process
            logger.info(f"运行程序: {name}")
        except Exception as e:
            logger.error(f"启动主程序失败: {e}")

    def stop_program(self, name):
        """停止一个正在运行的程序及其所有子进程（与原文件 webui-bak.py L246-269 一致）

        Args:
            name: 要停止的程序的名称
        """
        if name in self.my_subprocesses:
            pid = self.my_subprocesses[name].pid
            logger.info(f"停止程序和它所有的子进程: {name} with PID {pid}")

            try:
                if os.name == 'nt':  # Windows
                    command = ["taskkill", "/F", "/T", "/PID", str(pid)]
                    subprocess.run(command, check=True)
                else:  # POSIX系统，如Linux和macOS
                    os.killpg(os.getpgid(pid), signal.SIGKILL)

                logger.info(f"程序 {name} 和 它所有的子进程都被终止.")
            except Exception as e:
                logger.error(f"终止程序 {name} 失败: {e}")

            del self.my_subprocesses[name]
        else:
            logger.warning(f"程序 {name} 没有在运行.")

    def stop_programs(self):
        """根据配置停止所有程序（与原文件 webui-bak.py L271-282 一致）"""
        config_data = self.config._config

        # 停止协同程序
        for program in config_data.get("coordination_program", []):
            if not program.get("enable", False):
                continue
            self.stop_program(program.get("name", ""))

        # 停止主程序
        self.stop_program("main")

    def run_external_program(self, type="webui"):
        """运行外部程序（与原文件 webui-bak.py L357-382 一致）"""
        if self.running_flag:
            if type == "webui":
                ui.notify(position="top", type="warning", message="运行中，请勿重复运行")
            return

        try:
            self.running_flag = True

            # 启动协同程序和主程序
            self.start_programs()

            # 启动后一次性存活检查（E2）：3 秒后 poll 各子进程。部分失败场景下
            # start_programs 内部只 logger.error（沿用移植自 webui-bak 的行为），
            # 且 Windows shell=True 时命令不存在 Popen 也可能"假成功"——这里把
            # 秒退程序变成用户可见的 negative notify。不复位 running_flag：
            # 持续监控/自动恢复属独立议题（TODOS「main.py 子进程退出监控」条目）。
            def check_processes_alive():
                for name, proc in list(self.my_subprocesses.items()):
                    try:
                        if proc.poll() is not None:
                            logger.error(f"启动后检查: 程序 {name} 已退出 (code={proc.returncode})")
                            ui.notify(position="top", type="negative",
                                      message=f"程序 {name} 启动后已退出，请查看日志")
                    except Exception as e:
                        logger.error(f"启动后检查 {name} 失败: {e}")

            ui.timer(3.0, check_processes_alive, once=True)

            if type == "webui":
                ui.notify(position="top", type="positive", message="程序开始运行")
            logger.info("程序开始运行")

            return {"code": 200, "msg": "程序开始运行"}
        except Exception as e:
            if type == "webui":
                ui.notify(position="top", type="negative", message=f"错误：{e}")
            logger.error(traceback.format_exc())
            self.running_flag = False

            return {"code": -1, "msg": f"运行失败！{e}"}

    def stop_external_program(self, type="webui"):
        """停止外部程序（与原文件 webui-bak.py L386-403 一致）"""
        if self.running_flag:
            try:
                # 停止协同程序和主程序
                self.stop_programs()

                self.running_flag = False
                if type == "webui":
                    ui.notify(position="top", type="positive", message="程序已停止")
                logger.info("程序已停止")

                return {"code": 200, "msg": "程序已停止"}
            except Exception as e:
                if type == "webui":
                    ui.notify(position="top", type="negative", message=f"停止错误：{e}")
                logger.error(f"停止错误：{e}")

                return {"code": -1, "msg": f"停止失败！{e}"}
        else:
            if type == "webui":
                ui.notify(position="top", type="warning", message="程序未在运行")

            return {"code": 0, "msg": "程序未在运行"}
    
    def change_light_status(self):
        """改变暗夜模式状态"""
        self.dark_mode.value = not self.dark_mode.value
        if self.dark_mode.value:
            ui.notify(position="top", type="info", message="已切换到暗夜模式")
        else:
            ui.notify(position="top", type="info", message="已切换到日间模式")
    
    def restart_application(self, type="webui"):
        """重启应用程序（与原文件 webui-bak.py L415-427 一致）"""
        try:
            # 先停止运行
            self.stop_external_program(type)

            logger.info(f"重启webui")
            if type == "webui":
                ui.notify(position="top", type="ongoing", message=f"重启中...")
            python = sys.executable
            os.execl(python, python, *sys.argv)  # Start a new instance of the application
        except Exception as e:
            logger.error(f"重启失败: {e}")
            return {"code": -1, "msg": f"重启失败！{e}"}

    def factory(self, src_path='config.json.bak', dst_path='config.json', type="webui"):
        """恢复出厂配置（与原文件 webui-bak.py L430-451 一致）"""
        try:
            base_path = str(get_base_path())
            src_full = os.path.join(base_path, src_path)
            dst_full = os.path.join(base_path, dst_path)

            with open(src_full, 'r', encoding="utf-8") as source:
                with open(dst_full, 'w', encoding="utf-8") as destination:
                    destination.write(source.read())
            logger.info("恢复出厂配置成功！")
            if type == "webui":
                ui.notify(position="top", type="positive", message=f"恢复出厂配置成功！")

            # 重启
            self.restart_application()

            return {"code": 200, "msg": "恢复出厂配置成功！"}
        except Exception as e:
            logger.error(f"恢复出厂配置失败！\n{e}")
            if type == "webui":
                ui.notify(position="top", type="negative", message=f"恢复出厂配置失败！\n{e}")

            return {"code": -1, "msg": f"恢复出厂配置失败！\n{e}"}
    
    def scroll_to_top(self):
        """滚动到页面顶部"""
        ui.run_javascript('window.scrollTo({top: 0, behavior: "smooth"})')
    
    def goto_func_page(self):
        """跳转到功能页"""
        # 清除登录页面
        if hasattr(self, 'login_column') and self.login_column:
            self.login_column.clear()
        
        # 创建主界面
        self.create_main_ui()
    
    def check_login(self):
        """检查登录状态"""
        if not get_nested_value(self.config._config, "login", "enable"):
            # 不需要登录，直接进入功能页
            self.goto_func_page()
            return
        
        # 显示登录页面
        self.login_column = self.login_form.create_login_form(
            on_login=self.handle_login,
            on_forget_password=self.handle_forget_password
        )
    
    def handle_login(self):
        """处理登录（与原文件 webui-bak.py L5132-5184 一致）"""
        if not self.login_form.validate_input():
            return

        username = self.login_form.get_username()
        password = self.login_form.get_password()

        try:
            import requests
            from urllib.parse import urljoin

            config_data = self.config._config
            ums_api = config_data.get("login", {}).get("ums_api", "")

            if not ums_api:
                # 没有配置UMS API，直接登录成功
                self.user_info = {"username": username}
                ui.notify(position="top", type="positive", message="登录成功")
                logger.info(f"用户 {username} 登录成功")
                self.goto_func_page()
                return

            API_URL = urljoin(ums_api, '/auth/login')

            resp_json = requests.post(API_URL, json={
                "username": username,
                "password": password
            }).json()

            if resp_json is None:
                ui.notify(position="top", type="negative", message="登录失败")
                return

            if "data" not in resp_json or "success" not in resp_json:
                ui.notify(position="top", type="negative", message="用户名或密码不正确")
                return

            if not resp_json["success"]:
                ui.notify(position="top", type="warning",
                          message=f'账号过期时间：{resp_json["data"].get("expiration_ts", "未知")}，已到期，请联系管理员续费')
                return

            self.user_info = resp_json["data"]
            expiration_ts = resp_json["data"].get("expiration_ts", "")

            ui.notify(position="top", type="info",
                      message=f'登录成功，账号到期时间：{expiration_ts}')
            logger.info(f"用户 {username} 登录成功")

            # 启动过期检查定时器
            ui.timer(600.0, lambda: self._check_expiration())

            # 进入功能页
            self.goto_func_page()

        except Exception as e:
            logger.error(f"登录失败: {e}")
            ui.notify(position="top", type="negative", message=f"登录失败: {e}")
    
    def handle_forget_password(self):
        """处理忘记密码"""
        ui.notify(position="top", type="info", message="请联系管理员重置密码")
    
    def create_main_ui(self):
        """创建主界面 - 使用新的 drawer 布局"""
        # 使用新的 DrawerLayout，传入保存回调函数与 app 引用（E1：启停按钮经此调用
        # AIVtuberApp 函数链；app 为 Optional，None 时按钮降级为禁用态，兼容测试构造）
        self.drawer_layout = DrawerLayout(
            self.config._config,
            save_callback=self.config.save,
            app=self
        )

        # 创建左侧导航栏
        self.drawer_layout.create_drawer()

        # 创建导航栏功能分组
        self.drawer_layout.create_navigation_groups()

        # 创建内容区
        self.drawer_layout.create_content_area()

        # 默认显示第一个分组的功能列表
        function_groups = self.config._config.get("webui", {}).get("function_groups", [])
        if function_groups:
            first_group = function_groups[0]["name"]
            self.drawer_layout.select_group(first_group)
            self.drawer_layout.show_function_list(first_group)

        # 是否启用自动运行功能（E7：先于控制按钮构建执行——按钮初始态直接反映
        # running_flag，避免 auto_run 场景下按钮先构建出错误初始态、再等 timer 纠正）
        if get_nested_value(self.config._config, "webui", "auto_run"):
            logger.info("自动运行 已启用")
            self.run_external_program(type="api")

        # 创建底部控制按钮
        self._create_control_buttons()

        # 创建回到顶部按钮
        self._create_scroll_top_button()

    def create_main_ui_old(self):
        """创建主界面 - 旧版本（使用 tabs 布局）"""
        # 主题配置 - 使用简化的浅色/深色模式
        current_theme = self.theme_manager.get_theme_config()
        tab_panel_css = current_theme.get("tab_panel", "")
        card_css = current_theme.get("card", "")
        button_bottom_css = current_theme.get("button_bottom", "")
        button_bottom_color = current_theme.get("button_bottom_color", "primary")
        button_internal_css = current_theme.get("button_internal", "")
        button_internal_color = current_theme.get("button_internal_color", "")
        switch_internal_css = current_theme.get("switch_internal", "")
        echart_css = current_theme.get("echart", "")

        # 创建标签页导航
        tabs = self.navigation_tabs.create_tabs()

        # 创建标签页面板
        with ui.tab_panels(tabs, value=self.navigation_tabs.tabs['common_config']).classes('w-full'):
            # 通用配置标签页
            with ui.tab_panel(self.navigation_tabs.tabs['common_config']).style(self.navigation_tabs.tab_panel_css):
                common_config.create_common_config_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 大语言模型标签页
            with ui.tab_panel(self.navigation_tabs.tabs['llm']).style(self.navigation_tabs.tab_panel_css):
                llm.create_llm_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 文本转语音标签页
            with ui.tab_panel(self.navigation_tabs.tabs['tts']).style(self.navigation_tabs.tab_panel_css):
                tts.create_tts_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 变声标签页
            with ui.tab_panel(self.navigation_tabs.tabs['svc']).style(self.navigation_tabs.tab_panel_css):
                svc.create_svc_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 虚拟身体标签页
            with ui.tab_panel(self.navigation_tabs.tabs['visual_body']).style(self.navigation_tabs.tab_panel_css):
                visual_body.create_visual_body_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 文案标签页
            with ui.tab_panel(self.navigation_tabs.tabs['copywriting']).style(self.navigation_tabs.tab_panel_css):
                copywriting.create_copywriting_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 聊天标签页
            with ui.tab_panel(self.navigation_tabs.tabs['talk']).style(self.navigation_tabs.tab_panel_css):
                talk.create_talk_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 图像识别标签页
            with ui.tab_panel(self.navigation_tabs.tabs['image_recognition']).style(self.navigation_tabs.tab_panel_css):
                image_recognition.create_image_recognition_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 积分标签页
            with ui.tab_panel(self.navigation_tabs.tabs['integral']).style(self.navigation_tabs.tab_panel_css):
                integral.create_integral_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 助播标签页
            with ui.tab_panel(self.navigation_tabs.tabs['assistant_anchor']).style(self.navigation_tabs.tab_panel_css):
                assistant_anchor.create_assistant_anchor_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 翻译标签页
            with ui.tab_panel(self.navigation_tabs.tabs['translate']).style(self.navigation_tabs.tab_panel_css):
                translate.create_translate_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 串口标签页
            with ui.tab_panel(self.navigation_tabs.tabs['serial']).style(self.navigation_tabs.tab_panel_css):
                serial.create_serial_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

            # 数据分析标签页
            with ui.tab_panel(self.navigation_tabs.tabs['data_analysis']).style(self.navigation_tabs.tab_panel_css):
                data_analysis.create_data_analysis_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config,
                    data_analysis=self.data_analysis
                )

            # 页面配置标签页
            with ui.tab_panel(self.navigation_tabs.tabs['web']).style(self.navigation_tabs.tab_panel_css):
                web.create_web_tab(
                    self.config._config,
                    self.theme_manager.get_theme_config(),
                    self.set_config
                )

        # 创建底部控制按钮
        self._create_control_buttons()

        # 创建回到顶部按钮
        self._create_scroll_top_button()

        # 是否启用自动运行功能
        if get_nested_value(self.config._config, "webui", "auto_run"):
            logger.info("自动运行 已启用")
            self.run_external_program(type="api")
    
    def _create_control_buttons(self):
        """创建底部控制按钮 - 已移除重启和关灯按钮"""
        # 根据 CR-002 要求，重启和关灯按钮已移除
        # 一键运行/停止运行功能已合并到运行状态分组中
        # 保存配置按钮已移到三级参数设置页面
        pass
    
    def _create_scroll_top_button(self):
        """创建回到顶部按钮"""
        with ui.row().style("position:fixed; bottom: 20px; right: 20px;"):
            self.control_buttons.create_scroll_top_button(on_click=self.scroll_to_top)
    
    def _register_api_endpoints(self):
        """注册API端点（与原文件 webui-bak.py L602-925 一致）"""
        from starlette.requests import Request

        @app.post('/set_config')
        async def api_set_config(request: Request):
            """远程设置配置"""
            try:
                data_json = await request.json()
                logger.info(f'set_config接口 收到数据：{data_json}')

                config_path = data_json.get("config_path", "")
                config_data = data_json.get("data", {})

                if not config_path:
                    return {"code": -1, "message": "config_path不能为空"}

                # 读取现有配置
                import json
                try:
                    with open(config_path, 'r', encoding="utf-8") as config_file:
                        existing_data = json.load(config_file)
                except Exception as e:
                    logger.error(f"无法读取配置文件！\n{e}")
                    return {"code": -1, "message": f"无法读取配置文件！{e}"}

                # 合并字典
                existing_data.update(config_data)

                # 写入配置到配置文件
                try:
                    with open(config_path, 'w', encoding="utf-8") as config_file:
                        json.dump(existing_data, config_file, indent=2, ensure_ascii=False)
                        config_file.flush()

                    logger.info("配置数据已成功写入文件！")
                    return {"code": 200, "message": "配置数据已成功写入文件！"}
                except Exception as e:
                    logger.error(f"无法写入配置文件！\n{str(e)}")
                    return {"code": -1, "message": f"无法写入配置文件！{e}"}
            except Exception as e:
                logger.error(f"set_config处理失败: {e}")
                return {"code": -1, "message": f"set_config执行失败！{e}"}

        @app.post('/sys_cmd')
        async def api_sys_cmd(request: Request):
            """系统命令（run/stop/restart/factory）"""
            try:
                data_json = await request.json()
                logger.info(f'sys_cmd接口 收到数据：{data_json}')
                cmd_type = data_json.get('type', '')
                logger.info(f"开始执行 {cmd_type}命令...")

                if cmd_type == 'run':
                    resp_json = self.run_external_program(type="api")
                elif cmd_type == 'stop':
                    resp_json = self.stop_external_program(type="api")
                elif cmd_type == 'restart':
                    # 语义注记（E3 批次 /sys_cmd 语义标注）：此处 restart = 重启 webui
                    # 控制台自身（os.execl，见 restart_application），与 WebUI 导航栏
                    # 「停止&重启」按钮的系统级重启（stop+run，控制台不重启）不同。
                    # 语义统一/废弃待后续裁决（TODOS「/sys_cmd 重启语义统一」条目）。
                    api_type = data_json.get('api_type', 'api')
                    resp_json = self.restart_application(type=api_type)
                elif cmd_type == 'factory':
                    src_path = data_json.get('data', {}).get('src_path', 'config.json.bak')
                    dst_path = data_json.get('data', {}).get('dst_path', 'config.json')
                    resp_json = self.factory(src_path, dst_path, type="api")
                else:
                    resp_json = {"code": -1, "message": f"未知命令类型: {cmd_type}"}

                return resp_json
            except Exception as e:
                logger.error(f"sys_cmd处理失败: {e}")
                return {"code": -1, "message": f"sys_cmd执行失败！{e}"}

        @app.post('/send')
        async def api_send(request: Request):
            """发送数据到主程序"""
            try:
                data_json = await request.json()
                logger.info(f'WEBUI API send接口收到数据：{data_json}')

                import json
                import urllib.request
                config_data = self.config._config
                main_api_ip = "127.0.0.1" if config_data.get("api_ip") == "0.0.0.0" else config_data.get("api_ip", "127.0.0.1")
                main_api_port = config_data.get("api_port", 8000)
                url = f'http://{main_api_ip}:{main_api_port}/send'

                req = urllib.request.Request(
                    url,
                    data=json.dumps(data_json).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=60) as response:
                    resp_data = json.loads(response.read().decode('utf-8'))
                    return resp_data
            except Exception as e:
                logger.error(f"send处理失败: {e}")
                return {"code": -1, "message": f"发送数据失败！{e}"}

        @app.post('/callback')
        async def api_callback(request: Request):
            """数据回调（聊天记录）"""
            try:
                data_json = await request.json()
                logger.info(f'WEBUI API callback接口收到数据：{data_json}')
                # TODO: 实现聊天记录显示
                return {"code": 200, "message": "成功"}
            except Exception as e:
                logger.error(f"callback处理失败: {e}")
                return {"code": -1, "message": f"失败！{e}"}

        @app.post('/tts')
        async def api_tts(request: Request):
            """TTS合成接口"""
            try:
                data_json = await request.json()
                logger.info(f'WEBUI API tts接口收到数据：{data_json}')
                # TODO: 实现TTS合成
                return {"code": 200, "message": "成功", "data": {}}
            except Exception as e:
                logger.error(f"tts处理失败: {e}")
                return {"code": -1, "message": f"失败！{e}"}

        @app.post('/llm')
        async def api_llm(request: Request):
            """LLM推理接口"""
            try:
                data_json = await request.json()
                logger.info(f'WEBUI API llm接口 收到数据：{data_json}')

                import json
                import urllib.request
                config_data = self.config._config
                main_api_ip = "127.0.0.1" if config_data.get("api_ip") == "0.0.0.0" else config_data.get("api_ip", "127.0.0.1")
                main_api_port = config_data.get("api_port", 8000)
                url = f'http://{main_api_ip}:{main_api_port}/llm'

                req = urllib.request.Request(
                    url,
                    data=json.dumps(data_json).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=60) as response:
                    resp_data = json.loads(response.read().decode('utf-8'))
                    return resp_data
            except Exception as e:
                logger.error(f"llm处理失败: {e}")
                return {"code": -1, "message": f"失败！{e}"}

        @app.get('/get_sys_info')
        async def api_get_sys_info():
            """获取系统信息"""
            try:
                import json
                import urllib.request
                config_data = self.config._config
                main_api_ip = "127.0.0.1" if config_data.get("api_ip") == "0.0.0.0" else config_data.get("api_ip", "127.0.0.1")
                main_api_port = config_data.get("api_port", 8000)
                url = f'http://{main_api_ip}:{main_api_port}/get_sys_info'

                req = urllib.request.Request(url, method='GET')
                with urllib.request.urlopen(req, timeout=60) as response:
                    resp_data = json.loads(response.read().decode('utf-8'))
                    return resp_data
            except Exception as e:
                logger.error(f"get_sys_info处理失败: {e}")
                return {"code": -1, "message": f"get_sys_info处理失败！{e}"}

    def _check_expiration(self):
        """检查账号过期（与原文件 webui-bak.py L284-333 一致）"""
        try:
            import requests
            from urllib.parse import urljoin

            config_data = self.config._config
            ums_api = config_data.get("login", {}).get("ums_api", "")

            if not ums_api:
                return True

            API_URL = urljoin(ums_api, '/auth/check_expiration')

            if self.user_info is None:
                ui.notify(position="top", type="negative", message=f"账号登录信息失效，请重新登录")
                self.stop_programs()
                return False

            if "accessToken" not in self.user_info:
                ui.notify(position="top", type="negative", message=f"账号登录信息失效，请重新登录")
                self.stop_programs()
                return False

            headers = {
                "Authorization": "Bearer " + self.user_info["accessToken"]
            }

            response = requests.post(API_URL, headers=headers)

            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get("code") == 0 and resp_json.get("success"):
                    logger.info(f'账号可用，过期时间：{resp_json["data"]["expiration_ts"]}')
                    return True
                else:
                    ui.notify(position="top", type="negative",
                              message=f'账号已过期，请联系管理员续费')
                    self.stop_programs()
                    return False
            else:
                logger.error(f"自检异常！")
                return False
        except Exception as e:
            logger.error(f"检查过期失败: {e}")
            return False

    def run(self):
        """运行应用程序"""
        # 注册API端点
        self._register_api_endpoints()

        # 获取WebUI配置
        webui_ip = get_nested_value(self.config._config, "webui", "ip", default="0.0.0.0")
        webui_port = get_nested_value(self.config._config, "webui", "port", default=8086)
        webui_title = get_nested_value(self.config._config, "webui", "title", default="AI Vtuber")

        # 设置页面标题
        ui.page_title(webui_title)

        # 检查登录状态
        self.check_login()

        # 发送心跳包（与原文件 webui-bak.py L5127 一致）
        # 注意：心跳包功能需要common模块支持，暂时用日志替代
        ui.timer(9 * 60, lambda: logger.debug("发送心跳包"))

        # 启动应用
        ui.run(
            host=webui_ip,
            port=webui_port,
            title=webui_title,
            favicon="./ui/favicon-64.ico",
            language="zh-CN",
            dark=False,
            reload=False
        )


def main():
    """主函数"""
    try:
        app = AIVtuberApp()
        app.run()
    except Exception as e:
        logger.error(f"应用程序启动失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()