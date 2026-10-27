import requests
import random
import json
from hashlib import md5
import traceback
from pygtrans import Translate

from .common import Common
from .my_log import logger
from .config import Config


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


    # 重载config
    def reload_config(self, config_path):
        self.config = Config(config_path)

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

        if type == "baidu":
            return self.baidu_trans(text)
        elif type == "google":
            return self.google_trans(text)
        elif type == "argos":
            # P27：argos 离线兜底翻译（不可用返回 None，调用方按翻译失败降级）
            return self.argos_trans(text)
        elif type == "custom_llm":
            return self.custom_llm_trans(text)
        else:
            return self.google_trans(text)
        

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
            translation = result["trans_result"][0]["dst"]
            translation = translation.replace("パパパパ", "パンパカパーン")
            translation = translation.replace("ボンボン", "パンパカパーン")
            translation = translation.replace("RPG", "アールピージー")
            translation = translation.replace("HP", "エイチピー")
            translation = translation.replace("桃ちゃん", "モモイ")
            translation = translation.replace("緑ちゃん", "ミドリ")
            translation = translation.replace("みどりちゃん", "ミドリ")
            translation = translation.replace("ゆずさん", "ユズ")
            translation = translation.replace("優香さん", "ユウカ")
            translation = translation.replace("優香", "ユウカ")
            translation = translation.replace("孥", "ヌ")

            return translation
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
            if type in (None, "baidu"):
                to = self.baidu_config.get("to_lang")
            elif type == "google":
                to = self.google_config.get("tgt_lang")
            elif type == "argos":
                to = self.config.get("translate", "argos", "to_lang")
            else:
                return None
            return self._LANG_CODE_ALIASES.get(str(to or "").strip().lower())
        except Exception:
            return None

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