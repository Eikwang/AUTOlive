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

    # TTS.run 参数白名单（进程内直调；切片 3 参数精简——任务书"只保留参考音频/
    # 参考语种/合成语种/切分方式四项，其余使用系统默认"：top_k/top_p/temperature/
    # batch_size/fragment_interval/seed 等不再透传，引擎侧取默认值。
    # speed_factor 保留= P26 变速迁移（合成参数变速，优先于 pydub 回退路径）；
    # media_type/response_format/streaming_mode 为 HTTP 面专属，剥除）
    _INPROC_PARAM_KEYS = (
        "text_lang", "ref_audio_path", "aux_ref_audio_paths", "prompt_text", "prompt_lang",
        "text_split_method", "speed_factor",
    )

    # P25 降级一次性告警标记（多参考/ref_free 降级单参考时 log+notify 一次，防刷屏）
    _ref_fallback_warned = False

    async def _gpt_sovits_inproc(self, data):
        """R-2：进程内合成。参数装配顺序=gpt_sovits.params（C-1 事实来源）>
        data 运行时值（P25 多参考/ref_free、P26 speed 运行时覆盖）。

        P25 静态部分：参考音频支持列表（ref_audio_paths，第一行为主参考、
        其余进 aux_ref_audio_paths——上游 TTS.run 原生参数）+ ref_free
        无参考文本模式（默认开；装配层消化为 prompt_text=""，上游无 ref_free 键）。
        P25 spike（需 GPU 环境启动 inproc 实测）：N>1 参考 + 空参考文本的
        生效性——实测失败走降级单参考回退（log warning 一次）。

        P26 静态部分：data["speed"]（运行时语速）优先写入 speed_factor；
        pydub 变速为回退路径（playback_manager 仅对无 speed 参数的旧路径生效）。
        P26 spike（需 GPU 环境实测）：inproc speed_factor 生效性。
        """
        import asyncio
        from utils.gpt_sovits_inproc import get_engine
        from utils.audio_handle.tts_params import normalize_ref_paths, build_inproc_refs
        engine = get_engine()
        if engine is None:
            logger.error("[TTS-inproc] 引擎未初始化（仅 main.py 核心进程可用）。"
                         "若在错误进程调用属缺陷；可临时设 gpt_sovits.backend=api 走自备外部服务兜底")
            return None
        if engine.state == "red":  # 胶囊词汇=error
            logger.error("[TTS-inproc] 引擎 error 态——problem: 模型加载/推理失败；"
                         "cause: 见胶囊日志；fix: 工作台胶囊『重新加载模型』或设 backend=api 兜底")
            return None
        if not engine.ready:
            logger.warning(f"[TTS-inproc] 引擎 {engine.raw_state()} 态，本次请求等待加载完成后执行")
        gs = self.config.get("gpt_sovits") or {}
        params_cfg = dict(gs.get("params") or {})
        merged = {k: params_cfg.get(k) for k in self._INPROC_PARAM_KEYS
                  if params_cfg.get(k) is not None}
        merged["text_lang"] = data.get("language") or merged.get("text_lang") or "zh"

        # P25 多参考装配：ref_audio_paths 列表（运行时 > params 节）> 单值 ref_audio_path 兜底
        ref_paths = normalize_ref_paths(
            data.get("ref_audio_paths") or params_cfg.get("ref_audio_paths"))
        if not ref_paths and merged.get("ref_audio_path"):
            ref_paths = [merged["ref_audio_path"]]
        # ref_free 默认开（任务书：原系统支持无参考文本模式，直接使用该模式）
        ref_free = bool(params_cfg.get("ref_free", True))
        ref_fragments = build_inproc_refs(
            ref_paths, ref_free, merged.get("prompt_text") or "")
        merged.update(ref_fragments)
        if not ref_paths and "ref_audio_path" not in merged:
            logger.error("[TTS-inproc] 参考音频未配置——problem: ref_audio_path/ref_audio_paths 均缺失；"
                         "fix: 在 TTS 页配置参考音频或选用声音档案")
            return None

        # P26 变速迁移：运行时 speed 参数优先（合成参数变速）
        speed = data.get("speed")
        if speed is not None:
            try:
                merged["speed_factor"] = float(speed)
            except (TypeError, ValueError):
                pass

        text = data.get("content") or ""
        file_name = 'gpt_sovits_inproc_' + self.common.get_bj_time(4) + '.wav'
        voice_tmp_path = self.common.get_new_audio_path(self.audio_out_path, file_name)
        try:
            return await asyncio.to_thread(engine.synthesize, text, merged, voice_tmp_path)
        except Exception as first_err:
            # P25 降级预案：多参考/ref_free（空参考文本）实测失败 → 降级单参考重试一次
            degraded = dict(merged)
            fallback_used = False
            if degraded.get("aux_ref_audio_paths"):
                degraded.pop("aux_ref_audio_paths", None)
                fallback_used = True
            if ref_free:
                legacy_prompt = params_cfg.get("prompt_text") or ""
                if legacy_prompt:
                    degraded["prompt_text"] = legacy_prompt
                    fallback_used = True
                else:
                    degraded.pop("prompt_text", None)
            if fallback_used:
                if not MY_TTS._ref_fallback_warned:
                    MY_TTS._ref_fallback_warned = True
                    logger.warning(
                        "[TTS-inproc] 多参考音频/无参考文本模式合成失败，已降级单参考重试"
                        f"——problem: {first_err}；P25 spike 需在 GPU 环境实测 inproc 通道扩展性，"
                        "通道扩展任务将登记 TODOS")
                try:
                    return await asyncio.to_thread(engine.synthesize, text, degraded, voice_tmp_path)
                except Exception:
                    logger.error(traceback.format_exc())
            logger.error(f"[TTS-inproc] 合成失败——problem: {first_err}; "
                         f"cause: 引擎态/模型/参数（详见 gsv-inproc 日志）; "
                         f"fix: 检查胶囊状态与 ref_audio 配置，或 backend=api 兜底")
            return None

    async def edge_tts_api(self, data):
        """edge-tts 合成（切片 3 静态部分：WebTTS 退役，改用 EDGE-TTS——任务书）。

        配置：edge_tts 节（voice/rate/extra_args_json）；
        P26：data["speed"] 运行时语速经 speed_to_edge_rate 映射为 rate（优先于配置 rate）。
        高级参数 JSON 透传（escape hatch）：默认空=全默认，解析失败仅告警不阻塞。
        spike（需运行环境实测）：edge-tts 网络出网与音色可用性（P26/P27 批次外）。
        """
        try:
            import edge_tts  # 延迟导入：依赖可选
        except ImportError:
            logger.warning("[TTS-edge] edge-tts 未安装——fix: pip install edge-tts")
            return None
        from utils.audio_handle.tts_params import (
            EDGE_TTS_DEFAULT_VOICE, EDGE_TTS_DEFAULT_RATE,
            speed_to_edge_rate, speed_to_edge_pitch, parse_edge_extra_args, normalize_edge_rate)

        text = data.get("content") or ""
        if not text:
            logger.error("[TTS-edge] 合成文本为空")
            return None
        edge_cfg = self.config.get("edge_tts") or {}
        voice = data.get("voice") or edge_cfg.get("voice") or EDGE_TTS_DEFAULT_VOICE

        speed = data.get("speed")
        if speed is not None:
            rate = speed_to_edge_rate(speed)  # P26：运行时 speed → rate 百分比映射
        else:
            # 配置路径 rate 规范化：存量裸 "100%" 换算 "+0%"（edge-tts 7.x 拒收裸值）
            rate = normalize_edge_rate(edge_cfg.get("rate")) or EDGE_TTS_DEFAULT_RATE

        extra_args, extra_err = parse_edge_extra_args(edge_cfg.get("extra_args_json"))
        if extra_err:
            logger.warning(f"[TTS-edge] 高级参数 JSON 无效（已忽略，走全默认）——{extra_err}")

        # A2 pitch 联动（2026-10-22 批次，义务 R2-A2）：运行时语速同步调 pitch 保持音高
        # 一致——pitch_hz=(factor-1)*50 即 +1.2 倍→"+10Hz"，±50Hz 截断（speed_to_edge_pitch）。
        # 仅语速驱动时联动（speed=None 走配置 rate 不调 pitch）；用户 extra_args 显式
        # pitch 优先（escape hatch 覆盖自动联动）。d1a 裁决：试听不过则此联动一行禁用。
        synth_kwargs = {"rate": rate}
        if speed is not None:
            synth_kwargs["pitch"] = speed_to_edge_pitch(speed)

        file_name = 'edge_tts_' + self.common.get_bj_time(4) + '.mp3'
        voice_tmp_path = self.common.get_new_audio_path(self.audio_out_path, file_name)
        try:
            communicate = edge_tts.Communicate(text, voice, **synth_kwargs, **extra_args)
            await communicate.save(voice_tmp_path)
            logger.info(f"[TTS-edge] 合成成功 voice={voice} rate={rate}"
                        f"{' pitch=' + synth_kwargs['pitch'] if 'pitch' in synth_kwargs else ''} → {voice_tmp_path}")
            return voice_tmp_path
        except Exception as e:
            logger.error(f"[TTS-edge] 合成失败——problem: {e}; "
                         f"cause: 网络/音色名无效（详见上方配置）; "
                         f"fix: 检查出网与 voice 拼写，或切换语音合成为 gpt_sovits")
            return None

    async def gpt_sovits_api(self, data):
        # R-2：backend=inproc（默认）走进程内引擎——executor 线程+引擎互斥锁，零 HTTP；
        # backend=api 走既有 HTTP 兜底（用户自备外部 api_v2，F11 挂状态条）
        backend = (self.config.get("gpt_sovits", "backend") or "inproc")
        if backend == "inproc":
            return await self._gpt_sovits_inproc(data)
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

