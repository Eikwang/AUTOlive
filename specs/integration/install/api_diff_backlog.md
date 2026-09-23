# API 差异/适配遗留清单（统一环境补装过程产生）

> 约定：统一环境（runtime312）内只允许 pip；以下条目均无 cp312 win_amd64 预编译 wheel，
> 源码编译失败（VS BuildTools 为 x86 链接器配置，无法编 x64 扩展）。按"冒烟不过关的服务留旧环境"原则处理。

| 包 | 谁需要 | 影响 | 状态/兜底 |
|---|---|---|---|
| pesq | GPT-SoVITS BigVGAN 训练 | 仅训练 BigVGAN 时需要；推理链路不需要 | 挂起；训练需求出现时自编 wheel 或在 Linux/旧环境训练 |
| pyopenjtalk | GPT-SoVITS 日语文本前端 | 中文/英文 TTS 链路不需要 | 挂起；日语音素需求出现时从官方集成环境提取 wheel |
| jieba-fast | GPT-SoVITS 分词加速 | 纯性能优化，jieba（已装）功能等价 | 关闭（无功能损失） |
| eunjeon | GPT-SoVITS 韩语 mecab | 仅韩语 TTS 需要 | 关闭（中文直播场景无影响） |
| pynini | GPT-SoVITS 文本正则化 | tn/itn 链路可选 | 关闭（`--only-binary` 无二进制；tn/itn 用替代后端） |
| gruut | GPT-SoVITS 死代码引用 | 无（phonemizer.py 无调用方） | 不装 |
| google-generativeai | AUTOlive Gemini 通道 | 旧 SDK 硬性要求 protobuf 5.x，与 pb2 保护约束（protobuf==7.35.1）冲突 | **【裁决 2026-09-22 /autoplan 最终门】**采纳守卫方案①：gemini.py 顶层导入已加 try 守卫，缺包降级为警告，启动不阻断；选项③（无视约束裸装）升格为**禁止**（运行期静默破坏弹幕 pb2）。通道恢复路径=②迁移 google-genai（装包+适配，未排期） |
| xingchen | AUTOlive 讯飞星火通道 | 硬性要求 pydantic<2，与基线 pydantic==2.13.4 冲突（constraints 已拦截） | 挂起；适配期处理或留旧环境 |
| zhipuai | AUTOlive 智谱AI 通道 | 2026-09-02 批次6 安装遗漏（本次审计发现缺失并阻断启动） | **【裁决 2026-09-22 /autoplan 最终门】**zhipu.py 已加缺包守卫（同上）；用户手动补装命令已 dry-run 实证（`pip install -c constraints.txt zhipuai` → 2.1.5.20250825 + PyJWT 2.8.0，不触碰基线），装后实例化冒烟通过即为通道可用 |
| wenxinworkshop | AUTOlive 声明 | PyPI 无此包（声明笔误或私有源） | 跳过；百度文心走 baidu-aip（已装） |
| PySimpleGUI | RVC 旧声明 | 代码实际 import FreeSimpleGUI（已装） | 不装（付费授权） |
| torch-directml | RVC DML 备用后端 | NVIDIA 机器不需要 | 不装 |
| pyaudio | AUTOlive | 待批次6验证（PyPI 有 cp312 wheel 预期） | 批次6见分晓 |
| onnxruntime（纯CPU版） | 被传递依赖拉入过 | 与基线 onnxruntime-gpu 1.22.0 冲突 | 已卸载；constraints 钉 onnxruntime==1.22.0 防复发 |

## 版本元数据备注（不阻塞，冒烟验证）
- fairseq 0.12.3.1 声明 omegaconf==2.3.0 / hydra-core==1.3.2，已按其声明钉回。
- peft 解析为 0.20.0（受 transformers 5.x 兼容约束回退）；GPT-SoVITS LoRA 用法较浅，冒烟验证。
- typing-extensions 升至 4.16.0（向后兼容）。
