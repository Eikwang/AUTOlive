import requests
import random
import json
import threading
from collections import OrderedDict
from hashlib import md5
import traceback
from pygtrans import Translate

from .common import Common
from .my_log import logger
from .config import Config


# T2b H-1（/autoplan main-20261031-201249 终门批准批次）：翻译 LRU 缓存——
# 键=(原文, 目标语言判定码, 通道)，仅缓存成功译文（各通道失败返回 None 不缓存，
# 失败语义不变）。同用户刷同内容/复读场景零外呼（baidu/google 计费+延迟+限流
# 规避，v1 审查 H-1）。键含通道：通道切换后各自独立缓存（argos 不可用回退语义
# 不被其他通道缓存遮蔽——实施期 test_tts_argos 暴露的真实语义缺口）。reload_config
# 时清空（通道/目标语言配置变更后缓存作废）。
_TRANSLATE_CACHE_MAX = 512
_translate_cache = OrderedDict()
_translate_cache_lock = threading.Lock()


def _cache_get(text, to_lang, type=None):
    try:
        with _translate_cache_lock:
            v = _translate_cache.get((text, to_lang, type))
            if v is not None:
                _translate_cache.move_to_end((text, to_lang, type))
            return v
    except Exception:
        return None


def _cache_put(text, to_lang, type, value):
    try:
        with _translate_cache_lock:
            _translate_cache[(text, to_lang, type)] = value
            _translate_cache.move_to_end((text, to_lang, type))
            while len(_translate_cache) > _TRANSLATE_CACHE_MAX:
                _translate_cache.popitem(last=False)
    except Exception:
        pass


class My_Translate:
    def __init__(self, config_path):
        self.config = Config(config_path)
        self.common = Common()

        self.config_data = self.config.get("translate")
        self.baidu_config = self.config.get("translate", "baidu")
        self.google_config = self.config.get("translate", "google")

        # uc2/LLM 翻译接线（切片 3 收尾）：my_handle 在 GPT 通道就绪后注入回调
        # fn(text) -> str 或 None；未注入时 custom_llm_trans 按未翻译处理
        self._llm_translator = None

    def set_llm_translator(self, fn) -> None:
        """注入 LLM 翻译回调（fn(text)->str；异常/None 由调用方按失败处理）。"""
        self._llm_translator = fn


    # 重载config（2026-10-31 翻译审查批次修复两处断链：①此前只重建 self.config，
    # config_data/baidu_config/google_config 三个 __init__ 缓存保持旧值——热重载后
    # trans() 仍按旧 type 分发、baidu/google 通道仍用旧密钥/语言码；②Config(path)
    # 构造在类级缓存非 None 时 no-op，真读盘必须走 Config.reload——本方法自包含，
    # 不再依赖调用方先刷新类缓存）
    def reload_config(self, config_path):
        Config.reload(config_path)
        self.config = Config(config_path)
        self.config_data = self.config.get("translate")
        self.baidu_config = self.config.get("translate", "baidu")
        self.google_config = self.config.get("translate", "google")
        # T2b H-1：翻译配置热重载后缓存作废（通道/目标语言可能已变）
        try:
            with _translate_cache_lock:
                _translate_cache.clear()
        except Exception:
            pass

    def trans(self, text, type=None) -> str:
        """通用翻译调用此函数

        Args:
            text (str): 待翻译的文本
            type (str): 翻译类型（baidu/google/argos/custom_llm）

        Returns:
            (str)：翻译后的文本
        """
        if type is None:
            type = self.config_data["type"]

        # T2b H-1：缓存查询（键=原文+目标语言+通道；命中即零外呼）
        to_lang = self._target_lang(type)
        cached = _cache_get(text, to_lang, type)
        if cached is not None:
            return cached

        if type == "baidu":
            out = self.baidu_trans(text)
        elif type == "google":
            out = self.google_trans(text)
        elif type == "argos":
            # P27：argos 离线兜底翻译（不可用返回 None，调用方按翻译失败降级）
            out = self.argos_trans(text)
        elif type == "custom_llm":
            out = self.custom_llm_trans(text)
        else:
            out = self.google_trans(text)

        # 仅缓存成功译文（None=失败→调用方回退原文，失败结果不缓存防固定坏值）
        if out is not None:
            _cache_put(text, to_lang, type, out)
        return out
        

    def baidu_trans(self, text):
        """百度翻译

        Args:
            text (str): 待翻译的文本

        Return:
            (str)：翻译后的文本
        """

        # Set your own appid/appkey.
        appid = self.baidu_config["appid"]
        appkey = self.baidu_config["appkey"]

        # For list of language codes, please refer to `https://api.fanyi.baidu.com/doc/21`
        from_lang = self.baidu_config["from_lang"]
        to_lang =  self.baidu_config["to_lang"]

        endpoint = 'http://api.fanyi.baidu.com'
        path = '/api/trans/vip/translate'
        url = endpoint + path

        # Generate salt and sign
        def make_md5(s, encoding='utf-8'):
            return md5(s.encode(encoding)).hexdigest()

        salt = random.randint(32768, 65536)
        sign = make_md5(appid + text + str(salt) + appkey)

        # Build request
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        payload = {'appid': appid, 'q': text, 'from': from_lang, 'to': to_lang, 'salt': salt, 'sign': sign}

        try:
            # Send request
            r = requests.post(url, params=payload, headers=headers)
            result = r.json()

            logger.info(f"百度翻译结果={result}")
            return result["trans_result"][0]["dst"]
            # Show response
            # print(json.dumps(result, indent=4, ensure_ascii=False))
        except Exception as e:
            logger.error(traceback.format_exc())

            return None


    def google_trans(self, text):
        """谷歌翻译

        Args:
            text (str): 待翻译的文本

        Return:
            (str)：翻译后的文本
        """
        try:
            if self.config_data['google']['proxy'] != "":
                proxies = {'https': self.config_data['google']['proxy']}
            else:
                proxies = None

            client = Translate(proxies=proxies)

            src_lang = self.config_data['google']['src_lang']
            if src_lang == "auto":
                src_lang = None

            # 翻译句子
            ret = client.translate(text, target=self.config_data['google']['tgt_lang'], source=src_lang)
            logger.debug(ret)

            return ret.translatedText
        except Exception as e:
            logger.error(traceback.format_exc())

            return None

    def argos_trans(self, text):
        """argos 离线翻译（P27：开源 argos-translate 整合，离线兜底定位）

        Args:
            text (str): 待翻译的文本

        Return:
            (str)：翻译后的文本；argos 不可用（未安装/离线模型缺失/语言对不支持）
                   返回 None——T4 降级：调用方按翻译失败处理
        """
        from utils.translate_argos import translate_argos
        from_lang = self.config.get("translate", "argos", "from_lang") or "auto"
        to_lang = self.config.get("translate", "argos", "to_lang") or "zh"
        return translate_argos(text, from_lang=from_lang, to_lang=to_lang)

    def custom_llm_trans(self, text):
        """自定义LLM翻译（uc2 终门裁决：LLM 翻译为默认首选新增选项）

        回调由 my_handle 在 GPT 通道就绪后经 set_llm_translator 注入；未注入或
        回调返回空/抛错时返回 None（调用方按翻译失败处理，弹幕原文直通 LLM
        主链路，不阻塞），不返回占位串以免污染下游。
        """
        if self._llm_translator is None:
            logger.warning(
                "[translate-llm] 自定义LLM翻译回调未注入——"
                "翻译请求按未翻译处理（返回原文走主链路）")
            return None
        try:
            out = self._llm_translator(text)
            return out or None
        except Exception as e:
            logger.error(f"[translate-llm] LLM 翻译失败，原文直通主链路：{e}")
            return None

    # ④ 翻译跳过优化（2026-10-27 架构优化批次）：翻译在回复主链串行（翻译结果
    # 即 TTS 输入，无法并行），但「文本已是目标语言」场景占多数——跳过可省一次
    # 串行 API 往返（LLM 通道翻译更是一次完整 LLM 调用）。
    _LANG_CODE_ALIASES = {
        "zh": "zh", "zh-cn": "zh", "zh-tw": "zh", "yue": "yue", "auto_yue": "yue",
        "jp": "ja", "ja": "ja", "ko": "ko", "en": "en",
    }

    def _target_lang(self, type=None):
        """读当前翻译通道目标语言，映射到 lang_check 判定码（未知返回 None）。"""
        try:
            raw = self.raw_target_lang(type)
            if type == "custom_llm":
                # custom_llm 目标=回复语言中文描述（raw_target_lang），反查判定码
                return {"中文": "zh", "英文": "en", "日文": "ja",
                        "韩文": "ko", "粤语": "yue"}.get(raw)
            return self._LANG_CODE_ALIASES.get(str(raw or "").strip().lower())
        except Exception:
            return None

    def raw_target_lang(self, type=None):
        """当前通道原始目标语言码（LLM 翻译提示词/弹幕翻译目标回退用）。

        2026-10-31 翻译审查批次新增：此前 my_handle 注入的 LLM 翻译回调把目标
        语言固化读 baidu.to_lang——用户选 argos/google 通道时目标语言仍跟百度
        配置。现在各通道读各自的 to_lang 配置；custom_llm 无通道语言码，按
        任务书语义读「通用配置-回复语言」（need_lang，中文描述供提示词）。
        """
        try:
            if type in (None, "baidu"):
                return self.baidu_config.get("to_lang") or "zh"
            if type == "google":
                return self.google_config.get("tgt_lang") or "zh"
            if type == "argos":
                return self.config.get("translate", "argos", "to_lang") or "zh"
            if type == "custom_llm":
                need = self.config.get("need_lang") or "none"
                return {"zh": "中文", "en": "英文", "ja": "日文", "jp": "日文",
                        "ko": "韩文", "yue": "粤语"}.get(need, "中文")
        except Exception:
            pass
        return "zh"

    def trans_comment(self, text):
        """弹幕链翻译入口（2026-10-31 翻译审查批次：补全 trans_type=弹幕）。

        此前「弹幕」翻译类型全库零消费——外语弹幕原样进 LLM。任务书语义：
        收到的信息非「通用配置-回复语言」时翻译为设定语言再处理。目标语言=
        need_lang 映射判定码；need_lang=none（未设定回复语言）回退当前翻译
        通道 to_lang。目标与文本语言一致跳过；翻译失败回原文（不阻塞主链）。

        调用位置=双链 lang_filter_pass 之前：译文语言=回复语言，语言过滤
        自然放行，翻译与 G3 过滤两功能叠加语义一致（翻译而非丢弃）。
        """
        if not text:
            return text
        try:
            type = self.config_data["type"]
        except Exception:
            return text
        target = None
        try:
            need_lang = self.config.get("need_lang") or "none"
        except Exception:
            need_lang = "none"
        if need_lang != "none":
            # need_lang 值域 {zh,en,jp,ko,yue} 与别名表键对齐（jp→ja 已有映射）
            target = self._LANG_CODE_ALIASES.get(str(need_lang).strip().lower())
        if target is None:
            target = self._target_lang(type)
        if target:
            detected = self.common.lang_check(text)
            if detected == target:
                logger.debug(f"[translate] 弹幕已为目标语言 {target}，跳过翻译")
                return text
        out = self.trans(text, type)
        return out if out else text

    def trans_reply(self, text):
        """回复链翻译入口（comment/talk 双链共用单点，D-20 纪律）：目标语言与
        文本语言一致时跳过翻译（省一次串行 API 往返）；否则按配置通道翻译。

        失败/未翻译返回原文（等价调用方既有 `if tmp: resp_content = tmp` 语义）。
        """
        if not text:
            return text
        try:
            type = self.config_data["type"]
        except Exception:
            return text
        target = self._target_lang(type)
        if target:
            detected = self.common.lang_check(text)
            if detected == target:
                logger.debug(f"[translate] 文本已为目标语言 {target}，跳过翻译")
                return text
        out = self.trans(text, type)
        return out if out else text