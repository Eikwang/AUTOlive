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

        Args:
            content (str): 待合成的文本内容
            audio_synthesis_type (str, optional): 使用的tts类型. Defaults to "gpt_sovits".

        Returns:
            str: 合成的音频的路径
        """
        # 重载配置
        self.reload_config(self.config_path)

        if audio_synthesis_type == "gpt_sovits":
            if self.config.get("gpt_sovits", "language") == "自动识别":
                # 自动检测语言
                language = self.common.lang_check(content)

                logger.debug(f'language={language}')

                # 自定义语言名称（需要匹配请求解析）
                language_name_dict = {"en": "英文", "zh": "中文", "ja": "日文"}  

                if language in language_name_dict:
                    language = language_name_dict[language]
                else:
                    language = "中文"  # 无法识别出语言代码时的默认值
            else:
                language = self.config.get("gpt_sovits", "language")

            # 传太多有点冗余了
            data = {
                "type": self.config.get("gpt_sovits", "type"),
                "gradio_ip_port": self.config.get("gpt_sovits", "gradio_ip_port"),
                "ws_ip_port": self.config.get("gpt_sovits", "ws_ip_port"),
                "api_ip_port": self.config.get("gpt_sovits", "api_ip_port"),
                "ref_audio_path": self.config.get("gpt_sovits", "ref_audio_path"),
                "prompt_text": self.config.get("gpt_sovits", "prompt_text"),
                "prompt_language": self.config.get("gpt_sovits", "prompt_language"),
                "language": language,
                "cut": self.config.get("gpt_sovits", "cut"),
                "api_0322": self.config.get("gpt_sovits", "api_0322"),
                "api_0706": self.config.get("gpt_sovits", "api_0706"),
                "v2_api_0821": self.config.get("gpt_sovits", "v2_api_0821"),
                "webtts": self.config.get("gpt_sovits", "webtts"),
                "content": content
            }
                    
            # 调用接口合成语音
            voice_tmp_path = await self.my_tts.gpt_sovits_api(data)

        return voice_tmp_path


    async def copywriting_synthesis_audio(self, file_path, out_audio_path="out/", audio_synthesis_type="gpt_sovits"):
        """文案音频合成

        Args:
            file_path (str): 文案文本文件路径
            out_audio_path (str, optional): 音频输出的文件夹路径. Defaults to "out/".
            audio_synthesis_type (str, optional): 语音合成类型. Defaults to "gpt_sovits".

        Raises:
            Exception: _description_
            Exception: _description_

        Returns:
            str: 合成完毕的音频路径
        """
        try:
            max_len = self.config.get("filter", "max_len")
            max_char_len = self.config.get("filter", "max_char_len")
            file_path = os.path.join(file_path)

            audio_out_path = self.config.get("play_audio", "out_path")

            if not os.path.isabs(audio_out_path):
                if audio_out_path.startswith('./'):
                    audio_out_path = audio_out_path[2:]

                audio_out_path = os.path.join(os.getcwd(), audio_out_path)
                # 确保路径最后有斜杠
                if not audio_out_path.endswith(os.path.sep):
                    audio_out_path += os.path.sep


            logger.info(f"即将合成的文案：{file_path}")
            
            # 从文件路径提取文件名
            file_name = self.common.extract_filename(file_path)
            # 获取文件内容
            content = self.common.read_file_return_content(file_path)

            logger.debug(f"合成音频前的原始数据：{content}")
            content = self.common.remove_extra_words(content, max_len, max_char_len)
            # logger.info("裁剪后的合成文本:" + text)

            content = content.replace('\n', '。')

            # 变声并移动音频文件 减少冗余
            async def voice_change_and_put_to_queue(voice_tmp_path):
                voice_tmp_path = await self.voice_change(voice_tmp_path)

                if voice_tmp_path:
                    # 移动音频到 临时音频路径 并重命名
                    out_file_path = audio_out_path # os.path.join(os.getcwd(), audio_out_path)
                    logger.info(f"移动临时音频到 {out_file_path}")
                    self.common.move_file(voice_tmp_path, out_file_path, file_name + "-" + str(file_index))
                
                return voice_tmp_path

            # 文件名自增值，在后期多合一的时候起到排序作用
            file_index = 0

            # 是否语句切分
            if self.config.get("play_audio", "text_split_enable"):
                sentences = self.common.split_sentences(content)
            else:
                sentences = [content]

            logger.info(f"sentences={sentences}")
            
            # 遍历逐一合成文案音频
            for content in sentences:
                # 使用正则表达式替换头部的标点符号
                # ^ 表示字符串开始，[^\w\s] 匹配任何非字母数字或空白字符
                content = re.sub(r'^[^\w\s]+', '', content)

                # 设置重试次数
                retry_count = 3  
                while retry_count > 0:
                    file_index = file_index + 1

                    try:
                        voice_tmp_path = await self.audio_synthesis_use_local_config(content, audio_synthesis_type)
                        
                        if voice_tmp_path is None:
                            raise Exception(f"{audio_synthesis_type}合成失败")
                        
                        logger.info(f"{audio_synthesis_type}合成成功，合成内容：【{content}】，输出到={voice_tmp_path}") 

                        # 变声并移动音频文件 减少冗余
                        tmp_path = await voice_change_and_put_to_queue(voice_tmp_path)
                        if tmp_path is None:
                            raise Exception(f"{audio_synthesis_type}合成失败")

                        break
                    
                    except Exception as e:
                        logger.error(f"尝试失败，剩余重试次数：{retry_count - 1}")
                        logger.error(traceback.format_exc())
                        retry_count -= 1  # 减少重试次数
                        if retry_count <= 0:
                            logger.error(f"重试次数用尽，{audio_synthesis_type}合成最终失败，请排查配置、网络等问题")
                            self.abnormal_alarm_handle("tts")
                            return

            # 进行音频合并 输出到文案音频路径
            out_file_path = os.path.join(os.getcwd(), audio_out_path)
            self.merge_audio_files(out_file_path, file_name, file_index)

            file_path = os.path.join(os.getcwd(), audio_out_path, file_name + ".wav")
            logger.info(f"合成完毕后的音频位于 {file_path}")
            # 移动音频到 指定的文案音频路径 out_audio_path
            out_file_path = os.path.join(os.getcwd(), out_audio_path)
            logger.info(f"移动音频到 {out_file_path}")
            self.common.move_file(file_path, out_file_path)
            file_path = os.path.join(out_audio_path, file_name + ".wav")

            return file_path
        except Exception as e:
            logger.error(traceback.format_exc())
            return None
