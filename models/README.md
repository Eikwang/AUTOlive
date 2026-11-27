模型资产根目录（发行改造 2026-11-22 统一布局，注册中心 utils/resource_paths.py 单一解析入口）

├── gpt_sovits/   GPT-SoVITS 底模（apps/gpt-sovits/GPT_SoVITS/pretrained_models 为 junction 回指此处）
├── rvc/          RVC 变声模型
├── asr/          SenseVoice + VAD（原 models/iic）
├── uvr5/         MDXNet 等人声分离模型（原 models/mdxnet）
├── edtalk/       EDTalk 底模权重（原 edtalk/ckpts；edtalk/ckpts 为 junction 回指）
└── tuber/        数字人角色包（原根级 tuber/，按角色分子目录）

config.json `paths` 节同名键（非空）可覆盖以上默认位置。
