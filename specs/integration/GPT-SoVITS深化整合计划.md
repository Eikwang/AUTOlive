> **📦 已归档历史档案**（2026-10-21 批次标注）：本文件为整合期计划/纪要快照，记录当时决策与实施过程，不随现行代码演进；现行事实以 docs/ 与代码为准。

<!-- /autoplan restore point: "D:\\AI\\AUTOlive\\.gstack\\tmp\\autoplan\\main-autoplan-restore-20261013-144852.md" -->
## Implementation plan
# 实现计划：GPT-SoVITS 深化整合（训练管线完整化 + 推理进程内化）

 生成：2026-10-13 | 分支：main

> **状态：APPROVED（2026-10-13，/autoplan 四阶段审查通过：CEO Spec Loop 2 轮 PASS、Design 6→8、DX 7.6→8.6、Eng PASS 0 critical；终门 all 全推荐采纳：uc1a 进程内化确认+5 项 auto+plan1a 按现状批准）** | 来源：/autoplan ARGUMENTS + 三路代码勘察（AUTOlive 集成现状 / GPT-SoVITS 内部结构 / AICoverGen MDXNet）

## 目标（用户需求忠实转述）

1. **训练完整化**：把 GPT-SoVITS 六步训练流程整合进"数字人-训练声音-训练管线GPT-SoVITS"——①声音处理（MDXNet 三阶段，"已是干声（跳过 UVR5）"复选框已存在由其决定是否执行）②语音切分（默认参数）③语音识别（中文/粤语→达摩 ASR large/zh/float32；英日韩→Faster Whisper large-v3/auto/float16）④训练集格式化一键三连（直接迁移上游逻辑）⑤SoVITS 训练（batchsize:4 / total_epoch:10 / 文本模块学习率权重:0.4 / save_every_epoch:5，仅保存最新）⑥GPT 训练（batch_size:4 / total_epoch:20 / save_every_epoch:10，开启 DPO，仅保存最新）。版本=v2ProPlus，底模=s1v3.ckpt + s2Gv2ProPlus.pth + s2Dv2ProPlus.pth。
2. **推理进程内化**：替换 api_v2.py HTTP API 调用为完全内部集成（不再走 HTTP），参数设置放在"AI对话-文本转语音"页，替换原先的 GPT-SoVITS API 设置。
3. GPT-SoVITS 成为 AUTOlive 子模块，系统功能内部循环。

## 现状（代码核实，2026-10-13）

### AUTOlive 侧
- **TTS 调用链**：`utils/audio_handle/my_tts.py:101-282` 六个 HTTP/gradio 分支（api/api_0322/api_0706/v2_api_0821/gradio_0322/webtts）；运行时生效 `type:"api"`（config.json:348,350 → 127.0.0.1:9880 旧参数分支）。真分发器 `utils/audio/synthesis_manager.py:299-378` 只认 `gpt_sovits`/`none` 两类 tts_type；其余类型落 except 返回 -1（本地合成类型是死选项）。
- **托管编排**：`utils/gpt_sovits_service.py:24-104` 以 runtime312-clone 拉起 `api_v2.py -p 9880 -a 127.0.0.1 -c tts_infer_probe.yaml`；权重由 `config/tts_infer_probe.yaml` custom 节钉死（t2s=wy-e15.ckpt, vits=wy_e8_s424.pth, v2ProPlus, cuda, is_half=true）；预热 POST /tts（:62-90）；ManagedProcess（`utils/service_orchestrator.py:73`，GPT-SoVITS/RVC/DanmakuListener 三服务共用）。
- **api_v2 定制耦合**：apps/gpt-sovits/api_v2.py 与外部 D:\AI\GPT-SoVITS\api_v2.py md5 相同，含 AUTOlive 定制（`-rt realtime_url / delivery / GPT_REALTIME_URL`）——delivery=1 时把 16k PCM 推送到 EDTalk realtime_serve(9881)；`utils/audio/synthesis_manager.py:33-43` 按此分流。进程内化必须保留等价推送通道。
- **训练管线已真实接线**：`frontend/ui/tabs/train_voice.py`（授权卡/GPU 检查卡/步骤五态/日志尾/单步重跑；控件定义 :67-68，GPT-SoVITS 分支取值 :168-169 默认 epoch=12/batch_size=8）→ `utils/train_voice_pipeline.py` 7 步计划（uvr5/slice/asr/prepare/s2/s1/export）。缺口：**uvr5 步恒 SKIPPED**（:179-186"UVR5 无稳定 CLI"）；**ASR 硬编码单模型**（funasr large/zh/float16，:201-205，无 UI 控件）；s2/s1 已走 s2v2ProPlus.json / s1longer-v2.yaml 模板但参数默认值与用户要求不符（:168-169）；**三处既有缺陷**：①`:276` 硬编码 `pretrained_s1 = .../sv_pretrained_bernard_ckpt_20250213.ckpt`——该文件不存在（实测 pretrained_models/ 仅有 s1v3.ckpt），t2s_lightning_module.py:24-35 无 try 包裹，s1 训练启动即 FileNotFoundError（本计划必修）②`:192-195` slice 只传 7 个 argv（缺 _max=0.9/alpha=0.25/i_part/all_parts，与上游 webui 11 参数不等价）③`:270` s1 编排层无条件 `batch_size // 2`——上游 if_dpo 已在 data_module.py:46-50 自动减半，编排层再减半属双重减半（本计划修正）。
- **训练子进程 cwd=外部 D:\AI\GPT-SoVITS 树**（train_voice_pipeline.py:40-41,148-156），python=runtime312-clone；产物写外部树 logs/<exp>、GPT_weights_v2ProPlus/SoVITS_weights_v2ProPlus；tmp_s2.json/tmp_s1.yaml 现已落在 `gsv_root/logs/<exp>/`（:256,:277，不在上游 TEMP/）。
- **TTS 前端页**：`frontend/ui/tabs/tts.py`（648 行）——API 类型六选一下拉 + 四套参数子卡（api_0322:204-310 / api_0706:312-358 / v2_api_0821:360-498 / webtts:500-553）+ 模型路径卡（"加载模型"按钮 `_load_gpt_sovits_model` TODO 空壳 :556-564）；试听按钮走 `frontend/utils/audio.py:54-94` 空壳 stub（恒失败）。
- **声音档案切换**：tts.py:607-648 `_apply_profile` 写 config → Config.reload → **ServiceRegistry.get("gpt-sovits").manual_restart() 重启进程换模**（R2 兜底）。
- **voice_profiles**：config.json 当前无该节（首次训练 export 创建，`utils/voice_profiles.py:55-73`）；三端消费=TTS（tts.py:575-604）/变声（svc.py:24,85）/翻唱（cover_worker.py:253-268）。
- **gpu_policy**：`utils/gpu_policy.py:58` `is_background_task_allowed(vram_reserve_gb=4.0)`=直播运行中 OR EDTalk 8000 占用 OR 显存余量<阈值；⚠ `set_live_flag_source`（:22-26）零调用者——"直播中"信号从未接线恒 False。
- **环境**：主进程解释器=EDTalk runtime312（torch 2.11.0+cu128，**已实测装有 funasr 1.4.12/faster_whisper 1.2.1/fairseq 0.12.3.1/gradio 6.26.0**——进程内 import GPT_SoVITS 的依赖面成立）；托管/训练子进程=runtime312-clone（同版本 torch）；users.pth（6 条 GPT-SoVITS 路径）仍物理存在于两解释器，`utils/autolive_paths.py:28-37,66-88` 已有代码级 PYTHONPATH 注入。

### GPT-SoVITS 侧（上游结构事实）
- **webui.py 是子进程编排器**：预处理 4 脚本只认环境变量；训练脚本只认 `--config` 临时 JSON/YAML（TEMP/tmp_s2.json、TEMP/tmp_s1.yaml）。
- **ASR 双路径已存在**：`tools/asr/funasr_asr.py`（达摩，lang∈{zh,yue}，size 恒 large，precision 参数接收未接入=天然 float32）与 `tools/asr/fasterwhisper_asr.py`（lang∈{auto,zh,en,ja,ko,yue}，large-v3，precision 可选，HF 镜像 hf-mirror 硬编码）——与用户路由规则一一对应。输出 .list 格式 `wav路径|说话人|语言|文本`。
- **一键三连** = 1a 文本/BERT（1-get-text.py）→ 1b HuBERT/32k wav + **Pro 系追加 2-get-sv.py**（v2ProPlus 需要 7-sv_cn/*.pt）→ 1c 语义 token（3-get-semantic.py）。
- **s2 训练**：`GPT_SoVITS/s2_train.py --config <json>`；v2ProPlus 基底 `configs/s2v2ProPlus.json`；`if_save_latest` = 固定文件名 G_233333333333.pth 覆盖；`if_save_every_weights` 经 savee() 导出 fp16 权重到 SoVITS_weights_v2ProPlus/。Windows 单卡 torch≥2.9 已用 _SoloDDP 规避 DDP 崩溃（s2_train.py:52-65）——编排层不得强加 DDP env。
- **s1 训练**：`GPT_SoVITS/s1_train.py --config_file <yaml>`；基底 `configs/s1longer-v2.yaml`（v2ProPlus 亦用）；`if_dpo` = batch_size 减半 + DPO loss 切换；`my_model_ckpt.on_train_epoch_end` 自动清理旧 ckpt（s1_train.py:46-61）。
- **推理可完全脱离 HTTP**：`from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config` → `TTS(cfg)` → `tts.run(dict)` 生成器逐块 yield (sr, np.int16)；TTS.py 无 fastapi 依赖。**禁止 import api_v2**（import 即执行 parse_args+自动加载+写 yaml）。v2ProPlus 推理四件套+SV 模型（sv.py 硬编码路径 pretrained_eres2netv2w24s4ep4.ckpt）；is_v2pro 从权重文件头 b"06" 自动探测。
- **TTS.run 参数**（=v2_api_0821 参数树超集）：text/text_lang/ref_audio_path/aux_ref_audio_paths/prompt_text/prompt_lang/top_k/top_p/temperature/text_split_method(默认cut5)/batch_size/batch_threshold/split_bucket/speed_factor/fragment_interval/seed/parallel_infer/repetition_penalty。
- **坑**：①init_t2s/init_vits/set_device/enable_half_precision(save=True) 均回写 GPT_SoVITS/configs/tts_infer.yaml；②TextPreprocessor 写 `./音频输出/tts-out.txt`（相对 cwd，TTS.py:16 now_dir=os.getcwd()）；③TTS.run 非线程安全，需单推理临界区（api_v2.py:295-352 InferenceGate 范式）；④prompt_cache 换参考音频自动失效；⑤异常时自动置 None 并按原路径重新 init（TTS.py:1439-1484，自愈但并发危险）；⑥webui 启动清空 TEMP/——编排层临时配置不得落 TEMP/。

### AICoverGen MDXNet 侧
- **最小整合面**：`src/mdx.py` 的 `run_mdx(model_params, output_dir, model_path, filename, denoise=, suffix=, invert_suffix=, exclude_main=, keep_orig=)` + `mdxnet_models/{UVR-MDX-NET-Voc_FT.onnx 63.7MB, UVR_MDXNET_KARA_2.onnx 50.3MB, Reverb_HQ_By_FoxJoy.onnx 63.7MB, model_data.json}`；依赖仅 librosa/soundfile/numpy/torch/onnxruntime（无 gradio/pydub）。apps/aicovergen/src/ 已有逐字节相同副本；**apps/aicovergen 下无 mdxnet_models**（copy --skip-big 跳过大文件，copy_audio_projects.py:53），三个 onnx 实际在树外 D:\AI\AICoverGen\mdxnet_models\。
- **三阶段链**：人声/伴奏（Voc_FT，denoise=True）→ 主唱/和声（KARA_2，suffix='Backup', invert_suffix='Main'）→ 去混响（FoxJoy，invert_suffix='DeReverb', exclude_main=True）——**去混响人声来自反相轨**。
- **输入约束**：必须 44100Hz 立体声（内部 librosa.load mono=False, sr=44100）——整合侧需补 convert_to_stereo 等价逻辑（mono→ffmpeg -ac 2）。
- **gpu 侧**：run_mdx 自选 torch cuda + ORT CUDAExecutionProvider；VRAM<8GB 时 m_threads=1。

## 架构方案

### A. 推理进程内化（新模块 utils/gpt_sovits_inproc.py）

- **形态**：主进程内 `import GPT_SoVITS.TTS_infer_pack.TTS`（PYTHONPATH 经 autolive_paths 注入 + users.pth 双保险），构建 `TTS_Config（来源=进程内 dict/独立 yaml，禁止指向上游 configs/tts_infer.yaml 以避免回写污染）→ TTS(cfg)`；合成调用在**专用推理线程**执行，**全局互斥锁单推理临界区**（复刻 api_v2 InferenceGate 语义：请求排队或明确拒绝，不挂死）。
- **输出对齐（双出口共用一个消费循环）**：`tts.run` 生成器逐块 yield (sr, np.int16)——**引擎暴露逐块回调接口 `synthesize(text, params, on_chunk)`**：消费循环内每个 chunk 同时 ①累积入 wav 缓冲 ②按 delivery 语义推 EDTalk realtime_serve(9881)（16k PCM）；循环结束后写本地 wav 返回路径。非 EDTalk 场景推送为 no-op。下游文件消费（audio_player/字幕/文案重试链）零改动。
- **EDTalk 实时推送通道保留**：synthesis_manager.py:33-43 分流逻辑不变；推送实现在消费循环内（见上）。
- **模型切换（声音档案）**：`_apply_profile` 改调进程内 `init_t2s_weights/init_vits_weights` 重载（is_v2pro 自动探测）；事务语义=新模型加载失败保持旧模型+notify（沿用 CEO-S2 裁决）；prompt_cache 随换参考音频自动失效；**移除 manual_restart() 依赖**。
- **托管退役**：gpt_sovits.hosted 子进程托管退役（ManagedProcess 保留给 RVC/DanmakuListener）；状态条胶囊对 GPT-SoVITS 语义改为"模型加载状态探针"（loading/ready/error，异步加载不阻塞主进程启动）；预热=就绪后进程内合成一条短句（S7 语义平移）。
- **兜底通道语义（backend="api"）**：连接**用户自备**的外部 api_v2（api_ip_port 指向），AUTOlive 不拉起、不托管；UI 文案明示"需自行启动外部 GPT-SoVITS 服务"；该外部进程经既有 F11 语义挂入状态条观测。兼容既有 CEO-F11"hosted=false 挂外部进程"义务。
- **显存治理**：模型常驻主进程显存（与原 api_v2 子进程等量，显存面无净变化）；**gpu_policy 升级**：训练/翻唱准入的显存余量检查改为"扣除进程内 TTS 常驻占用后"的口径，防止训练 OOM 拖死进程内 TTS；`set_live_flag_source` 接线（main.py 直播状态→gpu_policy）。
- **兜底开关**：`gpt_sovits.backend: "inproc"|"api"`（默认 inproc）；api 兜底复用既有 v2_api_0821 通道（义务 B-5 语义平移）。

### B. 训练管线完整化（改造 utils/train_voice_pipeline.py）

- **步骤1 声音处理（MDXNet）**：填补 uvr5 占位步——`already_dry=True`（"已是干声"复选框，train_voice.py:69 已存在）跳过；否则进程内 import `apps/aicovergen/src/mdx.py` 三阶段链（denoise=True），输入先做 44100 立体声归一化（mono→ffmpeg -ac 2），最终干声 `*_Vocals_Main_DeReverb.wav` 作为切分输入。**模型资产落点=models/mdxnet/**（沿 models/rvc/ 范式：AUTOlive 管理的模型目录，大文件 gitignore+安装包 assets-manifest 补录 SHA-256，约 178MB），运行时从该目录加载，T-1 验收含资产到位条件。
- **步骤2 切分**：既有 slice 步修正后保持——现状 :192-195 仅传 7 argv，**补齐 `_max=0.9 / alpha=0.25 / i_part=0 / all_parts=1`** 对齐上游 webui.py:725-738 的 11 参数语义（分片逻辑单机=1 片，参数位保留）。
- **步骤3 ASR 双模型路由**：管线新增 `asr_lang` 参数（前端下拉：zh/yue/en/ja/ko/auto）；路由规则：zh|yue→`tools/asr/funasr_asr.py -f large -l <lang> -p float32`；en|ja|ko|auto→`tools/asr/fasterwhisper_asr.py -f large-v3 -l <lang> -p float16`（达摩 precision 未接入为上游事实，float32 为其天然行为，参数位保留以对齐 webui 语义）。
- **步骤4 一键三连**：既有 prepare 步已按 1-get-text→2-get-hubert-wav32k→2-get-sv→3-get-semantic 顺序接线（v2ProPlus 的 SV 步已含）——核对 env 参数完整性与 webui open1abc 等价，补齐缺项。
- **步骤5 SoVITS 训练**：s2 模板覆写默认值改为 batch_size:4 / epochs:10 / text_low_lr_rate:0.4 / save_every_epoch:5 / if_save_latest:1；"仅保存最新"=if_save_latest（固定名覆盖）+ 编排层 export 后清理历史 fp16 权重（仅留最新一份）。
- **步骤6 GPT 训练**：s1 模板覆写默认值改为 batch_size:4 / epochs:20 / save_every_n_epoch:10 / **if_dpo:1** / if_save_latest:1 / **pretrained_s1=<gsv_root>/GPT_SoVITS/pretrained_models/s1v3.ckpt（修复 :276 bernard 死路径）**；batch_size 换算链：UI 值 4 直写 config（**移除编排层 :270 的无条件 //2**），if_dpo=1 时上游 data_module.py:46-50 自动减半生效（实际训练 batch=2，日志明示）；同样编排层 fp16 清理。
- **export 步保留**：现状第 7 步 export（写 voice_profiles）不在本计划退役范围——DEFERRED 的"训练后自动接线"指"训练完成自动切换生产档案"，与 export 写档案本身无关，实施者不得误删。
- **底模预检（T-4）**：校验对象=**实际将写入训练配置的路径**（非固定文件名存在性）：s1v3.ckpt（pretrained_s1 覆写后的值）/ v2Pro/s2Gv2ProPlus.pth / v2Pro/s2Dv2ProPlus.pth（s2 模板 pretrained_s2G/s2D）/ sv/pretrained_eres2netv2w24s4ep4.ckpt（2-get-sv 与推理共用）/ bert+hubert+G2PW / **ASR 两组模型**（funasr：tools/asr/models/speech_paraformer-large* + fsmn-vad + ct-transformer-punc；faster-whisper：large-v3，本地缺失时走 hf-mirror 在线下载并计入预取耗时）。缺失走 DX-5 缺模型预取机制（预计耗时+一键预取+失败重试）。

### C. 前端改造

- **训练页（train_voice.py）**：参数默认值对齐（epoch 12→GPT 20/SoVITS 10 分列，batchsize 8→4）；新增 text_low_lr_rate(0.4)/save_every(5|10)/DPO 开关（默认开）控件；新增 ASR 语言下拉（默认 zh）；训练按钮二次确认含"训练期间 TTS 进程内推理共享显存"提示。
- **TTS 页（tts.py）**：GPT-SoVITS 卡重做——合成引擎=进程内（默认）+外部 API 兜底（折叠）；进程内参数面板=v2 参数树（ref_audio_path/prompt_text/prompt_lang/text_lang/top_k/top_p/temperature/text_split_method/batch_size/speed_factor/fragment_interval/repetition_penalty/seed 等，来源=v2_api_0821 节语义平移）；"加载模型"按钮接通（调进程内重载）；试听按钮接通（进程内合成固定文案）；四套旧 API 子卡退役（api/api_0322/api_0706/gradio_0322 移除，webtts 为独立外部引擎去留待裁决）。
- **状态条**：GPT-SoVITS 胶囊从进程健康改为模型状态（loading/ready/error + 推理中指示）。

### D. 配置与环境

- **config gpt_sovits 节迁移（键映射表，C-1 验收依据）**：

| 现键（config.json:347-438） | 处置 | 去向 |
|---|---|---|
| `type`（"api"） | 退役 | gpt_sovits 引擎选择统一由 synthesis_manager type="gpt_sovits" 承接，内部按 backend 分流 |
| `backend` | **新增** | "inproc"（默认）/"api"（用户自备外部进程） |
| `api_ip_port` | 保留降语义 | 仅 backend="api" 时消费（兜底通道端点） |
| `gradio_ip_port` | 删除 | gradio_0322 分支随四分支退役 |
| `api_0322` / `api_0706` 子节 | 删除 | 对应死分支退役 |
| `v2_api_0821` 子节 | 保留 | backend="api" 兜底参数源（不迁移） |
| `webtts` 子节 | 保留 | 独立外部引擎，不在 GPT-SoVITS 整合范围 |
| `ref_audio_path`/`prompt_text`/`prompt_language`/`language`/`cut` | 迁移 | 与 v2_api_0821 参数合并为 `gpt_sovits.params`（进程内 TTS.run 参数事实来源，键名对齐 TTS.run 清单） |
| `gpt_model_path`/`sovits_model_path`（F:\ 历史路径） | 迁移 | 初始 voice_profiles 档案"legacy"（F:\ 文件存在时）或标记废弃并日志提示重训练 |
| `hosted` | 退役 | backend 键替代（迁移脚本读取旧值：hosted=false→backend="api" 提示用户自备） |
| `tts_infer_yaml` | 迁移 | 指向 AUTOlive 独立 yaml（进程内 TTS_Config 来源，不再指上游 configs/tts_infer.yaml） |

- **运行副本归属**：apps/gpt-sovits 扩充为唯一运行副本（任务 S-1 承接），训练产物目录配置化兼容既有外部树产物。
- **环境**：进程内 import 跑在主进程解释器（EDTalk runtime312，依赖已实测齐备）；训练子进程继续 runtime312-clone；users.pth 收编义务（P1-2 既有）不变。

## 关键架构裁决点（CEO 阶段已全部裁决生效，标注对应 Decision Audit Trail 行）

1. **进程内化的崩溃隔离权衡**：CUDA 级错误（OOM/驱动崩溃）从"可重拉子进程"变为"可能拖死主进程"。缓解=TTS 自愈（TTS.py:1439-1484 异常自动重 init）+ 推理线程隔离 + 兜底 api 通道 + 显存预算治理。**用户已明确选择进程内**，计划如实接受并降险。【已裁决：审计 #2 接受用户方向】
2. **旧 API 分支退役范围**：api/api_0322/api_0706/gradio_0322 四分支删除，v2_api_0821 降级为兜底单通道；webtts 保留。【已裁决：审计 #3】
3. **运行副本归属**：apps/gpt-sovits 扩充为唯一运行副本（新增任务 S-1 承接），模型资产经配置指向既有 pretrained 位置。【已裁决：审计 #4】
4. **MDXNet 复用方式**：import apps/aicovergen/src/mdx.py；模型资产落 models/mdxnet/。【已裁决：审计 #5 + spec F3 修订】
5. **"仅保存最新"精确语义**：if_save_latest+编排层 fp16 权重清理（仅留最新一份）。【已裁决：审计 #6】
6. **ASR precision 语义**：达摩 float32 参数位保留（上游未接入=天然 float32，参数位对齐 webui 语义）。【已裁决：审计 #7】

## 任务分解

| # | 任务 | 目标文件 | 验收 |
|---|------|---------|------|
| S-1 | 运行副本迁 apps/gpt-sovits（承接 CEO 决策 4；**R/T 全系列任务的硬前置**——S-1 未完成前 R-1 import 与训练 cwd 均不可切换，S9 部署顺序）：副本扩充（补 tools/ + 全量 GPT_SoVITS/，以实施时实测文件清单为准——2026-10-13 快照为 316 文件）；pretrained_models/bert/hubert/G2PW 等模型资产经配置键指向既有位置（不复制数 GB 资产）；训练子进程 cwd/PYTHONPATH 与推理 import 全部切换至 apps/ 副本；copy_audio_projects.py 复制清单更新；训练产物目录（logs/、GPT_weights_v2ProPlus/、SoVITS_weights_v2ProPlus/）配置化（默认跟随副本根，兼容既有外部树产物迁移） | Scripts/copy_audio_projects.py + utils/autolive_paths.py + train_voice_pipeline.py | apps/ 副本 import GPT_SoVITS.TTS_infer_pack.TTS 成功；训练冒烟全程零外部树依赖；既有产物路径可配置兼容 |
| T-1 | MDXNet 声音处理步：填补 uvr5 占位，三阶段链+44100 立体声归一化+干声跳过；模型资产落 models/mdxnet/（三个 onnx+model_data.json，manifest 补 SHA-256） | utils/train_voice_pipeline.py + models/mdxnet/ | 带背景噪音样本跑通三阶段产出 DeReverb 干声；干声样本正确跳过；资产到位校验（缺失明示预取） |
| T-2 | ASR 双模型路由：asr_lang 参数+路由规则+前端下拉；fasterwhisper HF 镜像 env；验收前置=ASR 模型资产已预取（T-4） | train_voice_pipeline.py + train_voice.py | zh/yue/en/ja/ko 各出正确 .list；auto 落 fasterwhisper |
| T-3 | s2/s1 默认参数+DPO+仅保存最新；**修复 s1 底模接线（pretrained_s1→s1v3.ckpt 覆写，根治 :276 bernard 死路径）**；**移除 :270 无条件 //2**（DPO 由上游自动减半，UI 值直写）；slice 参数补齐 11 argv；编排层 fp16 清理；**确认 _run 列表参数无 shell=True（S3 注入面验证）** | train_voice_pipeline.py | 默认参数与用户规格一致；s1 训练用 s1v3.ckpt 底模启动成功；DPO 生效且实际 batch=UI 值/2 日志明示；训练后仅存最新权重对；_run 无 shell 确认留痕 |
| T-4 | 预检机制：校验"实际写入训练配置的路径"（s1v3/s2Gv2ProPlus/s2Dv2ProPlus/SV/bert/hubert/G2PW）+ASR 两组模型资产；缺失明示+预取 | train_voice_pipeline.py | 缺失时明示缺失项+预取入口；预检通过后六步可启动 |
| R-1 | 进程内推理引擎 utils/gpt_sovits_inproc.py：TTS_Config dict 构造+专用线程+互斥临界区+**逐块消费循环（synthesize(text, params, on_chunk)，写 wav 与推送共用该循环）**；合成耗时与首块延迟结构化日志（S8 增补） | 新模块 | 无 HTTP 依赖完成合成；并发请求排队不挂死；on_chunk 回调逐块收到 (sr, int16)；日志含耗时字段 |
| R-2 | my_tts/synthesis_manager 接进程内分支；backend 开关（inproc 默认 / api=用户自备外部进程+F11 挂状态条+UI 文案明示）；api 兜底走 v2_api_0821 | my_tts.py + synthesis_manager.py + config | inproc 默认生效；api 兜底可切回且文档明示自备前提 |
| R-3 | EDTalk 推送通道平移：消费循环内按 delivery 语义推 9881 | gpt_sovits_inproc.py + synthesis_manager.py | EDTalk 实时链路回归通过（流式逐块，非文件级） |
| R-4 | 换模事务：进程内 init_* 重载+失败保持旧模型；_apply_profile 去 manual_restart | gpt_sovits_inproc.py + tts.py | 切换失败不中断服务；成功后 prompt_cache 失效生效 |
| R-5 | 胶囊语义+异步加载+预热平移；托管退役（ManagedProcess 保留他服务） | service_registry + frontend/ui/layout.py + gpt_sovits_service.py | 启动不阻塞；胶囊反映模型状态 |
| R-6 | gpu_policy 升级：进程内 TTS 常驻显存计入预算+set_live_flag_source 接线 | gpu_policy.py + main.py | 训练准入按扣除后余量判断 |
| F-1 | 训练页参数面（默认值/新控件/ASR 下拉/显存提示；**参数分组折叠：基础=数据集/实验名/轮数/批大小，高级=lr权重/save_every/DPO/ASR 语言**（S11 增补）） | train_voice.py | 控件与用户规格一一对应；分组折叠生效 |
| F-2 | TTS 页重做（进程内参数面板/加载模型/试听接通/旧子卡退役） | tts.py | 试听可用；参数面完整 |
| C-1 | config 迁移（按上表键映射逐键处置+向后兼容：旧 config 无新键→默认值注入；hosted=false→backend="api" 自动映射）；**进程内 TTS_Config 的独立 yaml 不沿用 config/tts_infer_probe.yaml 模板节的模型路径**（该模板节 t2s 指向 bernard 死路径、vits 指向不存在的 s2Gv2ProPlus_pretrained_20250518.pth——现网 custom 节钉死 wy 权重故不可达，迁移时死链不得随行） | config 模块 | 键映射表 12 行逐键验收；旧 config 加载无破坏；F:\ 历史路径处置有日志 |
| TST-1 | 单测：inproc 临界区/换模事务/推送/MDX 步跳过逻辑/ASR 路由/参数固化/s1 底模接线（bernard 回归）/slice 11 argv/双减半回归 | tests/ | pytest 通过 |
| TST-2 | 端到端冒烟：干声样本六步全链路出模型对（S-1 副本上）+TTS 进程内合成+EDTalk 推送 | tests/ 或脚本 | 全链路产物存在且合成可听 |

## 风险

- R1: 进程内 CUDA 崩溃拖死主进程（用户知情选择的权衡）→ 自愈+线程隔离+兜底+显存预算。
- R2: 双解释器依赖漂移（主进程 EDTalk runtime312 vs 训练 runtime312-clone）→ 依赖清单校验入 TST-1。
- R3: 训练与进程内推理同卡显存争用 → R-6 准入+文案提示。
- R4: TTS 全局副作用（写上游 tts_infer.yaml/相对 cwd 文件）→ 进程内 Config 禁用 save 路径+cwd 固定 AUTOlive 根。
- R5: users.pth 隐性依赖 → autolive_paths 注入为代码契约，users.pth 仅兜底。
- R6: fairseq 等依赖在主进程 import 的加载开销/兼容 → 启动异步化+依赖实测（已在 EDTalk runtime312 装有）。

## 不在范围内

- RVC 变声/翻唱链改造（已按 2026-09-25 计划完成，仅 voice_profiles 消费不受影响）
- GPT-SoVITS 上游功能改造（仅编排/封装，不维护 fork；api_v2 的 EDTalk delivery 定制随托管退役由 R-3 平移）
- 多角色训练队列/断点续训（TODOS 既有延期项）
- 安装包分发更新（沿用既有 P1-5 机制，mdxnet_models 资产清单补录）
## Review record

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|----------------|-----------|-----------|----------|
| 1 | ceo | CEO 模式=SELECTIVE EXPANSION | Mechanical | 0E 计数 | ~15 文件新增能力（非 greenfield 非 fix），类别清晰 | SCOPE EXPANSION（无用户扩张信号）/REDUCTION（核心诉求不可减） |
| 2 | ceo | 推理进程内化按用户方向实施+降险四件套（自愈/线程隔离/兜底/显存预算） | User Challenge→接受 | P1 | 用户明确指令推翻既有 P1-1 api_v2 托管裁决；模型义务=降险非阻挡 | 保持托管（违背用户明确要求） |
| 3 | ceo | 旧 API 四分支（api/api_0322/api_0706/gradio_0322）退役；v2_api_0821 降级为兜底通道；webtts 保留 | Mechanical | P1/P4 | 四分支在 synthesis_manager 已是死路径（只认 gpt_sovits/none）；v2 兜底承接 B-5 义务 | 全保留（死代码膨胀）|
| 4 | ceo | 运行副本=apps/gpt-sovits 扩充（补 tools/+全量 GPT_SoVITS/），模型资产经配置指向既有 pretrained 位置，训练产物目录配置化 | Taste | P5 | 与 EDTalk/apps 唯一副本裁决（F6）一致；外部树长期使用中硬切有迁移风险 | 维持外部根（违背"成为子模块"诉求） |
| 5 | ceo | MDXNet 复用 apps/aicovergen/src/mdx.py | Mechanical | P4 DRY | 树内已有逐字节副本+翻唱链验证过；audio-separator 是新增依赖 | audio-separator[gpu]（新依赖无增益） |
| 6 | ceo | "仅保存最新"=if_save_latest+编排层 fp16 权重清理（仅留最新一份） | Mechanical | P5 | 精确满足用户语义；export 步骤已有"取最新权重对"逻辑 | 仅 if_save_latest（fp16 按 epoch 堆积不符语义） |
| 7 | ceo | ASR precision 参数位保留（达摩上游未接入=天然 float32） | Mechanical | P5 | 对齐 webui 语义；上游接入时零改动 | 移除参数（破坏 webui 对齐） |
| 8 | ceo | 训练进度百分比等 delight 候选 5 项 | Mechanical(defer) | — | 既有五态+日志尾已覆盖反馈闭环 | 纳入本期（范围膨胀） |

## Reality Rows

- REALITY: premise-table pass — 5 前提 4 条有实测收据（收据=本计划「现状（代码核实）」节 train_voice_pipeline.py:179-186/:201-205、config.json:348-350、tts_infer_probe.yaml custom 节），1 条修正（去 API 化动机=部署简化/生命周期统一而非回环性能），unverified=0
- REALITY: already-done pass — origin/main grep "gpt_sovits_inproc" 零命中（2026-10-13 本地快照，fetch SSL 失败受限）；训练/推理文件最近提交=cfdb4da4（WIP: P3 全任务，2026-09-25 批），无重复建设
- REALITY: surface-check pass — funasr_asr.py/fasterwhisper_asr.py/slice_audio.py 命令行经上游逐行核实（D:\AI\GPT-SoVITS\webui.py:383-426 与 :694-769、tools/asr/config.py:28-36、funasr_asr.py:25-68）
- REALITY: binding-decisions pass — ~/.gstack/projects/Eikwang-AUTOlive/decisions.jsonl:1（含 2 条"进程内"记录：2026-10-06 "DanmakuListener 系统模块整合：进程内整合方案 A 确认实施"、"audio_player/captions_printer 整合为进程内内置模块"）支持本方向；无推翻默认
- REALITY: numbers pass — MDX 模型 63.7/50.3/63.7MB [measured: D:\AI\AICoverGen\mdxnet_models\:1-4 目录实测]、s2Gv2ProPlus.pth 200,125,741B [measured: D:\AI\GPT-SoVITS\GPT_SoVITS\pretrained_models\v2Pro\:1 实测]、torch 2.11.0+cu128 [measured: D:\AI\EDTalk\runtime312\Lib\site-packages\torch\version.py:1 实测]
- REALITY: deferred-asks pass — TODOS.md:14-15（多角色管理/训练断点续训）与 :12（训练产物→推理自动接线）延期项保持不动；R-4 换模事务为自动接线铺路但不实施
- REALITY: pr-count pass — D:\AI\AUTOlive\.gstack\ship-policy.json:1（该文件不存在，2026-10-13 ls 实测为默认值来源），默认一波一 PR

## 0I 时间审查（实施时间线预演）

- **HOUR 1（地基）**：①进程内 import 的 cwd 契约——TTS.py:16 `now_dir=os.getcwd()`，TextPreprocessor 写 `./音频输出/tts-out.txt`：主进程启动时确保该目录存在或重定向 ②PYTHONPATH 注入时机：`import GPT_SoVITS.*` 之前必须先跑 autolive_paths 注入（sys.path 顺序敏感）③TTS_Config 用 dict 构造或 AUTOlive 独立 yaml，**禁止指向上游 GPT_SoVITS/configs/tts_infer.yaml**（init_t2s/init_vits/set_device 均回写该文件，污染上游配置）。
- **HOUR 2-3（核心逻辑歧义）**：①推理线程模型：TTS.run 同步生成器跑专用线程，互斥锁包住"整个生成器消费过程"（非单 chunk）②换模时进行中的合成请求：排队等待旧合成完成后换模（api_v2 InferenceGate 语义平移），拒绝策略不做 ③上游根 weight.json"当前选中模型"记录：进程内切换**不写**它，voice_profiles 是唯一事实来源。
- **HOUR 4-5（集成意外）**：①fairseq/G2PW 首次 import 开销显著（数秒级）→ 启动异步加载+胶囊 loading 态 ②推理线程未捕获异常的看门狗：异常冒泡置胶囊 error+自愈重 init ③运行副本若迁 apps/gpt-sovits：训练子进程 cwd/PYTHONPATH 同步对齐 apps/ 路径 ④训练子进程 env 需注入 HF_ENDPOINT=hf-mirror.com（fasterwhisper 首用下载）与 HF_HOME（对齐上游 bat 约定）。
- **HOUR 6+（打磨/测试）**：①单测 mock TTS 类接口（不真加载模型，CI 无 GPU）②临界区并发测试：两线程同时合成断言排队不交叉 ③config 迁移测试：旧 config 无新键→默认值注入且旧键不丢 ④E2E 冒烟依赖 GPU+底模文件，标记环境依赖测试与跳过条件。

## 0C Dream State Mapping

```
CURRENT STATE                       THIS PLAN                          12-MONTH IDEAL
TTS=HTTP 子进程托管(9880 回环)  -->  TTS=进程内器官(无服务进程,        -->  声音身份生产线全闭环：
训练=7步框架,MDX缺口,ASR硬编码       模型状态胶囊)                          录音→训练→TTS/变声/翻唱
参数面≠用户规格                      训练=六步完整(MDXNet+双ASR+DPO)        三端一键消费；训练即用
换模=重启托管进程(秒级中断)          换模=进程内热重载(失败保旧)            零跳线；多档案库+商用分发
```

本计划在"训练管线可信可用"与"推理生命周期统一"两个维度上直接向理想态推进；多档案库管理与商用分发明确不在本计划（TODOS 既定）。

## Review Sections（CEO 11 节深审）

### Section 1: Architecture Review

**Current scope**：SELECTIVE EXPANSION（审计 1-8 行）；已接受=进程内化+训练完整化+副本迁移+前端两页；DEFER=delight 五项+TODOS 三项；REJECT=无。

进程内化前后架构对比：

```
BEFORE（现状）                          AFTER（本计划）
┌─────────────────────┐               ┌──────────────────────────────────┐
│ AUTOlive 主进程      │               │ AUTOlive 主进程                   │
│  my_tts ──HTTP──→   │               │  my_tts → synthesis_manager       │
│  (127.0.0.1:9880)   │               │    → gpt_sovits_inproc（新）      │
│         ↑           │               │      → TTS 类（进程内 import）    │
│  api_v2 子进程       │               │      专用线程+互斥临界区           │
│  (runtime312-clone) │               │  TTS_Config ← AUTOlive 独立 yaml  │
│  ManagedProcess 托管 │               │  capsule: loading/ready/error     │
└─────────────────────┘               └──────────────────────────────────┘
                                              │ 训练（子进程保留）
                                      ┌───────────────┐
                                      │ s2/s1/prepare │ cwd=apps/gpt-sovits
                                      └───────────────┘
```

合成请求数据流四路径：
- **Happy**：text→参数装配（voice_profiles/params）→临界区获取→TTS.run 生成器→on_chunk（wav 缓冲+EDTalk 推送）→写 wav 返回路径
- **Nil**：text=None→TTS.run 内部 yield 16k 空静音（TTS.py:1189）→正常返回（上游语义保持）
- **Empty**：text=""→同 nil 路径（上游 yield 空静音）
- **Error**：模型推理异常→TTS 自愈（置 None+重 init，TTS.py:1439-1484）→本次请求返回明确失败→胶囊 error→后续请求触发重载

InProcEngine 状态机：

```
uninitialized ──start(异步加载)──→ loading ──成功──→ ready ⇄ inferencing（互斥）
     │                               │                  │
     │                               └─失败─→ error ←────┘（推理异常且自愈失败）
     │                                        │
     └────────────────手动重启/重载────────────┘
换模事务：ready → switching →（成功）ready [新模型] /（失败）ready [旧模型保持+notify]
非法转换防护：loading 态拒绝合成请求（排队或明确失败）；switching 期间合成请求排队等待
```

- **耦合**：GPT_SoVITS 成为运行时依赖（PYTHONPATH+users.pth 双通道）——本计划本质耦合，用户已裁决；ManagedProcess 仍服务 RVC/DanmakuListener（无扩散）。
- **SPOF**：推理线程→自愈；CUDA 级崩溃→主进程（R1 已声明+兜底 api）；EDTalk 推送失败→静默降级不伤主链路。
- **回滚**：backend="api" config 键一键切回外部进程模式；git revert 整批可回（config 向后兼容）。
- **发现 1（低）**：训练子进程 `_run()` 需确认列表参数无 shell=True（命令注入面验证义务）→ 并入 T-3 验收。
- **发现 2（低）**：多 GPU 编排明确不在范围（单机单卡现实），s2_train 的 gpu_numbers 配置固定 "0"。

### Section 2: Error & Rescue Map

| METHOD/CODEPATH | WHAT CAN GO WRONG | EXCEPTION CLASS | RESCUED? | RESCUE ACTION | USER SEES |
|---|---|---|---|---|---|
| InProcEngine#synthesize | 模型未就绪 | EngineNotReadyError | Y | 排队等待（loading）或明确失败（error） | 状态提示+重试入口 |
| InProcEngine#synthesize | 推理异常 | ModelInferError | Y | TTS 自愈重 init+本次明确失败 | 合成失败提示+胶囊 error |
| InProcEngine#synthesize | CUDA OOM/驱动崩溃 | CudaFatalError | Y(降险) | 兜底提示+backend=api 指引；进程级崩溃=R1 已声明风险 | 兜底文案+日志 |
| InProcEngine#switch_profile | 新模型加载失败 | ModelLoadError | Y | 事务回滚保旧模型+notify（CEO-S2 平移） | 切换失败提示，声音不中断 |
| InProcEngine#_push_edtalk | 9881 连接失败 | PushError | Y | 静默降级（文件播放不受影响）+warning 日志 | 无感（直播主链路正常） |
| MDX 三阶段 | onnx 会话/推理失败 | MdxStepError | Y | 步骤五态失败+原因摘要+重跑（DX2） | 步骤红色+日志定位 |
| MDX 三阶段 | mdxnet_models 资产缺失 | AssetMissingError | Y | T-4 类预检拦截+预取指引 | 缺失项+预取入口 |
| ASR 路由 | funasr 本地模型缺失→在线下载失败 | AsrDownloadError | Y | 明确文案+镜像/重试指引（DX5） | 预取提示 |
| ASR 路由 | fasterwhisper HF 下载失败 | HfDownloadError | Y | hf-mirror env 已注入+重试 | 同上 |
| s2/s1 训练 | 显存不足（与进程内 TTS 同卡） | GpuDeniedError | Y | gpu_policy 准入拒绝（R-6 扣除 TTS 常驻后口径）+再试时机 | 拒绝文案含占用来源 |
| s2/s1 训练 | 底模路径失效（bernard 类） | PretrainedMissingError | Y | T-4 预检拦截（校验实际写入配置的路径） | 训练启动前拦截+明示缺失 |
| config 迁移 | 键缺失/损坏 | ConfigMigrateError | Y | 默认值注入+原子写（E-4 既有） | 无感/日志 |
| 训练编排#step | 任一步失败 | StepFailure | Y | 步骤标红+链路停止+重跑（EDTalk 范式既有） | 步骤红色状态 |

GAP 计数：0（全部 rescued；CUDA 级崩溃为已声明接受的降险残余风险，有兜底与用户指引）。

### Section 3: Security & Threat Model

| 威胁面 | 评估 | 缓解 |
|---|---|---|
| 攻击面变化 | **净缩小**：9880 回环端口消失（原托管 api_v2 的网络面移除）；进程内调用无网络暴露 | — |
| 新输入面 | dataset_dir/exp_name（训练参数）、asr_lang（前端下拉） | 列表参数无 shell（T-3 验收确认 _run 无 shell=True）；asr_lang 白名单枚举（前端 select 限定） |
| 命令注入 | 训练子进程命令行拼接 | subprocess 列表参数（验证义务入 T-3）；路径来自用户本机配置，单机单用户低危 |
| 授权 | 无新数据访问；voice_profiles 消费面不变 | 既有 |
| 密钥 | 无新密钥 | — |
| 依赖 | **零新增 python 包**（mdx.py 用既有依赖；audio-separator 不引入——审计 #5） | — |
| 注入向量 | LLM 无涉；SQL 无涉 | — |
| 审计 | 训练操作授权勾选留档（voice_consent_log 既有）沿用 | — |

发现：0 高危/中危；1 低危验证义务（shell=True 确认，已并入 T-3）。

### Section 4: Data Flow & Interaction Edge Cases

训练六步数据流（含影子路径）：

```
数据集目录 ──存在校验──→ [①MDX:干声?]──已是干声──→ ┐
   │nil: 目录不存在→启动确认拦截      │否            │
   │empty: 空目录→切分步失败五态      ↓44100立体声归一│
   │error: ffmpeg 失败→步骤失败      三阶段分离      │
   └──────────────────────────────────→ DeReverb 干声─┤
                                                      ↓
                              [②切分] 11 argv ──→ sliced/*.wav
                                                      ↓
                              [③ASR: asr_lang 路由] ──→ asr_opt.list
                                nil: 空音频→逐文件跳过日志
                                error: 下载失败→预取指引
                                                      ↓
                              [④prepare 一键三连] ──→ logs/<exp>/{2,4,5,6,7}-*
                                stale: 已合格产物跳过（上游语义）
                                                      ↓
                    [⑤s2 训练] ──→ SoVITS_weights_v2ProPlus/*.pth（fp16 清理）
                    [⑥s1 训练+DPO] ──→ GPT_weights_v2ProPlus/*.ckpt
                                conflict: GPU 未准入→拒绝错峰
                                                      ↓
                              [export] voice_profiles upsert（原子）
                                dup: ID 冲突加序号（DX7 既有）
```

异步排序（换模 vs 合成共享 TTS 实例）：
- 边界不变式：同一时刻至多一个合成在推理（互斥锁），换模等待当前合成完成。
- 两种顺序：①合成完成→换模→新合成用新模型 ✓ ②换模请求先获锁→合成排队→换模完成→合成用新模型 ✓。竞争窗口由"锁包住整个生成器消费过程"消除。
- 回归证明：并发测试两线程（合成×合成、合成×换模）断言不交叉不挂死（TST-1 已含）。

交互边界：

| 交互 | 边界情况 | 处置 |
|---|---|---|
| 训练启动按钮 | 双击/重入 | 状态机防重入（现状 running 语义沿用）+二次确认卡 |
| 训练中导航离开 | 子进程继续 | CEO-D5 既有义务：随页关闭行为明写（沿用） |
| 换模中再选档案 | 重复切换 | loading 置禁（CEO-D7 既有义务沿用） |
| backend 切换 | inproc↔api | config 保存+提示 api 需自备服务（F4 语义） |
| 训练中切 backend | 引擎切换与训练互不相干（训练是子进程） | 无冲突，正常执行 |

发现：0 未处理缺口（全部沿用既有义务或已有设计）。

### Section 5: Code Quality Review

- 模块结构：gpt_sovits_inproc.py 单一职责封装（引擎+线程+临界区+换模事务），符合 utils/ 既有模块形态（对照 rvc_realtime_service.py 范式）。
- 复用：InferenceGate 语义轻量复刻（不 import api_v2 的裁决理由=import 副作用，已记录审计 #2 关联）；voice_profiles 读写、gpu_policy、原子写（E-4）全复用。
- 命名：synthesize/on_chunk/switch_profile 与上游 TTS.run 术语对齐。
- 边界：nil/empty 上游已处理（yield 空静音）；任务表已含 bernard/双减半/slice 三处既有缺陷修复。
- 复杂度：新方法无 >5 分支预测；训练路由函数（asr_lang→命令）单分支映射。
- 发现：0 新问题。

### Section 6: Test Review

测试图（新引入面→覆盖）：

```
inproc 引擎        → 单测（mock TTS 接口）：临界区并发/换模事务/on_chunk 逐块
  失败路径         → 单测：加载失败保旧模型；推理异常自愈触发
EDTalk 推送        → 集成：9881 不可达时降级+主链路正常
MDX 步             → 单测：干声跳过逻辑；资产缺失拦截；（三阶段真实分离=E2E）
ASR 路由           → 单测：5 语言路由映射；auto→fasterwhisper；float32/f16 参数位
s1 底模接线        → 单测：bernard 回归（pretrained_s1=s1v3.ckpt 覆写生效）
双减半回归         → 单测：UI 值 4 直写 config；DPO 上游自动减半
slice 11 argv      → 单测：参数表固化断言
config 迁移        → 集成：16 键映射逐键；旧 config 兼容；hosted→backend 映射
六步 E2E           → 冒烟（GPU 环境依赖标注）：干声样本→模型对→进程内合成→EDTalk 推送
```

- no_claim 标注：单测 mock TTS 接口（no_claim=真实 GPU 推理行为，E2E 补真实链路）；E2E 标注环境依赖（无 GPU 底模时 skip 条件）。
- 2am 测试：进程内合成冒烟+换模失败保旧模型（TST-1/TST-2 已含）。
- hostile QA：并发合成×换模；训练中切 backend；config 半损坏迁移。
- chaos：模型文件损坏→加载失败→error 胶囊+重载（自愈路径单测）。
- 金字塔：单测多/集成中/E2E 少 ✓；flakiness：E2E 依赖 GPU+模型文件（标注 skip 条件）；单测无时序依赖。
- 发现：0 新缺口（TST-1 已含全部回归项）。

### Section 7: Performance Review

- 显存：TTS 模型常驻主进程（v2ProPlus 推理量级，与原 api_v2 子进程等量——无净变化）；训练峰值 batch_size=4 由 GPU 准入把关（R-6 扣除 TTS 常驻后口径）。
- 慢路径 TOP3：①首次模型加载数十秒→异步+胶囊 loading（R-5）②MDX 三阶段分钟级（训练前置，一次性）③合成 p99 秒级（与 API 等价，省回环 HTTP 开销）。
- 缓存：prompt_cache 上游自带；resample LRU 上游自带；无新增热路径计算。
- 发现：0 新问题。

### Section 8: Observability & Debuggability

- 日志：inproc 引擎合成请求/耗时/换模事件结构化日志（并入 R-1 验收——**auto-accept 增补：合成耗时与首块延迟计时**）；训练步骤五态+日志尾既有。
- 指标：胶囊三态+推理中指示（R-5）；GPU 显存余量（gpu_policy 既有查询）。
- 3 周后排障：logs/services/ 日志形态平移至 inproc 日志文件；步骤产物目录可追溯。
- Runbook：胶囊 error→查看日志+重载按钮（DX2 既有四要素）。
- 发现：1 增补（R-1 加计时日志，低成本）。

### Section 9: Deployment & Rollout Review

- 部署顺序：**S-1 副本扩充为硬前置**（否则进程内 import 与训练 cwd 全部失败）→ R-1 引擎 → R-2 接线 → R-3/R-4/R-5 → T 系列（训练修复可与 R 系列并行，但同样依赖 S-1）→ C-1 迁移 → F 系列。
- 回滚：git revert 整批+config 向后兼容（旧键 hosted/type 可用）；backend="api" 运行时兜底（需用户自备外部进程，UI 文案明示）。
- 冒烟：TST-2 端到端。
- 发现：1 修订——任务表补 S-1 前置标注（任务表 S-1 行已列首位，补显式依赖声明）。

### Section 10: Long-Term Trajectory Review

- 技术债：users.pth 隐性依赖（R5 已列，收编义务既有）；上游 yaml 回写禁令已立。
- 路径依赖：进程内 TTS 为"训练后自动接线"（TODOS 既有 P2）直接铺路——换模事务（R-4）即其所需机制。
- 可逆性：3/5（backend 键+git revert 可回，但托管代码退役后回子进程需恢复 gpt_sovits_service.py——git 历史保留）。
- 1 年问题：单文件引擎封装+键映射表注释，新工程师可读。
- 平台潜力：进程内引擎模式可为未来其他模型进程内化提供范式（对照 DanmakuListener/audio_player 先例）。
- 发现：0 新问题。

### Section 11: Design & UX Review（UI scope=yes）

用户旅程（训练→消费闭环）：

```
训练声音页                    TTS 页                      直播
┌────────────────┐          ┌────────────────┐
│ 参数面(分组折叠) │──训练──→ │ 档案选择器      │──开播──→ 新声音说弹幕
│ ①基础:数据集/名 │          │ (loading 禁选)  │         （闭环 ≤1 页跳转）
│ ②高级:lr/save/  │          │ 试听按钮(可用!)  │
│   DPO/ASR语言   │          │ backend 折叠卡  │
│ 步骤五态+GPU卡  │          └────────────────┘
└────────────────┘
状态覆盖：训练五态既有｜换模 loading（D7）｜胶囊三态（R-5）｜试听空态→已接通
```

- 信息架构：训练页参数 2→7 控件，**分组折叠**（基础/高级）防止信息过载——并入 F-1 验收。
- AI slop 风险：无（全部控件对应用户明示规格，无泛用模板）。
- 响应式：桌面优先既有裁决，移动端不在范围。
- a11y：Quasar 基础+令牌（既有义务）。
- 30 分钟 touch：参数分组折叠（并入 F-1）✓。
- 建议：实施后跑 /design-review 实测（既有流程）。
- 发现：1 增补（F-1 加分组折叠验收）。

### What already exists（既有资产与复用）

| 子问题 | 既有实现 | 复用方式 |
|---|---|---|
| 训练编排框架 | train_voice_pipeline.py 7 步+五态+授权卡 | 改造 3 处（uvr5→MDX/ASR 路由/参数固化），不重建 |
| 推理能力 | 上游 TTS_infer_pack.TTS 类（无 fastapi 依赖） | 进程内 import 直调 |
| MDX 分离 | apps/aicovergen/src/mdx.py + cover_worker 副本调用先例 | 进程内 import run_mdx |
| ASR 双模型 | 上游 funasr_asr.py/fasterwhisper_asr.py | 子进程调用+路由层 |
| 换模机制 | api_v2 /set_*_weights 端点证明 init_* 重载可行（R2 备忘） | 进程内等价重载+事务 |
| GPU 准入 | utils/gpu_policy.py is_background_task_allowed | 升级口径不重建 |
| 进程内整合范式 | DanmakuListener/audio_player 两条 binding decision | 架构模式参照 |
| 声音档案 | voice_profiles 三端消费已就绪 | 零改动消费 |
| 原子写/E-4 | config 单一持久化模块 | 迁移复用 |

### Failure Modes Registry

| CODEPATH | FAILURE MODE | RESCUED? | TEST? | USER SEES? | LOGGED? |
|---|---|---|---|---|---|
| inproc#synthesize | 推理异常自愈失败 | Y | Y(TST-1) | error 胶囊+失败提示 | Y |
| inproc#switch | 新模型加载失败 | Y | Y(TST-1) | 旧模型保持+notify | Y |
| inproc 推理线程 | CUDA 级崩溃 | Y(降险声明) | N(E2E 环境) | 进程级=已知风险+兜底指引 | Y |
| EDTalk 推送 | 9881 不可达 | Y | Y(集成) | 无感降级 | Y |
| MDX 步 | 资产缺失/推理失败 | Y | Y(单测跳过逻辑) | 预取指引/步骤红 | Y |
| ASR | 模型缺失下载失败 | Y | Y(路由单测) | 预取提示 | Y |
| s1 训练 | bernard 类死路径 | Y | Y(bernard 回归) | 启动前预检拦截 | Y |
| config 迁移 | 键缺失/半损坏 | Y | Y(迁移集成) | 默认注入/日志 | Y |
| 训练编排#step | 任一步失败 | Y | Y(五态) | 步骤红+重跑 | Y |

CRITICAL GAP 计数：0（CUDA 级崩溃为已声明降险残余，非 silent）。

### Scope Expansion Decisions（SELECTIVE EXPANSION）

- Accepted：进程内化引擎（含降险四件套）、训练六步完整化、副本迁移 S-1、MDXNet 资产落 models/mdxnet/、既有三缺陷修复（bernard/双减半/slice）、前端两页、config 迁移、gpu_policy 升级（全部见审计 #1-#8）
- Deferred：训练进度百分比/试听训练成果/失败定位卡/底模一键校验按钮/训练完成通知（delight，TODOS）；断点续训/多角色/自动接线（TODOS 既有）
- Skipped：无（本 run 无 REJECT 项）

### Stale Diagram Audit

- docs/architecture/系统拓扑.md 的服务拓扑图：本计划实施后 GPT-SoVITS 托管节点将失效——**实施批次需同步更新该图**（并入 C-1 验收）
- specs/项目结构.md：无涉
- 其余计划触及文件无 ASCII 图

## DX Review (plan-devex-review)

<!-- autoplan-accepted:dx -->
审查日期：2026-10-13 | 模式：DX POLISH（既有产品增强，spawned 自动裁决）| 主用户：单人主播（半技术用户）| 外部声音：N/A（codex unavailable，同 CEO 批次根因；native fallback 同因不可用）

### Prior 基线与自动决策记录

- Prior 评分（音频三项目整合计划 DX 审查 final）：Getting Started 9 / API config 9 / Errors 9 / Docs 9 / Upgrade 8 / DevEnv 8 / Community 5(N/A) / Measurement 7；Overall 8；TTHW=双击→开播出声 ≤30 分钟（Competitive 档）。
- 既有 DX 义务 DX1-DX9 全部沿用（定义=音频三项目计划 P1/P2/P3 验收），本审查轴线="新失败面/新 UI/新 config 面是否平移既有义务"，新增发现编号接续 DX-10。
- Spawned 自动决策：①模式=DX POLISH；②TTHW 目标=旅程 A 维持 ≤30 分钟 + 旅程 B（录音→自己的声音可开播）端到端 ≤2 小时（Competitive 档）；③全部发现按 P5 结构性自动修复处置（验收条款增补，零功能扩张）；④Community 维度单人阶段 N/A 不扩展（沿用上批裁决）。

### Developer Persona Card（简版）

- Who：单人主播小雅，半技术用户；会双击安装包、会在 UI 填路径，不看控制台日志、不改代码；config.json 只在文档有示例片段时才敢手改。
- Context：装好 AUTOlive 想拥有"自己的声音"——训练→TTS 开播消费闭环。
- Tolerance：单页操作卡住 3 分钟即放弃该功能；迁移后界面大改无提示会以为"坏了"。
- Expects：训练按钮按下后每一步有状态；失败有原因+怎么办；训练完的声音自动出现在 TTS 页可选。

### Empathy Narrative（简版，含首次使用困惑内嵌）

我（小雅）升级 AUTOlive 后打开"数字人-文本转语音"页：熟悉的六个 API 类型下拉不见了，GPT-SoVITS 卡变成"进程内引擎（默认）+外部 API 兜底（折叠）"。没有任何提示告诉我为什么（DX-15 缺口）。我之前按文档排查过"端口 9880 被占用"，现在去翻文档——文档还写着 hosted=false 逃逸口，但那个键已退役（DX-14 缺口）。我录了一段声音进训练页：参数分了基础/高级两组，多了 ASR 语言下拉（默认中文，合理），二次确认里明说"训练期间 TTS 共享显存"（好）。点训练，MDX 分离→切分→识别→三连→SoVITS→GPT 六步状态逐个翻绿——上一版这里必崩（bernard 死路径），我根本走不到这一步。训练完成五态全绿，页面没有下一步指引（delight 已 defer，可接受），我记得去 TTS 页选新档案。切换档案时弹了"切换失败，声音不中断"，但没说失败原因和怎么办（DX-13 缺口）。如果我是 hosted=false 老用户，config 自动映射成 backend="api"，这条提示只写进了日志——我从不看日志（DX-16 缺口）。

### TTHW 评估（current→target）

| 旅程 | 起点→终点 | current | target | 证据 |
|---|---|---|---|---|
| A 装机出声 | 双击安装→开播 TTS 出声（内置音色） | ≤30 分钟（既有承诺，本计划不变；inproc 异步加载不阻塞首启，R-5） | ≤30 分钟（维持 Competitive 档） | 音频三项目计划 TTHW 条目 |
| B 自己的声音 | 数据就绪→新声音可开播 | **∞（训练链实际断裂）**：s1 启动即 FileNotFoundError（本计划「现状」节 bernard 死路径）；带噪素材无法训练（uvr5 恒 SKIPPED）；非中文素材无 ASR 路由 | 操作员动手 ≤30 分钟+机器时间；端到端 ≤2 小时（Competitive 档，建议入 TST-2 实测） | 本计划 B 节六步完整化 |
| C 维护者验证 | clone→pytest（mock 无 GPU）→E2E 冒烟 | 未定义 | TST-1（mock 全绿 CI 可跑）+TST-2（标注环境依赖 skip 条件） | 计划 TST-1/TST-2+0I HOUR 6 |

旅程 B 是本计划对 TTHW 的实质贡献：从"不可能"到"2 小时内"。建议将"端到端 ≤2 小时"写入 TST-2 验收作实测基准（DX-19）。

### Developer Journey Map（更新）

| STAGE | DEVELOPER DOES | FRICTION POINTS | STATUS |
|---|---|---|---|
| 1. Discover | 口碑/作者推荐 | — | ok |
| 2. Install | 双击安装包 | mdxnet 资产缺失 | fixed（T-1 manifest SHA-256+预取） |
| 3. Hello World | 首屏出声 | inproc 首块延迟（模型加载数十秒） | fixed（R-5 异步加载+loading 胶囊+预热） |
| 4. Real Usage | 开播 TTS/换声音 | 六选一下拉消失无提示 | fixed（DX-15 首次进入提示） |
| 5. Debug | 看胶囊/日志 | 兜底 api 通路失败文案未定义 | fixed（DX-12 错误表补行） |
| 6. Train Voice | 训练自己的声音 | 失败态无帮助入口；新失败面文案缺四要素 | fixed（DX-13；D5 既有三要素顺带补第四要素） |
| 7. Consume | TTS 页选用新档案 | params 与档案参数优先级未定义 | fixed（DX-11） |
| 8. Migrate | hosted→backend 映射 | 通知只走日志；文档指向退役键 | fixed（DX-14/DX-16） |

First-Time Confusion 摘要：T+0 升级打开 TTS 页界面大变无提示（DX-15）；T+5min 按旧文档排查 9880/hosted=false 踩退役键（DX-14）；T+40min 训练完成无下一步指引（deferred，接受）；T+50min 切档案失败文案无 cause/fix（DX-13）；T+60min hosted 老用户不知已映射 backend="api"（DX-16）。

### 8 维度评分表（initial→final）

| 维度 | prior | initial | final | 依据 |
|---|---|---|---|---|
| Getting Started | 9 | 9 | 9 | 不触碰安装/首启路径；旅程 A 维持 |
| API/CLI/SDK（config 键面+内部接口） | 9 | 7 | 9 | DX-10 示例片段、DX-11 优先级并入验收后恢复 |
| Error Messages | 9 | 7 | 9 | DX-12/13 四要素平移到新失败面 |
| Documentation | 9 | 7 | 9 | DX-14 文档同步义务补入 |
| Upgrade Path | 8 | 7 | 9 | DX-15 首次提示、DX-16 UI notify |
| Dev Environment | 8 | 8 | 8 | S-1 副本验收含零外部树依赖 |
| Community | 5 | 5 | 5 | N/A（单人阶段，不计入 Overall） |
| DX Measurement | 7 | 7 | 7 | 无遥测=有意沿用；TST-2 记录实测（DX-19） |
| **Overall** | 8 | **7.6** | **8.6** | Community 不计 |

DX PRINCIPLE COVERAGE：Zero Friction=covered（旅程 A 不变）/ Learn by Doing=covered（试听接通+训练五态）/ Fight Uncertainty=initial gap→final covered（DX-12/13）/ Opinionated+Escape Hatches=covered（inproc 默认+api 兜底逃逸口）/ Code in Context=covered（参数面板=v2 参数树语义平移）/ Magical Moments=covered（训练完成→自己的声音，载体既有）。

### 发现清单与处置（编号接续既有 DX1-DX9）

| # | 严重度 | 发现 | 定位 | 处置 |
|---|---|---|---|---|
| DX-10 | 中 | config 大迁移（16 键）无"附示例 JSON 片段"义务——半技术用户手改 gpt_sovits 节无参照（DX6 既有精神） | 计划 C 节键映射表 / C-1 | 并入 C-1 验收：迁移后 config 附完整 gpt_sovits 节示例片段+键注释 |
| DX-11 | 中 | gpt_sovits.params 与 voice_profiles 档案的参数优先级未定义——用户调过 top_p 后切档案，参数归谁？ | 计划 A 节换模 / C 节键映射 | 并入 C-1 键映射注释+F-2 验收：身份参数（ref_audio/prompt_text）随档案，风格参数（top_k/top_p/temperature 等）为页面级覆盖，规则明写 |
| DX-12 | 中 | backend="api" 兜底通路的失败模式无错误表行——外部服务未启动/不可达时用户看到什么未定义（现状 synthesis_manager.py:374 "合成失败，{e}" 非四要素） | 计划 Section 2 错误表 / R-2 | 错误表补行+R-2 验收：文案=四要素（服务未启动→检查 api_ip_port→启动指引→帮助入口） |
| DX-13 | 中 | 新失败面（EngineNotReady/ModelLoadError/切换失败）USER SEES 列仅一句抽象描述，未绑定 DX2 四要素验收；既有训练页失败态仍是三要素（train_voice.py:202 注释自证，无"查看帮助"入口） | 计划 Section 2 / R-1/R-4/F-1 | R-1/R-4 验收加四要素条款；F-1 顺带补训练页失败态第四要素（帮助入口指向指引卡） |
| DX-14 | 高 | docs/开发环境搭建.md 端口表+常见失败对照指向将退役的 9880/hosted=false（docs/开发环境搭建.md:45-51,73）——实施后文档指引指向已退役 config 键，误导升级用户排查；Stale Diagram Audit 只列了系统拓扑.md | 计划 Stale Diagram Audit / C-1 | C-1 验收（文档同步项）加：端口表删 9880 行、9881 语义更新（EDTalk 推送）、失败对照表 hosted=false 条目改写为 backend="api" |
| DX-15 | 中 | TTS 页整卡重做（六选一下拉退役）是本计划最大 UI 变化，缺 DX9 式"首次进入提示一次" | 计划 C 节 F-2 | F-2 验收加：首次进入 TTS 页 notify 一次"GPT-SoVITS 已切换为内置引擎，原 API 设置已转为兜底通道"（复用 legacy_svc.show 先例机制） |
| DX-16 | 中 | 迁移的用户决策点通知只走日志——legacy 档案创建/权重废弃"日志提示重训练"（主播不看控制台）；hosted=false→backend 映射提示同理 | 计划 C 节键映射表 / C-1 | C-1 验收加：迁移结果以 UI notify 或档案列表标注呈现；废弃路径档案在 TTS 页可见标注"已失效，请重训练" |
| DX-17 | 低 | 试听/加载模型按钮在 loading/error 态的可用性未定义；试听失败文案"请查看日志排查问题"（tts.py:71）非四要素 | 计划 C 节 F-2 | F-2 验收加：loading/error 态按钮禁用+tooltip；试听失败文案四要素化 |
| DX-18 | 低 | config 键值 "inproc" 对半技术用户偏技术（UI 已有中文映射，风险仅在 config 层） | 计划 D 节 backend 键 | 并入 DX-10 示例片段：注释 inproc=内置引擎 |
| DX-19 | 低 | 新训练旅程无 TTHW 目标条目——本计划核心价值缺时间预算 | 计划 0C 节 | 建议写入：数据就绪→可开播端到端 ≤2 小时（Competitive 档），TST-2 记录实测 |
| DX-20 | 低 | F-2"webtts 去留待裁决"与审计 #3"webtts 保留"表述矛盾 | 计划 C 节 F-2 | F-2 文案改为"webtts 保留（审计 #3）"，消除实施歧义 |

处置总述：11 条全部接受（P5 结构性自动修复——均为验收条款/文案/文档增补，零功能扩张，符合 DX POLISH 与 spawned 自动决策授权）。

### Magical Moment

训练完成→自己的声音第一次说出弹幕。载体=既有能力（五态成功+export 写档案+TTS 选择器+试听接通），本计划全部到位，无需新功能（DX POLISH verify exists 通过）。delight 增补（训练完成通知/试听训练成果/自动接线）已 defer（审计 #8），本报告不重开。

### NOT in scope（明确不计入本计划）

- 训练完成通知/试听训练成果/训练后自动接线（TODOS+审计 #8 deferred）
- 遥测/TTHW 自动测量（无遥测=有意，沿用上批裁决；TST-2 人工记录即可）
- Community 生态扩展（单人阶段 N/A）
- 移动端/响应式（桌面优先既有裁决）

### What already exists（本计划应复用的 DX 资产）

| 资产 | 位置 | 复用方式 |
|---|---|---|
| DX2 四要素失败态 | train_voice.py:202-207 五态机制 | 新失败面文案照此扩展（DX-13） |
| DX9 首次提示机制 | svc.py legacy 提示先例 | F-2 首次进入提示（DX-15） |
| DX5 预取 UX | T-4 预检机制 | MDX/ASR 资产缺失文案 |
| DX1 端口归因 | docs/开发环境搭建.md:49 探测+归因 | 9880 退役后端口表同步（DX-14） |
| 胶囊+帮助入口 | layout.py P1-3 机制 | 胶囊 error 态文案（Section 8 Runbook） |

### DX Implementation Checklist

```
DX IMPLEMENTATION CHECKLIST
============================
[x] 旅程 A TTHW ≤30 分钟不变（inproc 异步加载不阻塞首启，R-5）
[ ] 旅程 B 端到端 ≤2 小时写入 TST-2 验收并实测（DX-19）
[x] 安装一步到位（沿用 P1-5 安装包；mdxnet 资产 manifest+预取，T-1）
[x] 首次运行有意义输出（训练五态+胶囊 loading/ready）
[x] 魔法时刻载体到位（五态成功+export+试听接通，F-2）
[ ] 每条新错误文案=problem+cause+fix+帮助入口（DX-12/13，并入 R-1/R-2/R-4/F-1/F-2 验收）
[x] config 键名可猜（params 键名对齐 TTS.run 清单）
[ ] 迁移后 config 附 gpt_sovits 节示例片段+注释（DX-10，并入 C-1）
[ ] params 与档案参数优先级规则明写（DX-11，并入 C-1/F-2）
[ ] backend="api" 失败文案四要素+错误表补行（DX-12，并入 R-2）
[ ] TTS 页首次进入提示一次（DX-15，并入 F-2）
[ ] 迁移通知走 UI 而非仅日志（DX-16，并入 C-1）
[ ] 开发环境搭建.md 端口表/失败对照同步（DX-14，并入 C-1）
[ ] loading/error 态控件禁用+tooltip（DX-17，并入 F-2）
[x] 兜底=api 逃逸口保留（backend 键，UI 文案明示自备前提）
[ ] 文档示例注释 inproc=内置引擎（DX-18，并入 DX-10）
```

### Implementation Tasks（并入计划任务表验收，不新增独立任务）

- [ ] **DX-T1 (P2, human: ~1h / CC: ~10min)** — C-1 config 迁移验收增补 — 示例片段（DX-10/DX-18）+params 优先级注释（DX-11）+迁移 UI 通知（DX-16）+开发环境搭建.md 端口表同步（DX-14）
  - Surfaced by: DX 审查 DX-10/11/14/16 — 各条见发现表定位
  - Files: config 模块、docs/开发环境搭建.md、C-1 验收文本
  - Verify: 键映射逐键验收时附示例片段存在性检查；文档无 hosted/9880 残留指引
- [ ] **DX-T2 (P2, human: ~1h / CC: ~10min)** — R-2 兜底通路错误文案 — backend="api" 失败四要素+Section 2 错误表补行
  - Surfaced by: DX-12 — synthesis_manager.py:374 现状 "合成失败，{e}"
  - Files: my_tts.py、synthesis_manager.py、计划 Section 2
  - Verify: 停掉外部服务后试听，文案含 problem/cause/fix/帮助入口
- [ ] **DX-T3 (P2, human: ~40min / CC: ~5min)** — F-2 TTS 页验收增补 — 首次进入提示（DX-15）+按钮态（DX-17）
  - Surfaced by: DX-15/17 — tts.py:97 试听按钮现状
  - Files: tts.py、F-2 验收文本
  - Verify: 首次进入 notify 一次；loading 态试听禁用+tooltip
- [ ] **DX-T4 (P2, human: ~30min / CC: ~5min)** — R-1/R-4/F-1 四要素绑定 — 新失败态文案验收+训练页失败态补第四要素
  - Surfaced by: DX-13 — train_voice.py:202 三要素自证
  - Files: gpt_sovits_inproc.py、train_voice.py
  - Verify: 切换失败/引擎未就绪文案含四要素；失败态含帮助入口
- [ ] **DX-T5 (P3, human: ~20min / CC: ~5min)** — 文档与文案澄清 — webtts 表述（DX-20）+inproc 注释（DX-18）+TTHW 目标入 0C/TST-2（DX-19）
  - Surfaced by: DX-18/19/20
  - Files: 计划文本、docs
  - Verify: F-2 无"待裁决"字样；TST-2 含实测基准

### Consensus

N/A——外部声音不可用（codex unavailable：custom 端点无 frontier 模型+TaskOutput 缺失，同 CEO 批次根因，见 REPORT 表 Outside Review 行；native fallback 同因不可用）。本审查为单模型结论，11 条发现全部基于已核实代码事实（file:line 见定位列）。

## Design Review (plan-design-review)

<!-- autoplan-accepted:design -->
审查日期：2026-10-13 | 模式：DESIGN POLISH（既有产品参数面扩张+面板重做，spawned 自动裁决）| UI scope：训练页参数面（F-1）/ TTS 页 GPT-SoVITS 卡重做（F-2）/ 状态条胶囊语义（R-5）| 外部声音：N/A（同 CEO/DX 批次根因）；mockup 生成跳过（spawned 管道无批准回路，视觉把关移交实施后 /design-review——CEO S11 既有流程）

### 范围与基线

- 无 DESIGN.md——设计规范载体=frontend/styles/design_tokens.py 令牌系统 + 既有 D1-D12 裁决（录音指引卡/步骤五态/失败四要素/选择器分组 loading/档案命名表单）+ 宽度三档 + 禁硬编码色值 + 视口桌面优先 1280+（移动端不在范围）。本审查全部设计决策以此校准。
- 不重复 CEO/DX/Eng 审查：DX-10~DX-20（首次进入提示/四要素/迁移通知等）与 E-T1~E-T5 全部沿用；本节只处置设计维度新增缺口，发现编号 D-1 起（区别于 Eng 节 D1-D5 决策编号与 CEO-Dx 既有义务）。
- 代码事实核实：frontend/ui/tabs/train_voice.py（218 行）、frontend/ui/tabs/tts.py（649 行）、frontend/ui/layout.py 胶囊挂载区（:761-765）、frontend/ui/components/service_status.py（166 行）、frontend/styles/design_tokens.py（270 行）。

### Step 0: 初始评分

**6/10**——计划描述了控件清单与分组意向，但四处未决设计面拉低完整性：①F-1 分组切分有输入语义错位（D-1）②胶囊四态视觉映射空白（D-2）③F-2 参数树"等"字含糊+legacy 双轨未裁（D-3/D-4）④能力变化引起的文案漂移未察觉（D-5）。10 分形态=分组按用户心智切分、每态有令牌化视觉规格、参数面板逐控件清单化、文案与能力同步。

### 七 Pass 评分（initial → final）

| Pass | 维度 | initial | final | 变化依据 |
|---|---|---|---|---|
| 1 | 信息架构 | 6 | 9 | D-1（ASR 语言上移基础组）/D-3（参数清单化）/D-4（legacy 双轨退役）并入 |
| 2 | 交互状态覆盖 | 7 | 9 | D-2（胶囊四态映射）/D-7（试听 PARTIAL 标注）并入；状态覆盖表补全 |
| 3 | 用户旅程与情绪 | 7 | 9 | D-5（指引文案随能力同步）并入；storyboard 补全 |
| 4 | AI slop 风险 | 9 | 9 | OPERATE 面无 slop 模式，0 hard rejection；"等"字含糊是唯一含糊源（D-3 根因） |
| 5 | 设计系统对齐 | 7 | 9 | D-2（色映射令牌化）/D-6（宽度三档逐控件）并入 |
| 6 | 响应式与 a11y | 8 | 8 | 桌面优先既有裁决+Quasar 原生可达性；无新缺口 |
| 7 | 未决设计决策 | — | — | 9 resolved（spawned 自动裁决），0 deferred |

**总评：6/10 → 8/10**（取六维最低分）。达到 design-complete 门槛（全 Pass ≥8）。

### 状态覆盖表（Pass 2 产物）

| FEATURE | LOADING | EMPTY | ERROR | SUCCESS | PARTIAL |
|---|---|---|---|---|---|
| 训练步骤（六步） | 步骤 ▶ 黄+日志尾 | PENDING 灰点 | ✖ 红+四要素（既有三要素+帮助入口，DX-13） | ✔ 绿 | skipped"—"态（干声跳过 MDX 等，既有语义） |
| 换模（档案切换） | 档案行/选用按钮 loading 置禁（CEO-D7） | — | 失败保旧模型+四要素 notify（DX-13） | "当前"标记（既有） | — |
| GPT-SoVITS 胶囊 | loading=info 蓝点（D-2 映射） | 引擎恒在，无空态 | error 红点+详情区重载按钮 | ready 绿点 | inferencing=primary 蓝点+"推理中"副标题（D-2） |
| 试听按钮 | 禁用+tooltip（DX-17） | 引擎未就绪→禁用+提示 | 四要素文案（DX-17） | 音频卡+播放+删除（既有） | 部分静音段=上游 yield 空静音语义（D-7 标注） |
| 进程内参数面板 | N/A（配置直写不阻塞） | 默认值注入（C-1） | 保存校验提示 | 即时生效 | — |
| backend 折叠卡 | N/A | api_ip_port 未填→保存校验（复用 is_url_check 既有工具） | 外部服务不可达四要素（DX-12） | 切换 notify | — |
| 档案选择器 | 选用中置禁（D7） | "暂无档案——训练完成后自动出现在此处"（既有） | 废弃路径档案标注"已失效，请重训练"（DX-16） | 当前档案高亮（既有） | — |
| ASR 语言下拉 | N/A | 默认 zh（D-1 修正后位于基础组） | —（select 白名单枚举，Section 3 安全面） | — | — |

### 旅程 storyboard（Pass 3 产物）

| STEP | USER DOES | USER FEELS | PLAN SPECIFIES? |
|---|---|---|---|
| 1 | 打开训练页读指引 | 安心（前置条件一屏讲完） | 录音指引卡既有；D-5 修正文案后与 MDXNet 能力一致 |
| 2 | 选数据集+填实验名+选 ASR 语言 | 确定感（基础组 5 分钟搞定） | F-1 分组；D-1 修正后 ASR 语言在基础组可见可改 |
| 3 | 勾授权+点开始+过确认卡 | 郑重（显存共享知情） | 启动确认卡既有+D-8 确认卡形态 |
| 4 | 盯六步翻绿 | 期待（每步有状态） | 步骤五态既有 |
| 5 | 训练完去 TTS 页选新档案+试听 | 成就（自己的声音） | 档案选择器既有+试听接通（F-2） |
| 6 | 开播用新声音 | 信任（胶囊 ready 绿点常驻） | R-5 胶囊；D-2 映射 |
| 升级用户 T+0 | 打开 TTS 页界面大变 | 困惑→被安抚 | DX-15 首次进入提示 |

时间三尺度：5 秒=胶囊与五态即时可见；5 分钟=训练→试听闭环（DX-19 ≤2h 目标）；5 年=多档案库（TODOS 既有，不在范围）。

### Litmus scorecard（Pass 4 产物，OPERATE 模式）

| Check | 判定 | 证据 |
|---|---|---|
| 1. Brand unmistakable in first screen | YES | 侧边栏 AUTOlive 横幅常驻；工具页 brand 在细节（令牌蓝+横幅重字重） |
| 2. One strong visual anchor | YES | 训练页锚=步骤五态+开始按钮；TTS 页锚=档案选择器（F-2 后） |
| 3. Scannable by headlines only | YES | 卡片标题直述（录音指引/数据集与训练参数/GPU 占用检查/启动确认/流程步骤） |
| 4. Each section has one job | YES | 卡片单职责成立 |
| 5. Cards actually necessary | YES | 表单容器语义，无装饰卡格 |
| 6. Motion improves hierarchy | YES | 无装饰动画；胶囊 3s 刷新=状态语义非装饰 |
| 7. Premium without decorative shadows | YES | 令牌阴影 sm/md 功能性 |
| Hard rejections | 0 | 无 SaaS 卡格/3 列 feature grid/图标圆圈装饰/居中一切 |

### 发现清单与处置（编号 D-1 起）

| # | 严重度 | 发现 | 定位 | 处置 |
|---|---|---|---|---|
| D-1 | 高 | F-1 分组切分错位：ASR 语言归"高级"组违背输入语义——它是素材属性（与数据集目录同类），非训练超参；默认 zh+高级组折叠收起=非中文素材静默走错路由（funasr 处理英文音频产出坏 .list），失败延迟到 40 分钟训练后且无归因（satisficing 用户不展开高级组） | 计划 F-1 / train_voice.py:66-69 | 并入 F-1 验收：ASR 语言上移基础组（基础=数据集目录/实验名/轮数/批大小/已是干声/ASR 语言；高级=lr 权重/save_every/DPO 默认开）；折叠默认态=基础展开+高级收起 |
| D-2 | 高 | 胶囊四态视觉映射与详情形态未定义——R-5 只写"胶囊反映模型状态"：loading/ready/error/inferencing 的令牌色映射、inferencing 呈现方式、详情展开区内容（现 expansion 内"重启/帮助/日志尾"三件套的语义平移）全部空白 | 计划 R-5 / service_status.py:22-38,121-148 | 并入 R-5 验收：色映射 loading→var(--color-info)、ready→var(--color-success)、error→var(--color-danger)、inferencing→var(--color-primary)（8px 圆点+副标题"推理中"）；详情区=当前档案名+模型对+加载耗时+最近推理；"重启"按钮改"重新加载模型"（换模重载语义，非进程重启）；_STATE_COLOR 表并列扩展，禁硬编码 |
| D-3 | 中 | v2 参数树平移未裁剪——F-2"等"字含糊，实施者照抄 v2_api_0821 全部 17 控件会把 HTTP 分发器专用参数带进进程内面板：media_type 是 HTTP 返回格式参数，进程内固定 wav 落盘，不 earn 其像素（subtraction default） | 计划 C 节 F-2 / tts.py:480-486 | 并入 F-2 验收：进程内面板参数清单写死 16 项（ref_audio_path/prompt_text/prompt_lang/text_lang/text_split_method/top_k/top_p/temperature/batch_size/batch_threshold/split_bucket/speed_factor/fragment_interval/seed/repetition_penalty/parallel_infer），media_type 明确不入面板 |
| D-4 | 中 | legacy 双路径输入框（GPT模型路径/SOVITS模型路径）在 F-2 未写去留——与档案选择器双轨并存，且手填路径对外部 api 兜底同样无意义（外部进程自管模型），是"到底哪个生效"困惑源；C 节键映射已裁决迁 legacy 档案但 UI 未同步 | 计划 C 节 F-2 / tts.py:140-155 | 并入 F-2 验收：两输入框随旧卡退役，档案选择器为唯一模型对入口；legacy 路径仅存于迁移生成的档案记录（DX-16 废弃标注） |
| D-5 | 中 | 录音指引与干声复选框文案随 T-1 能力漂移——指引第④条"请提供已分离人声的干声文件（本版不含 UVR5 分离）"在 MDXNet 三阶段落地后失实：用户按指引手动分离白费 30 分钟或放弃可用素材；dry_check"已是干声（跳过 UVR5）"控件语义同步失准（文案-能力漂移=信任损耗） | 计划 T-1 / train_voice.py:30-35,69 | 并入 T-1/F-1 验收：指引第④条改写为"带背景音乐/噪音？系统自动分离（内置 MDXNet），无需预处理"；dry_check 改"跳过声音处理（素材已是干声）" |
| D-6 | 中 | 新控件宽度三档未落计划——lr 权重/save_every/轮数/批大小为数字类（sm=150px）、ASR 语言短枚举（sm）、数据集/实验名路径（lg，现状 100% 沿用）；现状 epoch/batch（train_voice.py:67-68）未设宽度一并补齐 | 计划 F-1 / design_tokens.py:40-42 | 并入 F-1 验收：宽度档位逐控件标注（数字=sm/枚举=sm/路径=lg） |
| D-7 | 低 | 试听 PARTIAL 态未定义——长文本分句合成部分句失败时上游 yield 空静音（TTS.py:1189 语义），表现为音频中段空白而非报错 | 计划 C 节 F-2 | 接受为上游语义并在 F-2 验收标注（显式失败面已四要素覆盖 DX-17；静音段面留待实施后 /design-review 实测评估） |
| D-8 | 低 | 训练二次确认形态未定——计划 C 节"训练按钮二次确认含共享显存提示"，但现状 _start（train_voice.py:139-174）为校验 notify 直启动，无确认卡形态可依托 | 计划 C 节 F-1 / train_voice.py:139 | 并入 F-1 验收：ui.dialog 确认卡（训练为重操作，与启动确认卡同层强化；文案含"训练期间 TTS 进程内推理共享显存，合成可能变慢"） |
| D-9 | 低 | backend 切换控件形态未定——config 键 inproc&#124;"api" 的 UI 呈现未写 | 计划 C 节 F-2 / D 节 backend 键 | 并入 F-2 验收：FormField.create_select 两项（与既有下拉范式一致）；api 态展开折叠卡（内含 api_ip_port+DX-12 四要素失败文案位+"需自行启动外部 GPT-SoVITS 服务"明示） |

处置总述：9 条全部接受（spawned 自动裁决=recommended，均为验收条款/文案/映射表增补，零功能扩张）。修正后全 Pass ≥8。

### NOT in scope（明确不计入本计划）

- 移动端/响应式（桌面优先 1280+ 既有裁决，DX 节同判）
- 新设计组件词汇（expansion/card/grid/select/dialog 全既有复用，零新组件）
- 试听 PARTIAL 静音段的主动检测 UI（D-7：接受上游语义，实施后 /design-review 实测）
- ARIA 增强（TODOS.md:27 既有全局债，本计划页面沿用 Quasar 原生可达性）
- 训练完成通知/自动接线 delight（审计 #8 已 defer，不重开）

### What already exists（本计划应复用的设计资产）

| 资产 | 位置 | 复用方式 |
|---|---|---|
| 状态胶囊组件 | frontend/ui/components/service_status.py | R-5 语义平移改 _STATE_COLOR/_STATE_LABEL 映射表+详情区，不新建组件 |
| 分组折叠范式 | service_status.py:125（ui.expansion） | F-1 基础/高级分组同款组件 |
| 设计令牌 | frontend/styles/design_tokens.py | 色映射/宽度三档/字号唯一来源 |
| 步骤五态机制 | train_voice.py:182-210 | F-1 新控件不触碰五态 |
| 档案选择器 | tts.py:566-648 | F-2 保留，升为唯一模型对入口 |
| 首次提示/通知机制 | ui.notify + DX9 legacy show 先例 | DX-15 提示/四要素文案 |
| D1-D12 既有裁决 | 计划与 TODOS | 全部适用于本计划页面 |

### Decision ledger（spawned 自动裁决记录）

| # | 决策点 | 自动裁决（recommended） | 理由 |
|---|---|---|---|
| D-1 | F-1 分组切分 | ASR 语言上移基础组 | 输入语义优先于专家超参；错路由失败无归因（Krug：让对的选择最可见） |
| D-2 | 胶囊四态映射 | info/success/danger/primary 四令牌 | 与既有 _STATE_COLOR 表并列扩展；模型加载非服务故障，不与 starting 的 warning 混淆 |
| D-3 | 参数树裁剪 | media_type 不入进程内面板 | HTTP 分发器专用参数；进程内固定 wav 落盘（subtraction default） |
| D-4 | legacy 双路径框 | 随旧卡退役 | 双轨困惑源；档案选择器为唯一入口（一种正确方式） |
| D-5 | 指引文案 | 随 T-1 同步改写 | 能力变化文案必须同步（信任在像素级建立，也在文案级流失） |
| D-6 | 宽度三档 | 逐控件标注并入验收 | 令牌合规既有裁决 |
| D-7 | 试听 PARTIAL | 接受上游语义+标注 | 显式失败面已四要素覆盖；静音段低概率 |
| D-8 | 二次确认形态 | ui.dialog 确认卡 | 重操作强化；与授权卡同层 |
| D-9 | backend 控件 | create_select 两项 | 既有范式一致；api 态展开折叠卡 |

### Implementation Tasks

由本审查发现综合（均为验收条款增补，并入既有任务表对应行）：

- [ ] **DS-T1 (P1, human: ~1.5h / CC: ~15min)** — F-1/T-1 验收增补 — ASR 语言上移基础组+折叠默认态（D-1）+指引/dry_check 文案同步（D-5）+宽度三档标注（D-6）+确认卡形态（D-8）
  - Surfaced by: Pass 1 D-1 / Pass 3 D-5 / Pass 5 D-6/D-8 — train_voice.py:30-35,66-69 现状
  - Files: 计划 F-1/T-1 验收文本、frontend/ui/tabs/train_voice.py
  - Verify: 非中文素材训练前 ASR 语言在基础组可见可改；文案无"本版不含 UVR5 分离"残留；数字控件宽度=150px 档
- [ ] **DS-T2 (P1, human: ~1h / CC: ~10min)** — R-5 验收增补 — 胶囊四态令牌色映射+inferencing 副标题+详情区语义平移（D-2）
  - Surfaced by: Pass 2/5 D-2 — service_status.py:22-38 映射表现状
  - Files: 计划 R-5 验收文本、frontend/ui/components/service_status.py、frontend/ui/layout.py
  - Verify: 四态色全部令牌变量（无硬编码）；error 态详情含重载入口；"重启"文案不残留
- [ ] **DS-T3 (P2, human: ~1h / CC: ~10min)** — F-2 验收增补 — 参数清单写死 16 项去 media_type（D-3）+legacy 双路径框退役（D-4）+backend create_select 形态（D-9）
  - Surfaced by: Pass 1/4 D-3/D-4/D-9 — tts.py:140-155,480-486 现状
  - Files: 计划 F-2 验收文本、frontend/ui/tabs/tts.py
  - Verify: 面板无 media_type 控件；无手填 GPT/SOVITS 路径框；backend 切换展开折叠卡
- [ ] **DS-T4 (P3, human: ~20min / CC: ~5min)** — F-2 验收标注 — 试听 PARTIAL 上游语义标注（D-7）
  - Surfaced by: Pass 2 D-7 — TTS.py:1189 空静音语义
  - Files: 计划 F-2 验收文本
  - Verify: F-2 验收含 PARTIAL 语义标注行

### Completion Summary

```
+====================================================================+
|         DESIGN PLAN REVIEW — COMPLETION SUMMARY                    |
+====================================================================+
| System Audit         | 无 DESIGN.md（令牌载体 design_tokens.py）  |
|                      | UI scope=F-1/F-2/R-5 三处                  |
| Step 0               | initial 6/10（分组错位/四态未定义/文案漂移）|
| Pass 1  (Info Arch)  | 6/10 → 9/10 after fixes                   |
| Pass 2  (States)     | 7/10 → 9/10 after fixes                   |
| Pass 3  (Journey)    | 7/10 → 9/10 after fixes                   |
| Pass 4  (AI Slop)    | 9/10 → 9/10（0 hard rejection）            |
| Pass 5  (Design Sys) | 7/10 → 9/10 after fixes                   |
| Pass 6  (Responsive) | 8/10 → 8/10（桌面优先既有裁决）             |
| Pass 7  (Decisions)  | 9 resolved, 0 deferred                    |
+--------------------------------------------------------------------+
| NOT in scope         | written (5 items)                         |
| What already exists  | written                                   |
| TODOS.md updates     | 0 items proposed（无新增设计债）            |
| Approved Mockups     | 0 generated（spawned 无批准回路）           |
| Decisions made       | 9 added to plan                           |
| Decisions deferred   | 0                                         |
| Overall design score | 6/10 → 8/10                               |
+====================================================================+
```

Plan is design-complete（全 Pass ≥8，0 unresolved）。实施后按既有流程跑 /design-review 做视觉实测 QA（D-7 静音段与胶囊四态实测项随行）。

## Eng Review (plan-eng-review)

<!-- autoplan-accepted:eng -->
审查日期：2026-10-13 | 模式：FULL_REVIEW（scope accepted as-is）| 复杂度门：触发（≈16 文件+2 新类）→ 结构裁决=Original arrangement | 外部声音：N/A（本批次 codex 不可用，同 CEO/DX 根因）| 工程复核范围：架构/代码路径/测试/依赖/失败模式（不重复 CEO/DX 审查；对 Decision Audit Trail 8 行与各 Review Sections 既有义务只做正确性复核）

### Reality Rows（eng）

- REALITY: premise-table pass — 计划「现状」5 前提全部实测复核：my_tts.py:101-282 六分支 ✓；synthesis_manager.py:310-363 只认 gpt_sovits/none ✓；tts_infer_probe.yaml custom 节钉死 wy 权重+模板节双死链（t2s=sv_pretrained_bernard_ckpt_20250213.ckpt、vits=s2Gv2ProPlus_pretrained_20250518.pth）✓；train_voice_pipeline.py:276 bernard 死路径、:270 无条件 //2、:192-195 slice 7 argv 三缺陷逐行确认 ✓；EDTalk 通道前提 1 条修正（见 E-1：托管启动命令不带 -rt，synthesis_manager:33-43 分流的是 playback_manager 文件级推送而非 delivery=1 流式），unverified=0
- REALITY: already-done pass — utils/gpt_sovits_inproc.py 不存在（2026-10-13 实测），训练/推理文件基线 cfdb4da4 无重复建设
- REALITY: surface-check pass — TTS.py:325-378 TTS_Config dict 构造（不写 yaml，但路径不存在时静默回落相对默认值 :366-377）、:1044 run(inputs: dict) 同步生成器、:1189 空 text yield 16k 静音、:1446-1487 自愈后 re-raise、init_t2s_weights:623/enable_half_precision:727,767 三处 save_configs；api_v2.py:295-352 InferenceGate（max_active=1/max_waiting=2/线程安全释放）；webui.py:724-739 slice 11 参数（现状缺 _max/alpha/i_part/all_parts 证实）；edtalk_client.py:144-163 push_full 整句文件级+单飞行句背压锁；external=apps 副本 api_v2/TTS.py md5 一致（53d90144/295d265a）
- REALITY: binding-decisions pass — 审计 #1-#8 与代码事实一致；CEO-S2 换模事务、F11 兜底、B-5 兜底通道义务在任务表中均有承接
- REALITY: numbers pass — 外部树（不含 pretrained_models/logs/output/TEMP/__pycache__）680 文件、tools/ 142 文件、GPT_SoVITS/ 215 文件 [measured: D:\AI\GPT-SoVITS find 实测]；apps 副本现 199 文件且无 tools/ [measured]；计划「316 文件」口径偏低但 S-1 已写明"以实施时实测为准"，不构成阻塞；MDX 三模型 63.7/50.3/63.7MB 与计划一致
- REALITY: deferred-asks pass — TODOS 三延期项（多角色/断点续训/自动接线）与审计 #8 delight 延期保持不动；export 步保留义务已写明"实施者不得误删"
- REALITY: pr-count pass — 一波一 PR（ship-policy 默认），S-1 为 R/T 系硬前置的顺序声明成立

### Scope Challenge（结论）

- 逐项核验后未发现可裁剪项：六步训练完整化、进程内化、副本迁移、前端两页、config 迁移均直接服务用户明示需求；CEO 已完成裁剪（死分支退役/MDXNet 复用等）。**scope accepted as-is**。
- 复杂度门结构问题（spawned 自动裁决=recommended）：Original arrangement（单文件引擎 utils/gpt_sovits_inproc.py + 训练管线原地改造）优于更小方案——引擎塞入 my_tts.py 会使该文件超 800 行且混合进程管理与合成编排职责；拆分引擎为多文件则违背 utils/ 单模块封装形态（对照 rvc_realtime_service.py 范式）。功能清单维持 CEO 裁决不动。
- TODOS 交叉：无新增 TODO（delight/自动接线均已 defer 且有归属）。

### Section 1: Architecture Review

**进程内化后组件图（对计划 Section 1 图的修正——补双进程拓扑与推送通道现状）：**

```
┌────────────────────────────────────────────────────────────────────┐
│ webui 前端进程（frontend/main.py，NiceGUI，ui.run:1154）             │
│  训练页/TTS页/胶囊（R-5 模型状态经 HTTP 从核心进程拉取）               │
│  ⚠ 禁止 import GPT_SoVITS（E-2：显存双份+事件循环阻塞风险）          │
└──────────────┬─────────────────────────────────────────────────────┘
               │ subprocess.Popen（frontend/main.py:287,436-438）
┌──────────────▼─────────────────────────────────────────────────────┐
│ 核心业务进程（main.py，EDTalk runtime312，含 asyncio 事件循环）       │
│  message_queue_thread → synthesis_manager.tts_handle               │
│    → my_tts.gpt_sovits_api ──backend=inproc──→ gpt_sovits_inproc   │
│         （新）InProcEngine：sys.path 注入→TTS_Config(dict,绝对路径) │
│           → TTS 实例（专用推理线程 + 全局互斥临界区）                │
│         backend=api ──→ v2_api_0821 HTTP 兜底（用户自备外部进程）    │
│  合成产物 wav 落盘 ──→ playback_manager（现状零改动）                │
│    ├─ is_edtalk_active → edtalk_client.push_full(整句,9881)        │
│    └─ 本地播放队列                                                  │
│  训练编排 train_voice_pipeline ──子进程(runtime312-clone)──→        │
│    cwd=apps/gpt-sovits：slice/ASR/prepare/s2/s1（gpu_policy 准入）  │
└────────────────────────────────────────────────────────────────────┘
托管退役：gpt_sovits_service.py 退役，ManagedProcess 保留给 RVC/DanmakuListener
```

计划 Section 1 的 BEFORE/AFTER 对比图与四路径（Happy/Nil/Empty/Error）数据流经核验成立：TTS.py:1189 空 text yield 16k 静音、:1446-1487 自愈后 re-raise（Error 路径"本次请求返回明确失败"与 re-raise 契约一致）；状态机非法转换防护与锁包住整个生成器消费过程的设计可直接用 threading 语义实现。修正点只有两处：①推送通道归属（E-1）；②进程归属（E-2）。

**发现：**

- `[P1] (confidence: 9/10) utils/audio/playback_manager.py:129-137 + api_v2.py:64 — R-3 把 EDTalk 推送内置进 inproc 消费循环，与下游 playback_manager 既有分流形成双推风险，且"流式逐块"违反 /audio/push_full 整段契约`。逐行证据：playback_manager.py:135-137 `if is_edtalk_active(self.config): await self.edtalk_client.push_full(voice_tmp_path, ...)`——它按消息的 voice_path 推送，不知道也不检查音频是谁合成的；edtalk_client.py:144-163 push_full 接收**整句 wav 文件**（load_wav_as_16k_pcm→base64→单飞行句背压锁）；api_v2.py 头部契约 `delivery=1: 合成→16kHz mono int16→base64→POST /audio/push_full`（合成完成后**一次性整段**，10MB 上限）；计划 A 节"每个 chunk 同时①累积入缓冲②按 delivery 语义推 EDTalk"若被实施为逐 chunk POST 将违反 realtime_serve 契约（分块流式能力在 D:\AI\EDTalk\realtime_serve\audio_buffer.py:54 push() 存在但需新端点+序列号+乱序缓冲）。REALITY 修正：AUTOlive 现状从未使用 api_v2 的 delivery=1（gpt_sovits_service.py:54-60 启动命令无 -rt，请求不带 delivery）——EDTalk 走的始终是 playback_manager 文件级通道。处置（D1，spawned 自动裁决=recommended）：**R-3 语义改为"inproc 合成落盘 → playback_manager 既有文件级通道推送，零改动零双推；on_chunk 仅做缓冲+进度+计时"**；真流式 push() 通道列入 NOT in scope（如未来需要另行立项）。R-3 验收同步改写，TST-2 的"EDTalk 推送"验收改为"playback_manager 分流回归 + inproc 产物可被 load_wav_as_16k_pcm 消费"。
- `[P2] (confidence: 8/10) frontend/main.py:1154 + main.py（子进程拉起:287,436-438） — inproc 引擎进程归属未写死`。AUTOlive 是双进程：NiceGUI webui（frontend/main.py）拉起核心业务子进程 main.py（synthesis_manager/my_tts/message_queue_thread 所在）。计划通篇"主进程"未指明归属；若实施者因 R-5 胶囊在 frontend/ui/layout.py 而把引擎 import 放进前端进程：显存双份（前端+核心各载一套模型）+ NiceGUI 3.x timer 回调同步阻塞会卡死事件循环（既有探索记忆）。处置（D2，自动裁决）：R-1 验收加"引擎 import 与模型加载只允许发生在 main.py 核心业务进程；前端胶囊经 HTTP 拉取状态，frontend 进程禁止 import GPT_SoVITS"。
- `[P2] (confidence: 9/10) TTS.py:327,623,727,767 + TextPreprocessor.py:72-74 — cwd 敏感副作用比计划预想多一层，且换模路径上 3 处 save_configs 无法用"选对 yaml 路径"规避`。证据：TTS.py:327 `configs_base_path: str = "GPT_SoVITS/configs/"`（**相对路径**）+ :328 `os.makedirs(exist_ok=True)`——TTS_Config 构造即在主进程 cwd 下创建 GPT_SoVITS/configs/ 目录；:623 init_t2s_weights 内 `self.configs.save_configs()`、:727/:767 enable_half_precision(save=True) 无条件写 `GPT_SoVITS/configs/tts_infer.yaml`（相对 cwd）；TextPreprocessor.py:72-74 每次合成写 `./音频输出/tts-out.txt`。主进程 cwd=D:\AI\AUTOlive 时，R-4 换模一次就在 AUTOlive 根下生成 GPT_SoVITS\configs\tts_infer.yaml + 音频输出\tts-out.txt 意外目录与文件。计划 0I HOUR 1 只写了"确保目录存在或重定向"，未落任务验收。处置（D3，自动裁决）：R-1/R-4 验收增补"cwd 副作用落点声明：GPT_SoVITS/configs/ 与 音频输出/ 两个目录加入 .gitignore 并在 R-4 验收中声明为可接受落点；或（推荐）在副本内对 3 处 save_configs 打 no-op 补丁（副本本就承载 api_v2 定制，非纯上游）"。
- `[P2] (confidence: 7/10) TTS.py:366-377 — TTS_Config dict 构造路径不存在时静默回落相对路径默认值`。证据：`if (self.t2s_weights_path in [None, ""]) or (not os.path.exists(...)): self.t2s_weights_path = self.default_configs[version]["t2s_weights_path"]`——默认值是 `GPT_SoVITS/pretrained_models/...` 相对路径，主进程 cwd 下必然加载失败，报错指向不明确的相对路径。处置（D4，自动裁决）：R-1 验收加"dict 构造前对 t2s/vits/bert/cnhuhbert 四路径做 isabs+exists 预检，失败抛 EngineConfigError（含缺失项清单），禁止静默回落"。
- `[P2] (confidence: 7/10) TTS.py:17,44 — 进程内 import 的 sys.path 顶层泛名包冲突审计义务缺失`。证据：模块顶部 `sys.path.append(now_dir)` 两次，且 import 面（module.models、tools.*、sv、text、process_ckpt、AR.*、BigVGAN）要求 gsv 根+GPT_SoVITS 目录均入 sys.path；PYTHONPATH env 对已运行主进程无效，必须 sys.path 注入且顺序敏感。GPT_SoVITS 的 `module`/`tools`/`text`/`sv`/`config` 等泛名包与主进程既有依赖（funasr/gradio/web_server）存在同名覆盖可能。处置（D5，自动裁决）：R-1 验收加"注入顺序断言（GPT_SoVITS 路径 append 在 site-packages 之后）+ 主进程关键依赖 import 冒烟测试"。

Section 1 发现计数：5（P1×1、P2×4）；单点故障/SPOF/回滚评估维持计划结论（backend 键一键切回 + git revert 整批；CUDA 级崩溃为已声明降险）。

### Section 2: Code Quality Review

- 模块结构：gpt_sovits_inproc.py 单一职责封装成立；InferenceGate 语义轻量复刻（threading 版信号量+有界等待）不 import api_v2（import 副作用：parse_args+自动加载+写 yaml）理由经核实成立（api_v2.py:204,226 顶部 import 即执行 argparse）。
- 复用核验：playback_manager/edtalk_client/voice_profiles/gpu_policy/原子写均为既有第一方资产，计划"What already exists"表与实测一致。
- `[P3] (confidence: 7/10) train_voice_pipeline.py:151-156 — T-3 的"确认 _run 列表参数无 shell=True"义务可改为回归断言`。证据：`subprocess.Popen(cmd, cwd=self.gsv_root, env=env, ..., creationflags=CREATE_NO_WINDOW)`——现状已是列表参数、无 shell=True。处置：T-3 验收措辞从"确认无"改为"断言回归（固化参数表单测内）"，避免实施者误解为待修改项。

Section 2 发现计数：1（P3×1）。共享代码评估：无新增抽取机会（引擎与现有 my_tts HTTP 分支是替代关系而非共享关系；rvc_realtime_service 范式参照已足够）。

### Section 3: Test Review

测试框架检测：pytest（tests/ 目录既有，CLAUDE.md 无 Testing 节）；测试计划工件已写盘：`~/.gstack/projects/Eikwang-AUTOlive/admin-main-eng-review-test-plan-20261013-160950.md`（分层策略/mock 边界/GPU 依赖标注/回归清单/Value Card）。

codepath-to-test 图（新代码路径→测试类型）：

```
CODE PATHS                                            USER FLOWS
[+] utils/gpt_sovits_inproc.py（新）                   [+] 训练→出声闭环
  ├── InProcEngine.__init__（sys.path 注入+预检）        ├── [→E2E] 数据就绪→六步→模型对→TTS 可选（TST-2）
  │   ├── [GAP→单测] 路径 isabs+exists 预检（E-5）       ├── [★★ TESTED→单测] 干声跳过逻辑（dry_check）
  │   └── [GAP→单测] 注入顺序断言（E-4）                 └── [→E2E] 切档案失败保旧模型+四要素 notify
  ├── synthesize(text,params,on_chunk)                [+] 播放/直播链
  │   ├── [GAP→单测] 临界区并发不交叉不挂死              ├── [★★ TESTED→集成] EDTalk 分流回归（E-1 修正后为
  │   ├── [GAP→单测] nil/empty→16k 静音正常返回         │   playback_manager 零改动回归+产物兼容断言）
  │   ├── [GAP→单测] run 异常 yield 空→re-raise→        ├── [GAP→单测] 9881 不可达降级不阻塞（既有语义）
  │   │   线程存活+下请求触发重载（TST-1 增补断言）      └── [GAP→集成] backend=api 外部服务未启动四要素文案
  │   └── [GAP→单测] 耗时/首块延迟结构化日志            [+] 边界交互
  └── switch_profile                                   ├── [GAP→单测] 换模中再选档案 loading 置禁
      ├── [GAP→单测] 失败事务回滚保旧模型                ├── [★★ TESTED] 训练双击防重入（现状五态）
      └── [GAP→单测] 成功后 prompt_cache 失效           └── [GAP→单测] config 半损坏迁移容错
[+] train_voice_pipeline.py（改造）
  ├── _st_uvr5→MDX：[GAP→单测] 三阶段跳过/资产缺失拦截；[→E2E] 真实分离
  ├── _st_slice：[GAP→单测] 11 argv 参数表固化
  ├── _st_asr：[GAP→单测] 5 语言路由映射+auto 归属+HF env
  ├── _st_s2：[GAP→单测] 模板覆写固化（batch=4/epoch=10/lr=0.4/save=5/if_save_latest）
  ├── _st_s1：[GAP→单测] bernard 回归+双减半回归+if_dpo 透传；[→E2E] 训练启动成功
  └── _run：[GAP→单测] 无 shell=True 回归断言（E-6）
[+] my_tts/synthesis_manager：[GAP→单测] backend 分流；[GAP→集成] api 兜底可切回
[+] config 迁移：[GAP→集成] 12 键逐键+旧 config 兼容+死链不随行
[+] gpu_policy：[GAP→单测] 扣除常驻口径+set_live_flag_source 接线（main.py 零调用现状→接线后断言）

COVERAGE: 0/24 paths tested (0%) — 全部为新增/改造路径 | QUALITY: 计划层缺口 22（均已在测试计划中给出测试类型与断言要点）
GAPS: 22（单测 17、集成 4、E2E 1）——全部并入 TST-1/TST-2 验收，无 LLM/eval 面
```

- REGRESSION RULE 核验：三处既有缺陷修复（bernard/双减半/slice）各有专门回归测试并已入测试计划回归清单；my_tts 六分支退役对 v2_api_0821 兜底通道的行为保持由"api 兜底可切回"集成测试守护。
- no_claim 标注、E2E skip 条件（无 GPU/缺模型资产→显式 skip）、并发测试（合成×合成、合成×换模）已写入测试计划工件。
- `[P2] (confidence: 8/10) TST-2 验收文本与 E-1 修正冲突 — "EDTalk 推送（流式逐块，非文件级）"在 R-3 语义修正后不再成立`。处置（随 D1）：TST-2 的 EDTalk 项改为"playback_manager 分流回归 + inproc 落盘产物可被 edtalk_client.push_full 消费（load_wav_as_16k_pcm 兼容）"。

Section 3 发现计数：1（P2×1，E-7）；缺口 22 全部转化为测试计划条目并入 TST-1/TST-2。

### Section 4: Performance Review

- 显存结论维持计划：v2ProPlus 常驻主进程与原 api_v2 子进程等量（无净变化）；nvidia-smi 口径下 TTS 占用天然反映在 free 值中，R-6 的"扣除常驻后口径"实现上=现查询口径+set_live_flag_source 接线，无新增计算。
- `[P3] (confidence: 7/10) TextPreprocessor.py:72-74 — 每次合成同步写 ./音频输出/tts-out.txt（相对 cwd）`。规模：每请求一次小文件覆盖写，吞吐影响可忽略；真实代价是主进程根目录出现调试文件（与 E-3 同根）。处置（并入 D3）：E-3 的落点声明覆盖此文件；不单独立项。
- 慢路径 TOP3（首载数十秒/MDX 分钟级/合成 p99 秒级）与缓存结论（prompt_cache/resample LRU 上游自带——外部树 TTS.py:45-71 已带锁保护 LRU，实测确认）维持计划。

Section 4 发现计数：1（P3×1，E-8，并入 E-3 处置）。

### Failure Modes Registry（含 critical gaps）

| CODEPATH | FAILURE MODE | RESCUED? | TEST? | USER SEES? | LOGGED? |
|---|---|---|---|---|---|
| inproc#synthesize | 推理异常→自愈 re-raise | Y | Y(单测：线程存活+重载触发) | 失败提示+胶囊 error | Y |
| inproc#synthesize | CUDA 级崩溃拖死核心进程 | Y(已声明降险) | N(E2E 环境) | 进程崩溃=已知风险+backend=api 兜底指引 | Y |
| inproc#switch | 新模型加载失败 | Y | Y(单测) | 旧模型保持+四要素 notify | Y |
| inproc 构造 | dict 路径缺失静默回落 | Y(E-5 修正后) | Y(单测) | EngineConfigError 明示缺失项 | Y |
| inproc 换模 | save_configs 写出意外目录 | Y(E-3 修正后) | Y(落点断言) | 无感（.gitignore+声明） | Y |
| 播放链 | EDTalk 推送与 inproc 双推 | Y(E-1 修正后=单通道) | Y(集成回归) | 无感 | Y |
| 训练编排 | s1 启动即 FileNotFoundError | Y | Y(bernard 回归) | 预检拦截+明示缺失 | Y |
| 训练编排 | 训练 OOM（同卡与 TTS 争用） | Y | Y(准入单测) | 拒绝文案含占用来源 | Y |
| config 迁移 | 键缺失/半损坏/死链随行 | Y | Y(迁移集成) | 默认注入/废弃标注 | Y |

CRITICAL GAP 计数：**0**（E-1/E-3/E-5 修正后无 silent failure；CUDA 级崩溃为计划 R1 已声明的用户知情降险，有兜底通道与指引文案）。

### What already exists / NOT in scope（复核）

- What already exists 表复核通过：八项既有资产（训练框架/TTS 类/MDX/ASR 双模型/换模机制/gpu_policy/进程内范式/voice_profiles）的复用方式与代码事实一致；补充两项——**playback_manager 文件级 EDTalk 通道**（E-1 修正后成为 R-3 的复用主体）与**外部树 TTS.py 已含 autoplan LRU+锁补丁**（TTS.py:45-71，副本 md5 一致，S-1 重拷时随行，无需重做）。
- NOT in scope 复核通过：RVC/翻唱链、上游功能改造、多角色/断点续训、安装包分发更新四项边界清晰；新增一项——**真流式 push() 推送通道**（E-1 裁决 DEFERRED，分块端点+序列号+背压另行立项）。

### Decision ledger（spawned 自动裁决记录）

| # | 决策点 | 自动裁决（recommended） | 理由 |
|---|---|---|---|
| D1 | R-3 EDTalk 推送通道语义 | 文件级通道复用（playback_manager 零改动）；真流式 DEFERRED | 单通道无双推；/audio/push_full 整段契约已验证；分块端点不存在于现网 |
| D2 | inproc 引擎进程归属 | main.py 核心业务进程；前端禁 import | 显存单份；NiceGUI 事件循环不受推理阻塞 |
| D3 | cwd 副作用处置 | 落点声明+.gitignore+（推荐）副本内 save_configs no-op 补丁 | 3 处回写无法靠选路径规避；副本本就非纯上游 |
| D4 | TTS_Config 路径预检 | 构造前 isabs+exists 断言，失败抛 EngineConfigError | 消灭静默回落这类最难排查的失败模式 |
| D5 | sys.path 冲突审计 | 注入顺序断言+关键依赖 import 冒烟 | 成本≈0 的防回归义务 |

Approval readiness: PASS（D1-D5 均为验收条款增补，无范围变化、无未决事项）。

### Implementation Tasks

由本审查发现综合（与测试计划工件配套；T1/T2 为 P1，并入对应任务验收后不阻塞实施启动）：

- [ ] **E-T1 (P1, human: ~1.5h / CC: ~15min)** — R-3/R-2/TST-2 验收修正 — EDTalk 推送语义改为 playback_manager 文件级通道复用，on_chunk 只做缓冲+进度+计时；真流式列 NOT in scope
  - Surfaced by: Section 1 — E-1（playback_manager.py:129-137 双推风险 + api_v2.py:64 整段契约）
  - Files: 计划任务表 R-3/R-2/TST-2 文本、utils/gpt_sovits_inproc.py
  - Verify: 检索计划无"流式逐块"残留表述；TST-2 集成回归含产物兼容断言
- [ ] **E-T2 (P1, human: ~1h / CC: ~10min)** — cwd 副作用验收落盘 — GPT_SoVITS/configs/ 与 音频输出/ 落点声明+.gitignore；副本内 3 处 save_configs no-op 补丁（推荐）
  - Surfaced by: Section 1 — E-3（TTS.py:327,623,727,767 + TextPreprocessor.py:72-74）
  - Files: utils/gpt_sovits_inproc.py、apps/gpt-sovits/GPT_SoVITS/TTS_infer_pack/TTS.py（副本补丁）、.gitignore
  - Verify: 换模一次后 git status 无意外新增目录/文件
- [ ] **E-T3 (P2, human: ~30min / CC: ~5min)** — 进程归属写死 — R-1 验收加"引擎仅允许在 main.py 核心进程 import"断言
  - Surfaced by: Section 1 — E-2（frontend/main.py:1154 双进程拓扑）
  - Files: 计划 R-1 验收文本、utils/gpt_sovits_inproc.py
  - Verify: frontend 进程进程内探针确认无 GPT_SoVITS 模块
- [ ] **E-T4 (P2, human: ~40min / CC: ~8min)** — 构造预检+回归断言 — 四路径 isabs+exists 预检（EngineConfigError）；_run 无 shell=True 改回归断言
  - Surfaced by: Section 1 E-5（TTS.py:366-377）+ Section 2 E-6（train_voice_pipeline.py:151-156）
  - Files: utils/gpt_sovits_inproc.py、tests/
  - Verify: 单测传入坏路径断言 EngineConfigError 且不回落
- [ ] **E-T5 (P3, human: ~30min / CC: ~5min)** — sys.path 注入顺序断言+关键依赖 import 冒烟
  - Surfaced by: Section 1 — E-4（TTS.py:17,44 泛名包入 path）
  - Files: utils/gpt_sovits_inproc.py、tests/
  - Verify: 单测断言 site-packages 优先级；gradio/funasr import 冒烟通过

### Completion Summary

- Step 0: Scope Challenge — scope accepted as-is（E-T1~E-T5 为验收条款增补，无范围变化）
- Reality rows: 7/7 applicable rows emitted（premise 1 条修正：EDTalk 通道现状），unverified premises = 0
- Architecture Review: 5 issues found（P1×1 + P2×4）
- Code Quality Review: 1 issue found（P3×1）
- Test Review: diagram produced, 22 gaps identified（全部并入 TST-1/TST-2 与测试计划工件）
- Performance Review: 1 issue found（P3×1，并入 E-T2 处置）
- NOT in scope: written（新增真流式推送通道 DEFERRED）
- What already exists: written（复核通过+补 2 项）
- TODOS.md updates: 0（无需新增）
- Failure modes: 0 critical gaps flagged
- Unresolved decisions: 0（D1-D5 spawned 自动裁决=recommended，已记录）
- Outside voice: N/A（本批次 codex 不可用，同 CEO/DX 根因；单模型结论，全部发现带 file:line 收据）
- Parallelization: S-1 硬前置 → 后续 Lane A（R-1→R-2→R-3/R-4/R-5）与 Lane B（T-1~T-4→F-1）可在 S-1 完成后并行，C-1 在 R-2 后收口；2 lanes, 2 parallel / 1 sequential
- 写盘工件：测试计划 `~/.gstack/projects/Eikwang-AUTOlive/admin-main-eng-review-test-plan-20261013-160950.md`；任务 `tasks-eng-review-20261013-160950.jsonl`（5 条）

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` (via /autoplan) | Scope & strategy | 1 | CLEAR | SELECTIVE_EXPANSION；Spec Loop 2 轮（R1: 3 P0+8 P1 全修复，R2 PASS 五维全 9）；Sections 1-11 发现 6 项（2 验证义务+3 增补+1 修订）全部并入任务表；0 critical gap |
| Outside Review | codex（provider 不可用） | Independent 2nd opinion | 1 | unavailable | custom 端点未配置 frontier 模型（修复：gstack-config set model_frontier_openai）；native fallback 不可用（TaskOutput 缺失）；no completed external review |
| Eng Review | `/plan-eng-review` (via /autoplan) | Architecture & tests (required) | 1 | issues_open | 8 issues（P1×1：R-3 双推风险+整段契约；P2×5：进程归属/cwd 回写/路径预检/sys.path 审计/TST-2 对齐；P3×2），0 critical gaps；5 项验收条款增补 E-T1~E-T5 并入任务；scope accepted as-is；测试计划已写盘 |
| Design Review | `/plan-design-review` (via /autoplan) | UI/UX gaps | 1 | clean | 9 发现（D-1~D-9：ASR 语言分组错位/胶囊四态映射/参数树裁剪/legacy 双轨/文案漂移/宽度三档等）全处置为验收增补 DS-T1~DS-T4；七维 6→8（最低分口径）；报告在 Eng Review 节前 |
| DX Review | `/plan-devex-review` (via /autoplan) | Developer experience gaps | 1 | issues_open | 11 发现（DX-10~DX-20）全处置为验收增补；评分 7.6→8.6；TTHW 旅程 B ∞→≤2h；报告在 Eng Review 节前 |

OUTSIDE COVERAGE: provider=codex, phase=plan-review(ceo), outside_status=unavailable（custom endpoint 无 frontier 模型配置 + TaskOutput 缺失），no completed external review; 既有批次（2026-09-24/25）同为 400 根因。

VERDICT: CEO + ENG + DESIGN CLEARED — Eng 发现 8 项全部转化为验收条款增补（E-T1~E-T5）与测试计划工件；Design 发现 9 项全部转化为验收增补（DS-T1~DS-T4），七维最低分 8；0 critical gap，可进入实施。

NO UNRESOLVED DECISIONS
