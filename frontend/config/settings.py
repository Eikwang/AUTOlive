"""
配置管理模块
负责加载和管理应用程序配置
"""
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional


class Settings:
    """配置管理类"""

    def __init__(self, config_path: str):
        """
        初始化配置管理器

        Args:
            config_path: 配置文件路径
        """
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """加载配置文件。

        文件不存在 = 首次运行，空配置合法；
        存在但解析失败 = 硬失败（保留损坏副本供人工恢复），
        禁止静默降级为空配置（空配置一旦被 save 落盘会毁掉用户全部配置）。
        """
        if not self.config_path.exists():
            self._config = {}
            return
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = json.load(f)
        except Exception as e:
            backup = self.config_path.with_name(
                f"{self.config_path.stem}.corrupt-{int(time.time())}{self.config_path.suffix}"
            )
            try:
                shutil.copy2(self.config_path, backup)
            except Exception:
                backup = "（副本保留失败）"
            raise RuntimeError(
                f"配置文件解析失败：{e}。已保留损坏副本：{backup}。"
                f"请修复或恢复 {self.config_path} 后重新启动（ refusing to start with empty config）。"
            ) from e

    def save(self) -> None:
        """保存配置到文件（原子写：tmp + os.replace；异常上抛，不吞）。

        E-F6：写失败时调用方（开关处理器）需要感知以回滚 UI 状态，
        静默吞异常会造成「开关已翻转 + 假成功提示 + 重启回滚」事故。

        写者分配矩阵（Eng F-ENG-2 critical，2026-10-06；R2 实测修正）：danmaku_listener 节
        的磁盘回读**仅在 main.py 可达时执行**（运行期窄端点是该节的活跃写者——防本进程
        旧快照覆盖其写入）。main.py 不可达时本进程是该节唯一写者——直接 dump 内存
        （R2 首版无条件回读会把 set_config 刚写入内存的新值用磁盘旧值覆盖回滚——
        实测导致房间/关键词/开关全部"保存后消失"）。

        choreography 节（2026-11-04 实测三批）：**无条件整节磁盘回读（enable 也是）**。
        该节磁盘写者=main 编排端点（applied/edtalk_unreachable）+webui 降级窄写
        （main 不可达时）+二级菜单开关单键窄写（narrow_write_choreography_enable）
        ——三者都直接落盘；webui 内存该节只是进页快照（含 enable），任何一次全量
        save dump 都会用旧快照覆盖磁盘新值（实测「点应用保存成功→切二级菜单开关
        →重启后编排配置全部还原」）。整节回读同时完成内存对齐；开关新值由窄写
        先落盘，回读不吞（写者矩阵：webui 侧对该节零整节写入面）。
        """
        if not self._config:
            raise RuntimeError("内存配置为空，拒绝落盘（防止覆盖既有配置）")
        # 耗时观测（2026-11-04 实测三批）：save 在 socketio handler 内同步执行，
        # 曾报「保存后页面重启（点击无响应→回初始页）」——干净环境未复现，埋点
        # 取证（含 _main_reachable TCP 探测 + 两节磁盘回读）。>2s 阻塞即断连级。
        import time as _time
        _t0 = _time.perf_counter()
        # 写者分配矩阵（Eng F-ENG-2；R2 实测修正）：danmaku_listener 节磁盘回读
        # 仅在 main.py 可达时执行（运行期窄端点是该节活跃写者——防旧快照覆盖）。
        # main.py 不可达时本进程是该节唯一写者——直接 dump 内存。
        # （首版无条件回读会把 set_config 刚写入内存的新值用磁盘旧值覆盖——
        #   实测导致房间/关键词/开关全部"保存后消失"；第二版缩进错位使 dump
        #   整体丢失、save 变 no-op——本版为最终形态，含回归测试双用例。）
        if self._main_reachable():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    disk = json.load(f)
                if isinstance(disk, dict) and isinstance(disk.get("danmaku_listener"), dict):
                    self._config["danmaku_listener"] = disk["danmaku_listener"]
            except FileNotFoundError:
                pass
            except Exception:
                from utils.my_log import logger
                logger.warning("danmaku_listener 节磁盘回读失败——按内存快照落盘", exc_info=True)
        # choreography 节保护（2026-11-04 实测三批，写者矩阵见 docstring）：
        # 无条件整节磁盘回读（enable 也是——内存节整体只是快照）
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                disk_ch = json.load(f).get("choreography")
            if isinstance(disk_ch, dict):
                self._config["choreography"] = disk_ch
        except FileNotFoundError:
            pass
        except Exception:
            from utils.my_log import logger
            logger.warning("choreography 节磁盘回读失败——按内存快照落盘", exc_info=True)
        tmp_path = self.config_path.with_suffix(
            self.config_path.suffix + f".tmp{os.getpid()}"
        )
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.config_path)
        except Exception:
            try:
                if tmp_path.exists():
                    os.unlink(tmp_path)
            except OSError:
                pass
            raise
        finally:
            _elapsed = _time.perf_counter() - _t0
            if _elapsed > 2.0:
                from utils.my_log import logger
                logger.warning(
                    f"[配置保存] 耗时 {_elapsed:.2f}s（>2s——同步保存阻塞事件循环，"
                    f"main 可达性探测与节回读在 handler 线程执行，需排查磁盘/进程锁）")

    def narrow_write_choreography_enable(self, enable: bool) -> None:
        """choreography.enable 单键窄写（2026-11-04 实测三批）。

        写者矩阵：该节磁盘权威（main 编排端点/降级窄写为整节写者），save 全量
        dump 已回读保护——本进程整节写入面为零。二级菜单启用开关的单键变更
        走此处：读磁盘节 → 仅改 enable → tmp+os.replace 原子写回。异常上抛
        （调用方 set_config 回滚内存+UI）。"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            disk = json.load(f)
        node = disk.get("choreography")
        if not isinstance(node, dict):
            node = {}
        try:
            from utils.my_log import logger
            logger.info(f"[choreography] 写盘审计（二级菜单开关窄写）："
                        f"enable {node.get('enable')}→{bool(enable)}（其余键保持磁盘值）")
        except Exception:
            pass
        node["enable"] = bool(enable)
        disk["choreography"] = node
        tmp_path = self.config_path.with_suffix(
            self.config_path.suffix + f".tmp{os.getpid()}"
        )
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(disk, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.config_path)
        except Exception:
            try:
                if tmp_path.exists():
                    os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _main_reachable(self) -> bool:
        """main.py 控制面可达性探测（TCP 10ms；无 HTTP 依赖）。api_port 为 main.py FastAPI 端口。"""
        import socket
        try:
            port = int(self.get("api_port", default=8000))
        except Exception:
            port = 8000
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            return False

    def get(self, *keys: str, default: Any = None) -> Any:
        """
        获取配置值
        
        Args:
            *keys: 配置键路径（支持多级键）
            default: 默认值
            
        Returns:
            配置值，如果不存在则返回默认值
        """
        result = self._config
        for key in keys:
            if isinstance(result, dict):
                result = result.get(key, default)
            else:
                return default
        return result
    
    def set(self, *keys: str, value: Any) -> None:
        """
        设置配置值
        
        Args:
            *keys: 配置键路径（支持多级键）
            value: 要设置的值
        """
        if not keys:
            return
        
        config = self._config
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        
        config[keys[-1]] = value
    
    def update(self, data: Dict[str, Any]) -> None:
        """
        更新配置
        
        Args:
            data: 要更新的配置数据
        """
        self._deep_update(self._config, data)
    
    def _deep_update(self, base: Dict, update: Dict) -> Dict:
        """深度更新字典"""
        for key, value in update.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                self._deep_update(base[key], value)
            else:
                base[key] = value
        return base


# 全局配置实例
_config_instance: Optional[Settings] = None


# 新配置节默认值（EDTalk功能集成计划 §4.1.4：init 时 ensure-default，
# 加载后补写缺失键并落盘，防 get 返回 None）。
# 注意：此处只补"缺失键"，不覆盖用户已配置的值。
_EDTALK_REALTIME_DEFAULTS: Dict[str, Any] = {
    # 2026-10-26 配置面收敛：api_ip_port/port/interpreter_path/segments 均为
    # 系统内部量（utils/edtalk_realtime/runtime.py 自动解析+记忆；端口被占自动
    # 切换；段调度并入动作编排页），不再入默认表；存量键一次性告知后可手动删除。
    "enable": False,
    "character_dir": "wy",
    "target_fps": 25,
    "buffer_frames": 16,
    "preroll_frames": 8,
    "face_repair": "adaptive",
    "gaze_correction": "adaptive",
    "gaze_convergence": 0.05,
}

_FASTRAG_DEFAULTS: Dict[str, Any] = {
    "enable": False,
    "port": 11420,
    "stream_reply": True,        # fastrag 流式输出开关（2026-10-29 用户裁决独立键）
    "llm_quant_bits": 16,        # LLM 量化：4=INT4 / 8=INT8 / 16=BF16（启动 env 注入）
    "reranker_quant_bits": 16,   # Reranker 量化：4/8/16（同上）
    "embedder_device": "cpu",    # Embedder 设备：cpu / cuda-fp16 / cuda（同上）
    # 对话人设与生成参数（2026-10-30 修复回答规则重启丢失：UI『应用并重启』
    # 时写回此节作为回显真源；服务端运行态真源仍为 fastrag-python
    # config_override.py——P2 补丁优先级 override > 本默认，语义不变）
    "template": {
        "system_role": "你是一位专业、友好的线上销售，现在正在进行直播。",
        "answer_rules": "",
        "temperature": 0.3,
        "top_p": 0.9,
        "max_new_tokens": 128,
        "max_answer_length": 80,
    },
}

# DanmakuListener 整合（2026-09-27 计划 G6/DX）：弹幕监听组件配置节
_DANMAKU_LISTENER_DEFAULTS: Dict[str, Any] = {
    "enabled": False,                       # 总开关：false 时托管不启动、前端操作禁用
    "component_root": "danmaku_listener",   # 组件副本目录（copy_danmaku.py 复制范式；COPY_MANIFEST.json 版本戳）
    "login_entry": True,                    # NEEDS_LOGIN 扫码弹窗常驻入口
    "web_console": False,                   # 组件浏览器控制台调试通道（默认关；无鉴权仅本机，勿长开）
    "web_console_port": 8080,               # web_console 端口（8080 占用检测，EADDRINUSE 事件环报错）
    "queue_maxsize": 500,                   # 业务队列上限（可覆盖运行参数——DX 5.1 逃生门）
    "stall_threshold_seconds": 60,          # 收流停滞判定阈值（生命信号——P2-0 ②）
    "cookie_dir": "cookie",                 # 登录态目录（必须落 danmaku_listener/ 之外——copy_danmaku.py 重跑 rmtree）
    "platforms": {                          # 12 平台启用开关（就绪梯度默认 B站/斗鱼/抖音）
        "bilibili": True,
        "douyu": True,
        "douyin": True,
        "huya": False,
        "kuaishou": False,
        "taobao": False,
        "1688": False,
        "meituan": False,
        "xiaohongshu": False,
        "pdd": False,
        "jd": False,
        "wechat_channels": False,
    },
    "rooms": [],                            # 房间权威源 [{platform, room_id, enabled}]（active 已退役——添加即启用）
    "keywords": [],                         # 屏蔽关键词权威源（仅过滤 DANMU）
    "keyword_filter_enabled": True,         # 关键词过滤总开关
}

# audio_player/captions_printer 整合（2026-09-25 计划 §4.5/§5.4/R7）
# A5/F-4（2026-10-22 审查批次）：priority_mapping 收敛单源至 filter.priority_mapping——
# 本默认表不再携带优先级映射（audio_player.priority_mapping 配置节退役，运行时
# 合并迁移见 utils/audio/audio_core.Audio.__init__ 的 _merge_legacy_priority_mapping）。
_AUDIO_PLAYER_DEFAULTS: Dict[str, Any] = {
    "device_index": -1,
    "queue_max": 50,
    "audio_interval": 0,
    "random_audio_interval": {"enable": False, "min": 0.1, "max": 3},
    # api_ip_port 仅外部服务模式（audio_player/audio_player_v2）使用，保留兼容
}

_WEB_CAPTIONS_PRINTER_DEFAULTS: Dict[str, Any] = {
    "keep_time_align_audio": True,
    "queue_max": 100,
    "show_mode": "1",
    "single_char_show_time": 80,
    "gradient_show_time": 500,
    "hide_time": 1000,
    "show_over_hide_time": 2000,
    "bg_color": "rgba(0,0,0,0.6)",
    "body_bg_color": "none",
    "font_color": "#FFFFFF",
    "subtitle_font_family": "Microsoft YaHei",
    "subtitle_font_size": 24,
    "subtitle_font_weight": "bold",
    "subtitle_webkit_text_stroke": 0,
    "subtitle_stroke_color": "#000000",
    "subtitle_bg_width": 400,
    "subtitle_bg_height": 60,
}


def _ensure_defaults(config: "Settings") -> None:
    """补写新增配置节的缺失键（只补缺失，不覆盖既有值）并落盘。"""
    changed = False
    cfg = config._config

    # metahuman_stream 驱动退役归一化（2026-10-22 批次 G2/A3）：
    # 存量 visual_body=metahuman_stream → edtalk_realtime 并 log 告知
    if cfg.get("visual_body") == "metahuman_stream":
        cfg["visual_body"] = "edtalk_realtime"
        changed = True
        try:
            from utils.base import logger
            logger.warning(
                "visual_body=metahuman_stream 已弃用（2026-10-22 批次退役）——已自动迁移为 "
                "edtalk_realtime；如需回看请查发布说明。metahuman_stream 配置节不再读取")
        except Exception:
            pass
    # metahuman_stream 配置节退役（不再读取；保留于存量文件不影响运行）

    if "edtalk_realtime" not in cfg:
        cfg["edtalk_realtime"] = dict(_EDTALK_REALTIME_DEFAULTS)
        changed = True
    else:
        for key, value in _EDTALK_REALTIME_DEFAULTS.items():
            if key not in cfg["edtalk_realtime"]:
                cfg["edtalk_realtime"][key] = value
                changed = True
    if "fastrag" not in cfg:
        cfg["fastrag"] = dict(_FASTRAG_DEFAULTS)
        changed = True
    else:
        for key, value in _FASTRAG_DEFAULTS.items():
            if key not in cfg["fastrag"]:
                cfg["fastrag"][key] = value
                changed = True
    if "danmaku_listener" not in cfg:
        cfg["danmaku_listener"] = dict(_DANMAKU_LISTENER_DEFAULTS)
        changed = True
    else:
        for key, value in _DANMAKU_LISTENER_DEFAULTS.items():
            if key not in cfg["danmaku_listener"]:
                cfg["danmaku_listener"][key] = value
                changed = True
        # platforms 子字典逐键补齐（新增平台不丢开关）
        plats = cfg["danmaku_listener"].get("platforms")
        if not isinstance(plats, dict):
            plats = {}
            cfg["danmaku_listener"]["platforms"] = plats
            changed = True
        for key, value in _DANMAKU_LISTENER_DEFAULTS["platforms"].items():
            if key not in plats:
                plats[key] = value
                changed = True
    if "audio_player" not in cfg:
        cfg["audio_player"] = dict(_AUDIO_PLAYER_DEFAULTS)
        changed = True
    else:
        for key, value in _AUDIO_PLAYER_DEFAULTS.items():
            if key not in cfg["audio_player"]:
                cfg["audio_player"][key] = value
                changed = True
    if "web_captions_printer" not in cfg:
        cfg["web_captions_printer"] = dict(_WEB_CAPTIONS_PRINTER_DEFAULTS)
        changed = True
    else:
        for key, value in _WEB_CAPTIONS_PRINTER_DEFAULTS.items():
            if key not in cfg["web_captions_printer"]:
                cfg["web_captions_printer"][key] = value
                changed = True
        # 整合后内置化：外部服务地址键退役（计划 §5.4）
        if "api_ip_port" in cfg["web_captions_printer"]:
            del cfg["web_captions_printer"]["api_ip_port"]
            changed = True
    # custom_llm OpenAI-compatible 收敛（C4，2026-10）：旧 url/headers/body/data_analysis
    # → base_url/model/api_key（S2 裁决：响应解析固定 OpenAI schema，不留求值后门）。
    # 运行时（utils 侧 Custom_LLM.__init__）有同函数内存迁移兜底；此处负责磁盘落盘。
    from utils.gpt_model.custom_llm import migrate_legacy_config
    _llm_section = cfg.get("custom_llm")
    _migrated, _migrated_changed = migrate_legacy_config(
        _llm_section if isinstance(_llm_section, dict) else None
    )
    if _migrated_changed:
        cfg["custom_llm"] = _migrated
        changed = True

    # 念弹幕周期触发已退役（2026-10-31 念弹幕审查批次）：R10/EV-1g 的
    # periodic_time 合并迁移逻辑随之删除——read_comment 节已无 periodic_trigger
    # 子节，即时入队取代定时回放；存量 periodic_trigger 子节由
    # _RETIRED_CONFIG_KEYS 告知后可删

    # reread 节默认（2026-10-30 过滤审查批次）：复读触发配置组退役——弹幕关键词
    # 触发复读整体移除（reread_handle 收口为手动直通，不读配置），默认注入与
    # 存量节补齐逻辑随退役删除；存量 reread 节由 _RETIRED_CONFIG_KEYS 告知后可删

    # 助播触发类型 v2 迁移（/autoplan main-20261026-214845 D3/E-E6，终门 uc1a）：
    # comment→ai_chat（改名）；reread/reread_top_priority→chat（归拢"聊天"，
    # 勾过复读的用户自动获得聊天勾选，功能不丢失）；local_qa_audio/
    # image_recognition_schedule/key_mapping→删除（用户原文"移除"）。
    # 幂等：含 v2 值或无旧键时零改动；未配置 type 的用户=空集（行为零变化，
    # 不注入默认勾选）。助播文本匹配格式 json→text 一并收敛（F6 实证同一
    # 管道行加载器，行为等价）。
    _aa = cfg.get("assistant_anchor") if isinstance(cfg.get("assistant_anchor"), dict) else None
    if isinstance(_aa, dict) and isinstance(_aa.get("type"), list):
        _old_type_set = set(str(v) for v in _aa["type"])
        if _old_type_set & {"comment", "reread", "reread_top_priority",
                            "local_qa_audio", "image_recognition_schedule", "key_mapping"}:
            _new_type = []
            for _t in _aa["type"]:
                _ts = str(_t)
                if _ts == "comment":
                    _mapped = "ai_chat"
                elif _ts in ("reread", "reread_top_priority"):
                    _mapped = "chat"
                elif _ts in ("local_qa_audio", "image_recognition_schedule", "key_mapping"):
                    continue  # 用户原文"移除"
                else:
                    _mapped = _ts
                if _mapped not in _new_type:
                    _new_type.append(_mapped)
            _aa["type"] = _new_type
            changed = True
            try:
                from utils.base import logger
                logger.warning(
                    "assistant_anchor.type 已迁移为 v2 枚举（comment→ai_chat、"
                    "reread/reread_top_priority→chat、local_qa_audio/"
                    "image_recognition_schedule/key_mapping 移除）——详见发布说明")
            except Exception:
                pass
    if isinstance(_aa, dict):
        _aa_text = _aa.get("local_qa") if isinstance(_aa.get("local_qa"), dict) else None
        _aa_fmt = _aa_text.get("text") if isinstance(_aa_text, dict) else None
        if isinstance(_aa_fmt, dict) and _aa_fmt.get("format") == "json":
            _aa_fmt["format"] = "text"
            changed = True

    # 主 local_qa 收敛迁移（/autoplan main-20261026-214845 终门 rec 后续项）：
    # ① 存储格式键退役——local_qa.text.type 与遗留顶层 local_qa.format 均无
    #    消费（F6：json/text 历来同一加载器；键面原与匹配代码读取的顶层键分裂）；
    # ② 顶层 local_qa.file_path/similarity 遗留值迁入 text.* 子节（UI/匹配代码
    #    统一读子节；仅子节缺键时回填，不覆盖既有值）；
    # ③ 音频档位键补默认（UI 档位下拉，缺失=相似度匹配）。
    _lq = cfg.get("local_qa") if isinstance(cfg.get("local_qa"), dict) else None
    if isinstance(_lq, dict):
        _lq_text = _lq.get("text") if isinstance(_lq.get("text"), dict) else None
        if _lq_text is not None and "type" in _lq_text:
            del _lq_text["type"]
            changed = True
        if "format" in _lq:
            del _lq["format"]
            changed = True
        if _lq_text is None:
            _lq_text = {}
            _lq["text"] = _lq_text
        for _lk in ("file_path", "similarity", "username_max_len"):
            if _lk in _lq and _lk not in _lq_text:
                _lq_text[_lk] = _lq[_lk]
                changed = True
        _lq_audio = _lq.get("audio") if isinstance(_lq.get("audio"), dict) else None
        if _lq_audio is not None and "type" not in _lq_audio:
            _lq_audio["type"] = "相似度匹配"
            changed = True

    # thanks 三通道周期触发已退役（2026-10-31 答谢审查批次）：答谢三通道
    # （entrance/gift/follow）的 periodic_time 合并迁移逻辑随之删除——即时
    # 分发+E-2 限流取代定时回放，config 不再有 thanks.<type>.periodic_trigger
    # 子节，存量节由 _RETIRED_CONFIG_KEYS 告知后可删

    if changed:
        try:
            config.save()
        except Exception as e:
            # 落盘失败不阻塞启动（内存中默认值已生效），但要可见
            try:
                from utils.my_log import logger
                logger.error(f"新配置节默认值落盘失败（内存中已生效）：{e}")
            except Exception:
                pass


def get_config() -> Settings:
    """获取全局配置实例"""
    global _config_instance
    if _config_instance is None:
        raise RuntimeError("配置尚未初始化，请先调用 init_config()")
    return _config_instance


def init_config(config_path: str) -> Settings:
    """
    初始化全局配置

    Args:
        config_path: 配置文件路径

    Returns:
        配置实例
    """
    global _config_instance
    _config_instance = Settings(config_path)
    _ensure_defaults(_config_instance)
    return _config_instance


def get_function_groups() -> list:
    """
    获取功能分组列表

    Returns:
        功能分组列表
    """
    config = get_config()
    return config.get("webui", "function_groups", default=[])
