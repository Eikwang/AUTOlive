<div align="center">

# ✨ AUTOlive ✨

[![python](https://img.shields.io/badge/python-3.10+-blue.svg?labelColor=black)](https://www.python.org/)

</div>

**AUTOlive** 是一款 AI 直播数字人整合中枢：直播弹幕实时驱动对话与互动，数字人形象实时开口回应。本项目 fork 自 [Ikaros-521/AI-Vtuber](https://github.com/Ikaros-521/AI-Vtuber)（Luna AI），已更名为 **AUTOlive** 独立演进（hard fork），整合了弹幕监听、对话生成、语音合成、数字人驱动与训练的完整直播链路。感谢原作者 [Ikaros-521](https://github.com/Ikaros-521) 与上游社区贡献者的杰出工作。

## 🧩 核心能力

- **弹幕互动（收与发）**：内置弹幕监听组件覆盖 **12 个直播平台**（抖音、斗鱼、哔哩哔哩、快手、虎牙、微信视频号、淘宝、1688、美团、小红书、拼多多、京东），进程内运行、逐引擎隔离；支持配置期扫码登录、多房间监听、关键词屏蔽；弹幕发送为第一阶段能力（实验性）。
- **对话链**：LLM 通道统一 **OpenAI-compatible** 三字段接入（base_url/model/api_key，兼容 DeepSeek/Qwen/GLM/Moonshot/Ollama 等主流平台），弹幕/礼物/进场/关注事件驱动回复，fastrag 本地知识库问答，积分体系（签到/礼物/入场/关注计分与查询）。
- **语音链路**：TTS 与实时变声的托管服务——[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)（文本转语音）、RVC（实时变声热切换）、AICoverGen（一键翻唱）作为一等公民随系统拉起，含声音训练流水线（训练声音页）。
- **数字人**：[EDTalk](https://github.com/References-of-Talk/EDTalk) 实时推理驱动（音频推送、口型同步），配套"训练主播"一键全链路训练（audio2lip ckpt）。
- **字幕与播放**：内置字幕前端（web_captions），音频播放器/字幕打印机内置化，断线自检。

## 🚀 快速开始

1. 克隆本仓库：
   ```bash
   git clone https://github.com/Eikwang/AUTOlive.git
   cd AUTOlive
   ```
2. 准备统一 Python 运行时 `runtime312`（EDTalk 共享环境，`D:\AI\EDTalk\runtime312`），环境补丁清单见 [docs/开发环境搭建.md](./docs/开发环境搭建.md)；
   - **ffmpeg**：安装后配置到系统环境变量 PATH（用于音频变速、变调等功能）。视频教程：https://www.bilibili.com/video/BV1S14y1Q799
3. 双击 `启动程序.bat` 启动 WebUI（默认 `http://127.0.0.1:8086`）；
4. 按 [docs/danmaku_module.md](./docs/danmaku_module.md) 的**三阶段上手**完成弹幕监听接入（环境自检 → 冒烟 → 添加房间到对话链）。

## 🏗️ 架构简述

```
WebUI(8086) + 主循环（对话链）
 ├── DanmakuListener：进程内 12 平台弹幕引擎（逐引擎隔离）
 ├── GPT-SoVITS / RVC：托管子进程（apps/ 一等公民，hosted 可关）
 └── EDTalk realtime：HTTP 音频推送（16kHz），外部 runtime
```

细节见 [docs/architecture/系统拓扑.md](./docs/architecture/系统拓扑.md) 与 [docs/danmaku_module.md](./docs/danmaku_module.md)。

## 💡 提问的智慧

提交 issues 前请先阅读：https://lug.ustc.edu.cn/wiki/doc/smart-questions

## 🀅 开发&项目相关

### 简易流程图

![image](./docs/simple_flowchart.png)
![image](./docs/chart.jpg)

**主线运行流程图（2026-10-22 主干图 v2，分层双图）**：
- 主干图：[docs/flowchartTD.md](./docs/flowchartTD.md) —— 人读、随批次维护，含四态图例（〔真实〕运行链在用 / 〔断链〕代码在但链路断开，出现即开批次修复 / 〔骨架〕框架在实现空 / 〔规划〕未开发）与终门裁决注释。
- 详图（只读历史归档）：[docs/flowchart-detail.md](./docs/flowchart-detail.md) —— 2026-10-20 图-码对齐版完整快照，逐节点代码锚点与前提表（基线 commit dcf1ea51，锚点不随后续批次复核）。
断链批次状态注记：按键映射（T5）、答谢生产端（T6）、黑名单（T7）、闲时任务（T8）、动态文案（T9）已修复收官；语音对话（T10，TODOS）仍为〔骨架〕待接通。修复后需同步更新主干图（改链必改图）。

### 改动记录

- [docs/开发记录/2026-10-12_整合阶段改动盘点.md](./docs/开发记录/2026-10-12_整合阶段改动盘点.md)——整合阶段（2026-09-24 → 10-12）76 提交盘点与全面测试准备清单

## License

本项目遵循 [GPL-3.0](./LICENSE) 协议开源，原始版权与许可声明见 [LICENSE](./LICENSE)。
