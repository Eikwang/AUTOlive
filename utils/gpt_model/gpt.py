# -*- coding: UTF-8 -*-
"""
@Project : AUTOlive 
@File    : gpt.py
@Author  : HildaM
@Email   : Hilda_quan@163.com
@Date    : 2023/06/23 下午 7:47 
@Description :  统一模型层抽象
"""
from utils.my_log import logger

from utils.gpt_model.custom_llm import Custom_LLM
from utils.gpt_model.fastrag_llm import Fastrag_LLM

class GPT_Model:
    openai = None

    def set_model_config(self, model_name, config, config_obj=None):
        # 2026-11-17 热切机制批次：透传共享 Config 实例（config_obj）——LLM 通道
        # 每请求现读节配置（model/api_key/base_url 等修改运行中生效）
        model_classes = {
            "custom_llm": Custom_LLM,
            "fastrag": Fastrag_LLM,
        }

        if model_name in model_classes:
            setattr(self, model_name, model_classes[model_name](config, config_obj=config_obj))

    def get(self, name):
        logger.info("GPT_MODEL: 进入get方法")
        try:
            if name != "reread":
                return getattr(self, name)
        except AttributeError:
            logger.warning(f"{name} 该模型不支持，如果不是LLM的类型，那就只是个警告，可以正常使用，请放心")
            return None


# 全局变量
GPT_MODEL = GPT_Model()
