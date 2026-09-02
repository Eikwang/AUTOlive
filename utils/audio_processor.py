"""
音频处理模块
提供录音、音频监听、音频播放等功能
"""

import threading
import time
import wave
from typing import List, Optional, Tuple

import numpy as np
import pyaudio

from utils.my_log import logger
from utils.config import Config
from utils.my_handle import My_handle
import utils.my_global as my_global


class AudioProcessor:
    """音频处理器类"""
    
    def __init__(self, config: Config, my_handle: Optional[My_handle] = None):
        """
        初始化音频处理器
        
        Args:
            config: 配置对象
            my_handle: 处理器对象
        """
        self.config = config
        self.my_handle = my_handle
        self.is_recording = False
        self.recording_thread = None
        
        # 音频参数
        self.chunk = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 44100
        
        logger.info("音频处理器初始化完成")
    
    def record_audio(self, output_path: str = "out/record.wav") -> bool:
        """
        录音功能（按右Shift键录音）
        
        Args:
            output_path: 录音输出路径
            
        Returns:
            bool: 录音是否成功（录音时间足够长）
        """
        import keyboard
        
        pressdown_num = 0
        p = pyaudio.PyAudio()
        stream = p.open(
            format=self.format,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk,
        )
        frames = []
        logger.info("Recording...")
        flag = 0
        
        while True:
            while keyboard.is_pressed("RIGHT_SHIFT"):
                flag = 1
                data = stream.read(self.chunk)
                frames.append(data)
                pressdown_num += 1
            if flag:
                break
        
        logger.info("Stopped recording.")
        stream.stop_stream()
        stream.close()
        p.terminate()
        
        # 保存录音文件
        wf = wave.open(output_path, "wb")
        wf.setnchannels(self.channels)
        wf.setsampwidth(p.get_sample_size(self.format))
        wf.setframerate(self.rate)
        wf.writeframes(b"".join(frames))
        wf.close()
        
        if pressdown_num >= 5:  # 粗糙的处理手段
            return True
        else:
            logger.info("杂鱼杂鱼，好短好短(录音时间过短,按右shift重新录制)")
            return False
    
    def audio_listen(self, volume_threshold: float = 800.0, 
                    silence_threshold: int = 15) -> List[bytes]:
        """
        音频监听功能（自动检测语音活动）
        
        Args:
            volume_threshold: 音量阈值
            silence_threshold: 沉默阈值
            
        Returns:
            List[bytes]: 录制的音频帧
        """
        audio = pyaudio.PyAudio()
        
        # 设置音频参数
        channels = self.config.get("talk", "CHANNELS")
        rate = self.config.get("talk", "RATE")
        device_index = int(self.config.get("talk", "device_index"))
        
        stream = audio.open(
            format=self.format,
            channels=channels,
            rate=rate,
            input=True,
            frames_per_buffer=self.chunk,
            input_device_index=device_index,
        )
        
        frames = []  # 存储录制的音频帧
        is_speaking = False  # 是否在说话
        silent_count = 0  # 沉默计数
        speaking_flag = False  # 录入标志位
        
        logger.info("[即将开始录音……]")
        
        while True:
            # 播放中不录音
            if self.config.get("talk", "no_recording_during_playback"):
                # 存在待合成音频 或 已合成音频还未播放 或 播放中 或 在数据处理中
                if (
                    self.my_handle.is_audio_queue_empty() != 15
                    or self.my_handle.is_handle_empty() == 1
                    or my_global.wait_play_audio_num > 0
                ):
                    time.sleep(
                        float(
                            self.config.get(
                                "talk", "no_recording_during_playback_sleep_interval"
                            )
                        )
                    )
                    continue
            
            # 读取音频数据
            data = stream.read(self.chunk)
            audio_data = np.frombuffer(data, dtype=np.short)
            max_dB = np.max(audio_data)
            
            if max_dB > volume_threshold:
                is_speaking = True
                silent_count = 0
            elif is_speaking is True:
                silent_count += 1
            
            if is_speaking is True:
                frames.append(data)
                if speaking_flag is False:
                    logger.info("[录入中……]")
                    speaking_flag = True
            
            if silent_count >= silence_threshold:
                break
        
        logger.info("[语音录入完成]")
        
        # 关闭音频流
        stream.stop_stream()
        stream.close()
        audio.terminate()
        
        return frames
    
    def start_recording_thread(self, callback=None):
        """
        启动录音线程
        
        Args:
            callback: 录音完成后的回调函数
        """
        if self.is_recording:
            logger.warning("录音已在进行中")
            return
        
        def recording_task():
            self.is_recording = True
            try:
                success = self.record_audio()
                if callback:
                    callback(success)
            except Exception as e:
                logger.error(f"录音失败: {e}")
                if callback:
                    callback(False)
            finally:
                self.is_recording = False
        
        self.recording_thread = threading.Thread(target=recording_task, daemon=True)
        self.recording_thread.start()
        logger.info("录音线程已启动")
    
    def stop_recording(self):
        """停止录音"""
        self.is_recording = False
        logger.info("录音已停止")
    
    def clear_audio_queues(self, message_queue: bool = True, 
                          voice_tmp_path_queue: bool = True, 
                          stop_audio_play: bool = True):
        """
        清空音频队列或停止播放
        
        Args:
            message_queue: 是否清空待合成消息队列
            voice_tmp_path_queue: 是否清空待播放音频队列
            stop_audio_play: 是否停止音频播放
        """
        if self.my_handle is None:
            logger.error("处理器未初始化，无法清空队列")
            return
        
        if message_queue:
            ret = self.my_handle.clear_queue("message_queue")
            if ret:
                logger.info("清空待合成消息队列成功！")
            else:
                logger.error("清空待合成消息队列失败！")
        
        if voice_tmp_path_queue:
            ret = self.my_handle.clear_queue("voice_tmp_path_queue")
            if ret:
                logger.info("清空待播放音频队列成功！")
            else:
                logger.error("清空待播放音频队列失败！")
        
        if stop_audio_play:
            ret = self.my_handle.stop_audio("pygame", True, True)
    
    def get_audio_devices(self) -> List[dict]:
        """
        获取可用的音频设备列表
        
        Returns:
            List[dict]: 音频设备信息列表
        """
        audio = pyaudio.PyAudio()
        devices = []
        
        for i in range(audio.get_device_count()):
            device_info = audio.get_device_info_by_index(i)
            devices.append({
                "index": i,
                "name": device_info["name"],
                "channels": device_info["maxInputChannels"],
                "sample_rate": device_info["defaultSampleRate"],
            })
        
        audio.terminate()
        return devices
    
    def test_audio_device(self, device_index: int, duration: float = 2.0) -> bool:
        """
        测试音频设备
        
        Args:
            device_index: 设备索引
            duration: 测试时长（秒）
            
        Returns:
            bool: 设备是否可用
        """
        try:
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                input=True,
                frames_per_buffer=self.chunk,
                input_device_index=device_index,
            )
            
            # 读取一小段音频数据
            data = stream.read(int(self.rate * duration))
            audio_data = np.frombuffer(data, dtype=np.short)
            
            stream.stop_stream()
            stream.close()
            audio.terminate()
            
            # 检查是否有有效音频数据
            if np.max(np.abs(audio_data)) > 0:
                return True
            return False
            
        except Exception as e:
            logger.error(f"测试音频设备失败: {e}")
            return False


# 创建全局实例（可选）
_audio_processor_instance = None


def get_audio_processor(config: Config = None, my_handle: My_handle = None) -> AudioProcessor:
    """
    获取音频处理器单例
    
    Args:
        config: 配置对象（首次调用时需要）
        my_handle: 处理器对象（首次调用时需要）
    
    Returns:
        AudioProcessor实例
    """
    global _audio_processor_instance
    if _audio_processor_instance is None:
        if config is None:
            raise ValueError("首次调用需要提供config参数")
        _audio_processor_instance = AudioProcessor(config, my_handle)
    return _audio_processor_instance


def create_audio_processor(config: Config, my_handle: My_handle = None) -> AudioProcessor:
    """
    创建音频处理器实例
    
    Args:
        config: 配置对象
        my_handle: 处理器对象
        
    Returns:
        AudioProcessor实例
    """
    return AudioProcessor(config, my_handle)
