from frontend.ui.components.config_helper import get_nested_value
"""
AUTOlive 主入口文件
模块化的WebUI应用程序
"""
import sys
import os
import signal
import socket
import subprocess
import threading
import time
import traceback
from collections import deque
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from nicegui import ui, app
from nicegui import context
from nicegui import core as _nicegui_core

# 导入配置模块
from frontend.config.settings import init_config, get_config
from frontend.config.paths import get_base_path, get_config_path, get_log_dir, get_output_dir

# 导入工具模块
from frontend.utils.helpers import SystemCommand
from frontend.utils.common import textarea_data_change
from utils import alarm_notifier

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
    talk,
    integral,
    assistant_anchor,
    translate,
    serial,
    data_analysis,
    audio_play,
    web_captions_printer,
    filter_config,
    filter_forget,
    filter_dedup,
    filter_queue,
    blacklist,
    read_comment,
    thanks,
    schedule,
    idle_time_task,
    trends_copywriting,
    local_qa,
    choose_song,
    key_mapping,
    database_config,
    system_settings,
    platform_config
)

# 导入样式模块
from frontend.styles.themes import init_theme_manager, get_theme_manager

# 导入日志模块
from utils.my_log import logger
from utils.autolive_paths import ensure_config_backup

# D-15（基础功能 2026-10-22 节 #9）：退役配置键面清单（键路径→说明）。
# 首启扫描存量命中→notify+log 一次性告知，防旧配置静默失效无感知；
# 迁移/清理职责归 settings._ensure_defaults 各迁移点（此处只做可见面，
# 不改配置）。visual_body=metahuman_stream 的自动迁移在 _ensure_defaults
# 有独立 log 告知，不在本清单（迁移后键已不存在）。
_RETIRED_CONFIG_KEYS: list = [
    (("metahuman_stream",), "metahuman_stream 配置节已退役（2026-10-22 批次），整节不再读取，可删除"),
    (("game",), "game 配置节已退役（游戏控制实现已移除，chat_type=game 不可用），可删除"),
    (("audio_player", "priority_mapping"), "audio_player.priority_mapping 已收敛至 filter.priority_mapping（运行时自动合并迁移），可删除"),
    (("filter", "lang_filter"), "filter.lang_filter 已退役（语言过滤由顶层 need_lang 键驱动），可删除"),
    (("talk", "show_chat_log"), "talk.show_chat_log 已退役（2026-10-25 批次：聊天记录固定记录弹幕与聊天互动，不再需要开关），可删除"),
    (("talk", "no_recording_during_playback_sleep_interval"), "talk.no_recording_during_playback_sleep_interval 已退役（2026-10-30 聊天审查批次：待播放音频列表为 0 才进行录音检测，不再需要间隔参数），可删除"),
    (("key_mapping", "copywriting_trigger_type"), "key_mapping 文案触发已移除（2026-10-24 精简批次），可删除"),
    (("key_mapping", "copywriting_single_sentence_trigger_once"), "key_mapping 文案触发已移除（2026-10-24 精简批次），可删除"),
    (("key_mapping", "local_audio_trigger_type"), "key_mapping 本地音频触发已移除（2026-10-24 精简批次），可删除"),
    (("key_mapping", "local_audio_single_sentence_trigger_once"), "key_mapping 本地音频触发已移除（2026-10-24 精简批次），可删除"),
    (("key_mapping", "img_path_trigger_type"), "key_mapping 图片触发已移除（2026-10-24 精简批次），可删除"),
    (("key_mapping", "img_path_single_sentence_trigger_once"), "key_mapping 图片触发已移除（2026-10-24 精简批次），可删除"),
    # 报警重构批次（2026-10-26）：旧报警配置整体退役，改为 abnormal_alarm.modules
    # 六模块开关 + abnormal_alarm.webhook 通告（错误计数/自动重启/本地音频不再存在）
    (("abnormal_alarm", "notify_enable"), "abnormal_alarm.notify_enable 已退役（子进程告警弹窗常开），可删除"),
    (("abnormal_alarm", "sound_enable"), "abnormal_alarm.sound_enable 已退役（声音报警通道从未接线），可删除"),
    (("abnormal_alarm", "platform"), "旧六类报警配置已退役（2026-10-26 重构：报警改为 abnormal_alarm.modules 开关 + webhook 通告），可删除"),
    (("abnormal_alarm", "llm"), "旧六类报警配置已退役（2026-10-26 重构：报警改为 abnormal_alarm.modules 开关 + webhook 通告），可删除"),
    (("abnormal_alarm", "tts"), "旧六类报警配置已退役（2026-10-26 重构：报警改为 abnormal_alarm.modules 开关 + webhook 通告），可删除"),
    (("abnormal_alarm", "svc"), "旧六类报警配置已退役（2026-10-26 重构：报警改为 abnormal_alarm.modules 开关 + webhook 通告），可删除"),
    (("abnormal_alarm", "visual_body"), "旧六类报警配置已退役（2026-10-26 重构：报警改为 abnormal_alarm.modules 开关 + webhook 通告），可删除"),
    (("abnormal_alarm", "other"), "旧六类报警配置已退役（2026-10-26 重构：报警改为 abnormal_alarm.modules 开关 + webhook 通告），可删除"),
    # 念弹幕审查批次（2026-10-31）：周期触发退役（即时入队取代定时回放）+ 变声死键
    (("read_comment", "periodic_trigger"), "read_comment.periodic_trigger 已退役（念弹幕审查批次 2026-10-31：系统已有限入/去重/优先级队列，弹幕处理时即时入队，定时回放机制删除），可删除"),
    (("read_comment", "voice_change"), "read_comment.voice_change 已退役（变声随主流程退役 2026-10-22 批次 A-6，此键后端从未消费），可删除"),
    # 答谢审查批次（2026-10-31）：三通道周期触发退役（即时分发+E-2 限流取代定时回放）
    (("thanks", "entrance", "periodic_trigger"), "thanks.entrance.periodic_trigger 已退役（答谢审查批次 2026-10-31：系统已有限入/去重/优先级队列，答谢即时分发+每分钟限流兜底，定时回放机制删除），可删除"),
    (("thanks", "gift", "periodic_trigger"), "thanks.gift.periodic_trigger 已退役（答谢审查批次 2026-10-31：系统已有限入/去重/优先级队列，答谢即时分发+每分钟限流兜底，定时回放机制删除），可删除"),
    (("thanks", "follow", "periodic_trigger"), "thanks.follow.periodic_trigger 已退役（答谢审查批次 2026-10-31：系统已有限入/去重/优先级队列，答谢即时分发+每分钟限流兜底，定时回放机制删除），可删除"),
    # 数字人配置面清理（2026-10-26）：reaction_segment 弹幕触发切段已被
    # choreography 编排规则全面取代（触发类型 gift/keyword/song + 序列编排），
    # 逻辑消费方为零——UI 控件与默认值已删，存量键一次性告知后可手动删除。
    (("edtalk_realtime", "reaction_segment"), "edtalk_realtime.reaction_segment 已退役（弹幕触发切段由『动作编排』页的编排规则驱动），可删除"),
    # 控制面收敛为纯本机回环（2026-10-26）：全系统消费方硬编码 127.0.0.1，
    # host 唯一作用=暴露局域网（无场景）；Token 防跨机访问（本机无意义）。
    # 启动映射固定 --host 127.0.0.1，省略 --api-token（服务端默认无鉴权）。
    (("edtalk_realtime", "host"), "edtalk_realtime.host 已退役（控制面固定本机回环），可删除"),
    (("edtalk_realtime", "api_token"), "edtalk_realtime.api_token 已退役（纯本机回环无鉴权面），可删除"),
    # 运行时量收敛（2026-10-26 第二批）：端口自动择址（占用自动切换，实际端口写
    # data/edtalk_runtime.json）、解释器自动探测+记忆、段调度并入动作编排页
    # （启动固定 --segments auto）。消费方经 runtime.resolve_api_base 统一解析。
    (("edtalk_realtime", "port"), "edtalk_realtime.port 已退役（端口自动择址：被占自动切换，实际端口记录于 data/edtalk_runtime.json），可删除"),
    (("edtalk_realtime", "api_ip_port"), "edtalk_realtime.api_ip_port 已退役（控制面基址由运行时文件自动解析），可删除"),
    (("edtalk_realtime", "interpreter_path"), "edtalk_realtime.interpreter_path 已退役（解释器自动探测并记忆上次使用的环境），可删除"),
    (("edtalk_realtime", "segments"), "edtalk_realtime.segments 已退役（默认全部素材轮播，画面接管走『动作编排』页），可删除"),
    # 角色素材目录退役（2026-10-31 画面设置批次）：素材增删/清单由『动作编排』页
    # 全面接管，目录本身为内部量（默认 wy，materials.resolve_character_dir 与
    # 启动参数同源兜底）——UI 配置项已删，存量键一次性告知后可手动删除
    (("edtalk_realtime", "character_dir"), "edtalk_realtime.character_dir 已退役（素材管理由『动作编排』页接管，目录为内部量默认 wy；删除后未配置时自动用 wy），可删除"),
    # 训练起始阶段键退役（2026-10-31 训练主播审查批次）：流程选择改由训练页
    # 阶段按钮多选承担，该键现版本不被读取（TrainPipeline.start_stage 仅构造
    # 参数默认值，不从 config 读取）
    (("train_streamer", "start_stage"), "train_streamer.start_stage 已退役（流程选择改由训练页阶段按钮多选，该键现版本不读取），可删除"),
    # 提示词前缀/后缀弃用（通用配置精简批次）：LLM 服务需单独设置提示词与回复
    # 规则，前后缀无意义；build_llm_input 不再读取，存量键一次性告知后可手动删除
    (("before_prompt",), "提示词前缀已弃用移除（LLM 服务需单独设置提示词与回复规则），可删除"),
    (("after_prompt",), "提示词后缀已弃用移除（LLM 服务需单独设置提示词与回复规则），可删除"),
    # 过滤审查批次（2026-10-30）：六前缀后缀配置退役（后端零消费点）+
    # 复读触发配置组退役（弹幕关键词触发复读功能整体移除；手动直接复读保留）
    (("filter", "before_must_str"), "弹幕触发前缀已退役（2026-10-30 过滤审查批次：后端零消费点），可删除"),
    (("filter", "after_must_str"), "弹幕触发后缀已退役（2026-10-30 过滤审查批次：后端零消费点），可删除"),
    (("filter", "before_filter_str"), "弹幕过滤前缀已退役（2026-10-30 过滤审查批次：后端零消费点），可删除"),
    (("filter", "after_filter_str"), "弹幕过滤后缀已退役（2026-10-30 过滤审查批次：后端零消费点），可删除"),
    (("filter", "before_must_str_for_llm"), "LLM触发前缀已退役（2026-10-30 过滤审查批次：后端零消费点），可删除"),
    (("filter", "after_must_str_for_llm"), "LLM触发后缀已退役（2026-10-30 过滤审查批次：后端零消费点），可删除"),
    (("reread",), "reread 复读触发配置组已退役（2026-10-30 过滤审查批次：弹幕关键词触发复读移除；聊天页手动直接复读不受影响），可删除"),
    # 限入审查批次（2026-10-31）：聊天/定时任务/闲时任务限流选项退役（系统
    # 交互/系统生成消息不参与弹幕处理流程限流，process_data 三类直通分发）
    (("filter", "talk_forget_duration"), "聊天遗忘间隔已退役（2026-10-31 限入审查批次：聊天消息不走限流窗口），可删除"),
    (("filter", "talk_forget_reserve_num"), "聊天保留数已退役（2026-10-31 限入审查批次：聊天消息不走限流窗口），可删除"),
    (("filter", "schedule_forget_duration"), "定时任务遗忘间隔已退役（2026-10-31 限入审查批次：系统生成消息不走限流窗口），可删除"),
    (("filter", "schedule_forget_reserve_num"), "定时任务保留数已退役（2026-10-31 限入审查批次：系统生成消息不走限流窗口），可删除"),
    (("filter", "idle_time_task_forget_duration"), "闲时任务遗忘间隔已退役（2026-10-31 限入审查批次：系统生成消息不走限流窗口），可删除"),
    # 数据分析审查批次（2026-10-31）：礼物榜单整体移除（用户裁决：与积分统计
    # -金额榜重复展示——integral.total_price 已含礼物价值口径）
    (("data_analysis", "gift"), "data_analysis.gift 已退役（2026-10-31 数据分析审查批次：礼物榜单移除，金额榜在积分统计卡已有），可删除"),
    # 本地问答审查批次（2026-10-31）：周期性触发退役（task_data 缓冲自始零写入点
    # ——回放永远取空列表的死机制，命中即时合成）；问答数据迁移 config 内嵌
    # （前端 textarea 实时保存，本地问答与助播知识库同构）
    (("local_qa", "periodic_trigger"), "local_qa.periodic_trigger 已退役（2026-10-31 本地问答审查批次：缓冲零写入的死机制，本地问答命中即时合成播放），可删除"),
    (("local_qa", "text", "file_path"), "local_qa.text.file_path 已退役（2026-10-31 本地问答审查批次：问答数据迁移 config 内嵌 local_qa.text.data，前端直编实时保存），可删除"),
    (("local_qa", "text", "username_max_len"), "local_qa.text.username_max_len 已退役（2026-10-31 本地问答审查批次：后端零消费死键），可删除"),
    (("assistant_anchor", "local_qa", "text", "file_path"), "assistant_anchor.local_qa.text.file_path 已退役（2026-10-31 本地问答审查批次：助播知识库问答数据迁移 config 内嵌 .text.data，前端直编实时保存），可删除"),
    (("filter", "idle_time_task_forget_reserve_num"), "闲时任务保留数已退役（2026-10-31 限入审查批次：系统生成消息不走限流窗口），可删除"),
    # 闲时任务审查批次（2026-10-31）：「等待播放音频数量阈值+闲时计时减小到」
    # 动态降时机制退役（用户裁决：冗余——后端零消费仅 UI 存在）
    (("idle_time_task", "wait_play_audio_num_threshold"), "等待播放音频数量阈值已退役（2026-10-31 闲时任务审查批次：动态降低闲时等待机制冗余，后端零消费），可删除"),
    (("idle_time_task", "idle_time_reduce_to"), "闲时计时减小到已退役（2026-10-31 闲时任务审查批次：动态降低闲时等待机制冗余，后端零消费），可删除"),
    # 点歌审查批次（2026-10-31）：同歌冷却/回应节流/准备文案退役（系统已有限入/
    # 去重/优先级队列防刷屏；无成品场景=播匹配成功文案后静默入队翻唱，准备文案冗余）
    (("choose_song", "song_cooldown_s"), "choose_song.song_cooldown_s 已退役（2026-10-31 点歌审查批次：系统已有限入/去重/优先级队列，同歌冷却防刷屏机制冗余），可删除"),
    (("choose_song", "reply_cooldown_s"), "choose_song.reply_cooldown_s 已退役（2026-10-31 点歌审查批次：点歌回应即时处理，节流机制冗余），可删除"),
    (("choose_song", "prepare_copy"), "choose_song.prepare_copy 已退役（2026-10-31 点歌审查批次：无成品场景播报匹配成功文案后静默入队翻唱，准备文案冗余），可删除"),
    # 动作编排审查批次（2026-10-31）：计数窗口退役（任务书弃用移除——触发计次
    # 不再随时间衰减，清零仅剩他键触发打断；UI 控件删除，存量键兼容读取一代）
    (("choreography", "reset_after_s"), "choreography.reset_after_s 计数窗口已退役（2026-10-31 动作编排审查批次：触发计次不再随时间衰减，他键触发清零语义保留），可删除"),
]


class AutoliveApp:
    """AUTOlive 应用程序类"""

    # webui 单例注册（2026-11-09 数字人模型管理批）：tab 与本模块同进程，
    # 函数内延迟 `from frontend.main import APP_HOLDER` 取实例——画面设置
    # 切换角色后自动重启 edtalk 服务等进程操作走此句柄（模块级定义避免循环
    # import：frontend.main → layout → tabs 的 import 链在本模块加载期闭合）。
    APP_HOLDER = {"app": None}

    # script mode 运行态单例（启动链假启动修复 2026-10-12）：每个浏览器连接都会
    # 重跑 __init__（root page 重入，__init__:106 重入判定），实例字段的运行态会被
    # 重置——running_flag 刷新即"假停止"、my_subprocesses 丢失成孤儿进程。
    # 运行态挂类级 property 共享；dark_mode 等每用户状态不受影响。
    _shared_running = False
    _shared_subprocesses = {}

    # 最近异常环形缓冲（D1 状态条数据源）：类级 deque（script mode 重入共享），
    # 监控（_monitor_subprocesses）与启动编排（run_external_program / D-14）都写入，
    # 消息入缓冲前经 redact_exception 脱敏（DX 义务）。
    _shared_exception_buffer = deque(maxlen=10)

    # UI-16 同期（TODOS 2026-09-23 节竞态条）：UI executor 线程与 /sys_cmd
    # async endpoint 并发启停的 TOCTOU 窗口收口——类级锁串行化运行态写入。
    # 读侧（状态条轮询）不加锁（bool 原子读；GIL 下无撕裂）。
    _running_flag_lock = threading.Lock()

    # 登录会话令牌表（类级共享：script mode 每连接重跑 __init__，不能落实例字段；
    # token -> user_info）。纯内存态：webui 重启即全部失效，浏览器需重新登录。
    # 页面构建阶段读不到 app.storage.tab（tab_id 由 websocket 握手才建立），
    # 故登录态以「内存令牌表 + 会话 cookie」承载（见 _enter_after_login）。
    _shared_sessions: dict = {}
    _AUTH_COOKIE = "autolive_auth"

    @property
    def running_flag(self) -> bool:
        return AutoliveApp._shared_running

    @running_flag.setter
    def running_flag(self, value) -> None:
        with AutoliveApp._running_flag_lock:
            AutoliveApp._shared_running = bool(value)

    @property
    def my_subprocesses(self) -> dict:
        return AutoliveApp._shared_subprocesses

    @my_subprocesses.setter
    def my_subprocesses(self, value: dict) -> None:
        # 测试与重置路径用赋值换新 dict；类级引用替换保持跨重入共享
        AutoliveApp._shared_subprocesses = value if isinstance(value, dict) else {}

    # ============ D1 运行状态条数据源（类级运行态的读取/写入便捷层） ============

    @classmethod
    def push_exception(cls, module: str, message: str, *, fix_hint: str = "") -> dict:
        """异常入环形缓冲（D1）：脱敏后追加，供状态条展示。

        Args:
            module: 模块名（main / edtalk_realtime / fastrag …）
            message: 异常消息（入缓冲前经 redact_exception 脱敏）
            fix_hint: 修复指引（D-14 三类：重登录/改端口/查路径）

        Returns:
            入缓冲的条目 dict（ts/module/message/fix_hint）
        """
        from frontend.utils.redact import redact_exception
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "module": module,
            "message": redact_exception(message),
            "fix_hint": str(fix_hint or ""),
        }
        cls._shared_exception_buffer.append(entry)
        return entry

    @classmethod
    def recent_exceptions(cls) -> list:
        """最近异常列表（时间正序，最新在末尾；上限 10 条）。"""
        return list(cls._shared_exception_buffer)

    def subprocess_health(self) -> list:
        """子进程健康快照（D1 状态条数据源）：[(name, alive)]。

        alive: True=存活 / False=已退出 / None=探测失败未知。
        """
        snap = []
        for name, proc in list(self.my_subprocesses.items()):
            try:
                snap.append((name, proc.poll() is None))
            except Exception:
                snap.append((name, None))
        return snap

    def send_chat_message(self, msg_type: str, content: str) -> bool:
        """聊天页按钮统一发送（修复 2026-10-25 实测批次：webui 重构自 webui-bak
        时 AutoliveApp 丢失此方法——layout.py:1686 hasattr 判定 False 致
        chat_send_callback=None，聊天框/直接复读恒报"发送失败——请确认系统已启动"）。

        语义与主进程 main.py send_chat_message 同构：POST core /send（webui
        /send 同构 payload）——reread/reread_top_priority 走 reread_handle
        （手动直通，999 链路），comment 走弹幕链（chat_source=chatbox 进聊天记录）。
        Returns: 是否成功（失败 notify 由调用方 talk 页承接）。
        """
        import json as _json
        import urllib.request as _urllib
        cfg = self.config._config
        main_api_ip = "127.0.0.1" if cfg.get("api_ip") == "0.0.0.0" else cfg.get("api_ip", "127.0.0.1")
        main_api_port = cfg.get("api_port", 8000)
        url = f'http://{main_api_ip}:{main_api_port}/send'
        payload = {"type": msg_type, "data": {
            "username": (cfg.get("talk") or {}).get("username", "主人"),
            "content": content, "chat_source": "chatbox"}}
        try:
            req = _urllib.Request(url, data=_json.dumps(payload).encode('utf-8'),
                                  headers={'Content-Type': 'application/json'})
            with _urllib.urlopen(req, timeout=10) as resp:
                resp_data = _json.loads(resp.read().decode('utf-8'))
            return isinstance(resp_data, dict) and resp_data.get("code") == 200
        except Exception as e:
            logger.error(f"聊天发送失败（{msg_type}）：{e}")
            return False

    def __init__(self):
        """初始化应用程序

        NiceGUI 3.x script mode（整合修复 2026-10-06）：顶层构建 UI 的主脚本被注册为
        root page——每个浏览器连接都会 runpy 重跑本模块与 main()（__name__='__main__'）。
        重入（server 已启动）时跳过 Audio/数据库/目录等重活，仅保留 config 与状态字段
        ——重活每次执行就是"前端不断重启"现象的根因（Audio init 约 12s）。
        """
        # webui 单例注册（script mode 重入幂等：同实例逻辑，覆盖注册无害）
        AutoliveApp.APP_HOLDER["app"] = self
        # 初始化基础路径
        self.base_path = get_base_path()

        # 初始化配置（必须在 _init_directories 之前；webui 进程内单例，重入幂等）
        config_path = get_config_path()
        self.config = init_config(str(config_path))

        # NiceGUI 3.x 重入判定：server 已启动 = 本次为 root page 重跑（非首次启动）
        from nicegui import core as _nicegui_core
        self._reentry = bool(getattr(_nicegui_core.app, "is_started", False))
        if not self._reentry:
            # 恢复出厂基线自维护（/autoplan 批准批次 C6 项，TODOS #8）：bak 缺失时
            # 从当前 config.json 固化一份，factory（:614）不再必炸。幂等，仅首次固化。
            _bak = os.path.join(str(self.base_path), "config.json.bak")
            ensure_config_backup(str(get_config_path()), _bak)
        if self._reentry:
            logger.debug("AutoliveApp 重入（script mode root page）——跳过重活初始化")

        # 初始化目录（重活，仅首次）
        if not self._reentry:
            self._init_directories()
        
        # 初始化主题管理器
        self.theme_manager = init_theme_manager(self.config._config)
        
        # 初始化UI组件
        self.navigation_tabs = NavigationTabs(self.config._config)
        self.control_buttons = ControlButtons(self.config._config)
        self.login_form = LoginForm(self.config._config)
        self.theme_manager_ui = ThemeManager(self.config._config)

        # 状态变量（运行态挂类级 property——见类头注释；此处不重置）
        self.user_info = None

        # 暗夜模式（任务7/DG-B8：单实例收敛 + 按配置初始化——先于任何 UI 构建
        # 与 check_login，登录页不闪白；缺省 light）
        self.dark_mode = ui.dark_mode()
        self.dark_mode.value = bool(
            get_nested_value(self.config._config, "webui", "dark_mode", default=False)
        )

        # 初始化Audio类（与原文件 webui-bak.py L141 一致；重活，重入跳过）
        if self._reentry:
            self.audio = None
        else:
            try:
                from utils.audio import Audio
                self.audio = Audio(str(get_config_path()), type=2)
                logger.info("Audio初始化完成")
            except Exception as e:
                logger.warning(f"Audio初始化失败: {e}")
                self.audio = None

        # 初始化数据分析模块（与原文件 webui-bak.py L4939-4941 一致；重活，重入跳过）
        if self._reentry:
            self.data_analysis = None
        else:
            try:
                from utils.data_analysis import Data_Analysis
                self.data_analysis = Data_Analysis(str(get_config_path()))
                logger.info("数据分析模块初始化完成")
            except Exception as e:
                logger.warning(f"数据分析模块初始化失败: {e}")
                self.data_analysis = None

        # 变声服务前端托管（C3 懒启动，2026-11-03 终门 uc1c 裁决）：进前端**不再
        # 直接拉起** rvc-vc 子进程（原实现每次重入都有 alive 2s 探测 vs 120s 就绪
        # 窗竞态双拉风险，且 GPU/CPU 常驻）——首次使用变声功能时 ensure_rvc_vc()
        # （svc 页挂点）按 C2 终版单例判定自动拉起，闲置自动休眠
        # （rvc_service.start_idle_monitor）。不注册状态条占位（2026-11-03 用户
        # 实测裁决：不使用变声时常驻胶囊是噪音，服务拉起后胶囊自然出现）。
        if bool(get_nested_value(self.config._config, "rvc_vc", "enabled")):
            logger.info("[rvc-vc] 懒启动模式：进程未拉起，首次使用变声时自动拉起"
                        "（闲置自动休眠；拉起后状态条可查）")

        if self._reentry:
            logger.debug("AUTOlive 应用程序初始化完成（重入）")
        else:
            logger.info("AUTOlive 应用程序初始化完成")
    
    def _init_directories(self):
        """初始化必要的目录"""
        # 日志目录
        log_dir = get_log_dir()
        logger.debug(f"日志目录: {log_dir}")
        
        # 输出目录
        output_dir = get_output_dir()
        logger.debug(f"输出目录: {output_dir}")
        
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
    
    # 外围模块显示名（D-14 部分启动 notify 文案用）
    _MODULE_LABELS = {
        "main": "主系统",
        "edtalk_realtime": "EDTalk 推理",
        "fastrag": "知识库服务",
    }

    def start_programs(self) -> list:
        """启动外围服务（EDTalk 实时推理 + fastrag 知识库），返回启动失败列表。

        coordination_program 配置驱动拉起已随模块退役移除（/autoplan 批准批次
        删除批，任务书"联动程序此功能已弃用，直接移除"）；EDTalk/fastrag 的
        拉起均显式 shell=False + list 传参（native H1）。

        Returns:
            [(module, reason, fix_hint), ...]——D-14 外围失败分级：调用方
            （run_external_program）降级继续（拉起主系统），仅告警不中断。
        """
        config_data = self.config._config
        failures = []

        # EDTalk 实时推理服务器（计划 §4.2：开关双检 + 端口幂等 + 身份验证）
        ok, reason, fix_hint = self._start_edtalk_realtime(config_data)
        if not ok:
            failures.append(("edtalk_realtime", reason, fix_hint))

        # fastrag 知识库服务（计划 §6.2）
        ok, reason, fix_hint = self._start_fastrag(config_data)
        if not ok:
            failures.append(("fastrag", reason, fix_hint))

        return failures

    def _start_main_system(self):
        """拉起主系统 main.py 子进程（编排层，启动链假启动修复 2026-10-12）。

        start_programs 只负责配置驱动的外围程序（coordination/edtalk/fastrag，
        tests/test_startup_wiring.py 锁定其行为）；main.py 主系统的拉起属启动
        编排。shell=False + list 传参（native H1 规范）。

        Returns:
            (ok, reason, fix_hint)——D-14：关键模块失败 → 调用方复位 running_flag
            并弹 negative 通知（主系统失败=整体失败，不做降级）。
        """
        main_py = os.path.join(str(getattr(self, "base_path", os.getcwd())), "main.py")
        if not os.path.exists(main_py):
            logger.error(f"主系统入口不存在: {main_py}（problem=入口缺失；fix=检查仓库完整性）")
            return False, f"主系统入口不存在: {main_py}", "请检查安装完整性（main.py 缺失）"
        logger.info(f"运行程序: main 主系统 位于: {os.path.dirname(main_py)}")
        try:
            process = subprocess.Popen(
                [sys.executable, main_py],
                cwd=str(os.path.dirname(main_py)), shell=False)
            self.my_subprocesses["main"] = process
            return True, "", ""
        except Exception as e:
            logger.error(f"启动主系统 main 失败: {e}")
            return False, f"启动主系统进程失败: {e}", "请查看完整日志；若提示端口占用请修改端口配置"

    def _start_edtalk_realtime(self, config_data: dict):
        """拉起 EDTalk realtime_serve 服务（计划 §4.2 + Eng 义务⑨⑬）。

        - 开关双检：visual_body == "edtalk_realtime" 且 edtalk_realtime.enable
        - 解释器存在性校验（缺失报错附修复动作）
        - 端口幂等探测 + GET /status 身份验证（占用者未必是 realtime_serve）
        - 训练进行中检测（双向显存防护）
        - 显式 shell=False + list 传参（native H1）

        Returns:
            (ok, reason, fix_hint)——D-14：ok=False 时由调用方（start_programs
            → run_external_program）降级继续并汇总告警；开关未启用/训练中/
            已在运行等"主动跳过"不算失败。
        """
        try:
            ed_cfg = config_data.get("edtalk_realtime", {})
        except Exception:
            ed_cfg = {}
        if config_data.get("visual_body") != "edtalk_realtime" or not ed_cfg.get("enable"):
            return True, "", ""

        # 解释器自动探测+记忆（2026-10-26 配置面收敛：不再来自用户配置）
        from utils.edtalk_realtime.runtime import (
            find_free_port, probe_identity, resolve_interpreter, write_runtime)
        interpreter = resolve_interpreter(config_data)  # 外层 config（内部自取 edtalk_realtime 节）
        if not interpreter or not os.path.exists(interpreter):
            logger.error(
                "EDTalk 解释器未找到。problem=runtime312 缺失；"
                "cause=项目根 runtime312 与 EDTalk 安装目录均无 python.exe；"
                "fix=检查迁移（python Scripts/migrate_runtime_check.py --dry-run），或安装 EDTalk 到 D:\\AI\\EDTalk"
            )
            return False, "EDTalk 解释器未找到（runtime312 缺失）", "检查迁移：python Scripts/migrate_runtime_check.py --dry-run"

        # 虚拟摄像头启动检测（2026-10-31 画面设置批次，任务书：进入系统后检测
        # 是否安装摄像头——画面输出的基本支持，若没有安装则执行安装）。
        # 未安装→编程化安装（UAC 弹窗用户确认，与『画面设置』页同一共享函数）；
        # 失败不阻断拉起（服务可运行，仅画面输出不可用），告警给出去处。
        from utils.edtalk_realtime.helpers import install_unity_capture, query_unity_capture
        cam_ok, cam_name = query_unity_capture()
        if not cam_ok:
            ui.notify(position="top", type="warning",
                      message="未检测到虚拟摄像头——正在执行安装（请在弹出的 UAC 窗口确认）……")
            try:
                install_unity_capture("AUTOlive 数字人", str(get_base_path()))
            except Exception as e:
                logger.error(f"虚拟摄像头自动安装失败: {e}")
                ui.notify(position="top", type="negative", timeout=10,
                          message="虚拟摄像头安装失败，数字人画面无法输出：" + str(e))
            else:
                cam_ok, cam_name = query_unity_capture()
                if cam_ok:
                    ui.notify(position="top", type="positive",
                              message="虚拟摄像头 " + cam_name + " 安装完成")
                else:
                    ui.notify(position="top", type="warning", timeout=10,
                              message="虚拟摄像头安装命令已执行但未检测到设备（可能需重启系统）"
                                      "——在此之前数字人画面无法输出；可在『画面设置』页重试")

        # 训练进行中检测（双向显存防护，16GB 卡训练与推理不可并存）
        try:
            from utils.train_streamer import pipeline as _train_pipeline
            if getattr(_train_pipeline, "training_running", False):
                logger.warning(
                    "训练正在进行中，启动实时推理服务器将导致 GPU 显存冲突（16GB 卡）——已跳过拉起，建议错开"
                )
                ui.notify(position="top", type="warning",
                          message="训练进行中，实时推理服务未启动（显存冲突风险），建议错开")
                return True, "", ""
        except ImportError:
            pass

        edtalk_dir = os.path.join(str(get_base_path()), "edtalk")
        if not os.path.isdir(edtalk_dir):
            logger.error(
                f"EDTalk 运行副本目录不存在：{edtalk_dir}。problem=副本缺失；"
                f"cause=尚未执行复制；fix=运行 scripts/copy_edtalk.py 一键复制脚本"
            )
            return False, f"EDTalk 运行副本目录不存在：{edtalk_dir}", "请运行 scripts/copy_edtalk.py 一键复制脚本"

        # 角色包权重预检（2026-11-10 用户实报：数据包 checkpoint 被删后服务
        # exit=1 模糊报错）——拉起前确认 checkpoint/new.pt（Generator 权重）
        # 与 audio2lip.pt（口型权重）在位，缺失给可行动指引（重新部署）。
        # 2026-11-10 tuber 角色库改造：数据包=仓库根 tuber/{启用角色}。
        character_name = str(ed_cfg.get("character_dir") or "wy")
        pkg_ckpt = os.path.join(str(get_base_path()), "tuber", character_name, "checkpoint")
        # 仅角色包目录已存在时查完整性（目录整体缺失=数据包未部署，保持原行为
        # 由服务端报错；测试假环境同理跳过）
        if os.path.isdir(pkg_ckpt):
            missing = [f for f in ("new.pt", "audio2lip.pt")
                       if not os.path.isfile(os.path.join(pkg_ckpt, f))]
            if missing:
                reason = (f"角色包 {character_name} 缺口型权重文件：{', '.join(missing)}"
                          f"（{pkg_ckpt}）")
                fix_hint = ("在『训练主播』页重新部署该角色（训练编排部署步会从"
                            " ckpt_models 权威副本恢复角色包权重），或重新训练")
                logger.error(f"{reason}。problem=角色包权重缺失；cause=数据包 checkpoint 不完整"
                             f"（可能被手动清理/删除）；fix={fix_hint}")
                return False, reason, fix_hint

        port = int(ed_cfg.get("port", 8000)) if ed_cfg.get("port") else 8000
        # host/API Token 已退役（2026-10-26）：控制面为纯本机回环（全系统消费方
        # 硬编码 127.0.0.1），省略 --host/--api-token 用服务端默认（回环+无鉴权）。

        # 端口策略（2026-10-26 收敛为内部量）：优先 8000；被 realtime_serve 占用
        # →复用（幂等跳过）；被其他服务占用→自动切换到可用端口（三段式告知，
        # 实际端口写运行时文件，消费方经 resolve_api_base 统一解析）。
        identity = probe_identity(port)
        if identity == "realtime_serve":
            write_runtime(port, interpreter)
            logger.warning(f"EDTalk realtime_serve 已在运行（端口 {port}），跳过拉起")
            ui.notify(position="top", type="warning",
                      message=f"EDTalk 服务已在运行（端口 {port}），跳过重复拉起")
            return True, "", ""
        if identity == "other":
            switched = find_free_port(port + 1)
            logger.warning(
                f"端口 {port} 被其他 HTTP 服务占用（响应非 realtime_serve），"
                f"已自动切换到 {switched}。problem=端口冲突已自动规避；"
                f"cause={port} 被未知服务占用；fix=无需处理（运行时文件已记录新端口），"
                f"如需固定端口可结束占用 {port} 的进程"
            )
            ui.notify(position="top", type="warning",
                      message=f"端口 {port} 被占用，数字人服务自动切换到 {switched}")
            port = switched
        # identity == "free"（连接失败 = 端口空闲）→ 直接用 preferred

        # 参数映射（config.edtalk_realtime → RUNBOOK 启动参数表）
        # 帧参数预检（2026-10-31 画面设置批次 F3）：服务端 config.py 对越界值
        # 直接 ValueError（拉起即崩）——启动链先 clamp 到合法域并告知
        # （buffer∈[1,300]，preroll∈[0,buffer]；任务书"预填充帧数需小于缓冲帧数"）。
        def _int_cfg(key, default):
            try:
                return int(str(ed_cfg.get(key, default)).strip())
            except (TypeError, ValueError):
                return default
        raw_buffer = _int_cfg("buffer_frames", 16)
        raw_preroll = _int_cfg("preroll_frames", 8)
        buffer_frames = min(max(raw_buffer, 1), 300)
        preroll_frames = min(max(raw_preroll, 0), buffer_frames)
        if (buffer_frames, preroll_frames) != (raw_buffer, raw_preroll):
            ui.notify(position="top", type="warning",
                      message=f"帧参数越界已自动修正：缓冲={buffer_frames}（1-300），"
                              f"预填充={preroll_frames}（须≤缓冲）；可在『画面设置』页调整")
        cmd = [interpreter, "-m", "realtime_serve.main"]
        # character_dir 语义升级（2026-11-10 tuber 角色库改造）：键值=当前启用
        # 角色名，服务加载绝对路径 tuber/{角色}（每角色一个数据包，画面设置
        # 切换=写此键+服务重启加载对应包）。
        cmd += ["--character-dir", os.path.join(str(get_base_path()), "tuber",
                                                str(ed_cfg.get("character_dir", "wy")))]
        cmd += ["--target-fps", str(_int_cfg("target_fps", 25))]
        cmd += ["--buffer-frames", str(buffer_frames)]
        cmd += ["--preroll-frames", str(preroll_frames)]
        face_repair = ed_cfg.get("face_repair", "adaptive")
        if face_repair and face_repair != "off":
            cmd += ["--face-repair", face_repair]
        gaze = ed_cfg.get("gaze_correction", "adaptive")
        if gaze and gaze != "off":
            cmd += ["--gaze-correction", gaze]
        cmd += ["--gaze-convergence", str(ed_cfg.get("gaze_convergence", 0.05))]
        # 段播放调度已退役（2026-10-26）：默认全部素材自然排序轮播（--segments auto），
        # 画面接管统一走『动作编排』页（默认画面池/规则序列经 /segment/schedule 下发）
        cmd += ["--segments", "auto"]
        # 统一画布合成层（2026-10-27 画面输出批次，R-ceo9/F-M3）：几何单一来源=
        # AUTOlive 配置键（edtalk_realtime.canvas_w/h 等），会话启动下发服务端
        cmd += ["--canvas-w", str(ed_cfg.get("canvas_w", 1280))]
        cmd += ["--canvas-h", str(ed_cfg.get("canvas_h", 720))]
        if ed_cfg.get("composite_enabled", True) is False:
            cmd += ["--no-composite"]
        _default_bg = str(ed_cfg.get("composite_default_bg", "") or "")
        if _default_bg:
            cmd += ["--composite-default-bg", _default_bg]
        cmd += ["--host", "127.0.0.1"]
        cmd += ["--port", str(port)]

        try:
            # 显式 shell=False + list 传参（native H1：路径含空格/中文不会被 cmd.exe 打碎）
            # stdout/stderr 落盘（2026-10-16 排障缺口：realtime_serve 全 print 输出
            # 随 webui 控制台丢失，出画失败无从取证）
            log_dir = str(get_log_dir())
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "edtalk_realtime.log")
            log_f = open(log_path, "ab", buffering=0)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            log_f.write(f"\n===== 启动 {stamp} 端口 {port} =====\n".encode("utf-8"))
            process = subprocess.Popen(cmd, cwd=edtalk_dir, shell=False,
                                       stdout=log_f, stderr=subprocess.STDOUT)
            self.my_subprocesses["edtalk_realtime"] = process
            self._edtalk_log_f = log_f  # 保引用防 GC 关闭句柄
            write_runtime(port, interpreter)  # 实际端口/解释器入运行时文件（消费方统一解析）
            logger.info(f"运行程序: edtalk_realtime（PID {process.pid}，端口 {port}，日志 {log_path}）")
            return True, "", ""
        except Exception as e:
            logger.error(f"启动 EDTalk realtime_serve 失败: {e}")
            return False, f"启动 EDTalk realtime_serve 失败: {e}", "请查看完整日志（log/edtalk_realtime.log）定位原因"

    def _start_fastrag(self, config_data: dict):
        """拉起 fastrag server（python 路线，autoplan main-20261029-102545 终门 rec）。

        2026-10-29 fastrag 内部化批次：启动物=fastrag-python/server.py（完成
        2026-10-15 uc1a 裁决）。rust 线已弃用删除（同日用户裁决：对话与索引
        构建全面应用 python 线；server.exe/src/Config.toml 等已清理）。

        预检四件（索引 faiss+data.json 双存在/解释器/server.py，缺失→ok=False
        +D-14 式 fix 指引）+ 端口预检（占用幂等探测，Eng E4）；端口经
        FASTRAG__SERVER__PORT 环境变量注入（Eng 义务⑬延续，服务端 config.py
        补丁 P4 读入并 fail-fast 解析非法值，DX F2）。

        Returns:
            (ok, reason, fix_hint)——D-14：ok=False 时由调用方（start_programs
            → run_external_program）降级继续并汇总告警；未启用不算失败。
        """
        fastrag_cfg = config_data.get("fastrag", {})
        if not fastrag_cfg.get("enable"):
            return True, "", ""

        base = str(get_base_path())
        fastrag_dir = os.path.join(base, "edtalk", "fastrag")
        py_dir = os.path.join(fastrag_dir, "fastrag-python")
        server_py = os.path.join(py_dir, "server.py")

        # 预检①：python 索引双件（Eng E2：faiss+data.json 双存在——build_index_native
        # save() 先写 faiss 后写 data.json，两写间崩溃留半成品索引穿透预检→lifespan 抛错）
        faiss_path = os.path.join(fastrag_dir, "kbindex_py", "chunk_index.faiss")
        data_path = os.path.join(fastrag_dir, "kbindex_py", "data.json")
        if not os.path.exists(faiss_path) or not os.path.exists(data_path):
            missing = "chunk_index.faiss" if not os.path.exists(faiss_path) else "data.json"
            miss_abs = faiss_path if missing == "chunk_index.faiss" else data_path
            logger.error(
                f"fastrag python 索引不完整：{missing} 缺失（{miss_abs}）。"
                f"problem=知识库索引缺失或上次构建中断；cause=尚未构建或构建中断；"
                f"fix=WebUI 知识库页点『构建知识库』，完成后重启系统")
            return False, f"fastrag 索引不完整：{missing} 缺失", "点『构建知识库』，完成后重启系统"
        # 预检②：入口脚本（副本区 fresh-install 现实态，DX F3：fix 给完整命令行+实际路径）
        if not os.path.exists(server_py):
            logger.error(
                f"fastrag server.py 不存在：{server_py}。problem=fastrag-python 副本缺失；"
                f"cause=fastrag 未复制；fix=在 AUTOlive 根目录运行 "
                f"python scripts/copy_edtalk.py 一键复制（含 fastrag-python/）")
            return False, f"fastrag server.py 不存在：{server_py}", "运行 scripts/copy_edtalk.py 一键复制脚本"
        # 预检③：解释器（Eng A1：resolve_interpreter 四级链，与 _start_edtalk_realtime 同源；
        # 构建按钮 llm.py 亦已统一切换，消除硬编码默认分叉）
        from utils.edtalk_realtime.runtime import resolve_interpreter
        interpreter = resolve_interpreter(config_data)
        if not interpreter or not os.path.exists(interpreter):
            logger.error(
                "fastrag 解释器未找到。problem=runtime312 缺失；"
                "cause=仓库副本与 EDTalk 安装目录均无 python.exe；"
                "fix=系统设置页配置 edtalk_realtime 解释器路径，或运行 scripts/copy_edtalk.py")
            return False, "fastrag 解释器未找到（runtime312 缺失）", "系统设置页配置 edtalk_realtime 解释器路径"

        # 端口解析（Eng E3：int() 收编 try——非法端口 fail-soft ok=False，
        # 与服务端 config.py P4 fail-fast 呼应）
        try:
            port = int(fastrag_cfg.get("port", 11420))
            if not (1 <= port <= 65535):
                raise ValueError
        except (TypeError, ValueError):
            bad_port = fastrag_cfg.get("port")
            logger.error(
                f"fastrag 端口配置非法：{bad_port!r}。problem=端口不是 1-65535 内整数；"
                f"cause=系统设置页 fastrag『端口』配置异常；fix=改为合法整数后重启系统")
            return False, f"fastrag 端口配置非法：{bad_port!r}", "修改 fastrag『端口』配置项（1-65535）"

        # 端口预检（D-14 connect_ex + Eng E4 幂等探测：fastrag 自身在运行→跳过拉起，
        # 防手动拉起过再点运行系统误报占用失败）
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _probe:
                _probe.settimeout(0.5)
                if _probe.connect_ex(("127.0.0.1", port)) == 0:
                    import urllib.request
                    try:
                        with urllib.request.urlopen(
                                f"http://127.0.0.1:{port}/health", timeout=2) as _resp:
                            if _resp.status == 200:
                                logger.warning(
                                    f"fastrag 服务已在运行（端口 {port}，/health 探测通过），跳过重复拉起")
                                return True, "", ""
                    except Exception:
                        pass
                    logger.error(
                        f"fastrag 端口 {port} 已被其他进程占用。problem=端口冲突；"
                        f"cause=端口被未知服务监听（非 fastrag）；fix=修改系统设置页 "
                        f"fastrag『端口』配置，或停止占用 {port} 的进程")
                    return False, f"fastrag 端口 {port} 已被占用", "修改 fastrag『端口』配置或停旧实例"
        except Exception:
            pass  # 探测失败不阻断拉起（保持宽容，Popen 层兜底）

        env = os.environ.copy()
        # 端口经环境变量注入（tomllib 只读，零 Config.toml 手术——native H2 延续；
        # python 路线由 config.py 补丁 P4 读 FASTRAG__SERVER__PORT）
        env["FASTRAG__SERVER__PORT"] = str(port)
        # 量化三键 env 注入（config.py P7 域校验 fail-fast；前端值域外回退默认并告警）
        for _key, _default, _domain in (
                ("llm_quant_bits", 16, (4, 8, 16)),
                ("reranker_quant_bits", 16, (4, 8, 16)),
                ("embedder_device", "cpu", ("cpu", "cuda-fp16", "cuda"))):
            _val = fastrag_cfg.get(_key, _default)
            if _val not in _domain:
                logger.warning(
                    f"fastrag {_key}={_val!r} 值域外，回退默认 {_default!r}。"
                    f"problem=配置非法；cause=WebUI 值异常；fix=在知识库页『推理配置』改为 {list(_domain)}")
                _val = _default
            env_key = f"FASTRAG__{_key.upper()}"
            env[env_key] = str(_val)

        try:
            # stdout/stderr 落盘（对齐 _start_edtalk_realtime：服务全 print 输出
            # 随 webui 控制台丢失则故障无从取证）
            log_dir = str(get_log_dir())
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "fastrag_server.log")
            log_f = open(log_path, "ab", buffering=0)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            log_f.write(f"\n===== 启动 {stamp} 端口 {port} 路线 python =====\n".encode("utf-8"))
            process = subprocess.Popen(
                [interpreter, server_py], cwd=py_dir, shell=False, env=env,
                stdout=log_f, stderr=subprocess.STDOUT)
            self.my_subprocesses["fastrag"] = process
            self._fastrag_log_f = log_f  # 保引用防 GC 关闭句柄
            logger.info(f"运行程序: fastrag（route=python，PID {process.pid}，端口 {port}，日志 {log_path}）")
            return True, "", ""
        except Exception as e:
            logger.error(f"启动 fastrag 失败: {e}")
            return False, f"启动 fastrag 失败: {e}", "请查看完整日志（log/fastrag_server.log）定位原因"

        # 注意（2026-10-15 EDTalk 整合义务修复）：此处曾残留 webui-bak 移植的
        # main.py 拉起代码——fastrag.enable=true 时 main.py 双拉起、首个进程成孤儿
        # （my_subprocesses["main"] 被覆盖，stop_programs 只杀得到第二个）。
        # main.py 的拉起由 run_external_program → _start_main_system 唯一负责，此处不再拉起。

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
        """停止一切由 AUTOlive 调度的进程（2026-11-07 进程治理批次，用户诉求：
        前端「停止」=零残留）。四层顺序：
        ①托管服务（ServiceRegistry 全实例 stop——rvc-vc 等 ManagedProcess，
          stop 内含 Windows 树杀，harvest 池/渲染子进程连带）；
        ②外围程序（my_subprocesses 的 edtalk_realtime/fastrag——原实现只停
          main，此二者有 pid 记录却从不停止=残留直接根源）；
        ③主系统树（taskkill /F /T——main 进程内的 inproc GSV/DanmakuListener
          及 main 拉起的 ManagedProcess 子进程按树连带终止）；
        ④残留收尸兜底（psutil 按命令行特征扫孤儿——registry 丢失/异常路径
          漏网的进程，特征=仓库根路径+模块标记，排除自身；api_v2 用户自备
          不在收尸范围，F11 不拉起不托管语义）；
        ⑤停止后 GPU 显存快照（PDH 路，2026-11-07 显存排查批次）——全停后
          残留显存的进程归属直接现形，为「逐模块激活二分」提供基线。
        重启=本方法+启动链（layout 既有串联），全部重启自动成立。"""
        # ①托管服务全停
        try:
            from utils.service_orchestrator import ServiceRegistry
            for svc in ServiceRegistry.instance().all():
                try:
                    logger.info(f"[stop] 停止托管服务: {svc.name}（state={svc.state}）")
                    svc.stop()
                except Exception as e:
                    logger.error(f"[stop] 托管服务 {svc.name} 停止失败（继续）: {e}")
        except Exception as e:
            logger.warning(f"[stop] ServiceRegistry 不可用（跳过①）: {e}")
        # ②外围程序（edtalk/fastrag——原实现漏停的直接根源）
        for name in ("edtalk_realtime", "fastrag"):
            self.stop_program(name)
        # ③主系统树
        self.stop_program("main")
        # ④收尸兜底
        try:
            self._reap_orphan_processes()
        except Exception as e:
            logger.warning(f"[stop] 收尸扫描异常（忽略）: {e}")
        # ⑤停止后 GPU 显存快照（2026-11-07 显存排查批次）：PDH 路落一条
        # 「谁还占着专用显存」——AUTOlive 全停后清零则残留归属本系统
        # （下一步逐模块激活二分定位）；仍高则归属系统侧（OBS/驱动等）。
        try:
            from utils.gpu_probe import gpu_memory_report
            logger.info(f"[stop] 停止后 GPU 显存快照:\n{gpu_memory_report()}")
        except Exception as e:
            logger.debug(f"[stop] GPU 快照不可用（忽略）: {e}")

    def _reap_orphan_processes(self) -> list:
        """残留收尸兜底：psutil 按命令行特征扫孤儿进程。

        特征 = cmdline 同时含【仓库根绝对路径】与【AUTOlive 托管模块标记】
        （rvc_adapter.py / realtime_serve / fastrag）；排除自身进程。
        api_v2.py 不在标记内（backend=api 用户自备，F11 不拉起不托管——杀
        用户自备服务违背预期）。返回被收尸的 pid 列表。"""
        import psutil
        home = str(get_base_path()).lower()
        markers = ("rvc_adapter.py", "realtime_serve", "fastrag")
        me = os.getpid()
        reaped = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                if proc.info["pid"] == me:
                    continue
                cmd = " ".join(proc.info["cmdline"] or []).lower()
                if not cmd:
                    continue
                if home in cmd and any(m in cmd for m in markers):
                    proc.kill()
                    reaped.append(proc.info["pid"])
                    logger.warning(f"[stop] 收尸残留进程 PID {proc.info['pid']}: {cmd[:120]}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not reaped:
            logger.info("[stop] 无残留进程")
        return reaped

    def run_external_program(self, type="webui"):
        """运行外部程序（与原文件 webui-bak.py L357-382 一致）"""
        if self.running_flag:
            if type == "webui":
                ui.notify(position="top", type="warning", message="运行中，请勿重复运行")
            return

        # 训练中启动主系统拦截（义务块"双向显存互斥拦截"升级：阻止整个启动而非
        # 仅跳过 EDTalk 拉起——design D2/D20：弹窗展示训练阶段+ETA+"前往训练页"跳转）
        try:
            from utils.train_streamer import pipeline as _train_pipeline
            if getattr(_train_pipeline, "training_running", False):
                prog = _train_pipeline.TrainPipeline.read_progress(
                    getattr(_train_pipeline, "training_role", "") or "")
                stage = (prog or {}).get("current_stage")
                stage_txt = f"阶段 {stage}/4" if stage else "进行中"
                eta_txt = ""
                if prog and prog.get("eta_s"):
                    eta_txt = f"，预计剩余约 {prog['eta_s'] // 60} 分钟"
                logger.warning("训练进行中，用户尝试启动主系统——已阻止（显存冲突防护）")
                ui.notify(
                    position="top", type="warning",
                    message=f"训练进行中（{stage_txt}{eta_txt}），主系统启动已阻止。"
                            f"请先停止训练或等其完成；可先用现有角色体验实时推理。",
                    actions=[{"label": "前往训练页", "type": "route",
                              "handler": lambda: ui.navigate.to("/train_streamer", new_tab=True)}],
                )
                return
        except ImportError:
            pass

        try:
            self.running_flag = True

            # 外围程序先行（D-14 分级：外围失败降级继续，不阻断主系统拉起）
            outer_failures = self.start_programs()

            # 启动主系统进程（启动链假启动修复 2026-10-12）：代码拆分后主系统
            # 独立为 main.py（控制面 API 8082 + 主循环），webui 仅前端——但启动链
            # 沿用 webui-bak 内嵌时代的逻辑，从未拉起 main.py（stop_programs 却
            # 一直有 "main" 停止项）→「启动系统」实际只拉外围程序，假启动。
            # sys.executable=webui 进程解释器（runtime312），与手动
            # `python main.py` 等价；stop_programs("main") 原样兼容。
            #
            # D-14 启动失败分支（/autoplan 批准批次切片 2）：主系统=关键模块，
            # 失败 → 复位 running_flag + negative 通知（附修复指引）+ 异常缓冲
            # 记录；外围模块失败 → log warning + 异常缓冲 + 降级继续（notify
            # warning 呈现"部分启动"显式状态）。
            ok, reason, fix_hint = self._start_main_system()
            if not ok:
                self.running_flag = False
                self.push_exception("main", reason, fix_hint=fix_hint)
                if type == "webui":
                    ui.notify(position="top", type="negative",
                              message=f"主系统启动失败：{reason}。修复指引：{fix_hint}")
                return {"code": -1, "msg": f"主系统启动失败！{reason}"}

            if outer_failures:
                labels = "、".join(
                    self._MODULE_LABELS.get(m, m) for m, _, _ in outer_failures)
                for m, r, h in outer_failures:
                    logger.warning(f"外围程序 {m} 启动失败（降级继续）: {r}")
                    self.push_exception(m, r, fix_hint=h)
                if type == "webui":
                    ui.notify(position="top", type="warning",
                              message=f"部分启动：{labels} 未启动（其余正常），详见侧边栏运行状态条")

            # 启动后一次性存活检查（E2）：3 秒后 poll 各子进程。部分失败场景下
            # start_programs 内部只 logger.error（沿用移植自 webui-bak 的行为），
            # 且 Windows shell=True 时命令不存在 Popen 也可能"假成功"——这里把
            # 秒退程序变成用户可见的 negative notify。不复位 running_flag：
            # 持续监控/自动恢复属独立议题（TODOS「main.py 子进程退出监控」条目）。
            # 通道分派（slot 修复 2026-10-12）：ui.timer 需要 NiceGUI slot 上下文，
            # 在 /sys_cmd 等 HTTP 请求上下文调用即抛 slot empty 异常——曾把 api
            # 通道的 run 连带 except 复位 running_flag（子进程成孤儿）。webui 通道
            # （按钮点击，有 slot）走 ui.timer+notify；api 通道走线程定时器+logger。
            def check_processes_alive(use_ui: bool = False):
                for name, proc in list(self.my_subprocesses.items()):
                    try:
                        if proc.poll() is not None:
                            logger.error(f"启动后检查: 程序 {name} 已退出 (code={proc.returncode})")
                            self.push_exception(name, f"程序启动后已退出 (code={proc.returncode})")
                            if use_ui:
                                ui.notify(position="top", type="negative",
                                          message=f"程序 {name} 启动后已退出，请查看日志")
                    except Exception as e:
                        logger.error(f"启动后检查 {name} 失败: {e}")

            if type == "webui":
                ui.timer(3.0, lambda: check_processes_alive(use_ui=True), once=True)
            else:
                threading.Timer(3.0, check_processes_alive).start()

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
        # 清除登录页面（整体删除：登录页为全屏 fixed 覆盖层，仅 clear 子元素会
        # 残留占满视口的空容器，把主界面顶出可视区）
        if getattr(self, 'login_column', None):
            self.login_column.delete()
            self.login_column = None

        # 创建主界面
        self.create_main_ui()
    
    def check_login(self):
        """检查登录状态"""
        if not get_nested_value(self.config._config, "login", "enable"):
            # 不需要登录，直接进入功能页
            self.goto_func_page()
            return

        # 会话令牌校验（cookie + 内存令牌表）：登录成功后经 JS 写入会话 cookie 并
        # 整页刷新，此处读 cookie 比对内存令牌，在页面构建上下文（slot 栈正常）里
        # 创建主界面。NiceGUI 3.x 事件处理器任务没有 slot 栈（新建元素无 parent
        # 不渲染，布局元素还会触发 require_top_level_layout 异常），不能在 handler
        # 里直接构建主界面。boot 阶段（app 未启动）无请求上下文，视为未登录。
        token = None
        if getattr(_nicegui_core.app, "is_started", False):
            try:
                token = context.client.request.cookies.get(self._AUTH_COOKIE)
            except RuntimeError:
                token = None
        if token is not None and token in AutoliveApp._shared_sessions:
            self.user_info = AutoliveApp._shared_sessions[token]
            logger.info(f"会话令牌校验通过，进入功能页（token={token[:8]}…）")
            if (get_nested_value(self.config._config, "login", "ums_api", default="")
                    and isinstance(self.user_info, dict) and "accessToken" in self.user_info):
                # UMS 登录：启动过期检查定时器
                ui.timer(600.0, lambda: self._check_expiration())
            self.goto_func_page()
            return

        # 显示登录页面
        self.login_column = self.login_form.create_login_form(
            on_login=self.handle_login,
            on_forget_password=self.handle_forget_password
        )

    def _enter_after_login(self):
        """登录成功后进入功能页。

        NiceGUI 3.x 事件处理器上下文没有 slot 栈（Element 无 parent 不渲染），
        不能在 handler 里直接构建主界面；页面构建阶段又读不到 app.storage.tab
        （tab_id 由 websocket 握手才建立）——改为签发随机会话令牌：写入类级内存
        令牌表（webui 重启即失效）+ JS 写会话 cookie，随后整页 reload，由
        check_login 在页面构建上下文里凭 cookie 完成鉴权与主界面构建。

        reload 以令牌为幂等守卫（sessionStorage 记录已消费的令牌）：websocket
        重连的消息重放不会再次触发 reload（否则重放→reload→重连→再重放会
        无限循环卡死页面）。
        """
        import secrets
        token = secrets.token_urlsafe(32)
        AutoliveApp._shared_sessions[token] = self.user_info
        cookie = f"{self._AUTH_COOKIE}={token}; path=/; SameSite=Strict"
        ui.run_javascript(
            f"if (sessionStorage.getItem('autolive_relogin') !== '{token}') {{"
            f"sessionStorage.setItem('autolive_relogin', '{token}');"
            f"document.cookie = '{cookie}';"
            f"location.reload();"
            f"}}"
        )
    
    def handle_login(self):
        """处理登录。

        鉴权顺序：配置了 ums_api 则优先走外部 UMS 服务（/auth/login，含账号到期
        管理）；UMS 未部署/未启动（重启后端口无监听、连接被拒）时兜底本地账号
        校验（login.username/password，即系统设置→账号管理页维护的账号）——
        修复开启登录后重启因 1119 拒绝连接而无法登录的问题。未配置 ums_api
        时直接走本地账号校验。
        """
        if not self.login_form.validate_input():
            return

        username = self.login_form.get_username()
        password = self.login_form.get_password()

        login_cfg = self.config._config.get("login", {})
        ums_api = login_cfg.get("ums_api", "")

        if ums_api:
            import requests
            from urllib.parse import urljoin

            try:
                API_URL = urljoin(ums_api, '/auth/login')

                resp_json = requests.post(API_URL, json={
                    "username": username,
                    "password": password
                }, timeout=5).json()

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

                app.storage.tab["login_notify"] = f'登录成功，账号到期时间：{expiration_ts}'
                logger.info(f"用户 {username} 登录成功（UMS）")

                # 写入登录态并整页刷新进入功能页
                self._enter_after_login()
                return

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                # UMS 服务不可达（本机未部署该服务/重启后未监听）：兜底本地账号校验
                logger.warning(f"UMS 服务不可达（{ums_api}），兜底本地账号校验：{e}")
            except Exception as e:
                logger.error(f"登录失败: {e}")
                ui.notify(position="top", type="negative", message=f"登录失败: {e}")
                return

        # 本地账号校验（与账号管理页配置的 login.username/password 比对；
        # 两者均未配置时保持旧行为直接放行）
        expected_username = str(login_cfg.get("username", "") or "")
        expected_password = str(login_cfg.get("password", "") or "")
        if expected_username or expected_password:
            if username != expected_username or password != expected_password:
                ui.notify(position="top", type="negative", message="用户名或密码不正确")
                return

        self.user_info = {"username": username}
        logger.info(f"用户 {username} 本地登录成功")

        # 写入登录态并整页刷新进入功能页
        self._enter_after_login()

    def handle_forget_password(self):
        """处理忘记密码"""
        ui.notify(position="top", type="info", message="本地账号可在『系统设置 → 账号管理』中查看/修改")
    
    def create_main_ui(self):
        """创建主界面 - 使用新的 drawer 布局"""
        # 使用新的 DrawerLayout，传入保存回调函数与 app 引用（E1：启停按钮经此调用
        # AutoliveApp 函数链；app 为 Optional，None 时按钮降级为禁用态，兼容测试构造）
        self.drawer_layout = DrawerLayout(
            self.config._config,
            save_callback=self.config.save,
            app=self,
            dark_mode=self.dark_mode
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

        # D-15 首启退役键面告知（延迟至客户端连接后 notify 可达；once=True）
        ui.timer(2.5, self._notify_retired_config_keys, once=True)

    def _notify_retired_config_keys(self):
        """D-15 首启退役键面告知（基础功能 2026-10-22 节 #9）：扫描存量退役
        键→notify+log 一次性告知（标志 retired_keys_notice_shown 防重复）。
        只做可见面不改配置；notify 失败（无客户端连接）不设标志，下轮启动再试。
        """
        try:
            if get_nested_value(self.config._config, "webui", "retired_keys_notice_shown"):
                return
            hits = []
            for keys, note in _RETIRED_CONFIG_KEYS:
                node = self.config._config
                for k in keys:
                    if not isinstance(node, dict) or k not in node:
                        node = None
                        break
                    node = node[k]
                if node is not None:
                    hits.append(f"{'.'.join(keys)}：{note}")
            if not hits:
                return
            msg = "检测到已退役配置键面（不影响运行，可删除）：\n" + "\n".join(hits)
            logger.warning(msg)
            ui.notify(position="top", type="warning", message=msg, timeout=10)
            # value 必须关键字传参（Settings.set(*keys, value) 签名，2026-10-24
            # 修复：原位置传参 TypeError 被吞→标志永不落盘→每次启动重复告知）
            self.config.set("webui", "retired_keys_notice_shown", value=True)
            self.config.save()
        except Exception as e:
            logger.debug(f"退役键面告知跳过（下轮启动再试）：{e}")

    def _monitor_subprocesses(self):
        """子进程存活监控：main 死亡→running_flag 复位+告警（auto_run 时自动重拉）。

        仅在 running_flag=True（运行中）时动作——主动停止流程已复位则静默跳过。
        main 重拉仅拉主系统（coordination/edtalk/fastrag 为独立进程各自存活，
        不经 run_external_program 重拉以免双实例）；重拉前查训练显存互斥
        （与 run_external_program 同判据），训练中跳过并保持告警等人工处理。

        D1（2026-10-19）：退出事件写入异常环形缓冲（状态条呈现）。
        报警重构（2026-10-26）：notify_enable 通道开关退役（弹窗告警常开）；
        edtalk_realtime/fastrag 退出并入报警模块（数字人服务/LLM服务）经
        WEBHOOK 推送；main 退出不在六模块清单内，维持弹窗+状态条。
        """
        if not self.running_flag:
            return
        for name, proc in list(self.my_subprocesses.items()):
            try:
                code = proc.poll()
            except Exception:
                continue
            if code is None:
                continue  # 存活
            logger.error(f"子进程退出监控：{name} 已退出（exit={code}）")
            self.my_subprocesses.pop(name, None)
            if name == "main":
                self.running_flag = False
                self.push_exception("main", f"主系统进程退出（exit={code}）",
                                    fix_hint="请查看完整日志定位原因后重新启动")
                ui.notify(position="top", type="negative",
                          message=f"主系统进程已退出（exit={code}）——请查看日志；启动按钮已恢复")
                if get_nested_value(self.config._config, "webui", "auto_run"):
                    try:
                        from utils.train_streamer import pipeline as _train_pipeline
                        if getattr(_train_pipeline, "training_running", False):
                            logger.warning("auto_run 自动重拉跳过：训练进行中（显存互斥），请训练完成后手动启动")
                            continue
                    except Exception:
                        pass
                    logger.warning("auto_run 已启用：自动重拉主系统 main")
                    ok, reason, fix_hint = self._start_main_system()
                    if ok and "main" in self.my_subprocesses:
                        self.running_flag = True
                    else:
                        self.push_exception("main", f"auto_run 自动重拉失败：{reason}",
                                            fix_hint=fix_hint)
            else:
                self.push_exception(name, f"外围程序退出（exit={code}）")
                ui.notify(position="top", type="warning",
                          message=f"外围程序 {name} 已退出（exit={code}）——请查看日志")
                if name == "edtalk_realtime":
                    alarm_notifier.alarm_error(
                        alarm_notifier.MODULE_DIGITAL_HUMAN,
                        f"EDTalk 实时推理进程退出（exit={code}）",
                        fix_hint="检查 EDTalk 服务日志与显卡占用后重新启动")
                elif name == "fastrag":
                    alarm_notifier.alarm_error(
                        alarm_notifier.MODULE_LLM,
                        f"知识库服务（fastrag）进程退出（exit={code}）",
                        fix_hint="检查 fastrag 服务日志后重新启动")
    
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

        def _http_auth_ok(request) -> bool:
            """HTTP 控制面鉴权门（T2 /set_config 加固交付包，F-sec-1/F-arch-1）。

            login.enable=true 时：会话 cookie（浏览器）或 X-Auth-Token 头（非浏览器
            调用方）须命中内存令牌表，否则 401——登录开启即同时锁 HTTP 控制端。
            login.enable=false（缺省，本地单用户场景）放行，暴露面由绑定默认
            127.0.0.1 + 0.0.0.0 启动告警（run() 内 C6 批）承担。异常 fail-closed。"""
            try:
                if not get_nested_value(self.config._config, "login", "enable"):
                    return True
                token = request.cookies.get(self._AUTH_COOKIE)
                if not token:
                    token = request.headers.get("X-Auth-Token")
                return bool(token) and token in AutoliveApp._shared_sessions
            except Exception:
                return False

        @app.post('/set_config')
        async def api_set_config(request: Request):
            """远程设置配置（T2 加固：路径白名单锚定 config.json + 鉴权门 + 原子写）"""
            try:
                if not _http_auth_ok(request):
                    return {"code": 401, "message": "未登录或会话已失效（login.enable=true 后控制端点须携带登录会话 cookie 或 X-Auth-Token）"}

                data_json = await request.json()
                logger.info(f'set_config接口 收到数据：{data_json}')

                config_path = data_json.get("config_path", "")
                config_data = data_json.get("data", {})

                if not config_path:
                    return {"code": -1, "message": "config_path不能为空"}

                # T2 路径白名单（F-arch-1）：config_path 锚定本 webui 的 config.json——
                # 原实现零校验可篡改任意既有 JSON 文件（关黑名单/改 WEBHOOK 指向）。
                # normcase+normpath 归一（Windows 大小写/分隔符/相对路径全部对齐）。
                import os as _os
                wanted = _os.path.normcase(_os.path.normpath(_os.path.abspath(str(get_config_path()))))
                given = _os.path.normcase(_os.path.normpath(_os.path.abspath(config_path)))
                if given != wanted:
                    logger.warning(f"set_config拒绝越权路径：{config_path}")
                    return {"code": -1, "message": "config_path 仅允许本系统 config.json（安全白名单，T2 批次）"}

                # 读取现有配置
                import json
                try:
                    with open(given, 'r', encoding="utf-8") as config_file:
                        existing_data = json.load(config_file)
                except Exception as e:
                    logger.error(f"无法读取配置文件！{e}")
                    return {"code": -1, "message": f"无法读取配置文件！{e}"}

                # 合并字典
                existing_data.update(config_data)

                # 原子写入（S-1 根修：临时文件+os.replace——中断只留完整旧/新文件）
                try:
                    from utils.config import atomic_write_json
                    atomic_write_json(given, existing_data, indent=2)
                    logger.info("配置数据已成功写入文件！")
                    return {"code": 200, "message": "配置数据已成功写入文件！"}
                except Exception as e:
                    logger.error(f"无法写入配置文件！{str(e)}")
                    return {"code": -1, "message": f"无法写入配置文件！{e}"}
            except Exception as e:
                logger.error(f"set_config处理失败: {e}")
                return {"code": -1, "message": f"set_config执行失败！{e}"}

        @app.post('/sys_cmd')
        async def api_sys_cmd(request: Request):
            """系统命令（run/stop/restart/factory；T2：login.enable=true 时须登录会话）"""
            try:
                if not _http_auth_ok(request):
                    return {"code": 401, "message": "未登录或会话已失效（login.enable=true 后控制端点须携带登录会话 cookie 或 X-Auth-Token）"}
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
            """数据回调（聊天记录）——UI-16 二轮验收任务 14：TODO 空实现修复，
            core 侧 webui_show_chat_log_callback 的聊天数据入共享缓冲，talk 页签
            ui.timer 轮询渲染（frontend/utils/chat_log_buffer.py）。"""
            try:
                data_json = await request.json()
                logger.info(f'WEBUI API callback接口收到数据：{data_json}')
                payload = (data_json or {}).get("data") or {}
                # 2026-10-25 重构：白名单——聊天记录收聊天功能互动
                # （voice=语音对话/chatbox=聊天框发送/comment=弹幕监听问答），
                # 点歌/积分/本地问答等其余来源（无标记/其它标记）在此丢弃
                # 2026-10-30 聊天审查批次：system=唤醒/睡眠/打断/舍弃系统反馈（D-10）
                if payload and payload.get("chat_source") in ("voice", "chatbox", "comment", "system"):
                    from frontend.utils.chat_log_buffer import append_chat
                    append_chat(payload)
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

        # 数字人素材管理控制面（2026-10-27 动作编排完善批次：素材管理&创建
        # 迁本进程独立线程——进 webui 即可添加/删除/改类型素材，无需启动主
        # 系统；config 传原始 dict（materials 侧按 dict.get 消费）。
        # 原 main 进程挂载已退役，写者矩阵=本进程单写者，防双写任务状态分裂）
        try:
            from utils.edtalk_realtime.materials import register_material_routes
            register_material_routes(app, self.config._config)
            logger.info("素材管理路由已挂载至 webui（/edtalk_materials/*）")
        except Exception:
            logger.error("素材管理路由挂载失败（动作编排页素材区不可用）", exc_info=True)

        # 点歌翻唱控制面（T3/2026-11-02 实测批次根修）：/cover/* 原注册于主进程
        # WebServer.app（uvicorn 8082），webui 单进程工作流不启动 8082——端点不可达
        # （实测 netstat 无监听+Max retries）。同素材路由模式挂本进程 NiceGUI app；
        # config 传 AutoliveApp.config（Config 对象形态，CoverQueue 零适配）。
        # webui 轻量进程无播放链（my_handle=None）→on_done=None=完成仅落盘，
        # 插播依赖点歌主链；webui 选择器前端经同源 8086 访问（_self_base）。
        try:
            from utils.cover_endpoints import register_cover_routes
            register_cover_routes(app, self.config,
                                  get_my_handle=lambda: getattr(self, "my_handle", None))
            logger.info("点歌翻唱路由已挂载至 webui（/cover/*，同源 8086）")
        except Exception:
            logger.error("点歌翻唱路由挂载失败（一键翻唱不可用）", exc_info=True)

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

    def _prewarm_tab_modules(self):
        """后台预热全部 tab 模块（断联修复，见 run() 挂点注释）。

        扫描 frontend/ui/tabs 包内全部 .py 逐个 importlib.import_module——
        torch 链等重加载在 daemon 线程完成，进页构建只剩 UI 装配（毫秒级）。
        磁盘扫描类（digital_human_models 清单）实测 ~2ms 无需预热。
        """
        import importlib
        import time as _time
        try:
            from frontend.ui import tabs as _tabs_pkg
            pkg_dir = os.path.dirname(_tabs_pkg.__file__)
            mods = [f"frontend.ui.tabs.{fn[:-3]}"
                    for fn in sorted(os.listdir(pkg_dir))
                    if fn.endswith(".py") and not fn.startswith("_")]
            t0 = _time.perf_counter()
            loaded = 0
            for m in mods:
                try:
                    importlib.import_module(m)
                    loaded += 1
                except Exception as e:
                    logger.warning(f"[预热] tab 模块加载失败（行为同旧，不阻断）: {m}: {e}")
            logger.info(f"[预热] tab 模块已后台加载 {loaded}/{len(mods)}"
                        f"（{_time.perf_counter() - t0:.1f}s）——进页构建不再阻塞事件循环")
        except Exception as e:
            logger.warning(f"[预热] tab 模块预热失败（行为同旧）: {e}")

    def run(self):
        """运行应用程序"""
        # 注册API端点
        self._register_api_endpoints()

        # 获取WebUI配置
        # 暴露面收敛（TODOS #6 / autoplan C6 批）：默认绑定改 127.0.0.1——旧默认 0.0.0.0
        # 会把无鉴权控制端（含 /sys_cmd run/stop/restart/factory）暴露给局域网。
        # 用户显式配置 0.0.0.0 时启动日志告警（页面常驻横幅由状态条任务 D-1 承载）。
        webui_ip = get_nested_value(self.config._config, "webui", "ip", default="127.0.0.1")
        if str(webui_ip) == "0.0.0.0":
            logger.warning(
                "webui 绑定为 0.0.0.0：控制端（含 /sys_cmd 启停/恢复出厂）将对局域网无鉴权暴露。"
                "确认需要局域网访问请保留；否则请在 webui 配置页把 IP 改回 127.0.0.1。"
                "（token 鉴权已登记 TODOS）"
            )
        webui_port = get_nested_value(self.config._config, "webui", "port", default=8086)
        webui_title = get_nested_value(self.config._config, "webui", "title", default="AUTOlive")

        # 设置页面标题
        ui.page_title(webui_title)

        # 检查登录状态
        self.check_login()

        # 发送心跳包（与原文件 webui-bak.py L5127 一致）
        # 注意：心跳包功能需要common模块支持，暂时用日志替代
        ui.timer(9 * 60, lambda: logger.debug("发送心跳包"))

        # NiceGUI 3.x script mode 重入防护：server 已启动时（root page 重跑），
        # UI 构建已完成（本方法前段），绝不能再 ui.run()——否则端口/事件循环冲突。
        from nicegui import core as _nicegui_core
        if getattr(_nicegui_core.app, "is_started", False):
            logger.debug("AutoliveApp.run() 重入——跳过 ui.run（server 已运行）")
            return

        # tab 模块后台预热（2026-11-09 断联修复，用户实报：进训练主播页重置回
        # 初始页+构建耗时 5.19s 警告）：NiceGUI 3.16 事件循环同步阻塞 >2s
        # （socket.io ping_timeout=2.0s 推导）即 WS 判死→浏览器断联重载。
        # train_streamer 等 tab 模块顶部 import torch 链秒级——首次进页在
        # socketio handler 内同步加载必超时。daemon 线程提前把全部 tab 模块
        # 拉进 sys.modules（进页构建只剩 UI 装配）；单模块失败静默（行为同旧）。
        threading.Thread(target=self._prewarm_tab_modules, daemon=True,
                         name="tab-prewarm").start()

        # C4-①（2026-11-03 治理批次，ENG-2 挂点修正）：Windows proactor 连接
        # 重置噪音过滤——挂点=on_startup（loop 就绪后恰执行一次；ui.run 之前
        # get_running_loop 首启必抛 RuntimeError）。幂等护栏防重复安装。
        def _install_loop_noise_filter():
            """C4-①：ConnectionResetError(10054) 的 proactor 连接丢失回调异常
            降为 DEBUG 单行（浏览器断开伴生噪音，erro5.log:52-59/91-106 实证）；
            过滤类别外异常链式委托原 handler，原 handler 为 None 时委托
            loop.default_exception_handler（spec CP-3：防非过滤异常静默吞弃）。"""
            import asyncio as _aio

            def _autolive_filter(loop, context):
                exc = context.get("exception")
                msg = str(context.get("message") or "")
                is_reset_noise = (
                    isinstance(exc, (ConnectionResetError, ConnectionAbortedError))
                    or (exc is not None and getattr(exc, "winerror", None) == 10054)
                    or "_call_connection_lost" in msg
                    and isinstance(exc, OSError)
                )
                if is_reset_noise:
                    logger.debug(f"[loop] 连接重置噪音已抑制: {exc or msg}")
                    return
                prev = getattr(_autolive_filter, "_prev_handler", None)
                if prev is not None:
                    prev(loop, context)
                else:
                    loop.default_exception_handler(context)

            def _prev_handler_setter(loop):
                prev = loop.get_exception_handler()
                _autolive_filter._prev_handler = prev

            async def _startup_install():
                loop = _aio.get_running_loop()
                if getattr(_autolive_filter, "_installed", False):
                    return  # 幂等（ENG-2：防重入重复安装成无界嵌套委托链）
                _prev_handler_setter(loop)
                loop.set_exception_handler(_autolive_filter)
                _autolive_filter._installed = True
                logger.info("[loop] proactor 连接重置噪音过滤器已挂载")

            _nicegui_core.app.on_startup(_startup_install)

        _install_loop_noise_filter()

        # NiceGUI 3.16 socket.io 消息超限断联（TODOS 既有问题）：AsyncServer 在
        # nicegui/nicegui.py:52 硬编码初始化，无 buffer 配置通道（默认 1MB，3.x script
        # mode 全量 UI 下发易超限致"连接丢失"）——启动前猴补调大至 10MB。
        # 注意 python-engineio 参数为 snake_case max_http_buffer_size（camelCase 是 JS 版）。
        # 升级 NiceGUI 若官方开放 socket.io 配置透传，移除本补丁改用官方参数。
        import socketio as _socketio
        if getattr(_socketio.AsyncServer.__init__, "_autolive_patched", None) is not True:
            _orig_server_init = _socketio.AsyncServer.__init__

            def _patched_server_init(self, *args, **kwargs):
                kwargs.setdefault("max_http_buffer_size", 10 * 1024 * 1024)
                _orig_server_init(self, *args, **kwargs)

            _patched_server_init._autolive_patched = True
            _socketio.AsyncServer.__init__ = _patched_server_init

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
        app = AutoliveApp()
        app.run()
    except Exception as e:
        logger.error(f"应用程序启动失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()