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
    # 串档防护一次性告警标记（2026-10-29：旧版 prompt_text 与多参考主音频不匹配）
    _legacy_prompt_mismatch_warned = False

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
        from utils.audio_handle.tts_params import (
            normalize_ref_paths, normalize_prompt_texts, build_inproc_refs,
            apply_text_fixes)
        engine = get_engine()
        if engine is None:
            logger.error("[TTS-inproc] 引擎未初始化（仅 main.py 核心进程可用）。"
                         "若在错误进程调用属缺陷——请检查启动流程")
            return None
        if engine.state == "red":  # 胶囊词汇=error
            logger.error("[TTS-inproc] 引擎 error 态——problem: 模型加载/推理失败；"
                         "cause: 见胶囊日志；fix: 工作台胶囊『重新加载模型』")
            return None
        if not engine.ready:
            logger.info(f"[TTS-inproc] 引擎 {engine.raw_state()} 态，本次请求等待加载完成后执行")
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
        # 2026-10-29 批次：参考文本与多音频逐行对应（prompt_texts[i] ↔ ref_audio_paths[i]）
        prompt_texts = normalize_prompt_texts(params_cfg.get("prompt_texts"))
        # 串档防护（2026-10-29 实测修复）：旧版单值 prompt_text 是旧版单值
        # ref_audio_path 的逐字转写。主参考实际来自 ref_audio_paths（多参考装配
        # ref_paths[0]）且 ≠ 旧版 ref_audio_path 时，旧转写与主参考非同一音频——
        # 禁止回落注入（实测：降级重试打出旧参考文本"マスター…"且音色错乱），
        # 宁可维持无参考文本语义。比较实际路径，旧单值与多参考首行相同时仍放行。
        legacy_prompt_text = merged.get("prompt_text") or ""
        if (legacy_prompt_text and ref_paths and merged.get("ref_audio_path")
                and ref_paths[0] != merged.get("ref_audio_path")):
            if not MY_TTS._legacy_prompt_mismatch_warned:
                MY_TTS._legacy_prompt_mismatch_warned = True
                logger.warning(
                    "[TTS-inproc] 旧版 prompt_text 与多参考主音频不匹配（该转写对应旧音频 "
                    f"{merged.get('ref_audio_path')}），已忽略——主参考转写请在 TTS 页参考文本首行填写")
            legacy_prompt_text = ""
            # 白名单合并已把旧 prompt_text 带入 merged——错配时须同步剥除，
            # 否则 fragments 省略 prompt_text 时 merged 残值仍透传引擎（S4 实证）
            merged.pop("prompt_text", None)
        ref_fragments = build_inproc_refs(
            ref_paths, ref_free, legacy_prompt_text,
            prompt_texts=prompt_texts)
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

        # 2026-10-30 审查批次：念法纠正文本前端（TTS 页即时配置，合成时现读）。
        # 多音字拼写改写（polyphone_map）同批退役——多音字统一走 GPT-SoVITS 引擎
        # 词典 多音字.txt（g2p 层改发音不改正文，发音精确；前端 TTS 页直读直写
        # 该文件），文本前端不再二次改写（防拼写串污染 g2p 输入）。
        from utils.audio_handle.tts_params import apply_text_fixes
        text = apply_text_fixes(
            data.get("content") or "",
            params_cfg.get("pronunciation_fix") or {},
            {})

        file_name = 'gpt_sovits_inproc_' + self.common.get_bj_time(4) + '.wav'
        voice_tmp_path = self.common.get_new_audio_path(self.audio_out_path, file_name)

        # 流式分段（2026-10-30 审查批次，任务书"流式输出"）：sink=逐段落盘回调
        # （tts_handle 注入，合成线程内同步调用）。每个回调块=TTS.run 一个切分
        # 片段的完整音频（streaming_mode=False 语义），独立落盘即推下游——
        # 首段落盘即入播放队列（不等全文合成完），响应速度显著提升；数字人
        # 对接=每段独立 push_full（Audio2Lip 异步推理，单飞行句背压保序）。
        sink = data.get("_on_segment")
        if callable(sink):
            import numpy as _np
            import soundfile as _sf
            out_dir = os.path.dirname(voice_tmp_path) or "."
            stem = os.path.splitext(os.path.basename(voice_tmp_path))[0] + "_seg"
            _seg_state = {"i": 0, "first": None}

            def _seg_writer(sr, chunk):
                idx = _seg_state["i"]
                _seg_state["i"] = idx + 1
                arr = _np.asarray(chunk, dtype=_np.int16)
                seg_path = os.path.join(out_dir, f"{stem}{idx}.wav")
                _sf.write(seg_path, arr, sr or 32000, subtype="PCM_16")
                if _seg_state["first"] is None:
                    _seg_state["first"] = seg_path
                try:
                    sink(seg_path, idx)
                except Exception:
                    logger.warning("[TTS-inproc] 流式分段回调异常（该段跳过）", exc_info=True)

            try:
                await asyncio.to_thread(
                    engine.synthesize, text, merged, voice_tmp_path, _seg_writer)
                return _seg_state["first"]
            except Exception:
                logger.error("[TTS-inproc] 流式分段合成失败", exc_info=True)
                return None

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
                # 降级恢复参考文本：prompt_texts 首行（主参考转写）> 旧版单值
                # prompt_text（已过串档防护——仅当主参考=旧版单值 ref_audio_path
                # 时才非空，见上方装配段）
                legacy_prompt = ((prompt_texts[0] if prompt_texts else "")
                                 or legacy_prompt_text)
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
                         f"fix: 检查胶囊状态与 ref_audio 配置")
            return None

    async def edge_tts_api(self, data):
        """edge-tts 合成（切片 3 静态部分：WebTTS 退役，改用 EDGE-TTS——任务书）。

        配置：edge_tts 节（voice/rate）；
        P26：data["speed"] 运行时语速经 speed_to_edge_rate 映射为 rate（优先于配置 rate）。
        2026-10-30：高级参数 JSON 透传（extra_args_json）退役移除——pitch 恒由
        随机语速联动接管（A2：speed_to_edge_pitch，+1.2x→"+10Hz"/0.9x→"-5Hz"，
        ±50Hz 截断），用户显式覆盖通道删除。
        spike（需运行环境实测）：edge-tts 网络出网与音色可用性（P26/P27 批次外）。
        """
        try:
            import edge_tts  # 延迟导入：依赖可选
        except ImportError:
            logger.warning("[TTS-edge] edge-tts 未安装——fix: pip install edge-tts")
            return None
        from utils.audio_handle.tts_params import (
            EDGE_TTS_DEFAULT_VOICE, EDGE_TTS_DEFAULT_RATE,
            speed_to_edge_rate, speed_to_edge_pitch, normalize_edge_rate)

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

        # A2 pitch 联动（2026-10-22 批次，义务 R2-A2）：运行时语速同步调 pitch 保持音高
        # 一致——pitch_hz=(factor-1)*50 即 +1.2 倍→"+10Hz"、0.9 倍→"-5Hz"，±50Hz 截断
        # （speed_to_edge_pitch）。仅语速驱动时联动（speed=None 走配置 rate 不调 pitch）。
        # d1a 裁决：试听不过则此联动一行禁用。
        synth_kwargs = {"rate": rate}
        if speed is not None:
            synth_kwargs["pitch"] = speed_to_edge_pitch(speed)

        file_name = 'edge_tts_' + self.common.get_bj_time(4) + '.mp3'
        voice_tmp_path = self.common.get_new_audio_path(self.audio_out_path, file_name)
        try:
            communicate = edge_tts.Communicate(text, voice, **synth_kwargs)
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
        """GPT-SoVITS 合成（2026-10-30 审查批次：本地合成统一进程内引擎）。

        「合成引擎（backend）」与「外部 API 地址」配置项退役移除——backend=api
        外部 HTTP 兜底通道（用户自备 api_v2 服务）随之删除，本方法恒走
        _gpt_sovits_inproc（进程内引擎，调用点/返回语义不变）。
        """
        return await self._gpt_sovits_inproc(data)

