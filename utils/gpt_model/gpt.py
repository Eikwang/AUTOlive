# -*- coding: UTF-8 -*-
"""
@Project : AI-Vtuber 
@File    : gpt.py
@Author  : HildaM
@Email   : Hilda_quan@163.com
@Date    : 2023/06/23 下午 7:47 
@Description :  统一模型层抽象
"""
from utils.my_log import logger

from utils.gpt_model.custom_llm import Custom_LLM

# 视觉模型（gemini/zhipu 通道已弃用并移除，/autoplan 2026-09-22 用户裁决）
from utils.gpt_model.blip import Blip

class GPT_Model:
    openai = None

    def set_model_config(self, model_name, config):
        model_classes = {
            "custom_llm": Custom_LLM,
        }

        if model_name in model_classes:
            setattr(self, model_name, model_classes[model_name](config))

    def set_vision_model_config(self, model_name, config):
        model_classes = {
            "blip": Blip,
        }

        if model_name not in model_classes:
            # 旧配置可能残留已弃用通道（gemini/zhipu），回退到 blip 并告警，避免 KeyError 中断功能链路
            logger.warning(f"视觉模型通道 '{model_name}' 已弃用或不存在，已回退到 blip（配置项 image_recognition.model 建议更新为 blip）")
            model_name = "blip"

        setattr(self, model_name, model_classes[model_name](config))

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
