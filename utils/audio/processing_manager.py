"""
音频处理模块 - 管理音频变速、合并等处理功能
"""

import os
import re
import traceback
from pydub import AudioSegment
from ..my_log import logger


class ProcessingMixin:
    """音频处理Mixin类"""
    
    def audio_speed_change(self, audio_path, speed_factor=1.0, pitch_factor=1.0):
        """音频变速

        Args:
            audio_path (str): 音频路径
            speed (int, optional): 部分速度倍率.  默认 1.
            type (int, optional): 变调倍率 1为不变调.  默认 1.

        Returns:
            str: 变速后的音频路径
        """
        logger.debug(f"audio_path={audio_path}, speed_factor={speed_factor}, pitch_factor={pitch_factor}")

        # 使用 pydub 打开音频文件
        audio = AudioSegment.from_file(audio_path)

        # 变速
        if speed_factor > 1.0:
            audio_changed = audio.speedup(playback_speed=speed_factor)
        elif speed_factor < 1.0:
            # 如果要放慢,使用set_frame_rate调帧率
            orig_frame_rate = audio.frame_rate
            slow_frame_rate = int(orig_frame_rate * speed_factor)
            audio_changed = audio._spawn(audio.raw_data, overrides={"frame_rate": slow_frame_rate})
        else:
            audio_changed = audio

        # 变调
        if pitch_factor != 1.0:
            semitones = 12 * (pitch_factor - 1)
            audio_changed = audio_changed._spawn(audio_changed.raw_data, overrides={
                "frame_rate": int(audio_changed.frame_rate * (2.0 ** (semitones / 12.0)))
            }).set_frame_rate(audio_changed.frame_rate)

        # 导出为临时文件
        audio_out_path = self.config.get("play_audio", "out_path")
        if not os.path.isabs(audio_out_path):
            if not audio_out_path.startswith('./'):
                audio_out_path = './' + audio_out_path
        file_name = f"temp_{self.common.get_bj_time(4)}.wav"
        temp_path = self.common.get_new_audio_path(audio_out_path, file_name)

        # 导出为新音频文件
        audio_changed.export(temp_path, format="wav")

        # 转换为绝对路径
        temp_path = os.path.abspath(temp_path)

        return temp_path


    def merge_audio_files(self, directory, base_filename, last_index, pause_duration=1, format="wav"):
        """合并文案音频文件"""
        merged_audio = None

        for i in range(1, last_index+1):
            filename = f"{base_filename}-{i}.{format}"  # 假设音频文件为 wav 格式
            filepath = os.path.join(directory, filename)

            if os.path.isfile(filepath):
                audio_segment = AudioSegment.from_file(filepath)
                
                if pause_duration > 0 and merged_audio is not None:
                    pause = AudioSegment.silent(duration=pause_duration * 1000)  # 将秒数转换为毫秒
                    merged_audio += pause
                
                if merged_audio is None:
                    merged_audio = audio_segment
                else:
                    merged_audio += audio_segment

                os.remove(filepath)  # 删除已合并的音频文件

        if merged_audio is not None:
            merged_filename = f"{base_filename}.wav"  # 合并后的文件名
            merged_filepath = os.path.join(directory, merged_filename)
            merged_audio.export(merged_filepath, format="wav")
            logger.info(f"音频文件合并成功：{merged_filepath}")
        else:
            logger.error("没有找到要合并的音频文件")


    async def audio_synthesis_use_local_config(self, content, audio_synthesis_type="gpt_sovits"):
        """使用本地配置进行音频合成，返回音频路径

        切片 3 键面重写（2026-10-20 实测修复）：旧实现硬取退役键
        （gpt_sovits.language/type/gradio_ip_port/api_0322...）且仅有 gpt_sovits
        分支——选 edge_tts 时 voice_tmp_path 无赋值路径，return 处抛
        UnboundLocalError（用户实测"语音生成测试失败"根因，弹幕链正常而
        测试链失败的分叉点）。新实现与弹幕链（synthesis_manager.tts_handle）
        同键面：gpt_sovits 参数装配收口在 _gpt_sovits_inproc（config.params
        为事实来源，C-1），edge_tts 走 edge_tts_api（voice/rate 自 edge_tts 节）。

        Args:
            content (str): 待合成的文本内容
            audio_synthesis_type (str, optional): 使用的tts类型. Defaults to "gpt_sovits".

        Returns:
            str: 合成的音频的路径（失败返回 None）
        """
        # 重载配置
        self.reload_config(self.config_path)

        voice_tmp_path = None
        if audio_synthesis_type == "gpt_sovits":
            data = {
                # P26：测试链默认语速取 params.speed_factor（与弹幕链 speed 透传同语义）
                "speed": self.config.get("gpt_sovits", "params", "speed_factor"),
                "content": content
            }
            voice_tmp_path = await self.my_tts.gpt_sovits_api(data)
        elif audio_synthesis_type == "edge_tts":
            voice_tmp_path = await self.my_tts.edge_tts_api({"content": content})
        else:
            logger.error(f"不支持的合成类型：{audio_synthesis_type}（可选 gpt_sovits / edge_tts）")

        return voice_tmp_path

