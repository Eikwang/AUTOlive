"""
音频设备模块 - 提供音频设备信息和路径处理功能
"""

import os
import pyaudio

from ..base import logger


class AudioDeviceMixin:
    """音频设备 Mixin"""

    # 获取新的音频路径
    def get_new_audio_path(self, audio_out_path, file_name):
        # 判断路径是否为绝对路径
        if os.path.isabs(audio_out_path):
            # 如果是绝对路径，直接使用
            voice_tmp_path = os.path.join(audio_out_path, file_name)
        else:
            # 如果不是绝对路径，检查是否包含 ./，如果不包含，添加 ./，然后拼接路径
            if not audio_out_path.startswith('./'):
                audio_out_path = './' + audio_out_path
            voice_tmp_path = os.path.normpath(os.path.join(audio_out_path, file_name))

        voice_tmp_path = os.path.abspath(voice_tmp_path)

        return voice_tmp_path

    # 获取所有的声卡设备信息
    def get_all_audio_device_info(self, type):
        """获取所有的声卡设备信息

        Args:
            type (str): 声卡类型，"in" 或 "out"

        Returns:
            list: 声卡设备信息列表
        """
        audio = pyaudio.PyAudio()
        device_infos = []
        device_count = audio.get_device_count()

        for device_index in range(device_count):
            device_info = audio.get_device_info_by_index(device_index)
            if type == "out":
                if device_info['maxOutputChannels'] > 0:
                    device_infos.append({"device_index": device_index, "device_info": device_info['name']})
            elif type == "in":
                if device_info['maxInputChannels'] > 0:
                    device_infos.append({"device_index": device_index, "device_info": device_info['name']})
            else:
                device_infos.append({"device_index": device_index, "device_info": device_info['name']})

        return device_infos
