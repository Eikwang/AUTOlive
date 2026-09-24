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
        """
        if not self._config:
            raise RuntimeError("内存配置为空，拒绝落盘（防止覆盖既有配置）")
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
                    os.remove(tmp_path)
            except Exception:
                pass
            raise
    
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
    "enable": False,
    "api_ip_port": "http://127.0.0.1:8000",
    "character_dir": "wy",
    "target_fps": 25,
    "buffer_frames": 16,
    "preroll_frames": 8,
    "face_repair": "adaptive",
    "gaze_correction": "adaptive",
    "gaze_convergence": 0.05,
    "segments": "auto",
    "host": "127.0.0.1",
    "api_token": "",
    "port": 8000,
    "interpreter_path": "D:\\AI\\EDTalk\\runtime312\\python.exe",
    "reaction_segment": True,
}

_FASTRAG_DEFAULTS: Dict[str, Any] = {
    "enable": False,
    "port": 11420,
}

# audio_player/captions_printer 整合（2026-09-25 计划 §4.5/§5.4/R7）
_AUDIO_PLAYER_DEFAULTS: Dict[str, Any] = {
    "device_index": -1,
    "queue_max": 50,
    "audio_interval": 0,
    "random_audio_interval": {"enable": False, "min": 0.1, "max": 3},
    "priority_mapping": {
        "reread_top_priority": 999,
        "comment": 30,
        "local_qa_audio": 30,
        "reread": 25,
        "gift": 20,
        "entrance": 20,
        "follow": 20,
        "thanks": 20,
        "song": 20,
        "read_comment": 20,
        "talk": 15,
        "idle_time_task": 10,
        "schedule": 10,
        "image_recognition_schedule": 10,
        "trends_copywriting": 10,
        "abnormal_alarm": 5,
        "copywriting": 1,
    },
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
    "subtitle_bg_width": 400,
    "subtitle_bg_height": 60,
}


def _ensure_defaults(config: "Settings") -> None:
    """补写新增配置节的缺失键（只补缺失，不覆盖既有值）并落盘。"""
    changed = False
    cfg = config._config
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
    # coordination_program 过期外部服务条目清理（计划 §4.6）
    coord = cfg.get("coordination_program")
    if isinstance(coord, list) and coord:
        cleaned = [c for c in coord if isinstance(c, dict) and c.get("executable", "").startswith("E://GitHub_pro//") is False]
        if len(cleaned) != len(coord):
            cfg["coordination_program"] = cleaned
            changed = True
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
