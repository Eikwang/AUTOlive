from nicegui import ui, app
import sys, os, json, subprocess, importlib, re, threading, signal
import traceback
import time
import asyncio
from urllib.parse import urljoin
from pathlib import Path

# from functools import partial

from utils.my_log import logger
from utils.config import Config
from utils.common import Common
from utils.audio import Audio

"""

@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@@@.:;;;++;;;;:,@@@@@@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@@:;+++++;;++++;;;.@@@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@:++++;;;;;;;;;;+++;,@@@@@@@@@@@@@@@@@
@@@@@@@@@@@.;+++;;;;;;;;;;;;;;++;:@@@@@@@@@@@@@@@@
@@@@@@@@@@;+++;;;;;;;;;;;;;;;;;;++;:@@@@@@@@@@@@@@
@@@@@@@@@:+++;;;;;;;;;;;;;;;;;;;;++;.@@@@@@@@@@@@@
@@@@@@@@;;+;;;;;;;;;;;;;;;;;;;;;;;++:@@@@@@@@@@@@@
@@@@@@@@;+;;;;:::;;;;;;;;;;;;;;;;:;+;,@@@@@@@@@@@@
@@@@@@@:+;;:;;:::;:;;:;;;;::;;:;:::;+;.@@@@@@@@@@@
@@@@@@.;+;::;:,:;:;;+:++:;:::+;:::::++:+@@@@@@@@@@
@@@@@@:+;;:;;:::;;;+%;*?;;:,:;*;;;;:;+;:@@@@@@@@@@
@@@@@@;;;+;;+;:;;;+??;*?++;,:;+++;;;:++:@@@@@@@@@@
@@@@@.++*+;;+;;;;+?;?**??+;:;;+.:+;;;;+;;@@@@@@@@@
@@@@@,+;;;;*++*;+?+;**;:?*;;;;*:,+;;;;+;,@@@@@@@@@
@@@@@,:,+;+?+?++?+;,?#%*??+;;;*;;:+;;;;+:@@@@@@@@@
@@@@@@@:+;*?+?#%;;,,?###@#+;;;*;;,+;;;;+:@@@@@@@@@
@@@@@@@;+;??+%#%;,,,;SSS#S*+++*;..:+;?;+;@@@@@@@@@
@@@@@@@:+**?*?SS,,,,,S#S#+***?*;..;?;**+;@@@@@@@@@
@@@@@@@:+*??*??S,,,,,*%SS+???%++;***;+;;;.@@@@@@@@
@@@@@@@:*?*;*+;%:,,,,;?S?+%%S?%+,:?;+:,,,@@@@@@@@
@@@@@@@,*?,;+;+S:,,,,%?+;S%S%++:+??+:,,,:@@@@@@@@
@@@@@@@,:,@;::;+,,,,,+?%*+S%#?*???*;,,,,,.@@@@@@@@
@@@@@@@@:;,::;;:,,,,,,,,,?SS#??*?+,.,,,:,@@@@@@@@@
@@@@@@;;+;;+:,:%?%*;,,,,SS#%*??%,.,,,,,:@@@@@@@@@
@@@@@.+++,++:;???%S?%;.+#####??;.,,,,,,:@@@@@@@@@
@@@@@:++::??+S#??%#??S%?#@#S*+?*,,,,,,:,@@@@@@@@@@
@@@@@:;;:*?;+%#%?S#??%SS%+#%..;+:,,,,,,@@@@@@@@@@@
@@@@@@,,*S*;?SS?%##%?S#?,.:#+,,+:,,,,,,@@@@@@@@@@@
@@@@@@@;%?%#%?*S##??##?,..*#,,+:,,;*;.@@@@@@@@@@@
@@@@@@.*%??#S*?S#@###%;:*,.:#:,+;:;*+:@@@@@@@@@@@@
@@@@@@,%S??SS%##@@#%S+..;;.,#*;???*?+++:@@@@@@@@@@
@@@@@@:S%??%####@@S,,*,.;*;+#*;+?%??#S%+.@@@@@@@@@
@@@@@@:%???%@###@@?,,:**S##S*;.,%S?;+*?+.,..@@@@@@
@@@@@@;%??%#@###@@#:.;@@#@%%,.,%S*;++*++++;.@@@@@
@@@@@@,%S?S@@###@@@%+#@@#@?;,.:?;??++?%?***+.@@@@@
@@@@@@.*S?S####@@####@@##@?..:*,+:??**%+;;;;..@@@@
@@@@@@:+%?%####@@####@@#@%;:.;;:,+;?**;++;,:;:,@@@
@@@@@@;;*%?%@##@@@###@#S#*:;*+,;.+***?******+:.@@@
@@@@@@:;:??%@###%##@#%++;+*:+;,:;+%?*;+++++;:.@@@@
@@@@@@.+;:?%@@#%;+S*;;,:::**+,;:%??*+.@....@@@@@@@
@@@@@@@;*::?#S#S+;,..,:,;:?+?++*%?+::@@@@@@@@@@@@@
@@@@@@@.+*+++?%S++...,;:***??+;++:.@@@@@@@@@@@@@@@
@@@@@@@@:::..,;+*+;;+*?**+;;;+;:.@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@@@,+*++;;:,..@@@@@@@@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@@@@::,.@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@

"""


"""
全局变量
"""
user_info = None

# 创建一个全局变量，用于表示程序是否正在运行
running_flag = False

# 定义一个标志变量，用来追踪定时器的运行状态
loop_screenshot_timer_running = False
loop_screenshot_timer = None

common = None
config = None
audio = None
my_handle = None
config_path = None

# 存储运行的子进程
my_subprocesses = {}

# 本地启动的web服务，用来加载本地的live2d
web_server_port = 12345

# 聊天记录计数
scroll_area_chat_box_chat_message_num = 0
# 聊天记录最多保留100条
scroll_area_chat_box_chat_message_max_num = 100


"""
初始化基本配置
"""
def init():
    """
    初始化基本配置
    """
    global config_path, config, common, audio

    common = Common()

    if getattr(sys, 'frozen', False):
        # 当前是打包后的可执行文件
        bundle_dir = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
        file_relative_path = bundle_dir.resolve()
    else:
        # 当前是源代码
        file_relative_path = Path(__file__).parent.resolve()

    # logger.info(file_relative_path)

    # 初始化文件夹
    def init_dir():
        # 创建日志文件夹
        log_dir = file_relative_path / 'log'
        # mkdir 方法的 parents=True 参数可以确保父目录的创建（如有必要），exist_ok=True 则避免在目录已存在时抛出异常。
        log_dir.mkdir(parents=True, exist_ok=True)

        # 创建音频输出文件夹
        audio_out_dir = file_relative_path / 'out'
        audio_out_dir.mkdir(parents=True, exist_ok=True)

    init_dir()
    logger.debug("项目相关文件夹初始化完成")

    # 配置文件路径
    config_path = file_relative_path / 'config.json'
    config_path = str(config_path)

    logger.debug("配置文件路径=" + str(config_path))

    # 实例化音频类
    audio = Audio(config_path, type=2)
    # 实例化配置类
    config = Config(config_path)

# 初始化基本配置
init()

# 将本地目录中的静态文件（如 CSS、JavaScript、图片等）暴露给 web 服务器，以便用户可以通过特定的 URL 访问这些文件。
if config.get("webui", "local_dir_to_endpoint", "enable"):
    for tmp in config.get("webui", "local_dir_to_endpoint", "config"):
        app.add_static_files(tmp['url_path'], tmp['local_dir'])

# 暗夜模式
dark = ui.dark_mode()

"""
通用函数
"""
def textarea_data_change(data):
    """
    字符串数组数据格式转换
    """
    tmp_str = ""
    if data is not None:
        for tmp in data:
            tmp_str = tmp_str + tmp + "\n"
        
    return tmp_str



"""
                                                                                                    
                                               .@@@@@                           @@@@@.              
                                               .@@@@@                           @@@@@.              
        ]]]]]   .]]]]`   .]]]]`   ,]@@@@@\`    .@@@@@,/@@@\`   .]]]]]   ]]]]]`  ]]]]].              
        =@@@@^  =@@@@@`  =@@@@. =@@@@@@@@@@@\  .@@@@@@@@@@@@@  *@@@@@   @@@@@^  @@@@@.              
         =@@@@ ,@@@@@@@ .@@@@` =@@@@^   =@@@@^ .@@@@@`  =@@@@^ *@@@@@   @@@@@^  @@@@@.              
          @@@@^@@@@\@@@^=@@@^  @@@@@@@@@@@@@@@ .@@@@@   =@@@@@ *@@@@@   @@@@@^  @@@@@.              
          ,@@@@@@@^ \@@@@@@@   =@@@@^          .@@@@@.  =@@@@^ *@@@@@  .@@@@@^  @@@@@.              
           =@@@@@@  .@@@@@@.    \@@@@@]/@@@@@` .@@@@@@]/@@@@@. .@@@@@@@@@@@@@^  @@@@@.              
            \@@@@`   =@@@@^      ,\@@@@@@@@[   .@@@@^\@@@@@[    .\@@@@@[=@@@@^  @@@@@.    
            
"""
# 配置
webui_ip = config.get("webui", "ip")
webui_port = config.get("webui", "port")
webui_title = config.get("webui", "title")

# CSS
theme_choose = config.get("webui", "theme", "choose")
tab_panel_css = config.get("webui", "theme", "list", theme_choose, "tab_panel")
card_css = config.get("webui", "theme", "list", theme_choose, "card")
button_bottom_css = config.get("webui", "theme", "list", theme_choose, "button_bottom")
button_bottom_color = config.get("webui", "theme", "list", theme_choose, "button_bottom_color")
button_internal_css = config.get("webui", "theme", "list", theme_choose, "button_internal")
button_internal_color = config.get("webui", "theme", "list", theme_choose, "button_internal_color")
switch_internal_css = config.get("webui", "theme", "list", theme_choose, "switch_internal")
echart_css = config.get("webui", "theme", "list", theme_choose, "echart")

def goto_func_page():
    """
    跳转到功能页
    """
    global audio, my_subprocesses, config

    # 过期时间
    expiration_ts = None

    def start_programs():
        """根据配置启动所有程序。
        """
        global config

        for program in config.get("coordination_program"):
            if not program["enable"]:
                continue

            name = program["name"]
            executable = program["executable"]  # Python 解释器的路径
            app_path = program["parameters"][0]  # 假设第一个参数总是 app.py 的路径
            
            # 从 app.py 的路径中提取目录
            app_dir = os.path.dirname(app_path)
            
            # 使用 Python 解释器路径和 app.py 路径构建命令
            cmd = [executable, app_path]

            logger.info(f"运行程序: {name} 位于: {app_dir}")
            
            # 在 app.py 文件所在的目录中启动程序
            process = subprocess.Popen(cmd, cwd=app_dir, shell=True)
            my_subprocesses[name] = process

        name = "main"
        # 根据操作系统的不同，微调参数
        if common.detect_os() in ['Linux', 'MacOS']:
            process = subprocess.Popen(["python", f"main.py"], shell=False)
        else:
            process = subprocess.Popen(["python", f"main.py"], shell=True)
        my_subprocesses[name] = process

        logger.info(f"运行程序: {name}")


    def stop_program(name):
        """停止一个正在运行的程序及其所有子进程，兼容 Windows、Linux 和 macOS。

        Args:
            name (str): 要停止的程序的名称。
        """
        if name in my_subprocesses:
            pid = my_subprocesses[name].pid  # 获取进程ID
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

            del my_subprocesses[name]  # 从进程字典中移除
        else:
            logger.warning(f"程序 {name} 没有在运行.")

    def stop_programs():
        """根据配置停止所有程序。
        """
        global config

        for program in config.get("coordination_program"):
            if not program["enable"]:
                continue
            
            stop_program(program["name"])

        stop_program("main")

    def check_expiration():
        try:
            import requests

            API_URL = urljoin(config.get("login", "ums_api"), '/auth/check_expiration')

            if user_info is None:
                ui.notify(position="top", type="negative", message=f"账号登录信息失效，请重新登录")
                stop_programs()
                return False

            if "accessToken" not in user_info:
                ui.notify(position="top", type="negative", message=f"账号登录信息失效，请重新登录")
                stop_programs()
                return False

            headers = {
                "Authorization": "Bearer " + user_info["accessToken"]
            }

            # 发送 POST 请求
            response = requests.post(API_URL, headers=headers)

            # 判断状态码
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json["code"] == 0 and resp_json["success"]:
                    remainder = common.time_difference_in_seconds(resp_json["data"]["expiration_ts"])
                    logger.info(f'账号可用，过期时间：{resp_json["data"]["expiration_ts"]}')
                    return True
                else:
                    remainder = common.time_difference_in_seconds(resp_json["data"]["expiration_ts"])
                    ui.notify(position="top", type="negative", message=f'账号过期时间：{resp_json["data"]["expiration_ts"]}，已过期：{remainder}秒，请联系管理员续费')
                    logger.error(f'账号过期时间：{resp_json["data"]["expiration_ts"]}，已过期：{remainder}秒，请联系管理员续费')
                    stop_programs()
                    return False
            # elif response.status_code == 401:
            #     ui.notify(position="top", type="negative", message=f"账号已到期，请联系管理员续费")
            #     logger.error(f"账号已到期，请联系管理员续费")
            #     stop_programs()

            #     return False
            else:
                logger.error(f"自检异常！")
                return False
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"错误：{e}")
            logger.error(traceback.format_exc())

            return False

    if config.get("login", "enable"):
        # 十分钟一次的检测
        ui.timer(600.0, lambda: check_expiration())


    """

      =@@^      ,@@@^        .@@@. .....   =@@.      ]@\  ,]]]]]]]]]]]]]]].  .]]]]]]]]]]]]]]]]]]]]    ,]]]]]]]]]]]]]]]]]`    ,/. @@@^ /]  ,@@@.               
      =@@^ .@@@@@@@@@@@@@@^  /@@\]]@@@@@=@@@@@@@@@.  \@@@`=@@@@@@@@@@@@@@@.  .@@@@@@@@@@@@@@@@@@@@    =@@@@@@@@@@@@@@@@@^   .\@@^@@@\@@@`.@@@^                
    @@@@@@@^@@@@@@@@@@@@@@^ =@@@@@^ =@@\]]]/@@]]@@].  =@/`=@@^  .@@@  .@@@.  .@@@^    @@@^    =@@@             ,/@@@@/`     =@@@@@@@@@@@^=@@@@@@@@@.          
    @@@@@@@^@@@^@@\`   =@@^.@@@]]]`=@@^=@@@@@@@@@@@.]]]]` =@@^=@@@@@@@^@@@.  .@@@\]]]]@@@\]]]]/@@@   @@@\/@\..@@@@[./@/@@@. ,[[\@@@@/[[[\@@@`..@@@`           
      =@@^ ,]]]/@@@]]]]]]]].\@@@@@^@@@OO=@@@@@@@@@..@@@@^ =@@^]]]@@@]]`@@@.  .@@@@@@@@@@@@@@@@@@@@   @@@^=@@@^@@@^/@@@\@@@..]@@@@@@@@@@]@@@@^ .@@@.           
      =@@@@=@@@@@@@@@@@@@@@. =@@^ .OO@@@.[[\@@[[[[.  =@@^ =@@^@@@@@@@@^@@@.  .@@@^    @@@^    =@@@   @@@^ .`,]@@@^`,` =@@@. \@/.]@@@^,@@@@@@\ =@@^            
   .@@@@@@@. .@@@`   /@@/  .@@@@@@@,.=@@=@@@@@@@@@^  =@@^,=@@^=@@@@@@@.@@@.  .@@@\]]]]@@@\]]]]/@@@   @@@^]@@@@@@@@@@@]=@@@. ]]]@@@\]]]]] .=@@\@@@.            
    @@\@@^  .@@@\.  /@@@.    =@@^ =@\@@^.../@@.....  =@@@@=@@^=@@[[\@@.@@@.  .@@@@@@@@@@@@@@@@@@@@   @@@@@@/..@@@^,@@@@@@@. O@@@@@@@@@@@  .@@@@@^             
      =@@^   ,\@@@@@@@@.     =@@^/^\@@@`@@@@@@@@@@^  /@@@/@@@`=@@OO@@@.@@@.  =@@@`    @@@^    =@@@   @@@^  \@@@@@^   .=@@@. .@@@@\`/@@/    /@@@\.             
      =@@^    ,/@@@@@@@@]    =@@@@^/@@@@]` =@@.     .\@/.=@@@ =@@[[[[[.@@@.  /@@@     @@@^   ./@@@   @@@^.............=@@@.    O@@@@@@\`,/@@@@@@@@`           
    @@@@@^.@@@@@@@/..[@@@@/. ,@@`/@@@`[@@@@@@@@@@@@.    /@@@^      =@@@@@@. /@@@^     @@@^,@@@@@@^   @@@@@@@@@@@@@@@@@@@@@..\@@@@@[,\@@\@@@@` ,@@@^           
    ,[[[.  .O[[.        [`        ,/         ......       ,^       .[[[[`     ,`      .... [[[[`                      ,[[[. .[.         ,/.     .`

    """
    # 创建一个函数，用于运行外部程序
    def run_external_program(config_path="config.json", type="webui"):
        global running_flag

        if running_flag:
            if type == "webui":
                ui.notify(position="top", type="warning", message="运行中，请勿重复运行")
            return

        try:
            running_flag = True

            # 启动协同程序和主程序
            start_programs()

            if type == "webui":
                ui.notify(position="top", type="positive", message="程序开始运行")
            logger.info("程序开始运行")

            return {"code": 200, "msg": "程序开始运行"}
        except Exception as e:
            if type == "webui":
                ui.notify(position="top", type="negative", message=f"错误：{e}")
            logger.error(traceback.format_exc())
            running_flag = False

            return {"code": -1, "msg": f"运行失败！{e}"}


    # 定义一个函数，用于停止正在运行的程序
    def stop_external_program(type="webui"):
        global running_flag

        if running_flag:
            try:
                # 停止协同程序
                stop_programs()

                running_flag = False
                if type == "webui":
                    ui.notify(position="top", type="positive", message="程序已停止")
                logger.info("程序已停止")
            except Exception as e:
                if type == "webui":
                    ui.notify(position="top", type="negative", message=f"停止错误：{e}")
                logger.error(f"停止错误：{e}")

                return {"code": -1, "msg": f"重启失败！{e}"}


    # 开关灯
    def change_light_status(type="webui"):
        if dark.value:
            button_light.set_text("关灯")
        else:
            button_light.set_text("开灯")
        dark.toggle()

    # 重启
    def restart_application(type="webui"):
        try:
            # 先停止运行
            stop_external_program(type)

            logger.info(f"重启webui")
            if type == "webui":
                ui.notify(position="top", type="ongoing", message=f"重启中...")
            python = sys.executable
            os.execl(python, python, *sys.argv)  # Start a new instance of the application
        except Exception as e:
            logger.error(traceback.format_exc())
            return {"code": -1, "msg": f"重启失败！{e}"}
        
    # 恢复出厂配置
    def factory(src_path='config.json.bak', dst_path='config.json', type="webui"):
        # src_path = 'config.json.bak'
        # dst_path = 'config.json'

        try:
            with open(src_path, 'r', encoding="utf-8") as source:
                with open(dst_path, 'w', encoding="utf-8") as destination:
                    destination.write(source.read())
            logger.info("恢复出厂配置成功！")
            if type == "webui":
                ui.notify(position="top", type="positive", message=f"恢复出厂配置成功！")
            
            # 重启
            restart_application()

            return {"code": 200, "msg": "恢复出厂配置成功！"}
        except Exception as e:
            logger.error(f"恢复出厂配置失败！\n{e}")
            if type == "webui":
                ui.notify(position="top", type="negative", message=f"恢复出厂配置失败！\n{e}")
            
            return {"code": -1, "msg": f"恢复出厂配置失败！\n{e}"}
    
    
        
    # GPT-SoVITS加载模型
    async def gpt_sovits_set_model():
        try:
            if select_gpt_sovits_type.value == "v2_api_0821":
                async def set_gpt_weights():
                    try:

                        API_URL = urljoin(input_gpt_sovits_api_ip_port.value, '/set_gpt_weights?weights_path=' + input_gpt_sovits_gpt_model_path.value)
                        
                        # logger.debug(API_URL)

                        resp_json = await common.send_async_request(API_URL, "GET", None, resp_data_type="json")

                        if resp_json is None:
                            content = f"gpt_weights：{input_gpt_sovits_gpt_model_path.value} 加载失败，请查看双方日志排查问题"
                            logger.error(content)
                            return False
                        else:
                            if resp_json["message"] == "success":
                                content = f"gpt_weights：{input_gpt_sovits_gpt_model_path.value} 加载成功"
                                logger.info(content)
                            else:
                                content = f"gpt_weights：{input_gpt_sovits_gpt_model_path.value} 加载失败，请查看双方日志排查问题"
                                logger.error(content)
                                return False
                        
                        return True
                    except Exception as e:
                        logger.error(traceback.format_exc())
                        logger.error(f'gpt_sovits未知错误: {e}')
                        return False

                async def set_sovits_weights():
                    try:

                        API_URL = urljoin(input_gpt_sovits_api_ip_port.value, '/set_sovits_weights?weights_path=' + input_gpt_sovits_sovits_model_path.value)
                        
                        resp_json = await common.send_async_request(API_URL, "GET", None, resp_data_type="json")

                        if resp_json is None:
                            content = f"sovits_weights：{input_gpt_sovits_sovits_model_path.value} 加载失败，请查看双方日志排查问题"
                            logger.error(content)
                            return False
                        else:
                            if resp_json["message"] == "success":
                                content = f"sovits_weights：{input_gpt_sovits_sovits_model_path.value} 加载成功"
                                logger.info(content)
                            else:
                                content = f"sovits_weights：{input_gpt_sovits_sovits_model_path.value} 加载失败，请查看双方日志排查问题"
                                logger.error(content)
                                return False
                        
                        return True
                    except Exception as e:
                        logger.error(traceback.format_exc())
                        logger.error(f'sovits_weights未知错误: {e}')
                        return False
            
                if await set_gpt_weights() and await set_sovits_weights():
                    content = "gpt_sovits模型加载成功"
                    logger.info(content)
                    ui.notify(position="top", type="positive", message=content)
                else:
                    content = "gpt_sovits模型加载失败，请查看双方日志排查问题"
                    logger.error(content)
                    ui.notify(position="top", type="negative", message=content)
            else:
                API_URL = urljoin(input_gpt_sovits_api_ip_port.value, '/set_model')

                data_json = {
                    "gpt_model_path": input_gpt_sovits_gpt_model_path.value,
                    "sovits_model_path": input_gpt_sovits_sovits_model_path.value
                }
                
                resp_data = await common.send_async_request(API_URL, "POST", data_json, resp_data_type="content")

                if resp_data is None:
                    content = "gpt_sovits加载模型失败，请查看双方日志排查问题"
                    logger.error(content)
                    ui.notify(position="top", type="negative", message=content)
                else:
                    content = "gpt_sovits加载模型成功"
                    logger.info(content)
                    ui.notify(position="top", type="positive", message=content)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f'gpt_sovits未知错误: {e}')
            ui.notify(position="top", type="negative", message=f'gpt_sovits未知错误: {e}')

    # 页面滑到顶部
    def scroll_to_top():
        # 这段JavaScript代码将页面滚动到顶部
        ui.run_javascript("window.scrollTo(0, 0);")   

    # 显示聊天数据的滚动框
    scroll_area_chat_box = None

    # 处理数据 显示聊天记录
    def data_handle_show_chat_log(data_json):
        global scroll_area_chat_box_chat_message_num

        if data_json["type"] == "llm":
            if data_json["data"]["content_type"] == "question":
                name = data_json["data"]['username']
                if 'user_face' in data_json["data"]:
                    # 由于直接请求b站头像返回403 所以暂时还是用默认头像
                    # avatar = data_json["data"]['user_face']
                    avatar = 'https://robohash.org/ui'
                else:
                    avatar = 'https://robohash.org/ui'
            else:
                name = data_json["data"]['type']
                avatar = "http://127.0.0.1:8081/favicon.ico"

            with scroll_area_chat_box:
                ui.chat_message(data_json["data"]["content"],
                    name=name,
                    stamp=data_json["data"]["timestamp"],
                    avatar=avatar
                )

                scroll_area_chat_box_chat_message_num += 1

            if scroll_area_chat_box_chat_message_num > scroll_area_chat_box_chat_message_max_num:
                scroll_area_chat_box.remove(0)

            scroll_area_chat_box.scroll_to(percent=1, duration=0.2)

    """

                  /@@@@@@@@          @@@@@@@@@@@@@@@].      =@@@@@@@       
                 =@@@@@@@@@^         @@@@@@@@@@@@@@@@@@`    =@@@@@@@       
                ,@@@@@@@@@@@`        @@@@@@@@@@@@@@@@@@@^   =@@@@@@@       
               .@@@@@@\@@@@@@.       @@@@@@@^   .\@@@@@@\   =@@@@@@@       
               /@@@@@/ \@@@@@\       @@@@@@@^    =@@@@@@@   =@@@@@@@       
              =@@@@@@. .@@@@@@^      @@@@@@@\]]]@@@@@@@@^   =@@@@@@@       
             ,@@@@@@^   =@@@@@@`     @@@@@@@@@@@@@@@@@@/    =@@@@@@@       
            .@@@@@@@@@@@@@@@@@@@.    @@@@@@@@@@@@@@@@/`     =@@@@@@@       
            /@@@@@@@@@@@@@@@@@@@\    @@@@@@@^               =@@@@@@@       
           =@@@@@@@@@@@@@@@@@@@@@^   @@@@@@@^               =@@@@@@@       
          ,@@@@@@@.       ,@@@@@@@`  @@@@@@@^               =@@@@@@@       
          @@@@@@@^         =@@@@@@@. @@@@@@@^               =@@@@@@@   

    """
    
    

    from starlette.requests import Request
    from utils.models import SendMessage, CommonResult, SysCmdMessage, SetConfigMessage

    """
    配置config

        config_path (str): 配置文件路径
        data (dict): 传入的json

    return:
        {"code": 200, "message": "成功"}
    """
    @app.post('/set_config')
    async def set_config(msg: SetConfigMessage):
        global config

        try:
            data_json = msg.dict()
            logger.info(f'set_config接口 收到数据：{data_json}')

            config_data = None

            try:
                with open(data_json["config_path"], 'r', encoding="utf-8") as config_file:
                    config_data = json.load(config_file)
            except Exception as e:
                logger.error(f"无法读取配置文件！\n{e}")
                return CommonResult(code=-1, message=f"无法读取配置文件！{e}")
            
            # 合并字典
            config_data.update(data_json["data"])

            # 写入配置到配置文件
            try:
                with open(data_json["config_path"], 'w', encoding="utf-8") as config_file:
                    json.dump(config_data, config_file, indent=2, ensure_ascii=False)
                    config_file.flush()  # 刷新缓冲区，确保写入立即生效

                logger.info("配置数据已成功写入文件！")

                return CommonResult(code=200, message="配置数据已成功写入文件！")
            except Exception as e:
                logger.error(f"无法写入配置文件！\n{str(e)}")
                return CommonResult(code=-1, message=f"无法写入配置文件！{e}")
        except Exception as e:
            logger.error(traceback.format_exc())
            return CommonResult(code=-1, message=f"{data_json['type']}执行失败！{e}")

    """
    系统命令
        type 命令类型（run/stop/restart/factory）
        data 传入的json

    data_json = {
        "type": "命令名",
        "data": {
            "key": "value"
        }
    }

    return:
        {"code": 200, "message": "成功"}
        {"code": -1, "message": "失败"}
    """
    @app.post('/sys_cmd')
    async def sys_cmd(msg: SysCmdMessage):
        try:
            data_json = msg.dict()
            logger.info(f'sys_cmd接口 收到数据：{data_json}')
            logger.info(f"开始执行 {data_json['type']}命令...")

            resp_json = {}

            if data_json['type'] == 'run':
                """
                {
                    "type": "run",
                    "data": {
                        "config_path": "config.json"
                    }
                }
                """
                # 运行
                resp_json = run_external_program(data_json['data']['config_path'], type="api")
            elif data_json['type'] =='stop':
                """
                {
                    "type": "stop",
                    "data": {
                        "config_path": "config.json"
                    }
                }
                """
                # 停止
                resp_json = stop_external_program(type="api")
            elif data_json['type'] =='restart':
                """
                {
                    "type": "restart",
                    "api_type": "webui",
                    "data": {
                        "config_path": "config.json"
                    }
                }
                """
                # 重启
                resp_json = restart_application(type=data_json['api_type'])
            elif data_json['type'] =='factory':
                """
                {
                    "type": "factory",
                    "api_type": "webui",
                    "data": {
                        "src_path": "config.json.bak",
                        "dst_path": "config.json"
                    }
                }
                """
                # 恢复出厂
                resp_json = factory(data_json['data']['src_path'], data_json['data']['dst_path'], type="api")

            return resp_json
        except Exception as e:
            logger.error(traceback.format_exc())
            return CommonResult(code=-1, message=f"{data_json['type']}执行失败！{e}")

    """
    发送数据
        type 数据类型（comment/gift/entrance/reread/tuning/...）
        key  根据数据类型自行适配

    data_json = {
        "type": "数据类型",
        "key": "value"
    }

    return:
        {"code": 200, "message": "成功"}
        {"code": -1, "message": "失败"}
    """
    @app.post('/send')
    async def send(msg: SendMessage):
        global config

        try:
            data_json = msg.dict()
            logger.info(f'WEBUI API send接口收到数据：{data_json}')

            main_api_ip = "127.0.0.1" if config.get("api_ip") == "0.0.0.0" else config.get("api_ip")
            resp_json = await common.send_async_request(f'http://{main_api_ip}:{config.get("api_port")}/send', "POST", data_json)

            return resp_json
        except Exception as e:
            logger.error(traceback.format_exc())
            return CommonResult(code=-1, message=f"发送数据失败！{e}")



    """
    数据回调
        data 传入的json

    data_json = {
        "type": "数据类型（llm）",
        "data": {
            "type": "LLM类型",
            "username": "用户名",
            "content_type": "内容的类型（question/answer）",
            "content": "回复内容",
            "timestamp": "时间戳"
        }
    }

    return:
        {"code": 200, "message": "成功"}
        {"code": -1, "message": "失败"}
    """
    @app.post('/callback')
    async def callback(request: Request):
        try:
            data_json = await request.json()
            logger.info(f'WEBUI API callback接口收到数据：{data_json}')

            data_handle_show_chat_log(data_json)

            return {"code": 200, "message": "成功"}
        except Exception as e:
            logger.error(traceback.format_exc())
            return CommonResult(code=-1, message=f"失败！{e}")


    """
    TTS合成，获取合成的音频文件路径
        data 传入的json

    例如：
    data_json = {
        "type": "reread",
        "tts_type": "gpt_sovits",
        "data": {
            "type": "api",
            "api_ip_port": "http://127.0.0.1:9880",
            "ref_audio_path": "F:\\GPT-SoVITS\\raws\\ikaros\\21.wav",
            "prompt_text": "マスター、どうりょくろか、いいえ、なんでもありません",
            "prompt_language": "日文",
            "language": "自动识别",
            "cut": "凑四句一切",
            "gpt_model_path": "F:\\GPT-SoVITS\\GPT_weights\\ikaros-e15.ckpt",
            "sovits_model_path": "F:\\GPT-SoVITS\\SoVITS_weights\\ikaros_e8_s280.pth",
            "webtts": {
                "api_ip_port": "http://127.0.0.1:8080",
                "spk": "sanyueqi",
                "lang": "zh",
                "speed": "1.0",
                "emotion": "正常"
            }
        },
        "username": "主人",
        "content": "你好，这就是需要合成的文本内容"
    }

    return:
        {
            "code": 200,
            "message": "成功",
            "data": {
                "type": "reread",
                "tts_type": "gpt_sovits",
                "data": {
                    "type": "api",
                    "api_ip_port": "http://127.0.0.1:9880",
                    "ref_audio_path": "F:\\\\GPT-SoVITS\\\\raws\\\\ikaros\\\\21.wav",
                    "prompt_text": "マスター、どうりょくろか、いいえ、なんでもありません",
                    "prompt_language": "日文",
                    "language": "自动识别",
                    "cut": "凑四句一切",
                    "gpt_model_path": "F:\\GPT-SoVITS\\GPT_weights\\ikaros-e15.ckpt",
                    "sovits_model_path": "F:\\GPT-SoVITS\\SoVITS_weights\\ikaros_e8_s280.pth",
                    "webtts": {
                        "api_ip_port": "http://127.0.0.1:8080",
                        "spk": "sanyueqi",
                        "lang": "zh",
                        "speed": "1.0",
                        "emotion": "正常"
                    }
                },
                "username": "主人",
                "content": "你好，这就是需要合成的文本内容",
                "result": {
                    "code": 200,
                    "msg": "合成成功",
                    "audio_path": "E:\\GitHub_pro\\AI-Vtuber\\out\\gpt_sovits_4.wav"
                }
            }
        }

        {"code": -1, "message": "失败"}
    """
    @app.post('/tts')
    async def tts(request: Request):
        try:
            data_json = await request.json()
            logger.info(f'WEBUI API tts接口收到数据：{data_json}')

            resp_json = await audio.tts_handle(data_json)

            return {"code": 200, "message": "成功", "data": resp_json}
        except Exception as e:
            logger.error(traceback.format_exc())
            return CommonResult(code=-1, message=f"失败！{e}")


    """
    LLM推理，获取推理结果
        data 传入的json

    例如：type就是聊天类型实际对应的值
    data_json = {
        "type": "chatgpt",
        "username": "用户名",
        "content": "你好"
    }

    return:
        {
            "code": 200,
            "message": "成功",
            "data": {
                "content": "你好，这是LLM回复的内容"
            }
        }

        {"code": -1, "message": "失败"}
    """
    @app.post('/llm')
    async def llm(request: Request):
        try:
            data_json = await request.json()
            logger.info(f'WEBUI API llm接口 收到数据：{data_json}')

            main_api_ip = "127.0.0.1" if config.get("api_ip") == "0.0.0.0" else config.get("api_ip")
            resp_json = await common.send_async_request(f'http://{main_api_ip}:{config.get("api_port")}/llm', "POST", data_json, "json", timeout=60)
            if resp_json:
                return resp_json
            
            return CommonResult(code=-1, message="失败！")
        except Exception as e:
            logger.error(traceback.format_exc())
            return CommonResult(code=-1, message=f"失败！{e}")

    # 获取系统信息接口
    @app.get("/get_sys_info")
    async def get_sys_info():
        try:
            # logger.info(f'WEBUI API get_sys_info接口 收到请求')

            main_api_ip = "127.0.0.1" if config.get("api_ip") == "0.0.0.0" else config.get("api_ip")
            resp_json = await common.send_async_request(f'http://{main_api_ip}:{config.get("api_port")}/get_sys_info', "GET", None, "json", timeout=60)
            if resp_json:
                return resp_json
            return CommonResult(code=-1, message="失败！")
        except Exception as e:
            logger.error(f"get_sys_info处理失败！{e}")
            return CommonResult(code=-1, message=f"get_sys_info处理失败！{e}")


        
    """
                                                     ./@\]                    
                   ,@@@@\*                             \@@^ ,]]]              
                      [[[*                      /@@]@@@@@/[[\@@@@/            
                        ]]@@@@@@\              /@@^  @@@^]]`[[                
                ]]@@@@@@@[[*                   ,[`  /@@\@@@@@@@@@@@@@@^       
             [[[[[`   @@@/                 \@@@@[[[\@@^ =@@/                  
              .\@@\* *@@@`                           [\@@@@@@\`               
                 ,@@\=@@@                         ,]@@@/`  ,\@@@@*            
                   ,@@@@`                     ,[[[[`  =@@@   ]]/O             
                   /@@@@@`                    ]]]@@@@@@@@@/[[[[[`             
                ,@@@@[ \@@@\`                      ./@@@@@@@]                 
          ,]/@@@@/`      \@@@@@\]]               ,@@@/,@@^ \@@@\]             
                           ,@@@@@@@@/[*       ,/@@/*  /@@^   [@@@@@@@\*       
                                                      ,@@^                    
                                                              
    """

    # 文案页-增加
    def copywriting_add():
        data_len = len(copywriting_config_var)
        tmp_config = {
            "file_path": f"data/copywriting{int(data_len / 5) + 1}/",
            "audio_path": f"out/copywriting{int(data_len / 5) + 1}/",
            "continuous_play_num": 2,
            "max_play_time": 10.0,
            "play_list": []
        }

        with copywriting_config_card.style(card_css):
            with ui.row():
                copywriting_config_var[str(data_len)] = ui.input(label=f"文案存储路径#{int(data_len / 5) + 1}", value=tmp_config["file_path"], placeholder='文案文件存储路径。不建议更改。').style("width:200px;")
                copywriting_config_var[str(data_len + 1)] = ui.input(label=f"音频存储路径#{int(data_len / 5) + 1}", value=tmp_config["audio_path"], placeholder='文案音频文件存储路径。不建议更改。').style("width:200px;")
                copywriting_config_var[str(data_len + 2)] = ui.input(label=f"连续播放数#{int(data_len / 5) + 1}", value=tmp_config["continuous_play_num"], placeholder='文案播放列表中连续播放的音频文件个数，如果超过了这个个数就会切换下一个文案列表').style("width:200px;")
                copywriting_config_var[str(data_len + 3)] = ui.input(label=f"连续播放时间#{int(data_len / 5) + 1}", value=tmp_config["max_play_time"], placeholder='文案播放列表中连续播放音频的时长，如果超过了这个时长就会切换下一个文案列表').style("width:200px;")
                copywriting_config_var[str(data_len + 4)] = ui.textarea(label=f"播放列表#{int(data_len / 5) + 1}", value=textarea_data_change(tmp_config["play_list"]), placeholder='此处填写需要播放的音频文件全名，填写完毕后点击 保存配置。文件全名从音频列表中复制，换行分隔，请勿随意填写').style("width:500px;")

    # 文案页-删除
    def copywriting_del(index):
        try:
            copywriting_config_card.remove(int(index) - 1)
            # 删除操作
            keys_to_delete = [str(5 * (int(index) - 1) + i) for i in range(5)]
            for key in keys_to_delete:
                if key in copywriting_config_var:
                    del copywriting_config_var[key]

            # 重新编号剩余的键
            updates = {}
            for key in sorted(copywriting_config_var.keys(), key=int):
                new_key = str(int(key) - 5 if int(key) > int(keys_to_delete[-1]) else key)
                updates[new_key] = copywriting_config_var[key]

            # 应用更新
            copywriting_config_var.clear()
            copywriting_config_var.update(updates)
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"错误，索引值配置有误：{e}")
            logger.error(traceback.format_exc())

    # 文案页-加载文本
    def copywriting_text_load():
        copywriting_text_path = input_copywriting_text_path.value
        if "" == copywriting_text_path:
            logger.warning(f"请输入 文案文本路径喵~")
            ui.notify(position="top", type="warning", message="请输入 文案文本路径喵~")
            return
        
        # 传入完整文件路径 绝对或相对
        logger.info(f"准备加载 文件：[{copywriting_text_path}]")
        new_file_path = os.path.join(copywriting_text_path)

        content = common.read_file_return_content(new_file_path)
        if content is None:
            logger.error(f"读取失败！请检测配置、文件路径、文件名")
            ui.notify(position="top", type="negative", message="读取失败！请检测配置、文件路径、文件名")
            return
        
        # 数据写入文本输入框中
        textarea_copywriting_text.value = content

        logger.info(f"成功加载文案：{copywriting_text_path}")
        ui.notify(position="top", type="positive", message=f"成功加载文案：{copywriting_text_path}")


    # 文案页-保存文案
    def copywriting_save_text():
        content = textarea_copywriting_text.value
        copywriting_text_path = input_copywriting_text_path.value
        if "" == copywriting_text_path:
            logger.warning(f"请输入 文案文本路径喵~")
            ui.notify(position="top", type="warning", message="请输入 文案文本路径喵~")
            return
        
        new_file_path = os.path.join(copywriting_text_path)
        if common.write_content_to_file(new_file_path, content):
            ui.notify(position="top", type="positive", message=f"保存成功~")
        else:
            ui.notify(position="top", type="negative", message=f"保存失败！请查看日志排查问题")


    # 文案页-合成音频
    async def copywriting_audio_synthesis():
        ui.notify(position="top", type="warning", message="文案音频合成中，将会阻塞其他任务运行，请勿做其他操作，查看日志情况，耐心等待")
        logger.warning("文案音频合成中，将会阻塞其他任务运行，请勿做其他操作，查看日志情况，耐心等待")
        
        copywriting_text_path = input_copywriting_text_path.value
        copywriting_audio_save_path = input_copywriting_audio_save_path.value
        audio_synthesis_type = select_copywriting_audio_synthesis_type.value

        file_path = await audio.copywriting_synthesis_audio(copywriting_text_path, copywriting_audio_save_path, audio_synthesis_type)

        if file_path:
            ui.notify(position="top", type="positive", message=f"文案音频合成成功，存储于：{file_path}")
        else:
            ui.notify(position="top", type="negative", message=f"文案音频合成失败！请查看日志排查问题")
            return

        def clear_copywriting_audio_card(file_path):
            copywriting_audio_card.clear()
            if common.del_file(file_path):
                ui.notify(position="top", type="positive", message=f"删除文件成功：{file_path}")
            else:
                ui.notify(position="top", type="negative", message=f"删除文件失败：{file_path}")
        
        # 清空card
        copywriting_audio_card.clear()
        tmp_label = ui.label(f"文案音频合成成功，存储于：{file_path}")
        tmp_label.move(copywriting_audio_card)
        audio_copywriting = ui.audio(src=file_path)
        audio_copywriting.move(copywriting_audio_card)
        button_copywriting_audio_del = ui.button('删除音频', on_click=lambda: clear_copywriting_audio_card(file_path), color=button_internal_color).style(button_internal_css)
        button_copywriting_audio_del.move(copywriting_audio_card)
        

    # 文案页-循环播放
    def copywriting_loop_play():
        if running_flag != 1:
            ui.notify(position="top", type="warning", message=f"请先点击“一键运行”，然后再进行播放")
            return
        
        logger.info("开始循环播放文案~")
        ui.notify(position="top", type="positive", message="开始循环播放文案~")
        
        audio.unpause_copywriting_play()

    # 文案页-暂停播放
    def copywriting_pause_play():
        if running_flag != 1:
            ui.notify(position="top", type="warning", message=f"请先点击“一键运行”，然后再进行暂停")
            return
        
        audio.pause_copywriting_play()
        logger.info("暂停文案完毕~")
        ui.notify(position="top", type="positive", message="暂停文案完毕~")

    """
    定时任务
    """
    # -增加
    def schedule_add():
        data_len = len(schedule_var)
        tmp_config = {
            "enable": False,
            "time_min": 60,
            "time_max": 120,
            "copy": []
        }

        with schedule_config_card.style(card_css):
            with ui.row():
                schedule_var[str(data_len)] = ui.switch(text=f"启用任务#{int(data_len / 4) + 1}", value=tmp_config["enable"]).style(switch_internal_css)
                schedule_var[str(data_len + 1)] = ui.input(label=f"最小循环周期#{int(data_len / 4) + 1}", value=tmp_config["time_min"], placeholder='定时任务循环的周期最小时长（秒），即每间隔这个周期就会执行一次').style("width:100px;")
                schedule_var[str(data_len + 2)] = ui.input(label=f"最大循环周期#{int(data_len / 4) + 1}", value=tmp_config["time_max"], placeholder='定时任务循环的周期最大时长（秒），即每间隔这个周期就会执行一次').style("width:100px;")
                schedule_var[str(data_len + 3)] = ui.textarea(label=f"文案列表#{int(data_len / 4) + 1}", value=textarea_data_change(tmp_config["copy"]), placeholder='存放文案的列表，通过空格或换行分割，通过{变量}来替换关键数据，可修改源码自定义功能').style("width:500px;")


    # -删除
    def schedule_del(index):
        try:
            schedule_config_card.remove(int(index) - 1)
            # 删除操作
            keys_to_delete = [str(4 * (int(index) - 1) + i) for i in range(4)]
            for key in keys_to_delete:
                if key in schedule_var:
                    del schedule_var[key]

            # 重新编号剩余的键
            updates = {}
            for key in sorted(schedule_var.keys(), key=int):
                new_key = str(int(key) - 4 if int(key) > int(keys_to_delete[-1]) else key)
                updates[new_key] = schedule_var[key]

            # 应用更新
            schedule_var.clear()
            schedule_var.update(updates)
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"错误，索引值配置有误：{e}")
            logger.error(traceback.format_exc())



    """
    动态文案
    """
    # 动态文案-增加
    def trends_copywriting_add():
        data_len = len(trends_copywriting_copywriting_var)
        tmp_config = {
            "folder_path": "",
            "prompt_change_enable": False,
            "prompt_change_content": ""
        }

        with trends_copywriting_config_card.style(card_css):
            with ui.row():
                trends_copywriting_copywriting_var[str(data_len)] = ui.input(label=f"文案路径#{int(data_len / 3) + 1}", value=tmp_config["folder_path"], placeholder='文案文件存储的文件夹路径').style("width:200px;")
                trends_copywriting_copywriting_var[str(data_len + 1)] = ui.switch(text=f"提示词转换#{int(data_len / 3) + 1}", value=tmp_config["prompt_change_enable"])
                trends_copywriting_copywriting_var[str(data_len + 2)] = ui.input(label=f"提示词转换内容#{int(data_len / 3) + 1}", value=tmp_config["prompt_change_content"], placeholder='使用此提示词内容对文案内容进行转换后再进行合成，使用的LLM为聊天类型配置').style("width:500px;")


    # 动态文案-删除
    def trends_copywriting_del(index):
        try:
            trends_copywriting_config_card.remove(int(index) - 1)
            # 删除操作
            keys_to_delete = [str(3 * (int(index) - 1) + i) for i in range(3)]
            for key in keys_to_delete:
                if key in trends_copywriting_copywriting_var:
                    del trends_copywriting_copywriting_var[key]

            # 重新编号剩余的键
            updates = {}
            for key in sorted(trends_copywriting_copywriting_var.keys(), key=int):
                new_key = str(int(key) - 3 if int(key) > int(keys_to_delete[-1]) else key)
                updates[new_key] = trends_copywriting_copywriting_var[key]

            # 应用更新
            trends_copywriting_copywriting_var.clear()
            trends_copywriting_copywriting_var.update(updates)
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"错误，索引值配置有误：{e}")
            logger.error(traceback.format_exc())

    
    """
    联动程序
    """
    # 联动程序-增加
    def coordination_program_add():
        data_len = len(coordination_program_var)
        tmp_config = {
            "enable": True,
            "name": "",
            "executable": "",
            "parameters": []
        }

        with coordination_program_config_card.style(card_css):
            with ui.row():
                coordination_program_var[str(data_len)] = ui.switch(f'启用#{int(data_len / 4) + 1}', value=tmp_config["enable"]).style(switch_internal_css)
                coordination_program_var[str(data_len + 1)] = ui.input(label=f"程序名#{int(data_len / 4) + 1}", value=tmp_config["name"], placeholder='给你的程序取个名字，别整特殊符号！').style("width:200px;")
                coordination_program_var[str(data_len + 2)] = ui.input(label=f"可执行程序#{int(data_len / 4) + 1}", value=tmp_config["executable"], placeholder='可执行程序的路径，最好是绝对路径，如python的程序').style("width:400px;")
                coordination_program_var[str(data_len + 3)] = ui.textarea(label=f'参数#{int(data_len / 4) + 1}', value=textarea_data_change(tmp_config["parameters"]), placeholder='参数，可以传入多个参数，换行分隔。如启动的程序的路径，命令携带的传参等').style("width:500px;")


    # 联动程序-删除
    def coordination_program_del(index):
        try:
            coordination_program_config_card.remove(int(index) - 1)
            # 删除操作
            keys_to_delete = [str(4 * (int(index) - 1) + i) for i in range(4)]
            for key in keys_to_delete:
                if key in coordination_program_var:
                    del coordination_program_var[key]

            # 重新编号剩余的键
            updates = {}
            for key in sorted(coordination_program_var.keys(), key=int):
                new_key = str(int(key) - 4 if int(key) > int(keys_to_delete[-1]) else key)
                updates[new_key] = coordination_program_var[key]

            # 应用更新
            coordination_program_var.clear()
            coordination_program_var.update(updates)
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"错误，索引值配置有误：{e}")
            logger.error(traceback.format_exc())


    """
    按键/文案映射
    """
    def key_mapping_add():
        data_len = len(key_mapping_config_var)
        tmp_config = {
            "keywords": [],
            "gift": [],
            "keys": [],
            "similarity": 1,
            "copywriting": [],
            "local_audio": [],
            "img_path": []
        }

        with key_mapping_config_card.style(card_css):
            with ui.row():
                num = int(data_len / 9) + 1
                key_mapping_config_var[str(data_len)] = ui.textarea(label=f"关键词#{num}", value=textarea_data_change(tmp_config["keywords"]), placeholder='此处输入触发的关键词，多个请以换行分隔').style("width:100px;")
                key_mapping_config_var[str(data_len + 1)] = ui.textarea(label=f"礼物#{num}", value=textarea_data_change(tmp_config["gift"]), placeholder='此处输入触发的礼物名，多个请以换行分隔').style("width:100px;")
                key_mapping_config_var[str(data_len + 2)] = ui.textarea(label=f"按键#{num}", value=textarea_data_change(tmp_config["keys"]), placeholder='此处输入你要映射的按键，多个按键请以换行分隔（按键名参考pyautogui规则）').style("width:100px;")
                key_mapping_config_var[str(data_len + 3)] = ui.input(label=f"相似度#{num}", value=tmp_config["similarity"], placeholder='关键词与用户输入的相似度，默认1即100%').style("width:50px;")
                key_mapping_config_var[str(data_len + 4)] = ui.textarea(label=f"文案#{num}", value=textarea_data_change(tmp_config["copywriting"]), placeholder='此处输入触发后合成的文案内容，多个请以换行分隔').style("width:300px;")
                key_mapping_config_var[str(data_len + 5)] = ui.textarea(label=f"文案#{num}", value=textarea_data_change(tmp_config["copywriting"]), placeholder='此处输入触发后合成的文案内容，多个请以换行分隔').style("width:300px;")
                key_mapping_config_var[str(data_len + 6)] = ui.input(label=f"串口名#{num}", value=tmp_config["serial_name"], placeholder='例如：COM1').style("width:100px;").tooltip('串口页配置的串口名，例如：COM1')
                key_mapping_config_var[str(data_len + 7)] = ui.textarea(label=f"串口发送内容#{num}", value=textarea_data_change(tmp_config["serial_send_data"]), placeholder='多个请以换行分隔，ASCII例如：open led\nHEX例如（2个字符的十六进制字符）：313233').style("width:300px;").tooltip('此处输入发送到串口的数据内容，数据类型根据串口页设置决定，多个请以换行分隔')
                key_mapping_config_var[str(data_len + 8)] = ui.textarea(label=f"串口发送内容#{num}", value=textarea_data_change(tmp_config["serial_send_data"]), placeholder='多个请以换行分隔，ASCII例如：open led\nHEX例如（2个字符的十六进制字符）：313233').style("width:300px;").tooltip('此处输入发送到串口的数据内容，数据类型根据串口页设置决定，多个请以换行分隔')
                          
    
    def key_mapping_del(index):
        try:
            num = 9
            key_mapping_config_card.remove(int(index) - 1)
            # 删除操作
            keys_to_delete = [str(num * (int(index) - 1) + i) for i in range(num)]
            for key in keys_to_delete:
                if key in key_mapping_config_var:
                    del key_mapping_config_var[key]

            # 重新编号剩余的键
            updates = {}
            for key in sorted(key_mapping_config_var.keys(), key=int):
                new_key = str(int(key) - num if int(key) > int(keys_to_delete[-1]) else key)
                updates[new_key] = key_mapping_config_var[key]

            # 应用更新
            key_mapping_config_var.clear()
            key_mapping_config_var.update(updates)
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"错误，索引值配置有误：{e}")
            logger.error(traceback.format_exc())


    """
    自定义命令
    """
    
    # 自定义命令-增加
    def custom_cmd_add():
        data_len = len(custom_cmd_config_var)

        tmp_config = {
            "keywords": [],
            "similarity": 1,
            "api_url": "",
            "api_type": "",
            "resp_data_type": "",
            "data_analysis": "",
            "resp_template": ""
        }

        with custom_cmd_config_card.style(card_css):
            with ui.row():
                custom_cmd_config_var[str(data_len)] = ui.textarea(label=f"关键词#{int(data_len / 7) + 1}", value=textarea_data_change(tmp_config["keywords"]), placeholder='此处输入触发的关键词，多个请以换行分隔').style("width:200px;")
                custom_cmd_config_var[str(data_len + 1)] = ui.input(label=f"相似度#{int(data_len / 7) + 1}", value=tmp_config["similarity"], placeholder='关键词与用户输入的相似度，默认1即100%').style("width:100px;")
                custom_cmd_config_var[str(data_len + 2)] = ui.textarea(label=f"API URL#{int(data_len / 7) + 1}", value=tmp_config["api_url"], placeholder='发送HTTP请求的API链接', validation={'请输入正确格式的URL': lambda value: common.is_url_check(value),}).style("width:300px;")
                custom_cmd_config_var[str(data_len + 3)] = ui.select(label=f"API类型#{int(data_len / 7) + 1}", value=tmp_config["api_type"], options={"GET": "GET"}).style("width:100px;")
                custom_cmd_config_var[str(data_len + 4)] = ui.select(label=f"请求返回数据类型#{int(data_len / 7) + 1}", value=tmp_config["resp_data_type"], options={"json": "json", "content": "content"}).style("width:150px;")
                custom_cmd_config_var[str(data_len + 5)] = ui.textarea(label=f"数据解析（eval执行）#{int(data_len / 7) + 1}", value=tmp_config["data_analysis"], placeholder='数据解析，请不要随意修改resp变量，会被用于最后返回数据内容的解析').style("width:200px;")
                custom_cmd_config_var[str(data_len + 6)] = ui.textarea(label=f"返回内容模板#{int(data_len / 7) + 1}", value=tmp_config["resp_template"], placeholder='请不要随意删除data变量，支持动态变量，最终会合并成完成内容进行音频合成').style("width:300px;")


    # 自定义命令-删除
    def custom_cmd_del(index):
        try:
            custom_cmd_config_card.remove(int(index) - 1)
            # 删除操作
            keys_to_delete = [str(7 * (int(index) - 1) + i) for i in range(7)]
            for key in keys_to_delete:
                if key in custom_cmd_config_var:
                    del custom_cmd_config_var[key]

            # 重新编号剩余的键
            updates = {}
            for key in sorted(custom_cmd_config_var.keys(), key=int):
                new_key = str(int(key) - 7 if int(key) > int(keys_to_delete[-1]) else key)
                updates[new_key] = custom_cmd_config_var[key]

            # 应用更新
            custom_cmd_config_var.clear()
            custom_cmd_config_var.update(updates)
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"错误，索引值配置有误：{e}")
            logger.error(traceback.format_exc())

    """
    串口
    """
    
    def serial_config_add():
        data_len = len(serial_config_var)

        tmp_config = {
            "serial_name": "COM1",
            "baudrate": "115200",
            "serial_data_type": "ASCII"
        }

        with serial_config_card.style(card_css):
            with ui.row():
                serial_config_var[str(data_len)] = ui.select(label=f"串口名#{int(data_len / 8) + 1}", value=tmp_config["serial_name"], options={f'{tmp_config["serial_name"]}': f'{tmp_config["serial_name"]}'}).style("width:200px;").tooltip('文案文件存储路径。不建议更改。')
                serial_config_var[str(data_len + 1)] = ui.select(
                    label=f"波特率#{int(data_len / 8) + 1}", 
                    value=tmp_config["baudrate"], 
                    options={'9600': '9600', '19200': '19200', '38400': '38400', '115200': '115200'}
                ).style("width:200px;").tooltip('波特率')
                serial_config_var[str(data_len + 2)] = ui.button('刷新串口', on_click=lambda: refresh_serial(int(data_len / 8)))
                serial_config_var[str(data_len + 3)] = ui.button('打开串口', on_click=lambda: connect_serial(int(data_len / 8)))
                serial_config_var[str(data_len + 4)] = ui.button('关闭串口', on_click=lambda: disconnect_serial(int(data_len / 8)))

                serial_config_var[str(data_len + 5)] = ui.select(label=f"发送数据类型#{int(data_len / 8) + 1}", value=tmp_config["serial_data_type"], options={'ASCII': 'ASCII', 'HEX': 'HEX'},).style("width:100px;").tooltip('发送的数据类型')
                serial_config_var[str(data_len + 6)] = ui.input(label=f"发送数据#{int(data_len / 8) + 1}", value="", placeholder='填要发的内容，连接后，点 发送').style("width:200px;").tooltip('填要发的内容，连接后，点 发送')
                serial_config_var[str(data_len + 7)] = ui.button('发送', on_click=lambda: send_data_to_serial(int(data_len / 8)))

    def serial_config_del(index):
        try:
            serial_config_card.remove(int(index) - 1)
            # 删除操作
            keys_to_delete = [str(8 * (int(index) - 1) + i) for i in range(8)]
            for key in keys_to_delete:
                if key in serial_config_var:
                    del serial_config_var[key]

            # 重新编号剩余的键
            updates = {}
            for key in sorted(serial_config_var.keys(), key=int):
                new_key = str(int(key) - 8 if int(key) > int(keys_to_delete[-1]) else key)
                updates[new_key] = serial_config_var[key]

            # 应用更新
            serial_config_var.clear()
            serial_config_var.update(updates)
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"错误，索引值配置有误：{e}")
            logger.error(traceback.format_exc())


    """
    添加本地路径到URL路径
    """
    # -增加
    def webui_local_dir_to_endpoint_add():
        data_len = len(webui_local_dir_to_endpoint_config_var)
        tmp_config = {
            "url_path": "",
            "local_dir": "",
        }

        with webui_local_dir_to_endpoint_config_card.style(card_css):
            with ui.row():
                webui_local_dir_to_endpoint_config_var[str(data_len)] = ui.input(label=f"URL路径#{int(data_len / 2) + 1}", value=tmp_config["url_path"], placeholder='以斜杠（"/"）开始的字符串，它标识了应该为客户端提供文件的URL路径').style("width:300px;")
                webui_local_dir_to_endpoint_config_var[str(data_len + 1)] = ui.input(label=f"本地文件夹路径#{int(data_len / 2) + 1}", value=tmp_config["local_dir"], placeholder='本地文件夹路径，建议相对路径，最好是项目内部的路径').style("width:300px;")


    # -删除
    def webui_local_dir_to_endpoint_del(index):
        try:
            webui_local_dir_to_endpoint_config_card.remove(int(index) - 1)
            # 删除操作
            keys_to_delete = [str(2 * (int(index) - 1) + i) for i in range(2)]
            for key in keys_to_delete:
                if key in webui_local_dir_to_endpoint_config_var:
                    del webui_local_dir_to_endpoint_config_var[key]

            # 重新编号剩余的键
            updates = {}
            for key in sorted(webui_local_dir_to_endpoint_config_var.keys(), key=int):
                new_key = str(int(key) - 2 if int(key) > int(keys_to_delete[-1]) else key)
                updates[new_key] = webui_local_dir_to_endpoint_config_var[key]

            # 应用更新
            webui_local_dir_to_endpoint_config_var.clear()
            webui_local_dir_to_endpoint_config_var.update(updates)
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"错误，索引值配置有误：{e}")
            logger.error(traceback.format_exc())


    # 配置模板保存
    def config_template_save(file_path: str):
        try:
            with open(config_path, 'r', encoding="utf-8") as config_file:
                config_data = json.load(config_file)

            config_data = webui_config_to_dict(config_data)

            # 将JSON数据保存到文件中
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(config_data, file, indent=2, ensure_ascii=False)
                file.flush()  # 刷新缓冲区，确保写入立即生效

            logger.info("配置模板保存成功！")
            ui.notify(position="top", type="positive", message=f"配置模板保存成功！")

            return True
        except Exception as e:
            logger.error(f"配置模板保存失败！\n{e}")
            ui.notify(position="top", type="negative", message=f"配置模板保存失败！{e}")
            return False


    # 配置模板加载
    def config_template_load(file_path: str):
        try:
            with open(file_path, 'r', encoding="utf-8") as config_file:
                config_data = json.load(config_file)

            # 将JSON数据保存到文件中
            with open(config_path, "w", encoding="utf-8") as file:
                json.dump(config_data, file, indent=2, ensure_ascii=False)
                file.flush()  # 刷新缓冲区，确保写入立即生效

            logger.info("配置模板加载成功！重启后读取！想反悔就直接保存下当前配置，然后再重启！！！")
            ui.notify(position="top", type="positive", message=f"配置模板加载成功！重启后读取！想反悔就直接保存下当前配置，然后再重启！！！")
            
            return True
        except Exception as e:
            logger.error(f"配置模板读取失败！\n{e}")
            ui.notify(position="top", type="negative", message=f"配置模板读取失败！{e}")
            return False


    """
    配置操作
    """
    # 配置检查
    def check_config():
        try:
            # 通用配置 页面 配置正确性校验
            if select_platform.value == 'bilibili2' and select_bilibili_login_type.value == 'cookie' and input_bilibili_cookie.value == '':
                ui.notify(position="top", type="warning", message="请先前往 通用配置-哔哩哔哩，填写B站cookie")
                return False
            elif select_platform.value == 'bilibili2' and select_bilibili_login_type.value == 'open_live' and \
                (input_bilibili_open_live_ACCESS_KEY_ID.value == '' or input_bilibili_open_live_ACCESS_KEY_SECRET.value == '' or \
                input_bilibili_open_live_APP_ID.value == '' or input_bilibili_open_live_ROOM_OWNER_AUTH_CODE.value == ''):
                ui.notify(position="top", type="warning", message="请先前往 通用配置-哔哩哔哩，填写开放平台配置")
                return False


            """
            针对配置情况进行提示
            """
            tip_config = f'平台：{platform_options[select_platform.value]} | ' +\
                f'大语言模型：{chat_type_options[select_chat_type.value]} | ' +\
                f'语音合成：{audio_synthesis_type_options[select_audio_synthesis_type.value]} | ' +\
                f'虚拟身体：{visual_body_options[select_visual_body.value]}'
            ui.notify(position="top", type="info", message=tip_config)

            # 检测平台配置，进行提示
            if select_platform.value == "dy":
                ui.notify(position="top", type="warning", message="对接抖音平台时，请先开启抖音弹幕监听程序！直播间号不需要填写")
            elif select_platform.value == "bilibili":
                ui.notify(position="top", type="info", message="哔哩哔哩1 监听不是很稳定，推荐使用 哔哩哔哩2")
            elif select_platform.value == "bilibili2":
                if select_bilibili_login_type.value == "不登录":
                    ui.notify(position="top", type="warning", message="哔哩哔哩2 在不登录的情况下，无法获取用户完整的用户名")

            if select_visual_body.value == "metahuman_stream":
                ui.notify(position="top", type="warning", message="对接metahuman_stream时，语音合成由metahuman_stream托管，不受AI Vtuber控制，请自行参考官方文档对接TTS")

            if config.get("webui", "show_card", "common_config", "local_qa"):
                if not common.is_json_convertible(textarea_local_qa_text_json_file_content.value):
                    ui.notify(position="top", type="negative", message="本地问答json数据格式不正确，请检查JSON语法！")
                    return False

            return True
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"配置错误：{e}")
            return False

    """
    
.................................................................................................................................................................
.................................................................................................................................................................
.................................................................................................................................................................
.................................................................................................................................................................
.............................................................................................................:**.................................................
........+++..........-++:....:++:...*##############:%%%%%%%%%#.....%%%%%%%%%%%%%%%%%%%%%%%.....%@#...........-@%..........+%%%%%%%%%%%%%+-----------:............
........%@#..........=@@=....-@@=....::::%#:=@+::::.........%%.....%%.....%%.....%#.....%%......+@@*..#%%%%%%%@@%%%%%%%%....=@#.....%@-.*%@#######%@=............
........%@#..........=@@=....-@@=........%*.-@+.............%%.....%@%%%%%@@%%%%%@@%%%%%@%........%%:........-@%............=@#.....%@-..#@-......#@-............
........%@#..........=@@=....-@@=....%%%%@@%%@%%%%=.........%%.....::........#%=........::...................=@%:...........=@%#####%@-..=@=.....-%%.............
........%@#..........=@@=....-@@=....%%..%*.-@+.=@=.........%%...%%%%%%%%%%%%@@%%%%%%%%%%%%*.:-----..#%%%%%%%%%%%%%%%%%@-...=@#-----%@-..:%#.....*@=.............
........%@#..........=@@=....-@@=....%%.:%*.-@+.=@=.-%@@@@@@@%...............%@=.............+##%@%.....=%%+:..=@#....#%:...=@#.....%@-...#@-....%%..............
........%@#..........=@@=....-@@=....%%.+@=.-@+.=@=.=@+.....##.......@%***************#@+.......=@%....-..:*%#.=@#....+*....=@#-----%@-...-%#...#@=..............
........%@#..........=@@=....-@@=....%%+@#...*%%%@=.=@+..............@#===============*@+.......=@%...-#@%*:...+@*..........=@%*****%@-....*@=.=%*...............
........#@%..........+@@:....-@@=....%%-*.......=@=.=@+..............@#-::::::::::::::+@+.......=@%......:**...*@+..........=@#.....%@-.....%@#%%................
........*@@=........:%@#.....-@@=....%@%%%%%%%%%%@=.=@+......-*:.....@%%%%%%%%%%%%%%%%%@+.......=@%.:%@@@@@@@@@@@@@@@@@@%...=@#.....%@++*=...%@#.................
.........*@@%-.....*%@%......-@@=....%%.........=@=.-@+......+@=.....@*...............=@+.......=@%..:........%@*.........+#%@%%%@@@@@#+-..:%@%@%................
..........:%%@@@@@@%%-.......-@@=....%%.........=@=.-@+......%@-.....@%%%%%%%%%%%%%%%%%@+.......=@%#@%:....:#@%*%@%*......+*=:......%@-...#@%..:%@*..............
.....................................%@@@@@@@@@@@@=.:%@#+==+%@%.....:@#...............=@*.......#@@#-..:+%@@%-....=#@@#-............%@--%@%-.....+%@%=...........
.....................................%%.........=%=...=*****+:..-***************************-...-+...#@%#+:..........-#%:...........%@=%#:.........+#............
.................................................................................................................................................................
.................................................................................................................................................................
.................................................................................................................................................................
.................................................................................................................................................................

    """

    # 读取webui配置到dict变量
    def webui_config_to_dict(config_data):
        """读取webui配置到dict变量

        Args:
            config_data (dict): 从本地配置文件读取的dict数据
        """

        def common_textarea_handle(content):
            """通用的textEdit 多行文本内容处理

            Args:
                content (str): 原始多行文本内容

            Returns:
                _type_: 处理好的多行文本内容
            """
            ret = [token.strip() for token in content.split("\n") if token.strip()]
            return ret

        # 类型处理函数
        def handle_int(value):
            if value.value == '' or value.value is None:
                return 0
            return int(value.value)

        def handle_float(value):
            if value.value == '' or value.value is None:
                return 0
            return round(float(value.value), 2)

        def handle_string(value):
            return str(value.value)

        def handle_bool(value):
            return bool(value.value)

        def handle_textarea(value):
            return common_textarea_handle(value.value)

        # 处理器映射
        type_handlers = {
            'int': handle_int,
            'float': handle_float,
            'str': handle_string,
            'bool': handle_bool,
            'textarea': handle_textarea,
        }

        def update_nested_dict(target, keys, value):
            """递归更新嵌套字典"""
            if len(keys) == 1:
                target[keys[0]] = value
                return
            if keys[0] not in target:
                target[keys[0]] = {}
            update_nested_dict(target[keys[0]], keys[1:], value)

        def process_config_mapping(config_data, mapping, show_card_check=None):
            """处理配置映射，支持不同层级的嵌套"""
            def recurse_mapping(current_mapping, current_path=[]):
                for key, value in current_mapping.items():
                    new_path = current_path + [key]
                    if isinstance(value, dict):
                        recurse_mapping(value, new_path)
                    else:
                        component, type_name = value
                        handler = type_handlers[type_name]
                        processed_value = handler(component)
                        update_nested_dict(config_data, new_path, processed_value)

            recurse_mapping(mapping)
            return config_data


        def update_config(config_mapping, config, config_data, type="common_config"):
            # 处理常规配置
            for section, section_mapping in config_mapping.items():
                if type is not None:
                    if config.get("webui", "show_card", type, section):
                        if section not in config_data:
                            config_data[section] = {}
                        
                        process_config_mapping(config_data[section], section_mapping)
                else:
                    if section not in config_data:
                        config_data[section] = {}
                    
                    process_config_mapping(config_data[section], section_mapping)

            return config_data



        try:
            """
            通用配置
            """
            if True:
                config_data["platform"] = select_platform.value
                config_data["room_display_id"] = input_room_display_id.value
                config_data["chat_type"] = select_chat_type.value
                config_data["visual_body"] = select_visual_body.value
                config_data["need_lang"] = select_need_lang.value
                config_data["before_prompt"] = input_before_prompt.value
                config_data["after_prompt"] = input_after_prompt.value
                config_data["audio_synthesis_type"] = select_audio_synthesis_type.value

                config_mapping = {
                    "comment_template": {
                        "enable": (switch_comment_template_enable, 'bool'),
                        "copywriting": (input_comment_template_copywriting, 'str'),
                    },
                    "reply_template": {
                        "enable": (switch_reply_template_enable, 'bool'),
                        "username_max_len": (input_reply_template_username_max_len, 'int'),
                        "copywriting": (textarea_reply_template_copywriting, 'textarea'),
                    },
                    "bilibili": {
                        "login_type": (select_bilibili_login_type, 'str'),
                        "cookie": (input_bilibili_cookie, 'str'),
                        "ac_time_value": (input_bilibili_ac_time_value, 'str'),
                        "username": (input_bilibili_username, 'str'),
                        "password": (input_bilibili_password, 'str'),
                        "open_live": {
                            "ACCESS_KEY_ID": (input_bilibili_open_live_ACCESS_KEY_ID, 'str'),
                            "ACCESS_KEY_SECRET": (input_bilibili_open_live_ACCESS_KEY_SECRET, 'str'),
                            "APP_ID": (input_bilibili_open_live_APP_ID, 'int'),
                            "ROOM_OWNER_AUTH_CODE": (input_bilibili_open_live_ROOM_OWNER_AUTH_CODE, 'str'),
                        },
                    },
                    "ordinaryroad_barrage_fly": {
                        "ws_ip_port": (input_ordinaryroad_barrage_fly_ws_ip_port, 'str'),
                        "taskIds": (textarea_ordinaryroad_barrage_fly_taskIds, 'textarea'),
                    },
                    "twitch": {
                        "token": (input_twitch_token, 'str'),
                        "user": (input_twitch_user, 'str'),
                        "proxy_server": (input_twitch_proxy_server, 'str'),
                        "proxy_port": (input_twitch_proxy_port, 'str'),
                    },
                }
                config_data = update_config(config_mapping, config, config_data, None)
                
                config_mapping = {}
                # 日志
                if config.get("webui", "show_card", "common_config", "log"):
                    config_data["comment_log_type"] = select_comment_log_type.value
                    config_data["captions"]["enable"] = switch_captions_enable.value
                    config_data["captions"]["file_path"] = input_captions_file_path.value
                    config_data["captions"]["raw_file_path"] = input_captions_raw_file_path.value

            
                # 音频播放
                if config.get("webui", "show_card", "common_config", "play_audio"):
                    # audio_player
                    config_data["audio_player"]["api_ip_port"] = input_audio_player_api_ip_port.value

                    config_mapping = {
                        "play_audio": {
                            "enable": (switch_play_audio_enable, 'bool'),
                            "text_split_enable": (switch_play_audio_text_split_enable, 'bool'),
                            "info_to_callback": (switch_play_audio_info_to_callback, 'bool'),
                            "interval_num_min": (input_play_audio_interval_num_min, 'int'),
                            "interval_num_max": (input_play_audio_interval_num_max, 'int'),
                            "normal_interval_min": (input_play_audio_normal_interval_min, 'float'),
                            "normal_interval_max": (input_play_audio_normal_interval_max, 'float'),
                            "out_path": (input_play_audio_out_path,'str'),
                            "player": (select_play_audio_player,'str'),
                        }
                    }

                if config.get("webui", "show_card", "common_config", "read_comment"):
                    config_mapping["read_comment"] = {
                        "enable": (switch_read_comment_enable, 'bool'),
                        "read_username_enable": (switch_read_comment_read_username_enable, 'bool'),
                        "username_max_len": (input_read_comment_username_max_len, 'int'),
                        "voice_change": (switch_read_comment_voice_change, 'bool'),
                        "read_username_copywriting": (textarea_read_comment_read_username_copywriting, 'textarea'),
                        "periodic_trigger": {
                            "enable": (switch_read_comment_periodic_trigger_enable, 'bool'),
                            "periodic_time_min": (input_read_comment_periodic_trigger_periodic_time_min, 'int'),
                            "periodic_time_max": (input_read_comment_periodic_trigger_periodic_time_max, 'int'),
                            "trigger_num_min": (input_read_comment_periodic_trigger_trigger_num_min, 'int'),
                            "trigger_num_max": (input_read_comment_periodic_trigger_trigger_num_max, 'int'),
                        },
                    }

                if config.get("webui", "show_card", "common_config", "local_qa"):
                    config_mapping["local_qa"] = {
                        "periodic_trigger": {
                            "enable": (switch_local_qa_periodic_trigger_enable, 'bool'),
                            "periodic_time_min": (input_local_qa_periodic_trigger_periodic_time_min, 'int'),
                            "periodic_time_max": (input_local_qa_periodic_trigger_periodic_time_max, 'int'),
                            "trigger_num_min": (input_local_qa_periodic_trigger_trigger_num_min, 'int'),
                            "trigger_num_max": (input_local_qa_periodic_trigger_trigger_num_max, 'int'),
                        },
                        "text": {
                            "enable": (switch_local_qa_text_enable, 'bool'),
                            "type": (select_local_qa_text_type, 'str'),
                            "file_path": (input_local_qa_text_file_path, 'str'),
                            "similarity": (input_local_qa_text_similarity, 'float'),
                            "username_max_len": (input_local_qa_text_username_max_len, 'int'),
                        },
                        "audio": {
                            "enable": (switch_local_qa_audio_enable, 'bool'),
                            "file_path": (input_local_qa_audio_file_path, 'str'),
                            "similarity": (input_local_qa_audio_similarity, 'float'),
                        },
                    }

                if config.get("webui", "show_card", "common_config", "filter"):
                    config_mapping["filter"] = {
                        "before_must_str": (textarea_filter_before_must_str, 'textarea'),
                        "after_must_str": (textarea_filter_after_must_str, 'textarea'),
                        "before_filter_str": (textarea_filter_before_filter_str, 'textarea'),
                        "after_filter_str": (textarea_filter_after_filter_str, 'textarea'),
                        "before_must_str_for_llm": (textarea_filter_before_must_str_for_llm, 'textarea'),
                        "after_must_str_for_llm": (textarea_filter_after_must_str_for_llm, 'textarea'),
                        "badwords": {
                            "enable": (switch_filter_badwords_enable, 'bool'),
                            "discard": (switch_filter_badwords_discard, 'bool'),
                            "path": (input_filter_badwords_path,'str'),
                            "bad_pinyin_path": (input_filter_badwords_bad_pinyin_path,'str'),
                            "replace": (input_filter_badwords_replace,'str'),
                        },
                        "username_convert_digits_to_chinese": (switch_filter_username_convert_digits_to_chinese, 'bool'),
                        "emoji": (switch_filter_emoji, 'bool'),
                        "max_len": (input_filter_max_len, 'int'),
                        "max_char_len": (input_filter_max_char_len, 'int'),
                        "comment_forget_duration": (input_filter_comment_forget_duration, 'float'),
                        "comment_forget_reserve_num": (input_filter_comment_forget_reserve_num, 'int'),
                        "gift_forget_duration": (input_filter_gift_forget_duration, 'float'),
                        "gift_forget_reserve_num": (input_filter_gift_forget_reserve_num, 'int'),
                        "entrance_forget_duration": (input_filter_entrance_forget_duration, 'float'),
                        "entrance_forget_reserve_num": (input_filter_entrance_forget_reserve_num, 'int'),
                        "follow_forget_duration": (input_filter_follow_forget_duration, 'float'),
                        "follow_forget_reserve_num": (input_filter_follow_forget_reserve_num, 'int'),
                        "talk_forget_duration": (input_filter_talk_forget_duration, 'float'),
                        "talk_forget_reserve_num": (input_filter_talk_forget_reserve_num, 'int'),
                        "schedule_forget_duration": (input_filter_schedule_forget_duration, 'float'),
                        "schedule_forget_reserve_num": (input_filter_schedule_forget_reserve_num, 'int'),
                        "idle_time_task_forget_duration": (input_filter_idle_time_task_forget_duration, 'float'),
                        "idle_time_task_forget_reserve_num": (input_filter_idle_time_task_forget_reserve_num, 'int'),
                        "image_recognition_schedule_forget_duration": (input_filter_image_recognition_schedule_forget_duration, 'float'),
                        "image_recognition_schedule_forget_reserve_num": (input_filter_image_recognition_schedule_forget_reserve_num, 'int'),
                        "limited_time_deduplication": {
                            "enable": (switch_filter_limited_time_deduplication_enable, 'bool'),
                            "comment": (input_filter_limited_time_deduplication_comment, 'int'),
                            "gift": (input_filter_limited_time_deduplication_gift, 'int'),
                            "entrance": (input_filter_limited_time_deduplication_entrance, 'int'),
                        },
                        "message_queue_max_len": (input_filter_message_queue_max_len, 'int'),
                        "voice_tmp_path_queue_max_len": (input_filter_voice_tmp_path_queue_max_len, 'int'),
                        "voice_tmp_path_queue_min_start_play": (input_filter_voice_tmp_path_queue_min_start_play, 'int'),
                        "priority_mapping": {
                            "idle_time_task": (input_filter_priority_mapping_idle_time_task, 'int'),
                            "image_recognition_schedule": (input_filter_priority_mapping_image_recognition_schedule, 'int'),
                            "local_qa_audio": (input_filter_priority_mapping_local_qa_audio, 'int'),
                            "comment": (input_filter_priority_mapping_comment, 'int'),
                            "song": (input_filter_priority_mapping_song, 'int'),
                            "read_comment": (input_filter_priority_mapping_read_comment, 'int'),
                            "entrance": (input_filter_priority_mapping_entrance, 'int'),
                            "gift": (input_filter_priority_mapping_gift, 'int'),
                            "follow": (input_filter_priority_mapping_follow, 'int'),
                            "talk": (input_filter_priority_mapping_talk, 'int'),
                            "reread": (input_filter_priority_mapping_reread, 'int'),
                            "key_mapping": (input_filter_priority_mapping_key_mapping, 'int'),
                            "integral": (input_filter_priority_mapping_integral, 'int'),
                            "reread_top_priority": (input_filter_priority_mapping_reread_top_priority, 'int'),
                            "copywriting": (input_filter_priority_mapping_copywriting, 'int'),
                            "abnormal_alarm": (input_filter_priority_mapping_abnormal_alarm, 'int'),
                            "trends_copywriting": (input_filter_priority_mapping_trends_copywriting, 'int'),
                            "schedule": (input_filter_priority_mapping_schedule, 'int'),
                            "assistant_anchor_text": (input_filter_priority_mapping_assistant_anchor_text, 'int'),
                            "assistant_anchor_audio": (input_filter_priority_mapping_assistant_anchor_audio, 'int'),
                        },
                        "blacklist": {
                            "enable": (switch_filter_blacklist_enable, 'bool'),
                            "username": (textarea_filter_blacklist_username, 'textarea'),
                        }
                    }

                if config.get("webui", "show_card", "common_config", "thanks"):
                    config_mapping["thanks"] = {
                        "username_max_len": (input_thanks_username_max_len, 'int'),
                        "entrance_enable": (switch_thanks_entrance_enable, 'bool'),
                        "entrance_random": (switch_thanks_entrance_random, 'bool'),
                        "entrance_copy": (textarea_thanks_entrance_copy, 'textarea'),
                        "entrance": {
                            "periodic_trigger": {
                                "enable": (switch_thanks_entrance_periodic_trigger_enable, 'bool'),
                                "periodic_time_min": (input_thanks_entrance_periodic_trigger_periodic_time_min, 'int'),
                                "periodic_time_max": (input_thanks_entrance_periodic_trigger_periodic_time_max, 'int'),
                                "trigger_num_min": (input_thanks_entrance_periodic_trigger_trigger_num_min, 'int'),
                                "trigger_num_max": (input_thanks_entrance_periodic_trigger_trigger_num_max, 'int'),
                            }
                        },
                        "gift_enable": (switch_thanks_gift_enable, 'bool'),
                        "gift_random": (switch_thanks_gift_random, 'bool'),
                        "gift_copy": (textarea_thanks_gift_copy, 'textarea'),
                        "gift": {
                            "periodic_trigger": {
                                "enable": (switch_thanks_gift_periodic_trigger_enable, 'bool'),
                                "periodic_time_min": (input_thanks_gift_periodic_trigger_periodic_time_min, 'int'),
                                "periodic_time_max": (input_thanks_gift_periodic_trigger_periodic_time_max, 'int'),
                                "trigger_num_min": (input_thanks_gift_periodic_trigger_trigger_num_min, 'int'),
                                "trigger_num_max": (input_thanks_gift_periodic_trigger_trigger_num_max, 'int'),
                            }
                        },
                        "follow_enable": (switch_thanks_follow_enable, 'bool'),
                        "follow_random": (switch_thanks_follow_random, 'bool'),
                        "follow_copy": (textarea_thanks_follow_copy, 'textarea'),
                        "follow": {
                            "periodic_trigger": {
                                "enable": (switch_thanks_follow_periodic_trigger_enable, 'bool'),
                                "periodic_time_min": (input_thanks_follow_periodic_trigger_periodic_time_min, 'int'),
                                "periodic_time_max": (input_thanks_follow_periodic_trigger_periodic_time_max, 'int'),
                                "trigger_num_min": (input_thanks_follow_periodic_trigger_trigger_num_min, 'int'),
                                "trigger_num_max": (input_thanks_follow_periodic_trigger_trigger_num_max, 'int'),
                            }
                        },
                        "lowest_price": (input_thanks_lowest_price, 'float')
                    }

                if config.get("webui", "show_card", "common_config", "audio_random_speed"):
                    config_mapping["audio_random_speed"] = {
                        "normal": {
                            "enable": (switch_audio_random_speed_normal_enable, 'bool'),
                            "speed_min": (input_audio_random_speed_normal_speed_min, 'float'),
                            "speed_max": (input_audio_random_speed_normal_speed_max, 'float'),
                        },
                        "copywriting": {
                            "enable": (switch_audio_random_speed_copywriting_enable, 'bool'),
                            "speed_min": (input_audio_random_speed_copywriting_speed_min, 'float'),
                            "speed_max": (input_audio_random_speed_copywriting_speed_max, 'float'),
                        },
                    }
                if config.get("webui", "show_card", "common_config", "choose_song"):
                    config_mapping["choose_song"] = {
                        "enable": (switch_choose_song_enable, 'bool'),
                        "start_cmd": (textarea_choose_song_start_cmd,'textarea'),
                        "stop_cmd": (textarea_choose_song_stop_cmd,'textarea'),
                        "random_cmd": (textarea_choose_song_random_cmd,'textarea'),
                        "song_path": (input_choose_song_song_path,'str'),
                        "match_fail_copy": (input_choose_song_match_fail_copy,'str'),
                        "similarity": (input_choose_song_similarity,'float'),
                    }
                if config.get("webui", "show_card", "common_config", "sd"):
                    config_mapping["sd"] = {
                        "enable": (switch_sd_enable, 'bool'),
                        "translate_type": (select_sd_translate_type, 'str'),
                        "prompt_llm": {
                            "type": (select_sd_prompt_llm_type, 'str'),
                            "before_prompt": (input_sd_prompt_llm_before_prompt, 'str'),
                            "after_prompt": (input_sd_prompt_llm_after_prompt, 'str'),
                        },
                        "trigger": (input_sd_trigger, 'str'),
                        "ip": (input_sd_ip, 'str'),
                        "port": (input_sd_port, 'int'),
                        "negative_prompt": (input_sd_negative_prompt, 'str'),
                        "seed": (input_sd_seed, 'float'),
                        "styles": (textarea_sd_styles, 'textarea'),
                        "cfg_scale": (input_sd_cfg_scale, 'int'),
                        "steps": (input_sd_steps, 'int'),
                        "hr_resize_x": (input_sd_hr_resize_x, 'int'),
                        "hr_resize_y": (input_sd_hr_resize_y, 'int'),
                        "enable_hr": (switch_sd_enable_hr, 'bool'),
                        "hr_scale": (input_sd_hr_scale, 'int'),
                        "hr_second_pass_steps": (input_sd_hr_second_pass_steps, 'int'),
                        "denoising_strength": (input_sd_denoising_strength, 'float'),
                        "save_enable": (switch_sd_save_enable, 'bool'),
                        "loop_cover": (switch_sd_loop_cover, 'bool'),
                        "save_path": (input_sd_save_path, 'str'),
                    }
                if config.get("webui", "show_card", "common_config", "search_online"):
                    config_mapping["search_online"] = {
                        "enable": (switch_search_online_enable, 'bool'),
                        "keyword_enable": (switch_search_online_keyword_enable, 'bool'),
                        "before_keyword": (textarea_search_online_before_keyword, 'textarea'),
                        "engine": (select_search_online_engine, 'str'),
                        "engine_id": (input_search_online_engine_id, 'int'),
                        "count": (input_search_online_count, 'int'),
                        "resp_template": (input_search_online_resp_template, 'str'),
                        "http_proxy": (input_search_online_http_proxy, 'str'),
                        "https_proxy": (input_search_online_https_proxy, 'str'),
                    }
                if config.get("webui", "show_card", "common_config", "web_captions_printer"):
                    config_mapping["web_captions_printer"] = {
                        "enable": (switch_web_captions_printer_enable, 'bool'),
                        "api_ip_port": (input_web_captions_printer_api_ip_port, 'str'),
                    }
                if config.get("webui", "show_card", "common_config", "database"):
                    config_mapping["database"] = {
                        "path": (input_database_path, 'str'),
                        "comment_enable": (switch_database_comment_enable, 'bool'),
                        "entrance_enable": (switch_database_entrance_enable, 'bool'),
                        "gift_enable": (switch_database_gift_enable, 'bool'),
                    }
                if config.get("webui", "show_card", "common_config", "abnormal_alarm"):
                    config_mapping["abnormal_alarm"] = {
                        "platform": {
                            "enable": (switch_abnormal_alarm_platform_enable, 'bool'),
                            "type": (select_abnormal_alarm_platform_type, 'str'),
                            "start_alarm_error_num": (input_abnormal_alarm_platform_start_alarm_error_num, 'int'),
                            "auto_restart_error_num": (input_abnormal_alarm_platform_auto_restart_error_num, 'int'),
                            "local_audio_path": (input_abnormal_alarm_platform_local_audio_path, 'str'),
                        },
                        "llm": {
                            "enable": (switch_abnormal_alarm_llm_enable, 'bool'),
                            "type": (select_abnormal_alarm_llm_type, 'str'),
                            "start_alarm_error_num": (input_abnormal_alarm_llm_start_alarm_error_num, 'int'),
                            "auto_restart_error_num": (input_abnormal_alarm_llm_auto_restart_error_num, 'int'),
                            "local_audio_path": (input_abnormal_alarm_llm_local_audio_path, 'str'),
                        },
                        "tts": {
                            "enable": (switch_abnormal_alarm_tts_enable, 'bool'),
                            "type": (select_abnormal_alarm_tts_type, 'str'),
                            "start_alarm_error_num": (input_abnormal_alarm_tts_start_alarm_error_num, 'int'),
                            "auto_restart_error_num": (input_abnormal_alarm_tts_auto_restart_error_num, 'int'),
                            "local_audio_path": (input_abnormal_alarm_tts_local_audio_path, 'str'),
                        },
                        "svc": {
                            "enable": (switch_abnormal_alarm_svc_enable, 'bool'),
                            "type": (select_abnormal_alarm_svc_type, 'str'),
                            "start_alarm_error_num": (input_abnormal_alarm_svc_start_alarm_error_num, 'int'),
                            "auto_restart_error_num": (input_abnormal_alarm_svc_auto_restart_error_num, 'int'),
                            "local_audio_path": (input_abnormal_alarm_svc_local_audio_path, 'str'),
                        },
                        "visual_body": {
                            "enable": (switch_abnormal_alarm_visual_body_enable, 'bool'),
                            "type": (select_abnormal_alarm_visual_body_type, 'str'),
                            "start_alarm_error_num": (input_abnormal_alarm_visual_body_start_alarm_error_num, 'int'),
                            "auto_restart_error_num": (input_abnormal_alarm_visual_body_auto_restart_error_num, 'int'),
                            "local_audio_path": (input_abnormal_alarm_visual_body_local_audio_path, 'str'),
                        },
                        "other": {
                            "enable": (switch_abnormal_alarm_other_enable, 'bool'),
                            "type": (select_abnormal_alarm_other_type, 'str'),
                            "start_alarm_error_num": (input_abnormal_alarm_other_start_alarm_error_num, 'int'),
                            "auto_restart_error_num": (input_abnormal_alarm_other_auto_restart_error_num, 'int'),
                            "local_audio_path": (input_abnormal_alarm_other_local_audio_path, 'str'),
                        },
                    }

                config_data = update_config(config_mapping, config, config_data, "common_config")
                
                # 定时任务
                if config.get("webui", "show_card", "common_config", "schedule"):
                    tmp_arr = []
                    # logger.info(schedule_var)
                    for index in range(len(schedule_var) // 4):
                        tmp_json = {
                            "enable": False,
                            "time_min": 60,
                            "time_max": 120,
                            "copy": []
                        }
                        tmp_json["enable"] = schedule_var[str(4 * index)].value
                        tmp_json["time_min"] = round(float(schedule_var[str(4 * index + 1)].value), 1)
                        tmp_json["time_max"] = round(float(schedule_var[str(4 * index + 2)].value), 1)
                        tmp_json["copy"] = common_textarea_handle(schedule_var[str(4 * index + 3)].value)

                        tmp_arr.append(tmp_json)
                    # logger.info(tmp_arr)
                    config_data["schedule"] = tmp_arr

                # 闲时任务
                if config.get("webui", "show_card", "common_config", "idle_time_task"):
                    config_data["idle_time_task"]["enable"] = switch_idle_time_task_enable.value
                    config_data["idle_time_task"]["type"] = select_idle_time_task_type.value

                    config_data["idle_time_task"]["min_msg_queue_len_to_trigger"] = int(input_idle_time_task_idle_min_msg_queue_len_to_trigger.value)
                    config_data["idle_time_task"]["min_audio_queue_len_to_trigger"] = int(input_idle_time_task_idle_min_audio_queue_len_to_trigger.value)

                    config_data["idle_time_task"]["idle_time_min"] = int(input_idle_time_task_idle_time_min.value)
                    config_data["idle_time_task"]["idle_time_max"] = int(input_idle_time_task_idle_time_max.value)
                    config_data["idle_time_task"]["wait_play_audio_num_threshold"] = int(input_idle_time_task_wait_play_audio_num_threshold.value)
                    config_data["idle_time_task"]["idle_time_reduce_to"] = int(input_idle_time_task_idle_time_reduce_to.value)

                    tmp_arr = []
                    for index in range(len(idle_time_task_trigger_type_var)):
                        if idle_time_task_trigger_type_var[str(index)].value:
                            tmp_arr.append(common.find_keys_by_value(idle_time_task_trigger_type_mapping, idle_time_task_trigger_type_var[str(index)].text)[0])
                    # logger.info(tmp_arr)
                    config_data["idle_time_task"]["trigger_type"] = tmp_arr

                    config_data["idle_time_task"]["comment"]["enable"] = switch_idle_time_task_comment_enable.value
                    config_data["idle_time_task"]["comment"]["random"] = switch_idle_time_task_comment_random.value
                    config_data["idle_time_task"]["copywriting"]["copy"] = common_textarea_handle(textarea_idle_time_task_copywriting_copy.value)
                    config_data["idle_time_task"]["copywriting"]["enable"] = switch_idle_time_task_copywriting_enable.value
                    config_data["idle_time_task"]["copywriting"]["random"] = switch_idle_time_task_copywriting_random.value
                    config_data["idle_time_task"]["comment"]["copy"] = common_textarea_handle(textarea_idle_time_task_comment_copy.value)
                    config_data["idle_time_task"]["local_audio"]["enable"] = switch_idle_time_task_local_audio_enable.value
                    config_data["idle_time_task"]["local_audio"]["random"] = switch_idle_time_task_local_audio_random.value
                    config_data["idle_time_task"]["local_audio"]["path"] = common_textarea_handle(textarea_idle_time_task_local_audio_path.value)


                # 动态文案
                if config.get("webui", "show_card", "common_config", "trends_copywriting"):
                    config_data["trends_copywriting"]["enable"] = switch_trends_copywriting_enable.value
                    config_data["trends_copywriting"]["llm_type"] = select_trends_copywriting_llm_type.value
                    config_data["trends_copywriting"]["random_play"] = switch_trends_copywriting_random_play.value
                    config_data["trends_copywriting"]["play_interval"] = int(input_trends_copywriting_play_interval.value)
                    tmp_arr = []
                    for index in range(len(trends_copywriting_copywriting_var) // 3):
                        tmp_json = {
                            "folder_path": "",
                            "prompt_change_enable": False,
                            "prompt_change_content": ""
                        }
                        tmp_json["folder_path"] = trends_copywriting_copywriting_var[str(3 * index)].value
                        tmp_json["prompt_change_enable"] = trends_copywriting_copywriting_var[str(3 * index + 1)].value
                        tmp_json["prompt_change_content"] = trends_copywriting_copywriting_var[str(3 * index + 2)].value

                        tmp_arr.append(tmp_json)
                    # logger.info(tmp_arr)
                    config_data["trends_copywriting"]["copywriting"] = tmp_arr

                
                # 按键映射
                if config.get("webui", "show_card", "common_config", "key_mapping"):
                    config_data["key_mapping"]["enable"] = switch_key_mapping_enable.value
                    config_data["key_mapping"]["type"] = select_key_mapping_type.value
                    config_data["key_mapping"]["key_trigger_type"] = select_key_mapping_key_trigger_type.value
                    config_data["key_mapping"]["key_single_sentence_trigger_once"] = switch_key_mapping_key_single_sentence_trigger_once_enable.value
                    config_data["key_mapping"]["copywriting_trigger_type"] = select_key_mapping_copywriting_trigger_type.value
                    config_data["key_mapping"]["copywriting_single_sentence_trigger_once"] = switch_key_mapping_copywriting_single_sentence_trigger_once_enable.value
                    config_data["key_mapping"]["local_audio_trigger_type"] = select_key_mapping_local_audio_trigger_type.value
                    config_data["key_mapping"]["local_audio_single_sentence_trigger_once"] = switch_key_mapping_local_audio_single_sentence_trigger_once_enable.value
                    config_data["key_mapping"]["serial_trigger_type"] = select_key_mapping_serial_trigger_type.value
                    config_data["key_mapping"]["serial_single_sentence_trigger_once"] = switch_key_mapping_serial_single_sentence_trigger_once_enable.value
                    config_data["key_mapping"]["img_path_trigger_type"] = select_key_mapping_img_path_trigger_type.value
                    config_data["key_mapping"]["img_path_single_sentence_trigger_once"] = switch_key_mapping_img_path_single_sentence_trigger_once_enable.value
                    

                    config_data["key_mapping"]["start_cmd"] = input_key_mapping_start_cmd.value
                    tmp_arr = []
                    # logger.info(key_mapping_config_var)

                    num = 9

                    for index in range(len(key_mapping_config_var) // num):
                        tmp_json = {
                            "keywords": [],
                            "gift": [],
                            "keys": [],
                            "similarity": 0.8,
                            "copywriting": [],
                            "serial_name": "",
                            "serial_send_data": [],
                            "img_path": []
                        }
                        tmp_json["keywords"] = common_textarea_handle(key_mapping_config_var[str(num * index)].value)
                        tmp_json["gift"] = common_textarea_handle(key_mapping_config_var[str(num * index + 1)].value)
                        tmp_json["keys"] = common_textarea_handle(key_mapping_config_var[str(num * index + 2)].value)
                        tmp_json["similarity"] = key_mapping_config_var[str(num * index + 3)].value
                        tmp_json["copywriting"] = common_textarea_handle(key_mapping_config_var[str(num * index + 4)].value)
                        tmp_json["local_audio"] = common_textarea_handle(key_mapping_config_var[str(num * index + 5)].value)
                        tmp_json["serial_name"] = key_mapping_config_var[str(num * index + 6)].value
                        tmp_json["serial_send_data"] = common_textarea_handle(key_mapping_config_var[str(num * index + 7)].value)
                        tmp_json["img_path"] = common_textarea_handle(key_mapping_config_var[str(num * index + 8)].value)

                        tmp_arr.append(tmp_json)
                    # logger.info(tmp_arr)
                    config_data["key_mapping"]["config"] = tmp_arr

                # 自定义命令
                if config.get("webui", "show_card", "common_config", "custom_cmd"):
                    config_data["custom_cmd"]["enable"] = switch_custom_cmd_enable.value
                    config_data["custom_cmd"]["type"] = select_custom_cmd_type.value
                    tmp_arr = []
                    # logger.info(custom_cmd_config_var)
                    for index in range(len(custom_cmd_config_var) // 7):
                        tmp_json = {
                            "keywords": [],
                            "similarity": 1,
                            "api_url": "",
                            "api_type": "",
                            "resp_data_type": "",
                            "data_analysis": "",
                            "resp_template": ""
                        }
                        tmp_json["keywords"] = common_textarea_handle(custom_cmd_config_var[str(7 * index)].value)
                        tmp_json["similarity"] = float(custom_cmd_config_var[str(7 * index + 1)].value)
                        tmp_json["api_url"] = custom_cmd_config_var[str(7 * index + 2)].value
                        tmp_json["api_type"] = custom_cmd_config_var[str(7 * index + 3)].value
                        tmp_json["resp_data_type"] = custom_cmd_config_var[str(7 * index + 4)].value
                        tmp_json["data_analysis"] = custom_cmd_config_var[str(7 * index + 5)].value
                        tmp_json["resp_template"] = custom_cmd_config_var[str(7 * index + 6)].value

                        tmp_arr.append(tmp_json)
                    # logger.info(tmp_arr)
                    config_data["custom_cmd"]["config"] = tmp_arr

                # 动态配置
                if config.get("webui", "show_card", "common_config", "trends_config"):
                    config_data["trends_config"]["enable"] = switch_trends_config_enable.value
                    tmp_arr = []
                    # logger.info(trends_config_path_var)
                    for index in range(len(trends_config_path_var) // 2):
                        tmp_json = {
                            "online_num": "0-999999999",
                            "path": "config.json"
                        }
                        tmp_json["online_num"] = trends_config_path_var[str(2 * index)].value
                        tmp_json["path"] = trends_config_path_var[str(2 * index + 1)].value

                        tmp_arr.append(tmp_json)
                    # logger.info(tmp_arr)
                    config_data["trends_config"]["path"] = tmp_arr

                
                # 联动程序
                if config.get("webui", "show_card", "common_config", "coordination_program"):
                    tmp_arr = []
                    for index in range(len(coordination_program_var) // 4):
                        tmp_json = {
                            "enable": True,
                            "name": "",
                            "executable": "",
                            "parameters": []
                        }
                        tmp_json["enable"] = coordination_program_var[str(4 * index)].value
                        tmp_json["name"] = coordination_program_var[str(4 * index + 1)].value
                        tmp_json["executable"] = coordination_program_var[str(4 * index + 2)].value
                        tmp_json["parameters"] = common_textarea_handle(coordination_program_var[str(4 * index + 3)].value)

                        tmp_arr.append(tmp_json)
                    # logger.info(tmp_arr)
                    config_data["coordination_program"] = tmp_arr
                
                tmp_arr = []
                for index in range(len(luoxi_project_Live_Comment_Assistant_type_var)):
                    if luoxi_project_Live_Comment_Assistant_type_var[str(index)].value:
                        tmp_arr.append(
                            common.find_keys_by_value(
                                luoxi_project_Live_Comment_Assistant_type_mapping, 
                                luoxi_project_Live_Comment_Assistant_type_var[str(index)].text
                            )[0]
                        )
                # logger.info(tmp_arr)
                config_data["luoxi_project"]["Live_Comment_Assistant"]["type"] = tmp_arr

                tmp_arr = []
                for index in range(len(luoxi_project_Live_Comment_Assistant_trigger_position_var)):
                    if luoxi_project_Live_Comment_Assistant_trigger_position_var[str(index)].value:
                        tmp_arr.append(
                            common.find_keys_by_value(
                                luoxi_project_Live_Comment_Assistant_trigger_position_mapping, 
                                luoxi_project_Live_Comment_Assistant_trigger_position_var[str(index)].text
                            )[0]
                        )
                # logger.info(tmp_arr)
                config_data["luoxi_project"]["Live_Comment_Assistant"]["trigger_position"] = tmp_arr

                config_mapping = {
                    "luoxi_project": {
                        "Live_Comment_Assistant": {
                            "enable": (switch_luoxi_project_Live_Comment_Assistant_enable, 'bool'),
                            "version": (select_luoxi_project_Live_Comment_Assistant_version, 'str'),
                            "api_ip_port": (input_luoxi_project_Live_Comment_Assistant_api_ip_port, 'str'),
                        }
                    }
                }

                config_data = update_config(config_mapping, config, config_data, None)

            """
            LLM
            """
            if True:
                config_mapping = {}
                if config.get("webui", "show_card", "llm", "custom_llm"):
                    config_mapping["custom_llm"] = {
                        "url": (textarea_custom_llm_url, 'str'),
                        "method": (textarea_custom_llm_method, 'str'),
                        "headers": (textarea_custom_llm_headers, 'str'),
                        "proxies": (textarea_custom_llm_proxies, 'str'),
                        "body_type": (select_custom_llm_body_type, 'str'),
                        "body": (textarea_custom_llm_body, 'str'),
                        "resp_data_type": (select_custom_llm_resp_data_type, 'str'),
                        "data_analysis": (textarea_custom_llm_data_analysis, 'str'),
                        "resp_template": (textarea_custom_llm_resp_template, 'str'),
                    }

                config_data = update_config(config_mapping, config, config_data, "llm")

            """
            TTS
            """
            if True:
                config_mapping = {}
                if config.get("webui", "show_card", "tts", "gpt_sovits"):
                    config_mapping["gpt_sovits"] = {
                        "type": (select_gpt_sovits_type, 'str'),
                        "gradio_ip_port": (input_gpt_sovits_gradio_ip_port, 'str'),
                        "api_ip_port": (input_gpt_sovits_api_ip_port, 'str'),
                        "ref_audio_path": (input_gpt_sovits_ref_audio_path, 'str'),
                        "prompt_text": (input_gpt_sovits_prompt_text, 'str'),
                        "prompt_language": (select_gpt_sovits_prompt_language, 'str'),
                        "language": (select_gpt_sovits_language, 'str'),
                        "cut": (select_gpt_sovits_cut, 'str'),
                        "gpt_model_path": (input_gpt_sovits_gpt_model_path, 'str'),
                        "sovits_model_path": (input_gpt_sovits_sovits_model_path, 'str'),
                        "api_0322": {
                            "ref_audio_path": (input_gpt_sovits_api_0322_ref_audio_path, 'str'),
                            "prompt_text": (input_gpt_sovits_api_0322_prompt_text, 'str'),
                            "prompt_lang": (select_gpt_sovits_api_0322_prompt_lang, 'str'),
                            "text_lang": (select_gpt_sovits_api_0322_text_lang, 'str'),
                            "text_split_method": (select_gpt_sovits_api_0322_text_split_method, 'str'),
                            "top_k": (input_gpt_sovits_api_0322_top_k, 'int'),
                            "top_p": (input_gpt_sovits_api_0322_top_p, 'float'),
                            "temperature": (input_gpt_sovits_api_0322_temperature, 'float'),
                            "batch_size": (input_gpt_sovits_api_0322_batch_size, 'int'),
                            "speed_factor": (input_gpt_sovits_api_0322_speed_factor, 'float'),
                            "fragment_interval": (input_gpt_sovits_api_0322_fragment_interval, 'str'),
                            "split_bucket": (switch_gpt_sovits_api_0322_split_bucket, 'bool'),
                            "return_fragment": (switch_gpt_sovits_api_0322_return_fragment, 'bool'),
                        },
                        "api_0706": {
                            "refer_wav_path": (input_gpt_sovits_api_0706_refer_wav_path, 'str'),
                            "prompt_text": (input_gpt_sovits_api_0706_prompt_text, 'str'),
                            "prompt_language": (select_gpt_sovits_api_0706_prompt_language, 'str'),
                            "text_language": (select_gpt_sovits_api_0706_text_language, 'str'),
                            "cut_punc": (input_gpt_sovits_api_0706_cut_punc, 'str'),
                        },
                        "v2_api_0821": {
                            "ref_audio_path": (input_gpt_sovits_v2_api_0821_ref_audio_path, 'str'),
                            "prompt_text": (input_gpt_sovits_v2_api_0821_prompt_text, 'str'),
                            "prompt_lang": (select_gpt_sovits_v2_api_0821_prompt_lang, 'str'),
                            "text_lang": (select_gpt_sovits_v2_api_0821_text_lang, 'str'),
                            "text_split_method": (select_gpt_sovits_v2_api_0821_text_split_method, 'str'),
                            "top_k": (input_gpt_sovits_v2_api_0821_top_k, 'int'),
                            "top_p": (input_gpt_sovits_v2_api_0821_top_p, 'float'),
                            "temperature": (input_gpt_sovits_v2_api_0821_temperature, 'float'),
                            "batch_size": (input_gpt_sovits_v2_api_0821_batch_size, 'int'),
                            "batch_threshold": (input_gpt_sovits_v2_api_0821_batch_threshold, 'float'),
                            "split_bucket": (switch_gpt_sovits_v2_api_0821_split_bucket, 'bool'),
                            "speed_factor": (input_gpt_sovits_v2_api_0821_speed_factor, 'float'),
                            "fragment_interval": (input_gpt_sovits_v2_api_0821_fragment_interval, 'float'),
                            "seed": (input_gpt_sovits_v2_api_0821_seed, 'int'),
                            "media_type": (input_gpt_sovits_v2_api_0821_media_type, 'str'),
                            "parallel_infer": (switch_gpt_sovits_v2_api_0821_parallel_infer, 'bool'),
                            "repetition_penalty": (input_gpt_sovits_v2_api_0821_repetition_penalty, 'float'),
                        },
                        "webtts": {
                            "version": (select_gpt_sovits_webtts_version, 'str'),
                            "api_ip_port": (input_gpt_sovits_webtts_api_ip_port, 'str'),
                            "spk": (input_gpt_sovits_webtts_spk, 'str'),
                            "lang": (select_gpt_sovits_webtts_lang, 'str'),
                            "speed": (input_gpt_sovits_webtts_speed, 'str'),
                            "emotion": (input_gpt_sovits_webtts_emotion, 'str'),
                        }
                    }

                config_data = update_config(config_mapping, config, config_data, "tts")

            """
            SVC
            """
            if True:
                config_mapping = {}
                if config.get("webui", "show_card", "svc", "so_vits_svc"):  
                    config_mapping["so_vits_svc"] = {
                        "enable": (switch_so_vits_svc_enable, 'bool'),
                        "config_path": (input_so_vits_svc_config_path, 'str'),
                        "api_ip_port": (input_so_vits_svc_api_ip_port, 'str'),
                        "spk": (input_so_vits_svc_spk, 'str'),
                        "tran": (input_so_vits_svc_tran, 'float'),
                        "wav_format": (input_so_vits_svc_wav_format, 'str'),
                    }

                config_data = update_config(config_mapping, config, config_data, "svc")
                  
            """
            虚拟身体
            """
            if True:
                config_mapping = {}
                if config.get("webui", "show_card", "visual_body", "metahuman_stream"):  
                    config_mapping["metahuman_stream"] = {
                        "type": (select_metahuman_stream_type, 'str'),
                        "api_ip_port": (input_metahuman_stream_api_ip_port, 'str'),
                    }

                config_data = update_config(config_mapping, config, config_data, None)

            """
            文案
            """
            if True:
                config_data["copywriting"]["auto_play"] = switch_copywriting_auto_play.value
                config_data["copywriting"]["random_play"] = switch_copywriting_random_play.value
                config_data["copywriting"]["audio_interval"] = input_copywriting_audio_interval.value
                config_data["copywriting"]["switching_interval"] = input_copywriting_switching_interval.value
                config_data["copywriting"]["text_path"] = input_copywriting_text_path.value
                config_data["copywriting"]["audio_save_path"] = input_copywriting_audio_save_path.value
                config_data["copywriting"]["audio_synthesis_type"] = select_copywriting_audio_synthesis_type.value
                
                tmp_arr = []
                # logger.info(copywriting_config_var)
                for index in range(len(copywriting_config_var) // 5):
                    tmp_json = {
                        "file_path": "",
                        "audio_path": "",
                        "continuous_play_num": 1,
                        "max_play_time": 10.0,
                        "play_list": []
                    }
                    tmp_json["file_path"] = copywriting_config_var[str(5 * index)].value
                    tmp_json["audio_path"] = copywriting_config_var[str(5 * index + 1)].value
                    tmp_json["continuous_play_num"] = int(copywriting_config_var[str(5 * index + 2)].value)
                    tmp_json["max_play_time"] = float(copywriting_config_var[str(5 * index + 3)].value)
                    tmp_json["play_list"] = common_textarea_handle(copywriting_config_var[str(5 * index + 4)].value)
                    

                    tmp_arr.append(tmp_json)
                # logger.info(tmp_arr)
                config_data["copywriting"]["config"] = tmp_arr

            """
            积分
            """
            if True:
                config_data["integral"]["enable"] = switch_integral_enable.value

                config_data["integral"]["sign"]["enable"] = switch_integral_sign_enable.value
                config_data["integral"]["sign"]["get_integral"] = int(input_integral_sign_get_integral.value)
                config_data["integral"]["sign"]["cmd"] = common_textarea_handle(textarea_integral_sign_cmd.value)
                tmp_arr = []
                # logger.info(integral_sign_copywriting_var)
                for index in range(len(integral_sign_copywriting_var) // 2):
                    tmp_json = {
                        "sign_num_interval": "",
                        "copywriting": []
                    }
                    tmp_json["sign_num_interval"] = integral_sign_copywriting_var[str(2 * index)].value
                    tmp_json["copywriting"] = common_textarea_handle(integral_sign_copywriting_var[str(2 * index + 1)].value)

                    tmp_arr.append(tmp_json)
                # logger.info(tmp_arr)
                config_data["integral"]["sign"]["copywriting"] = tmp_arr

                config_data["integral"]["gift"]["enable"] = switch_integral_gift_enable.value
                config_data["integral"]["gift"]["get_integral_proportion"] = float(input_integral_gift_get_integral_proportion.value)
                tmp_arr = []
                for index in range(len(integral_gift_copywriting_var) // 2):
                    tmp_json = {
                        "gift_price_interval": "",
                        "copywriting": []
                    }
                    tmp_json["gift_price_interval"] = integral_gift_copywriting_var[str(2 * index)].value
                    tmp_json["copywriting"] = common_textarea_handle(integral_gift_copywriting_var[str(2 * index + 1)].value)

                    tmp_arr.append(tmp_json)
                # logger.info(tmp_arr)
                config_data["integral"]["gift"]["copywriting"] = tmp_arr

                config_data["integral"]["entrance"]["enable"] = switch_integral_entrance_enable.value
                config_data["integral"]["entrance"]["get_integral"] = int(input_integral_entrance_get_integral.value)
                tmp_arr = []
                for index in range(len(integral_entrance_copywriting_var) // 2):
                    tmp_json = {
                        "entrance_num_interval": "",
                        "copywriting": []
                    }
                    tmp_json["entrance_num_interval"] = integral_entrance_copywriting_var[str(2 * index)].value
                    tmp_json["copywriting"] = common_textarea_handle(integral_entrance_copywriting_var[str(2 * index + 1)].value)

                    tmp_arr.append(tmp_json)
                # logger.info(tmp_arr)
                config_data["integral"]["entrance"]["copywriting"] = tmp_arr

                config_data["integral"]["crud"]["query"]["enable"] = switch_integral_crud_query_enable.value
                config_data["integral"]["crud"]["query"]["cmd"] = common_textarea_handle(textarea_integral_crud_query_cmd.value)
                config_data["integral"]["crud"]["query"]["copywriting"] = common_textarea_handle(textarea_integral_crud_query_copywriting.value)

            """
            聊天
            """
            if True:
                tmp_arr = []
                for index in range(len(talk_interrupt_clean_type_var)):
                    if talk_interrupt_clean_type_var[str(index)].value:
                        tmp_arr.append(
                            common.find_keys_by_value(
                                talk_interrupt_clean_type_mapping, 
                                talk_interrupt_clean_type_var[str(index)].text
                            )[0]
                        )
                # logger.info(tmp_arr)
                config_data["talk"]["interrupt_talk"]["clean_type"] = tmp_arr

                config_mapping = {
                    "talk": {
                        "key_listener_enable": (switch_talk_key_listener_enable, 'bool'),
                        "direct_run_talk": (switch_talk_direct_run_talk, 'bool'),
                        "device_index": (select_talk_device_index, 'str'),
                        "no_recording_during_playback": (switch_talk_no_recording_during_playback, 'bool'),
                        "no_recording_during_playback_sleep_interval": (input_talk_no_recording_during_playback_sleep_interval, 'float'),
                        "username": (input_talk_username, 'str'),
                        "continuous_talk": (switch_talk_continuous_talk, 'bool'),
                        "trigger_key": (select_talk_trigger_key, 'str'),
                        "stop_trigger_key": (select_talk_stop_trigger_key, 'str'),
                        "volume_threshold": (input_talk_volume_threshold, 'float'),
                        "silence_threshold": (input_talk_silence_threshold, 'float'),
                        "CHANNELS": (input_talk_silence_CHANNELS, 'int'),
                        "RATE": (input_talk_silence_RATE, 'int'),
                        "show_chat_log": (switch_talk_show_chat_log, 'bool'),
                        "interrupt_talk": {
                            "enable": (switch_talk_interrupt_talk_enable, 'bool'),
                            "keywords": (textarea_talk_interrupt_talk_keywords, 'textarea'),
                        },
                        "wakeup_sleep": {
                            "enable": (switch_talk_wakeup_sleep_enable, 'bool'),
                            "mode": (select_talk_wakeup_sleep_mode, 'str'),
                            "wakeup_word": (textarea_talk_wakeup_sleep_wakeup_word, 'textarea'),
                            "sleep_word": (textarea_talk_wakeup_sleep_sleep_word, 'textarea'),
                            "wakeup_copywriting": (textarea_talk_wakeup_sleep_wakeup_copywriting, 'textarea'),
                            "sleep_copywriting": (textarea_talk_wakeup_sleep_sleep_copywriting, 'textarea'),
                        },
                        "type": (select_talk_type, 'str'),
                        "google": {
                            "tgt_lang": (select_talk_google_tgt_lang, 'str'),
                        },
                        "baidu": {
                            "app_id": (input_talk_baidu_app_id, 'str'),
                            "api_key": (input_talk_baidu_api_key, 'str'),
                            "secret_key": (input_talk_baidu_secret_key, 'str'),
                        },
                        "faster_whisper": {
                            "model_size": (input_faster_whisper_model_size, 'str'),
                            "language": (select_faster_whisper_language, 'str'),
                            "device": (select_faster_whisper_device, 'str'),
                            "compute_type": (select_faster_whisper_compute_type, 'str'),
                            "download_root": (input_faster_whisper_download_root, 'str'),
                            "beam_size": (input_faster_whisper_beam_size, 'int'),
                        },
                        "sensevoice": {
                            "asr_model_path": (input_sensevoice_asr_model_path, 'str'),
                            "vad_model_path": (input_sensevoice_vad_model_path, 'str'),
                            "vad_max_single_segment_time": (input_sensevoice_vad_max_single_segment_time, 'int'),
                            "device": (input_sensevoice_vad_device, 'str'),
                            "language": (select_sensevoice_language, 'str'),
                            "text_norm": (input_sensevoice_text_norm, 'str'),
                            "batch_size_s": (input_sensevoice_batch_size_s, 'int'),
                            "batch_size": (input_sensevoice_batch_size, 'int'),
                        },
                    }
                }

                config_data = update_config(config_mapping, config, config_data, None)

            """
            图像识别
            """
            if True:
                config_mapping = {
                    "image_recognition": {
                        "enable": (button_image_recognition_enable, 'bool'),
                        "model": (select_image_recognition_model, 'str'),
                        "img_save_path": (input_image_recognition_img_save_path, 'str'),
                        "prompt": (input_image_recognition_prompt, 'str'),
                        "screenshot_window_title": (select_image_recognition_screenshot_window_title, 'str'),
                        "screenshot_delay": (input_image_recognition_screenshot_delay, 'float'),
                        "loop_screenshot_enable": (switch_image_recognition_loop_screenshot_enable, 'bool'),
                        "loop_screenshot_delay": (input_image_recognition_loop_screenshot_delay, 'int'),
                        "cam_screenshot_enable": (switch_image_recognition_cam_screenshot_enable, 'bool'),
                        "cam_index": (select_image_recognition_cam_index, 'int'), 
                        "cam_screenshot_delay": (input_image_recognition_cam_screenshot_delay, 'float'),
                        "loop_cam_screenshot_enable": (switch_image_recognition_loop_cam_screenshot_enable, 'bool'),
                        "loop_cam_screenshot_delay": (input_image_recognition_loop_cam_screenshot_delay, 'int'),
                        "blip": {
                            "model": (select_image_recognition_blip_model, 'str'),
                        },
                    }
                }


                config_data = update_config(config_mapping, config, config_data, None)

            """
            助播
            """
            if True:
                
                tmp_arr = []
                for index in range(len(assistant_anchor_type_var)):
                    if assistant_anchor_type_var[str(index)].value:
                        tmp_arr.append(common.find_keys_by_value(assistant_anchor_type_mapping, assistant_anchor_type_var[str(index)].text)[0])
                # logger.info(tmp_arr)
                config_data["assistant_anchor"]["type"] = tmp_arr

                config_mapping = {
                    "assistant_anchor": {
                        "enable": (switch_assistant_anchor_enable, 'bool'),
                        "username": (input_assistant_anchor_username, 'str'),
                        "audio_synthesis_type": (select_assistant_anchor_audio_synthesis_type, 'str'),
                        "local_qa": {
                            "text": {
                                "enable": (switch_assistant_anchor_local_qa_text_enable, 'bool'),
                                "format": (select_assistant_anchor_local_qa_text_format, 'str'),
                                "file_path": (input_assistant_anchor_local_qa_text_file_path, 'str'),
                                "similarity": (input_assistant_anchor_local_qa_text_similarity, 'float')
                            },
                            "audio": {
                                "enable": (switch_assistant_anchor_local_qa_audio_enable, 'bool'),
                                "type": (select_assistant_anchor_local_qa_audio_type, 'str'),
                                "file_path": (input_assistant_anchor_local_qa_audio_file_path, 'str'),
                                "similarity": (input_assistant_anchor_local_qa_audio_similarity, 'float')
                            }
                        }
                    }
                }

                config_data = update_config(config_mapping, config, config_data, None)

            """
            翻译
            """
            if True:
                config_mapping = {
                    "translate": {
                        "enable": (switch_translate_enable, 'bool'),
                        "type": (select_translate_type, 'str'),
                        "trans_type": (select_translate_trans_type, 'str'),
                        "baidu": {
                            "appid": (input_translate_baidu_appid, 'str'),
                            "appkey": (input_translate_baidu_appkey, 'str'),
                            "from_lang": (select_translate_baidu_from_lang, 'str'),
                            "to_lang": (select_translate_baidu_to_lang, 'str'),
                        },
                        "google": {
                            "proxy": (input_translate_google_proxy, 'str'),
                            "src_lang": (select_translate_google_src_lang, 'str'),
                            "tgt_lang": (select_translate_google_tgt_lang, 'str'),
                        },
                    }
                }

                config_data = update_config(config_mapping, config, config_data, None)

            """
            串口
            """
            if True:
                tmp_arr = []
                for index in range(len(serial_config_var) // 8):
                    tmp_json = {
                        "serial_name": "COM1",
                        "baudrate": "115200",
                        "serial_data_type": "ASCII"
                    }
                    tmp_json["serial_name"] = serial_config_var[str(8 * index)].value
                    tmp_json["baudrate"] = serial_config_var[str(8 * index + 1)].value
                    tmp_json["serial_data_type"] = serial_config_var[str(8 * index + 5)].value

                    tmp_arr.append(tmp_json)
                # logger.info(tmp_arr)
                config_data["serial"]["config"] = tmp_arr

            """
            数据分析
            """
            if True:
                config_mapping = {
                    "data_analysis": {
                        "comment_word_cloud": {
                            "top_num": (input_data_analysis_comment_word_cloud_top_num, 'int'),
                        },
                        "integral": {
                            "top_num": (input_data_analysis_integral_top_num, 'int'),
                        },
                        "gift": {
                            "top_num": (input_data_analysis_gift_top_num, 'int'),
                        },
                    }
                }
                config_data = update_config(config_mapping, config, config_data, None)

            """
            UI配置
            """
            if True:
                config_mapping = {
                    "webui": {
                        "title": (input_webui_title, 'str'),
                        "ip": (input_webui_ip, 'str'),
                        "port": (input_webui_port, 'int'),
                        "auto_run": (switch_webui_auto_run, 'bool'),
                        "local_dir_to_endpoint": {
                            "enable": (switch_webui_local_dir_to_endpoint_enable, 'bool'),
                        },
                        "show_card": {
                            "common_config": {
                                "read_comment": (switch_webui_show_card_common_config_read_comment, 'bool'),
                                "filter": (switch_webui_show_card_common_config_filter, 'bool'),
                                "thanks": (switch_webui_show_card_common_config_thanks, 'bool'),
                                "local_qa": (switch_webui_show_card_common_config_local_qa, 'bool'),
                                "choose_song": (switch_webui_show_card_common_config_choose_song, 'bool'),
                                "sd": (switch_webui_show_card_common_config_sd, 'bool'),
                                "log": (switch_webui_show_card_common_config_log, 'bool'),
                                "schedule": (switch_webui_show_card_common_config_schedule, 'bool'),
                                "idle_time_task": (switch_webui_show_card_common_config_idle_time_task, 'bool'),
                                "trends_copywriting": (switch_webui_show_card_common_config_trends_copywriting, 'bool'),
                                "database": (switch_webui_show_card_common_config_database, 'bool'),
                                "play_audio": (switch_webui_show_card_common_config_play_audio, 'bool'),
                                "web_captions_printer": (switch_webui_show_card_common_config_web_captions_printer, 'bool'),
                                "key_mapping": (switch_webui_show_card_common_config_key_mapping, 'bool'),
                                "custom_cmd": (switch_webui_show_card_common_config_custom_cmd, 'bool'),
                                "trends_config": (switch_webui_show_card_common_config_trends_config, 'bool'),
                                "abnormal_alarm": (switch_webui_show_card_common_config_abnormal_alarm, 'bool'),
                                "coordination_program": (switch_webui_show_card_common_config_coordination_program, 'bool'),
                            },
                            "llm": {
                                "custom_llm": (switch_webui_show_card_llm_custom_llm, 'bool'),
                            },
                            "tts": {
                                "gpt_sovits": (switch_webui_show_card_tts_gpt_sovits, 'bool'),
                            },
                            "svc": {
                                "so_vits_svc": (switch_webui_show_card_svc_so_vits_svc, 'bool'),
                            },
                            "visual_body": {
                                "metahuman_stream": (switch_webui_show_card_visual_body_metahuman_stream, 'bool'),
                            },
                        },
                        "theme": {
                            "choose": (select_webui_theme_choose, 'str'),
                        },
                    },
                    "login": {
                        "enable": (switch_login_enable, 'bool'),
                        "username": (input_login_username, 'str'),
                        "password": (input_login_password, 'str'),
                    }
                }

                config_data = update_config(config_mapping, config, config_data, None)

                tmp_arr = []
                for index in range(len(webui_local_dir_to_endpoint_config_var) // 2):
                    tmp_json = {
                        "url_path": "",
                        "local_dir": ""
                    }
                    tmp_json["url_path"] = webui_local_dir_to_endpoint_config_var[str(2 * index)].value
                    tmp_json["local_dir"] = webui_local_dir_to_endpoint_config_var[str(2 * index + 1)].value

                    tmp_arr.append(tmp_json)
                # logger.info(tmp_arr)
                config_data["webui"]["local_dir_to_endpoint"]["config"] = tmp_arr

                
            return config_data
        except Exception as e:
            logger.error(f"无法读取webui配置到变量！\n{e}")
            ui.notify(position="top", type="negative", message=f"无法读取webui配置到变量！\n{e}")
            logger.error(traceback.format_exc())

            return None

    # 保存配置
    def save_config():
        """
        保存配置到本地配置文件中
        """
        global config, config_path

        # 配置检查
        if not check_config():
            return False

        try:
            with open(config_path, 'r', encoding="utf-8") as config_file:
                config_data = json.load(config_file)
        except Exception as e:
            logger.error(f"无法读取配置文件！\n{e}")
            ui.notify(position="top", type="negative", message=f"无法读取配置文件！{e}")
            return False

        # 读取webui配置到dict变量
        config_data = webui_config_to_dict(config_data)
        if config_data is None:
            return False

        # 写入本地问答json数据到文件
        if config.get("webui", "show_card", "common_config", "local_qa"):
            try:
                ret = common.write_content_to_file(input_local_qa_text_json_file_path.value, textarea_local_qa_text_json_file_content.value, write_log=False)
                if not ret:
                    ui.notify(position="top", type="negative", message="无法写入本地问答json数据到文件！\n详细报错见日志")
                    return False
            except Exception as e:
                logger.error(f"无法写入本地问答json数据到文件！\n{str(e)}")
                ui.notify(position="top", type="negative", message=f"无法写入本地问答json数据到文件！\n{str(e)}")
                return False   

        # 写入配置到配置文件
        try:
            with open(config_path, 'w', encoding="utf-8") as config_file:
                json.dump(config_data, config_file, indent=2, ensure_ascii=False)
                config_file.flush()  # 刷新缓冲区，确保写入立即生效

            logger.info("配置数据已成功写入文件！")
            ui.notify(position="top", type="positive", message="配置数据已成功写入文件！")

            return True
        except Exception as e:
            logger.error(f"无法写入配置文件！\n{str(e)}")
            ui.notify(position="top", type="negative", message=f"无法写入配置文件！\n{str(e)}")
            return False
        


    """

    ..............................................................................................................
    ..............................................................................................................
    ..........................,]].................................................................................
    .........................O@@@@^...............................................................................
    .....=@@@@@`.....O@@@....,\@@[.....................................,@@@@@@@@@@]....O@@@^......=@@@@....O@@@^..
    .....=@@@@@@.....O@@@............................................=@@@@/`..,[@@/....O@@@^......=@@@@....O@@@^..
    .....=@@@@@@@....O@@@....,]]]].......]@@@@@]`.....,/@@@@\`....../@@@@..............O@@@^......=@@@@....O@@@^..
    .....=@@@/@@@\...O@@@....=@@@@....,@@@@@@@@@@^..,@@@@@@@@@@\...=@@@@...............O@@@^......=@@@@....O@@@^..
    .....=@@@^,@@@\..O@@@....=@@@@...,@@@@`........=@@@/....=@@@\..=@@@@....]]]]]]]]...O@@@^......=@@@@....O@@@^..
    .....=@@@^.=@@@^.O@@@....=@@@@...O@@@^.........@@@@......@@@@..=@@@@....=@@@@@@@...O@@@^......=@@@@....O@@@^..
    .....=@@@^..\@@@^=@@@....=@@@@...@@@@^........,@@@@@@@@@@@@@@..=@@@@.......=@@@@...O@@@^......=@@@@....O@@@^..
    .....=@@@^...\@@@/@@@....=@@@@...O@@@^.........@@@@`...........,@@@@`......=@@@@...O@@@^......=@@@@....O@@@^..
    .....=@@@^....@@@@@@@....=@@@@...,@@@@`........=@@@@......,.....=@@@@`.....=@@@@...=@@@@`.....@@@@^....O@@@^..
    .....=@@@^....,@@@@@@....=@@@@....,@@@@@@@@@@`..=@@@@@@@@@@@`....,@@@@@@@@@@@@@@....,@@@@@@@@@@@@`.....O@@@^..
    .....,[[[`.....,[[[[[....,[[[[.......[@@@@@[`.....,[@@@@@[`.........,\@@@@@@[`.........[@@@@@@[........[[[[`..
    ..............................................................................................................
    ..............................................................................................................

    """

    # 语音合成所有配置项
    audio_synthesis_type_options = {
        'gpt_sovits': 'GPT_SoVITS',
    }

    # 聊天类型所有配置项
    chat_type_options = {
        'custom_llm': '自定义LLM',
    }

    platform_options = {
        'talk': '聊天模式', 
        'bilibili': '哔哩哔哩', 
        'bilibili2': '哔哩哔哩2', 
        'dy': '抖音', 
        'dy2': '抖音2', 
        'ks': '快手',
        'ks2': '快手2',
        'pdd': '拼多多',
        'wxlive': '微信视频号',
        'taobao': '淘宝',
        '1688': '1688',
        'douyu': '斗鱼',
        'ordinaryroad_barrage_fly': '让弹幕飞',
        'youtube': 'YouTube', 
        'twitch': 'twitch', 
        'tiktok': 'tiktok',
    }

    visual_body_options = {
        'metahuman_stream': 'metahuman_stream',
    }

    with ui.tabs().classes('w-full') as tabs:
        common_config_page = ui.tab('通用配置')
        llm_page = ui.tab('大语言模型')
        tts_page = ui.tab('文本转语音')
        svc_page = ui.tab('变声')
        visual_body_page = ui.tab('虚拟身体')
        copywriting_page = ui.tab('文案')
        talk_page = ui.tab('聊天')
        image_recognition_page = ui.tab('图像识别')
        integral_page = ui.tab('积分')
        assistant_anchor_page = ui.tab('助播')
        translate_page = ui.tab('翻译')
        serial_page = ui.tab('串口')
        data_analysis_page = ui.tab('数据分析')
        web_page = ui.tab('页面配置')

    with ui.tab_panels(tabs, value=common_config_page).classes('w-full'):
        with ui.tab_panel(common_config_page).style(tab_panel_css):
            with ui.row():
                
                select_platform = ui.select(
                    label='平台', 
                    options=platform_options, 
                    value=config.get("platform")
                ).style("width:200px;")

                input_room_display_id = ui.input(label='直播间号', placeholder='一般为直播间URL最后/后面的字母或数字', value=config.get("room_display_id")).style("width:200px;").tooltip('一般为直播间URL最后/后面的字母或数字')

                select_chat_type = ui.select(
                    label='大语言模型', 
                    options=chat_type_options, 
                    value=config.get("chat_type")
                ).style("width:200px;").tooltip('选用的LLM类型。相关的弹幕信息等会传递给此LLM进行推理，获取回答')

                select_visual_body = ui.select(
                    label='虚拟身体', 
                    options=visual_body_options, 
                    value=config.get("visual_body")
                ).style("width:200px;").tooltip('选用的虚拟身体类型。如果使用VTS对接，就选其他，用什么展示身体就选什么，大部分对接的选项需要单独启动对应的服务端程序，请勿随便选择。')

                select_audio_synthesis_type = ui.select(
                    label='语音合成', 
                    options=audio_synthesis_type_options, 
                    value=config.get("audio_synthesis_type")
                ).style("width:200px;").tooltip('选用的TTS类型，所有的文本内容最终都将通过此TTS进行语音合成')

            with ui.row():
                select_need_lang = ui.select(
                    label='回复语言', 
                    options={'none': '所有', 'zh': '中文', 'en': '英文', 'jp': '日文'}, 
                    value=config.get("need_lang")
                ).style("width:200px;").tooltip('限制回复的语言，如：选中中文，则只会回复中文提问，其他语言将被跳过')

                input_before_prompt = ui.input(label='提示词前缀', placeholder='此配置会追加在弹幕前，再发送给LLM处理', value=config.get("before_prompt")).style("width:200px;").tooltip('此配置会追加在弹幕前，再发送给LLM处理')
                input_after_prompt = ui.input(label='提示词后缀', placeholder='此配置会追加在弹幕后，再发送给LLM处理', value=config.get("after_prompt")).style("width:200px;").tooltip('此配置会追加在弹幕后，再发送给LLM处理')
                switch_comment_template_enable = ui.switch('启用弹幕模板', value=config.get("comment_template", "enable")).style(switch_internal_css).tooltip('此配置会追加在弹幕后，再发送给LLM处理')
                input_comment_template_copywriting = ui.input(label='弹幕模板', value=config.get("comment_template", "copywriting"), placeholder='此配置会对弹幕内容进行修改，{}内为变量，会被替换为指定内容，请勿随意删除变量').style("width:200px;").tooltip('此配置会对弹幕内容进行修改，{}内为变量，会被替换为指定内容，请勿随意删除变量')
                switch_reply_template_enable = ui.switch('启用回复模板', value=config.get("reply_template", "enable")).style(switch_internal_css).tooltip('此配置会在LLM输出的答案中进行回复内容的重新构建')
                input_reply_template_username_max_len = ui.input(label='回复用户名的最大长度', value=config.get("reply_template", "username_max_len"), placeholder='回复用户名的最大长度').style("width:200px;").tooltip('回复用户名的最大长度')
                textarea_reply_template_copywriting = ui.textarea(
                    label='回复模板', 
                    placeholder='此配置会对LLM回复内容进行修改，{}内为变量，会被替换为指定内容，请勿随意删除变量', 
                    value=textarea_data_change(config.get("reply_template", "copywriting"))
                ).style("width:500px;").tooltip('此配置会对LLM回复内容进行修改，{}内为变量，会被替换为指定内容，请勿随意删除变量')

            with ui.expansion('功能管理', icon="settings", value=True).classes('w-full'):

                with ui.card().style(card_css):
                    ui.label('平台相关')
                    with ui.card().style(card_css):
                        ui.label('哔哩哔哩')
                        with ui.row():
                            select_bilibili_login_type = ui.select(
                                label='登录方式',
                                options={'cookie': 'cookie', '手机扫码': '手机扫码', '手机扫码-终端': '手机扫码-终端', '账号密码登录': '账号密码登录', 'open_live': '开放平台', '不登录': '不登录'},
                                value=config.get("bilibili", "login_type")
                            ).style("width:100px")
                            input_bilibili_cookie = ui.input(label='cookie', placeholder='b站登录后F12抓网络包获取cookie，强烈建议使用小号！有封号风险，虽然实际上没听说有人被封过', value=config.get("bilibili", "cookie")).style("width:500px;").tooltip('b站登录后F12抓网络包获取cookie，强烈建议使用小号！有封号风险，虽然实际上没听说有人被封过')
                            input_bilibili_ac_time_value = ui.input(label='ac_time_value', placeholder='b站登录后，F12控制台，输入window.localStorage.ac_time_value获取(如果没有，请重新登录)', value=config.get("bilibili", "ac_time_value")).style("width:500px;").tooltip('仅在平台：哔哩哔哩，情况下可选填写。b站登录后，F12控制台，输入window.localStorage.ac_time_value获取(如果没有，请重新登录)')
                        with ui.row():
                            input_bilibili_username = ui.input(label='账号', value=config.get("bilibili", "username"), placeholder='b站账号（建议使用小号）').style("width:300px;").tooltip('仅在平台：哔哩哔哩，登录方式：账号密码登录，情况下填写。b站账号（建议使用小号）')
                            input_bilibili_password = ui.input(label='密码', value=config.get("bilibili", "password"), placeholder='b站密码（建议使用小号）').style("width:300px;").tooltip('仅在平台：哔哩哔哩，登录方式：账号密码登录，情况下填写。b站密码（建议使用小号）')
                        with ui.row():
                            with ui.card().style(card_css):
                                ui.label('开放平台')
                                with ui.row():
                                    input_bilibili_open_live_ACCESS_KEY_ID = ui.input(label='ACCESS_KEY_ID', value=config.get("bilibili", "open_live", "ACCESS_KEY_ID"), placeholder='开放平台ACCESS_KEY_ID').style("width:160px;").tooltip('仅在平台：哔哩哔哩2，登录方式：开放平台，情况下填写。开放平台ACCESS_KEY_ID')
                                    input_bilibili_open_live_ACCESS_KEY_SECRET = ui.input(label='ACCESS_KEY_SECRET', value=config.get("bilibili", "open_live", "ACCESS_KEY_SECRET"), placeholder='开放平台ACCESS_KEY_SECRET').style("width:200px;").tooltip('仅在平台：哔哩哔哩2，登录方式：开放平台，情况下填写。开放平台ACCESS_KEY_SECRET')
                                    input_bilibili_open_live_APP_ID = ui.input(label='项目ID', value=config.get("bilibili", "open_live", "APP_ID"), placeholder='开放平台 创作者服务中心 项目ID').style("width:100px;").tooltip('仅在平台：哔哩哔哩2，登录方式：开放平台，情况下填写。开放平台 创作者服务中心 项目ID')
                                    input_bilibili_open_live_ROOM_OWNER_AUTH_CODE = ui.input(label='身份码', value=config.get("bilibili", "open_live", "ROOM_OWNER_AUTH_CODE"), placeholder='直播中心用户 身份码').style("width:100px;").tooltip('仅在平台：哔哩哔哩2，登录方式：开放平台，情况下填写。直播中心用户 身份码')
                    with ui.card().style(card_css):
                        ui.label('让弹幕飞')
                        with ui.row():
                            input_ordinaryroad_barrage_fly_ws_ip_port = ui.input(label='WebSocket地址', value=config.get("ordinaryroad_barrage_fly", "ws_ip_port"), placeholder='默认：ws://127.0.0.1:9898').style("width:300px;").tooltip('根据实际服务配置，填写WebSocket地址')
                            textarea_ordinaryroad_barrage_fly_taskIds = ui.textarea(
                                label='任务ID', 
                                placeholder='成功监听的任务在页面中可以复制对应的任务ID，可以自定义编辑多个（换行分隔）', 
                                value=textarea_data_change(config.get("ordinaryroad_barrage_fly", "taskIds"))
                            ).style("width:500px;").tooltip('成功监听的任务在页面中可以复制对应的任务ID，可以自定义编辑多个（换行分隔）')
                    with ui.card().style(card_css):
                        ui.label('twitch')
                        with ui.row():
                            input_twitch_token = ui.input(label='token', value=config.get("twitch", "token"), placeholder='访问 https://twitchapps.com/tmi/ 获取，格式为：oauth:xxx').style("width:300px;")
                            input_twitch_user = ui.input(label='用户名', value=config.get("twitch", "user"), placeholder='你的twitch账号用户名').style("width:300px;")
                            input_twitch_proxy_server = ui.input(label='HTTP代理IP地址', value=config.get("twitch", "proxy_server"), placeholder='代理软件，http协议监听的ip地址，一般为：127.0.0.1').style("width:200px;")
                            input_twitch_proxy_port = ui.input(label='HTTP代理端口', value=config.get("twitch", "proxy_port"), placeholder='代理软件，http协议监听的端口，一般为：1080').style("width:200px;")
                            
                if config.get("webui", "show_card", "common_config", "play_audio"):
                    with ui.card().style(card_css):
                        ui.label('音频播放')
                        with ui.row():
                            switch_play_audio_enable = ui.switch('启用', value=config.get("play_audio", "enable")).style(switch_internal_css)
                            switch_play_audio_text_split_enable = ui.switch('启用文本切分', value=config.get("play_audio", "text_split_enable")).style(switch_internal_css).tooltip('启用后会将LLM等待合成音频的消息根据内部切分算法切分成多个短句，以便TTS快速合成')
                            switch_play_audio_info_to_callback = ui.switch('音频信息回传给内部接口', value=config.get("play_audio", "info_to_callback")).style(switch_internal_css).tooltip('启用后，会在当前音频播放完毕后，将程序中等待播放的音频信息传递给内部接口，用于闲时任务的闲时清零功能。\n不过这个功能会一定程度的拖慢程序运行，如果你不需要闲时清零，可以关闭此功能来提高响应速度')
                            
                        with ui.row():
                            input_play_audio_interval_num_min = ui.input(label='间隔时间重复次数最小值', value=config.get("play_audio", "interval_num_min"), placeholder='普通音频播放间隔时间，重复睡眠次数最小值。会在最大最小值之间随机生成一个重复次数，就是 次数 x 时间 = 最终间隔时间').tooltip('普通音频播放间隔时间重复睡眠次数最小值。会在最大最小值之间随机生成一个重复次数，就是 次数 x 时间 = 最终间隔时间')
                            input_play_audio_interval_num_max = ui.input(label='间隔时间重复次数最大值', value=config.get("play_audio", "interval_num_max"), placeholder='普通音频播放间隔时间，重复睡眠次数最大值。会在最大最小值之间随机生成一个重复次数，就是 次数 x 时间 = 最终间隔时间').tooltip('普通音频播放间隔时间重复睡眠次数最大值。会在最大最小值之间随机生成一个重复次数，就是 次数 x 时间 = 最终间隔时间')
                            input_play_audio_normal_interval_min = ui.input(label='普通音频播放间隔最小值', value=config.get("play_audio", "normal_interval_min"), placeholder='就是弹幕回复、唱歌等音频播放结束后到播放下一个音频之间的一个间隔时间，单位：秒').tooltip('就是弹幕回复、唱歌等音频播放结束后到播放下一个音频之间的一个间隔时间，单位：秒。次数 x 时间 = 最终间隔时间')
                            input_play_audio_normal_interval_max = ui.input(label='普通音频播放间隔最大值', value=config.get("play_audio", "normal_interval_max"), placeholder='就是弹幕回复、唱歌等音频播放结束后到播放下一个音频之间的一个间隔时间，单位：秒').tooltip('就是弹幕回复、唱歌等音频播放结束后到播放下一个音频之间的一个间隔时间，单位：秒。次数 x 时间 = 最终间隔时间')
                            
                            input_play_audio_out_path = ui.input(label='音频输出路径', placeholder='音频文件合成后存储的路径，支持相对路径或绝对路径', value=config.get("play_audio", "out_path")).tooltip('音频文件合成后存储的路径，支持相对路径或绝对路径')
                            select_play_audio_player = ui.select(
                                label='音频播放器',
                                options={'pygame': 'pygame', 'audio_player_v2': 'audio_player_v2', 'audio_player': 'audio_player'},
                                value=config.get("play_audio", "player")
                            ).style("width:200px").tooltip('选用的音频播放器，默认pygame不需要再安装其他程序。audio player需要单独安装对接，详情看视频教程')
                    
                        with ui.card().style(card_css):
                            ui.label('audio_player')
                            with ui.row():
                                input_audio_player_api_ip_port = ui.input(
                                    label='API地址', 
                                    value=config.get("audio_player", "api_ip_port"), 
                                    placeholder='audio_player的API地址，只需要 http://ip:端口 即可',
                                    validation={
                                        '请输入正确格式的URL': lambda value: common.is_url_check(value),
                                    }
                                ).style("width:200px;").tooltip('仅在 音频播放器：audio_player等，情况下填写。audio_player的API地址，只需要 http://ip:端口 即可')

                        with ui.card().style(card_css):
                            ui.label('音频随机变速')     
                            with ui.grid(columns=3):
                                switch_audio_random_speed_normal_enable = ui.switch('普通音频变速', value=config.get("audio_random_speed", "normal", "enable")).style(switch_internal_css).tooltip('是否启用 针对 普通音频的音频变速功能。此功能需要安装配置ffmpeg才能使用')
                                input_audio_random_speed_normal_speed_min = ui.input(label='速度下限', value=config.get("audio_random_speed", "normal", "speed_min")).style("width:200px;").tooltip('音频变速的下限，最终速度会在上下限之间随机一个值进行变速')
                                input_audio_random_speed_normal_speed_max = ui.input(label='速度上限', value=config.get("audio_random_speed", "normal", "speed_max")).style("width:200px;").tooltip('音频变速的上限，最终速度会在上下限之间随机一个值进行变速')
                            with ui.grid(columns=3):
                                switch_audio_random_speed_copywriting_enable = ui.switch('文案音频变速', value=config.get("audio_random_speed", "copywriting", "enable")).style(switch_internal_css).tooltip('是否启用 针对 文案页音频的音频变速功能。此功能需要安装配置ffmpeg才能使用')
                                input_audio_random_speed_copywriting_speed_min = ui.input(label='速度下限', value=config.get("audio_random_speed", "copywriting", "speed_min")).style("width:200px;").tooltip('音频变速的下限，最终速度会在上下限之间随机一个值进行变速')
                                input_audio_random_speed_copywriting_speed_max = ui.input(label='速度上限', value=config.get("audio_random_speed", "copywriting", "speed_max")).style("width:200px;").tooltip('音频变速的上限，最终速度会在上下限之间随机一个值进行变速')

                if config.get("webui", "show_card", "common_config", "filter"):
                    with ui.card().style(card_css):
                        ui.label('过滤')    
                        with ui.grid(columns=6):
                            textarea_filter_before_must_str = ui.textarea(label='弹幕触发前缀', placeholder='前缀必须携带其中任一字符串才能触发\n例如：配置#，那么这个会触发：#你好', value=textarea_data_change(config.get("filter", "before_must_str"))).style("width:200px;").tooltip("前缀必须携带其中任一字符串才能触发\n例如：配置#，那么这个会触发：#你好")
                            textarea_filter_after_must_str = ui.textarea(label='弹幕触发后缀', placeholder='后缀必须携带其中任一字符串才能触发\n例如：配置。那么这个会触发：你好。', value=textarea_data_change(config.get("filter", "before_must_str"))).style("width:200px;").tooltip("后缀必须携带其中任一字符串才能触发\n例如：配置。那么这个会触发：你好。")
                            textarea_filter_before_filter_str = ui.textarea(label='弹幕过滤前缀', placeholder='当前缀为其中任一字符串时，弹幕会被过滤\n例如：配置#，那么这个会被过滤：#你好', value=textarea_data_change(config.get("filter", "before_filter_str"))).style("width:200px;").tooltip("当前缀为其中任一字符串时，弹幕会被过滤\n例如：配置#，那么这个会被过滤：#你好")
                            textarea_filter_after_filter_str = ui.textarea(label='弹幕过滤后缀', placeholder='当后缀为其中任一字符串时，弹幕会被过滤\n例如：配置#，那么这个会被过滤：你好#', value=textarea_data_change(config.get("filter", "before_filter_str"))).style("width:200px;").tooltip("当后缀为其中任一字符串时，弹幕会被过滤\n例如：配置#，那么这个会被过滤：你好#")
                            textarea_filter_before_must_str_for_llm = ui.textarea(label='LLM触发前缀', placeholder='前缀必须携带其中任一字符串才能触发LLM\n例如：配置#，那么这个会触发：#你好', value=textarea_data_change(config.get("filter", "before_must_str_for_llm"))).style("width:200px;").tooltip("前缀必须携带其中任一字符串才能触发LLM\n例如：配置#，那么这个会触发：#你好")
                            textarea_filter_after_must_str_for_llm = ui.textarea(label='LLM触发后缀', placeholder='后缀必须携带其中任一字符串才能触发LLM\n例如：配置。那么这个会触发：你好。', value=textarea_data_change(config.get("filter", "before_must_str_for_llm"))).style("width:200px;").tooltip('后缀必须携带其中任一字符串才能触发LLM\n例如：配置。那么这个会触发：你好。')
                            
                        with ui.row():
                            input_filter_max_len = ui.input(label='最大单词数', placeholder='最长阅读的英文单词数（空格分隔）', value=config.get("filter", "max_len")).style("width:150px;").tooltip('最长阅读的英文单词数（空格分隔）')
                            input_filter_max_char_len = ui.input(label='最大字符数', placeholder='最长阅读的字符数，双重过滤，避免溢出', value=config.get("filter", "max_char_len")).style("width:150px;").tooltip('最长阅读的字符数，双重过滤，避免溢出')
                            switch_filter_username_convert_digits_to_chinese = ui.switch('用户名中的数字转中文', value=config.get("filter", "username_convert_digits_to_chinese")).style(switch_internal_css).tooltip('用户名中的数字转中文')
                            switch_filter_emoji = ui.switch('弹幕表情过滤', value=config.get("filter", "emoji")).style(switch_internal_css)
                        with ui.grid(columns=5):
                            switch_filter_badwords_enable = ui.switch('违禁词过滤', value=config.get("filter", "badwords", "enable")).style(switch_internal_css)
                            switch_filter_badwords_discard = ui.switch('违禁语句丢弃', value=config.get("filter", "badwords", "discard")).style(switch_internal_css)
                            input_filter_badwords_path = ui.input(label='违禁词路径', value=config.get("filter", "badwords", "path"), placeholder='本地违禁词数据路径（你如果不需要，可以清空文件内容）').style("width:200px;").tooltip('本地违禁词数据路径（你如果不需要，可以清空文件内容）')
                            input_filter_badwords_bad_pinyin_path = ui.input(label='违禁拼音路径', value=config.get("filter", "badwords", "bad_pinyin_path"), placeholder='本地违禁拼音数据路径（你如果不需要，可以清空文件内容）').style("width:200px;").tooltip('本地违禁拼音数据路径（你如果不需要，可以清空文件内容）')
                            input_filter_badwords_replace = ui.input(label='违禁词替换', value=config.get("filter", "badwords", "replace"), placeholder='在不丢弃违禁语句的前提下，将违禁词替换成此项的文本').style("width:200px;").tooltip('在不丢弃违禁语句的前提下，将违禁词替换成此项的文本')
                        
                        with ui.expansion('消息遗忘&保留设置', icon="settings", value=True).classes('w-full'):
                            with ui.element('div').classes('p-2 bg-blue-100'):
                                ui.label("遗忘间隔 指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，但会保留最新的n个数据；保留数 指的是保留最新收到的数据的数量")
                            with ui.grid(columns=4):
                                input_filter_comment_forget_duration = ui.input(
                                    label='弹幕遗忘间隔', 
                                    placeholder='例：1', 
                                    value=config.get("filter", "comment_forget_duration")
                                ).style("width:200px;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义')
                                input_filter_comment_forget_reserve_num = ui.input(label='弹幕保留数', placeholder='保留最新收到的数据的数量', value=config.get("filter", "comment_forget_reserve_num")).style("width:200px;").tooltip('保留最新收到的数据的数量')
                                input_filter_gift_forget_duration = ui.input(label='礼物遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=config.get("filter", "gift_forget_duration")).style("width:200px;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义')
                                input_filter_gift_forget_reserve_num = ui.input(label='礼物保留数', placeholder='保留最新收到的数据的数量', value=config.get("filter", "gift_forget_reserve_num")).style("width:200px;").tooltip('保留最新收到的数据的数量')
                            with ui.grid(columns=4):
                                input_filter_entrance_forget_duration = ui.input(label='入场遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=config.get("filter", "entrance_forget_duration")).style("width:200px;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义')
                                input_filter_entrance_forget_reserve_num = ui.input(label='入场保留数', placeholder='保留最新收到的数据的数量', value=config.get("filter", "entrance_forget_reserve_num")).style("width:200px;").tooltip('保留最新收到的数据的数量')
                                input_filter_follow_forget_duration = ui.input(label='关注遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=config.get("filter", "follow_forget_duration")).style("width:200px;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义')
                                input_filter_follow_forget_reserve_num = ui.input(label='关注保留数', placeholder='保留最新收到的数据的数量', value=config.get("filter", "follow_forget_reserve_num")).style("width:200px;").tooltip('保留最新收到的数据的数量')
                            with ui.grid(columns=4):
                                input_filter_talk_forget_duration = ui.input(label='聊天遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=config.get("filter", "talk_forget_duration")).style("width:200px;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义')
                                input_filter_talk_forget_reserve_num = ui.input(label='聊天保留数', placeholder='保留最新收到的数据的数量', value=config.get("filter", "talk_forget_reserve_num")).style("width:200px;").tooltip('保留最新收到的数据的数量')
                                input_filter_schedule_forget_duration = ui.input(label='定时遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=config.get("filter", "schedule_forget_duration")).style("width:200px;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义')
                                input_filter_schedule_forget_reserve_num = ui.input(label='定时保留数', placeholder='保留最新收到的数据的数量', value=config.get("filter", "schedule_forget_reserve_num")).style("width:200px;").tooltip('保留最新收到的数据的数量')
                            with ui.grid(columns=4):
                                input_filter_idle_time_task_forget_duration = ui.input(label='闲时任务遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=config.get("filter", "idle_time_task_forget_duration")).style("width:200px;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义')
                                input_filter_idle_time_task_forget_reserve_num = ui.input(label='闲时任务保留数', placeholder='保留最新收到的数据的数量', value=config.get("filter", "idle_time_task_forget_reserve_num")).style("width:200px;").tooltip('保留最新收到的数据的数量')
                                input_filter_image_recognition_schedule_forget_duration = ui.input(label='图像识别遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=config.get("filter", "image_recognition_schedule_forget_duration")).style("width:200px;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义')
                                input_filter_image_recognition_schedule_forget_reserve_num = ui.input(label='图像识别保留数', placeholder='保留最新收到的数据的数量', value=config.get("filter", "image_recognition_schedule_forget_reserve_num")).style("width:200px;").tooltip('保留最新收到的数据的数量')
                        with ui.expansion('限定时间段内数据重复丢弃', icon="settings", value=True).classes('w-full'):
                            with ui.row():
                                switch_filter_limited_time_deduplication_enable = ui.switch('启用', value=config.get("filter", "limited_time_deduplication", "enable")).style(switch_internal_css)
                                input_filter_limited_time_deduplication_comment = ui.input(label='弹幕检测周期', value=config.get("filter", "limited_time_deduplication", "comment"), placeholder='在这个周期时间（秒）内，重复的数据将被丢弃').style("width:200px;").tooltip('在这个周期时间（秒）内，重复的数据将被丢弃')
                                input_filter_limited_time_deduplication_gift = ui.input(label='礼物检测周期', value=config.get("filter", "limited_time_deduplication", "gift"), placeholder='在这个周期时间（秒）内，重复的数据将被丢弃').style("width:200px;").tooltip('在这个周期时间（秒）内，重复的数据将被丢弃')
                                input_filter_limited_time_deduplication_entrance = ui.input(label='入场检测周期', value=config.get("filter", "limited_time_deduplication", "entrance"), placeholder='在这个周期时间（秒）内，重复的数据将被丢弃').style("width:200px;").tooltip('在这个周期时间（秒）内，重复的数据将被丢弃')
                                    
                        with ui.expansion('待合成音频的消息&待播放音频队列', icon="settings", value=True).classes('w-full'):
                            with ui.row():
                                input_filter_message_queue_max_len = ui.input(label='消息队列最大保留长度', placeholder='收到的消息，生成的文本内容，会根据优先级存入消息队列，当新消息的优先级低于队列中所有的消息且超过此长度时，此消息将被丢弃', value=config.get("filter", "message_queue_max_len")).style("width:160px;").tooltip('收到的消息，生成的文本内容，会根据优先级存入消息队列，当新消息的优先级低于队列中所有的消息且超过此长度时，此消息将被丢弃')
                                input_filter_voice_tmp_path_queue_max_len = ui.input(label='音频播放队列最大保留长度', placeholder='合成后的音频，会根据优先级存入待播放音频队列，当新音频的优先级低于队列中所有的音频且超过此长度时，此音频将被丢弃', value=config.get("filter", "voice_tmp_path_queue_max_len")).style("width:200px;").tooltip('合成后的音频，会根据优先级存入待播放音频队列，当新音频的优先级低于队列中所有的音频且超过此长度时，此音频将被丢弃')

                                input_filter_voice_tmp_path_queue_min_start_play = ui.input(
                                    label='音频播放队列首次触发播放阈值', 
                                    placeholder='正整数 例如：20，如果你不想开播前缓冲一定数量的音频，请配置0', 
                                    value=config.get("filter", "voice_tmp_path_queue_min_start_play")
                                ).style("width:200px;").tooltip('此功能用于缓存一定数量的音频后再开始播放。如果你不想开播前缓冲一定数量的音频，请配置0；如果你想提前准备一些音频，如因为TTS合成慢的原因，可以配置此值，让TTS提前合成你的其他任务触发的内容')

                                with ui.element('div').classes('p-2 bg-blue-100'):
                                    ui.label("下方优先级配置，请使用正整数。数字越大，优先级越高，就会优先合成音频播放")
                                    ui.label("另外需要注意，由于shi山原因，目前这个队列内容是文本切分后计算的长度，所以如果回复内容过长，可能会有丢数据的情况")
                            with ui.grid(columns=5):
                                input_filter_priority_mapping_idle_time_task = ui.input(label='闲时任务 优先级', value=config.get("filter", "priority_mapping", "idle_time_task"), placeholder='数字越大，优先级越高，但这个并非文本，所以暂时没啥用，预留').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_image_recognition_schedule = ui.input(label='图像识别 优先级', value=config.get("filter", "priority_mapping", "image_recognition_schedule"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_local_qa_audio = ui.input(label='本地问答-音频 优先级', value=config.get("filter", "priority_mapping", "local_qa_audio"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_comment = ui.input(label='弹幕回复 优先级', value=config.get("filter", "priority_mapping", "comment"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_copywriting = ui.input(label='文案 优先级', value=config.get("filter", "priority_mapping", "copywriting"), placeholder='数字越大，优先级越高，文案页的文案，但这个并非文本，所以暂时没啥用，预留').style("width:200px;").tooltip('数字越大，优先级越高')
                                
                            with ui.grid(columns=5):
                                input_filter_priority_mapping_song = ui.input(label='点歌 优先级', value=config.get("filter", "priority_mapping", "song"), placeholder='数字越大，优先级越高，但这个并非文本，所以暂时没啥用，预留').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_read_comment = ui.input(label='念弹幕 优先级', value=config.get("filter", "priority_mapping", "read_comment"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_entrance = ui.input(label='入场欢迎 优先级', value=config.get("filter", "priority_mapping", "entrance"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_gift = ui.input(label='礼物答谢 优先级', value=config.get("filter", "priority_mapping", "gift"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_follow = ui.input(label='关注答谢 优先级', value=config.get("filter", "priority_mapping", "follow"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                            with ui.grid(columns=5):
                                input_filter_priority_mapping_talk = ui.input(label='聊天（语音输入） 优先级', value=config.get("filter", "priority_mapping", "talk"), placeholder='数字越大，优先级越高，但这个并非文本，所以暂时没啥用，预留').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_reread = ui.input(label='复读 优先级', value=config.get("filter", "priority_mapping", "reread"), placeholder='数字越大，优先级越高，但这个并非文本，所以暂时没啥用，预留').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_key_mapping = ui.input(label='按键映射 优先级', value=config.get("filter", "priority_mapping", "key_mapping"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_integral = ui.input(label='积分 优先级', value=config.get("filter", "priority_mapping", "integral"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_reread_top_priority = ui.input(label='最高优先级复读 优先级', value=config.get("filter", "priority_mapping", "reread_top_priority"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                
                            with ui.grid(columns=5):
                                input_filter_priority_mapping_abnormal_alarm = ui.input(label='异常报警 优先级', value=config.get("filter", "priority_mapping", "abnormal_alarm"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_trends_copywriting = ui.input(label='动态文案 优先级', value=config.get("filter", "priority_mapping", "trends_copywriting"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_schedule = ui.input(label='定时任务 优先级', value=config.get("filter", "priority_mapping", "schedule"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_assistant_anchor_text = ui.input(label='助播-文本 优先级', value=config.get("filter", "priority_mapping", "assistant_anchor_text"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                input_filter_priority_mapping_assistant_anchor_audio = ui.input(label='助播-音频 优先级', value=config.get("filter", "priority_mapping", "assistant_anchor_audio"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高')
                                
                        
                        with ui.expansion('弹幕黑名单', icon="settings", value=True).classes('w-full'):
                            with ui.row():
                                switch_filter_blacklist_enable = ui.switch('启用', value=config.get("filter", "blacklist", "enable")).style(switch_internal_css)
                            
                            with ui.row():
                                textarea_filter_blacklist_username = ui.textarea(label='用户名 黑名单', value=textarea_data_change(config.get("filter", "blacklist", "username")), placeholder='屏蔽此名单内所有用户的弹幕，用户名以换行分隔').style("width:500px;")
                            
            with ui.expansion('互动功能', icon="question_answer", value=True).classes('w-full'):

                if config.get("webui", "show_card", "common_config", "read_comment"):
                    with ui.card().style(card_css):
                        ui.label('念弹幕')
                        with ui.grid(columns=4):
                            switch_read_comment_enable = ui.switch('启用', value=config.get("read_comment", "enable")).style(switch_internal_css)
                            switch_read_comment_read_username_enable = ui.switch('念用户名', value=config.get("read_comment", "read_username_enable")).style(switch_internal_css)
                            input_read_comment_username_max_len = ui.input(label='用户名最大长度', value=config.get("read_comment", "username_max_len"), placeholder='需要保留的用户名的最大长度，超出部分将被丢弃').style("width:100px;").tooltip('需要保留的用户名的最大长度，超出部分将被丢弃')
                            switch_read_comment_voice_change = ui.switch('变声', value=config.get("read_comment", "voice_change")).style(switch_internal_css)
                        with ui.grid(columns=2):
                            textarea_read_comment_read_username_copywriting = ui.textarea(
                                label='念用户名文案', 
                                placeholder='念用户名时使用的文案，可以自定义编辑多个（换行分隔），实际中会随机一个使用', 
                                value=textarea_data_change(config.get("read_comment", "read_username_copywriting"))
                            ).style("width:500px;").tooltip('念用户名时使用的文案，可以自定义编辑多个（换行分隔），实际中会随机一个使用')
                        with ui.row():
                            switch_read_comment_periodic_trigger_enable = ui.switch('周期性触发启用', value=config.get("read_comment", "periodic_trigger", "enable")).style(switch_internal_css)
                            input_read_comment_periodic_trigger_periodic_time_min = ui.input(
                                label='触发周期最小值', 
                                value=config.get("read_comment", "periodic_trigger", "periodic_time_min"), 
                                placeholder='例如：5'
                            ).style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成')
                            input_read_comment_periodic_trigger_periodic_time_max = ui.input(
                                label='触发周期最大值', 
                                value=config.get("read_comment", "periodic_trigger", "periodic_time_max"), 
                                placeholder='例如：10'
                            ).style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成')
                            input_read_comment_periodic_trigger_trigger_num_min = ui.input(
                                label='触发次数最小值', 
                                value=config.get("read_comment", "periodic_trigger", "trigger_num_min"), 
                                placeholder='例如：0'
                            ).style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成')
                            input_read_comment_periodic_trigger_trigger_num_max = ui.input(
                                label='触发次数最大值', 
                                value=config.get("read_comment", "periodic_trigger", "trigger_num_max"), 
                                placeholder='例如：1'
                            ).style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成')
                
                if config.get("webui", "show_card", "common_config", "local_qa"):
                    with ui.card().style(card_css):
                        ui.label('本地问答')
                        with ui.row():
                            switch_local_qa_periodic_trigger_enable = ui.switch('周期性触发启用', value=config.get("local_qa", "periodic_trigger", "enable")).style(switch_internal_css)
                            input_local_qa_periodic_trigger_periodic_time_min = ui.input(label='触发周期最小值', value=config.get("local_qa", "periodic_trigger", "periodic_time_min"), placeholder='每隔这个周期的时间会触发n次此功能').style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成')
                            input_local_qa_periodic_trigger_periodic_time_max = ui.input(label='触发周期最大值', value=config.get("local_qa", "periodic_trigger", "periodic_time_max"), placeholder='每隔这个周期的时间会触发n次此功能').style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成')
                            input_local_qa_periodic_trigger_trigger_num_min = ui.input(label='触发次数最小值', value=config.get("local_qa", "periodic_trigger", "trigger_num_min"), placeholder='周期到后，会触发n次此功能').style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成') 
                            input_local_qa_periodic_trigger_trigger_num_max = ui.input(label='触发次数最大值', value=config.get("local_qa", "periodic_trigger", "trigger_num_max"), placeholder='周期到后，会触发n次此功能').style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成') 
                            
                        with ui.grid(columns=5):
                            switch_local_qa_text_enable = ui.switch('启用文本匹配', value=config.get("local_qa", "text", "enable")).style(switch_internal_css)
                            select_local_qa_text_type = ui.select(
                                label='弹幕日志类型',
                                options={'json': '自定义json', 'text': '一问一答'},
                                value=config.get("local_qa", "text", "type")
                            )
                            input_local_qa_text_file_path = ui.input(label='文本问答数据路径', placeholder='本地问答文本数据存储路径', value=config.get("local_qa", "text", "file_path")).style("width:200px;")
                            input_local_qa_text_similarity = ui.input(label='文本最低相似度', placeholder='最低文本匹配相似度，就是说用户发送的内容和本地问答库中设定的内容的最低相似度。\n低了就会被当做一般弹幕处理', value=config.get("local_qa", "text", "similarity")).style("width:200px;")
                            input_local_qa_text_username_max_len = ui.input(label='用户名最大长度', value=config.get("local_qa", "text", "username_max_len"), placeholder='需要保留的用户名的最大长度，超出部分将被丢弃').style("width:100px;")       
                        with ui.grid(columns=4):
                            switch_local_qa_audio_enable = ui.switch('启用音频匹配', value=config.get("local_qa", "audio", "enable")).style(switch_internal_css)
                            input_local_qa_audio_file_path = ui.input(label='音频存储路径', placeholder='本地问答音频文件存储路径', value=config.get("local_qa", "audio", "file_path")).style("width:200px;")
                            input_local_qa_audio_similarity = ui.input(label='音频最低相似度', placeholder='最低音频匹配相似度，就是说用户发送的内容和本地音频库中音频文件名的最低相似度。\n低了就会被当做一般弹幕处理', value=config.get("local_qa", "audio", "similarity")).style("width:200px;")
                        with ui.row():
                            input_local_qa_text_json_file_path = ui.input(label='json文件路径', placeholder='填写json文件路径，默认为本地问答文本数据存储路径', value=config.get("local_qa", "text", "file_path")).style("width:200px;").tooltip("填写json文件路径，默认为本地问答文本数据存储路径")

                            def local_qa_text_json_file_reload():
                                try:
                                    # 只做了个判空 所以别乱填
                                    if input_local_qa_text_json_file_path.value != "":
                                        textarea_local_qa_text_json_file_content.value = json.dumps(common.read_file(input_local_qa_text_json_file_path.value, "dict"), ensure_ascii=False, indent=3)
                                except Exception as e:
                                    logger.error(traceback.format_exc())
                                    ui.notify(f"文件路径有误或其他问题。报错：{str(e)}", position="top", type="negative")

                            button_local_qa_text_json_file_reload = ui.button('加载文件', on_click=lambda: local_qa_text_json_file_reload(), color=button_internal_color).style(button_internal_css)

                            textarea_local_qa_text_json_file_content = ui.textarea(label='JSON文件内容', placeholder='注意格式！').style("width:700px;")

                            local_qa_text_json_file_reload()
                
                if config.get("webui", "show_card", "common_config", "thanks"):
                    with ui.card().style(card_css):
                        ui.label('答谢')  
                        with ui.row():
                            input_thanks_username_max_len = ui.input(label='用户名最大长度', value=config.get("thanks", "username_max_len"), placeholder='需要保留的用户名的最大长度，超出部分将被丢弃').style("width:100px;")       
                        with ui.expansion('入场设置', icon="settings", value=True).classes('w-full'):
                            with ui.row():
                                switch_thanks_entrance_enable = ui.switch('启用入场欢迎', value=config.get("thanks", "entrance_enable")).style(switch_internal_css)
                                switch_thanks_entrance_random = ui.switch('随机选取', value=config.get("thanks", "entrance_random")).style(switch_internal_css)
                                textarea_thanks_entrance_copy = ui.textarea(label='入场文案', value=textarea_data_change(config.get("thanks", "entrance_copy")), placeholder='用户进入直播间的相关文案，请勿动 {username}，此字符串用于替换用户名').style("width:500px;")

                            with ui.row():
                                switch_thanks_entrance_periodic_trigger_enable = ui.switch('周期性触发启用', value=config.get("thanks", "entrance", "periodic_trigger", "enable")).style(switch_internal_css)
                                input_thanks_entrance_periodic_trigger_periodic_time_min = ui.input(label='触发周期最小值', value=config.get("thanks", "entrance", "periodic_trigger", "periodic_time_min"), placeholder='每隔这个周期的时间会触发n次此功能').style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成')
                                input_thanks_entrance_periodic_trigger_periodic_time_max = ui.input(label='触发周期最大值', value=config.get("thanks", "entrance", "periodic_trigger", "periodic_time_max"), placeholder='每隔这个周期的时间会触发n次此功能').style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成')
                                input_thanks_entrance_periodic_trigger_trigger_num_min = ui.input(label='触发次数最小值', value=config.get("thanks", "entrance", "periodic_trigger", "trigger_num_min"), placeholder='周期到后，会触发n次此功能').style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成') 
                                input_thanks_entrance_periodic_trigger_trigger_num_max = ui.input(label='触发次数最大值', value=config.get("thanks", "entrance", "periodic_trigger", "trigger_num_max"), placeholder='周期到后，会触发n次此功能').style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成') 
                        with ui.expansion('礼物设置', icon="settings", value=True).classes('w-full'):
                            with ui.row():
                                switch_thanks_gift_enable = ui.switch('启用礼物答谢', value=config.get("thanks", "gift_enable")).style(switch_internal_css)
                                switch_thanks_gift_random = ui.switch('随机选取', value=config.get("thanks", "gift_random")).style(switch_internal_css)
                                textarea_thanks_gift_copy = ui.textarea(label='礼物文案', value=textarea_data_change(config.get("thanks", "gift_copy")), placeholder='用户赠送礼物的相关文案，请勿动 {username} 和 {gift_name}，此字符串用于替换用户名和礼物名').style("width:500px;")
                                input_thanks_lowest_price = ui.input(label='最低答谢礼物价格', value=config.get("thanks", "lowest_price"), placeholder='设置最低答谢礼物的价格（元），低于这个设置的礼物不会触发答谢').style("width:100px;")
                            with ui.row():
                                switch_thanks_gift_periodic_trigger_enable = ui.switch('周期性触发启用', value=config.get("thanks", "gift", "periodic_trigger", "enable")).style(switch_internal_css)
                                input_thanks_gift_periodic_trigger_periodic_time_min = ui.input(label='触发周期最小值', value=config.get("thanks", "gift", "periodic_trigger", "periodic_time_min"), placeholder='每隔这个周期的时间会触发n次此功能').style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成')
                                input_thanks_gift_periodic_trigger_periodic_time_max = ui.input(label='触发周期最大值', value=config.get("thanks", "gift", "periodic_trigger", "periodic_time_max"), placeholder='每隔这个周期的时间会触发n次此功能').style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成')
                                input_thanks_gift_periodic_trigger_trigger_num_min = ui.input(label='触发次数最小值', value=config.get("thanks", "gift", "periodic_trigger", "trigger_num_min"), placeholder='周期到后，会触发n次此功能').style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成') 
                                input_thanks_gift_periodic_trigger_trigger_num_max = ui.input(label='触发次数最大值', value=config.get("thanks", "gift", "periodic_trigger", "trigger_num_max"), placeholder='周期到后，会触发n次此功能').style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成') 
                        with ui.expansion('关注设置', icon="settings", value=True).classes('w-full'):
                            with ui.row():
                                switch_thanks_follow_enable = ui.switch('启用关注答谢', value=config.get("thanks", "follow_enable")).style(switch_internal_css)
                                switch_thanks_follow_random = ui.switch('随机选取', value=config.get("thanks", "follow_random")).style(switch_internal_css)
                                textarea_thanks_follow_copy = ui.textarea(label='关注文案', value=textarea_data_change(config.get("thanks", "follow_copy")), placeholder='用户关注时的相关文案，请勿动 {username}，此字符串用于替换用户名').style("width:500px;")
                            with ui.row():
                                switch_thanks_follow_periodic_trigger_enable = ui.switch(
                                    '周期性触发启用', 
                                    value=config.get("thanks", "follow", "periodic_trigger", "enable")
                                ).style(switch_internal_css)
                                input_thanks_follow_periodic_trigger_periodic_time_min = ui.input(
                                    label='触发周期最小值', 
                                    value=config.get("thanks", "follow", "periodic_trigger", "periodic_time_min"), 
                                    placeholder='每隔这个周期的时间会触发n次此功能'
                                ).style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成')
                                input_thanks_follow_periodic_trigger_periodic_time_max = ui.input(
                                    label='触发周期最大值', 
                                    value=config.get("thanks", "follow", "periodic_trigger", "periodic_time_max"), 
                                    placeholder='每隔这个周期的时间会触发n次此功能'
                                ).style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成')
                                input_thanks_follow_periodic_trigger_trigger_num_min = ui.input(
                                    label='触发次数最小值', 
                                    value=config.get("thanks", "follow", "periodic_trigger", "trigger_num_min"), 
                                    placeholder='周期到后，会触发n次此功能'
                                ).style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成') 
                                input_thanks_follow_periodic_trigger_trigger_num_max = ui.input(
                                    label='触发次数最大值', 
                                    value=config.get("thanks", "follow", "periodic_trigger", "trigger_num_max"), 
                                    placeholder='周期到后，会触发n次此功能'
                                ).style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成') 
                        
                if config.get("webui", "show_card", "common_config", "choose_song"): 
                    with ui.card().style(card_css):
                        ui.label('点歌模式') 
                        with ui.row():
                            switch_choose_song_enable = ui.switch('启用', value=config.get("choose_song", "enable")).style(switch_internal_css)
                            textarea_choose_song_start_cmd = ui.textarea(
                                label='点歌触发命令', 
                                value=textarea_data_change(config.get("choose_song", "start_cmd")), 
                                placeholder='点歌触发命令，换行分隔，支持多个命令，弹幕发送触发（完全匹配才行）'
                            ).style("width:200px;").tooltip('点歌触发命令，换行分隔，支持多个命令，弹幕发送触发（完全匹配才行）')
                            textarea_choose_song_stop_cmd = ui.textarea(
                                label='取消点歌命令', 
                                value=textarea_data_change(config.get("choose_song", "stop_cmd")), 
                                placeholder='停止点歌命令，换行分隔，支持多个命令，弹幕发送触发（完全匹配才行）'
                            ).style("width:200px;").tooltip('停止点歌命令，换行分隔，支持多个命令，弹幕发送触发（完全匹配才行）')
                            textarea_choose_song_random_cmd = ui.textarea(
                                label='随机点歌命令', 
                                value=textarea_data_change(config.get("choose_song", "random_cmd")), 
                                placeholder='随机点歌命令，换行分隔，支持多个命令，弹幕发送触发（完全匹配才行）'
                            ).style("width:200px;").tooltip('随机点歌命令，换行分隔，支持多个命令，弹幕发送触发（完全匹配才行）')
                        with ui.row():
                            input_choose_song_song_path = ui.input(
                                label='歌曲路径', 
                                value=config.get("choose_song", "song_path"), 
                                placeholder='歌曲音频存放的路径，会自动读取音频文件'
                            ).style("width:200px;").tooltip('歌曲音频存放的路径，会自动读取音频文件')
                            input_choose_song_match_fail_copy = ui.input(
                                label='匹配失败文案', 
                                value=config.get("choose_song", "match_fail_copy"), 
                                placeholder='匹配失败返回的音频文案 注意 {content} 这个是用于替换用户发送的歌名的，请务必不要乱删！影响使用！'
                            ).style("width:300px;").tooltip('匹配失败返回的音频文案 注意 {content} 这个是用于替换用户发送的歌名的，请务必不要乱删！影响使用！')
                            input_choose_song_similarity = ui.input(
                                label='匹配最低相似度', 
                                value=config.get("choose_song", "similarity"), 
                                placeholder='最低音频匹配相似度，就是说用户发送的内容和本地音频库中音频文件名的最低相似度。\n低了就会被当做一般弹幕处理'
                            ).style("width:200px;").tooltip('最低音频匹配相似度，就是说用户发送的内容和本地音频库中音频文件名的最低相似度。\n低了就会被当做一般弹幕处理')
                
                if config.get("webui", "show_card", "common_config", "schedule"): 
                    with ui.card().style(card_css):
                        ui.label('定时任务')
                        with ui.row():
                            input_schedule_index = ui.input(label='任务索引', value="", placeholder='任务组的排序号，就是说第一个组是1，第二个组是2，以此类推。请填写纯正整数')
                            button_schedule_add = ui.button('增加任务组', on_click=schedule_add, color=button_internal_color).style(button_internal_css)
                            button_schedule_del = ui.button('删除任务组', on_click=lambda: schedule_del(input_schedule_index.value), color=button_internal_color).style(button_internal_css)
                        
                        schedule_var = {}
                        schedule_config_card = ui.card()
                        for index, schedule in enumerate(config.get("schedule")):
                            with schedule_config_card.style(card_css):
                                with ui.row():
                                    schedule_var[str(4 * index)] = ui.switch(text=f"启用任务#{index}", value=schedule["enable"]).style(switch_internal_css)
                                    schedule_var[str(4 * index + 1)] = ui.input(label=f"最小循环周期#{index}", value=schedule["time_min"], placeholder='定时任务循环的周期最小时长（秒），即每间隔这个周期就会执行一次').style("width:100px;").tooltip('定时任务循环的周期最小时长（秒），最终周期会从最大最小之间随机生成，即每间隔这个周期就会执行一次')
                                    schedule_var[str(4 * index + 2)] = ui.input(label=f"最大循环周期#{index}", value=schedule["time_max"], placeholder='定时任务循环的周期最大时长（秒），即每间隔这个周期就会执行一次').style("width:100px;").tooltip('定时任务循环的周期最小时长（秒），最终周期会从最大最小之间随机生成，即每间隔这个周期就会执行一次')
                                    schedule_var[str(4 * index + 3)] = ui.textarea(label=f"文案列表#{index}", value=textarea_data_change(schedule["copy"]), placeholder='存放文案的列表，通过空格或换行分割，通过{变量}来替换关键数据，可修改源码自定义功能').style("width:500px;").tooltip('存放文案的列表，通过空格或换行分割，通过{变量}来替换关键数据，可修改源码自定义功能')
                    
                if config.get("webui", "show_card", "common_config", "idle_time_task"): 
                    with ui.card().style(card_css):
                        ui.label('闲时任务')
                        with ui.row():
                            switch_idle_time_task_enable = ui.switch('启用', value=config.get("idle_time_task", "enable")).style(switch_internal_css)
                            select_idle_time_task_type = ui.select(
                                label='机制类型',
                                options={
                                    '待合成消息队列更新闲时': '待合成消息队列更新闲时', 
                                    '待播放音频队列更新闲时': '待播放音频队列更新闲时', 
                                    '直播间无消息更新闲时': '直播间无消息更新闲时',
                                },
                                value=config.get("idle_time_task", "type")
                            ).tooltip('闲时任务执行的逻辑，在不同逻辑下可以实现不同的触发效果。\n如果是用于带货，可以选用 待播放音频队列更新闲时，然后把触发值设为1，从而在音频数少于1的情况下才会触发闲时任务，有效抑制大量任务产生。\n如果用于不需要一直说话的场景，推荐使用：直播间无消息更新闲时，然后把间隔设大点，隔一段时间触发一次。')
                        with ui.row():
                            input_idle_time_task_idle_min_msg_queue_len_to_trigger = ui.input(
                                label='待合成消息队列个数小于此值时触发', 
                                value=config.get("idle_time_task", "min_msg_queue_len_to_trigger"), 
                                placeholder='待合成消息队列个数小于此值时，才会触发闲时任务'
                            ).style("width:250px;").tooltip('待合成消息队列个数小于此值时，才会触发闲时任务')
                            input_idle_time_task_idle_min_audio_queue_len_to_trigger = ui.input(
                                label='待播放音频队列个数小于此值时触发', 
                                value=config.get("idle_time_task", "min_audio_queue_len_to_trigger"), 
                                placeholder='待播放音频队列个数小于此值时，才会触发闲时任务'
                            ).style("width:250px;").tooltip('待播放音频队列个数小于此值时，才会触发闲时任务')
                            
                        with ui.row():
                            input_idle_time_task_idle_time_min = ui.input(
                                label='最小闲时时间', 
                                value=config.get("idle_time_task", "idle_time_min"), 
                                placeholder='最小闲时间隔时间（正整数，单位：秒），就是在没有弹幕情况下经过的时间'
                            ).style("width:150px;").tooltip('最小闲时间隔时间（正整数，单位：秒），就是在没有弹幕情况下经过的时间')
                            input_idle_time_task_idle_time_max = ui.input(
                                label='最大闲时时间', 
                                value=config.get("idle_time_task", "idle_time_max"), 
                                placeholder='最大闲时间隔时间（正整数，单位：秒），就是在没有弹幕情况下经过的时间'
                            ).style("width:150px;").tooltip('最大闲时间隔时间（正整数，单位：秒），就是在没有弹幕情况下经过的时间')
                            input_idle_time_task_wait_play_audio_num_threshold = ui.input(
                                label='等待播放音频数量阈值', 
                                value=config.get("idle_time_task", "wait_play_audio_num_threshold"), 
                                placeholder='当等待播放音频数量超过这个阈值，将会在音频播放完毕后触发闲时时间减少到设定的缩减值，旨在控制闲时任务触发总量'
                            ).style("width:150px;").tooltip('当等待播放音频数量超过这个阈值，将会在音频播放完毕后触发闲时时间减少到设定的缩减值，旨在控制闲时任务触发总量')
                            input_idle_time_task_idle_time_reduce_to = ui.input(label='闲时计时减小到', value=config.get("idle_time_task", "idle_time_reduce_to"), placeholder='达到阈值情况下，闲时计时缩减到的数值').style("width:150px;").tooltip('达到阈值情况下，闲时计时缩减到的数值')
                            
                        with ui.row():
                            ui.label('刷新闲时计时的消息类型')
                            # 类型列表
                            idle_time_task_trigger_type_list = ["comment", "gift", "entrance", "follow"]
                            idle_time_task_trigger_type_mapping = {
                                "comment": "弹幕",
                                "gift": "礼物",
                                "entrance": "入场",
                                "follow": "关注",
                            }
                            idle_time_task_trigger_type_var = {}
                            
                            for index, idle_time_task_trigger_type in enumerate(idle_time_task_trigger_type_list):
                                if idle_time_task_trigger_type in config.get("idle_time_task", "trigger_type"):
                                    idle_time_task_trigger_type_var[str(index)] = ui.checkbox(text=idle_time_task_trigger_type_mapping[idle_time_task_trigger_type], value=True)
                                else:
                                    idle_time_task_trigger_type_var[str(index)] = ui.checkbox(text=idle_time_task_trigger_type_mapping[idle_time_task_trigger_type], value=False)
                    

                        with ui.row():
                            switch_idle_time_task_copywriting_enable = ui.switch('文案模式', value=config.get("idle_time_task", "copywriting", "enable")).style(switch_internal_css)
                            switch_idle_time_task_copywriting_random = ui.switch('随机文案', value=config.get("idle_time_task", "copywriting", "random")).style(switch_internal_css)
                            textarea_idle_time_task_copywriting_copy = ui.textarea(
                                label='文案列表', 
                                value=textarea_data_change(config.get("idle_time_task", "copywriting", "copy")), 
                                placeholder='文案列表，文案之间用换行分隔，文案会丢LLM进行处理后直接合成返回的结果'
                            ).style("width:800px;").tooltip('文案列表，文案之间用换行分隔，文案会丢LLM进行处理后直接合成返回的结果')
                        
                        with ui.row():
                            switch_idle_time_task_comment_enable = ui.switch('弹幕触发LLM模式', value=config.get("idle_time_task", "comment", "enable")).style(switch_internal_css)
                            switch_idle_time_task_comment_random = ui.switch('随机弹幕', value=config.get("idle_time_task", "comment", "random")).style(switch_internal_css)
                            textarea_idle_time_task_comment_copy = ui.textarea(
                                label='弹幕列表', 
                                value=textarea_data_change(config.get("idle_time_task", "comment", "copy")), 
                                placeholder='弹幕列表，弹幕之间用换行分隔，文案会丢LLM进行处理后直接合成返回的结果'
                            ).style("width:800px;").tooltip('弹幕列表，弹幕之间用换行分隔，文案会丢LLM进行处理后直接合成返回的结果')
                        with ui.row():
                            switch_idle_time_task_local_audio_enable = ui.switch('本地音频模式', value=config.get("idle_time_task", "local_audio", "enable")).style(switch_internal_css)
                            switch_idle_time_task_local_audio_random = ui.switch('随机本地音频', value=config.get("idle_time_task", "local_audio", "random")).style(switch_internal_css)
                            textarea_idle_time_task_local_audio_path = ui.textarea(
                                label='本地音频路径列表', 
                                value=textarea_data_change(config.get("idle_time_task", "local_audio", "path")), 
                                placeholder='本地音频路径列表，相对/绝对路径之间用换行分隔，音频文件会直接丢进音频播放队列'
                            ).style("width:800px;").tooltip('本地音频路径列表，相对/绝对路径之间用换行分隔，音频文件会直接丢进音频播放队列')

                if config.get("webui", "show_card", "common_config", "search_online"):      
                    with ui.card().style(card_css):
                        ui.label('联网搜索')
                        with ui.row():
                            switch_search_online_enable = ui.switch('启用', value=config.get("search_online", "enable")).style(switch_internal_css) 
                            switch_search_online_keyword_enable = ui.switch('关键词触发', value=config.get("search_online", "keyword_enable")).style(switch_internal_css) 
                            textarea_search_online_before_keyword = ui.textarea(
                                label='关键词前缀', 
                                placeholder='前缀必须携带其中任一字符串才能触发联网搜索\n例如：配置 【联网：】那么这个会触发：联网：杭州天气', 
                                value=textarea_data_change(config.get("search_online", "before_keyword"))
                            ).style("width:200px;").tooltip('前缀必须携带其中任一字符串才能触发联网搜索\n例如：配置 【联网：】那么这个会触发：联网：杭州天气')
                            
                            select_search_online_engine = ui.select(
                                label='搜索引擎',
                                options={'baidu': '百度搜索', 'google': '谷歌搜索'},
                                value=config.get("search_online", "engine")
                            ).style("width:100px;").tooltip('使用的搜索引擎')
                            input_search_online_engine_id = ui.input(label='引擎ID', value=config.get("search_online", "engine_id"), placeholder='默认：1，不建议修改').style("width:100px;").tooltip('就是单个引擎可能会有多种搜索方式，目前仅谷歌有2种，默认不建议修改')
                            input_search_online_count = ui.input(label='检索的文章数', value=config.get("search_online", "count"), placeholder='默认为：1，但有时候可能文章内容解析出来是空的，可以适当扩大检索数').style("width:100px;").tooltip('默认为：1，但有时候可能文章内容解析出来是空的，可以适当扩大检索数')
                            input_search_online_resp_template = ui.input(label='结果模板', value=config.get("search_online", "resp_template"), placeholder='检索后数据会以这个模板格式生成问题，请不要删除{}的两个变量，会导致无法运行').style("width:500px;").tooltip('检索后数据会以这个模板格式生成问题，请不要删除{}的两个变量，会导致无法运行')
                            input_search_online_http_proxy = ui.input(
                                label='HTTP代理地址', 
                                value=config.get("search_online", "http_proxy"), 
                                placeholder='http代理地址，需要走代理时配置此项。',
                                validation={
                                    '请输入正确格式的URL': lambda value: common.is_url_check(value),
                                }
                            ).style("width:200px;").tooltip('http代理地址，需要走代理时配置此项。')
                            input_search_online_https_proxy = ui.input(
                                label='HTTPS代理地址', 
                                value=config.get("search_online", "https_proxy"), 
                                placeholder='https代理地址，需要走代理时配置此项。',
                                validation={
                                    '请输入正确格式的URL': lambda value: common.is_url_check(value),
                                }
                            ).style("width:200px;").tooltip('https代理地址，需要走代理时配置此项。')
                        
                if config.get("webui", "show_card", "common_config", "trends_copywriting"):        
                    with ui.card().style(card_css):
                        ui.label('动态文案')
                        with ui.row():
                            switch_trends_copywriting_enable = ui.switch('启用', value=config.get("trends_copywriting", "enable")).style(switch_internal_css)
                            select_trends_copywriting_llm_type = ui.select(
                                label='LLM类型',
                                options=chat_type_options,
                                value=config.get("trends_copywriting", "llm_type")
                            ).style("width:200px;")
                            switch_trends_copywriting_random_play = ui.switch('随机播放', value=config.get("trends_copywriting", "random_play")).style(switch_internal_css)
                            input_trends_copywriting_play_interval = ui.input(label='文案播放间隔', value=config.get("trends_copywriting", "play_interval"), placeholder='文案于文案之间的播放间隔时间（秒）').style("width:200px;").tooltip('文案于文案之间的播放间隔时间（秒）')
                        
                        with ui.row():
                            input_trends_copywriting_index = ui.input(label='文案索引', value="", placeholder='文案组的排序号，就是说第一个组是1，第二个组是2，以此类推。请填写纯正整数').tooltip('文案组的排序号，就是说第一个组是1，第二个组是2，以此类推。请填写纯正整数')
                            button_trends_copywriting_add = ui.button('增加文案组', on_click=trends_copywriting_add, color=button_internal_color).style(button_internal_css)
                            button_trends_copywriting_del = ui.button('删除文案组', on_click=lambda: trends_copywriting_del(input_trends_copywriting_index.value), color=button_internal_color).style(button_internal_css)
                        
                        trends_copywriting_copywriting_var = {}
                        trends_copywriting_config_card = ui.card()
                        for index, trends_copywriting_copywriting in enumerate(config.get("trends_copywriting", "copywriting")):
                            with trends_copywriting_config_card.style(card_css):
                                with ui.row():
                                    trends_copywriting_copywriting_var[str(3 * index)] = ui.input(label=f"文案路径#{index + 1}", value=trends_copywriting_copywriting["folder_path"], placeholder='文案文件存储的文件夹路径').style("width:200px;").tooltip('文案文件存储的文件夹路径')
                                    trends_copywriting_copywriting_var[str(3 * index + 1)] = ui.switch(text=f"提示词转换#{index + 1}", value=trends_copywriting_copywriting["prompt_change_enable"])
                                    trends_copywriting_copywriting_var[str(3 * index + 2)] = ui.input(label=f"提示词转换内容#{index + 1}", value=trends_copywriting_copywriting["prompt_change_content"], placeholder='使用此提示词内容对文案内容进行转换后再进行合成，使用的LLM为聊天类型配置').style("width:500px;").tooltip('使用此提示词内容对文案内容进行转换后再进行合成，使用的LLM为聊天类型配置')
                
                if config.get("webui", "show_card", "common_config", "key_mapping"):  
                    with ui.card().style(card_css):
                        ui.label('按键/文案/音频/串口/图片 映射')
                        with ui.row():
                            switch_key_mapping_enable = ui.switch('启用', value=config.get("key_mapping", "enable")).style(switch_internal_css)
                            input_key_mapping_start_cmd = ui.input(
                                label='命令前缀', 
                                value=config.get("key_mapping", "start_cmd"), 
                                placeholder='想要触发此功能必须以这个字符串做为命令起始，不然将不会被解析为按键映射命令'
                            ).style("width:200px;").tooltip('想要触发此功能必须以这个字符串做为命令起始，不然将不会被解析为按键映射命令')
                            select_key_mapping_type = ui.select(
                                label='捕获类型',
                                options={'弹幕': '弹幕', '回复': '回复', '弹幕+回复': '弹幕+回复'},
                                value=config.get("key_mapping", "type")
                            ).style("width:200px").tooltip('什么类型的数据会触发这个板块的功能')
                        with ui.row():
                            
                            select_key_mapping_key_trigger_type = ui.select(
                                label='按键触发类型',
                                options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'},
                                value=config.get("key_mapping", "key_trigger_type")
                            ).style("width:120px").tooltip('什么类型的数据会触发按键映射')
                            switch_key_mapping_key_single_sentence_trigger_once_enable = ui.switch('单句仅触发一次（按键）', value=config.get("key_mapping", "key_single_sentence_trigger_once")).style(switch_internal_css).tooltip('一句话的数据，是否只让这句话触发一次按键映射，因为一句话中可能会有多个关键词，触发多次')
                            select_key_mapping_copywriting_trigger_type = ui.select(
                                label='文案触发类型',
                                options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'},
                                value=config.get("key_mapping", "copywriting_trigger_type")
                            ).style("width:120px").tooltip('什么类型的数据会触发文案映射')
                            switch_key_mapping_copywriting_single_sentence_trigger_once_enable = ui.switch('单句仅触发一次（文案）', value=config.get("key_mapping", "copywriting_single_sentence_trigger_once")).style(switch_internal_css).tooltip('一句话的数据，是否只让这句话触发一次文案映射，因为一句话中可能会有多个关键词，触发多次')
                            select_key_mapping_local_audio_trigger_type = ui.select(
                                label='本地音频触发类型',
                                options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'},
                                value=config.get("key_mapping", "local_audio_trigger_type")
                            ).style("width:120px").tooltip('什么类型的数据会触发本地音频映射')
                            switch_key_mapping_local_audio_single_sentence_trigger_once_enable = ui.switch('单句仅触发一次（文案）', value=config.get("key_mapping", "local_audio_single_sentence_trigger_once")).style(switch_internal_css).tooltip('一句话的数据，是否只让这句话触发一次本地音频映射，因为一句话中可能会有多个关键词，触发多次')
                            select_key_mapping_serial_trigger_type = ui.select(
                                label='串口触发类型',
                                options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'},
                                value=config.get("key_mapping", "serial_trigger_type")
                            ).style("width:120px").tooltip('什么类型的数据会触发文案映射')
                            switch_key_mapping_serial_single_sentence_trigger_once_enable = ui.switch('单句仅触发一次（串口）', value=config.get("key_mapping", "serial_single_sentence_trigger_once")).style(switch_internal_css).tooltip('一句话的数据，是否只让这句话触发一次文案映射，因为一句话中可能会有多个关键词，触发多次')
                            select_key_mapping_img_path_trigger_type = ui.select(
                                label='图片触发类型',
                                options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'},
                                value=config.get("key_mapping", "img_path_trigger_type")
                            ).style("width:120px").tooltip('什么类型的数据会触发文案映射')
                            switch_key_mapping_img_path_single_sentence_trigger_once_enable = ui.switch('单句仅触发一次（图片显示）', value=config.get("key_mapping", "img_path_single_sentence_trigger_once")).style(switch_internal_css).tooltip('一句话的数据，是否只让这句话触发一次文案映射，因为一句话中可能会有多个关键词，触发多次')
                            
                        with ui.row():
                            input_key_mapping_index = ui.input(label='配置索引', value="", placeholder='配置组的排序号，就是说第一个组是1，第二个组是2，以此类推。请填写纯正整数').tooltip('配置组的排序号，就是说第一个组是1，第二个组是2，以此类推。请填写纯正整数')
                            button_key_mapping_add = ui.button('增加配置组', on_click=key_mapping_add, color=button_internal_color).style(button_internal_css)
                            button_key_mapping_del = ui.button('删除配置组', on_click=lambda: key_mapping_del(input_key_mapping_index.value), color=button_internal_color).style(button_internal_css)
                        
                        
                        key_mapping_config_var = {}
                        key_mapping_config_card = ui.card()
                        for index, key_mapping_config in enumerate(config.get("key_mapping", "config")):
                            with key_mapping_config_card.style(card_css):
                                with ui.row():
                                    num = 9
                                    key_mapping_config_var[str(num * index)] = ui.textarea(label=f"关键词#{index + 1}", value=textarea_data_change(key_mapping_config["keywords"]), placeholder='此处输入触发的关键词，多个请以换行分隔').style("width:100px;").tooltip('此处输入触发的关键词，多个请以换行分隔')
                                    key_mapping_config_var[str(num * index + 1)] = ui.textarea(label=f"礼物#{index + 1}", value=textarea_data_change(key_mapping_config["gift"]), placeholder='此处输入触发的礼物名，多个请以换行分隔').style("width:100px;").tooltip('此处输入触发的礼物名，多个请以换行分隔')
                                    key_mapping_config_var[str(num * index + 2)] = ui.textarea(label=f"按键#{index + 1}", value=textarea_data_change(key_mapping_config["keys"]), placeholder='此处输入你要映射的按键，多个按键请以换行分隔（按键名参考pyautogui规则）').style("width:100px;").tooltip('此处输入你要映射的按键，多个按键请以换行分隔（按键名参考pyautogui规则）')
                                    key_mapping_config_var[str(num * index + 3)] = ui.input(label=f"相似度#{index + 1}", value=key_mapping_config["similarity"], placeholder='关键词与用户输入的相似度，默认1即100%').style("width:50px;").tooltip('关键词与用户输入的相似度，默认1即100%')
                                    key_mapping_config_var[str(num * index + 4)] = ui.textarea(label=f"文案#{index + 1}", value=textarea_data_change(key_mapping_config["copywriting"]), placeholder='此处输入触发后合成的文案内容，多个请以换行分隔').style("width:300px;").tooltip('此处输入触发后合成的文案内容，多个请以换行分隔')
                                    key_mapping_config_var[str(num * index + 5)] = ui.textarea(label=f"本地音频#{index + 1}", value=textarea_data_change(key_mapping_config["local_audio"]), placeholder='此处输入触发后播放的本地音频路径，多个请以换行分隔').style("width:300px;").tooltip('此处输入触发后播放的本地音频路径，多个请以换行分隔')
                                    key_mapping_config_var[str(num * index + 6)] = ui.input(label=f"串口名#{index + 1}", value=key_mapping_config["serial_name"], placeholder='例如：COM1').style("width:100px;").tooltip('串口页配置的串口名，例如：COM1')
                                    key_mapping_config_var[str(num * index + 7)] = ui.textarea(label=f"串口发送内容#{index + 1}", value=textarea_data_change(key_mapping_config["serial_send_data"]), placeholder='多个请以换行分隔，ASCII例如：open led\nHEX例如（2个字符的十六进制字符）：313233').style("width:300px;").tooltip('此处输入发送到串口的数据内容，数据类型根据串口页设置决定，多个请以换行分隔')
                                    key_mapping_config_var[str(num * index + 8)] = ui.textarea(label=f"图片路径#{index + 1}", value=textarea_data_change(key_mapping_config["img_path"]), placeholder='多个请以换行分隔，支持绝对路径或相对路径，需要注意路径的斜杠哈').style("width:300px;").tooltip('此处输入图片路径，多个请以换行分隔。默认随机显示')
                                    
                                    # with ui.card().style(card_css):
                                    #     key_mapping_config_var[str(6 * index + 5)] = ui.textarea(label=f"串口号#{index + 1}", value=textarea_data_change(key_mapping_config["serial_name"]), placeholder='例如：COM1').style("width:100px;").tooltip('发送的串口名')
                                    #     key_mapping_config_var[str(6 * index + 5)] = ui.textarea(label=f"发送数据#{index + 1}", value=key_mapping_config["serial_data"], placeholder='根据类型填写，多个请以换行分隔').style("width:300px;").tooltip('根据类型填写，多个请以换行分隔')
                
            with ui.expansion('辅助软件对接', icon="extension", value=True).classes('w-full'):

                with ui.card().style(card_css):
                    ui.label("洛曦 直播弹幕助手")
                    with ui.row():
                        switch_luoxi_project_Live_Comment_Assistant_enable = ui.switch('启用', value=config.get("luoxi_project", "Live_Comment_Assistant", "enable")).style(switch_internal_css)
                        select_luoxi_project_Live_Comment_Assistant_version = ui.select(
                            label='对接版本',
                            options={'V0.1.x': 'V0.1.x'},
                            value=config.get("luoxi_project", "Live_Comment_Assistant", "version")
                        ).style("width:100px;").tooltip('版本，因为不同版本可能会有接口上的改动')
                        input_luoxi_project_Live_Comment_Assistant_api_ip_port = ui.input(
                            label='API地址', 
                            value=config.get("luoxi_project", "Live_Comment_Assistant", "api_ip_port"), 
                            placeholder='洛曦 直播弹幕助手 API地址'
                        ).style("width:200px;").tooltip('洛曦 直播弹幕助手 API地址')
                
                    with ui.card().style(card_css):
                        ui.label("触发类型")
                        with ui.row():    
                            # 类型列表源自 音频合成的所支持的type值
                            luoxi_project_Live_Comment_Assistant_type_list = [
                                "comment",
                                "comment_reply", 
                                "idle_time_task", 
                                "entrance_reply", 
                                "follow_reply", 
                                "gift_reply", 
                                "reread", 
                                "schedule",
                                "integral",
                                "key_mapping_copywriting"
                            ]
                            luoxi_project_Live_Comment_Assistant_type_mapping = {
                                "comment": "弹幕消息",
                                "comment_reply": "弹幕回复",
                                "idle_time_task": "闲时任务",
                                "entrance_reply": "入场回复",
                                "follow_reply": "关注回复",
                                "gift_reply": "礼物回复",
                                "reread": "复读",
                                "schedule": "定时任务",
                                "integral": "积分消息",
                                "key_mapping_copywriting": "按键映射-文案"
                            }
                            luoxi_project_Live_Comment_Assistant_type_var = {}
                            
                            for index, luoxi_project_Live_Comment_Assistant_type in enumerate(luoxi_project_Live_Comment_Assistant_type_list):
                                if luoxi_project_Live_Comment_Assistant_type in config.get("luoxi_project", "Live_Comment_Assistant", "type"):
                                    luoxi_project_Live_Comment_Assistant_type_var[str(index)] = ui.checkbox(
                                        text=luoxi_project_Live_Comment_Assistant_type_mapping[luoxi_project_Live_Comment_Assistant_type], 
                                        value=True
                                    )
                                else:
                                    luoxi_project_Live_Comment_Assistant_type_var[str(index)] = ui.checkbox(
                                        text=luoxi_project_Live_Comment_Assistant_type_mapping[luoxi_project_Live_Comment_Assistant_type], 
                                        value=False
                                    )
                    with ui.card().style(card_css):
                        ui.label("触发位置")
                        with ui.row(): 
                            luoxi_project_Live_Comment_Assistant_trigger_position_list = [
                                "消息产生时", 
                                "音频播放时",
                            ]
                            luoxi_project_Live_Comment_Assistant_trigger_position_mapping = {
                                "消息产生时": "消息产生时",
                                "音频播放时": "音频播放时",
                            }
                            luoxi_project_Live_Comment_Assistant_trigger_position_var = {}
                            
                            for index, luoxi_project_Live_Comment_Assistant_trigger_position in enumerate(luoxi_project_Live_Comment_Assistant_trigger_position_list):
                                if luoxi_project_Live_Comment_Assistant_trigger_position in config.get("luoxi_project", "Live_Comment_Assistant", "trigger_position"):
                                    luoxi_project_Live_Comment_Assistant_trigger_position_var[str(index)] = ui.checkbox(
                                        text=luoxi_project_Live_Comment_Assistant_trigger_position_mapping[luoxi_project_Live_Comment_Assistant_trigger_position], 
                                        value=True
                                    )
                                else:
                                    luoxi_project_Live_Comment_Assistant_trigger_position_var[str(index)] = ui.checkbox(
                                        text=luoxi_project_Live_Comment_Assistant_trigger_position_mapping[luoxi_project_Live_Comment_Assistant_trigger_position], 
                                        value=False
                                    )        
                

                if config.get("webui", "show_card", "common_config", "sd"):      
                    with ui.card().style(card_css):
                        ui.label('Stable Diffusion')
                        with ui.row():
                            switch_sd_enable = ui.switch('启用', value=config.get("sd", "enable")).style(switch_internal_css) 
                            select_sd_translate_type = ui.select(
                                label='翻译类型',
                                options={'none': '不启用', 'baidu': '百度翻译', 'google': '谷歌翻译'},
                                value=config.get("sd", "translate_type")
                            ).style("width:100px;").tooltip('针对触发的画图命令使用翻译页的配置，进行翻译后再丢给SD画图')
                            select_sd_prompt_llm_type = ui.select(
                                label='LLM类型',
                                options=chat_type_options,
                                value=config.get("sd", "prompt_llm", "type")
                            ).style("width:100px;")
                            input_sd_prompt_llm_before_prompt = ui.input(label='提示词前缀', value=config.get("sd", "prompt_llm", "before_prompt"), placeholder='LLM提示词前缀').style("width:300px;")
                            input_sd_prompt_llm_after_prompt = ui.input(label='提示词后缀', value=config.get("sd", "prompt_llm", "after_prompt"), placeholder='LLM提示词后缀').style("width:300px;")
                        with ui.row(): 
                            input_sd_trigger = ui.input(label='弹幕触发前缀', value=config.get("sd", "trigger"), placeholder='触发的关键词（弹幕头部触发）').style("width:200px;")
                            input_sd_ip = ui.input(label='IP地址', value=config.get("sd", "ip"), placeholder='服务运行的IP地址').style("width:200px;")
                            input_sd_port = ui.input(label='端口', value=config.get("sd", "port"), placeholder='服务运行的端口').style("width:100px;")
                            input_sd_negative_prompt = ui.input(label='负面提示词', value=config.get("sd", "negative_prompt"), placeholder='负面文本提示，用于指定与生成图像相矛盾或相反的内容').style("width:200px;")
                            input_sd_seed = ui.input(label='随机种子', value=config.get("sd", "seed"), placeholder='随机种子，用于控制生成过程的随机性。可以设置一个整数值，以获得可重复的结果。').style("width:100px;")
                            textarea_sd_styles = ui.textarea(label='图像风格', placeholder='样式列表，用于指定生成图像的风格。可以包含多个风格，例如 ["anime", "portrait"]', value=textarea_data_change(config.get("sd", "styles"))).style("width:200px;")
                        with ui.row():
                            input_sd_cfg_scale = ui.input(label='提示词相关性', value=config.get("sd", "cfg_scale"), placeholder='提示词相关性，无分类器指导信息影响尺度(Classifier Free Guidance Scale) -图像应在多大程度上服从提示词-较低的值会产生更有创意的结果。').style("width:100px;")
                            input_sd_steps = ui.input(label='生成图像步数', value=config.get("sd", "steps"), placeholder='生成图像的步数，用于控制生成的精确程度。').style("width:100px;") 
                            input_sd_hr_resize_x = ui.input(label='图像水平像素', value=config.get("sd", "hr_resize_x"), placeholder='生成图像的水平尺寸。').style("width:100px;")
                            input_sd_hr_resize_y = ui.input(label='图像垂直像素', value=config.get("sd", "hr_resize_y"), placeholder='生成图像的垂直尺寸。').style("width:100px;")
                            input_sd_denoising_strength = ui.input(label='去噪强度', value=config.get("sd", "denoising_strength"), placeholder='去噪强度，用于控制生成图像中的噪点。').style("width:100px;")
                        with ui.row():
                            switch_sd_enable_hr = ui.switch('高分辨率生成', value=config.get("sd", "enable_hr")).style(switch_internal_css)
                            input_sd_hr_scale = ui.input(label='高分辨率缩放因子', value=config.get("sd", "hr_scale"), placeholder='高分辨率缩放因子，用于指定生成图像的高分辨率缩放级别。').style("width:200px;")
                            input_sd_hr_second_pass_steps = ui.input(label='高分生二次传递步数', value=config.get("sd", "hr_second_pass_steps"), placeholder='高分辨率生成的第二次传递步数。').style("width:200px;")
                            switch_sd_save_enable = ui.switch('保存图片到本地', value=config.get("sd", "save_enable")).style(switch_internal_css)
                            switch_sd_loop_cover = ui.switch('本地图片循环覆盖', value=config.get("sd", "loop_cover")).style(switch_internal_css)
                            input_sd_save_path = ui.input(label='图片保存路径', value=config.get("sd", "save_path"), placeholder='生成图片存储路径，不建议修改').style("width:200px;")
                
                if config.get("webui", "show_card", "common_config", "web_captions_printer"):
                    with ui.card().style(card_css):
                        ui.label('web字幕打印机')
                        with ui.grid(columns=2):
                            switch_web_captions_printer_enable = ui.switch('启用', value=config.get("web_captions_printer", "enable")).style(switch_internal_css).tooltip("如果您使用了audio player来做音频播放，并开启了其web字幕打印机功能,\n那请勿启动此功能，因为这样就重复惹")
                            input_web_captions_printer_api_ip_port = ui.input(
                                label='API地址', 
                                value=config.get("web_captions_printer", "api_ip_port"), 
                                placeholder='web字幕打印机的API地址，只需要 http://ip:端口 即可',
                                validation={
                                    '请输入正确格式的URL': lambda value: common.is_url_check(value),
                                }
                            ).style("width:200px;").tooltip('web字幕打印机的API地址，只需要 http://ip:端口 即可')


            with ui.expansion('高级功能', icon="view_in_ar", value=True).classes('w-full'):
                if config.get("webui", "show_card", "common_config", "log"):
                    with ui.card().style(card_css):
                        ui.label('日志')
                        with ui.grid(columns=4):
                            switch_captions_enable = ui.switch('启用', value=config.get("captions", "enable")).style(switch_internal_css)

                            select_comment_log_type = ui.select(
                                label='弹幕日志类型',
                                options={'问答': '问答', '问题': '问题', '回答': '回答', '不记录': '不记录'},
                                value=config.get("comment_log_type")
                            )

                            input_captions_file_path = ui.input(label='字幕日志路径', value=config.get("captions", "file_path"), placeholder='字幕日志存储路径').style("width:200px;")
                            input_captions_raw_file_path = ui.input(label='原文字幕日志路径', placeholder='原文字幕日志存储路径',
                                                                value=config.get("captions", "raw_file_path")).style("width:200px;")

                if config.get("webui", "show_card", "common_config", "database"):  
                    with ui.card().style(card_css):
                        ui.label('数据库')
                        with ui.grid(columns=4):
                            switch_database_comment_enable = ui.switch('弹幕日志', value=config.get("database", "comment_enable")).style(switch_internal_css)
                            switch_database_entrance_enable = ui.switch('入场日志', value=config.get("database", "entrance_enable")).style(switch_internal_css)
                            switch_database_gift_enable = ui.switch('礼物日志', value=config.get("database", "gift_enable")).style(switch_internal_css)
                            input_database_path = ui.input(label='数据库路径', value=config.get("database", "path"), placeholder='数据库文件存储路径').style("width:200px;")
                            
                              
                if config.get("webui", "show_card", "common_config", "custom_cmd"):  
                    with ui.card().style(card_css):
                        ui.label('自定义命令')
                        with ui.row():
                            switch_custom_cmd_enable = ui.switch('启用', value=config.get("custom_cmd", "enable")).style(switch_internal_css)
                            select_custom_cmd_type = ui.select(
                                label='类型',
                                options={'弹幕': '弹幕'},
                                value=config.get("custom_cmd", "type")
                            ).style("width:200px")
                        with ui.row():
                            input_custom_cmd_index = ui.input(label='配置索引', value="", placeholder='配置组的排序号，就是说第一个组是1，第二个组是2，以此类推。请填写纯正整数')
                            button_custom_cmd_add = ui.button('增加配置组', on_click=custom_cmd_add, color=button_internal_color).style(button_internal_css)
                            button_custom_cmd_del = ui.button('删除配置组', on_click=lambda: custom_cmd_del(input_custom_cmd_index.value), color=button_internal_color).style(button_internal_css)
                        
                        custom_cmd_config_var = {}
                        custom_cmd_config_card = ui.card()
                        for index, custom_cmd_config in enumerate(config.get("custom_cmd", "config")):
                            with custom_cmd_config_card.style(card_css):
                                with ui.row():
                                    custom_cmd_config_var[str(7 * index)] = ui.textarea(label=f"关键词#{index + 1}", value=textarea_data_change(custom_cmd_config["keywords"]), placeholder='此处输入触发的关键词，多个请以换行分隔').style("width:200px;")
                                    custom_cmd_config_var[str(7 * index + 1)] = ui.input(label=f"相似度#{index + 1}", value=custom_cmd_config["similarity"], placeholder='关键词与用户输入的相似度，默认1即100%').style("width:100px;")
                                    custom_cmd_config_var[str(7 * index + 2)] = ui.textarea(
                                        label=f"API URL#{index + 1}", 
                                        value=custom_cmd_config["api_url"], 
                                        placeholder='发送HTTP请求的API链接', 
                                        validation={
                                            '请输入正确格式的URL': lambda value: common.is_url_check(value),
                                        }
                                    ).style("width:300px;").tooltip('发送HTTP请求的API链接')
                                    custom_cmd_config_var[str(7 * index + 3)] = ui.select(label=f"API类型#{index + 1}", value=custom_cmd_config["api_type"], options={"GET": "GET"}).style("width:100px;")
                                    custom_cmd_config_var[str(7 * index + 4)] = ui.select(label=f"请求返回数据类型#{index + 1}", value=custom_cmd_config["resp_data_type"], options={"json": "json", "content": "content"}).style("width:150px;")
                                    custom_cmd_config_var[str(7 * index + 5)] = ui.textarea(label=f"数据解析（eval执行）#{index + 1}", value=custom_cmd_config["data_analysis"], placeholder='数据解析，请不要随意修改resp变量，会被用于最后返回数据内容的解析').style("width:200px;").tooltip('数据解析，请不要随意修改resp变量，会被用于最后返回数据内容的解析')
                                    custom_cmd_config_var[str(7 * index + 6)] = ui.textarea(label=f"返回内容模板#{index + 1}", value=custom_cmd_config["resp_template"], placeholder='请不要随意删除data变量，支持动态变量，最终会合并成完成内容进行音频合成').style("width:300px;").tooltip("请不要随意删除data变量，支持动态变量，最终会合并成完成内容进行音频合成")


                if config.get("webui", "show_card", "common_config", "trends_config"):  
                    with ui.card().style(card_css):
                        ui.label('动态配置')
                        with ui.row():
                            switch_trends_config_enable = ui.switch('启用', value=config.get("trends_config", "enable")).style(switch_internal_css)
                        trends_config_path_var = {}
                        for index, trends_config_path in enumerate(config.get("trends_config", "path")):
                            with ui.grid(columns=2):
                                trends_config_path_var[str(2 * index)] = ui.input(label="在线人数范围", value=trends_config_path["online_num"], placeholder='在线人数范围，用减号-分隔，例如：0-10').style("width:200px;").tooltip("在线人数范围，用减号-分隔，例如：0-10")
                                trends_config_path_var[str(2 * index + 1)] = ui.input(label="配置路径", value=trends_config_path["path"], placeholder='此处输入加载的配置文件的路径').style("width:200px;").tooltip("此处输入加载的配置文件的路径")
                
                if config.get("webui", "show_card", "common_config", "abnormal_alarm"): 
                    with ui.card().style(card_css):
                        ui.label('异常报警')
                        with ui.row():
                            switch_abnormal_alarm_platform_enable = ui.switch('启用平台报警', value=config.get("abnormal_alarm", "platform", "enable")).style(switch_internal_css)
                            select_abnormal_alarm_platform_type = ui.select(
                                label='类型',
                                options={'local_audio': '本地音频'},
                                value=config.get("abnormal_alarm", "platform", "type")
                            )
                            input_abnormal_alarm_platform_start_alarm_error_num = ui.input(label='开始报警错误数', value=config.get("abnormal_alarm", "platform", "start_alarm_error_num"), placeholder='开始异常报警的错误数，超过这个数后就会报警').style("width:100px;")
                            input_abnormal_alarm_platform_auto_restart_error_num = ui.input(label='自动重启错误数', value=config.get("abnormal_alarm", "platform", "auto_restart_error_num"), placeholder='记得先启用“自动运行”功能。自动重启的错误数，超过这个数后就会自动重启webui。').style("width:100px;")
                            input_abnormal_alarm_platform_local_audio_path = ui.input(label='本地音频路径', value=config.get("abnormal_alarm", "platform", "local_audio_path"), placeholder='本地音频存储的文件路径（可以是多个音频，随机一个）').style("width:300px;")
                        with ui.row():
                            switch_abnormal_alarm_llm_enable = ui.switch('启用LLM报警', value=config.get("abnormal_alarm", "llm", "enable")).style(switch_internal_css)
                            select_abnormal_alarm_llm_type = ui.select(
                                label='类型',
                                options={'local_audio': '本地音频'},
                                value=config.get("abnormal_alarm", "llm", "type")
                            )
                            input_abnormal_alarm_llm_start_alarm_error_num = ui.input(label='开始报警错误数', value=config.get("abnormal_alarm", "llm", "start_alarm_error_num"), placeholder='开始异常报警的错误数，超过这个数后就会报警').style("width:100px;")
                            input_abnormal_alarm_llm_auto_restart_error_num = ui.input(label='自动重启错误数', value=config.get("abnormal_alarm", "llm", "auto_restart_error_num"), placeholder='记得先启用“自动运行”功能。自动重启的错误数，超过这个数后就会自动重启webui。').style("width:100px;")
                            input_abnormal_alarm_llm_local_audio_path = ui.input(label='本地音频路径', value=config.get("abnormal_alarm", "llm", "local_audio_path"), placeholder='本地音频存储的文件路径（可以是多个音频，随机一个）').style("width:300px;")
                        with ui.row():
                            switch_abnormal_alarm_tts_enable = ui.switch('启用TTS报警', value=config.get("abnormal_alarm", "tts", "enable")).style(switch_internal_css)
                            select_abnormal_alarm_tts_type = ui.select(
                                label='类型',
                                options={'local_audio': '本地音频'},
                                value=config.get("abnormal_alarm", "tts", "type")
                            )
                            input_abnormal_alarm_tts_start_alarm_error_num = ui.input(label='开始报警错误数', value=config.get("abnormal_alarm", "tts", "start_alarm_error_num"), placeholder='开始异常报警的错误数，超过这个数后就会报警').style("width:100px;")
                            input_abnormal_alarm_tts_auto_restart_error_num = ui.input(label='自动重启错误数', value=config.get("abnormal_alarm", "tts", "auto_restart_error_num"), placeholder='记得先启用“自动运行”功能。自动重启的错误数，超过这个数后就会自动重启webui。').style("width:100px;")
                            input_abnormal_alarm_tts_local_audio_path = ui.input(label='本地音频路径', value=config.get("abnormal_alarm", "tts", "local_audio_path"), placeholder='本地音频存储的文件路径（可以是多个音频，随机一个）').style("width:300px;")
                        with ui.row():
                            switch_abnormal_alarm_svc_enable = ui.switch('启用SVC报警', value=config.get("abnormal_alarm", "svc", "enable")).style(switch_internal_css)
                            select_abnormal_alarm_svc_type = ui.select(
                                label='类型',
                                options={'local_audio': '本地音频'},
                                value=config.get("abnormal_alarm", "svc", "type")
                            )
                            input_abnormal_alarm_svc_start_alarm_error_num = ui.input(label='开始报警错误数', value=config.get("abnormal_alarm", "svc", "start_alarm_error_num"), placeholder='开始异常报警的错误数，超过这个数后就会报警').style("width:100px;")
                            input_abnormal_alarm_svc_auto_restart_error_num = ui.input(label='自动重启错误数', value=config.get("abnormal_alarm", "svc", "auto_restart_error_num"), placeholder='记得先启用“自动运行”功能。自动重启的错误数，超过这个数后就会自动重启webui。').style("width:100px;")
                            input_abnormal_alarm_svc_local_audio_path = ui.input(label='本地音频路径', value=config.get("abnormal_alarm", "svc", "local_audio_path"), placeholder='本地音频存储的文件路径（可以是多个音频，随机一个）').style("width:300px;")
                        with ui.row():
                            switch_abnormal_alarm_visual_body_enable = ui.switch('启用虚拟身体报警', value=config.get("abnormal_alarm", "visual_body", "enable")).style(switch_internal_css)
                            select_abnormal_alarm_visual_body_type = ui.select(
                                label='类型',
                                options={'local_audio': '本地音频'},
                                value=config.get("abnormal_alarm", "visual_body", "type")
                            )
                            input_abnormal_alarm_visual_body_start_alarm_error_num = ui.input(label='开始报警错误数', value=config.get("abnormal_alarm", "visual_body", "start_alarm_error_num"), placeholder='开始异常报警的错误数，超过这个数后就会报警').style("width:100px;")
                            input_abnormal_alarm_visual_body_auto_restart_error_num = ui.input(label='自动重启错误数', value=config.get("abnormal_alarm", "visual_body", "auto_restart_error_num"), placeholder='记得先启用“自动运行”功能。自动重启的错误数，超过这个数后就会自动重启webui。').style("width:100px;")
                            input_abnormal_alarm_visual_body_local_audio_path = ui.input(label='本地音频路径', value=config.get("abnormal_alarm", "visual_body", "local_audio_path"), placeholder='本地音频存储的文件路径（可以是多个音频，随机一个）').style("width:300px;")
                        with ui.row():
                            switch_abnormal_alarm_other_enable = ui.switch('启用其他报警', value=config.get("abnormal_alarm", "other", "enable")).style(switch_internal_css)
                            select_abnormal_alarm_other_type = ui.select(
                                label='类型',
                                options={'local_audio': '本地音频'},
                                value=config.get("abnormal_alarm", "other", "type")
                            )
                            input_abnormal_alarm_other_start_alarm_error_num = ui.input(label='开始报警错误数', value=config.get("abnormal_alarm", "other", "start_alarm_error_num"), placeholder='开始异常报警的错误数，超过这个数后就会报警').style("width:100px;")
                            input_abnormal_alarm_other_auto_restart_error_num = ui.input(label='自动重启错误数', value=config.get("abnormal_alarm", "other", "auto_restart_error_num"), placeholder='记得先启用“自动运行”功能。自动重启的错误数，超过这个数后就会自动重启webui。').style("width:100px;")
                            input_abnormal_alarm_other_local_audio_path = ui.input(label='本地音频路径', value=config.get("abnormal_alarm", "other", "local_audio_path"), placeholder='本地音频存储的文件路径（可以是多个音频，随机一个）').style("width:300px;")
                        
                if config.get("webui", "show_card", "common_config", "coordination_program"):
                    with ui.expansion('联动程序', icon="settings", value=True).classes('w-full'):
                        with ui.row():
                            input_coordination_program_index = ui.input(label='配置索引', value="", placeholder='配置组的排序号，就是说第一个组是1，第二个组是2，以此类推。请填写纯正整数')
                            button_coordination_program_add = ui.button('增加配置组', on_click=coordination_program_add, color=button_internal_color).style(button_internal_css)
                            button_coordination_program_del = ui.button('删除配置组', on_click=lambda: coordination_program_del(input_coordination_program_index.value), color=button_internal_color).style(button_internal_css)
                        
                        coordination_program_var = {}
                        coordination_program_config_card = ui.card()
                        for index, coordination_program in enumerate(config.get("coordination_program")):
                            with coordination_program_config_card.style(card_css):
                                with ui.row():
                                    coordination_program_var[str(4 * index)] = ui.switch(f'启用#{index + 1}', value=coordination_program["enable"]).style(switch_internal_css)
                                    coordination_program_var[str(4 * index + 1)] = ui.input(label=f"程序名#{index + 1}", value=coordination_program["name"], placeholder='给你的程序取个名字，别整特殊符号！').style("width:200px;")
                                    coordination_program_var[str(4 * index + 2)] = ui.input(label=f"可执行程序#{index + 1}", value=coordination_program["executable"], placeholder='可执行程序的路径，最好是绝对路径，如python的程序').style("width:400px;")
                                    coordination_program_var[str(4 * index + 3)] = ui.textarea(label=f'参数#{index + 1}', value=textarea_data_change(coordination_program["parameters"]), placeholder='参数，可以传入多个参数，换行分隔。如启动的程序的路径，命令携带的传参等').style("width:500px;")
                

        with ui.tab_panel(llm_page).style(tab_panel_css):
            if config.get("webui", "show_card", "llm", "custom_llm"):
                with ui.card().style(card_css):
                    ui.label("自定义LLM")
                    with ui.row():
                        textarea_custom_llm_url = ui.textarea(
                            label=f"API URL", 
                            value=config.get("custom_llm", "url"), 
                            placeholder='发送HTTP请求的API链接', 
                            validation={
                                '请输入正确格式的URL': lambda value: common.is_url_check(value),
                            }
                        ).style("width:200px;").tooltip('发送HTTP请求的API链接')
                        textarea_custom_llm_method = ui.select(label=f"API类型", value=config.get("custom_llm", "method"), options={"GET": "GET", "POST": "POST"}).style("width:100px;").tooltip('API类型')
                        textarea_custom_llm_headers = ui.textarea(label=f"请求头", value=config.get("custom_llm", "headers"), placeholder='换行分隔，例：Content-Type:application/json\nAuthorization:Bearer sk').style("width:300px;").tooltip('换行分隔，例：Content-Type:application/json\nAuthorization:Bearer sk')
                        textarea_custom_llm_proxies = ui.textarea(label=f"代理", value=config.get("custom_llm", "proxies"), placeholder='requests库代理配置方法，json数据用"双引号').style("width:200px;").tooltip('requests库代理配置方法，json数据用"双引号')
                    with ui.row():
                        select_custom_llm_body_type = ui.select(label=f"请求体类型", value=config.get("custom_llm", "body_type"), options={"json": "json", "raw": "raw"}).style("width:150px;").tooltip('请求体类型')
                        textarea_custom_llm_body = ui.textarea(label=f"请求体", value=config.get("custom_llm", "body"), placeholder='请求体，写字符串，注意变量需要两个大括号包裹{{}}，json数据的话用"双引号').style("width:300px;").tooltip('请求体，写字符串，注意变量需要两个大括号包裹{{}}，json数据的话用"双引号')
                        select_custom_llm_resp_data_type = ui.select(label=f"请求返回数据类型", value=config.get("custom_llm", "resp_data_type"), options={"json": "json", "content": "content"}).style("width:150px;").tooltip('请求返回数据类型')
                        textarea_custom_llm_data_analysis = ui.textarea(label=f"数据解析（eval执行）", value=config.get("custom_llm", "data_analysis"), placeholder='数据解析，请不要随意修改resp变量，会被用于最后返回数据内容的解析').style("width:300px;").tooltip('数据解析，请不要随意修改resp变量，会被用于最后返回数据内容的解析')
                        textarea_custom_llm_resp_template = ui.textarea(label=f"返回内容模板", value=config.get("custom_llm", "resp_template"), placeholder='请不要随意删除data变量，支持动态变量，最终会合并成完成内容进行音频合成').style("width:300px;").tooltip('请不要随意删除data变量，支持动态变量，最终会合并成完成内容进行音频合成')

        with ui.tab_panel(tts_page).style(tab_panel_css):
            # 通用-合成试听音频
            async def tts_common_audio_synthesis():
                ui.notify(position="top", type="warning", message="音频合成中，将会阻塞其他任务运行，请勿做其他操作，查看日志情况，耐心等待")
                logger.warning("音频合成中，将会阻塞其他任务运行，请勿做其他操作，查看日志情况，耐心等待")
                
                content = input_tts_common_text.value
                audio_synthesis_type = select_tts_common_audio_synthesis_type.value

                # 使用本地配置进行音频合成，返回音频路径
                file_path = await audio.audio_synthesis_use_local_config(content, audio_synthesis_type)

                if file_path:
                    logger.info(f"音频合成成功，存储于：{file_path}")
                    ui.notify(position="top", type="positive", message=f"音频合成成功，存储于：{file_path}")
                else:
                    logger.error(f"音频合成失败！请查看日志排查问题")
                    ui.notify(position="top", type="negative", message=f"音频合成失败！请查看日志排查问题")
                    return

                def clear_tts_common_audio_card(file_path):
                    tts_common_audio_card.clear()
                    if common.del_file(file_path):
                        ui.notify(position="top", type="positive", message=f"删除文件成功：{file_path}")
                    else:
                        ui.notify(position="top", type="negative", message=f"删除文件失败：{file_path}")
                
                # 清空card
                tts_common_audio_card.clear()
                tmp_label = ui.label(f"音频合成成功，存储于：{file_path}")
                tmp_label.move(tts_common_audio_card)
                audio_tmp = ui.audio(src=file_path)
                audio_tmp.move(tts_common_audio_card)
                button_audio_del = ui.button('删除音频', on_click=lambda: clear_tts_common_audio_card(file_path), color=button_internal_color).style(button_internal_css)
                button_audio_del.move(tts_common_audio_card)
                
                
            with ui.card().style(card_css):
                ui.label("合成测试（只是测试，若确认使用此TTS，请前往 通用配置 配置 语音合成）")
                with ui.row():
                    select_tts_common_audio_synthesis_type = ui.select(
                        label='语音合成', 
                        options=audio_synthesis_type_options, 
                        value=config.get("audio_synthesis_type")
                    ).style("width:200px;")
                    input_tts_common_text = ui.input(label='待合成音频内容', placeholder='此处填写待合成的音频文本内容', value="此处填写待合成的音频文本内容，用于试听效果，类型切换不需要保存即可生效。").style("width:350px;")
                    button_tts_common_audio_synthesis = ui.button('试听', on_click=lambda: tts_common_audio_synthesis(), color=button_internal_color).style(button_internal_css)
                tts_common_audio_card = ui.card()
                with tts_common_audio_card.style(card_css):
                    with ui.row():
                        ui.label("此处显示生成的音频，仅显示最新合成的音频，可以在此操作删除合成的音频")

            if config.get("webui", "show_card", "tts", "gpt_sovits"): 
                with ui.card().style(card_css):
                    ui.label("GPT-SoVITS")
                    with ui.row():
                        select_gpt_sovits_type = ui.select(
                            label='API类型', 
                            options={
                                'api':'api', 
                                'api_0322':'api_0322', 
                                'api_0706':'api_0706', 
                                'v2_api_0821': 'v2_api_0821', 
                                'webtts':'WebTTS', 
                                'gradio_0322':'gradio_0322',
                            }, 
                            value=config.get("gpt_sovits", "type")
                        ).style("width:100px;")
                        input_gpt_sovits_gradio_ip_port = ui.input(
                            label='Gradio API地址', 
                            value=config.get("gpt_sovits", "gradio_ip_port"), 
                            placeholder='官方webui程序启动后gradio监听的地址',
                            validation={
                                '请输入正确格式的URL': lambda value: common.is_url_check(value),
                            }
                        ).style("width:200px;")
                        input_gpt_sovits_api_ip_port = ui.input(
                            label='API地址（http）', 
                            value=config.get("gpt_sovits", "api_ip_port"), 
                            placeholder='官方API程序启动后监听的地址',
                            validation={
                                '请输入正确格式的URL': lambda value: common.is_url_check(value),
                            }
                        ).style("width:200px;")
                        
                    
                    with ui.row():
                        input_gpt_sovits_gpt_model_path = ui.input(
                            label='GPT模型路径', 
                            value=config.get("gpt_sovits", "gpt_model_path"), 
                            placeholder='GPT模型路径，填绝对路径'
                        ).style("width:300px;")
                        input_gpt_sovits_sovits_model_path = ui.input(label='SOVITS模型路径', value=config.get("gpt_sovits", "sovits_model_path"), placeholder='SOVITS模型路径，填绝对路径').style("width:300px;")
                        button_gpt_sovits_set_model = ui.button('加载模型', on_click=lambda: gpt_sovits_set_model(), color=button_internal_color).style(button_internal_css)
                    
                    with ui.card().style(card_css):
                        ui.label("api")
                        with ui.row():
                            input_gpt_sovits_ref_audio_path = ui.input(label='参考音频路径', value=config.get("gpt_sovits", "ref_audio_path"), placeholder='参考音频路径，建议填绝对路径').style("width:300px;")
                            input_gpt_sovits_prompt_text = ui.input(label='参考音频的文本', value=config.get("gpt_sovits", "prompt_text"), placeholder='参考音频的文本').style("width:200px;")
                            select_gpt_sovits_prompt_language = ui.select(
                                label='参考音频的语种', 
                                options={'中文':'中文', '日文':'日文', '英文':'英文'}, 
                                value=config.get("gpt_sovits", "prompt_language")
                            ).style("width:150px;")
                            select_gpt_sovits_language = ui.select(
                                label='需要合成的语种', 
                                options={'自动识别':'自动识别', '中文':'中文', '日文':'日文', '英文':'英文'}, 
                                value=config.get("gpt_sovits", "language")
                            ).style("width:150px;")
                            select_gpt_sovits_cut = ui.select(
                                label='语句切分', 
                                options={
                                    '不切':'不切', 
                                    '凑四句一切':'凑四句一切', 
                                    '凑50字一切':'凑50字一切', 
                                    '按中文句号。切':'按中文句号。切', 
                                    '按英文句号.切':'按英文句号.切',
                                    '按标点符号切':'按标点符号切'
                                }, 
                                value=config.get("gpt_sovits", "cut")
                            ).style("width:200px;")
                    
                    with ui.card().style(card_css):
                        ui.label("api_0322 | gradio_0322")
                        with ui.row():
                            input_gpt_sovits_api_0322_ref_audio_path = ui.input(label='参考音频路径', value=config.get("gpt_sovits", "api_0322", "ref_audio_path"), placeholder='参考音频路径，建议填绝对路径').style("width:300px;")
                            input_gpt_sovits_api_0322_prompt_text = ui.input(label='参考音频的文本', value=config.get("gpt_sovits", "api_0322", "prompt_text"), placeholder='参考音频的文本').style("width:200px;")
                            select_gpt_sovits_api_0322_prompt_lang = ui.select(
                                label='参考音频的语种', 
                                options={'中文':'中文', '日文':'日文', '英文':'英文'}, 
                                value=config.get("gpt_sovits", "api_0322", "prompt_lang")
                            ).style("width:150px;")
                            select_gpt_sovits_api_0322_text_lang = ui.select(
                                label='需要合成的语种', 
                                options={
                                    '自动识别':'自动识别', 
                                    '中文':'中文', 
                                    '日文':'日文', 
                                    '英文':'英文', 
                                    '中英混合': '中英混合',
                                    '日英混合': '日英混合',
                                    '多语种混合': '多语种混合',
                                }, 
                                value=config.get("gpt_sovits", "api_0322", "text_lang")
                            ).style("width:150px;")
                            select_gpt_sovits_api_0322_text_split_method = ui.select(
                                label='语句切分', 
                                options={
                                    '不切':'不切', 
                                    '凑四句一切':'凑四句一切', 
                                    '凑50字一切':'凑50字一切', 
                                    '按中文句号。切':'按中文句号。切', 
                                    '按英文句号.切':'按英文句号.切',
                                    '按标点符号切':'按标点符号切'
                                }, 
                                value=config.get("gpt_sovits", "api_0322", "text_split_method")
                            ).style("width:200px;")
                        with ui.row():
                            input_gpt_sovits_api_0322_top_k = ui.input(label='top_k', value=config.get("gpt_sovits", "api_0322", "top_k"), placeholder='top_k').style("width:100px;")
                            input_gpt_sovits_api_0322_top_p = ui.input(label='top_p', value=config.get("gpt_sovits", "api_0322", "top_p"), placeholder='top_p').style("width:100px;")
                            input_gpt_sovits_api_0322_temperature = ui.input(label='temperature', value=config.get("gpt_sovits", "api_0322", "temperature"), placeholder='temperature').style("width:100px;")
                            input_gpt_sovits_api_0322_batch_size = ui.input(label='batch_size', value=config.get("gpt_sovits", "api_0322", "batch_size"), placeholder='batch_size').style("width:100px;")
                            input_gpt_sovits_api_0322_speed_factor = ui.input(label='speed_factor', value=config.get("gpt_sovits", "api_0322", "speed_factor"), placeholder='speed_factor').style("width:100px;")
                            input_gpt_sovits_api_0322_fragment_interval = ui.input(label='分段间隔(秒)', value=config.get("gpt_sovits", "api_0322", "fragment_interval"), placeholder='fragment_interval').style("width:100px;")
                            switch_gpt_sovits_api_0322_split_bucket = ui.switch('split_bucket', value=config.get("gpt_sovits", "api_0322", "split_bucket")).style(switch_internal_css)
                            switch_gpt_sovits_api_0322_return_fragment = ui.switch('return_fragment', value=config.get("gpt_sovits", "api_0322", "return_fragment")).style(switch_internal_css)
                    
                    with ui.card().style(card_css):
                        ui.label("api_0706")
                        with ui.row():
                            input_gpt_sovits_api_0706_refer_wav_path = ui.input(label='参考音频路径', value=config.get("gpt_sovits", "api_0706", "refer_wav_path"), placeholder='参考音频路径，建议填绝对路径').style("width:300px;")
                            input_gpt_sovits_api_0706_prompt_text = ui.input(label='参考音频的文本', value=config.get("gpt_sovits", "api_0706", "prompt_text"), placeholder='参考音频的文本').style("width:200px;")
                            select_gpt_sovits_api_0706_prompt_language = ui.select(
                                label='参考音频的语种', 
                                options={'中文':'中文', '日文':'日文', '英文':'英文'}, 
                                value=config.get("gpt_sovits", "api_0706", "prompt_language")
                            ).style("width:150px;")
                            select_gpt_sovits_api_0706_text_language = ui.select(
                                label='需要合成的语种', 
                                options={
                                    '自动识别':'自动识别', 
                                    '中文':'中文', 
                                    '日文':'日文', 
                                    '英文':'英文', 
                                    '中英混合': '中英混合',
                                    '日英混合': '日英混合',
                                    '多语种混合': '多语种混合',
                                }, 
                                value=config.get("gpt_sovits", "api_0706", "text_language")
                            ).style("width:150px;")
                            input_gpt_sovits_api_0706_cut_punc = ui.input(label='文本切分', value=config.get("gpt_sovits", "api_0706", "cut_punc"), placeholder='文本切分符号设定, 符号范围,.;?!、，。？！；：…').style("width:200px;")
                    
                    with ui.card().style(card_css):
                        ui.label("v2_api_0821")
                        with ui.row():
                            input_gpt_sovits_v2_api_0821_ref_audio_path = ui.input(label='参考音频路径', value=config.get("gpt_sovits", "v2_api_0821", "ref_audio_path"), placeholder='参考音频路径，建议填绝对路径').style("width:300px;")
                            input_gpt_sovits_v2_api_0821_prompt_text = ui.input(label='参考音频的文本', value=config.get("gpt_sovits", "v2_api_0821", "prompt_text"), placeholder='参考音频的文本').style("width:200px;")
                            select_gpt_sovits_v2_api_0821_prompt_lang = ui.select(
                                label='参考音频的语种', 
                                options={'zh':'中文', 'ja':'日文', 'en':'英文'}, 
                                value=config.get("gpt_sovits", "v2_api_0821", "prompt_lang")
                            ).style("width:150px;")
                            select_gpt_sovits_v2_api_0821_text_lang = ui.select(
                                label='需要合成的语种', 
                                options={
                                    "all_zh": "中文",
                                    "all_yue": "粤语",
                                    "en": "英文",
                                    "all_ja": "日文",
                                    "all_ko": "韩文",
                                    "zh": "中英混合",
                                    "yue": "粤英混合",
                                    "ja": "日英混合",
                                    "ko": "韩英混合",
                                    "auto": "多语种混合",    #多语种启动切分识别语种
                                    "auto_yue": "多语种混合(粤语)",
                                }, 
                                value=config.get("gpt_sovits", "v2_api_0821", "text_lang")
                            ).style("width:150px;")
                            select_gpt_sovits_v2_api_0821_text_split_method = ui.select(
                                label='语句切分', 
                                options={
                                    'cut0':'不切', 
                                    'cut1':'凑四句一切', 
                                    'cut2':'凑50字一切', 
                                    'cut3':'按中文句号。切', 
                                    'cut4':'按英文句号.切',
                                    'cut5':'按标点符号切'
                                }, 
                                value=config.get("gpt_sovits", "v2_api_0821", "text_split_method")
                            ).style("width:200px;")
                        with ui.row():
                            input_gpt_sovits_v2_api_0821_top_k = ui.input(label='top_k', value=config.get("gpt_sovits", "v2_api_0821", "top_k"), placeholder='top_k').style("width:100px;")
                            input_gpt_sovits_v2_api_0821_top_p = ui.input(label='top_p', value=config.get("gpt_sovits", "v2_api_0821", "top_p"), placeholder='top_p').style("width:100px;")
                            input_gpt_sovits_v2_api_0821_temperature = ui.input(label='temperature', value=config.get("gpt_sovits", "v2_api_0821", "temperature"), placeholder='temperature').style("width:100px;")
                            input_gpt_sovits_v2_api_0821_batch_size = ui.input(label='batch_size', value=config.get("gpt_sovits", "v2_api_0821", "batch_size"), placeholder='batch_size').style("width:100px;")
                            input_gpt_sovits_v2_api_0821_batch_threshold = ui.input(label='batch_threshold', value=config.get("gpt_sovits", "v2_api_0821", "batch_threshold"), placeholder='batch_threshold').style("width:100px;")
                            switch_gpt_sovits_v2_api_0821_split_bucket = ui.switch('split_bucket', value=config.get("gpt_sovits", "v2_api_0821", "split_bucket")).style(switch_internal_css)
                            input_gpt_sovits_v2_api_0821_speed_factor = ui.input(label='speed_factor', value=config.get("gpt_sovits", "v2_api_0821", "speed_factor"), placeholder='speed_factor').style("width:100px;")
                            input_gpt_sovits_v2_api_0821_fragment_interval = ui.input(label='分段间隔(秒)', value=config.get("gpt_sovits", "v2_api_0821", "fragment_interval"), placeholder='fragment_interval').style("width:100px;")
                            input_gpt_sovits_v2_api_0821_seed = ui.input(label='seed', value=config.get("gpt_sovits", "v2_api_0821", "seed"), placeholder='seed').style("width:100px;")
                            input_gpt_sovits_v2_api_0821_media_type = ui.input(label='media_type', value=config.get("gpt_sovits", "v2_api_0821", "media_type"), placeholder='media_type').style("width:100px;")
                            switch_gpt_sovits_v2_api_0821_parallel_infer = ui.switch('parallel_infer', value=config.get("gpt_sovits", "v2_api_0821", "parallel_infer")).style(switch_internal_css)
                            input_gpt_sovits_v2_api_0821_repetition_penalty = ui.input(label='repetition_penalty', value=config.get("gpt_sovits", "v2_api_0821", "repetition_penalty"), placeholder='repetition_penalty').style("width:100px;")
                            

                    with ui.card().style(card_css):
                        ui.label("WebTTS相关配置")
                        with ui.row():
                            select_gpt_sovits_webtts_version = ui.select(
                                label='版本', 
                                options={
                                    '1':'1', 
                                    '1.4':'1.4', 
                                    '2':'2'
                                }, 
                                value=config.get("gpt_sovits", "webtts", "version")
                            ).style("width:80px;")
                            input_gpt_sovits_webtts_api_ip_port = ui.input(label='API地址', value=config.get("gpt_sovits", "webtts", "api_ip_port"), placeholder='API监听地址').style("width:200px;")
                            input_gpt_sovits_webtts_spk = ui.input(label='音色', value=config.get("gpt_sovits", "webtts", "spk"), placeholder='音色').style("width:100px;")
                            select_gpt_sovits_webtts_lang = ui.select(
                                label='语言', 
                                options={
                                    'zh':'中文', 
                                    'en':'英文', 
                                    'jp':'日文'
                                }, 
                                value=config.get("gpt_sovits", "webtts", "lang")
                            ).style("width:100px;")
                            input_gpt_sovits_webtts_speed = ui.input(label='语速', value=config.get("gpt_sovits", "webtts", "speed"), placeholder='语速').style("width:100px;")
                            input_gpt_sovits_webtts_emotion = ui.input(label='情感', value=config.get("gpt_sovits", "webtts", "emotion"), placeholder='情感').style("width:100px;")
        
        with ui.tab_panel(svc_page).style(tab_panel_css):
            if config.get("webui", "show_card", "svc", "so_vits_svc"):
                with ui.card().style(card_css):
                    ui.label("SO-VITS-SVC")
                    with ui.row():
                        switch_so_vits_svc_enable = ui.switch('启用', value=config.get("so_vits_svc", "enable")).style(switch_internal_css)
                        input_so_vits_svc_config_path = ui.input(label='配置文件路径', placeholder='模型配置文件config.json的路径', value=config.get("so_vits_svc", "config_path"))
                        input_so_vits_svc_config_path.style("width:400px")
                    with ui.grid(columns=2):
                        input_so_vits_svc_api_ip_port = ui.input(
                            label='API地址', 
                            placeholder='flask_api_full_song服务运行的ip端口，例如：http://127.0.0.1:1145', 
                            value=config.get("so_vits_svc", "api_ip_port"),
                            validation={
                                '请输入正确格式的URL': lambda value: common.is_url_check(value),
                            }
                        )
                        input_so_vits_svc_api_ip_port.style("width:400px")
                        input_so_vits_svc_spk = ui.input(label='说话人', placeholder='说话人，需要和配置文件内容对应', value=config.get("so_vits_svc", "spk"))
                        input_so_vits_svc_spk.style("width:400px") 
                        input_so_vits_svc_tran = ui.input(label='音调', placeholder='音调设置，默认为1', value=config.get("so_vits_svc", "tran"))
                        input_so_vits_svc_tran.style("width:300px")
                        input_so_vits_svc_wav_format = ui.input(label='输出音频格式', placeholder='音频合成后输出的格式', value=config.get("so_vits_svc", "wav_format"))
                        input_so_vits_svc_wav_format.style("width:300px") 
        with ui.tab_panel(visual_body_page).style(tab_panel_css):
            if config.get("webui", "show_card", "visual_body", "metahuman_stream"):
                with ui.card().style(card_css):
                    ui.label("metahuman_stream")
                    with ui.row():
                        select_metahuman_stream_type = ui.select(
                            label='类型', 
                            options={'ernerf': 'ernerf', 'musetalk': 'musetalk', 'wav2lip': 'wav2lip'}, 
                            value=config.get("metahuman_stream", "type")
                        ).style("width:100px;")
                        input_metahuman_stream_api_ip_port = ui.input(
                            label='API地址', 
                            value=config.get("metahuman_stream", "api_ip_port"), 
                            placeholder='metahuman_stream应用启动API后，监听的ip和端口',
                            validation={
                                '请输入正确格式的URL': lambda value: common.is_url_check(value),
                            }
                        )

        with ui.tab_panel(copywriting_page).style(tab_panel_css):
            with ui.row():
                switch_copywriting_auto_play = ui.switch('自动播放', value=config.get("copywriting", "auto_play")).style(switch_internal_css)
                switch_copywriting_random_play = ui.switch('音频随机播放', value=config.get("copywriting", "random_play")).style(switch_internal_css)
                input_copywriting_audio_interval = ui.input(label='音频播放间隔', value=config.get("copywriting", "audio_interval"), placeholder='文案音频播放之间的间隔时间。就是前一个文案播放完成后，到后一个文案开始播放之间的间隔时间。').tooltip('文案音频播放之间的间隔时间。就是前一个文案播放完成后，到后一个文案开始播放之间的间隔时间。')
                input_copywriting_switching_interval = ui.input(label='音频切换间隔', value=config.get("copywriting", "switching_interval"), placeholder='文案音频切换到弹幕音频的切换间隔时间（反之一样）。\n就是在播放文案时，有弹幕触发并合成完毕，此时会暂停文案播放，然后等待这个间隔时间后，再播放弹幕回复音频。').tooltip('n就是在播放文案时，有弹幕触发并合成完毕，此时会暂停文案播放，然后等待这个间隔时间后，再播放弹幕回复音频。')
            with ui.row():
                input_copywriting_index = ui.input(label='文案索引', value="", placeholder='文案组的排序号，就是说第一个组是1，第二个组是2，以此类推。请填写纯正整数')
                button_copywriting_add = ui.button('增加文案组', on_click=copywriting_add, color=button_internal_color).style(button_internal_css)
                button_copywriting_del = ui.button('删除文案组', on_click=lambda: copywriting_del(input_copywriting_index.value), color=button_internal_color).style(button_internal_css)

            copywriting_config_var = {}
            copywriting_config_card = ui.card()
            for index, copywriting_config in enumerate(config.get("copywriting", "config")):
                with copywriting_config_card.style(card_css):
                    with ui.row():
                        copywriting_config_var[str(5 * index)] = ui.input(label=f"文案存储路径#{index + 1}", value=copywriting_config["file_path"], placeholder='文案文件存储路径。不建议更改。').style("width:200px;").tooltip('文案文件存储路径。不建议更改。')
                        copywriting_config_var[str(5 * index + 1)] = ui.input(label=f"音频存储路径#{index + 1}", value=copywriting_config["audio_path"], placeholder='文案音频文件存储路径。不建议更改。').style("width:200px;").tooltip('文案音频文件存储路径。不建议更改。')
                        copywriting_config_var[str(5 * index + 2)] = ui.input(label=f"连续播放数#{index + 1}", value=copywriting_config["continuous_play_num"], placeholder='文案播放列表中连续播放的音频文件个数，如果超过了这个个数就会切换下一个文案列表').style("width:200px;").tooltip('文案播放列表中连续播放的音频文件个数，如果超过了这个个数就会切换下一个文案列表')
                        copywriting_config_var[str(5 * index + 3)] = ui.input(label=f"连续播放时间#{index + 1}", value=copywriting_config["max_play_time"], placeholder='文案播放列表中连续播放音频的时长，如果超过了这个时长就会切换下一个文案列表').style("width:200px;").tooltip('文案播放列表中连续播放音频的时长，如果超过了这个时长就会切换下一个文案列表')
                        copywriting_config_var[str(5 * index + 4)] = ui.textarea(label=f"播放列表#{index + 1}", value=textarea_data_change(copywriting_config["play_list"]), placeholder='此处填写需要播放的音频文件全名，填写完毕后点击 保存配置。文件全名从音频列表中复制，换行分隔，请勿随意填写').style("width:500px;").tooltip('此处填写需要播放的音频文件全名，填写完毕后点击 保存配置。文件全名从音频列表中复制，换行分隔，请勿随意填写')

            with ui.card().style(card_css):
                ui.label("文案音频合成")
                with ui.row():
                    input_copywriting_text_path = ui.input(label='文案文本路径', value=config.get("copywriting", "text_path"), placeholder='待合成的文案文本文件的路径').style("width:250px;").tooltip('待合成的文案文本文件的路径')
                    button_copywriting_text_load = ui.button('加载文本', on_click=copywriting_text_load, color=button_internal_color).style(button_internal_css)
                    input_copywriting_audio_save_path = ui.input(label='音频存储路径', value=config.get("copywriting", "audio_save_path"), placeholder='音频合成后存储的路径').style("width:250px;").tooltip('音频合成后存储的路径')
                    # input_copywriting_chunking_stop_time = ui.input(label='断句停顿时长', value=config.get("copywriting", "chunking_stop_time"), placeholder='自动根据标点断句后，2个句子之间的无声时长').style("width:150px;")
                    select_copywriting_audio_synthesis_type = ui.select(
                        label='语音合成', 
                        options=audio_synthesis_type_options, 
                        value=config.get("copywriting", "audio_synthesis_type")
                    ).style("width:200px;")
                with ui.row():
                    textarea_copywriting_text = ui.textarea(label='文案文本', value='', placeholder='此处对需要合成文案音频的文本内容进行编辑。文案会自动根据逻辑进行切分，然后根据配置合成完整的一个音频文件。').style("width:1000px;").tooltip('此处对需要合成文案音频的文本内容进行编辑。文案会自动根据逻辑进行切分，然后根据配置合成完整的一个音频文件。')
                with ui.row():
                    button_copywriting_save_text = ui.button('保存文案', on_click=copywriting_save_text, color=button_internal_color).style(button_internal_css)
                    button_copywriting_audio_synthesis = ui.button('合成音频', on_click=lambda: copywriting_audio_synthesis(), color=button_internal_color).style(button_internal_css)
                copywriting_audio_card = ui.card()
                with copywriting_audio_card.style(card_css):
                    with ui.row():
                        ui.label("此处显示生成的文案音频，仅显示最新合成的文案音频，可以在此操作删除合成的音频")
        with ui.tab_panel(integral_page).style(tab_panel_css):
            with ui.card().style(card_css):
                ui.label("通用")
                with ui.grid(columns=3):
                    switch_integral_enable = ui.switch('启用', value=config.get("integral", "enable")).style(switch_internal_css)
            with ui.card().style(card_css):
                ui.label("签到")
                with ui.grid(columns=3):
                    switch_integral_sign_enable = ui.switch('启用', value=config.get("integral", "sign", "enable")).style(switch_internal_css)
                    input_integral_sign_get_integral = ui.input(label='获得积分数', value=config.get("integral", "sign", "get_integral"), placeholder='签到成功可以获得的积分数，请填写正整数！')
                    textarea_integral_sign_cmd = ui.textarea(label='命令', value=textarea_data_change(config.get("integral", "sign", "cmd")), placeholder='弹幕发送以下命令可以触发签到功能，换行分隔命令')
                with ui.card().style(card_css):
                    ui.label("文案")
                    integral_sign_copywriting_var = {}
                    for index, integral_sign_copywriting in enumerate(config.get("integral", "sign", "copywriting")):
                        with ui.grid(columns=2):
                            integral_sign_copywriting_var[str(2 * index)] = ui.input(label=f"签到数区间#{index}", value=integral_sign_copywriting["sign_num_interval"], placeholder='限制在此区间内的签到数来触发对应的文案，用-号来进行区间划分，包含边界值')
                            integral_sign_copywriting_var[str(2 * index + 1)] = ui.textarea(label=f"文案#{index}", value=textarea_data_change(integral_sign_copywriting["copywriting"]), placeholder='在此签到区间内，触发的文案内容，换行分隔').style("width:400px;")
            with ui.card().style(card_css):
                ui.label("礼物")
                with ui.grid(columns=3):
                    switch_integral_gift_enable = ui.switch('启用', value=config.get("integral", "gift", "enable")).style(switch_internal_css)
                    input_integral_gift_get_integral_proportion = ui.input(label='获得积分比例', value=config.get("integral", "gift", "get_integral_proportion"), placeholder='此比例和礼物真实金额（元）挂钩，默认就是1元=10积分')
                with ui.card().style(card_css):
                    ui.label("文案")
                    integral_gift_copywriting_var = {}
                    for index, integral_gift_copywriting in enumerate(config.get("integral", "gift", "copywriting")):
                        with ui.grid(columns=2):
                            integral_gift_copywriting_var[str(2 * index)] = ui.input(label=f"礼物价格区间#{index}", value=integral_gift_copywriting["gift_price_interval"], placeholder='限制在此区间内的礼物价格来触发对应的文案，用-号来进行区间划分，包含边界值')
                            integral_gift_copywriting_var[str(2 * index + 1)] = ui.textarea(label=f"文案#{index}", value=textarea_data_change(integral_gift_copywriting["copywriting"]), placeholder='在此礼物区间内，触发的文案内容，换行分隔').style("width:400px;")
            with ui.card().style(card_css):
                ui.label("入场")
                with ui.grid(columns=3):
                    switch_integral_entrance_enable = ui.switch('启用', value=config.get("integral", "entrance", "enable")).style(switch_internal_css)
                    input_integral_entrance_get_integral = ui.input(label='获得积分数', value=config.get("integral", "entrance", "get_integral"), placeholder='签到成功可以获得的积分数，请填写正整数！')
                with ui.card().style(card_css):
                    ui.label("文案")
                    integral_entrance_copywriting_var = {}
                    for index, integral_entrance_copywriting in enumerate(config.get("integral", "entrance", "copywriting")):
                        with ui.grid(columns=2):
                            integral_entrance_copywriting_var[str(2 * index)] = ui.input(label=f"入场数区间#{index}", value=integral_entrance_copywriting["entrance_num_interval"], placeholder='限制在此区间内的入场数来触发对应的文案，用-号来进行区间划分，包含边界值')
                            integral_entrance_copywriting_var[str(2 * index + 1)] = ui.textarea(label=f"文案#{index}", value=textarea_data_change(integral_entrance_copywriting["copywriting"]), placeholder='在此入场区间内，触发的文案内容，换行分隔').style("width:400px;")
            with ui.card().style(card_css):
                ui.label("增删改查")
                with ui.card().style(card_css):
                    ui.label("查询")
                    with ui.grid(columns=3):
                        switch_integral_crud_query_enable = ui.switch('启用', value=config.get("integral", "crud", "query", "enable")).style(switch_internal_css)
                        textarea_integral_crud_query_cmd = ui.textarea(label="命令", value=textarea_data_change(config.get("integral", "crud", "query", "cmd")), placeholder='弹幕发送以下命令可以触发查询功能，换行分隔命令')
                        textarea_integral_crud_query_copywriting = ui.textarea(label="文案", value=textarea_data_change(config.get("integral", "crud", "query", "copywriting")), placeholder='触发查询功能后返回的文案内容，换行分隔命令').style("width:400px;")

        with ui.tab_panel(talk_page).style(tab_panel_css): 
            with ui.row().style("position:fixed; top: 100px; right: 20px;"):
                with ui.expansion('聊天记录', icon="question_answer", value=True):
                    scroll_area_chat_box = ui.scroll_area().style("width:500px; height:700px;")
                

            with ui.row():
                switch_talk_key_listener_enable = ui.switch('启用按键监听', value=config.get("talk", "key_listener_enable")).style(switch_internal_css).tooltip("启用后，可以通过键盘单击下放配置的录音按键，启动语音识别对话功能")
                switch_talk_direct_run_talk = ui.switch('直接语音对话', value=config.get("talk", "direct_run_talk")).style(switch_internal_css).tooltip("如果启用了，将在首次运行时直接进行语音识别，而不需手动点击开始按键。针对有些系统按键无法触发的情况下，配合连续对话和唤醒词使用")
                
                audio_device_info_list = common.get_all_audio_device_info("in")
                logger.info(f"声卡输入设备={audio_device_info_list}")
                audio_device_info_dict = {str(device['device_index']): device['device_info'] for device in audio_device_info_list}

                logger.debug(f"声卡输入设备={audio_device_info_dict}")

                select_talk_device_index = ui.select(
                    label='声卡输入设备', 
                    options=audio_device_info_dict, 
                    value=config.get("talk", "device_index")
                ).style("width:300px;").tooltip('这就是语言对话输入的声卡（麦克风），选择你对应的麦克风即可，如果需要监听电脑声卡可以配合虚拟声卡来实现')
                
                switch_talk_no_recording_during_playback = ui.switch('播放中不进行录音', value=config.get("talk", "no_recording_during_playback")).style(switch_internal_css).tooltip('AI在播放音频的过程中不进行录音，从而防止麦克风和扬声器太近导致的循环录音的问题')
                input_talk_no_recording_during_playback_sleep_interval = ui.input(label='播放中不进行录音的检测间隔(秒)', value=config.get("talk", "no_recording_during_playback_sleep_interval"), placeholder='这个值设置正常不需要太大，因为在启用了“播放中不进行录音”时，不会出现录音到AI说的话的情况，设置太大会导致恢复录音的时间变慢').style("width:200px;").tooltip('这个值设置正常不需要太大，因为不会出现录音到AI说的话的情况')
                
                input_talk_username = ui.input(label='你的名字', value=config.get("talk", "username"), placeholder='日志中你的名字，暂时没有实质作用').style("width:200px;")
                switch_talk_continuous_talk = ui.switch('连续对话', value=config.get("talk", "continuous_talk")).style(switch_internal_css).tooltip('仅需按一次录音按键，后续就不需要按了，会自动根据沉默阈值切分等待后，继续录音')
            with ui.row():
                data_json = {}
                for line in ["google", "baidu", "faster_whisper", "sensevoice"]:
                    data_json[line] = line
                select_talk_type = ui.select(
                    label='录音类型', 
                    options=data_json, 
                    value=config.get("talk", "type")
                ).style("width:200px;").tooltip('选择使用的STT类型')

                with open('data/keyboard.txt', 'r') as file:
                    file_content = file.read()
                # 按行分割内容，并去除每行末尾的换行符
                lines = file_content.strip().split('\n')
                data_json = {}
                for line in lines:
                    data_json[line] = line
                select_talk_trigger_key = ui.select(
                    label='录音按键', 
                    options=data_json, 
                    value=config.get("talk", "trigger_key"),
                    with_input=True,
                    clearable=True
                ).style("width:200px;").tooltip('按压此按键就可以触发录音了，按一次就行了')
                select_talk_stop_trigger_key = ui.select(
                    label='停录按键', 
                    options=data_json, 
                    value=config.get("talk", "stop_trigger_key"),
                    with_input=True,
                    clearable=True
                ).style("width:200px;").tooltip('按压此按键就可以停止录音了，按一次就行了')

                input_talk_volume_threshold = ui.input(label='音量阈值', value=config.get("talk", "volume_threshold"), placeholder='音量阈值，指的是触发录音的起始音量值，请根据自己的麦克风进行微调到最佳').style("width:100px;").tooltip('音量阈值，指的是触发录音的起始音量值，请根据自己的麦克风进行微调到最佳')
                input_talk_silence_threshold = ui.input(label='停录计数', value=config.get("talk", "silence_threshold"), placeholder='停录计数，指的是音量低于起始值的计数，这个值越大，切分音频越慢，即需要等待更长时间才会停止录音，但也不能太小，不然说一半就停了').style("width:100px;").tooltip('沉默阈值，指的是触发停止路径的最低音量值，请根据自己的麦克风进行微调到最佳')
                input_talk_silence_CHANNELS = ui.input(label='CHANNELS', value=config.get("talk", "CHANNELS"), placeholder='录音用的参数').style("width:100px;")
                input_talk_silence_RATE = ui.input(label='RATE', value=config.get("talk", "RATE"), placeholder='录音用的参数').style("width:100px;")
                switch_talk_show_chat_log = ui.switch('聊天记录', value=config.get("talk", "show_chat_log")).style(switch_internal_css)
            
            with ui.row():
                textarea_talk_chat_box = ui.textarea(label='聊天框-和AI对话', value="", placeholder='此处填写对话内容可以直接进行对话（前面配置好聊天模式，记得运行先）').style("width:500px;").tooltip("此处填写对话内容可以直接进行对话（前面配置好聊天模式，记得运行先）")
                
                '''
                    聊天页相关的函数
                '''

                # 发送 聊天框内容
                async def talk_chat_box_send():
                    global running_flag
                    
                    if running_flag != 1:
                        ui.notify(position="top", type="info", message="请先点击“一键运行”，然后再进行聊天")
                        return

                    # 获取用户名和文本内容
                    username = input_talk_username.value
                    content = textarea_talk_chat_box.value

                    # 清空聊天框
                    textarea_talk_chat_box.value = ""

                    data = {
                        "type": "comment",
                        "data": {
                            "type": "comment",
                            "platform": "webui",
                            "username": username,
                            "content": content
                        }
                    }

                    logger.debug(f"data={data}")

                    main_api_ip = "127.0.0.1" if config.get("api_ip") == "0.0.0.0" else config.get("api_ip")
                    await common.send_async_request(f'http://{main_api_ip}:{config.get("api_port")}/send', "POST", data)


                # 发送 聊天框内容 进行复读
                async def talk_chat_box_reread(insert_index=-1, type="reread"):
                    global running_flag

                    if running_flag != 1:
                        ui.notify(position="top", type="warning", message="请先点击“一键运行”，然后再进行聊天")
                        return
                    
                    # 获取用户名和文本内容
                    username = input_talk_username.value
                    content = textarea_talk_chat_box.value

                    # 清空聊天框
                    textarea_talk_chat_box.value = ""

                    if insert_index == -1:
                        data = {
                            "type": type,
                            "data": {
                                "type": type,
                                "username": username,
                                "content": content
                            }
                        }
                    else:

                        data = {
                            "type": type,
                            "data": {
                                "type": type,
                                "username": username,
                                "content": content,
                                "insert_index": insert_index
                            }
                        }

                    if switch_talk_show_chat_log.value:
                        show_chat_log_json = {
                            "type": "llm",
                            "data": {
                                "type": type,
                                "username": username,
                                "content_type": "question",
                                "content": content,
                                "timestamp": common.get_bj_time(0)
                            }
                        }
                        data_handle_show_chat_log(show_chat_log_json)

                    main_api_ip = "127.0.0.1" if config.get("api_ip") == "0.0.0.0" else config.get("api_ip")
                    await common.send_async_request(f'http://{main_api_ip}:{config.get("api_port")}/send', "POST", data)

                # 发送 聊天框内容 进行LLM的调教
                async def talk_chat_box_tuning():
                    global running_flag

                    if running_flag != 1:
                        ui.notify(position="top", type="warning", message="请先点击“一键运行”，然后再进行聊天")
                        return
                    
                    # 获取用户名和文本内容
                    username = input_talk_username.value
                    content = textarea_talk_chat_box.value

                    # 清空聊天框
                    textarea_talk_chat_box.value = ""

                    data = {
                        "type": "tuning",
                        "data": {
                            "type": "tuning",
                            "username": username,
                            "content": content
                        }
                    }

                    main_api_ip = "127.0.0.1" if config.get("api_ip") == "0.0.0.0" else config.get("api_ip")
                    await common.send_async_request(f'http://{main_api_ip}:{config.get("api_port")}/send', "POST", data)

                button_talk_chat_box_send = ui.button('发送', on_click=lambda: talk_chat_box_send(), color=button_internal_color).style(button_internal_css).tooltip("发送文本给LLM，模拟弹幕触发操作")
                button_talk_chat_box_reread = ui.button('直接复读', on_click=lambda: talk_chat_box_reread(), color=button_internal_color).style(button_internal_css).tooltip("发送文本给内部机制，触发TTS 复读类型的消息")
                button_talk_chat_box_tuning = ui.button('调教', on_click=lambda: talk_chat_box_tuning(), color=button_internal_color).style(button_internal_css).tooltip("发送文本给LLM，但不会进行TTS等操作")
                button_talk_chat_box_reread_first = ui.button('直接复读-插队首', on_click=lambda: talk_chat_box_reread(0, "reread_top_priority"), color=button_internal_color).style(button_internal_css).tooltip("最高优先级 发送文本给内部机制，触发TTS 直接复读类型的消息")
        
            with ui.expansion('对话打断', icon="settings", value=True).classes('w-2/3'):
                with ui.row():
                    switch_talk_interrupt_talk_enable = ui.switch('启用', value=config.get("talk", "interrupt_talk", "enable")).style(switch_internal_css)
                    textarea_talk_interrupt_talk_keywords = ui.textarea(
                        label='打断关键词', 
                        placeholder='如：等一下、住嘴 多个请换行分隔', 
                        value=textarea_data_change(config.get("talk", "interrupt_talk", "keywords"))
                    ).style("width:200px;").tooltip("打断关键词，当语句中包含出现这些词时，会中断对话，具体清除内容根据清除类型自定义")
                    
                    with ui.card().style(card_css):
                        ui.label("清除类型")
                        with ui.row(): 
                            talk_interrupt_clean_type_list = [
                                "message_queue", 
                                "voice_tmp_path_queue",
                                "audio_play"
                            ]
                            talk_interrupt_clean_type_mapping = {
                                "message_queue": "待合成消息队列",
                                "voice_tmp_path_queue": "待播放音频队列",
                                "audio_play": "正在播放中的音频",
                            }
                            talk_interrupt_clean_type_var = {}
                            
                            for index, talk_interrupt_clean_type in enumerate(talk_interrupt_clean_type_list):
                                if talk_interrupt_clean_type in config.get("talk", "interrupt_talk", "clean_type"):
                                    talk_interrupt_clean_type_var[str(index)] = ui.checkbox(
                                        text=talk_interrupt_clean_type_mapping[talk_interrupt_clean_type], 
                                        value=True
                                    )
                                else:
                                    talk_interrupt_clean_type_var[str(index)] = ui.checkbox(
                                        text=talk_interrupt_clean_type_mapping[talk_interrupt_clean_type], 
                                        value=False
                                    ) 
            with ui.expansion('语音唤醒与睡眠', icon="settings", value=True).classes('w-2/3'):
                with ui.row():
                    switch_talk_wakeup_sleep_enable = ui.switch('启用', value=config.get("talk", "wakeup_sleep", "enable")).style(switch_internal_css)
                    select_talk_wakeup_sleep_mode = ui.select(
                        label='唤醒模式', 
                        options={"长期唤醒": "长期唤醒", "单次唤醒": "单次唤醒"}, 
                        value=config.get("talk", "wakeup_sleep", "mode")
                    ).style("width:100px").tooltip("长期唤醒：说完唤醒词后，会触发提示语，后期对话不需要唤醒词；单次唤醒：每次对话都需要携带唤醒词，否则默认保持睡眠，且不会触发提示语")
                    textarea_talk_wakeup_sleep_wakeup_word = ui.textarea(label='唤醒词', placeholder='如：管家 多个请换行分隔', value=textarea_data_change(config.get("talk", "wakeup_sleep", "wakeup_word"))).style("width:200px;")
                    textarea_talk_wakeup_sleep_sleep_word = ui.textarea(label='睡眠词', placeholder='如：关机 多个请换行分隔', value=textarea_data_change(config.get("talk", "wakeup_sleep", "sleep_word"))).style("width:200px;")
                    textarea_talk_wakeup_sleep_wakeup_copywriting = ui.textarea(label='唤醒提示语', placeholder='如：在的 多个请换行分隔', value=textarea_data_change(config.get("talk", "wakeup_sleep", "wakeup_copywriting"))).style("width:300px;")
                    textarea_talk_wakeup_sleep_sleep_copywriting = ui.textarea(label='睡眠提示语', placeholder='如：晚安 多个请换行分隔', value=textarea_data_change(config.get("talk", "wakeup_sleep", "sleep_copywriting"))).style("width:300px;")

            with ui.expansion('谷歌', icon="settings", value=False).classes('w-2/3'):
                with ui.grid(columns=1):
                    data_json = {}
                    for line in ["zh-CN", "en-US", "ja-JP"]:
                        data_json[line] = line
                    select_talk_google_tgt_lang = ui.select(
                        label='目标翻译语言', 
                        options=data_json, 
                        value=config.get("talk", "google", "tgt_lang")
                    ).style("width:200px")
            with ui.expansion('百度', icon="settings", value=False).classes('w-2/3'):
                with ui.grid(columns=3):    
                    input_talk_baidu_app_id = ui.input(label='AppID', value=config.get("talk", "baidu", "app_id"), placeholder='百度云 语音识别应用的 AppID')
                    input_talk_baidu_api_key = ui.input(label='API Key', value=config.get("talk", "baidu", "api_key"), placeholder='百度云 语音识别应用的 API Key')
                    input_talk_baidu_secret_key = ui.input(label='Secret Key', value=config.get("talk", "baidu", "secret_key"), placeholder='百度云 语音识别应用的 Secret Key')
            with ui.expansion('faster_whisper', icon="settings", value=False).classes('w-2/3'):
                with ui.row():    
                    input_faster_whisper_model_size = ui.input(label='model_size', value=config.get("talk", "faster_whisper", "model_size"), placeholder='Size of the model to use')
                    data_json = {}
                    for line in ["自动识别", 'af', 'am', 'ar', 'as', 'az', 'ba', 'be', 'bg', 'bn', 'bo', 'br', 'bs', 'ca', 'cs', 'cy', 'da', 'de', 'el', 'en', 'es', 'et', 'eu', 'fa', 'fi', 'fo', 'fr', 'gl', 'gu', 'ha', 'haw', 'he', 'hi', 'hr', 'ht', 'hu', 'hy', 'id', 'is', 'it', 'ja', 'jw', 'ka', 'kk', 'km', 'kn', 'ko', 'la', 'lb', 'ln', 'lo', 'lt', 'lv', 'mg', 'mi', 'mk', 'ml', 'mn', 'mr', 'ms', 'mt', 'my', 'ne', 'nl', 'nn', 'no', 'oc', 'pa', 'pl', 'ps', 'pt', 'ro', 'ru', 'sa', 'sd', 'si', 'sk', 'sl', 'sn', 'so', 'sq', 'sr', 'su', 'sv', 'sw', 'ta', 'te', 'tg', 'th', 'tk', 'tl', 'tr', 'tt', 'uk', 'ur', 'uz', 'vi', 'yi', 'yo', 'zh', 'yue']:
                        data_json[line] = line
                    select_faster_whisper_language = ui.select(
                        label='识别语言', 
                        options=data_json, 
                        value=config.get("talk", "faster_whisper", "language")
                    ).style("width:200px")
                    data_json = {}
                    for line in ["cuda", "cpu", "auto"]:
                        data_json[line] = line
                    select_faster_whisper_device = ui.select(
                        label='device', 
                        options=data_json, 
                        value=config.get("talk", "faster_whisper", "device")
                    ).style("width:200px")
                    data_json = {}
                    for line in ["float16", "int8_float16", "int8"]:
                        data_json[line] = line
                    select_faster_whisper_compute_type = ui.select(
                        label='compute_type', 
                        options=data_json, 
                        value=config.get("talk", "faster_whisper", "compute_type")
                    ).style("width:200px")
                    input_faster_whisper_download_root = ui.input(label='download_root', value=config.get("talk", "faster_whisper", "download_root"), placeholder='模型下载路径')
                    input_faster_whisper_beam_size = ui.input(label='beam_size', value=config.get("talk", "faster_whisper", "beam_size"), placeholder='系统在每个步骤中要考虑的最可能的候选序列数。具有较大的beam_size将使系统产生更准确的结果，但可能需要更多的计算资源；较小的beam_size会减少计算需求，但可能降低结果的准确性。')
            with ui.expansion('SenseVoice', icon="settings", value=False).classes('w-2/3'):
                with ui.row():    
                    input_sensevoice_asr_model_path = ui.input(label='ASR 模型路径', value=config.get("talk", "sensevoice", "asr_model_path"), placeholder='ASR模型路径').tooltip("ASR模型路径")
                    input_sensevoice_vad_model_path = ui.input(label='VAD 模型路径', value=config.get("talk", "sensevoice", "vad_model_path"), placeholder='VAD模型路径').tooltip("VAD模型路径")
                    input_sensevoice_vad_max_single_segment_time = ui.input(label='VAD 模型路径', value=config.get("talk", "sensevoice", "vad_max_single_segment_time"), placeholder='VAD单段最大语音时间').tooltip("VAD单段最大语音时间")
                    input_sensevoice_vad_device = ui.input(label='device', value=config.get("talk", "sensevoice", "device"), placeholder='使用设备device').tooltip("使用设备device")
                    
                    data_json = {}
                    for line in ['zh', 'en', 'jp']:
                        data_json[line] = line
                    select_sensevoice_language = ui.select(
                        label='识别语言', 
                        options=data_json, 
                        value=config.get("talk", "sensevoice", "language")
                    ).style("width:100px")
                    input_sensevoice_text_norm = ui.input(label='text_norm', value=config.get("talk", "sensevoice", "text_norm"), placeholder='text_norm').style("width:100px").tooltip("text_norm")
                    input_sensevoice_batch_size_s = ui.input(label='batch_size_s', value=config.get("talk", "sensevoice", "batch_size_s"), placeholder='batch_size_s').style("width:100px").tooltip("batch_size_s")
                    input_sensevoice_batch_size = ui.input(label='batch_size', value=config.get("talk", "sensevoice", "batch_size"), placeholder='batch_size').style("width:100px").tooltip("batch_size")
            
        with ui.tab_panel(image_recognition_page).style(tab_panel_css):
            with ui.card().style(card_css): 
                async def get_llm_resp(screenshot_path: str, send_to_all: bool=True):
                    try:
                        # logger.warning(f"screenshot_path={screenshot_path}")

                        prompt = input_image_recognition_prompt.value

                        if select_image_recognition_model.value == "blip":
                            from utils.gpt_model.blip import Blip

                            blip = Blip(config.get("image_recognition", "blip"))

                            resp_content = blip.get_resp_with_img(prompt, screenshot_path)

                            data = {
                                "type": "reread",
                                "data": {
                                    "username": config.get("talk", "username"),
                                    "content": resp_content,
                                    "insert_index": -1
                                }
                            }

                        if send_to_all:
                            if data is not None:
                                main_api_ip = "127.0.0.1" if config.get("api_ip") == "0.0.0.0" else config.get("api_ip")
                                await common.send_async_request(f'http://{main_api_ip}:{config.get("api_port")}/send', "POST", data)

                        return data
                    except Exception as e:
                        logger.error(traceback.format_exc())
                        return None
                        
                async def loop_screenshot_toggle_timer(interval_time: float):
                    global loop_screenshot_timer_running, loop_screenshot_timer


                    async def image_recognition_screenshot_and_send():
                        global running_flag

                        if running_flag != 1:
                            ui.notify(position="top", type="warning", message="请先点击“一键运行”，然后再进行截图识别")
                            return
                        
                        logger.info(f"触发截图识别")

                        # 根据窗口名截图
                        screenshot_path = common.capture_window_by_title(input_image_recognition_img_save_path.value, select_image_recognition_screenshot_window_title.value)

                        data = await get_llm_resp(screenshot_path)

                        
                    if loop_screenshot_timer_running:
                        # 如果定时器已经在运行，则停止它
                        loop_screenshot_timer.cancel()
                    else:
                        # 如果定时器未在运行，则启动它
                        loop_screenshot_timer = ui.timer(interval=interval_time, callback=lambda: image_recognition_screenshot_and_send())  # 设置定时器，每秒执行一次perform_task函数
                        loop_screenshot_timer.activate()
                    loop_screenshot_timer_running = not loop_screenshot_timer_running  # 更新定时器运行状态

                # 截图并发送LLM
                async def image_recognition_screenshot_and_send(sleep_time: float):
                    global running_flag

                    if running_flag != 1:
                        ui.notify(position="top", type="warning", message="请先点击“一键运行”，然后再进行截图识别")
                        return
                    
                    logger.info(f"{input_image_recognition_screenshot_delay.value}后触发截图识别")
                    ui.notify(position="top", type="positive", message=f"{input_image_recognition_screenshot_delay.value}后触发截图识别")
                    
                    await asyncio.sleep(sleep_time)

                    # 根据窗口名截图
                    screenshot_path = common.capture_window_by_title(input_image_recognition_img_save_path.value, select_image_recognition_screenshot_window_title.value)
                    data = await get_llm_resp(screenshot_path)

                # 摄像头截图并发送LLM
                async def image_recognition_cam_screenshot_and_send(sleep_time: float):
                    global running_flag

                    if running_flag != 1:
                        ui.notify(position="top", type="warning", message="请先点击“一键运行”，然后再进行截图识别")
                        return
                    
                    logger.info(f"{input_image_recognition_cam_screenshot_delay.value}后触发摄像头截图识别")
                    ui.notify(position="top", type="positive", message=f"{input_image_recognition_screenshot_delay.value}后触发摄像头截图识别")
                    
                    await asyncio.sleep(sleep_time)

                    # 根据摄像头索引截图
                    screenshot_path = common.capture_image(input_image_recognition_img_save_path.value, int(select_image_recognition_cam_index.value))
                    data = await get_llm_resp(screenshot_path)


                ui.label("通用")
                with ui.row():
                    button_image_recognition_enable = ui.switch('启用', value=config.get("image_recognition", "enable")).style(switch_internal_css)
                    select_image_recognition_model = ui.select(
                        label='模型',
                        options={'blip': 'blip'},
                        value=config.get("image_recognition", "model")
                    ).style("width:150px")
                    
                    input_image_recognition_img_save_path = ui.input(label='截图保存路径', value=config.get("image_recognition", "img_save_path"), placeholder='截图保存路径，支持绝对或相对路径')
                    input_image_recognition_prompt = ui.input(label='携带的提示词', value=config.get("image_recognition", "prompt"), placeholder='图片识别时附带的提示词，协同图片获取回答')
                    
                    
                with ui.card().style(card_css):
                    ui.label("电脑截图")
                    with ui.row():
                        window_titles = common.list_visible_windows()
                        data_json = {}
                        for line in window_titles:
                            data_json[line] = line
                        select_image_recognition_screenshot_window_title = ui.select(
                            label='截图窗口标题', 
                            options=data_json, 
                            value=config.get("image_recognition", "screenshot_window_title")
                        ).style("width:300px")
                        input_image_recognition_screenshot_delay = ui.input(label='N秒后进行截图', value=config.get("image_recognition", "screenshot_delay"), placeholder='截图延迟，方便用户打开对应窗口').style("width:100px")
                        button_image_recognition_screenshot_and_send = ui.button('截图并发送', on_click=lambda: image_recognition_screenshot_and_send(float(input_image_recognition_screenshot_delay.value)), color=button_internal_color).style(button_internal_css)
                    
                        switch_image_recognition_loop_screenshot_enable = ui.switch('循环截图并发送', value=config.get("image_recognition", "loop_screenshot_enable")).style(switch_internal_css)
                        input_image_recognition_loop_screenshot_delay = ui.input(label='N秒后自动截图', value=config.get("image_recognition", "loop_screenshot_delay"), placeholder='自动截图延迟，用户在玩游戏或者看视频等情况下，可以自动触发图像识别').style("width:100px")
                        # button_image_recognition_loop_screenshot_and_send = ui.button('循环截图并发送', on_click=lambda: loop_screenshot_toggle_timer(float(input_image_recognition_screenshot_delay.value)), color=button_internal_color).style(button_internal_css)
                with ui.card().style(card_css):
                    ui.label("摄像头截图")
                    with ui.row():
                        switch_image_recognition_cam_screenshot_enable = ui.switch('启用', value=config.get("image_recognition", "cam_screenshot_enable")).style(switch_internal_css)
                        
                        if config.get("image_recognition", "cam_screenshot_enable"):
                            cam_indexs = common.list_cameras()
                        else:
                            cam_indexs = []
                        data_json = {}
                        for line in cam_indexs:
                            data_json[line] = line
                        select_image_recognition_cam_index = ui.select(
                            label='摄像头索引', 
                            options=data_json, 
                            value=config.get("image_recognition", "cam_index")
                        ).style("width:100px")
                        input_image_recognition_cam_screenshot_delay = ui.input(label='N秒后进行截图', value=config.get("image_recognition", "cam_screenshot_delay"), placeholder='截图延迟，方便用户调整摄像头').style("width:100px")
                        button_image_recognition_cam_screenshot_and_send = ui.button('截图并发送', on_click=lambda: image_recognition_cam_screenshot_and_send(float(input_image_recognition_cam_screenshot_delay.value)), color=button_internal_color).style(button_internal_css)
                    
                        switch_image_recognition_loop_cam_screenshot_enable = ui.switch('循环截图并发送', value=config.get("image_recognition", "loop_cam_screenshot_enable")).style(switch_internal_css)
                        input_image_recognition_loop_cam_screenshot_delay = ui.input(label='N秒后自动截图', value=config.get("image_recognition", "loop_cam_screenshot_delay"), placeholder='自动截图延迟，可以自动触发图像识别').style("width:100px")
                        
            with ui.card().style(card_css):
                ui.label("Blip")
                with ui.row():
                    select_image_recognition_blip_model = ui.select(
                        label='模型', 
                        options={
                            'Salesforce/blip-image-captioning-large': 'Salesforce/blip-image-captioning-large',
                            'Salesforce/blip-image-captioning-base': 'Salesforce/blip-image-captioning-base',
                        }, 
                        value=config.get("image_recognition", "blip", "model")
                    ).style("width:300px")

        with ui.tab_panel(assistant_anchor_page).style(tab_panel_css):
            with ui.row():
                switch_assistant_anchor_enable = ui.switch('启用', value=config.get("assistant_anchor", "enable")).style(switch_internal_css)
                input_assistant_anchor_username = ui.input(label='助播名', value=config.get("assistant_anchor", "username"), placeholder='助播的用户名，暂时没啥用')
                select_assistant_anchor_audio_synthesis_type = ui.select(
                    label='语音合成', 
                    options=audio_synthesis_type_options, 
                    value=config.get("assistant_anchor", "audio_synthesis_type")
                ).style("width:200px;")
            with ui.card().style(card_css):
                ui.label("触发类型")
                with ui.row():
                    # 类型列表源自audio_synthesis_handle 音频合成的所支持的type值
                    assistant_anchor_type_list = ["comment", "local_qa_audio", "song", "reread", "read_comment", "gift", 
                                                  "entrance", "follow", "idle_time_task", "reread_top_priority", "schedule", 
                                                  "image_recognition_schedule", "key_mapping", "integral"]
                    assistant_anchor_type_mapping = {
                        "comment": "弹幕",
                        "local_qa_audio": "本地问答-音频",
                        "song": "点歌",
                        "reread": "复读",
                        "read_comment": "念弹幕",
                        "gift": "礼物",
                        "entrance": "入场",
                        "follow": "关注",
                        "idle_time_task": "闲时任务",
                        "reread_top_priority": "最高优先级-复读",
                        "schedule": "定时任务",
                        "image_recognition_schedule": "图像识别定时任务",
                        "key_mapping": "按键映射",
                        "integral": "积分",
                    }
                    assistant_anchor_type_var = {}
                    
                    for index, assistant_anchor_type in enumerate(assistant_anchor_type_list):
                        if assistant_anchor_type in config.get("assistant_anchor", "type"):
                            assistant_anchor_type_var[str(index)] = ui.checkbox(text=assistant_anchor_type_mapping[assistant_anchor_type], value=True)
                        else:
                            assistant_anchor_type_var[str(index)] = ui.checkbox(text=assistant_anchor_type_mapping[assistant_anchor_type], value=False)
            with ui.grid(columns=4):
                switch_assistant_anchor_local_qa_text_enable = ui.switch('启用文本匹配', value=config.get("assistant_anchor", "local_qa", "text", "enable")).style(switch_internal_css)
                select_assistant_anchor_local_qa_text_format = ui.select(
                    label='存储格式',
                    options={'json': '自定义json', 'text': '一问一答'},
                    value=config.get("assistant_anchor", "local_qa", "text", "format")
                )
                input_assistant_anchor_local_qa_text_file_path = ui.input(label='文本问答数据路径', value=config.get("assistant_anchor", "local_qa", "text", "file_path"), placeholder='本地问答文本数据存储路径').style("width:200px;")
                input_assistant_anchor_local_qa_text_similarity = ui.input(label='文本最低相似度', value=config.get("assistant_anchor", "local_qa", "text", "similarity"), placeholder='最低文本匹配相似度，就是说用户发送的内容和本地问答库中设定的内容的最低相似度。\n低了就会被当做一般弹幕处理').style("width:200px;")
            with ui.grid(columns=4):
                switch_assistant_anchor_local_qa_audio_enable = ui.switch('启用音频匹配', value=config.get("assistant_anchor", "local_qa", "audio", "enable")).style(switch_internal_css)
                select_assistant_anchor_local_qa_audio_type = ui.select(
                    label='匹配算法',
                    options={'包含关系': '包含关系', '相似度匹配': '相似度匹配'},
                    value=config.get("assistant_anchor", "local_qa", "audio", "type")
                )
                input_assistant_anchor_local_qa_audio_file_path = ui.input(label='音频存储路径', value=config.get("assistant_anchor", "local_qa", "audio", "file_path"), placeholder='本地问答音频文件存储路径').style("width:200px;")
                input_assistant_anchor_local_qa_audio_similarity = ui.input(label='音频最低相似度', value=config.get("assistant_anchor", "local_qa", "audio", "similarity"), placeholder='最低音频匹配相似度，就是说用户发送的内容和本地音频库中音频文件名的最低相似度。\n低了就会被当做一般弹幕处理').style("width:200px;")
        
        with ui.tab_panel(translate_page).style(tab_panel_css):
            with ui.row():
                switch_translate_enable = ui.switch('启用', value=config.get("translate", "enable")).style(switch_internal_css)
                select_translate_type = ui.select(
                        label='类型', 
                        options={'baidu': '百度翻译', 'google': '谷歌翻译'}, 
                        value=config.get("translate", "type")
                    ).style("width:100px;")
                select_translate_trans_type = ui.select(
                        label='翻译类型', 
                        options={'弹幕': '弹幕', '回复': '回复', '弹幕+回复': '弹幕+回复'}, 
                        value=config.get("translate", "trans_type")
                    ).style("width:150px;")
            with ui.card().style(card_css):
                ui.label("百度翻译")
                with ui.row():
                    input_translate_baidu_appid = ui.input(label='APP ID', value=config.get("translate", "baidu", "appid"), placeholder='翻译开放平台 开发者中心 APP ID')
                    input_translate_baidu_appkey = ui.input(label='密钥', value=config.get("translate", "baidu", "appkey"), placeholder='翻译开放平台 开发者中心 密钥')
                    select_translate_baidu_from_lang = ui.select(
                        label='源语言', 
                        options={'auto': '自动检测', 'zh': '中文', 'cht': '繁体中文', 'en': '英文', 'jp': '日文', 'kor': '韩文', 'yue': '粤语', 'wyw': '文言文'}, 
                        value=config.get("translate", "baidu", "from_lang")
                    ).style("width:100px;")
                    select_translate_baidu_to_lang = ui.select(
                        label='目标语言', 
                        options={'zh': '中文', 'cht': '繁体中文', 'en': '英文', 'jp': '日文', 'kor': '韩文', 'yue': '粤语', 'wyw': '文言文'}, 
                        value=config.get("translate", "baidu", "to_lang")
                    ).style("width:100px;")
            with ui.card().style(card_css):
                ui.label("谷歌翻译")
                with ui.row():
                    input_translate_google_proxy = ui.input(label='代理地址', value=config.get("translate", "google", "proxy"), placeholder='代理的完整地址，请携带协议')
                    select_translate_google_src_lang = ui.select(
                        label='源语言', 
                        options={'auto': '自动', 'zh-CN': '中文', 'en': '英文', 'ja': '日文'}, 
                        value=config.get("translate", "google", "src_lang")
                    ).style("width:100px;")
                    select_translate_google_tgt_lang = ui.select(
                        label='目标语言', 
                        options={'zh-CN': '中文', 'en': '英文', 'ja': '日文'}, 
                        value=config.get("translate", "google", "tgt_lang")
                    ).style("width:100px;")

        with ui.tab_panel(serial_page).style(tab_panel_css):
            with ui.element('div').classes('p-2 bg-blue-100'):
                ui.label("此页完成串口配置后，可以在“通用配置”的 串口映射配置功能\n 注意！！！此处连接测试完成后，请 关闭串口，因为程序是跨进程使用的，所以不关掉会占用串口，导致无法正常使用！")
                        
            with ui.row():
                input_serial_config_index = ui.input(label='串口配置索引', value="", placeholder='串口配置组的排序号，就是说第一个组是1，第二个组是2，以此类推。请填写纯正整数')
                button_serial_config_add = ui.button('增加串口配置组', on_click=serial_config_add, color=button_internal_color).style(button_internal_css)
                button_serial_config_del = ui.button('删除串口配置组', on_click=lambda: serial_config_del(input_serial_config_index.value), color=button_internal_color).style(button_internal_css)

            serial_config_var = {}
            serial_config_card = ui.card()

            # 刷新串口列表
            async def refresh_serial(index: int):
                logger.warning(index)
                try:
                    from utils.serial_manager_instance import get_serial_manager

                    serial_manager = get_serial_manager()

                    list_ports = await serial_manager.list_ports()
                    logger.info(f"搜索到的串口：{list_ports}")
                    ui.notify(position="top", type="positive", message=f"搜索到的串口：{list_ports}")
                    serial_config_var[str(8 * index)].set_options(list_ports)
                except Exception as e:
                    logger.error(traceback.format_exc())
                    ui.notify(position="top", type="negative", message=f"{traceback.format_exc()}")
                
            async def connect_serial(index: int):
                logger.warning(index)
                try:
                    from utils.serial_manager_instance import get_serial_manager

                    serial_manager = get_serial_manager()

                    serial_name = serial_config_var[str(8 * index)].value
                    baudrate = serial_config_var[str(8 * index + 1)].value
                    resp_json = await serial_manager.connect(serial_name, baudrate)
                    if resp_json['ret']:
                        ui.notify(position="top", type="positive", message=f"{resp_json['msg']}")
                    else:
                        ui.notify(position="top", type="negative", message=f"{resp_json['msg']}")
                except Exception as e:
                    logger.error(traceback.format_exc())
                    ui.notify(position="top", type="negative", message=f"{traceback.format_exc()}")

            async def disconnect_serial(index: int):
                logger.warning(index)
                try:
                    from utils.serial_manager_instance import get_serial_manager

                    serial_manager = get_serial_manager()

                    serial_name = serial_config_var[str(8 * index)].value
                    resp_json = await serial_manager.disconnect(serial_name)
                    if resp_json['ret']:
                        ui.notify(position="top", type="positive", message=f"{resp_json['msg']}")
                    else:
                        ui.notify(position="top", type="negative", message=f"{resp_json['msg']}")
                except Exception as e:
                    logger.error(traceback.format_exc())
                    ui.notify(position="top", type="negative", message=f"{traceback.format_exc()}")


            async def send_data_to_serial(index: int):
                try:
                    from utils.serial_manager_instance import get_serial_manager

                    serial_manager = get_serial_manager()

                    serial_name = serial_config_var[str(8 * index)].value
                    serial_data_type = serial_config_var[str(8 * index + 5)].value
                    send_data = serial_config_var[str(8 * index + 6)].value
                    resp_json = await serial_manager.send_data(serial_name, send_data, serial_data_type)
                    if resp_json['ret']:
                        ui.notify(position="top", type="positive", message=f"{resp_json['msg']}")
                    else:
                        ui.notify(position="top", type="negative", message=f"{resp_json['msg']}")
                except Exception as e:
                    logger.error(traceback.format_exc())
                    ui.notify(position="top", type="negative", message=f"{traceback.format_exc()}")  

            
            for index, serial_config in enumerate(config.get("serial", "config")):
                with serial_config_card.style(card_css):
                    with ui.row():
                        serial_config_var[str(8 * index)] = ui.select(label=f"串口名#{index + 1}", value=serial_config["serial_name"], options={f'{serial_config["serial_name"]}': f'{serial_config["serial_name"]}'}).style("width:200px;").tooltip('文案文件存储路径。不建议更改。')
                        serial_config_var[str(8 * index + 1)] = ui.select(
                            label=f"波特率#{index + 1}", 
                            value=serial_config["baudrate"], 
                            options={'9600': '9600', '19200': '19200', '38400': '38400', '115200': '115200'}
                        ).style("width:200px;").tooltip('波特率')

                        # TODO:这里的传参一直是0，index值有问题，bug待定位
                        serial_config_var[str(8 * index + 2)] = ui.button('刷新串口', on_click=lambda idx=index: refresh_serial(idx))
                        serial_config_var[str(8 * index + 3)] = ui.button('打开串口', on_click=lambda idx=index: connect_serial(idx))
                        serial_config_var[str(8 * index + 4)] = ui.button('关闭串口', on_click=lambda idx=index: disconnect_serial(idx))

                        serial_config_var[str(8 * index + 5)] = ui.select(label=f"发送数据类型#{index + 1}", value=serial_config["serial_data_type"], options={'ASCII': 'ASCII', 'HEX': 'HEX'},).style("width:100px;").tooltip('发送的数据类型')
                        serial_config_var[str(8 * index + 6)] = ui.input(label=f"发送数据#{index + 1}", value="", placeholder='填要发的内容，连接后，点 发送').style("width:200px;").tooltip('填要发的内容，连接后，点 发送')
                        serial_config_var[str(8 * index + 7)] = ui.button('发送', on_click=lambda idx=index: send_data_to_serial(idx))

        with ui.tab_panel(data_analysis_page).style(tab_panel_css):
            from utils.data_analysis import Data_Analysis

            data_analysis = Data_Analysis(config_path)

            data_analysis_comment_word_cloud_card = ui.card()
            with data_analysis_comment_word_cloud_card.style("width:100%;"):
                echart_comment_word_cloud = ui.echart(data_analysis.get_comment_word_cloud_option(
                    int(config.get("data_analysis", "comment_word_cloud", "top_num")))
                ).style(echart_css)
        
                with ui.row():
                    input_data_analysis_comment_word_cloud_top_num = ui.input(label='前N个关键词', value=config.get("data_analysis", "comment_word_cloud", "top_num"), placeholder='筛选前N个弹幕关键词做为词云数据')
                    def update_echart_comment_word_cloud():
                        data_analysis_comment_word_cloud_card.remove(0)
                        echart_comment_word_cloud = ui.echart(data_analysis.get_comment_word_cloud_option(
                            int(input_data_analysis_comment_word_cloud_top_num.value))
                        ).style(echart_css)
                        echart_comment_word_cloud.move(data_analysis_comment_word_cloud_card, 0)
                    ui.button('更新数据', on_click=lambda: update_echart_comment_word_cloud())
            
            data_analysis_integral_card = ui.card()
            with data_analysis_integral_card.style("width:100%;"):
                echart_integral = ui.echart(data_analysis.get_integral_option(
                    "integral", int(config.get("data_analysis", "integral", "top_num")))
                ).style(echart_css)
        
                with ui.row():
                    input_data_analysis_integral_top_num = ui.input(label='Top N个数据', value=config.get("data_analysis", "integral", "top_num"), placeholder='筛选Top N个数据')
                    def update_echart_integral(type):
                        data_analysis_integral_card.remove(0)
                        echart_integral = ui.echart(data_analysis.get_integral_option(
                            type,
                            int(input_data_analysis_integral_top_num.value))
                        ).style(echart_css)
                        echart_integral.move(data_analysis_integral_card, 0)
                    ui.button('获取积分榜', on_click=lambda: update_echart_integral('integral'))
                    ui.button('获取观看榜', on_click=lambda: update_echart_integral('view_num'))
                    ui.button('获取签到榜', on_click=lambda: update_echart_integral('sign_num'))
                    ui.button('获取金额榜', on_click=lambda: update_echart_integral('total_price'))
            data_analysis_gift_card = ui.card()
            with data_analysis_gift_card.style("width:100%;"):
                echart_gift = ui.echart(data_analysis.get_gift_option(int(config.get("data_analysis", "gift", "top_num")))).style(echart_css)
        
                with ui.row():
                    input_data_analysis_gift_top_num = ui.input(label='Top N个数据', value=config.get("data_analysis", "gift", "top_num"), placeholder='筛选Top N个数据')
                    def update_echart_gift():
                        data_analysis_gift_card.remove(0)
                        echart_gift = ui.echart(data_analysis.get_gift_option(
                            int(input_data_analysis_gift_top_num.value))
                        ).style(echart_css)
                        echart_gift.move(data_analysis_gift_card, 0)
                    ui.button('更新数据', on_click=lambda: update_echart_gift())
        with ui.tab_panel(web_page).style(tab_panel_css):
            with ui.card().style(card_css):
                ui.label("webui配置")
                with ui.row():
                    input_webui_title = ui.input(label='标题', placeholder='webui的标题', value=config.get("webui", "title")).style("width:250px;")
                    input_webui_ip = ui.input(label='IP地址', placeholder='webui监听的IP地址', value=config.get("webui", "ip")).style("width:150px;")
                    input_webui_port = ui.input(label='端口', placeholder='webui监听的端口', value=config.get("webui", "port")).style("width:100px;")
                    switch_webui_auto_run = ui.switch('自动运行', value=config.get("webui", "auto_run")).style(switch_internal_css)
            
            with ui.card().style(card_css):
                ui.label("本地路径指定URL路径访问")
                with ui.row():
                    input_webui_local_dir_to_endpoint_index = ui.input(label='配置索引', value="", placeholder='配置组的排序号，就是说第一个组是1，第二个组是2，以此类推。请填写纯正整数')
                    button_webui_local_dir_to_endpoint_add = ui.button('增加配置组', on_click=webui_local_dir_to_endpoint_add, color=button_internal_color).style(button_internal_css)
                    button_webui_local_dir_to_endpoint_del = ui.button('删除配置组', on_click=lambda: webui_local_dir_to_endpoint_del(input_webui_local_dir_to_endpoint_index.value), color=button_internal_color).style(button_internal_css)
                
                with ui.row():
                    switch_webui_local_dir_to_endpoint_enable = ui.switch('启用', value=config.get("webui", "local_dir_to_endpoint", "enable")).style(switch_internal_css)
                with ui.row():
                    webui_local_dir_to_endpoint_config_var = {}
                    webui_local_dir_to_endpoint_config_card = ui.card()
                    for index, webui_local_dir_to_endpoint_config in enumerate(config.get("webui", "local_dir_to_endpoint", "config")):
                        with webui_local_dir_to_endpoint_config_card.style(card_css):
                            with ui.row():
                                webui_local_dir_to_endpoint_config_var[str(2 * index)] = ui.input(label=f"URL路径#{index + 1}", value=webui_local_dir_to_endpoint_config["url_path"], placeholder='以斜杠（"/"）开始的字符串，它标识了应该为客户端提供文件的URL路径').style("width:200px;")
                                webui_local_dir_to_endpoint_config_var[str(2 * index + 1)] = ui.input(label=f"本地文件夹路径#{index + 1}", value=webui_local_dir_to_endpoint_config["local_dir"], placeholder='本地文件夹路径，建议相对路径，最好是项目内部的路径').style("width:300px;")
                               

            with ui.card().style(card_css):
                ui.label("CSS")
                with ui.row():
                    theme_list = config.get("webui", "theme", "list").keys()
                    data_json = {}
                    for line in theme_list:
                        data_json[line] = line
                    select_webui_theme_choose = ui.select(
                        label='主题', 
                        options=data_json, 
                        value=config.get("webui", "theme", "choose")
                    )

            with ui.card().style(card_css):
                ui.label("配置模板")
                with ui.row():
                    # 获取指定路径下指定拓展名的文件名列表
                    config_template_paths = common.get_specify_extension_names_in_folder("./", "*.json")
                    data_json = {}
                    for line in config_template_paths:
                        data_json[line] = line
                    select_config_template_path = ui.select(
                        label='配置模板路径', 
                        options=data_json, 
                        value="",
                        with_input=True,
                        new_value_mode='add-unique',
                        clearable=True
                    )

                    button_config_template_save = ui.button('保存webui配置到文件', on_click=lambda: config_template_save(select_config_template_path.value), color=button_internal_color).style(button_internal_css)
                    button_config_template_load = ui.button('读取模板到本地（慎点）', on_click=lambda: config_template_load(select_config_template_path.value), color=button_internal_color).style(button_internal_css)
                    


            with ui.card().style(card_css):
                ui.label("板块显示/隐藏")
                
                with ui.card().style(card_css):
                    ui.label("通用配置")
                    with ui.row():
                        switch_webui_show_card_common_config_read_comment = ui.switch('念弹幕', value=config.get("webui", "show_card", "common_config", "read_comment")).style(switch_internal_css)
                        switch_webui_show_card_common_config_filter = ui.switch('过滤', value=config.get("webui", "show_card", "common_config", "filter")).style(switch_internal_css)
                        switch_webui_show_card_common_config_thanks = ui.switch('答谢', value=config.get("webui", "show_card", "common_config", "thanks")).style(switch_internal_css)
                        switch_webui_show_card_common_config_local_qa = ui.switch('本地问答', value=config.get("webui", "show_card", "common_config", "local_qa")).style(switch_internal_css)
                        switch_webui_show_card_common_config_choose_song = ui.switch('点歌', value=config.get("webui", "show_card", "common_config", "choose_song")).style(switch_internal_css)
                        switch_webui_show_card_common_config_sd = ui.switch('Stable Diffusion', value=config.get("webui", "show_card", "common_config", "sd")).style(switch_internal_css)
                        switch_webui_show_card_common_config_log = ui.switch('日志', value=config.get("webui", "show_card", "common_config", "log")).style(switch_internal_css)
                        switch_webui_show_card_common_config_schedule = ui.switch('定时任务', value=config.get("webui", "show_card", "common_config", "schedule")).style(switch_internal_css)
                        switch_webui_show_card_common_config_idle_time_task = ui.switch('闲时任务', value=config.get("webui", "show_card", "common_config", "idle_time_task")).style(switch_internal_css)
                        switch_webui_show_card_common_config_trends_copywriting = ui.switch('动态文案', value=config.get("webui", "show_card", "common_config", "trends_copywriting")).style(switch_internal_css)
                        switch_webui_show_card_common_config_database = ui.switch('数据库', value=config.get("webui", "show_card", "common_config", "database")).style(switch_internal_css)
                        switch_webui_show_card_common_config_play_audio = ui.switch('音频播放', value=config.get("webui", "show_card", "common_config", "play_audio")).style(switch_internal_css)
                        switch_webui_show_card_common_config_web_captions_printer = ui.switch('web字幕打印机', value=config.get("webui", "show_card", "common_config", "web_captions_printer")).style(switch_internal_css)
                        switch_webui_show_card_common_config_key_mapping = ui.switch('按键/文案映射', value=config.get("webui", "show_card", "common_config", "key_mapping")).style(switch_internal_css)
                        switch_webui_show_card_common_config_custom_cmd = ui.switch('自定义命令', value=config.get("webui", "show_card", "common_config", "custom_cmd")).style(switch_internal_css)
                        
                        switch_webui_show_card_common_config_trends_config = ui.switch('动态配置', value=config.get("webui", "show_card", "common_config", "trends_config")).style(switch_internal_css)
                        switch_webui_show_card_common_config_abnormal_alarm = ui.switch('异常报警', value=config.get("webui", "show_card", "common_config", "abnormal_alarm")).style(switch_internal_css)
                        switch_webui_show_card_common_config_coordination_program = ui.switch('联动程序', value=config.get("webui", "show_card", "common_config", "coordination_program")).style(switch_internal_css)
                        
                
                with ui.card().style(card_css):
                    ui.label("大语言模型")
                    with ui.row():
                        switch_webui_show_card_llm_custom_llm = ui.switch('自定义LLM', value=config.get("webui", "show_card", "llm", "custom_llm")).style(switch_internal_css)
                        
                with ui.card().style(card_css):
                    ui.label("文本转语音")
                    with ui.row():
                        switch_webui_show_card_tts_gpt_sovits = ui.switch('gpt_sovits', value=config.get("webui", "show_card", "tts", "gpt_sovits")).style(switch_internal_css)
                        
                with ui.card().style(card_css):
                    ui.label("变声")
                    with ui.row():
                        switch_webui_show_card_svc_so_vits_svc = ui.switch('SO-VITS-SVC', value=config.get("webui", "show_card", "svc", "so_vits_svc")).style(switch_internal_css)
                with ui.card().style(card_css):
                    ui.label("虚拟身体")
                    with ui.row():
                        switch_webui_show_card_visual_body_metahuman_stream = ui.switch('metahuman_stream', value=config.get("webui", "show_card", "visual_body", "metahuman_stream")).style(switch_internal_css)
                                
                    
            
            with ui.card().style(card_css):
                ui.label("账号管理")
                with ui.row():
                    switch_login_enable = ui.switch('登录功能', value=config.get("login", "enable")).style(switch_internal_css)
                    input_login_username = ui.input(label='用户名', placeholder='您的账号喵，配置在config.json中', value=config.get("login", "username")).style("width:250px;")
                    input_login_password = ui.input(label='密码', password=True, placeholder='您的密码喵，配置在config.json中', value=config.get("login", "password")).style("width:250px;")
    with ui.grid(columns=6).style("position: fixed; bottom: 10px; text-align: center;"):
        button_save = ui.button('保存配置', on_click=lambda: save_config(), color=button_bottom_color).style(button_bottom_css).tooltip("保存webui的配置到本地文件，有些配置保存后需要重启生效")
        button_run = ui.button('一键运行', on_click=lambda: run_external_program(), color=button_bottom_color).style(button_bottom_css).tooltip("运行main.py")
        # 创建一个按钮，用于停止正在运行的程序
        button_stop = ui.button("停止运行", on_click=lambda: stop_external_program(), color=button_bottom_color).style(button_bottom_css).tooltip("停止运行main.py")
        button_light = ui.button('关灯', on_click=lambda: change_light_status(), color=button_bottom_color).style(button_bottom_css)
        # button_stop.enabled = False  # 初始状态下停止按钮禁用
        button_restart = ui.button('重启', on_click=lambda: restart_application(), color=button_bottom_color).style(button_bottom_css).tooltip("停止运行main.py并重启webui")
        # factory_btn = ui.button('恢复出厂配置', on_click=lambda: factory(), color=button_bottom_color).style(tab_panel_css)

    with ui.row().style("position:fixed; bottom: 20px; right: 20px;"):
        ui.button('⇧', on_click=lambda: scroll_to_top(), color=button_bottom_color).style(button_bottom_css)

    # 是否启用自动运行功能
    if config.get("webui", "auto_run"):
        logger.info("自动运行 已启用")
        run_external_program(type="api")

# 发送心跳包
ui.timer(9 * 60, lambda: common.send_heartbeat())

# 是否启用登录功能（暂不合理）
if config.get("login", "enable"):

    def my_login():
        try:
            global user_info

            username = input_login_username.value
            password = input_login_password.value

            if username == "" or password == "":
                ui.notify(position="top", type="info", message="用户名或密码不能为空")
                return

            API_URL = urljoin(config.get("login", "ums_api"), '/auth/login')
                        
            resp_json = common.check_login(API_URL, username, password)

            if resp_json is None:
                ui.notify(position="top", type="negative", message="登录失败")
                return

            if "data" not in resp_json or "success" not in resp_json:
                ui.notify(position="top", type="negative", message="用户名或密码不正确")
                return

            if not resp_json["success"]:
                remainder = common.time_difference_in_seconds(resp_json["data"]["expiration_ts"])
                ui.notify(position="top", type="warning", message=f'账号过期时间：{resp_json["data"]["expiration_ts"]}，已到期，请联系管理员续费')
                return

            user_info = resp_json["data"]
            expiration_ts = resp_json["data"]["expiration_ts"]

            remainder = common.time_difference_in_seconds(expiration_ts)
            if remainder < 0:
                ui.notify(position="top", type="warning", message=f"账号已过期：{remainder}秒，请联系管理员续费")
                return

            ui.notify(position="top", type="info", message=f'登录成功，账号到期时间：{resp_json["data"]["expiration_ts"]}，剩余时长：{remainder}秒')

            label_login.delete()
            input_login_username.delete()
            input_login_password.delete()
            button_login.delete()
            button_login_forget_password.delete()

            login_column.style("")
            login_card.style("position: unset;")

            goto_func_page()

            return
        except Exception as e:
            logger.error(traceback.format_exc())
            return

    # @ui.page('/forget_password')
    def forget_password():
        ui.notify(position="top", type="info", message="请联系管理员修改密码！")


    login_column = ui.column().style("width:100%;text-align: center;")
    with login_column:
        login_card = ui.card().style(config.get("webui", "theme", "list", theme_choose, "login_card"))
        with login_card:
            label_login = ui.label('AI    Vtuber').style("font-size: 30px;letter-spacing: 5px;color: #3b3838;")
            input_login_username = ui.input(label='用户名', placeholder='您的账号，请找管理员申请', value="").style("width:250px;")
            input_login_password = ui.input(label='密码', password=True, placeholder='您的密码，请找管理员申请', value="").style("width:250px;")
            button_login = ui.button('登录', on_click=lambda: my_login()).style("width:250px;")
            button_login_forget_password = ui.button('忘记账号/密码怎么办？', on_click=lambda: forget_password()).style("width:250px;")
            # link_login_forget_password = ui.link('忘记账号密码怎么办？', forget_password)

else:
    login_column = ui.column().style("width:100%;text-align: center;")
    with login_column:
        login_card = ui.card().style(config.get("webui", "theme", "list", theme_choose, "login_card"))
        
        # 跳转到功能页
        goto_func_page()


ui.run(host=webui_ip, port=webui_port, title=webui_title, favicon="./ui/favicon-64.ico", language="zh-CN", dark=False, reload=False)
# ui.run(host=webui_ip, port=webui_port, title=webui_title, favicon="./ui/favicon-64.ico", language="zh-CN", dark=False, reload=False,
#        ssl_certfile="F:\\FunASR_WS\\cert.pem", ssl_keyfile="F:\\FunASR_WS\\key.pem")