> **📦 已归档历史档案**（2026-10-21 批次标注）：本文件为整合期计划/纪要快照，记录当时决策与实施过程，不随现行代码演进；现行事实以 docs/ 与代码为准。

<!-- /autoplan restore point: "C:\\Users\\admin\\.gstack\\projects\\Eikwang-AUTOlive\\main-autoplan-restore-20260924-050105.md" -->
## Implementation plan
<!-- /autoplan: ACTIVE_PLAN — EDTalk 功能集成计划（2026-09-24） -->

# AUTOlive × EDTalk 功能集成计划

> 日期：2026-09-24 ｜ 发起：用户 /autoplan 指令
> 前置文档：`specs/integration/EDTalk实时推理服务对接纪要.md`（T1 spike，2026-09-03）、
> `specs/integration/统一环境依赖整合计划.md`（2026-09-02，runtime312 基线）、
> `D:\AI\EDTalk\realtime_serve\RUNBOOK.md`、`D:\AI\EDTalk\realtime_serve\EMBEDDING.md`（契约 v0 冻结）、
> EDTalk 记忆：微调配方（Run C）、Audio2Mouth 优化定稿（2026-09-19）

## 1. 用户原始需求（源输入）

将 EDTalk 四个模块集成到 AUTOlive：实时推理、模型训练、数据处理、AI 对话。
相关代码已开发完成并测试通过；**只需复制代码到 AUTOlive 目录，打通接口路径，使各功能模块参数连通；所需模型直接复制**。

- **模块一（实时推理）**：`D:\AI\EDTalk\realtime_serve` 集成到"虚拟身体"功能。导航栏"虚拟身体"→"数字人"，二级菜单"虚拟身体"→"画面设置"。有开关，开启后在运行系统时启动实时推理服务器。参数设置替代原先的 metahuman_stream（服务器参数见 RUNBOOK.md）。
- **模块二（模型训练）**：`train_fine_tune.py`（底模微调）+ `train/train_audio2mouth.py`（口型微调）+ `data_preprocess/data_preprocess_for_train`（数据预处理）整合到"数字人"二级功能下的"训练主播"。无开关、独立功能、不依赖系统运行。用户输入原始视频文件夹路径 → 点击开始训练 → 自动完成全链路训练。
- **模块三（AI 对话 fastrag）**：Rust 编译的 exe（RAG 服务）整合到"AI对话-大语言模型"。有开关，关闭后不随系统启动。只整合 build-index（构建知识库，用户手动调用）与 server（API 服务，由开关决定启动）。

## 2. 现状核实（实地核查 2026-09-24）

| 事实 | 位置 | 影响 |
|---|---|---|
| 导航分组由 `config.webui.function_groups` 驱动 | config.json | 改名"虚拟身体"→"数字人"、新增二级项 |
| 二级开关由 `frontend/config/enable_paths.py` ENABLE_PATHS 映射 | enable_paths.py | 新页开关语义按需接入 |
| 服务启动统一走 `start_programs()`（coordination_program[] + main.py） | frontend/main.py:192-234 | 实时推理/fastrag 的启动挂接点 |
| ⚠ `cmd = [executable, app_path]` 不透传 parameters[1:] | frontend/main.py:213 | 需小改为 `[executable] + parameters`（对接纪要 A2） |
| metahuman_stream 模式 = 文本 echo（`/human`），不本地 TTS | utils/audio/synthesis_manager.py:31,94 | EDTalk 模式音频链路完全不同 |
| EDTalk 端自带音频播放（sounddevice，音画帧锁定） | realtime_serve/audio_player.py | edtalk 模式下 AUTOlive 不得本地播放（防双声） |
| `/audio/push_full` 是唯一有效整句音频通道（`/audio/push` 已死） | EMBEDDING.md v0.4 | 客户端只对接 push_full |
| 嵌入方须自行限流（单飞行句 + 队列 ≤2） | EMBEDDING.md v0.4 | edtalk_client 设计约束 |
| TTS 输出采样率 = 16kHz（用户已确认） | 对接纪要 §5 | 无需转码 |
| chat_type 目前仅 custom_llm，模板化 HTTP（url/body/data_analysis 全可配） | utils/gpt_model/custom_llm.py | fastrag /query 可直接用 custom_llm 模板表达 |
| config 无自动默认值合并（_deep_update 仅更新路径） | frontend/config/settings.py | 新配置节需默认值迁移 |
| 统一解释器 = `D:\AI\EDTalk\runtime312\python.exe`（不可搬迁，不进 git） | 统一环境计划 §6 | 复制代码后的运行时解释器仍指向 runtime312 |
| `D:\AI\start\start_edtalk.bat` 已存在（旧整合产物） | D:\AI\start\ | 与本计划启动方式并存，需说明关系 |
| AUTOlive webui 8086；EDTalk 8000；fastrag 11420 | 各 README/config | 端口无冲突 |

## 3. 代码复制清单（EDTalk → AUTOlive）

运行时目录布局：代码与模型复制进 `D:\AI\AUTOlive\edtalk\`（避免与 AUTOlive 既有 utils/、config/ 等命名冲突）；**解释器与依赖不复制**——统一使用 `D:\AI\EDTalk\runtime312\python.exe`（既定基线，环境不可搬迁）。

| 源（D:\AI\EDTalk\） | 目标（D:\AI\AUTOlive\edtalk\） | 内容 | git 处置 |
|---|---|---|---|
| realtime_serve/ | edtalk/realtime_serve/ | 实时推理服务代码 + weights/（gaze 权重） | 代码入库；权重 .gitignore |
| train_fine_tune.py + fine_tune/ | edtalk/train_fine_tune.py + edtalk/fine_tune/ | 底模微调 | 入库 |
| train/train_audio2mouth.py + train/trainer_audio2mouth.py + train/networks*/ + train/loss/ + train/util/ | edtalk/train/（按原相对结构） | 口型微调 | 入库 |
| data_preprocess/ | edtalk/data_preprocess/ | 11 步预处理链 | 入库 |
| datasets/ + networks/ + hparams.py + audio.py | edtalk/同名 | 训练/推理共用模块 | 入库 |
| face_detection/ + face_sr/ + pytorch_gaze_redirection/ | edtalk/同名 | 依赖库 | 入库 |
| ckpts/（底模 ckpt）| edtalk/ckpts/ | EDTalk_lip_pose.pt、Audio2Lip.pt 等 | .gitignore（大文件直接复制） |
| tools/（eval_finetune.py、mouth_metrics.py 等） | edtalk/tools/ | 评估工具 | 入库 |
| fastrag/ 整目录 | edtalk/fastrag/ | server.exe、build-index.exe、Config.toml、chekpoint/（模型）、knowledgebase/、kbindex/ | exe+模型+索引 .gitignore；Config.toml/knowledgebase 入库 |

复制后须冒烟：runtime312 解释器在 AUTOlive\edtalk 目录下 `python -m realtime_serve.main --no-unitycapture`、`train_fine_tune.py --help`、`fastrag\server.exe` 均可启动（相对导入/路径假设逐项验证——EDTalk 代码内部存在对仓库根的路径假设，复制后第一次运行就是验证）。

复制后另须**迷你训练冒烟**（通过后才放开一键训练按钮）：对小样本目录（1-2 个短视频）跑前 2 步预处理 + 底模微调 50 iter，验证 lmdb 写入、CUDA 链路与素材目录假设——`--help`/启动级冒烟不足以验证 15 小时链路的正确性。

复制动作附加义务：① 生成 `edtalk/MANIFEST.md` 记录各目录来源路径与复制日期；② RunC resume 产物（`ckpt_models/wy_runC/checkpoint/035000.pt`）一并复制进 edtalk/ 并在编排参数中固定相对路径——**宣告 `D:\AI\AUTOlive\edtalk` 为唯一运行副本**（EDTalk 目录降为开发存档，验证期后归档 start_edtalk.bat，防双份代码漂移）；③ 同步 `pip freeze` + Python 版本快照入 docs（防 EDTalk 侧环境升级静默断裂）。**复制动作脚本化**：以上复制+MANIFEST+freeze+冒烟全部封装为一键脚本（`scripts/copy_edtalk.py`，运营者双击/单命令执行，逐项打印进度与校验结果——不要求运营者手敲 robocopy）

## 4. 模块一：数字人（实时推理集成）

### 4.1 导航与页面

1. **导航改名**（config.json `webui.function_groups[5]`）：分组"虚拟身体"→"数字人"；二级"虚拟身体"→"画面设置"（tab_key 保持 `visual_body`，避免代码级连锁改名；前端显示名与 function_groups 文案分离）。
2. **新增二级功能"训练主播"**（tab_key: `train_streamer`），挂在"数字人"分组下（见模块二）。
3. **画面设置页重做**（`frontend/ui/tabs/visual_body.py`）：
   - **驱动类型**（沿用 `config.visual_body` 语义扩展）：`metahuman_stream` / `edtalk_realtime` 二选一（互斥，沿用既有警告文案模式）。
   - **EDTalk 实时推理配置区**（新配置节 `config.edtalk_realtime`，存 config.json）：
     - `enable`（bool，默认 false）——开关：开启后"运行系统"时启动实时推理服务器；**运行中切换只影响下次"运行系统"**
     - `api_ip_port`（默认 `http://127.0.0.1:8000`）
     - `character_dir`（角色素材目录，默认 `wy`）
     - `target_fps`（默认 25）、`buffer_frames`（默认 16）、`preroll_frames`（默认 8）
     - `face_repair`（off/on/adaptive，默认 adaptive）、`gaze_correction`（off/on/adaptive，默认 adaptive）、`gaze_convergence`（默认 0.05）
     - `segments`（默认 auto / 指定列表）；**指定列表控件 = 多行文本（一行一个段名）+ 格式校验 + "从 /status 获取可用段"辅助按钮**（段名大小写敏感，UI 给合法示例）
     - `host`（默认 127.0.0.1）、`api_token`（默认空）
     - `port`（默认 8000）——**服务端启动参数、客户端 api_ip_port、端口幂等探测三处统一引用此键**（改端口一处生效，杜绝客户端/服务端脱钩）
     - `interpreter_path`（默认 `D:\AI\EDTalk\runtime312\python.exe`）——启动与训练编排统一读取；**拉起/训练前校验存在性，缺失时报错附修复动作（指向此配置键）**（消除跨目录硬编码死点）
   - **运行控制区**（对接纪要 A1，直调 HTTP 不落 config）：页面三区顺序 = 驱动类型 → EDTalk 配置区 → 运行控制区（**默认折叠，服务运行时自动展开**）；独立卡片呈现，卡片标题标注"运行时控制（立即生效，不保存）"与配置区视觉区隔；gaze 热切（409 置灰）、repair 热切、段调度/切换、GET /status 状态回显（current_segment/production_fps/underruns/preroll_state）——**5 秒轮询 + 手动刷新按钮，连续 3 次失败转离线态**；EDTalk 未启动时状态区显示"服务未运行"空态而非空白。**按钮状态矩阵（驱动类型 × 服务状态 2×4）逐格定义**：未启动=全禁用+"服务未运行"；启动中=禁用+"启动中"；运行中=可用；推送异常=可用+警告色。**驱动类型 × enable 四组合提示**：edtalk+enable=false="服务未启用，运行系统将无声"；metahuman+enable=true="开关已开但 metahuman 驱动不使用该服务"；其余两组合正常。
   - metahuman_stream 参数区保留（类型/API 地址），选 edtalk_realtime 时**收起为折叠面板**（标题可见+提示"当前使用 EDTalk 实时推理"，不删除配置）。
4. 新配置节默认值：Settings init 时 ensure-default（加载后补写缺失键并落盘 config.json），防 get 返回 None。

### 4.2 启动接线

1. `start_programs()`（frontend/main.py）：
   - **parameters 透传修复**：`cmd = [executable] + parameters`（对接纪要 A2，向后兼容）。
   - 若 `visual_body == "edtalk_realtime"` 且 `edtalk_realtime.enable`：拉起 `runtime312\python.exe -m realtime_serve.main --character-dir <…> --target-fps <…> …`（cwd=`D:\AI\AUTOlive\edtalk`，参数从 config.edtalk_realtime 映射，按 RUNBOOK 参数表）。**拉起前探测 8000 端口：已占用则跳过拉起并日志告警**（防 start_edtalk.bat 手动双启动的端口冲突）；**同时检测训练进行中标志：训练中运行系统同样弹警告**（16GB 卡双向防护）
   - 若 `fastrag.enable`：拉起 `edtalk\fastrag\server.exe`（cwd=fastrag 目录，见模块三）。
   - 拉起记录进 `my_subprocesses`，随 stop_programs() 一并 taskkill（复用既有停止链路）。
2. 与 `D:\AI\start\start_edtalk.bat` 的关系：webui 启动路径成为 EDTalk 服务的主启动方式；start_edtalk.bat 保留为手动/调试入口，不冲突。

### 4.3 音频链路（核心数据面，对接纪要 A3 修订版）

1. 新建 `utils/edtalk_realtime/edtalk_client.py`（照 AUDIO_PLAYER 模式：requests/aiohttp + api_ip_port 配置 + try/except + logger）：
   - `push_full(pcm_path)`：读 16kHz int16 wav → base64 → POST /audio/push_full
   - `switch_segment(segment, at)`、`set_gaze_params(...)`、`set_repair_params(...)`、`get_status()`
   - **限流**：单飞行句 + 等待队列上限 2（EMBEDDING v0.4 过载语义）。**完成判定启发式（native E1 实证：push_full 返回 {ok,duration_s,processing:"async"} 无逐句完成信号；过早推送会被 render_loop 的 _flush_buffer 截断上一句剩余音频）**：最短飞行时间 = `duration_s × (1+0.2)` + 观察 /status buffer_depth 回落，双条件齐才放行下一句；启发式不精确时的取舍 = 宁可延迟不截断（断句不可接受）；**实施首日双端实测一并验证截断边界**（并入 R2 义务）。**背压语义（native A2）**：跳过本地播放后节拍由推送链负责——链路各队列（message_queue→synthesis→playback→edtalk_client）全部有界，满载时**阻塞式背压**（上游等下游，不丢弃台词），溢出只发生在 edtalk_client 等待队列且受背压保护不触发
   - 未启动容错：连接失败降级为跳过推送 + logger.error，不阻塞直播主流程；**推送健康度运营可见**：画面设置页状态区显示"EDTalk 推送：正常 / 未启动 / 最近 N 次失败"；会话内首次推送失败弹一次 toast"数字人服务未连接，直播将无声"，后续失败累计计数不刷屏；**EDTalk 错误体三段式透传**：响应体 {problem, cause, fix}（EMBEDDING 契约）原样写入日志与状态区 tooltip——运营者所见即服务端给出的修法
2. `synthesis_manager.py` 分流扩展：`visual_body == "edtalk_realtime"` **且 `edtalk_realtime.enable == true`** 时走**本地 TTS 合成**（走既有 audio_synthesis 链路，不进 metahuman echo 分支）；选 edtalk_realtime 但 enable=false 时 UI 常驻警告"数字人服务未启用，运行系统将无声"（音频分流与启动条件对齐，杜绝静音直播组合）。
3. `playback_manager.py` 两处 play 旁（147、245）：`visual_body == "edtalk_realtime"` 且 `edtalk_realtime.enable == true` 时**跳过本地播放**（EDTalk 端 sounddevice 帧锁定播放，本地播放会双声）——TTS 落盘后仅推送 `/audio/push_full`。
   - ⚠ 修订说明：对接纪要 A3 写"播放与推送并行"，但 2026-09-03 EDTalk 音频重构后音频播放由 EDTalk 帧锁定负责（RUNBOOK「消费端帧锁定」节 + audio_player.py 实证），本计划按"仅推送不播放"执行。实施首日以一次实测双端验证收口。
4. 反应段联动（弹幕→切段，A4，CEO 已接受）：实现期在回复分发点挂 `switch_segment(at=segment_end)`；配置载体 = `config.edtalk_realtime.reaction_segment`（bool，默认 true，UI 可关；**UI 标签用布尔语义文案"弹幕触发切段：开/关"**，防误读为段名）；降级容错同上。

## 5. 模块二：训练主播（一键全链路训练）

### 5.1 页面（新 `frontend/ui/tabs/train_streamer.py`，无开关、不进 ENABLE_PATHS）

- 原始视频文件夹路径输入（必填，含角色说话视频）+ 角色名（默认取文件夹名）；**素材要求提示**（tooltip/折叠说明）：视频格式与放法（original_videos 子目录）、建议时长与数量参照 EDTalk 数据集处理流程（wy 实例 = 529 视频/63055 clips；更少素材可训但口型质量预期下降）
- 开始训练按钮（二次确认：训练耗时数小时、占用 GPU）+ 停止按钮；训练进行中开始按钮置灰（防重入）。停止按钮语义：终止当前子进程 → 编排线程按失败即停收尾 → 开始按钮复位；日志标记"用户停止"（区分步骤失败）。**停止需二次确认**，文案暴露代价："终止将丢弃当前步骤进度（本期无断点续训），已耗时 X 小时，确定停止？"
- 进度日志区上方设**常驻步骤条**（14 步列表：当前步高亮、已完成打勾、每步耗时——与 §5.2 步骤建模一一对应，回答"到哪了/还剩多久"）；日志区仅作细节流（子进程 stdout 实时回显）。**空态**：未开始训练时显示引导文案"输入路径后点击开始训练"+ 主操作提示（非空白终端）；**配色走设计令牌**（新令牌 --bg-log/--text-log，禁硬编码色值）；**窄屏**：768px 以下日志区 overflow-x auto 防等宽文本撑破布局
- 高级参数折叠区（默认收起，预填记忆配方默认值）：底模微调 iter（默认 30000，经济截断点）、exp_name、audio2mouth 配方参数（lip_dim_weight 5.0 / mouth_vgg_weight 0.2 / smooth 0.2 / epoch 2 等，来自 Audio2Mouth 定稿配方）
- 显存提示：开始训练前同时检测**实时推理服务器（8000 端口）与直播系统自身运行状态（running_flag）**，任一在跑即警告"训练与直播/推理不可并存，建议错开"（16GB 卡，batch4 微调 ~12GB）

### 5.2 编排（AUTOlive 侧新 `utils/train_streamer/pipeline.py`）

逐步 subprocess 调用（解释器 = runtime312，cwd = `D:\AI\AUTOlive\edtalk`），任一步失败即停并回显错误：

```
1. data_preprocess/data_preprocess_for_train/run_preprocess.py（**EDTalk 自带编排器，PIPELINE_STEPS=11 步为唯一步骤事实来源**：crop_video_HDTF → split_video/audio → prepare_lmdb → get_mel → extract_lmdk → extract_bbox → extract_lip_pose_features → prepare_feature_lmdb → crop_head_video → **prepare_gaze_data**——pipeline 直接调用它，继承其幂等/失败即停/步骤选择（--steps），**不重建步骤清单**；native E2 实测修正：计划原清单漏 prepare_gaze_data 步）
12. train_fine_tune.py --datapath <角色> --exp_name <角色> --only_fine_tune_dec --iter 30000
13. tools/eval_finetune.py --cand-ckpt <步骤12产物>（**解析 eval_results.jsonl 机器可读输出**——eval_finetune.py:330 实证；评估门槛：姿势MAE≤11.1px、贴回≤3.6px、背景SSIM降幅≤0.02、开合比0.8-1.25）；**评估不过门槛 = 默认停止并回显全部指标**（不自动进入口型微调；UI 提供"仍要继续"显式确认——门槛标定于研究基准数据集，用户对真实素材可用性的判断优先于阈值；评估前移至训练中途的方案延期 TODOS）
14. train/train_audio2mouth.py（定稿配方参数，resume RunC 产物）

步骤条相应为 4 大阶段（预处理编排 / 底模微调 / 评估 / 口型微调，预处理阶段内解析 run_preprocess 输出显示 11 子步进度）；copy_edtalk.py 冒烟含 **run_preprocess.py 全部 11 个步骤脚本存在性校验**。
```

- 数据目录约定：预处理工作目录 = 用户输入路径（original_videos 子目录），与 EDTalk 原流程一致（数据集处理流程.md 11 步）。
- 训练产物路径回显（ckpt_models/<exp>/checkpoint/xxx.pt）。**训练完成后提供"应用到数字人"一键接线**：确认对话框列出源产物路径、目标权重路径、文件体积，确认后复制 + 提示重启 realtime_serve 生效（打通"训练→开播"最后一公里；此为 taste 备注项留最终批准门确认）。**映射待验证声明（native A1）**：realtime_serve 加载 `<character_dir>/checkpoint/new.pt` 与 `audio2lip.pt`（sequence_frame_pack.py 实证），训练产物 `ckpt_models/<exp>/checkpoint/<iter>.pt` 为内嵌 args 的完整 ckpt——**键结构对齐需实施首日一次性验证冒烟**（预埋验证任务：产物 ckpt 键 → new.pt 键映射表），验证通过前"应用到数字人"按钮保持置灰并提示"映射验证中"；应用对话框校验"仅允许自产 ckpt"（native S2，pickle 反序列画面）。
- **步骤级重跑**：编排按步骤建模，支持"从第 N 步开始"起始参数（UI 高级选项 + 失败断点提示）——webui 重启/步骤失败后无需手敲 11 步命令行。**每完成一步即写进度文件** `edtalk/train_progress/<角色>.json`（已完成步骤、当前步骤、时间戳）；页面加载时读取并显示"上次进行到第 X 步（中断于第 Y 步）"，"从第 N 步开始"预填为 Y。
- 长任务模型：webui 进程内后台线程 + ui.timer 轮询日志（照 schedule/coordination 既有模式）。webui 重启边界如实声明：**当前正在执行的步骤（子进程）独立存活跑完，但编排线程随 webui 死亡——后续步骤中断，需人工从断点步骤手动重跑**；页面重开可重新附着日志（仅当前步骤）。

## 6. 模块三：fastrag（知识库 RAG）

### 6.1 页面（`frontend/ui/tabs/llm.py` 新增"fastrag 知识库"卡片）

- `enable` 开关（新配置节 `config.fastrag`，默认 false）——关闭后不随系统启动 server.exe
- 端口显示（默认 11420，可改，写 `edtalk/fastrag/Config.toml`——运行时配置文件，改后重启 server 生效）
- 知识库说明（放 .md/.txt 到 `edtalk/fastrag/knowledgebase/`，Markdown 标题分块）
- **构建知识库按钮** → 构建前校验 knowledgebase/ 目录非空（空则提示"请先放入 .md/.txt 文件"）；subprocess 调 `build-index.exe`（cwd=fastrag 目录），完成后提示附"重启 server"按钮（复用 my_subprocesses 停止+拉起链路，不走停系统绕路）
- 服务状态探测（GET /health，回显 chunks/rerank 状态）；**离线态显示"服务未运行"灰字 + 引导文案**（开启开关并运行系统后自动恢复）
- "应用 fastrag 请求模板"按钮：应用前先实测一次 /query 校验响应字段（确认 answer 键存在再写入模板）；二次确认并备份原 custom_llm 配置到日志（防静默覆盖用户云端 LLM 配置）；然后一键把 custom_llm 配置填为 `url=http://127.0.0.1:<配置端口>/query`（**引用当前配置端口变量，config.json 为 UI 真源、应用时同步写回 Config.toml**——模板永不硬编码端口）、`body={"question":"{{prompt}}"}`、`data_analysis=resp["answer"]`（chat_type 仍为 custom_llm，零主程序 LLM 代码改动）。**原配置落盘备份** `config.custom_llm.bak.json`（非仅日志），确认对话框展示将被覆盖的 url/body/data_analysis，应用后卡片提供"还原上次配置"按钮

### 6.2 启动接线

- `start_programs()`：`fastrag.enable == true` → `subprocess.Popen([edtalk\fastrag\server.exe], cwd=fastrag目录)`，进 my_subprocesses 随停。**安全边界（native S1 实证：server.rs:199 硬编码 0.0.0.0 且无鉴权）**：copy_edtalk.py 附 `netsh advfirewall` 入站阻断 11420 规则（本机回环不走防火墙，正常使用不受影响，局域网不可达）；edtalk/README.md 登记此暴露为已接受风险；**新拉起分支显式 shell=False + list 传参**（native H1：main.py:216 存量 shell=True 不得沿用——含空格/中文路径会被 cmd.exe 打碎）
- 注意：fastrag 开关是**服务进程级**开关（不进 ENABLE_PATHS——那是"功能逻辑跳过"语义）。

## 7. NOT in scope（本期不做）

| 项 | 理由 |
|---|---|
| 产物→推理服务自动接线（训练完自动替换角色权重） | 无现成代码，属新开发；用户未要求 |
| EDTalk 代码重构/深度改造 | 用户明示"代码已开发完成测试通过，只复制打通" |
| fastrag 语义改造（question 拼接策略、prompt 工程适配） | fastrag 独立契约；Config.toml 可调 |
| 多角色管理/训练队列 | 单角色链路先落地 |
| 子模块收编/路径迁移专项 | 统一环境计划既定延期项 |
| 训练 webui 重启后进度恢复 | 长任务模型 v1：当前步骤独立存活跑完，后续步骤人工重跑（见 §5.2 如实声明） |
| 训练断点续训（中断后从最近 checkpoint 自动恢复） | 步骤级失败即停 + 日志留存已覆盖最小需求；resume 能力登记 TODOS |

## 8. 风险与待验证点

| # | 风险 | 缓解 |
|---|---|---|
| R1 | EDTalk 代码复制后路径假设断裂（仓库根相对路径、test_data、wy 素材目录） | 复制清单含素材/权重目录；复制后逐入口冒烟（§3） |
| R2 | 双声风险（AUTOlive 本地播放 + EDTalk 播放） | §4.3 修订为"仅推送"；实施首日双端实测收口 |
| R3 | 训练与实时推理 GPU 显存冲突（16GB） | UI 提示 + 开始训练前探测 8000 端口服务状态 |
| R4 | 长训练（累计 ~15h）中途失败无断点续训 | 步骤级失败即停 + 日志留存；断点续训列入 NOT in scope 备注 |
| R5 | `cmd = [executable] + parameters` 修改对既有 coordination_program 条目的兼容 | 既有条目 parameters 仅 app_path，行为不变；回归启动路径 |
| R6 | fastrag exe 与 Config.toml 相对路径假设（必须在 fastrag 目录下运行） | Popen cwd 固定 fastrag 目录 |
| R7 | push_full 过载（连续弹幕高频触发 TTS） | edtalk_client 单飞行句限流（EMBEDDING v0.4） |
| R8 | 模型大文件复制占用磁盘/时间（ckpts+weights+fastrag 模型） | 复制清单一次性执行；.gitignore 防大文件入库 |

## 9. 验收标准

1. 导航显示"数字人"分组，二级含"画面设置"与"训练主播"。
2. 画面设置页：edtalk_realtime 配置齐备（开关+参数），选 edtalk_realtime 时 metahuman 提示互斥。
3. 开启开关 → 运行系统 → realtime_serve 进程按配置参数拉起，/status 可查询，控制区热切生效。
4. 直播链路：弹幕 → LLM → TTS → 音频推送 EDTalk → 虚拟摄像头口型同步、无本地双声；TTS 完成→EDTalk 出帧延迟 ≤500ms（首日实测一并收口）。
5. 训练主播：输入路径 → 一键完成 11 步预处理 + 底模微调 + 评估 + 口型微调，全程日志可见，产物路径回显。
6. fastrag：开关控制 server.exe 启停；构建知识库按钮生成 kbindex；custom_llm 指向 fastrag 后 /query 正常回答。
7. 既有功能回归：coordination_program 启动、metahuman_stream 模式、custom_llm 原配置均不受影响。
8. 反应段联动：直播回复触发后 EDTalk 收到 segment/switch（at=segment_end）；EDTalk 未启动时该触发降级为日志记录且直播流程不受影响。
9. fastrag 模板：点击"应用 fastrag 请求模板"后 custom_llm 配置变为 /query 模板且 /query 联通可答。
10. 训练主播：开始训练前检测到 EDTalk 服务在运行（8000 端口探测）时给出显存冲突警告。

<!-- autoplan-accepted:ceo -->
- 用户原始需求三点全部保持：①实时推理集成"虚拟身体"→"数字人/画面设置"+开关随系统启动+参数替代 metahuman_stream（按 RUNBOOK 参数表）；②训练主播独立页无开关，输入路径一键完成 11 步预处理+底模微调+评估+口型微调；③fastrag 整合大语言模型页，server 由开关控制启动，build-index 由构建知识库按钮手动触发。
- 代码与模型复制进 D:\AI\AUTOlive\edtalk\（隔离布局）；解释器统一用 D:\AI\EDTalk\runtime312\python.exe（既定基线，不复制环境）。
- 音频链路按"仅推送不本地播放"执行（EDTalk 帧锁定播放）；实施首日双端实测收口（R2 义务）。
- 复制完成后逐入口冒烟：realtime_serve --no-unitycapture、train_fine_tune.py --help、fastrag server.exe（R1 义务）。
- edtalk_client 限流：单飞行句 + 队列 ≤2（EMBEDDING v0.4 契约义务）。
- parameters 透传修复 cmd = [executable] + parameters，且回归既有 coordination_program 条目（R5 义务）。
- 训练前探测 EDTalk 服务状态并提示显存冲突（0G 接受扩展 #3）。
- 反应段联动：回复分发点挂 switch_segment(at=segment_end)，连接失败降级跳过；配置载体 = config.edtalk_realtime.reaction_segment（bool，默认 true，UI 可关）（0G 接受扩展 #1）。
- fastrag 请求模板一键填充 custom_llm 配置（0G 接受扩展 #2）。
- 新配置节 edtalk_realtime / fastrag 带默认值迁移（init ensure-default，§4.1.4 定案）。
- 大文件（ckpts/weights/exe/chekpoint/kbindex）.gitignore 防入库；代码与 Config.toml/knowledgebase 入库。
- 延期项登记 TODOS.md：评估前移至训练中途、多角色管理/训练队列、进程级断点续训（步骤级重跑已入本期）。
- native CEO 声部修正义务（2026-09-24 裁决）：迷你训练冒烟后放开一键按钮；edtalk/ 唯一运行副本+MANIFEST+RunC 产物复制+pip freeze 快照；realtime_serve 拉起前 8000 端口幂等探测；训练前双检测（8000 端口+running_flag）；评估不过默认停+"仍要继续"显式确认；fastrag 模板应用前 /query 实测字段+二次确认+原配置备份；步骤级"从第 N 步开始"重跑；训练完成"应用到数字人"一键接线（taste 留门）；验收 #4 延迟 ≤500ms。
- Sections 1-11 行内义务（CEO 深审）：pipeline 子进程统一 encoding='utf-8', errors='replace'（禁 shell=True，参数列表传参）；训练日志落盘 logs/train_streamer/<日期>.log；fastrag 构建按钮加 spinner 反馈；ensure-default 补 1 个 config 合并单测；edtalk_client 推送日志含 segment 与耗时。
<!-- /autoplan-accepted:ceo -->

<!-- autoplan-accepted:design -->
- 设计义务（7 passes 裁决，2026-09-24）：①运行控制区独立卡片+"立即生效"标注+5s 轮询+手动刷新+服务未运行空态；②训练日志区空态引导文案、配色新令牌 --bg-log/--text-log、窄屏 overflow-x auto；③metahuman_stream 区收起为折叠面板；④训练完成 ui.notify positive + 产物路径高亮（Pass 3 成功反馈）；⑤构建知识库空态（knowledgebase 为空时提示先放文件）；⑥评估"仍要继续"确认框用 Quasar dialog（destructive 语义样式）；⑦全部新 UI 遵循 design_tokens.py 令牌（禁硬编码色值）与 FormField/三列网格样板；⑧mockup 未生成（designer API key 缺失），实施后跑 /design-review 做视觉 QA；⑨native design 声部修正（15 项全采纳，2026-09-24）：推送健康度可见面+首次失败 toast、停止二次确认（代价文案）、常驻步骤条+每步耗时、进度文件 train_progress/<角色>.json、控制区 2×4 按钮状态矩阵+四组合提示+默认折叠、fastrag 端口真源=config.json 同步写回 Config.toml+模板引用变量、模板备份落盘 config.custom_llm.bak.json+还原按钮、构建前校验+完成后重启 server 按钮、segments 多行文本+获取可用段辅助、训练↔运行系统双向显存检测、应用到数字人确认对话框（源/目标/体积）。
<!-- /autoplan-accepted:design -->

<!-- autoplan-accepted:dx -->
- DX 义务（8 passes 裁决，2026-09-24，DX POLISH，persona=单人直播运营者）：①复制动作一键脚本化（scripts/copy_edtalk.py：复制+MANIFEST+freeze+冒烟，逐项进度与校验输出）；②EDTalk 错误体 {problem,cause,fix} 三段式透传到日志与状态区；③训练页素材要求 tooltip（original_videos 目录约定+素材量参考）；④日常启动路径保持 2 步（开关已配→点运行），首次配置预算 ≤15 分钟（大文件复制时间为机器决定）；⑤错误信息一律 problem+cause+fix 三段（自产错误对齐 EDTalk 契约风格），且每条错误附一条 fix 动作与一个文档锚点（如 RUNBOOK §端口冲突）；⑥edtalk/README.md 为本期交付物（三条冒烟命令原样可粘贴、config 键→CLI 参数映射表、14 步训练命令映射、训练产物路径约定、MANIFEST 补 git 处置/大小两列）；⑦复制 runbook 编号步骤化（原样命令+预期输出+两类常见失败处置）+体量/机时预算标注+迷你冒烟量化判据（lmdb 非零/50 iter loss 收敛区间等 3-4 条）+隐藏 dev 旁路键（config.json 手工键，文档注明风险自负）；⑧前置文档阅读顺序一行图（必读份/节与本计划章节对应）；⑨训练编排步骤边界分隔行（[步骤 N/14] 开始/完成+耗时）写入日志、进度文件读取 try/except（损坏按无进度处理+告警）、推送前校验 wav 头 16kHz（不符明确报错）、预处理步参数本期不暴露（显式取舍）。
<!-- /autoplan-accepted:dx -->

<!-- autoplan-accepted:eng -->
- Eng 义务（4 节裁决，2026-09-24）：①编排以 run_preprocess.py PIPELINE_STEPS（11 步含 prepare_gaze_data）为唯一步骤事实来源，直接调用不重建；②评估解析 eval_results.jsonl（非 regex stdout）；③fastrag 安全边界：copy_edtalk.py 附 netsh 入站阻断 11420 + README 登记已接受风险 + 新拉起分支显式 shell=False/list 传参（native H1）；④"应用到数字人"映射首日验证冒烟+验证前置灰+仅自产 ckpt 校验（native A1/S2）；⑤单飞行句完成判定启发式（duration_s×1.2 + buffer_depth 回落双条件，宁可延迟不截断）+ 首日验证截断边界（native E1）；⑥链路队列全有界+阻塞式背压（native A2）；⑦edtalk_client 内置 16kHz→任意源重采样（soundfile+ratecv，native E3：edge-tts 默认 24kHz）；⑧base_dir 锁文件（PID+时间戳）跨会话互斥+磁盘余量预检（native E4）；⑨8000 探测后 GET /status 验证身份（native E5）；⑩edtalk 模式语义变化清单入 README（stop_current_audio 无效/播放回调跳过/变速后文件推送，native E6）；⑪冒烟加 import __file__ 断言落 AUTOlive 侧（.pth 暗门，native H3）；⑫fastrag 构建前先停 server 再拉起（Windows 文件锁，native H2）；⑬fastrag 端口经 FASTRAG__SERVER__PORT 环境变量注入（tomllib 只读，零 TOML 手术，native H2）；⑭is_edtalk_active() 统一帮助函数（四处双键判定，native A3+T4 合并）；⑮STRUCTURE.md 同步更新（行内义务既有）。
<!-- /autoplan-accepted:eng -->
## Review record

<!-- autoplan-baseline-edits:ceo {"sourceSha256":"4b7b955f7e995a1e4815e71981653a4c94ec4a1bf04217192acbe745ef59dd12","replacements":[{"oldText":"- `enable`（bool，默认 false）——开关：开启后\"运行系统\"时启动实时推理服务器","newText":"- `enable`（bool，默认 false）——开关：开启后\"运行系统\"时启动实时推理服务器；**运行中切换只影响下次\"运行系统\"**"},{"oldText":"4. 新配置节默认值：沿用 `migrate_config.py` 模式一次性写入 config.json（或在 init 时 ensure-default），防 get 返回 None。","newText":"4. 新配置节默认值：Settings init 时 ensure-default（加载后补写缺失键并落盘 config.json），防 get 返回 None。"},{"oldText":"2. `synthesis_manager.py` 分流扩展：`visual_body == \"edtalk_realtime\"` 时走**本地 TTS 合成**（走既有 audio_synthesis 链路，不进 metahuman echo 分支）。","newText":"2. `synthesis_manager.py` 分流扩展：`visual_body == \"edtalk_realtime\"` **且 `edtalk_realtime.enable == true`** 时走**本地 TTS 合成**（走既有 audio_synthesis 链路，不进 metahuman echo 分支）；选 edtalk_realtime 但 enable=false 时 UI 常驻警告\"数字人服务未启用，运行系统将无声\"（音频分流与启动条件对齐，杜绝静音直播组合）。"},{"oldText":"3. `playback_manager.py` 两处 play 旁（147、245）：`visual_body == \"edtalk_realtime\"` 时**跳过本地播放**","newText":"3. `playback_manager.py` 两处 play 旁（147、245）：`visual_body == \"edtalk_realtime\"` 且 `edtalk_realtime.enable == true` 时**跳过本地播放**"},{"oldText":"4. 反应段联动（弹幕→切段，A4）：实现期在回复分发点挂 `switch_segment(at=segment_end)`，运营可配；降级容错同上。（用户未显式要求，按 boil-lakes 保留为小项，eng 审查可裁。）","newText":"4. 反应段联动（弹幕→切段，A4，CEO 已接受）：实现期在回复分发点挂 `switch_segment(at=segment_end)`；配置载体 = `config.edtalk_realtime.reaction_segment`（bool，默认 true，UI 可关）；降级容错同上。"},{"oldText":"- 开始训练按钮（二次确认：训练耗时数小时、占用 GPU）+ 停止按钮","newText":"- 开始训练按钮（二次确认：训练耗时数小时、占用 GPU）+ 停止按钮；训练进行中开始按钮置灰（防重入）。停止按钮语义：终止当前子进程 → 编排线程按失败即停收尾 → 开始按钮复位；日志标记\"用户停止\"（区分步骤失败）"},{"oldText":"13. tools/eval_finetune.py --cand-ckpt …（评估门槛：姿势MAE≤11.1px、贴回≤3.6px、背景SSIM降幅≤0.02、开合比0.8-1.25）","newText":"13. tools/eval_finetune.py --cand-ckpt …（评估门槛：姿势MAE≤11.1px、贴回≤3.6px、背景SSIM降幅≤0.02、开合比0.8-1.25）；**评估不过门槛 = 默认停止并回显全部指标**（不自动进入口型微调；UI 提供\"仍要继续\"显式确认——门槛标定于研究基准数据集，用户对真实素材可用性的判断优先于阈值；评估前移至训练中途的方案延期 TODOS）"},{"oldText":"- 长任务模型：webui 进程内后台线程 + ui.timer 轮询日志（照 schedule/coordination 既有模式）；webui 重启不恢复训练进度（训练子进程独立存活，页面重开可重新附着）。","newText":"- 长任务模型：webui 进程内后台线程 + ui.timer 轮询日志（照 schedule/coordination 既有模式）。webui 重启边界如实声明：**当前正在执行的步骤（子进程）独立存活跑完，但编排线程随 webui 死亡——后续步骤中断，需人工从断点步骤手动重跑**；页面重开可重新附着日志（仅当前步骤）。"},{"oldText":"- 端口显示（默认 11420，可改，写 Config.toml 或环境变量 FASTRAG__SERVER__PORT）","newText":"- 端口显示（默认 11420，可改，写 `edtalk/fastrag/Config.toml`——运行时配置文件，改后重启 server 生效）"},{"oldText":"| 训练 webui 重启后进度恢复 | 长任务模型 v1：重新附着即可 |","newText":"| 训练 webui 重启后进度恢复 | 长任务模型 v1：当前步骤独立存活跑完，后续步骤人工重跑（见 §5.2 如实声明） |\n| 训练断点续训（中断后从最近 checkpoint 自动恢复） | 步骤级失败即停 + 日志留存已覆盖最小需求；resume 能力登记 TODOS |"},{"oldText":"7. 既有功能回归：coordination_program 启动、metahuman_stream 模式、custom_llm 原配置均不受影响。","newText":"7. 既有功能回归：coordination_program 启动、metahuman_stream 模式、custom_llm 原配置均不受影响。\n8. 反应段联动：直播回复触发后 EDTalk 收到 segment/switch（at=segment_end）；EDTalk 未启动时该触发降级为日志记录且直播流程不受影响。\n9. fastrag 模板：点击\"应用 fastrag 请求模板\"后 custom_llm 配置变为 /query 模板且 /query 联通可答。\n10. 训练主播：开始训练前检测到 EDTalk 服务在运行（8000 端口探测）时给出显存冲突警告。"},{"oldText":"复制后须冒烟：runtime312 解释器在 AUTOlive\\edtalk 目录下 `python -m realtime_serve.main --no-unitycapture`、`train_fine_tune.py --help`、`fastrag\\server.exe` 均可启动（相对导入/路径假设逐项验证——EDTalk 代码内部存在对仓库根的路径假设，复制后第一次运行就是验证）。","newText":"复制后须冒烟：runtime312 解释器在 AUTOlive\\edtalk 目录下 `python -m realtime_serve.main --no-unitycapture`、`train_fine_tune.py --help`、`fastrag\\server.exe` 均可启动（相对导入/路径假设逐项验证——EDTalk 代码内部存在对仓库根的路径假设，复制后第一次运行就是验证）。\n\n复制后另须**迷你训练冒烟**（通过后才放开一键训练按钮）：对小样本目录（1-2 个短视频）跑前 2 步预处理 + 底模微调 50 iter，验证 lmdb 写入、CUDA 链路与素材目录假设——`--help`/启动级冒烟不足以验证 15 小时链路的正确性。\n\n复制动作附加义务：① 生成 `edtalk/MANIFEST.md` 记录各目录来源路径与复制日期；② RunC resume 产物（`ckpt_models/wy_runC/checkpoint/035000.pt`）一并复制进 edtalk/ 并在编排参数中固定相对路径——**宣告 `D:\\AI\\AUTOlive\\edtalk` 为唯一运行副本**（EDTalk 目录降为开发存档，验证期后归档 start_edtalk.bat，防双份代码漂移）；③ 同步 `pip freeze` + Python 版本快照入 docs（防 EDTalk 侧环境升级静默断裂）。"},{"oldText":"（cwd=`D:\\AI\\AUTOlive\\edtalk`，参数从 config.edtalk_realtime 映射，按 RUNBOOK 参数表）。","newText":"（cwd=`D:\\AI\\AUTOlive\\edtalk`，参数从 config.edtalk_realtime 映射，按 RUNBOOK 参数表）。**拉起前探测 8000 端口：已占用则跳过拉起并日志告警**（防 start_edtalk.bat 手动双启动的端口冲突）"},{"oldText":"- 显存提示：训练时若实时推理服务器在跑，提示先关闭（16GB 卡两者不可同时跑，batch4 微调 ~12GB）","newText":"- 显存提示：开始训练前同时检测**实时推理服务器（8000 端口）与直播系统自身运行状态（running_flag）**，任一在跑即警告\"训练与直播/推理不可并存，建议错开\"（16GB 卡，batch4 微调 ~12GB）"},{"oldText":"- \"应用 fastrag 请求模板\"按钮：一键把 custom_llm 配置填为 `url=http://127.0.0.1:11420/query`、`body={\"question\":\"{{prompt}}\"}`、`data_analysis=resp[\"answer\"]`（chat_type 仍为 custom_llm，零主程序 LLM 代码改动）","newText":"- \"应用 fastrag 请求模板\"按钮：应用前先实测一次 /query 校验响应字段（确认 answer 键存在再写入模板）；二次确认并备份原 custom_llm 配置到日志（防静默覆盖用户云端 LLM 配置）；然后一键把 custom_llm 配置填为 `url=http://127.0.0.1:11420/query`、`body={\"question\":\"{{prompt}}\"}`、`data_analysis=resp[\"answer\"]`（chat_type 仍为 custom_llm，零主程序 LLM 代码改动）"},{"oldText":"- 训练产物路径回显（ckpt_models/<exp>/checkpoint/xxx.pt）；产出的 audio2lip ckpt 手动/文档指引接入实时推理（角色权重替换）——本期做到\"训练完成+产物可见\"，产物→推理服务的自动接线不在本期（无现成代码，属新开发）。","newText":"- 训练产物路径回显（ckpt_models/<exp>/checkpoint/xxx.pt）。**训练完成后提供\"应用到数字人\"一键接线**：复制 audio2lip 产物到角色权重位置 + 提示重启 realtime_serve 生效（打通\"训练→开播\"最后一公里；此为 taste 备注项留最终批准门确认）。\n- **步骤级重跑**：编排按步骤建模，支持\"从第 N 步开始\"起始参数（UI 高级选项 + 失败断点提示）——webui 重启/步骤失败后无需手敲 11 步命令行。"},{"oldText":"4. 直播链路：弹幕 → LLM → TTS → 音频推送 EDTalk → 虚拟摄像头口型同步、无本地双声。","newText":"4. 直播链路：弹幕 → LLM → TTS → 音频推送 EDTalk → 虚拟摄像头口型同步、无本地双声；TTS 完成→EDTalk 出帧延迟 ≤500ms（首日实测一并收口）。"}]} -->
<!-- autoplan-baseline-edits:design {"sourceSha256":"675091bc1aec29daefbec13faae4c9a043f8e09fcfd812ec6d9fe6e89ff18d8a","replacements":[{"oldText":"- **运行控制区**（对接纪要 A1，直调 HTTP 不落 config）：gaze 热切（409 置灰）、repair 热切、段调度/切换、GET /status 状态回显（current_segment/production_fps/underruns/preroll_state）。","newText":"- **运行控制区**（对接纪要 A1，直调 HTTP 不落 config）：页面三区顺序 = 驱动类型 → EDTalk 配置区 → 运行控制区（**默认折叠，服务运行时自动展开**）；独立卡片呈现，卡片标题标注\"运行时控制（立即生效，不保存）\"与配置区视觉区隔；gaze 热切（409 置灰）、repair 热切、段调度/切换、GET /status 状态回显（current_segment/production_fps/underruns/preroll_state）——**5 秒轮询 + 手动刷新按钮，连续 3 次失败转离线态**；EDTalk 未启动时状态区显示\"服务未运行\"空态而非空白。**按钮状态矩阵（驱动类型 × 服务状态 2×4）逐格定义**：未启动=全禁用+\"服务未运行\"；启动中=禁用+\"启动中\"；运行中=可用；推送异常=可用+警告色。**驱动类型 × enable 四组合提示**：edtalk+enable=false=\"服务未启用，运行系统将无声\"；metahuman+enable=true=\"开关已开但 metahuman 驱动不使用该服务\"；其余两组合正常。"},{"oldText":"- 进度日志区（子进程 stdout 实时回显 + 当前步骤高亮：预处理 11 步 → 底模微调 → 评估 → 口型微调）","newText":"- 进度日志区上方设**常驻步骤条**（14 步列表：当前步高亮、已完成打勾、每步耗时——与 §5.2 步骤建模一一对应，回答\"到哪了/还剩多久\"）；日志区仅作细节流（子进程 stdout 实时回显）。**空态**：未开始训练时显示引导文案\"输入路径后点击开始训练\"+ 主操作提示（非空白终端）；**配色走设计令牌**（新令牌 --bg-log/--text-log，禁硬编码色值）；**窄屏**：768px 以下日志区 overflow-x auto 防等宽文本撑破布局"},{"oldText":"   - metahuman_stream 参数区保留（类型/API 地址），选 edtalk_realtime 时视觉降权。","newText":"   - metahuman_stream 参数区保留（类型/API 地址），选 edtalk_realtime 时**收起为折叠面板**（标题可见+提示\"当前使用 EDTalk 实时推理\"，不删除配置）。"},{"oldText":"   - 未启动容错：连接失败降级为跳过推送 + logger.error，不阻塞直播主流程","newText":"   - 未启动容错：连接失败降级为跳过推送 + logger.error，不阻塞直播主流程；**推送健康度运营可见**：画面设置页状态区显示\"EDTalk 推送：正常 / 未启动 / 最近 N 次失败\"；会话内首次推送失败弹一次 toast\"数字人服务未连接，直播将无声\"，后续失败累计计数不刷屏"},{"oldText":"停止按钮语义：终止当前子进程 → 编排线程按失败即停收尾 → 开始按钮复位；日志标记\"用户停止\"（区分步骤失败）","newText":"停止按钮语义：终止当前子进程 → 编排线程按失败即停收尾 → 开始按钮复位；日志标记\"用户停止\"（区分步骤失败）。**停止需二次确认**，文案暴露代价：\"终止将丢弃当前步骤进度（本期无断点续训），已耗时 X 小时，确定停止？\""},{"oldText":"- **步骤级重跑**：编排按步骤建模，支持\"从第 N 步开始\"起始参数（UI 高级选项 + 失败断点提示）——webui 重启/步骤失败后无需手敲 11 步命令行。","newText":"- **步骤级重跑**：编排按步骤建模，支持\"从第 N 步开始\"起始参数（UI 高级选项 + 失败断点提示）——webui 重启/步骤失败后无需手敲 11 步命令行。**每完成一步即写进度文件** `edtalk/train_progress/<角色>.json`（已完成步骤、当前步骤、时间戳）；页面加载时读取并显示\"上次进行到第 X 步（中断于第 Y 步）\"，\"从第 N 步开始\"预填为 Y。"},{"oldText":"**训练完成后提供\"应用到数字人\"一键接线**：复制 audio2lip 产物到角色权重位置 + 提示重启 realtime_serve 生效","newText":"**训练完成后提供\"应用到数字人\"一键接线**：确认对话框列出源产物路径、目标权重路径、文件体积，确认后复制 + 提示重启 realtime_serve 生效"},{"oldText":"     - `segments`（默认 auto / 指定列表）","newText":"     - `segments`（默认 auto / 指定列表）；**指定列表控件 = 多行文本（一行一个段名）+ 格式校验 + \"从 /status 获取可用段\"辅助按钮**（段名大小写敏感，UI 给合法示例）"},{"oldText":"**拉起前探测 8000 端口：已占用则跳过拉起并日志告警**（防 start_edtalk.bat 手动双启动的端口冲突）","newText":"**拉起前探测 8000 端口：已占用则跳过拉起并日志告警**（防 start_edtalk.bat 手动双启动的端口冲突）；**同时检测训练进行中标志：训练中运行系统同样弹警告**（16GB 卡双向防护）"},{"oldText":"然后一键把 custom_llm 配置填为 `url=http://127.0.0.1:11420/query`、`body={\"question\":\"{{prompt}}\"}`、`data_analysis=resp[\"answer\"]`（chat_type 仍为 custom_llm，零主程序 LLM 代码改动）","newText":"然后一键把 custom_llm 配置填为 `url=http://127.0.0.1:<配置端口>/query`（**引用当前配置端口变量，config.json 为 UI 真源、应用时同步写回 Config.toml**——模板永不硬编码端口）、`body={\"question\":\"{{prompt}}\"}`、`data_analysis=resp[\"answer\"]`（chat_type 仍为 custom_llm，零主程序 LLM 代码改动）。**原配置落盘备份** `config.custom_llm.bak.json`（非仅日志），确认对话框展示将被覆盖的 url/body/data_analysis，应用后卡片提供\"还原上次配置\"按钮"},{"oldText":"- **构建知识库按钮** → subprocess 调 `build-index.exe`（cwd=fastrag 目录），完成后提示重启 server 生效","newText":"- **构建知识库按钮** → 构建前校验 knowledgebase/ 目录非空（空则提示\"请先放入 .md/.txt 文件\"）；subprocess 调 `build-index.exe`（cwd=fastrag 目录），完成后提示附\"重启 server\"按钮（复用 my_subprocesses 停止+拉起链路，不走停系统绕路）"},{"oldText":"- 服务状态探测（GET /health，回显 chunks/rerank 状态）","newText":"- 服务状态探测（GET /health，回显 chunks/rerank 状态）；**离线态显示\"服务未运行\"灰字 + 引导文案**（开启开关并运行系统后自动恢复）"}]} -->
<!-- autoplan-baseline-edits:dx {"sourceSha256":"872b97da49612b3b8da1992bca782735e8fada14b5aceaf44ed861e8a92416cf","replacements":[{"oldText":"③ 同步 `pip freeze` + Python 版本快照入 docs（防 EDTalk 侧环境升级静默断裂）。","newText":"③ 同步 `pip freeze` + Python 版本快照入 docs（防 EDTalk 侧环境升级静默断裂）。**复制动作脚本化**：以上复制+MANIFEST+freeze+冒烟全部封装为一键脚本（`scripts/copy_edtalk.py`，运营者双击/单命令执行，逐项打印进度与校验结果——不要求运营者手敲 robocopy）"},{"oldText":"会话内首次推送失败弹一次 toast\"数字人服务未连接，直播将无声\"，后续失败累计计数不刷屏","newText":"会话内首次推送失败弹一次 toast\"数字人服务未连接，直播将无声\"，后续失败累计计数不刷屏；**EDTalk 错误体三段式透传**：响应体 {problem, cause, fix}（EMBEDDING 契约）原样写入日志与状态区 tooltip——运营者所见即服务端给出的修法"},{"oldText":"- 原始视频文件夹路径输入（必填，含角色说话视频）+ 角色名（默认取文件夹名）","newText":"- 原始视频文件夹路径输入（必填，含角色说话视频）+ 角色名（默认取文件夹名）；**素材要求提示**（tooltip/折叠说明）：视频格式与放法（original_videos 子目录）、建议时长与数量参照 EDTalk 数据集处理流程（wy 实例 = 529 视频/63055 clips；更少素材可训但口型质量预期下降）"},{"oldText":"     - `host`（默认 127.0.0.1）、`api_token`（默认空）","newText":"     - `host`（默认 127.0.0.1）、`api_token`（默认空）\n     - `port`（默认 8000）——**服务端启动参数、客户端 api_ip_port、端口幂等探测三处统一引用此键**（改端口一处生效，杜绝客户端/服务端脱钩）\n     - `interpreter_path`（默认 `D:\\AI\\EDTalk\\runtime312\\python.exe`）——启动与训练编排统一读取；**拉起/训练前校验存在性，缺失时报错附修复动作（指向此配置键）**（消除跨目录硬编码死点）"},{"oldText":"配置载体 = `config.edtalk_realtime.reaction_segment`（bool，默认 true，UI 可关）；降级容错同上。","newText":"配置载体 = `config.edtalk_realtime.reaction_segment`（bool，默认 true，UI 可关；**UI 标签用布尔语义文案\"弹幕触发切段：开/关\"**，防误读为段名）；降级容错同上。"}]} -->
<!-- autoplan-baseline-edits:eng {"sourceSha256":"63d5129d5a39531ad90159369c8f8de78e4e871591ff44de5bf765f7ccb25fde","replacements":[{"oldText":"1-11. data_preprocess/data_preprocess_for_train/ 各步（crop_video_HDTF → split_video/audio →\n      prepare_lmdb → get_mel → extract_lmdk → extract_bbox → extract_lip_pose_features →\n      prepare_feature_lmdb → crop_head_video，--base_dir 指向用户输入目录）\n12. train_fine_tune.py --datapath <角色> --exp_name <角色> --only_fine_tune_dec --iter 30000\n13. tools/eval_finetune.py --cand-ckpt …（评估门槛：姿势MAE≤11.1px、贴回≤3.6px、背景SSIM降幅≤0.02、开合比0.8-1.25）；**评估不过门槛 = 默认停止并回显全部指标**（不自动进入口型微调；UI 提供\"仍要继续\"显式确认——门槛标定于研究基准数据集，用户对真实素材可用性的判断优先于阈值；评估前移至训练中途的方案延期 TODOS）\n14. train/train_audio2mouth.py（定稿配方参数，resume RunC 产物）","newText":"1. data_preprocess/data_preprocess_for_train/run_preprocess.py（**EDTalk 自带编排器，PIPELINE_STEPS=11 步为唯一步骤事实来源**：crop_video_HDTF → split_video/audio → prepare_lmdb → get_mel → extract_lmdk → extract_bbox → extract_lip_pose_features → prepare_feature_lmdb → crop_head_video → **prepare_gaze_data**——pipeline 直接调用它，继承其幂等/失败即停/步骤选择（--steps），**不重建步骤清单**；native E2 实测修正：计划原清单漏 prepare_gaze_data 步）\n12. train_fine_tune.py --datapath <角色> --exp_name <角色> --only_fine_tune_dec --iter 30000\n13. tools/eval_finetune.py --cand-ckpt <步骤12产物>（**解析 eval_results.jsonl 机器可读输出**——eval_finetune.py:330 实证；评估门槛：姿势MAE≤11.1px、贴回≤3.6px、背景SSIM降幅≤0.02、开合比0.8-1.25）；**评估不过门槛 = 默认停止并回显全部指标**（不自动进入口型微调；UI 提供\"仍要继续\"显式确认——门槛标定于研究基准数据集，用户对真实素材可用性的判断优先于阈值；评估前移至训练中途的方案延期 TODOS）\n14. train/train_audio2mouth.py（定稿配方参数，resume RunC 产物）\n\n步骤条相应为 4 大阶段（预处理编排 / 底模微调 / 评估 / 口型微调，预处理阶段内解析 run_preprocess 输出显示 11 子步进度）；copy_edtalk.py 冒烟含 **run_preprocess.py 全部 11 个步骤脚本存在性校验**。"},{"oldText":"- `start_programs()`：`fastrag.enable == true` → `subprocess.Popen([edtalk\\fastrag\\server.exe], cwd=fastrag目录)`，进 my_subprocesses 随停。","newText":"- `start_programs()`：`fastrag.enable == true` → `subprocess.Popen([edtalk\\fastrag\\server.exe], cwd=fastrag目录)`，进 my_subprocesses 随停。**安全边界（native S1 实证：server.rs:199 硬编码 0.0.0.0 且无鉴权）**：copy_edtalk.py 附 `netsh advfirewall` 入站阻断 11420 规则（本机回环不走防火墙，正常使用不受影响，局域网不可达）；edtalk/README.md 登记此暴露为已接受风险；**新拉起分支显式 shell=False + list 传参**（native H1：main.py:216 存量 shell=True 不得沿用——含空格/中文路径会被 cmd.exe 打碎）"},{"oldText":"**训练完成后提供\"应用到数字人\"一键接线**：确认对话框列出源产物路径、目标权重路径、文件体积，确认后复制 + 提示重启 realtime_serve 生效（打通\"训练→开播\"最后一公里；此为 taste 备注项留最终批准门确认）。","newText":"**训练完成后提供\"应用到数字人\"一键接线**：确认对话框列出源产物路径、目标权重路径、文件体积，确认后复制 + 提示重启 realtime_serve 生效（打通\"训练→开播\"最后一公里；此为 taste 备注项留最终批准门确认）。**映射待验证声明（native A1）**：realtime_serve 加载 `<character_dir>/checkpoint/new.pt` 与 `audio2lip.pt`（sequence_frame_pack.py 实证），训练产物 `ckpt_models/<exp>/checkpoint/<iter>.pt` 为内嵌 args 的完整 ckpt——**键结构对齐需实施首日一次性验证冒烟**（预埋验证任务：产物 ckpt 键 → new.pt 键映射表），验证通过前\"应用到数字人\"按钮保持置灰并提示\"映射验证中\"；应用对话框校验\"仅允许自产 ckpt\"（native S2，pickle 反序列画面）。"},{"oldText":"- **限流**：单飞行句（上一句 processing 完成前不推下一句）+ 队列上限 2（EMBEDDING v0.4 过载语义）","newText":"- **限流**：单飞行句 + 等待队列上限 2（EMBEDDING v0.4 过载语义）。**完成判定启发式（native E1 实证：push_full 返回 {ok,duration_s,processing:\"async\"} 无逐句完成信号；过早推送会被 render_loop 的 _flush_buffer 截断上一句剩余音频）**：最短飞行时间 = `duration_s × (1+0.2)` + 观察 /status buffer_depth 回落，双条件齐才放行下一句；启发式不精确时的取舍 = 宁可延迟不截断（断句不可接受）；**实施首日双端实测一并验证截断边界**（并入 R2 义务）。**背压语义（native A2）**：跳过本地播放后节拍由推送链负责——链路各队列（message_queue→synthesis→playback→edtalk_client）全部有界，满载时**阻塞式背压**（上游等下游，不丢弃台词），溢出只发生在 edtalk_client 等待队列且受背压保护不触发"}]} -->



<!-- autoplan-accepted:ceo -->
- 用户原始需求三点全部保持：①实时推理集成"虚拟身体"→"数字人/画面设置"+开关随系统启动+参数替代 metahuman_stream（按 RUNBOOK 参数表）；②训练主播独立页无开关，输入路径一键完成 11 步预处理+底模微调+评估+口型微调；③fastrag 整合大语言模型页，server 由开关控制启动，build-index 由构建知识库按钮手动触发。
- 代码与模型复制进 D:\AI\AUTOlive\edtalk\（隔离布局）；解释器统一用 D:\AI\EDTalk\runtime312\python.exe（既定基线，不复制环境）。
- 音频链路按"仅推送不本地播放"执行（EDTalk 帧锁定播放）；实施首日双端实测收口（R2 义务）。
- 复制完成后逐入口冒烟：realtime_serve --no-unitycapture、train_fine_tune.py --help、fastrag server.exe（R1 义务）。
- edtalk_client 限流：单飞行句 + 队列 ≤2（EMBEDDING v0.4 契约义务）。
- parameters 透传修复 cmd = [executable] + parameters，且回归既有 coordination_program 条目（R5 义务）。
- 训练前探测 EDTalk 服务状态并提示显存冲突（0G 接受扩展 #3）。
- 反应段联动：回复分发点挂 switch_segment(at=segment_end)，连接失败降级跳过；配置载体 = config.edtalk_realtime.reaction_segment（bool，默认 true，UI 可关）（0G 接受扩展 #1）。
- fastrag 请求模板一键填充 custom_llm 配置（0G 接受扩展 #2）。
- 新配置节 edtalk_realtime / fastrag 带默认值迁移（init ensure-default，§4.1.4 定案）。
- 大文件（ckpts/weights/exe/chekpoint/kbindex）.gitignore 防入库；代码与 Config.toml/knowledgebase 入库。
- 延期项登记 TODOS.md：评估前移至训练中途、多角色管理/训练队列、进程级断点续训（步骤级重跑已入本期）。
- native CEO 声部修正义务（2026-09-24 裁决）：迷你训练冒烟后放开一键按钮；edtalk/ 唯一运行副本+MANIFEST+RunC 产物复制+pip freeze 快照；realtime_serve 拉起前 8000 端口幂等探测；训练前双检测（8000 端口+running_flag）；评估不过默认停+"仍要继续"显式确认；fastrag 模板应用前 /query 实测字段+二次确认+原配置备份；步骤级"从第 N 步开始"重跑；训练完成"应用到数字人"一键接线（taste 留门）；验收 #4 延迟 ≤500ms。
- Sections 1-11 行内义务（CEO 深审）：pipeline 子进程统一 encoding='utf-8', errors='replace'（禁 shell=True，参数列表传参）；训练日志落盘 logs/train_streamer/<日期>.log；fastrag 构建按钮加 spinner 反馈；ensure-default 补 1 个 config 合并单测；edtalk_client 推送日志含 segment 与耗时。
<!-- /autoplan-accepted:ceo -->

<!-- autoplan-accepted:design -->
- 设计义务（7 passes 裁决，2026-09-24）：①运行控制区独立卡片+"立即生效"标注+5s 轮询+手动刷新+服务未运行空态；②训练日志区空态引导文案、配色新令牌 --bg-log/--text-log、窄屏 overflow-x auto；③metahuman_stream 区收起为折叠面板；④训练完成 ui.notify positive + 产物路径高亮（Pass 3 成功反馈）；⑤构建知识库空态（knowledgebase 为空时提示先放文件）；⑥评估"仍要继续"确认框用 Quasar dialog（destructive 语义样式）；⑦全部新 UI 遵循 design_tokens.py 令牌（禁硬编码色值）与 FormField/三列网格样板；⑧mockup 未生成（designer API key 缺失），实施后跑 /design-review 做视觉 QA；⑨native design 声部修正（15 项全采纳，2026-09-24）：推送健康度可见面+首次失败 toast、停止二次确认（代价文案）、常驻步骤条+每步耗时、进度文件 train_progress/<角色>.json、控制区 2×4 按钮状态矩阵+四组合提示+默认折叠、fastrag 端口真源=config.json 同步写回 Config.toml+模板引用变量、模板备份落盘 config.custom_llm.bak.json+还原按钮、构建前校验+完成后重启 server 按钮、segments 多行文本+获取可用段辅助、训练↔运行系统双向显存检测、应用到数字人确认对话框（源/目标/体积）。
<!-- /autoplan-accepted:design -->

<!-- autoplan-accepted:dx -->
- DX 义务（8 passes 裁决，2026-09-24，DX POLISH，persona=单人直播运营者）：①复制动作一键脚本化（scripts/copy_edtalk.py：复制+MANIFEST+freeze+冒烟，逐项进度与校验输出）；②EDTalk 错误体 {problem,cause,fix} 三段式透传到日志与状态区；③训练页素材要求 tooltip（original_videos 目录约定+素材量参考）；④日常启动路径保持 2 步（开关已配→点运行），首次配置预算 ≤15 分钟（大文件复制时间为机器决定）；⑤错误信息一律 problem+cause+fix 三段（自产错误对齐 EDTalk 契约风格），且每条错误附一条 fix 动作与一个文档锚点（如 RUNBOOK §端口冲突）；⑥edtalk/README.md 为本期交付物（三条冒烟命令原样可粘贴、config 键→CLI 参数映射表、14 步训练命令映射、训练产物路径约定、MANIFEST 补 git 处置/大小两列）；⑦复制 runbook 编号步骤化（原样命令+预期输出+两类常见失败处置）+体量/机时预算标注+迷你冒烟量化判据（lmdb 非零/50 iter loss 收敛区间等 3-4 条）+隐藏 dev 旁路键（config.json 手工键，文档注明风险自负）；⑧前置文档阅读顺序一行图（必读份/节与本计划章节对应）；⑨训练编排步骤边界分隔行（[步骤 N/14] 开始/完成+耗时）写入日志、进度文件读取 try/except（损坏按无进度处理+告警）、推送前校验 wav 头 16kHz（不符明确报错）、预处理步参数本期不暴露（显式取舍）。
<!-- /autoplan-accepted:dx -->

<!-- autoplan-accepted:eng -->
- Eng 义务（4 节裁决，2026-09-24）：①编排以 run_preprocess.py PIPELINE_STEPS（11 步含 prepare_gaze_data）为唯一步骤事实来源，直接调用不重建；②评估解析 eval_results.jsonl（非 regex stdout）；③fastrag 安全边界：copy_edtalk.py 附 netsh 入站阻断 11420 + README 登记已接受风险 + 新拉起分支显式 shell=False/list 传参（native H1）；④"应用到数字人"映射首日验证冒烟+验证前置灰+仅自产 ckpt 校验（native A1/S2）；⑤单飞行句完成判定启发式（duration_s×1.2 + buffer_depth 回落双条件，宁可延迟不截断）+ 首日验证截断边界（native E1）；⑥链路队列全有界+阻塞式背压（native A2）；⑦edtalk_client 内置 16kHz→任意源重采样（soundfile+ratecv，native E3：edge-tts 默认 24kHz）；⑧base_dir 锁文件（PID+时间戳）跨会话互斥+磁盘余量预检（native E4）；⑨8000 探测后 GET /status 验证身份（native E5）；⑩edtalk 模式语义变化清单入 README（stop_current_audio 无效/播放回调跳过/变速后文件推送，native E6）；⑪冒烟加 import __file__ 断言落 AUTOlive 侧（.pth 暗门，native H3）；⑫fastrag 构建前先停 server 再拉起（Windows 文件锁，native H2）；⑬fastrag 端口经 FASTRAG__SERVER__PORT 环境变量注入（tomllib 只读，零 TOML 手术，native H2）；⑭is_edtalk_active() 统一帮助函数（四处双键判定，native A3+T4 合并）；⑮STRUCTURE.md 同步更新（行内义务既有）。
<!-- /autoplan-accepted:eng -->

### Eng Step 0 Scope Challenge（复杂度门，2026-09-24）

- **复杂度**：触碰文件 >8（frontend×4 + 新增×4 + config.json + edtalk/ 复制）→ 复杂度门触发。autoplan 覆盖规则 P2"从不缩减"：三模块（推理/训练/知识库）各自文件域独立，`edtalk/` 隔离子目录已是"更少活动部件"的结构方案；新文件数是用户三模块需求的必然产物。**结构确认：原布局保留**（无裁剪提案）。
- **复用映射**：0B 完成（start_programs/AUDIO_PLAYER/Custom_LLM/FormField/ENABLE_PATHS 五项复用，零重建）。
- **Search check**：subprocess 编排/HTTP 客户端/webui 表单页均为 Layer 1 成熟模式，无新基础设施——跳过外部搜索（in-distribution 知识足够）。
- **TODOS 交叉**：三阶段延期项已全部登记；eng 无新增 TODO（DRY 修复入任务 T4）。
- **Distribution**：单机自用，无构建/发布/分发面（N/A）。

### Eng Sections 1-4 主视角发现（2026-09-24）

**Section 1 架构**——依赖图：
```
弹幕源 → main.py 运行时 → LLM(custom_llm → fastrag /query) → TTS → synthesis_manager
                                                                            ↓ (edtalk_realtime 模式)
webui(8086) → start_programs ──┬→ main.py 运行时                     playback_manager
                               ├→ realtime_serve(:8000) ←─── push_full(edtalk_client)
                               └→ fastrag server(:11420)                 ↓
训练主播页 → pipeline.py ─(subprocess×14, runtime312)→ edtalk/   EDTalk 帧锁定播放 → 虚拟摄像头
```
生产失败场景逐一核验：①EDTalk 推理 OOM→推送堆积（单飞行句限流缓解✓）；②webui 重启丢编排（步骤重跑+进度文件✓）；③AV 误拦 server.exe（冒烟 gate 含 fastrag server.exe 启动，首日暴露✓）；④training 时点"运行系统"（双向检测✓）。SPOF=webui 单机集中（自用可接受）。**0 critical**。

**Section 2 代码质量**——2 项：
- [P2] (confidence 8/10) playback_manager.py:147,245 — 两处 play 旁将写入相同的分流条件（visual_body+enable 双检），DRY 违例风险：抽 `_is_edtalk_mode()` 公共方法，两处复用（采纳→任务 T4）。
- [P3] (confidence 8/10) frontend/STRUCTURE.md:35 — tab 结构文档将因 train_streamer 新页过时——义务：实施时同步更新 STRUCTURE.md（已入行内义务）。

**Section 3 测试**（测试图，18 条新 codepath 全 GAP 属新功能预期）：
```
CODE PATHS                                          USER FLOWS
[+] utils/edtalk_realtime/edtalk_client.py          [→E2E] 完整直播链路（首日实测，AC4）
  ├── 16kHz 校验          [GAP→单测 T1]             ├── [→集成] 推送失败降级（T1）
  ├── base64+POST         [GAP→单测 T1]             └── [→集成] 段切换联动（AC8）
  ├── 单飞行句限流         [GAP→单测 T1]
  ├── 降级容错             [GAP→单测 T1]            [+] 训练一键
  └── 三段式透传           [GAP→单测 T1]              ├── [→手工] 迷你训练冒烟（gate）
[+] utils/train_streamer/pipeline.py                 └── [→手工] 停止确认+断点重跑
  ├── 编排+失败即停        [GAP→单测 T2]
  ├── 进度文件+损坏容错    [GAP→单测 T2]            [+] fastrag 卡片
  ├── 步骤级重跑参数       [GAP→单测 T2]              ├── [→集成] 模板填充+还原（AC9）
  └── argv 签名映射        [GAP→单测 T2]              └── [→集成] 构建+重启按钮（AC6）
[+] frontend/main.py 启动接线
  ├── cmd 透传回归(R5铁律) [GAP→单测 T3] ← CRITICAL REGRESSION RISK：威胁既有 coordination_program
  ├── 8000 幂等探测        [GAP→单测 T3]
  ├── interpreter 校验     [GAP→单测 T3]
  └── ensure-default       [GAP→单测 T3]
COVERAGE: 0/18（新功能）；P1 单测义务 13 项（T1-T3）；LLM/eval：无（fastrag 语义改造 NOT in scope）
```
R5 回归为 IRON RULE 关键项：cmd 透传改动威胁既有 coordination_program 启动行为，回归测试为放行前置（T3）。

**Section 4 性能**——0 新发现（无 DB 访问；base64 内存峰值 ≤10MB×1 句可忽略；日志轮询行缓冲已有义务）。

**Eng 失败模式登记册**：
```
CODEPATH              | FAILURE MODE      | RESCUED? | TEST? | USER SEES?      | LOGGED?
cmd 透传修改           | 旧条目启动破坏     | Y(兼容设计) | Y(T3 回归) | 启动失败 notify | Y
push_full             | OOM/过载          | Y(限流)    | Y(T1)    | 口型延迟日志    | Y
pipeline 步骤          | OOM/路径缺失      | Y(即停+断点) | Y(T2)  | 断点提示        | Y
server.exe            | AV 拦截           | Y(冒烟gate) | Y(冒烟)  | 冒烟报告标红    | Y
CRITICAL GAPS: 0
```

### Eng Completion Summary

```
+====================================================================+
|         MEGA PLAN REVIEW — COMPLETION SUMMARY (ENG, FINAL)         |
+====================================================================+
| Step 0: Scope Challenge | 复杂度门触发→原布局确认（P2 不缩减）        |
| Architecture Review     | 0 issues（失败场景 4 项全有缓解）           |
| Code Quality Review     | 2 issues（DRY T4 + STRUCTURE.md 义务）     |
| Test Review             | 图已出，18 GAP→13 P1 单测义务（T1-T3）     |
| Performance Review      | 0 issues                                  |
| NOT in scope            | written（§7 7 项）                        |
| What already exists     | written（§2 14 项复用映射）               |
| TODOS.md updates        | 0 新增（三阶段延期项已登记）               |
| Failure modes           | 4 total, 0 CRITICAL GAPS                  |
| Unresolved decisions    | 0（本阶段全部机械裁决）                    |
| Outside voice           | codex unavailable（provider 400）          |
| Parallelization         | 2 lanes（见下）                            |
| Lake Score              | N/A                                       |
+====================================================================+
```

**Worktree 并行策略**：
| Step | Modules touched | Depends on |
|------|----------------|------------|
| 复制+冒烟 | edtalk/（新） | — |
| 模块一 UI+接线 | frontend/, utils/edtalk_realtime/ | 复制+冒烟 |
| 模块二训练 | frontend/ui/tabs/train_streamer, utils/train_streamer/ | 复制+冒烟 |
| 模块三 fastrag | frontend/ui/tabs/llm.py | 复制+冒烟 |
Lane A: 模块一 UI+接线（sequential, frontend/main.py 共享）
Lane B: 模块二 → 模块三（train_streamer 与 llm.py 独立，可与 Lane A 并行；merge 后统一回归）
执行序：先复制+冒烟（gate）→ Launch A + B 并行 → merge → 回归 T3 + AC7。
冲突旗标：frontend/main.py 被 Lane A 独占（start_programs 接线），Lane B 不触碰 main.py → 无冲突。


### DX DUAL VOICES — CONSENSUS TABLE [subagent-only]（Codex 400 不可用）

```
DX DUAL VOICES — CONSENSUS TABLE:
  Dimension                           Claude(主)  Claude(子代理native)  Consensus
  1. Getting started < 5 min?          日常2步✓    F1 无runbook/无预算     N/A（单声部）
  2. API/CLI naming guessable?         ✓          F5/F13 硬编码死点挑战   N/A（单声部）
  3. Error messages actionable?        三段式      F8 缺fix动作+文档锚点   N/A（单声部）
  4. Docs findable & complete?         部分覆盖    F11 README交付物缺失    N/A（单声部）
  5. Upgrade path safe?                MANIFEST✓  （无重大异议）          N/A（单声部）
  6. Dev environment friction-free?    解释器固定  F13 可覆盖键缺失        N/A（单声部）
Codex 缺席：六单元 N/A。native 15 项（0 critical/5 high/10 medium）全部 0D 机械裁决采纳。
```

### Developer Persona Card（DX）

```
TARGET DEVELOPER PERSONA（= 运营者 persona）
Who:       单人直播运营者（非程序员），自用 Windows 单机
Context:   日常开播前配置与启动；偶尔训练新角色、重建知识库
Tolerance: 日常启动 >5 步即烦躁；首次配置可接受 ≤15 分钟（含复制）
Expects:   开关一开就工作；出错能看到"怎么办"而不是堆栈
```

### Developer Empathy Narrative（DX，第一人称）

“周五晚上开播前，我在画面设置页确认 EDTalk 开关开着，点『运行系统』——两分钟内虚拟摄像头有画面，这很好。但如果 8000 端口被昨天调试残留的进程占着，我需要页面直接告诉我『端口被占，用 netstat 找到 PID 或改 port 配置』，而不是去翻日志。训练新主播时，我最怕两件事：点错停止丢掉十几个小时（确认框必须告诉我代价），以及半夜训练卡住我却分不清『正常慢』还是『死了』（每步耗时和量化判据是我的救命绳）。重建知识库后，我要一个『重启 server』按钮，而不是去任务管理器杀进程。”

### Developer Journey Map（DX）

```
STAGE           | DEVELOPER DOES              | FRICTION POINTS            | STATUS
----------------|-----------------------------|----------------------------|--------
1. Discover     | 导航看到"数字人"分组         | 无                         | ok
2. Install      | 跑 copy_edtalk.py 一键脚本   | 手敲 robocopy（F1）        | fixed（脚本化+runbook）
3. Hello World  | 冒烟+迷你训练门→配置→运行    | 判据不量化（F4）/无旁路(F14)| fixed（量化判据+dev键）
4. Real Usage   | 开关+运行系统；推送健康度可见| 端口脱钩（F5）             | fixed（port 键）
5. Debug        | 状态区/toast/日志三段式      | 缺fix动作+锚点（F8）       | fixed（错误模板义务）
6. Upgrade      | MANIFEST+freeze 重复制       | 无自动同步（合理，TODOS）  | ok
```

### DX Scorecard

```
+====================================================================+
|              DX PLAN REVIEW — SCORECARD                             |
+====================================================================+
| Dimension            | Score  | 初始  | 趋势  |
|----------------------|--------|-------|-------|
| Getting Started      | 8/10   | 7/10  | ↑     |
| API/CLI/SDK（配置面）| 9/10   | 8/10  | ↑     |
| Error Messages       | 9/10   | 8/10  | ↑     |
| Documentation        | 9/10   | 7/10  | ↑     |
| Upgrade Path         | 8/10   | 8/10  | —     |
| Dev Environment      | 9/10   | 8/10  | ↑     |
| Community            | N/A    | —     | 单机自用无社区面 |
| DX Measurement       | 8/10   | 8/10  | —     |
+--------------------------------------------------------------------+
| TTHW                 | 首次 3 步（脚本→冒烟→配置）+复制机时 | 日常 ≤2 min |
| Competitive Rank     | Competitive（自托管工具第一梯队）    |
| Magical Moment       | 已设计：一键脚本→冒烟全绿→开关一开数字人开播 |
| Product Type         | 自托管运营平台（单人）               |
| Mode                 | DX POLISH                            |
| Overall DX           | 7/10 → 9/10                         |
+====================================================================+
| DX PRINCIPLE COVERAGE                                               |
| Zero Friction      | covered（2 步日常启动）                         |
| Learn by Doing     | covered（冒烟+迷你训练门）                      |
| Fight Uncertainty  | covered（三段式+健康度+步骤耗时）               |
| Opinionated + Escape Hatches | covered（默认预填+dev旁路+还原按钮）  |
| Code in Context    | covered（runbook 原样命令）                     |
| Magical Moments    | covered（一键脚本全绿）                         |
+====================================================================+
```

### DX Implementation Checklist

```
DX IMPLEMENTATION CHECKLIST
============================
[x] 日常启动 ≤ 2 分钟（开关已配→点运行）
[x] 复制一键脚本化（copy_edtalk.py：复制+MANIFEST+freeze+冒烟）
[x] 首次运行产出可验证结果（迷你训练冒烟量化判据）
[x] Magical moment：冒烟全绿→开关一开即开播
[x] 每条错误 = problem + cause + fix 动作 + 文档锚点
[x] 配置键命名与 RUNBOOK 参数一一对应；port/interpreter 可覆盖
[x] 每个配置项有预填默认值（ensure-default）
[ ] README 含可粘贴命令与映射表（实施期交付，edtalk/README.md）
[ ] 步骤边界日志行 + 进度文件容错（实施期随 pipeline 落地）
[ ] 16kHz wav 头校验（实施期随 edtalk_client 落地）
```

### DX 阶段 NOT in scope / What already exists

- **NOT in scope（DX）**：EDTalk 侧代码变更自动同步到副本（手工重复制+MANIFEST 足够，TODOS 记录）；预处理参数 UI 暴露（v1 显式取舍）；自动更新机制（单机自用）。
- **What already exists（DX）**：RUNBOOK/EMBEDDING 契约文档（错误三段式源头）；migrate_config.py 迁移模式；ensure-default 机制；ui.notify/logger 反馈链；coordination_program 启停模式；my_subprocesses 复用停止链路。全部复用。


### DESIGN OUTSIDE VOICES — LITMUS SCORECARD [subagent-only]（Codex 400 不可用）

```
DESIGN OUTSIDE VOICES — LITMUS SCORECARD:
═══════════════════════════════════════════════════════════════
  Check                                    Claude  Codex  Consensus
  ─────────────────────────────────────── ─────── ─────── ─────────
  1. Brand unmistakable in first screen?   N/A*    —      N/A（单声部）
  2. One strong visual anchor?             YES     —      N/A（单声部）
  3. Scannable by headlines only?          YES     —      N/A（单声部）
  4. Each section has one job?             YES     —      N/A（单声部）
  5. Cards actually necessary?             YES     —      N/A（单声部）
  6. Motion improves hierarchy?            YES     —      N/A（单声部）
  7. Premium without decorative shadows?   YES     —      N/A（单声部）
  ─────────────────────────────────────── ─────── ─────── ─────────
  Hard rejections triggered:               0       —      —
═══════════════════════════════════════════════════════════════
* OPERATE 类后台工作台，品牌首屏规则不适用（分类器：App UI Rules）。
```

### Design Completion Summary

```
+====================================================================+
|         DESIGN PLAN REVIEW — COMPLETION SUMMARY                    |
+====================================================================+
| System Audit         | 无 DESIGN.md；design_tokens.py 为事实设计系统 |
|                      | （记忆：禁硬编码色值）；UI scope=画面设置重做 |
|                      | /训练主播新页/fastrag 卡片/导航改名           |
| Step 0               | 初始 7/10；缺口=状态细节/长任务可见面          |
| Pass 1  (Info Arch)  | 8/10 → 9/10（三区顺序+控制区区隔+折叠默认）   |
| Pass 2  (States)     | 7/10 → 9/10（2×4 状态矩阵+空态+健康度可见面） |
| Pass 3  (Journey)    | 8/10 → 9/10（停止代价文案+完成通知+重启按钮） |
| Pass 4  (AI Slop)    | 9/10 → 9/10（OPERATE 类；黑名单 11 项零命中） |
| Pass 5  (Design Sys) | 8/10 → 9/10（--bg-log/--text-log 新令牌+端口真源）|
| Pass 6  (Responsive) | 8/10 → 9/10（日志窄屏溢出；a11y 继承既有）    |
| Pass 7  (Decisions)  | 5 resolved（轮询/折叠/控件/真源/确认面），0 deferred |
+--------------------------------------------------------------------+
| NOT in scope         | written（mockup 反馈循环→实施后 /design-review）|
| What already exists  | written（design_tokens/FormField/三列网格/sys-btn）|
| TODOS.md updates     | 0 新增（设计债全部入本期义务）                 |
| Approved Mockups     | 0 generated（API key 缺失）                    |
| Decisions made       | 17（主视角 6 + native 采纳 11 组）            |
| Decisions deferred   | 0                                             |
| Overall design score | 7/10 → 9/10                                  |
+====================================================================+
```

### Design 阶段 NOT in scope / What already exists

- **NOT in scope（设计）**：视觉 mockup 生成与反馈循环（designer API key 缺失；实施后 /design-review 视觉 QA 兜底）；深色模式专项适配（既有 themes.py 令牌覆盖，新令牌 --bg-log 需含深色值——义务行内）；动效设计（后台工作台克制原则，无新增动效）。
- **What already exists（设计）**：frontend/styles/design_tokens.py（CSS 变量令牌：颜色/字体/间距/圆角/阴影+深色覆盖）；FormField 组件（标签可见+三列网格样板）；q-card 统一样式（.q-card > div:first-child 标题规则）；sys-btn 按钮态（悬停亮度/禁用降透明）；ENABLE_PATHS 二级开关渲染模式；ui.notify 反馈模式；ui.timer 轮询模式（schedule/coordination 先例）。全部复用，零新造。


### 决策审计（Decision Audit Trail）

| # | 阶段 | 决策 | 分类 | 原则 | 理由 | 被否选项 |
|---|---|---|---|---|---|---|
| 1 | CEO | 复制布局选 edtalk/ 隔离子目录 | 机械 | P5 | 显式隔离防命名冲突，路径前缀统一 | 散装合并进 AUTOlive 根 |
| 2 | CEO | 音频链路"仅推送不本地播放" | 机械 | P1 | EDTalk 帧锁定播放是音画同步的正确架构；双播放是 bug | 对接纪要 A3 原文"并行播放"（过时） |
| 3 | CEO | 接受扩展：反应段联动 A4 | 机械 | P2 | 对接纪要既定项、<1d、爆炸半径内 | 延期 |
| 4 | CEO | 接受扩展：fastrag 模板一键填充 | 机械 | P2/P5 | S 级、零主程序改动 | 手动配置 |
| 5 | CEO | 接受扩展：训练前 GPU 检测 | 机械 | P1 | R3 最低成本缓解 | 不做 |
| 6 | CEO | 延期：产物自动接线/多角色/断点续训 | 机械 | P3 | 用户未要求或超出最小修复 | 纳入本期 |
| 7 | CEO | 拒绝：fastrag 热更新 | 机械 | P4 | 需改 Rust 源码，超出用户明示整合范围 | 纳入本期 |
| 8 | CEO | tab_key 保持 visual_body 不连锁改名 | 机械 | P5 | 显示名与 key 分离，改动面最小 | 全链改名 |

### 0I 时间轴拷问（实现就绪，2026-09-24）

- **HOUR 1（基础）**：大文件复制（ckpts/weights/chekpoint，数 GB）先行执行并同步 .gitignore；解释器硬编码 `D:\AI\EDTalk\runtime312\python.exe`。
- **HOUR 2-3（核心歧义）**：① playback_manager 两处 play（147、245）的分流钩子；② 单飞行句语义——`push_full` 立即返回 `processing:"async"`，"完成"判定须轮询 /status 或推理事件，HTTP 返回 ≠ 推理完成；③ `--segments` 三态传参（缺省/auto/列表）；④ character_dir 相对名解析基准（orifra/ 目录，段名大小写敏感）。
- **HOUR 4-5（集成惊吓点）**：① 预处理脚本 argv 签名不一致——第 11 步 `crop_head_video.py xx` 是**位置参数**，其余为 `--base_dir`（pipeline.py 逐脚本核对）；② 无 UnityCapture 滤镜环境须 `--no-unitycapture` 兜底；③ eval_finetune `--cand-ckpt` 需从 checkpoint 目录解析最新产物；④ fastrag 端口改动须同步 custom_llm 模板 URL。
- **HOUR 6+（打磨）**：训练 stdout 突发日志需行缓冲防 ui.timer 轮询丢行；webui 断线重连后训练页状态重新附着。

### CEO 双声部共识表 [subagent-only]（Codex provider 400 不可用，降级单声部）

```
CEO DUAL VOICES — CONSENSUS TABLE: [subagent-only]
  Dimension                           Claude(主)      Claude(子代理native)  Consensus
  1. 前提有效性                        实证核实        F3/F4 挑战(假设未验证)  N/A（单声部）
  2. 是否在解决正确的问题              是              F1 闭环断裂挑战        N/A（单声部）
  3. 范围标定是否正确                  是              F8 范围外拉回建议      N/A（单声部）
  4. 替代方案是否充分探索              已记录          F10 分期建议          N/A（单声部）
  5. 竞争/生态风险                     R1-R8           F13 北极星指标建议     N/A（单声部）
  6. 6 个月轨迹是否健全                是              F7 漂移挑战           N/A（单声部）
Codex 缺席：六单元一律 N/A，不标 CONFIRMED。native 13 项发现已逐项 0D 裁决（11 机械接受、
1 User Challenge F10 留最终门、1 部分延期）。
```

### Sections 1-11 审查（CEO 深审，2026-09-24）

**Section 1 架构**：系统拓扑——AUTOlive webui(8086) 经 start_programs() 拉起三服务（main.py 运行时 / realtime_serve:8000 / fastrag:11420），服务间 HTTP 隔离；音频数据流 happy path（弹幕→LLM→TTS→push_full→EDTalk 帧锁定播放→虚拟摄像头）；nil/error path（EDTalk 未启动→降级跳过+日志，直播继续）；empty path（空文本→既有 max_len 过滤后跳过）。新状态机：edtalk_client 单飞行句 idle→pushing→(轮询/status)→idle；重复 push 被"上一句未完成"门拒绝（合法过渡）。耦合点 playback_manager/synthesis_manager 各一处分支（enable 双检）。SPOF：EDTalk 挂→数字人画面静止但直播链路存活（可接受，有日志）；webui 挂→训练编排断（F8 步骤级重跑兜底）。回滚：全部为新增文件+分支语句，git revert 单提交回滚。发现 1 项：realtime_serve 拉起用 `-m realtime_serve.main` 时 sys.path 必须含 edtalk/ 根（cwd 已设 edtalk，python -m 语义下成立）——OK。无新 GAP。

**Section 2 错误与救援**：
```
METHOD/CODEPATH            | WHAT CAN GO WRONG          | EXCEPTION CLASS     | RESCUED? | USER SEES
edtalk_client#push_full    | 连接拒绝/超时               | ClientError         | Y 降级跳过+logger.error | 日志+UI 警告
edtalk_client#push_full    | 409/413 契约错误            | HTTPStatusError     | Y 记录响应体             | 日志
edtalk_client#单飞行句      | 上句 processing 卡死        | TimeoutError        | Y 轮询超时强制放行+告警  | 口型延迟日志
start_programs#edtalk      | 8000 占用/启动即退          | SubprocessError     | Y 幂等跳过+3s 存活检查(既有) | notify+日志
pipeline#步骤N             | 非零退出/OOM              | CalledProcessError  | Y 失败即停+回显+断点提示 | 日志区+UI
pipeline#步骤13            | 评估不过门槛                | ThresholdFail       | Y 默认停+确认继续        | 指标面板
build-index#构建           | knowledgebase 空/exe 缺失   | SubprocessError     | Y 回显 stderr           | notify
GAP 1 项：pipeline 子进程 stdout 编码（GBK 控制台 vs UTF-8 日志）未显式指定 → 实施时统一 encoding='utf-8', errors='replace'（行内义务，无阻塞）。
```

**Section 3 安全**：无新对外端口（全 127.0.0.1）；realtime_serve host 默认回环+api_token 可选（RUNBOOK E4 安全默认）符合预期；训练路径输入=用户本地目录（无注入面——subprocess 参数列表传参非 shell 拼接，实施时保持 list 形式禁 shell=True）；webui 0.0.0.0 暴露面为 TODOS 既有条目（#6），本计划不扩大。发现 0 项。

**Section 4 数据流与交互边界**：双启动（bat+webui）已幂等保护；训练中停止（P1 语义已定）；UI 断线重连（附着当前步骤日志）；fastrag 端口改动与模板 URL 不同步（C3 遗留观察，模板按钮一次性使用可接受，实施在 UI 提示中注明）。交互边界表：双击开始（防重入置灰✓）、训练中导航离开（后台线程存活✓）、8000 端口冲突（幂等✓）。无未处理缺口。

**Section 5 代码质量**：edtalk_client 照 AUDIO_PLAYER 既有模式（一致性✓）；pipeline 照 schedule/coordination 模式（✓）；新 tab 照 FormField/三列网格样板（✓）；无重复实现（对照 utils/ 无既有 HTTP 推送客户端）。0 项发现。

**Section 6 测试**：新 codepath 映射——edtalk_client（单测：mock HTTP 400/413/超时/单飞行句）、pipeline（集成：小样本 2 步预处理+50 iter 冒烟，即 F3 迷你冒烟）、启动接线（AC3/AC7）、音频链路（AC4 首日双端实测）、fastrag（AC9）。凌晨 2 点敢发布的测试=迷你训练冒烟+AC4 双端实测。既有 pytest 套件覆盖 config 合并（ensure-default 需补 1 个单测——义务行内备注）。失败路径测试：EDTalk 未启动降级、评估不过停、用户停止标记。

**Section 7 性能**：GPU 显存冲突（R3+F5 双检测已覆盖）；ui.timer 日志轮询频率（1s 级，行缓冲防丢——0I 已记）；push_full base64 内存峰值（10MB 上限×1 句，可忽略）。0 项新发现。

**Section 8 可观测性**：各环节 logger 已定义（edtalk_client 每次推送记 segment/耗时、pipeline 每步骤起止+exit code）；/status 回显区已是 day-1 仪表盘；训练步骤高亮=进度可观测。GAP：pipeline 无统一日志文件落盘路径——行内义务（logs/train_streamer/<日期>.log），无阻塞。

**Section 9 部署**：复制→.gitignore→冒烟→迷你训练冒烟→放开按钮，顺序明确；feature flag=enable 开关（默认 false 安全）；回滚=git revert+删除 edtalk/ 目录；deploy 风险窗口=复制期间 EDTalk 原目录不动（零影响）。0 项新发现。

**Section 10 长期轨迹**：唯一运行副本声明（F7）消除双份漂移；MANIFEST+freeze 快照=可追溯；可逆性 4/5（edtalk/ 目录整体可删，分支代码可 revert）；路径依赖=runtime312 硬编码（TODOS 既有 #3 解耦议题）；知识集中=本计划+MANIFEST+对接纪要三文档。Phase 2 方向：产物接线自动化→多角色→断点续训（TODOS 已登记）。

**Section 11 设计与 UX（UI scope=yes）**：信息架构——"数字人"分组（画面设置/训练主播）符合运营者心智模型（设备→工作流）；用户流：配置页（设置→保存→运行）与训练页（输入→确认→监视→应用）状态独立；交互状态覆盖——画面设置页（配置默认值✓/服务未启动警告✓/409 置灰✓）、训练页（进行中置灰✓/失败断点提示✓/空路径必填校验✓）、fastrag 卡片（/health 状态✓/构建中反馈需 spinner——行内义务）；AI slop 风险低（全部照既有设计令牌/三列网格样板，无通用模板感）；响应式（768px 断点既有规则适用）；无障碍（Quasar 基础兜底+TODOS #5 既有）。建议实施后跑 /design-review。0 项阻塞发现。

### Failure Modes Registry（CEO，规范化）

```
CODEPATH                    | FAILURE MODE         | RESCUED? | TEST? | USER SEES?      | LOGGED?
复制后入口冒烟               | 路径假设断裂          | Y(冒烟gate) | Y(迷你冒烟) | 冒烟报告标红     | Y
realtime_serve 拉起          | 8000 占用/启动即退    | Y(幂等+存活检查) | Y(AC3/AC7) | notify+日志  | Y
edtalk_client#push_full     | 连接失败/契约错误      | Y(降级跳过) | Y(单测mock) | 日志+UI 警告    | Y
音频分流条件                | enable=false 静音直播  | Y(双检+常驻警告) | Y | UI 常驻警告     | Y
pipeline 步骤               | 非零退出/OOM         | Y(失败即停+断点) | Y(迷你冒烟) | 日志区+断点提示 | Y
pipeline 步骤13             | 评估不过门槛          | Y(默认停+确认) | Y | 指标面板        | Y
pipeline stdout             | 编码乱码              | Y(utf-8 义务) | N(行内) | 日志可读        | Y
fastrag server              | 端口变更不同步         | Y(UI 提示) | Y(AC9) | notify          | Y
fastrag 模板                | 静默覆盖用户配置       | Y(确认+备份) | Y(AC9) | 确认框          | Y
CRITICAL GAPS: 0
```

### CEO Completion Summary

```
+====================================================================+
|            MEGA PLAN REVIEW — COMPLETION SUMMARY (CEO)             |
+====================================================================+
| Mode selected        | SELECTIVE_EXPANSION (autoplan 固定)          |
| System Audit         | 热文件=本计划触碰文件；无 stash/handoff；     |
|                      | 前端 batch2/改名/环境整合为直接前置工作       |
| Step 0               | SELECTIVE EXPANSION；接受3扩展/延期3/拒绝1；  |
|                      | spec loop 3轮收敛 9/10 PASS                  |
| S1  (Arch)           | 0 新 GAP（拓扑/数据流/状态机/回滚已核）       |
| S2  (Errors)         | 9 错误路径映射，0 CRITICAL GAP                |
| S3  (Security)       | 0 项新增暴露面；subprocess 禁 shell=True 义务 |
| S4  (Data/UX)        | 交互边界全覆盖（双启动/重入/断连）             |
| S5  (Quality)        | 0 issues（复用既有模式）                      |
| S6  (Tests)          | AC1-10 + 迷你训练冒烟；1 行内义务(单测)       |
| S7  (Perf)           | 0 新发现（显存冲突已双检测）                   |
| S8  (Observ)         | 1 行内义务（日志落盘路径）                     |
| S9  (Deploy)         | 复制→冒烟→放开按钮顺序；零影响窗口            |
| S10 (Future)         | 可逆性 4/5；唯一副本声明消除漂移               |
| S11 (Design)         | 0 阻塞发现（UI scope 跑完）                    |
+--------------------------------------------------------------------+
| NOT in scope         | written (7 项)                               |
| What already exists  | written（§2 现状核实 14 项复用映射）           |
| Dream state delta    | written（0C）                                |
| Error/rescue registry| 9 codepaths, 0 CRITICAL GAPS                 |
| Failure modes        | 9 total, 0 CRITICAL GAPS                     |
| TODOS.md updates     | 3 项已登记                                   |
| Scope proposals      | 7 proposed, 3 accepted                       |
| CEO plan             | written（~/.gstack/.../ceo-plans/）          |
| Outside voice        | codex unavailable（provider 400）[subagent-only] |
| Lake Score           | N/A（kind-only 决策）                        |
| Diagrams produced    | 音频数据流/单飞行句状态机/部署顺序（文字化）   |
| Stale diagrams found | 0                                            |
| Unresolved decisions | 2（F10 复制vs分期 User Challenge、F1 接线 taste）→ Phase 4 |
+====================================================================+
```

