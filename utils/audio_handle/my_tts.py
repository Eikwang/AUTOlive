import json, os
import aiohttp, ssl, asyncio
from urllib.parse import urlencode
from gradio_client import Client
import traceback
from urllib.parse import urljoin
import random, copy

from utils.common import Common
from utils.my_log import logger
from utils.config import Config

class MY_TTS:
    def __init__(self, config_path):
        self.common = Common()
        self.config = Config(config_path)

        # 创建一个不执行证书验证的 SSLContext 对象
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

        # 获取 werkzeug 库的日志记录器
        # werkzeug_logger = logger.getLogger("werkzeug")
        # # 设置 httpx 日志记录器的级别为 WARNING
        # werkzeug_logger.setLevel(logger.WARNING)

        # 请求超时
        self.timeout = 60

        # 使用内部成员做配置
        self.use_class_config = False
        # 备份一下配置
        self.class_config = copy.copy(self.config)

        try:
            self.audio_out_path = self.config.get("play_audio", "out_path")

            if not os.path.isabs(self.audio_out_path):
                if not self.audio_out_path.startswith('./'):
                    self.audio_out_path = './' + self.audio_out_path
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error("请检查播放音频的音频输出路径配置！！！这将影响程序使用！")


    # 获取随机数，单数据就是原数值，有-则判断为范围性数据，随机一个数值，返回float数据
    def get_random_float(self, data):
        # 将非字符串的情况统一处理为长度相同的最小值和最大值
        if isinstance(data, str) and "-" in data:
            min, max = map(float, data.split("-"))
        else:
            min = max = float(data)
        
        # 返回指定范围内的随机浮点数
        return random.uniform(min, max)

    # 音频文件base64编码 传入文件路径
    def encode_audio_to_base64(self, file_path):
        import base64

        if file_path == "" or file_path is None:
            return None

        with open(file_path, "rb") as audio_file:
            audio_data = audio_file.read()
            encoded_audio = base64.b64encode(audio_data).decode('utf-8')
        return encoded_audio

    async def download_audio(self, type: str, file_url: str, timeout: int=30, request_type: str="get", data=None, json_data=None, audio_suffix: str="wav"):
        async with aiohttp.ClientSession() as session:
            try:
                if request_type == "get":
                    async with session.get(file_url, params=data, timeout=timeout) as response:
                        if response.status == 200:
                            content = await response.read()
                            file_name = type + '_' + self.common.get_bj_time(4) + '.' + audio_suffix
                            voice_tmp_path = self.common.get_new_audio_path(self.audio_out_path, file_name)
                            with open(voice_tmp_path, 'wb') as file:
                                file.write(content)
                            return voice_tmp_path
                        else:
                            logger.error(f'{type} 下载音频失败: {response.status}')
                            return None
                else:
                    async with session.post(file_url, data=data, json=json_data, timeout=timeout) as response:
                        if response.status == 200:
                            content = await response.read()
                            file_name = type + '_' + self.common.get_bj_time(4) + '.' + audio_suffix
                            voice_tmp_path = self.common.get_new_audio_path(self.audio_out_path, file_name)
                            with open(voice_tmp_path, 'wb') as file:
                                file.write(content)
                            return voice_tmp_path
                        else:
                            logger.error(f'{type} 下载音频失败: {response.status}')
                            return None
            except asyncio.TimeoutError:
                logger.error("{type} 下载音频超时")
                return None

    async def gpt_sovits_api(self, data):
        import base64
        import mimetypes
        import websockets
        import asyncio

        def file_to_data_url(file_path):
            # 根据文件扩展名确定 MIME 类型
            mime_type, _ = mimetypes.guess_type(file_path)

            # 读取文件内容
            with open(file_path, "rb") as file:
                file_content = file.read()

            # 转换为 Base64 编码
            base64_encoded_data = base64.b64encode(file_content).decode('utf-8')

            # 构造完整的 Data URL
            return f"data:{mime_type};base64,{base64_encoded_data}"

               
        try:
            logger.debug(f"data={data}")
            
            if data["type"] == "gradio_0322":
                # gradio_client.predict() 是同步阻塞调用，使用 run_in_executor 避免阻塞事件循环
                loop = asyncio.get_event_loop()
                voice_tmp_path = await loop.run_in_executor(
                    None,
                    lambda: Client(data["gradio_ip_port"]).predict(
                        data["content"],	# str  in '需要合成的文本' Textbox component
                        data["api_0322"]["text_lang"],	# Literal['中文', '英文', '日文', '中英混合', '日英混合', '多语种混合']  in '需要合成的语种' Dropdown component
                        data["api_0322"]["ref_audio_path"],	# filepath  in '请上传3~10秒内参考音频，超过会报错！' Audio component
                        data["api_0322"]["prompt_text"],	# str  in '参考音频的文本' Textbox component
                        data["api_0322"]["prompt_lang"],	# Literal['中文', '英文', '日文', '中英混合', '日英混合', '多语种混合']  in '参考音频的语种' Dropdown component
                        data["api_0322"]["top_k"],	# float (numeric value between 1 and 100) in 'top_k' Slider component
                        data["api_0322"]["top_p"],	# float (numeric value between 0 and 1) in 'top_p' Slider component
                        data["api_0322"]["temperature"],	# float (numeric value between 0 and 1) in 'temperature' Slider component
                        data["api_0322"]["text_split_method"],	# Literal['不切', '凑四句一切', '凑50字一切', '按中文句号。切', '按英文句号.切', '按标点符号切']  in '怎么切' Radio component
                        int(data["api_0322"]["batch_size"]),	# float (numeric value between 1 and 200) in 'batch_size' Slider component
                        float(data["api_0322"]["speed_factor"]),	# float (numeric value between 0.25 and 4) in 'speed_factor' Slider component
                        data["api_0322"]["split_bucket"],	# bool  in '开启无参考文本模式。不填参考文本亦相当于开启。' Checkbox component
                        data["api_0322"]["return_fragment"],	# bool  in '数据分桶(可能会降低一点计算量,选就对了)' Checkbox component
                        data["api_0322"]["fragment_interval"],	# float (numeric value between 0.01 and 1) in '分段间隔(秒)' Slider component
                        api_name="/inference"
                    )
                )
                if voice_tmp_path:
                    new_file_path = self.common.move_file(voice_tmp_path, os.path.join(self.audio_out_path, 'gpt_sovits_' + self.common.get_bj_time(4)), 'gpt_sovits_' + self.common.get_bj_time(4))

                return new_file_path
            elif data["type"] == "api":
                try:
                    data_json = {
                        "refer_wav_path": data["ref_audio_path"],
                        "prompt_text": data["prompt_text"],
                        "prompt_language": data["prompt_language"],
                        "text": data["content"],
                        "text_language": data["language"]
                    }
                                        
                    return await self.download_audio("gpt_sovits", data["api_ip_port"], self.timeout, "post", None, data_json)
                except aiohttp.ClientError as e:
                    logger.error(traceback.format_exc())
                    logger.error(f'gpt_sovits请求失败: {e}')
                except Exception as e:
                    logger.error(traceback.format_exc())
                    logger.error(f'gpt_sovits未知错误: {e}')
            elif data["type"] == "api_0322":
                try:

                    data_json = {
                        "text": data["content"],
                        "text_lang": data["api_0322"]["text_lang"],
                        "ref_audio_path": data["api_0322"]["ref_audio_path"],
                        "prompt_text": data["api_0322"]["prompt_text"],
                        "prompt_lang": data["api_0322"]["prompt_lang"],
                        "top_k": data["api_0322"]["top_k"],
                        "top_p": data["api_0322"]["top_p"],
                        "temperature": data["api_0322"]["temperature"],
                        "text_split_method": data["api_0322"]["text_split_method"],
                        "batch_size":int(data["api_0322"]["batch_size"]),
                        "speed_factor":float(data["api_0322"]["speed_factor"]),
                        "split_bucket":data["api_0322"]["split_bucket"],
                        "return_fragment":data["api_0322"]["return_fragment"],
                        "fragment_interval":data["api_0322"]["fragment_interval"],
                    }
                                        
                    return await self.download_audio("gpt_sovits", data["api_ip_port"], self.timeout, "post", None, data_json)
                except aiohttp.ClientError as e:
                    logger.error(traceback.format_exc())
                    logger.error(f'gpt_sovits请求失败: {e}')
                except Exception as e:
                    logger.error(traceback.format_exc())
                    logger.error(f'gpt_sovits未知错误: {e}')
            elif data["type"] == "api_0706":
                try:

                    data_json = {
                        "text": data["content"],
                        "refer_wav_path": data["api_0706"]["refer_wav_path"],
                        "text_language": data["api_0706"]["text_language"],
                        "prompt_text": data["api_0706"]["prompt_text"],
                        "prompt_language": data["api_0706"]["prompt_language"],
                        "cut_punc": data["api_0706"]["cut_punc"],
                    }
                                        
                    return await self.download_audio("gpt_sovits", data["api_ip_port"], self.timeout, "post", None, data_json)
                except aiohttp.ClientError as e:
                    logger.error(traceback.format_exc())
                    logger.error(f'gpt_sovits请求失败: {e}')
                except Exception as e:
                    logger.error(traceback.format_exc())
                    logger.error(f'gpt_sovits未知错误: {e}')
            elif data["type"] == "v2_api_0821":
                try:
                    data_json = {
                        "text": data["content"],
                        "text_lang": data[data["type"]]["text_lang"],
                        "ref_audio_path": data[data["type"]]["ref_audio_path"],
                        "aux_ref_audio_paths": data[data["type"]]["aux_ref_audio_paths"],
                        "prompt_text": data[data["type"]]["prompt_text"],
                        "prompt_lang": data[data["type"]]["prompt_lang"],
                        "top_k": int(data[data["type"]]["top_k"]),
                        "top_p": float(data[data["type"]]["top_p"]),
                        "temperature": float(data[data["type"]]["temperature"]),
                        "text_split_method": data[data["type"]]["text_split_method"],
                        "batch_size": int(data[data["type"]]["batch_size"]),
                        "split_bucket": data[data["type"]]["split_bucket"],
                        "speed_factor": float(data[data["type"]]["speed_factor"]),
                        "fragment_interval": float(data[data["type"]]["fragment_interval"]),
                        "seed": int(data[data["type"]]["seed"]),
                        "media_type": data[data["type"]]["media_type"],
                        "streaming_mode": data[data["type"]]["streaming_mode"],
                        "parallel_infer": data[data["type"]]["parallel_infer"],
                        "repetition_penalty": float(data[data["type"]]["repetition_penalty"]),
                    }

                    API_URL = urljoin(data["api_ip_port"], '/tts')

                    return await self.download_audio("gpt_sovits", API_URL, self.timeout, "post", None, data_json)
                except aiohttp.ClientError as e:
                    logger.error(traceback.format_exc())
                    logger.error(f'gpt_sovits请求失败: {e}')
                except Exception as e:
                    logger.error(traceback.format_exc())
                    logger.error(f'gpt_sovits未知错误: {e}')
            
            elif data["type"] == "webtts":
                try:
                    # 使用字典推导式构建 params 字典，只包含非空字符串的值
                    params = {
                        key: value
                        for key, value in data["webtts"].items()
                        if value != ""
                        if key != "api_ip_port"
                    }

                    params["speed"] = self.get_random_float(params["speed"])
                    params["text"] = data["content"]

                    if params["version"] in ["1", "2"]:
                        return await self.download_audio("gpt_sovits", data["webtts"]["api_ip_port"], self.timeout, "get", params)
                    elif params["version"] == "1.4":
                        async with aiohttp.ClientSession() as session:
                            async with session.get(data["webtts"]["api_ip_port"], params=params, timeout=self.timeout) as response:
                                resp_json = await response.json()

                                url = urljoin(data["webtts"]["api_ip_port"], resp_json['url'])

                                return await self.download_audio("gpt_sovits", url, self.timeout, "get", params)
                except aiohttp.ClientError as e:
                    logger.error(traceback.format_exc())
                    logger.error(f'gpt_sovits请求失败: {e}')
                except Exception as e:
                    logger.error(traceback.format_exc())
                    logger.error(f'gpt_sovits未知错误: {e}')
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f'gpt_sovits未知错误，请检查您的gpt_sovits推理是否启动/配置是否正确，报错内容: {e}')
        
        return None

