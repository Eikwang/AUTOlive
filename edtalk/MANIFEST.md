# EDTalk 运行副本 MANIFEST

- 复制日期：2026-09-24 07:08:46
- 来源：D:\AI\EDTalk
- 唯一运行副本声明：本目录（AUTOlive/edtalk）为唯一运行副本，EDTalk 原目录降为开发存档

| 源（D:\AI\EDTalk） | 目标（edtalk/） | git 处置 | 说明 | 大小(GB) |
|---|---|---|---|---|
| realtime_serve | realtime_serve | ignore-weights | 实时推理服务代码 | 0.37 |
| train_fine_tune.py | train_fine_tune.py | track | 底模微调入口 | 0.00 |
| fine_tune | fine_tune | track | 底模微调模块 | 0.00 |
| data_preprocess | data_preprocess | track | 11 步预处理链 | 0.00 |
| datasets | datasets | track | 训练数据集模块 | 0.00 |
| networks | networks | track | 网络模块 | 0.00 |
| hparams.py | hparams.py | track | 超参数 | 0.00 |
| audio.py | audio.py | track | 音频工具 | 0.00 |
| face_detection | face_detection | track | 人脸检测库 | 0.00 |
| face_sr | face_sr | track | 面部修复库 | 0.00 |
| pytorch_gaze_redirection | pytorch_gaze_redirection | track | 视线纠正库 | 0.40 |
| tools | tools | track | 评估工具（eval_finetune/mouth_metrics） | 0.00 |
| ckpts | ckpts | ignore | 底模权重（大文件） | 3.25 |
| ckpt_models/wy_runC | ckpt_models/wy_runC | ignore | RunC resume 产物（Eng F7） | 0.61 |
| fastrag | fastrag | ignore-exe | fastrag 服务（exe+模型） | 13.77 |
