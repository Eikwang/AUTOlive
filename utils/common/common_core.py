"""
通用工具核心模块 - 整合所有通用工具子模块的主要类
"""

from .validation_mixin import ValidationMixin
from .time_mixin import TimeMixin
from .dict_mixin import DictMixin
from .text_mixin import TextMixin
from .file_mixin import FileMixin
from .audio_device_mixin import AudioDeviceMixin
from .network_mixin import NetworkMixin


class Common(ValidationMixin, TimeMixin, DictMixin, TextMixin, FileMixin, AudioDeviceMixin, NetworkMixin):
    """
    通用工具主类 - 通过Mixin模式组合各个功能模块
    """
    def __init__(self):  
        self.count = 1
