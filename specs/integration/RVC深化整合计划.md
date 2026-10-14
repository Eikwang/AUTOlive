<!-- /autoplan restore point: "D:\\AI\\AUTOlive\\.gstack\\tmp\\autoplan\\main-autoplan-restore-20261014-155823.md" -->
## Implementation plan
# RVC 与歌曲翻唱深化整合计划

> 状态：**APPROVED**（2026-10-14 /autoplan 终门 `all`：plan1a+uc1a+C-5 立即插播+uc2a save_every=8+d1a 变声保留独立进程）
> 前置：GPT-SoVITS 深化整合已完成（45bb1104，E2E 验收通过），本计划为音频三项目深化的第二批次。
> **复刻面基准绑定**：RVC fork commit `4452ddd9`（integrate-baseline-20260902 tag）——14 条隐式契约（退出码/argv/命名/特判）全部绑定该版本，上游重大升级时本批复刻面作废重估（native 发现 5.2/3.2）。

## 1. 背景与目标

GPT-SoVITS 深化整合确立了"进程内器官+训练管线完整化"的整合范式（T-1~T-4 训练链、R-1 进程内引擎、步间资源清理、voice_profiles 档案机制）。本批次将同款范式应用到 RVC20240604Nvidia 与 AICoverGen，覆盖三个功能：

1. **RVC 训练**（数字人-训练声音-训练管线-RVC）：六步管线（声音处理→处理数据→特征提取→训练模型→特征索引→导出档案），默认值按用户规格固化。
2. **实时变声**（AI对话-变声）：整合 gui_v1.py 能力，消费 RVC 管线训练的模型。
3. **歌曲翻唱**（高级功能-点歌模式，新增歌曲翻唱模块）：提取 AICoverGen 一键翻唱，消费 RVC 管线训练的模型。

2026-09-25 音频三项目整合（P2/P3）已交付浅整合底座：变声三层服务（0.4s 热切换实证）、RVC 四步训练 CLI、翻唱队列状态机。本批次在既有底座上深化，不是重建。**差异化主张**（native 发现 5.1）：本批价值不在"能用 RVC/翻唱"（现成工具多），而在**无人值守闭环**——弹幕点歌命中→自动翻唱→成品入播放队列回播，全程零人工；且 TTS/变声/翻唱三端消费同一声音档案（一源三用），验收③据此含全链路时延/成功率记录。**供给双通道定位**（native 发现 1.2）：训练=声音身份定制；社区预训练模型导入=即时扩充（放入 models/rvc/ 即被变声/翻唱扫描消费，零集成成本）。

## 2. 勘察工程事实（2026-10-14，三路并行勘察）

### 2.1 RVC20240604Nvidia（训练+实时变声来源）

- **运行时**：fork 实际基准 = `D:\AI\EDTalk\runtime312`（Python 3.12.12 / torch 2.11.0+cu128，go-web.bat 证实）；项目内 `runtime/`（py3.9 嵌入式）已弃用但 AUTOlive 四处硬编码仍指向它。本地改造含 py3.12/torch2.11 兼容层（`infer/lib/torch_compat.py` weights_only 白名单）。
- **训练编排**：infer-web.py 五入口全是 Gradio 回调；除 `train_index`（进程内 numpy+faiss）外全部 `subprocess.Popen` 调脚本：`infer/modules/train/preprocess.py`（自带 slicer2 切分+16k/40k 双产物）、`extract/extract_f0_rmvpe.py`（f0，rmvpe_gpu）、`extract_feature_print.py`（HuBERT 特征，v2→3_feature768）、`train.py`（训练，**完成信号=退出码 2333333**）。
- **说话人 ID**：不经过"处理数据"，只在 `click_train` 生成 filelist 时拼行尾（默认 0）。
- **绕过 webui 编排必须复刻的隐式职责（14 条，勘察确认）**：
  1. `logs/{exp}/config.json` 必须先于 train.py 落盘（train 侧硬读），**v2+40k 特判共用 `configs/v1/40k.json`**（configs/inuse 无 v2/40k.json）；已存在不覆盖。
  2. `filelist.txt` 由编排层生成：四目录（0_gt_wavs/3_feature768/2a_f0/2b-f0nsf）文件名取交集（缺产物=静默丢样本）、路径 `\\` 转义、行尾拼 spk_id、追加 2 条 `logs/mute` 行、shuffle。
  3. 底模解析：v2+40k+f0 = `assets/pretrained_v2/f0G40k.pth` + `f0D40k.pth`（无 -v2 后缀）；缺失传空串=随机初始化仅 warning（必须预检拦截）。
  4. 产物目录约定：`0_gt_wavs`(40k)/`1_16k_wavs`(16k)/`2a_f0`/`2b-f0nsf`/`3_feature768`/`total_fea.npy`/`added_IVF{n}_Flat_nprobe_1_{exp}_{ver}.index`。
  5. index 推理端只认 `added_*` 前缀（文件名含 "trained" 被过滤），需 `os.link` 硬链接副本到 `assets/indices/`。
  6. 分片 argv 大小写契约：rmvpe_gpu 传 `"True"/"False"`（大写），feature 传 `"true"/"false"`（小写）；`gpus_rmvpe`="0-0"（单卡 2 进程），`CUDA_VISIBLE_DEVICES` 由子进程自设。
  7. Windows 单卡训练主进程直跑（fork 补丁规避 spawn+DDP access violation）；`-g 0`→`CUDA_VISIBLE_DEVICES=0`。
  8. 小模型落点 `assets/weights/` 平铺（无版本子目录），保存期命名 `{exp}_e{E}_s{S}.pth`；`-l 1` 时大 ckpt 固定名 `G_2333333.pth`。
  9. 子进程 cwd 必须是仓库根（脚本全用相对路径）；PATH 需前置仓库根（ffmpeg.exe）。
  10. `.env` 必须在 cwd（weight_root/index_root/outside_index_root 均来自它）。
  11. torch≥2.6 加载兼容：所有 torch.load 走 `safe_torch_load` 或先 `apply_safe_checkpoint_globals()`；独立子进程需各自调用。
  12. `Config()` 构造即 argparse.parse_args——宿主进程 argv 含未知参数直接 SystemExit；进程内构造需绕过。
  13. rmvpe_gpu 的 is_half 传字符串恒 fp32（上游行为，接受）；日志文件编排层先 truncate、子进程 append。
  14. f0 段与特征段在同一入口先 f0 后 feature 串行；f0 失败不阻断特征提取（产物交集机制兜底）。

### 2.2 AICoverGen（歌曲翻唱来源）

- 一键翻唱 = `src/main.py` `song_cover_pipeline()` 八步：下载（yt_dlp）→立体声化→三阶段 MDX 分离→RVC 换声→人声效果链（pedalboard）→整体变调（sox，仅 -pall≠0）→混音导出（pydub，成品 `{歌名} ({模型} Ver).mp3`）→清理。
- **分离引擎已复用**：`utils/train_voice_pipeline.py:_st_mdx` 进程内直调 `apps/aicovergen/src/mdx.py` + `models/mdxnet/` 资产——翻唱与训练共用一套分离，无需重建。
- RVC 推理是自带平行实现（`src/rvc.py` + `src/infer_pack/` + `src/vc_infer_pipeline.py`），不依赖 RVC20240604Nvidia 的 infer/lib。
- **可运行 runtime = RVC20240604Nvidia\runtime（py3.9 嵌入式，依赖全齐：fairseq 0.12.2/pedalboard/pydub/sox/yt_dlp/torch 2.0.0+cu118）**；AUTOlive 的 runtime312-clone 缺 fairseq/pedalboard/sox/yt_dlp 不可用。
- AUTOlive 侧已有 CLI 封装：`utils/cover_worker.py` 队列状态机（pending→running→done/failed/timeout，冷却/同观众上限/原子落位）+ `feature_handler.choose_song_handle` 异步入队。**翻唱链路通至模型供给前**（outside 发现 2 修正：共享模型落点 `_rvc_models_root`=rvc_vc.rvc_root/rvc_models 与消费端 AICoverGen根/rvc_models 两根不相交，含共享模型的翻唱任务必然失败——既有断点，C-2 修复）；零前端。
- **既有 bug（勘察发现）**：cover_worker 派发 CLI 未传 `-oformat`（main.py 默认 `mp3`），而 `_find_produced_wav()` 只匹配 `.wav`——落库命中未混音的 RVC 中间干人声而非最终成品。
- sox 不在系统 PATH（-pall≠0 时会失败；cover_worker 不传 -pall，主流程安全）。

### 2.3 AUTOlive 现状（P2/P3 浅整合产物盘点）

**可直接继承**：`service_orchestrator.py` ManagedProcess/ServiceRegistry（状态机/端口归因/E-1 校验）；`rvc_realtime_service.py` RealtimeVC 管线移植+事务热切换（0.4s 实证）；`svc.py` 变声页 UI 骨架；`voice_profiles.py` 数据层（E-4 原子写，9 字段已含 rvc_model/rvc_index）；`gpu_policy.py`（R-6 inproc 预留已是深化形态）；`cover_worker.py` 队列状态机与 9 个测试；`copy_audio_projects.py` 清单机制（rvc/aicovergen 已在 items 表）。

**应退役/重写（9 项）**：
1. `train_rvc_pipeline.py:205-223` 档案挂接段：dir() 死代码、绕过 upsert_profile 单写者契约、异常静默吞掉。
2. 变声参数链断裂：前端写 `rvc_vc.pitch/protect`（svc.py:151-167）但服务端不消费（内置默认、/switch 无 pitch 参数）——改了不生效。
3. 翻唱 profile_id 语义错位：`feature_handler.py:327` 拿 `gpt_sovits.voice_profile_id`（TTS 档案）当 RVC 档案用，rvc_model 全空回退取第一个 pth。
4. rvc_root 硬编码 ×4 分散（rvc_service.py:28,43 / train_rvc_pipeline.py:38 / cover_worker.py:73）。
5. apps/rvc、apps/aicovergen 副本闲置：运行时全部走外部树（与 S-1 装后形态承诺不符）；变声消费的还是旧版 `tools/rvc_for_realtime.py`（无 formant），fork 新版 `infer/lib/rtrvc.py` 未用。
6. 旧 so-vits-svc 活口：`so_vits_svc.enable=true` + synthesis_manager 仍消费。
7. 翻唱零前端：choose_song.py 无翻唱控件/队列状态展示。
8. config 杂质：voice_profiles 同级 `"profiles": []`。
9. RVC 训练管线无资产预检/无步间清理/参数硬编码（-se 5；对比 GSV 深化模板七项工程能力全缺）；且训练子进程仍用已弃用的 py3.9 嵌入式 runtime。

**其他事实**：`models/rvc/` 已有 wy.pth+index（未挂档案）；voice_profiles 现有 soundtest×2（仅 GSV 字段）；RVC 专项测试为零；`test_gptsovits_deep_integration.py`（13 测试）是测试包模板；GPU 准入三调用点已有，实时变声无准入（关键路径优先侧，保持）。

## 3. 用户规格固化（默认参数表）

| 项 | 值 | 来源 |
|---|---|---|
| 训练 batch_size | 前端可调（RVC webui 默认=显存//2，AUTOlive 默认 8，沿用现状） | 现状+勘察 |
| total_epoch | 20 | 用户规格 |
| save_every_epoch | 8（终门 uc2a 已确认） | 用户规格 |
| 模型是否带音高指导 (f0) | true | 用户规格 |
| 版本 | v2 | 用户规格 |
| 目标采样率 | 40k | 用户规格 |
| CPU 进程数（提取音高和处理数据） | 10 | 用户规格 |
| 音高提取算法 | rmvpe_gpu | 用户规格 |
| 说话人 ID | 0 | 用户规格 |
| 实验名 | 前端设置，voice_profiles 统一 | 用户规格 |
| 翻唱 index_rate / f0 | 0.75 / rmvpe（cover_worker 现状） | 现状 |
| 变声 f0method | rmvpe（现 rmvpe；rtrvc 支持 fcpe，F-2 暴露） | 现状 |

## 4. 任务分解

### S 结构与副本
- **S-1 RVC 运行副本正式化**：config 新增 `paths.rvc_root`（默认 apps/rvc）+ `paths.rvc_python`（默认 `D:\AI\EDTalk\runtime312\python.exe`，RVC fork 实测基准）；4 处硬编码收敛进 AutolivePaths；Scripts/copy_audio_projects.py 的 rvc 项补 link_items（assets/pretrained_v2、assets/hubert、assets/rmvpe、**logs/mute（filelist mute 行依赖，备注收口）**等 big 资产 junction，对齐 GSV S-1 模式）并执行迁移；**`.env`（weight_root/index_root/outside_index_root 三键）与 `ffmpeg.exe` 一并复制入副本根**（子进程 cwd=副本根的相对路径依赖）；AICoverGen 副本核对（apps/aicovergen/src 已逐字节一致，补 mdxnet_models/rvc_models junction 或复制核对）；**fork 基准记录+漂移 tripwire（复核发现 5）**：S-1 记录 fork 基准 commit 4452ddd9 并对 14 条契约涉及的 fork 文件做哈希清单入库（一次性近零成本），check_assets() 启动比对，不一致告警"复刻面基准已漂移"（纪律→tripwire）。
- **S-2 runtime 矩阵固化**：RVC 训练+变声 = runtime312（rvc_python）；翻唱 CLI 保持 py3.9 嵌入式 runtime，**解释器路径由显式 config 键钉住（outside 发现 3：cover_worker.py:80-83 默认派生 `{rvc_root}/runtime/python.exe` 在根翻转后会指向不存在的 apps/rvc/runtime——`rvc_vc.runtime_python`（或 paths.cover_python）显式=D:\AI\RVC20240604Nvidia\runtime\python.exe）**；翻唱的 RVC 模型目录发现跟随 aicovergen 副本根（发现 2）。**执行环境矩阵**（P-10）：①编排进程=AUTOlive 主 python（runtime312-clone，T-1 分离/T-5 索引在此进程内跑，faiss-cpu 1.14.3 已装、sklearn 随本批预检缺则装）②训练子进程（T-2/T-3/T-4）=runtime312（EDTalk）③翻唱 CLI 子进程=py3.9 嵌入式④**变声服务子进程（rvc_realtime_service，R-1）=runtime312**（sounddevice/FreeSimpleGUI 依赖随迁确认，0I HOUR 4-5 收口）。

### T RVC 训练管线（重写 train_rvc_pipeline.py，对齐 GSV 深化模板）
- **T-0 runtime312 训练 spike（native 发现 2.1，开工前置）**：用 fork 自带脚本在 runtime312 下最小实跑一次训练到出 ckpt（preprocess 小样本+train 1-2 epoch），实证 py3.12/torch 2.11 下六步链路前提；spike 失败则整批重估（回 py39 应急或等 fork 修复），不在未验证地基上盖 14 条复刻。spike 记录时长/显存作 T-4 估算校准。
- **T-1 声音处理步**：复用 GSV `_st_mdx` 三阶段（mdx_vocals 落位语义一致）；already_dry 跳过；步后 `_release_local_gpu`+`_wait_vram_release`。
- **T-2 处理数据步**：`preprocess.py <dataset> 40000 10 <logs/exp> False 3.7`；预 truncate preprocess.log；产物计数断言（0_gt_wavs/1_16k_wavs 非空）兜底上游 except 吞错。
- **T-3 特征提取步**：先 f0 后 feature 串行。f0：`extract_f0_rmvpe.py <n_part> <i_part> 0 <logs/exp> True`（**逐参数语义**：n_part=gpus_rmvpe="0-0" 段数派生=2 分片、i_part=逐分片派发 0/1、第三参=i_gpu=0、is_half 传字符串 "True" 大写契约且上游恒 fp32；**与规格 10 的映射**：CPU 进程数 10 仅作用于 T-2 preprocess 的 n_p（上游 rmvpe_gpu 分支不消费 n_p，勘察事实 3），无冲突）；feature：`extract_feature_print.py cuda 2 0 0 <logs/exp> v2 false`（小写契约，v2→3_feature768）；产物计数断言。
- **T-4 训练步**：①config.json 模板覆写（v2+40k→configs/v1/40k.json 蓝本，写入 logs/{exp}/config.json）②filelist 生成（四目录交集+路径转义+spk_id=0+mute 2 行+shuffle）③`train.py -e exp -sr 40k -f0 1 -bs 8 -g 0 -te 20 -se 8 -pg f0G40k.pth -pd f0D40k.pth -l 1 -c 0 -sw 1 -v v2`（**-c 0 维持现状**：cache fp16 抬显存峰值且用户规格无此项，outside 发现 8）；完成判定=退出码 2333333；资产预检拦截底模缺失（空串=随机初始化陷阱）。
- **T-5 特征索引步**：进程内复刻 train_index（合并 3_feature768→>2e5 时 MiniBatchKMeans 降采样→n_ivf=min(16√N, N//39)→faiss IVF,Flat→total_fea.npy+added_IVF{n}_Flat_nprobe_1_{exp}_v2.index→os.link 到 assets/indices）。
- **T-6 导出步**：**导出取训练终态无后缀 `{exp}.pth`**（train.py:636-650 训练结束另存=完整 20 epoch 模型；带后缀 `{exp}_e{E}_s{S}.pth` 仅 epoch%save_every==0 时存=e8/e16 中间点（train.py:581），作断点续训保留——outside 发现 4 更正：te20/se8 不存在 e20 带后缀文件，按"最高 epoch 解析 _e(\d+)_s"会落选终态文件导出 e16 欠训练模型；现行 :201 的 G_* glob 在 -l 1 下永不匹配=现行迁移步必然失败的又一既有 bug）；终态模型+added index 配对迁移 `models/rvc/`；`voice_profiles.upsert_profile` 单写者接线（rvc_model/rvc_index 字段）；删除 dir() 死代码段；export 后 notify"新声音已就绪，可在变声页切换"（delight 1 并入）；**py39 翻唱 runtime 双冒烟（P-11+outside 发现 5）**：torch.load 权重头 + `faiss.read_index`（消费端 faiss-cpu 1.7.2 vs 写入端 1.14.3，vc_infer_pipeline.py:498-510 对 index 读取失败是静默降级 index=None 继续转换——只测 .pth 测不出检索失效）。
- **T-7 管线骨架**：`check_assets()` 资产预检（hubert/rmvpe/f0G40k/f0D40k/v1-40k.json/configs inuse/**副本根 .env 三键存在性**/**rvc_python 指向的 python.exe 存在性（CMP-1，0I HOUR 1 收口）**/**轻量指纹校验（torch.__version__ 级，native 发现 2.3：预检失败提示"外部环境可能已变更，检查 fork 是否升级"）**）；`_cleanup_after_step` 步间 taskkill /F /T+显存回落轮询（GPU 残留显存教训直迁；**Eng 裁决：清理/GPU 门从 utils/train_process_utils.py import（本批新建，自 train_voice_pipeline.py:285-298,327-333 提取，GSV 侧同步改 import，净省 ~55 行+单一修复点）**）；`DEFAULTS` 集中+`prepare()` 全参数化；`_plan()` 动态化（already_dry→mdx SKIPPED）；authorized 断言；每步 GPU 准入门。**大 ckpt 磁盘处置**（备注收口）：`-l 1` 产物 G_2333333.pth 留在 logs/{exp}/ 不断点不删（断点续训保留），export 后仅在日志注明。

### R 实时变声（保留三层服务架构，深化）
- **R-1 推理类升级**：rvc_realtime_service 消费点从旧版 `tools/rvc_for_realtime.py`（无 formant）升级为 fork 新版 `infer/lib/rtrvc.py`（formant+fcpe+last_rvc 复用链完整）；服务子进程 runtime 迁移 runtime312（S-2）；**启动器 rvc_service.py 一并处置**（P-4：仅 python 命令随 rvc_python 收敛，服务注册关系不变）；`Config()` 构造前清 sys.argv（argparse 陷阱）；harvest 不用（n_cpu=1，f0method 走 rmvpe/fcpe，不建 multiprocessing 队列池）。
- **R-2 参数链修复**：/params 端点（或 /switch 扩展）接受 pitch/formant/index_rate/protect/f0method 热更新，映射 rtrvc 的 change_key/change_formant/change_index_rate；前端写入值真实生效。
- **R-3 档案接入**：svc.py 模型列表改读 voice_profiles（rvc_model/rvc_index 非空档案优先展示）+models/rvc/ 目录扫描兜底；点选热切换走档案对；参数面板按 R-2 端点接线；**社区模型导入说明**（native 发现 1.2）：放入 models/rvc/（.pth+.index 同前缀）即被扫描消费，变声/翻唱页即时可见，挂档案可选（空态卡指引文案）；**兼容性防护（复核发现 3）**：扫描时读模型头（version/sr）在 UI 标注兼容性，加载失败按单模型粒度隔离上报（不拖垮切换事务），TST-1b 补失配模型加载回归。
- **R-4 遗留收敛**：`voice_profiles` 杂质键 `"profiles": []` 退役；so_vits_svc 活口退役评估（config enable 置 false+synthesis_manager 调用点确认，若直播链在用则仅标注），**退役时点=下一整合批次（native 发现 3.3，非开放式后置）**。

### C 歌曲翻唱（既有队列深化）
- **C-1 成品 bug 修复**：cover_worker 派发加 `-oformat wav`；`_find_produced_wav()` 兜底匹配 `" Ver)."` 成品名（不再命中中间干人声）。
- **C-2 档案语义修复**：翻唱选型改读 RVC 专属语义（voice_profiles.rvc_model/rvc_index 优先→models/rvc/ 扫描兜底）；feature_handler.py:327 停止借用 gpt_sovits.voice_profile_id，选择值持久化键=config `choose_song.cover_profile_id`（C-3 前端写、本处读，outside 发现 7）；**沿用既有 `_share_model_to_covergen` 共享通道并修正落点基根（outside 发现 2，blocking）**：cover_worker.py:71-74 `_rvc_models_root()` 现派生自 `rvc_vc.rvc_root`（默认 D:\AI\RVC20240604Nvidia\rvc_models——目录不存在）而消费端 main.py:31-34 读 `AICoverGen根/rvc_models`，写读两根永不相交=共享模型翻唱必然失败（既有断点）；基根切换到 aicovergen 根（apps/aicovergen/rvc_models，S-1 junction 对齐），CLI 传参契约 `-dir autolive_{profile}` 不变。
- **C-3 前端翻唱模块**：choose_song.py 新增"歌曲翻唱"模块——队列状态卡（pending/running/done/failed/**timeout（归并 failed 色系展示，CON-3）**+当前任务）、翻唱参数（音色档案选择→写 config `choose_song.cover_profile_id`、音高）、成品列表（song/covers/），**EMPTY 态引导文案**（"点歌命中后将自动翻唱"，S11 收口）。翻唱链路全程对用户可见。**failed 态展示 reason（DX Pass 3）**：失败原因可读化（模型缺失/显存不足/GPU 准入等待/超时），对齐 GSV 失败四要素范式。**音高参数映射（outside 发现 1 更正，blocking）**：暴露的"音高"接到 AICoverGen `-p/--pitch-change`——**CLI -p 单位本身就是八度（main.py:330 help "(Octaves)"），AICoverGen main.py:292 内部完成 ×12 半音换算；前端八度值直接传 `-p`，不得预乘 12**（预乘会 144 半音）。默认 0，**不接 `-pall`**（sox 依赖未装，§5 已排除）。**设计规格（Design P1/P4/P7）**：三块内容（状态/参数/成品）用**折叠分组**承载（对齐 train_voice.py 参数分组折叠范式，防卡片墙堆叠）；音高滑杆 -12~+12 八度步长 1（AICoverGen webui 同款范围，超出传值钳制）；成品列表空态与队列 EMPTY 态共用引导文案；对齐设计令牌。
- **C-5 翻唱成品自动回播（复核发现 1，终门已裁决 uc1a=立即插播）**：翻唱 done 后自动写入 song 播放队列回播（复用 cover_worker 既有冷却/同观众上限语义做播控）；**插播时机=完成后立即插播**（完整零人工闭环；需处理与 TTS 对话流的播放通道互斥——播放通道互斥策略随实施细化：排队等待当前播报完成 vs 打断，倾向排队）；验收③="命中→自动播报"。
- **C-4 cover_worker 根收敛**：aicovergen root → config paths（apps/aicovergen）；模型目录发现跟随 S-1 副本。

### F 前端
- **F-1 训练页 RVC 卡**：专属参数面板，**两组归属明确（复核发现 8）**——可调组：batch_size/total_epoch 20/save_every 8（epoch 是最常调旋钮不固定）；固定展示组：采样率 40k/版本 v2/f0 true/CPU 进程数 10/音高算法 rmvpe_gpu/说话人 ID 0。双管线切换时参数区重排（现仅复用 GSV 输入框）；**数据集输入与实验名字段归属说明**（P-9）：数据集目录输入卡与实验名输入为双管线共用（RVC 管线读同一数据集卡+实验名写入 voice_profiles slug ID，说话人 ID 固定 0 不暴露控件）；GSV 深化后的资产预检 notify/失败四要素 UI 对 RVC 同款。**设计规格（Design P1/P2）**：RVC 参数卡与 GSV 卡同位替换（非并存堆叠，管线切换按钮即重排触发）；切换时各管线表单值独立保留（切走再切回不丢输入）；RVC 卡内部分组折叠对齐 train_voice.py 既有参数分组范式。
- **F-2 变声页深化**：R-3 档案选择+R-2 参数面板接线（pitch/formant/index_rate/protect/f0method）+formant 滑杆（rtrvc 新能力）。**设计规格（Design P1/P2/P6）**：档案列表=既有卡片网格范式（档案优先分组+扫描模型兜底分组，分组标题区分来源）；**滑杆量程**（Pass 7）：pitch -16~+16 半音（gui_v1 同款）、formant -2.0~+2.0 步长 0.05（rtrvc 契约）、index_rate 0~1 步长 0.01、protect 0~1 步长 0.01；变声页档案列表 LOADING 态=扫描中骨架占位（models/rvc 目录+档案读取期间）；对齐设计令牌（CSS 变量/宽度三档，禁硬编码色值）。

### TST 测试与验收
- **TST-1 单测包** `tests/features/test_rvc_deep_integration.py`（模板=test_gptsovits_deep_integration.py），按实施顺序分两批落地（P-5）：
  - **TST-1a（随 T 系列落地）**：DEFAULTS 固化断言（20/8/40k/v2/f0/10/spk0）、config.json 特判（v2+40k→v1/40k.json）、filelist 生成（交集/mute 行/转义/spk_id）、added_* 命名与 trained 过滤语义、rc=2333333 完成判定、分片 argv 大小写契约、索引复刻（n_ivf 公式/降采样阈值）、档案单写者接线、.env 三键预检、**导出命名模型（outside 发现 4：终态无后缀 {exp}.pth 优先于 e8/e16 带后缀中间点，覆盖无后缀终态文件断言）**。
  - **TST-1b（随 R/C 系列落地）**：-oformat 修复回归、参数链端点契约（/params pitch/formant/index_rate/protect/f0method→rtrvc 方法映射）、**翻唱音高参数映射（outside 发现 1 修订：用户 1 八度→派发 `-p 1` 直传，断言无 ×12 预乘、无 -pall）**、**翻唱档案选型契约（CMP-2：rvc_model/rvc_index 优先→models/rvc/ 扫描兜底、不再读 gpt_sovits.voice_profile_id、_rvc_models_root 基根=aicovergen 根）**。
- **TST-2 E2E 实跑**：tests/sound 素材（GSV E2E 同源）跑 RVC 六步全链路，分两阶段（P-5）：
  - **TST-2a（训练链，T-6 后）**：声处理→处理数据→特征→训练→索引→导出；产物 models/rvc/{exp}.pth+index；voice_profiles 自动写入 rvc_model/rvc_index；py39 翻唱 runtime load 冒烟（T-6 内置项实跑验证）。
  - **TST-2b（变声+翻唱，R/C 系列后）**：变声服务（runtime312）加载新模型冒烟+参数热更新生效；翻唱成品验证（最终混音 wav 含伴奏，非干人声）。
- **验收标准**：①训练六步全链路跑通且步间显存回落（复用 GPU 教训判据：功耗形态非 util）②变声页可选档案、参数热更新生效、切模事务语义保持③点歌命中→翻唱成品正确（混音完整）→前端可见，**手测记录点歌命中→成品可播的全链路时延与成功率（差异化主张的验收数字，native 发现 5.1）+受众侧度量两项（复核发现 2）：每场直播翻唱点歌命中数、成品完成/播出数（cover_worker 日志落地，为下一批供给投入裁决提供依据）**④TST-1a/TST-1b 全过⑤旧路径（py39 训练/借用 TTS 档案/参数不生效）全部关闭。

## 5. NOT in scope

- AICoverGen 的 yt_dlp 在线下载（S-3 拒绝弹幕 URL 裁决延续；仅本地音频输入）。
- 翻唱整体变调 -pall（sox 依赖未装，主流程不触发；标注后续项）。
- RVC 多卡 DDP 训练（单卡场景，fork 补丁即主进程直跑）。
- harvest f0 算法实时变声支持（multiprocessing 池不迁移；rmvpe/fcpe 覆盖）。
- runtime312-clone 补装 AICoverGen 依赖（翻唱保持 py39 runtime）——**成本对比**（native 发现 4.3）：补装 fairseq/pedalboard/sox/yt_dlp 四包为一次性成本（fairseq 0.12.3.1 在 EDTalk\runtime312 已实证可装），可消灭 P-11 跨 runtime 赌注+T-6 双冒烟对价；本批保持 py39（避免 C 系列连锁改动+双冒烟已防护），**统一绑定触发条件（复核发现 6）：本批 TST-2b 通过即列入下一批首项；翻唱端再发生任一跨 runtime 兼容事件时提前启动**——若统一则 P-11/发现 5 防护项整体退役。
- so_vits_svc 完整退役迁移（仅评估+标注，直播链在用则后置）。
- 变声服务进程内化进 main.py（音频设备捕获-回放循环天然独立进程，保留三层架构；与 GPT-SoVITS TTS 进程内化不同构，理由见 §6 决策 D1）。

## 6. 关键决策与风险

- **D1 变声保留独立进程架构**（非进程内化）：TTS 进程内化是因为对话链在 main.py 内直接消费生成器；变声是 sounddevice 输入设备独占+实时音频回调，独立进程是音频设备安全边界（设备崩溃不拖垮核心进程），且 rvc_realtime_service 已实证 0.4s 热切换。深化=推理类升级+参数链修复+档案接入，不动进程拓扑。
- **D2 训练 runtime 迁 runtime312**：RVC fork 本地改造（py3.12 兼容层+torch_compat）全部针对 EDTalk\runtime312 基准；py3.9 嵌入式已被 fork 弃用。风险=①实时变声在 runtime312 下端到端未验证（RVC 项目 TODOS 自述），TST-2b 覆盖；回退半径（CON-2）：**训练侧=rvc_python 回切 py39（outside 发现 6：该预案未经任何测试覆盖，仅应急级——fork 的 torch_compat 兼容层全针对 runtime312）；变声侧=rvc_python 回切+推理类同步回退旧版 tools/rvc_for_realtime.py（R-1 升级时保留旧版文件不删）**②**跨 runtime 模型兼容**（P-11）：训练产物 torch 2.11 产出、翻唱端 py39 torch 2.0 加载——T-6 内置 py39 双冒烟（.pth load+faiss.read_index），不兼容则提前暴露（翻唱端回退或 runtime 统一提前）③**共享环境依赖（outside 发现 6+native 发现 3.1）**：EDTalk\runtime312 为多项目共享环境（RVC fork TODOS 自述警告共享破坏半径），本批使 AUTOlive 训练+变声成为第三共享依赖方，破坏半径覆盖全部三条链；缓解=check_assets 存在性+指纹预检+rvc_python 可配置+**runtime312 pip freeze 快照入库（破坏后 30 分钟内重建）**；**独立 venv 拆两条（复核发现 4 消歧）**：T-8a 训练链独立 venv=本批 E2E 验收通过后即启动；T-8b 变声链最小 venv=defer（触发条件=T-8a 落地后下一批次，或共享环境首次实际破坏时提前）。
- **D3 步间资源清理直迁**：GSV E2E 的教训（残留显存→WDDM 换页慢 10-20 倍）同样适用于 RVC 训练（preprocess 多进程/f0 双进程/训练主进程均为独立子进程树）。
- **D4 save_every=8 歧义**：按 save_every_epoch=8 理解（RVC webui 该值默认 5，用户显式给 8 即为 save 间隔），**终门 uc2a 已确认**。
- **D5 翻唱 profile 语义**（P-7 回写）：现状借用 gpt_sovits.voice_profile_id 当 RVC 档案（feature_handler.py:327），rvc_model 全空回退取第一个 pth；改为 RVC 专属语义（rvc_model/rvc_index 优先→models/rvc/ 扫描兜底），bug 修复级裁决。
- **D6 编排路线=CLI 复刻，非 gradio_client 驱动 webui**（outside 发现 9 补记录+native 发现 4.1 量化）：量化对比——CLI 复刻面=14 条隐式契约的**永久维护负债**（fork 升级时需逐条再校验，file:line 出处已落 §2.1 供追溯）vs webui 驱动=**1 个常驻进程+Gradio API 脆性面**（API 无稳定性承诺+进程管理负担）。"范式一致性"是惯性论证非决定理由；决定理由=API 脆性不可控 vs 契约负债可控（有出处可校验）。fork 基准 commit 4452ddd9 已在头部绑定。**第三备选否决记录（复核发现 7）**：fork 内置 `cli_train.py` 编排脚本（契约落 fork 内代码、升级时随代码更新）——否决理由=本批保持 fork 零改动以维持升级自由度+外部复刻有解耦优点；**登记为 fork 重大升级触发复刻面重估时的首选方案**。
- **风险**：junction 边界（RVC assets 内 .gitignore 文件已删，注意 git 状态）；preprocess 多进程在 n_p=10 下的 spawn 开销；filelist 交集静默丢样本（T-2/T-3 产物计数断言前置拦截）。

## 7. 实施顺序

**Day-1 价值前置**（native 发现 1.1）：消费冒烟（既有 wy.pth→变声热切换→翻唱成品，实证消费端可行性）→ **C-1/C-2 现存 bug 修复**（翻唱落库错误+档案语义，用户价值立竿见影）→ **T-0 spike**（runtime312 训练实跑冒烟，native 发现 2.1）→ S-1/S-2 → T-7 骨架 → T-1~T-6 六步 → TST-1a → TST-2a（训练链 E2E）→ R-1~R-4 → C-4 → C-3 → F-1/F-2 → TST-1b → TST-2b（变声+翻唱验证）→ 全量回归。（P-5/P-6/CLA-2：TST 按依赖拆分两批；C-4 在 C-3 前收口副本根语义；R 系列可与 T 系列并行推进）


<!-- autoplan-accepted:ceo -->
- 用户规格 §3 全表固化：total_epoch=20/save_every=8（D4 终门确认项）/f0=true/v2/40k/CPU 进程数=10/rmvpe_gpu/spk_id=0/实验名前端设置走 voice_profiles；验证=TST-1a DEFAULTS 断言。
- 三功能范围：RVC 六步训练管线（T-1~T-7，对齐 GSV 深化模板七项工程能力）、实时变声保留三层架构+推理类升级 rtrvc+参数链闭合+档案接入（R-1~R-4）、歌曲翻唱成品修复+档案专属语义+前端模块（C-1~C-4）；验证=TST-1a/1b+TST-2a/2b。
- S-1 副本迁移含 .env 三键+ffmpeg.exe+logs/mute 落位、rvc_models 基根=aicovergen 根（outside 发现 2）；S-2 执行环境矩阵四行+翻唱解释器显式 config 键（outside 发现 3）。
- 导出规则=训练终态无后缀 {exp}.pth 优先（outside 发现 4）；T-6 py39 双冒烟（.pth+faiss.read_index，outside 发现 5）；T-4 -c 0 维持现状（outside 发现 8）。
- 翻唱音高映射=-p 直传八度值（outside 发现 1）；档案选择持久化键=config choose_song.cover_profile_id（outside 发现 7）。
- NOT in scope 七项（§5）+cherry-pick 否决四项（0G）为范围边界；D1（变声保留独立进程）/D2（runtime312+三重风险缓解）/D5（翻唱档案语义）/D6（CLI 复刻路线）为裁决记录。
- 验收标准五条（§4 TST 节）：训练链跑通+步间显存回落、变声档案/参数生效、翻唱成品正确+前端可见、TST 全过、旧路径全关闭。
<!-- /autoplan-accepted:ceo -->
## Review record

### CEO Step 0（2026-10-14）

#### 0A Premise Challenge

| premise | claim | receipt | verdict |
|---|---|---|---|
| 三端声音身份断裂 | 变声页写 pitch/protect 服务端不消费 | rvc_realtime_service.py:62-75（内置默认、/switch 无 pitch 参数） | verified |
| 翻唱成品落库错误 | CLI 默认 mp3，finder 只匹配 .wav | cover_worker.py:212-216 + :303；AICoverGen main.py:246 | verified |
| 训练管线缺深化能力 | 无预检/清理/参数硬编码/-se 5 | train_rvc_pipeline.py:172-183（-se 硬编码）、:205-223（dir() 死代码） | verified |
| 训练子进程用弃用 runtime | py3.9 嵌入式，fork 已迁 runtime312 | train_rvc_pipeline.py:38 硬编码 rvc_root\runtime；RVC fork go-web.bat:1 | verified |
| 变声消费旧版推理类 | tools/rvc_for_realtime 无 formant | rvc_realtime_service.py:44-48 import 旧版；gui_v1.py:93 用 rtrvc | verified |

真实痛点=用户要在三端（TTS/变声/翻唱）消费同一声音身份，RVC 端与翻唱端是断点。计划直接解决（训练管线深化→模型产出可靠；参数链闭合→用户改的值生效；档案贯通→三端统一；翻唱 bug 修复→成品正确），非代理指标。do-nothing 成本：RVC 训练重演 GSV 残留显存教训（十几个小时换页）；变声静默失败；点歌回播未混音干人声；4 处硬编码环境迁移即断。

#### 0B Existing Code Leverage

重建为零：训练管线模板=GSV train_voice_pipeline.py 深化形态；分离引擎=apps/aicovergen/src/mdx.py 已进程内复用（T-1 零新建）；变声=rvc_realtime_service 保留架构仅升级推理类；翻唱=cover_worker 队列仅修 bug 与接线；档案=voice_profiles 数据层不动仅消费；测试=GSV TST-1 模板复刻。already-done 检查：origin/main 自 2026-10-13 以来的 GSV 批次（45bb1104）不含本计划命名文件（train_rvc_pipeline/svc/cover_worker 均未在近期触碰），无重叠实施。

#### 0C Dream State Mapping

```
CURRENT STATE                      THIS PLAN                       12-MONTH IDEAL
GSV 深化闭环(训练+TTS)      --->   RVC 端对齐深化标准        --->  声音身份生产线完全闭环
RVC 浅整合(训练 CLI 粗糙、          六步训练管线+变声参数链闭合+     录音→一次训练→TTS/变声/翻唱
参数链断、消费外部树)               翻唱成品正确且可见；三端消费      三端同时可用；弹幕点歌自动翻唱
翻唱后端半通(队列在、前端无、        同一 voice_profiles
成品 bug)
```

#### 0E Mode（auto-decided）

SELECTIVE EXPANSION——既有底座保留（三层变声服务/翻唱队列/档案数据层）+深化能力新增（训练管线/参数链/翻唱前端）。~14 文件（估计）≤15 不触发 REDUCTION；非 greenfield 非 fix/refactor。

#### 0G Cherry-pick（delight scan 裁决）

| 候选 | effort | 裁决 |
|---|---|---|
| 训练完成 notify"新声音已就绪" | S | 并入 T-6（export 后一行 notify，零新增任务） |
| 翻唱进度百分比 | M（gr.Progress CLI 下 no-op，需日志解析） | NOT in scope |
| 变声页试听按钮 | S | NOT in scope（需新推理端点，超出变声服务现有面） |
| 翻唱成品试听 | S | NOT in scope（点歌回播通道已覆盖核心） |
| 多档案批量重跑索引 | M | NOT in scope |

HOLD 检查：复杂度必要性成立（T 系列为用户显式规格；R-2/C-1/C-2 为真 bug）；可延期项（formant 滑杆→F-2 顺带暴露 rtrvc 能力，随任务不单独存在）；invariants 保持（0.4s 热切换语义/E-4 单写者/E-1 校验/GPU 准入）。

#### 决策 ledger

| ID | Contract | Current | Proposed | Status |
|---|---|---|---|---|
| D1 | 变声进程拓扑 | 三层服务独立进程（9391 控制面） | 保留独立进程+推理类升级（非进程内化） | taste→终门（用户可能类比 GSV 期待进程内化；工程理由：sounddevice 设备流为独立进程安全边界，TTS 拉模式 vs 变声推模式本质不同） |
| D2 | 训练 runtime | py3.9 嵌入式（弃用） | 迁 runtime312（rvc_python 可配置回切） | auto（fork 本地改造全部针对 runtime312；py39 被 fork 弃用） |
| D4 | "total_epoch默认8"歧义 | 按 save_every_epoch=8 理解 | 同 current | taste→终门确认（RVC webui 默认 5，用户显式给 8） |
| D5 | 翻唱 profile 语义 | 借用 gpt_sovits.voice_profile_id | RVC 专属语义（rvc_model/rvc_index 优先→目录扫描兜底） | auto（bug 修复） |

#### 0I Temporal Interrogation

- **HOUR 1（基础）**：S-1 junction 迁移前确认 RVC fork git 状态（22 modified+2 deleted .gitignore——副本迁移不受影响但记录基线）；rvc_python 默认值需 EDTalk\runtime312 存在性预检。
- **HOUR 2-3（核心歧义）**：T-4 filelist 生成时 0_gt_wavs 与 1_16k_wavs 文件名对应（{srcidx}_{slice} 双目录同名约定）；T-5 索引的 sklearn 在 runtime312 可用性未验证（faiss-cpu 1.14.3 已验证）→ 实施先验证，缺则随 S-2 pip 装；rc=2333333 正常完成信号与异常退出判别。
- **HOUR 4-5（集成意外）**：R-1 构造 Config 前清 sys.argv（argparse SystemExit 陷阱）+is_half/use_jit 固定；变声服务迁 runtime312 后 FreeSimpleGUI/sounddevice 仍需（服务保留音频流）；py3.9→3.12 的 librosa API 差异由 fork 本地改造覆盖（requirements.txt runtime312 基准）。
- **HOUR 6+（打磨）**：TST-2 复用 tests/sound 歌曲素材（mdx 后干声对 RVC 同样适用）；变声冒烟需音频设备（用户实机）；翻唱 E2E 用 song/ 本地 mp3；feasibility blockers=sklearn 验证（唯一未验证依赖）。

#### Completion Summary（CEO）

- premise challenges：5 项全 verified（见 0A 表），unverified=0
- Error & Rescue：T-2/T-3 产物计数断言（上游吞错兜底）、T-4 底模缺失预检拦截（空串陷阱）、rc=2333333 判别、sklearn 预检、D2 回退预案（rvc_python 回切 py39）
- NOT in scope：§5 七项（含 cherry-pick 否决 4 项）
- What already exists：0B 复用清单（重建为零）
- dream state delta：0C 图
- 模式：SELECTIVE EXPANSION（auto-decided）；新增 scope：无独立任务（delight 1 并入 T-6）

### Spec Review Loop（0H）

- **第 1 轮（2026-10-14）**：7.5/10，不 PASS。11 项发现（P-1~P-11）：P-1 .env/ffmpeg 落位缺失（重要）、P-2 模型共享通路只写一半、P-3 checkpoint 选择规则缺失、P-4 rvc_service.py 处置未定义、P-5 TST 顺序矛盾（重要）、P-6 C-4 漏排、P-7 D5 编号不连续、P-8 音高参数映射未定义、P-9 F-1 控件归属缺失、P-10 T-5 执行环境模糊、P-11 跨 runtime 模型兼容风险（中）。全部 receipts 抽查属实（11/13 精确命中、1 行号偏差 5 行无实质影响、1 条 reviewer 待验证项经本会话读源确认属实）。
- **修订**：11/11 落盘（S-1 补 .env/ffmpeg；T-7 补 .env 预检；C-2 补共享通道+CLI 契约；T-6 补 checkpoint 规则+notify+py39 load 冒烟；R-1 补 rvc_service 处置；TST 拆 a/b；§7 重构补 C-4；§6 补 D5；C-3 补音高映射；F-1 补控件归属；S-2 补执行环境矩阵；D2 补跨 runtime 风险）。
- **第 2 轮**：8.5/10。11 项修订 10 项干净修复、P-10 引入 CON-1（矩阵缺变声行）。剩余 9 条（CON-1/2/3、CLA-1/2/3/4、CMP-1/2）已全部修订收口：S-2 矩阵补变声服务行、D2 明确回退半径（变声侧=回切+推理类同步回退旧版不删）、C-3 状态卡补 timeout、T-3 逐参数注释+与规格 10 映射澄清（CPU 进程数不作用于 rmvpe_gpu 分支）、§7 C 系列顺序显式化、C-3 单位短语修正、空 Review record 标题回填本节、T-7 补 rvc_python 存在性预检、TST-1b 补翻唱档案选型断言；备注两条收口（S-1 点名 logs/mute、T-7 大 ckpt 磁盘处置）。

### CEO 11 节深度审查（implementation-ready depth，2026-10-14）

**Section 1 Architecture**——系统架构图（新增组件=加粗）：

```
              ┌────────────────────────────────────────────────────┐
              │        AUTOlive 主进程 (runtime312-clone 编排)      │
              │  train_voice_pipeline(GSV 七步)                     │
              │  **train_rvc_pipeline(RVC 六步,本批重写)**          │
              │  voice_profiles(E-4 单写者)◄──**T-6 export 写入**   │
              │  cover_worker(翻唱队列)◄──**C-2 档案选型**          │
              │  gpu_policy(准入:三调用点+R-6 预留)                 │
              └───┬──────────────┬───────────────┬─────────────────┘
                  │子进程         │进程内直调      │HTTP 127.0.0.1
     ┌────────────▼───┐  ┌───────▼────────┐  ┌───▼──────────────┐
     │ RVC 训练子进程   │  │apps/aicovergen │  │**变声服务子进程** │
     │ (runtime312:    │  │src/mdx.py 三阶 │  │rvc_realtime_     │
     │ preprocess/f0/  │  │段分离(T-1 复用)│  │service(**R-1 升级│
     │ feature/train)  │  └───────┬────────┘  │rtrvc.py**),9391  │
     └────────────────┘          │           └───┬──────────────┘
                                 ▼               │
                    models/mdxnet, models/rvc    │加载 rvc_model
                    (junction→RVC fork 资产)      ▼
              ┌────────────────────────┐   voice_profiles.rvc_model
              │ 翻唱 CLI 子进程(py39)   │   +added_* index
              │ AICoverGen main.py     │
              │ **C-1 -oformat wav**   │
              └────────────────────────┘
```

耦合=voice_profiles 成为三端写读枢纽（T-6 写/C-2、R-3 读）——有意耦合，即"声音身份"本体；依赖收敛 config paths（S-1）。SPOF：变声服务崩溃→orchestrator 自动重拉（已有）；训练失败即停（语义保持）。10x/100x：单用户系统，GPU 准入序列化并发，无扩展问题。Rollback：rvc_root 配置回切+R-1 保留旧版推理类+git 历史——可逆。Value test：TST 包/E2E 驱动均有消费者（回归防线），非无主工件。**0 issues。**

**Section 2 Error & Rescue Map**——

```
METHOD/CODEPATH        | WHAT CAN GO WRONG           | EXCEPTION/RESCUE
T-2 preprocess 子进程   | 产物空(上游 except 吞错)     | 产物计数断言→步 FAILED+四要素 UI ✓
T-3 f0/feature 子进程   | 分片 argv 错/缺产物          | 产物计数断言 ✓
T-4 train.py           | rc≠2333333(OOM/崩溃)        | rc 判别→FAILED ✓
T-4 config 模板+filelist| 底模缺失(空串=随机初始化陷阱) | check_assets 预检拦截 ✓
T-5 index 复刻          | sklearn 缺失                | 预检+随 S-2 装 ✓
T-6 py39 load 冒烟      | torch 跨版本不兼容          | 提前暴露→计划级返工预案 ✓
R-1 rtrvc 升级          | Config() argparse SystemExit| 构造前清 sys.argv ✓
R-2 /params 端点        | 非法值                      | 值域校验+三要素错误 ✓
C-1 成品定位            | 成品名不匹配                | finder " Ver)." 兜底+retry ✓
S-1 junction 迁移       | 源资产缺失/被占用           | 迁移前存在性检查 ✓
```

**0 CRITICAL GAPS。**

**Section 3 Security**——新攻击面：/params（127.0.0.1 绑定保持+值域校验）、junction（本地资产无外部输入）、cover_worker 参数列表派发（非 shell 拼接，无注入面，保持）。无新 secrets、无 PII 新增。High=0。**0 issues。**

**Section 4 Data Flow & Edge Cases**——关键流：dataset→mdx→preprocess(双产物)→f0/feature→filelist(交集)→train→ckpt→export→profiles→三端。边界：空数据集/空产物（预检+断言拦）、重复实验名（slug 冲突序号，已有）、GSV+RVC 并发训练（GPU 门序列化）、export 写 profiles 与 cover 读并发（E-4 原子写+目录扫描兜底 ✓）。**0 unhandled。**

**Section 5 Code Quality**——重写对齐 GSV 模式同构性好；`_cleanup_after_step`/`_gpu_gate` 复用方式（共享小模块 vs 复制）为实施细节，倾向独立小模块避免跨管线 import 拉入 GSV 依赖（P5 explicit），实施时定，不阻塞。**0 issues（1 实施注记）。**

**Section 6 Test Review**——映射：T-2~T-6 每步→TST-1a 单测（mock _run，断言 argv/产物断言逻辑）；T-5 n_ivf 公式→单测；R-2→TST-1b 契约；C-1→TST-1b 回归；C-2→TST-1b 选型单测；全链路→TST-2a/2b E2E（实机）。no_claim 样例：TST-1a argv 断言=no_claim "mock _run 不证明训练实际收敛，由 TST-2a 实跑补证"。2am 测试=TST-2a 全绿；hostile QA=步间崩溃重跑（步骤级状态+断点语义保持）；chaos=训练中 kill 进程树（_cleanup_after_step 覆盖）。pyramid：多单测+2 E2E，合理。flakiness：TST-2 依赖 GPU+实机（标注实机测试非 CI）。**0 gaps。**

**Section 7 Performance**——T-5 big_npy 本规模（~200 片段×768 维）≈72MB 内存，KMeans 阈值 2e5 行不触发；T-2 n_p=10 ≈2GB 内存可接受；训练 batch8/40k 显存估 6-8GB（16GB 卡内，GPU 准入保证）；翻唱全链路 2-4 分钟 vs job_timeout 10min 足够。**0 issues。**

**Section 8 Observability**——训练步骤五态+日志尾（GSV 同款）、变声 /status last_infer_time（已有）、翻唱队列状态机（C-3 前端可见化）、logs/{exp}/*.log 保留可回溯。单用户系统无 alert 需求（UI 状态卡即监控）。**0 gaps。**

**Section 9 Deployment**——本地系统，重启生效；主风险=S-1 迁移首跑，TST-2a 即首跑验证+回退预案（rvc_root 回切）。post-migration checklist：junction 目录可达（dir 验证）→check_assets 全绿→TST-2a。**1 risk（已配预案）。**

**Section 10 Long-term**——债务标注：翻唱 py39 runtime 与主环境分裂（后续统一项，已列 §5）；RVC fork 22 modified 未提交（上游漂移，季度分诊惯例已有）。Reversibility 4/5。voice_profiles 枢纽化使未来第四消费端（如实时 API 变声）接入成本低——正向平台潜力。**2 debt 标注（非新增）。**

**Section 11 Design & UX**——IA：训练页双管线参数区重排（F-1）、变声档案优先列表（R-3）、点歌翻唱状态卡（C-3）。状态覆盖补强（并入 C-3）：EMPTY 态=无翻唱记录引导文案（"点歌命中后将自动翻唱"）。User flow：

```
训练页 ─选RVC→ 参数面板(F-1) ─开始→ 步骤五态+日志 ─完成→ notify"新声音已就绪"
变声页 ◄─档案列表(R-3)──点选→热切换事务；参数面板(F-2)→/params 生效
点歌页 ─翻唱模块(C-3)→ 队列状态卡+档案/音高+成品列表(EMPTY 有引导)
```

AI slop 风险低（全部对齐既有 NiceGUI 卡片范式）。**1 enhancement（并入 C-3）。**

**汇总**：11 节 0 CRITICAL GAP、0 High 安全项、1 实施注记（S5）、1 enhancement 并入（S11）、2 debt 标注（S10，已有惯例）。

### REALITY rows（CEO，2026-10-14）

REALITY: premise-table pass 0A 表 5 项全 verified（参数链断裂/翻唱落库错误/训练管线缺深化/py39 弃用/旧版推理类，receipt 齐），unverified=0（rvc_realtime_service.py:62-75、cover_worker.py:212-216+:303、train_rvc_pipeline.py:172-183、RVC go-web.bat:1、rvc_realtime_service.py:44-48）
REALITY: already-done pass git log origin/main 近期（GSV 批次 45bb1104）不含本计划命名文件；train_rvc_pipeline/svc/cover_worker/feature_handler 自 2026-10-01 零提交（spec reviewer 独立复核属实）
REALITY: surface-check pass 计划命名全部 CLI/argv 形态来自勘察 file:line receipt（preprocess.py argv/infer-web.py:243-252、extract_f0_rmvpe.py :18-23、extract_feature_print.py :8-20、train.py argv :592-608、AICoverGen CLI main.py:326-347、cover_worker 派发 :212-216）；无未验证的新命名端点（/params 为新增端点，契约在 R-2 定义由 TST-1b 断言）
REALITY: binding-decisions pass gstack-decision-search --scope branch --recent 50（2026-10-14 执行，决策记录 C:/Users/admin/.gstack/projects/Eikwang-AUTOlive/decisions.jsonl:1-6）近 6 条决策无冲突：GSV 深化（decisions.jsonl:6，2026-10-13）为本计划前置且已完成；danmaku/audio_player 三条不触碰本计划文件；无 overruled default 重引入
REALITY: numbers pass 训练参数 20/8/10/40k/v2/f0/spk0=[用户规格，本计划 specs/integration/RVC深化整合计划.md:68 §3 表]；n_ivf=min(16√N,N//39)=infer-web.py:681、KMeans>2e5=infer-web.py:663-673、rc2333333=train.py:652、f0 分片 argv=extract_f0_rmvpe.py:18-23；T-5 内存 72MB/训练显存 6-8GB/翻唱 2-4min=[estimated：768 维（extract_feature_print.py:129-135）×~200 片段、GSV batch4 实测 4GB 外推、AICoverGen 八步全链路推算（src/main.py:248-320）；TST-2a 实测校准]
REALITY: deferred-asks pass 用户请求三功能（训练/实时变声/歌曲翻唱）全覆盖（本计划 §1，specs/integration/RVC深化整合计划.md:8-14），无遗留未做的用户要求；§5 NOT in scope 七项（specs/integration/RVC深化整合计划.md:125）均为计划主动排除非用户遗漏
REALITY: pr-count pass 无 .gstack/ship-policy.json（HEAD=45bb1104 仓库状态实查 2026-10-14，D:/AI/AUTOlive/.gstack/ 下无该文件）→默认 one PR per wave；本计划单批次单 PR，无用户可见引用分裂

### Outside Voice（Closing sequence，2026-10-14）

- **Codex 外部端点**：model_frontier_openai 已修复（gpt-6-astra，解决上次会话遗留），但端点侧 400 InvalidParameter（火山 coding API 对 Codex CLI 请求体解析失败）→ outside_status=unavailable。
- **Native fallback（Plan 子代理，同 prompt，in-host）**：完成，9 项发现（4 blocking），全部基于三树源码实读。整合处置（Analyze→Resolve→Apply）：
  1. 【High】C-3 音高映射方向反了（-p 单位即八度，main.py:330 help + :292 内部 ×12；预乘会 144 半音）→ C-3/TST-1b 更正，blocking 已修。
  2. 【High】共享模型通道写读两根不相交（_rvc_models_root=rvc_vc.rvc_root/rvc_models 不存在 vs 消费端 AICoverGen根/rvc_models）→ C-2 补基根切换+S-1 junction 对齐+§2.2 表述修正，blocking 已修。
  3. 【Medium】_rt_python 默认派生在根翻转后指向不存在路径 → S-2 显式 config 键钉住，已修。
  4. 【Medium】T-6 导出命名模型错误（te20/se8 只有 e8/e16+无后缀终态；"最高 epoch"解析会导出 e16 欠训练模型）→ T-6 改终态优先+TST-1a 断言覆盖，blocking 已修。
  5. 【Medium】faiss index 跨版本冒烟缺失+静默降级（1.14.3 写 vs 1.7.2 读，read_index 失败静默）→ T-6 双冒烟（.pth+faiss.read_index），已修。
  6. 【Medium】runtime312 共享环境依赖风险+py39 回退未验证 → D2 补风险③+回退预案降级为应急级+独立 venv 升级路径，已修。
  7. 【Low】C-3 档案选择持久化键未定义 → 定义 config choose_song.cover_profile_id（C-3 写/C-2 读），已修。
  8. 【Low】T-4 -c 1 未解释变更 → 维持 -c 0 并注明，已修。
  9. 【Low】gradio_client 替代未记录对比 → D6 决策记录补齐（不采纳理由：API 稳定性+常驻进程管理+GSV 范式一致性），已修。
- reviewer 确认无误的抽查（校准）：filelist 交集/转义/mute/spk_id、config 特判、rc=2333333、三脚本 argv 契约（含 rmvpe.py:489 恒 fp32）、added_ 前缀、n_ivf 公式、资产存在性、runtime312 依赖齐备性、rtrvc 接口、GSV 助手命名、C-1 诊断。
- **Cross-model tension：跳过**（外部 codex 未完成，无 completed external review；native fallback 供 in-host findings，不算 outside coverage）。
- OUTSIDE COVERAGE：provider=codex unavailable（端点 400）；native fallback completed（9 findings，9/9 resolved）。

### Native reviewer 正式快照审查（CEO，2026-10-14）

INPUT: ceo 332c59bf9084c4ba6f20376bbd8e8f62b406a7e2720c5cc5fa756519906cdd86——**有条件批准**，无 critical，3 High/9 Medium。12 项发现处置（Analyze→Resolve→Apply）：

1. 【High】1.1 消费链价值前置（现存用户可见 bug 被排后）→ **接受**：Day-1 消费冒烟+C-1/C-2 提前到 T 系列前，§7 重排（R 系列可与 T 并行）。
2. 【High】2.1 runtime312 训练可用性未验证且验证后置 → **接受**：新增 T-0 spike（开工前置，失败则整批重估）。
3. 【High】3.1 共享环境瘫痪三链场景 → **接受**：pip freeze 快照入库+独立 venv 时点提前到 E2E 验收后；变声链独立最小 venv defer TODOS。
4. 【Medium】1.2 社区模型导入通道未评估 → **接受**：§1 双通道定位+R-3 导入说明（零集成成本）。
5. 【Medium】2.2 save_every=8 开工前确认 → 按 /autoplan 管线约束（User Challenge 终门统一问），维持 D4 终门确认，终门 brief 显眼呈现。
6. 【Medium】2.3 外部环境漂移指纹预检 → **接受**：T-7 补 torch 版本指纹+变更提示。
7. 【Medium】3.2 fork 基准 commit+契约出处落档 → **接受**：头部绑定 4452ddd9+出处已在 §2.1。
8. 【Medium】3.3 so_vits_svc 退役开放式 → **接受**：R-4 定时点=下一批次。
9. 【Medium】4.1 D6 量化对比不完整 → **接受**：量化补强（负债 vs 脆性面）。
10. 【Medium】4.3 翻唱 runtime 统一成本对比缺失 → **接受**：§5 补对比+下一批评估时点（本批保持 py39 避免连锁改动）。
11. 【Medium】5.1 差异化主张未陈述 → **接受**：§1 补无人值守闭环主张+验收③时延/成功率记录。
12. 【Medium】5.2 上游演进时间窗风险 → **接受**：头部复刻面基准绑定声明（与 3.2 合并）。

### Native reviewer 正式快照复核（CEO，2026-10-14）

INPUT: ceo f452bbf803e1a83571483b49db03fbc305e5d7d89cecf0560aa5200b879eee36（快照 autoplan-ceo-YKmkmm，含全部 12 项发现整合）——**批准，附 4 项终门条件**，无 critical（1 High/7 Medium/1 Low）。8 项发现处置：

1. 【High】1 差异化主张"自动回播"无对应任务 → **C-5 新增**（终门裁决项：插播时机=完成即播 vs 下次命中回播）。
2. 【Medium】2 验收缺受众侧度量 → 验收③追加命中数/完成播出数记录。
3. 【Medium】3 社区模型版本/采样率失配 → R-3 模型头标注+单模型隔离+TST-1b 回归。
4. 【Medium】4 D2 venv 表述矛盾 → 拆 T-8a（训练链，E2E 后启动）/T-8b（变声链，defer 带触发器）。
5. 【Medium】5 fork 漂移依赖人的记忆 → S-1 哈希清单 tripwire+check_assets 比对告警。
6. 【Medium】6 runtime 统一无触发条件 → 绑定 TST-2b 通过即列下一批首项。
7. 【Medium】7 D6 漏 fork 内 cli_train.py 备选 → 否决理由补记录+登记为 fork 升级重估首选。
8. 【Low】8 F-1"固定展示"歧义 → 明确可调组/固定展示组。

复核裁决原文："该计划的问题定义、排序纪律与风险门控均属上乘，主要残余风险集中在'主张大于交付'与'无触发器的后置'两类——恰好都是文字层面即可修复的缺陷。"8 项已全部落地于计划正文。快照 YKmkmm 为 native 复核通过基准；其后 8 处条件落地修订为终门前正常修订（review record 留痕）。

### Design Review Phase 2（2026-10-14，/autoplan 管线）

**System Audit**：无 DESIGN.md（frontend-design-tokens 记忆=事实设计系统：CSS 变量/宽度三档/禁硬编码色值）；UI scope yes（F-1 训练页 RVC 卡/F-2 变声页深化/C-3 点歌翻唱模块/R-3 档案列表）；既有可复用范式=train_voice.py 参数分组折叠+svc.py 卡片网格+状态卡 2s 轮询+GPT-SoVITS 卡重写模板。分类器=OPERATE（管理面板）。**Step 0.5 mockup 跳过**：本计划 UI 全部为既有管理界面卡片级增改（对齐既有设计系统，无新视觉方向），mockup 增值低且 board 反馈循环需用户参与与 /autoplan 管线自动性冲突——文字审查覆盖。

**7 Passes（评分→修复→复评）**：
- **Pass 1 IA：7→8.5**。发现：RVC 参数卡与 GSV 卡的并存/替换关系未定义、变声页档案与扫描模型分组顺序未定义。修复落 F-1（同位替换+切换即重排+表单值独立保留）/F-2（档案优先分组+扫描兜底分组+分组标题区分）。
- **Pass 2 状态覆盖：7.5→8.5**。发现：变声页档案列表 LOADING 态缺失、管线切换表单值保留未定义。修复落 F-2（骨架占位）/F-1（独立保留）。C-3 五态+EMPTY 已在 CEO 阶段补齐。
- **Pass 3 旅程：8**。情绪弧=配置焦虑→训练等待→notify 成就感→变声验证→点歌收获；notify→变声页引导=文本指引（NiceGUI notify 无跳转，既有约定）。No issues found（弧完整）。
- **Pass 4 AI Slop：8→8.5**。硬拒绝：0 命中（OPERATE 面、无新首屏、卡片=状态展示必要存在）。发现：C-3 三块内容有卡片墙堆叠风险。修复落 C-3（折叠分组承载，对齐既有范式）。litmus：7 项全 YES（对齐既有系统）。
- **Pass 5 设计系统：7.5→9**。无 DESIGN.md 文件（建议后续 /design-consultation，不阻塞本批——既有 CSS 变量系统即事实标准）。发现：F/C 系列未显式约束设计令牌。修复落 F-1/F-2/C-3（对齐 CSS 变量/宽度三档/禁硬编码色值）。
- **Pass 6 响应式+A11y：8**。桌面工作站场景（直播管理面板），无新响应式需求；触达目标/键盘/对比度沿既有 Quasar 兜底+全局 TODOS 项。No new gaps。
- **Pass 7 未决决策：4 项全部落计划**——①C-5 插播时机（CEO 终门裁决项保持）②音高滑杆量程 -12~+12 八度（落 C-3）③pitch -16~+16/formant -2~2/index_rate/protect 量程（落 F-2）④表单值保留策略（落 F-1）。

**Overall design score：7/10 → 8.5/10**（最低 pass=Pass 1 修复后 8.5）。Decisions made=8（全部落计划文本）；Decisions deferred=1（C-5 插播时机→终门）。

### DX Review Phase 2.5（2026-10-14，/autoplan 管线）

**Persona**：owner-operator（单人直播主，非专业开发者，全 webui 操作，对齐 GSV 深化审查口径）。product_type=local_tool。**DX Trend**：prior=7.6→8.6（GSV 深化批，2026-10-13），本批基线继承 8.6 口径。**Competitive tier**：独立开三个工具（RVC webui+w-okada+AICoverGen webui）=基线对照；本批闭环目标=TTHW 远优于基线。

**8 Passes（评分→处置）**：
- **Pass 1 Getting Started：8**。旅程=数据集就绪→训练页选 RVC→默认参数→开始→notify→变声页切换：操作时间 <5min（UI 步骤），机器时间为训练时长。TTHW 目标：**数据集就绪→变声页可用新声音=操作 <10min+训练机器时间（TST-2a 实测记录基线）**。Magical moment=notify"新声音已就绪"（T-6 已含）。No new gaps。
- **Pass 2 API/CLI 设计：8.5**。操作面=NiceGUI 控件+/params 端点：参数名对齐 rtrvc 契约（pitch/formant/index_rate/protect/f0method 一致性 ✓）、全部有默认值（DEFAULTS 集中 ✓）、渐进披露（可调/固定两组 ✓）。No issues。
- **Pass 3 错误与排障：8→8.5**。Trace 3 错误路径：①训练失败→GSV 四要素 UI 同款（计划已含 ✓）②变声切换失败→三要素 problem/cause/fix（既有 ✓）③**翻唱 failed→用户只见状态不见原因（gap）**。修复落 C-3（failed 态展示 reason 可读化）。R-2 /params 错误=三要素（S2 表已含 ✓）。
- **Pass 4 文档：7.5**。训练页录音指引卡（已有）；RVC 参数语义=UI 固定展示组内建说明（F-1 已含）；docs 模块级文档=TODOS 全局第 4 项（全面测试后，不阻塞）。遗留=TODOS 承接，无新 gap。
- **Pass 5 升级迁移：8.5**。S-1 副本迁移+rvc_python 可配置回退（已有）；config 新键 get() 缺省向后兼容（既有模式）；fork 基准绑定+tripwire（CEO 复核已落）。No new gaps。
- **Pass 6 环境工具：8.5**。T-0 spike+check_assets 存在性+指纹+pip freeze 快照=环境可信度三层（计划已含）；sklearn 预检缺则装。No new gaps。
- **Pass 7 社区生态：n/a**（单用户私有系统，无社区面）。
- **Pass 8 度量反馈：8.5**。验收③时延/成功率+受众度量两项（native 复核已补）=DX 度量闭环；cover_worker 日志为数据源。No new gaps。

**DX Scorecard：initial 8.0 → overall 8.6**（最低 pass=Pass 4 的 7.5 为 TODOS 承接项不计入有效 gap；有效最低=Pass 1/3 修复后）。TTHW：现状≈手动三工具拼接 15min+全人工 → 目标=操作 <10min 零人工。DX Implementation Checklist：①C-3 reason 展示（DX-1，P2）②TST-2a 记录 TTHW 基线数字（随 E2E）。Decisions made=2（C-3 reason+TTHW 基线记录）。

### Eng Review Phase 3（2026-10-14，/autoplan 管线，always LAST）

**Scope Challenge（Eng，grounded in code）**：改动面 ~15 文件（CEO 估 14+DX/Design 增补 2 处均在既有文件内）。>8 文件触发复杂度裁决——**裁决：复杂度必要**：T 系列=用户显式规格（六步训练），R-2/C-1/C-2/C-3=verified 既有 bug/缺口修复（0A 表 receipts），S-1=勘察发现的 4 处硬编码+副本闲置的结构性收敛；无一项可为减而保目标。REALITY: premise-table pass（CEO 0A 表 5 项 receipts 沿用，Eng 复核 train.py:581/636-650 导出命名、main.py:330 -p 单位两处关键 receipt 经 outside 发现 4/1 修正后已落文本，unverified=0）。already-done/surface-check/binding-decisions/numbers/deferred-asks/pr-count 沿用 CEO 7/7（REALITY 行见 CEO 节，Eng 无新增反驳）。

**Architecture（Eng 视角补充，图=CEO S1 架构图引用）**：分布架构=零新发布面（本地单机，无 CI/CD 变更）；瓶颈=GPU 单卡序列化（gpu_policy 门保证训练/翻唱互斥，变声=直播关键路径优先侧常驻）；SPOF=EDTalk\runtime312 共享环境（D2 风险③三层缓解：预检+快照+T-8a/T-8b venv 路线）——生产失败场景已映射（CEO S2 表 0 CRITICAL GAP 沿用，Eng 复核 T-4 rc 判别/C-1 finder 兜底两路径无新增 silent failure）。**No new issues。**

**Code Quality（Shared-code rubric 正式评估）**：
- 机会：`_cleanup_after_step`/`_gpu_gate`/`_vram_used_gb`/`_wait_vram_release` 在 train_voice_pipeline.py:285-298,327-333（现有，verified first-party）与重写后的 train_rvc_pipeline.py（proposed caller，assumption 标注）间共享。
- Rubric 判定：**提取**——两 caller 行为一致（taskkill 进程树+显存回落轮询+准入门，纯函数无状态）；提取至 `utils/train_process_utils.py`（~60 行）；RVC 侧复制 60 行→import ~5 行，净省 ~55 行+GPU 教训演进时单一修复点（上次教训：残留显存换页——该防护逻辑未来还会改，两份拷贝=漂移温床）。Adoption：RVC 重写时直接 import（零迁移成本）。Blast radius=两训练管线（共享失败=显存清理失效，两侧同险）。**落计划：T-7 注记更新为"清理/GPU 门从 utils/train_process_utils.py import（本批新建，自 train_voice_pipeline.py 提取，GSV 侧同步改 import）"。**
- 错误处理缺口：无新增（CEO S2 表全覆盖）；ASCII 图准确性=CEO S1 图与现状核对一致。

**Test review（5 步，测试计划已落盘 ~/.gstack/projects/Eikwang-AUTOlive/eng-review-test-plan-20261014.md）**：
- Step 1 codepath trace：六步训练链（每步入口→变换→产物→失败路径）+变声参数链+翻唱档案链——20 断言全覆盖（见测试计划表）。
- Step 2 用户流：训练（配置→启动→等待→notify）、变声（选档案→切换→调参）、翻唱（点歌→排队→成品回播）+交互边界（双击开始=管线 running 态幂等（既有）、切换中途训练完成=notify 与热切换互不阻塞（S4 已裁））。
- Step 3 分支对照：TST-1a 14 断言（T 系列全分支）+TST-1b 6 断言（R/C 面）+test_cover_queue 9 用例（既有）+test_voice_profiles（既有）——X 值=全部分支有 ★★+ 测试映射；value cards 见测试计划表。
- E2E 矩阵：训练链 6 组件串联+[→E2E]（TST-2a）；变声+翻唱消费端+[→E2E]（TST-2b）；单测不可达的 GPU/rc/跨 runtime 面=实机 E2E 专属。
- **codepath-to-test diagram**：

```
T-1 mdx ──────────► TST-1a#12(SKIPPED plan) ───► TST-2a（实机分离产物）
T-2 preprocess ────► TST-1a#3(产物断言)  ───► TST-2a（计数实测）
T-3 f0/feature ────► TST-1a#6(argv 契约)  ───► TST-2a
T-4 train ─────────► TST-1a#2,5,14(cfg/rc/argv) ► TST-2a（rc=2333333 实测）
T-5 index ─────────► TST-1a#7(公式)       ───► TST-2b（faiss 可检索）
T-6 export ────────► TST-1a#8,10(命名/单写者) ► TST-2a（py39 双冒烟实跑）
R-1 rtrvc ─────────► （TST-2b 实机加载）   ◄── R-2 /params ► TST-1b#16（契约）
R-3 档案 ──────────► TST-1b#19(失配隔离)
C-1 oformat ───────► TST-1b#15（回归）
C-2 选型 ──────────► TST-1b#18（回归+基根）
C-3 前端 ──────────► TST-1b#17,20（映射/键）
S-1 tripwire ──────► TST-1a#9（哈希比对）
```

**Performance（Eng 复核 CEO S7）**：T-5 内存 72MB[estimated]、训练显存 6-8GB[estimated]（TST-2a 校准）、翻唱 2-4min vs timeout 10min——**No new issues**。

**Failure Modes Registry**：CEO S2 表 10 行沿用（RESCUED=Y 全行，0 CRITICAL GAP）；Eng 新增行：T-5 sklearn 缺失→预检拦（RESCUED=Y/TEST=TST-1a#9 预检断言）。

**Eng Completion Summary**：scope challenge=复杂度必要（grounded）；架构/性能=No new issues（CEO 沿用+Eng 复核）；shared-code=1 提取裁决（train_process_utils.py）；test=20 断言+E2E 两阶段，测试计划落盘；critical gaps=0。

<!-- autoplan-baseline-edits:ceo {"sourceSha256":"f452bbf803e1a83571483b49db03fbc305e5d7d89cecf0560aa5200b879eee36","replacements":[{"oldText":"- **C-4 cover_worker 根收敛**：aicovergen root → config paths（apps/aicovergen）；模型目录发现跟随 S-1 副本。","newText":"- **C-5 翻唱成品自动回播（复核发现 1，终门裁决项）**：翻唱 done 后自动写入 song 播放队列回播（复用 cover_worker 既有冷却/同观众上限语义做播控）；**插播时机策略终门二选一**：完成后立即插播（完整零人工闭环，与 TTS 对话流的播放通道互斥需处理）vs 下次点歌命中回播（现状 cached 路径已支持，主张降级为\"成品入库+下次命中自动回播\"）。采纳 (a) 时验收③改\"命中→自动播报\"。\n- **C-4 cover_worker 根收敛**：aicovergen root → config paths（apps/aicovergen）；模型目录发现跟随 S-1 副本。"},{"oldText":"挂档案可选（空态卡指引文案）。","newText":"挂档案可选（空态卡指引文案）；**兼容性防护（复核发现 3）**：扫描时读模型头（version/sr）在 UI 标注兼容性，加载失败按单模型粒度隔离上报（不拖垮切换事务），TST-1b 补失配模型加载回归。"},{"oldText":"AICoverGen 副本核对（apps/aicovergen/src 已逐字节一致，补 mdxnet_models/rvc_models junction 或复制核对）。","newText":"AICoverGen 副本核对（apps/aicovergen/src 已逐字节一致，补 mdxnet_models/rvc_models junction 或复制核对）；**fork 基准记录+漂移 tripwire（复核发现 5）**：S-1 记录 fork 基准 commit 4452ddd9 并对 14 条契约涉及的 fork 文件做哈希清单入库（一次性近零成本），check_assets() 启动比对，不一致告警\"复刻面基准已漂移\"（纪律→tripwire）。"},{"oldText":"- **F-1 训练页 RVC 卡**：专属参数面板（batch_size/total_epoch 20/save_every 8/采样率 40k 固定展示/版本 v2 固定展示/f0 true 固定展示/CPU 进程数 10/音高算法 rmvpe_gpu 固定展示），双管线切换时参数区重排","newText":"- **F-1 训练页 RVC 卡**：专属参数面板，**两组归属明确（复核发现 8）**——可调组：batch_size/total_epoch 20/save_every 8（epoch 是最常调旋钮不固定）；固定展示组：采样率 40k/版本 v2/f0 true/CPU 进程数 10/音高算法 rmvpe_gpu/说话人 ID 0。双管线切换时参数区重排"},{"oldText":"**独立 venv 启动时点=本批 E2E 验收通过后即启动**（native 发现 3.1 修复③，非\"实际破坏后\"）；变声链先行独立最小 venv defer TODOS（常驻直播进程，价值最高但属新增基础设施）。","newText":"**独立 venv 拆两条（复核发现 4 消歧）**：T-8a 训练链独立 venv=本批 E2E 验收通过后即启动；T-8b 变声链最小 venv=defer（触发条件=T-8a 落地后下一批次，或共享环境首次实际破坏时提前）。"},{"oldText":"fork 基准 commit 4452ddd9 已在头部绑定。","newText":"fork 基准 commit 4452ddd9 已在头部绑定。**第三备选否决记录（复核发现 7）**：fork 内置 `cli_train.py` 编排脚本（契约落 fork 内代码、升级时随代码更新）——否决理由=本批保持 fork 零改动以维持升级自由度+外部复刻有解耦优点；**登记为 fork 重大升级触发复刻面重估时的首选方案**。"},{"oldText":"（翻唱保持 py39 runtime，后续统一项）——**成本对比**（native 发现 4.3）：补装 fairseq/pedalboard/sox/yt_dlp 四包为一次性成本（fairseq 0.12.3.1 在 EDTalk\\runtime312 已实证可装），可消灭 P-11 跨 runtime 赌注+T-6 双冒烟对价；本批保持 py39（避免 C 系列连锁改动+双冒烟已防护），**下一批评估统一**，若统一则 P-11/发现 5 防护项整体退役。","newText":"（翻唱保持 py39 runtime）——**成本对比**（native 发现 4.3）：补装 fairseq/pedalboard/sox/yt_dlp 四包为一次性成本（fairseq 0.12.3.1 在 EDTalk\\runtime312 已实证可装），可消灭 P-11 跨 runtime 赌注+T-6 双冒烟对价；本批保持 py39（避免 C 系列连锁改动+双冒烟已防护），**统一绑定触发条件（复核发现 6）：本批 TST-2b 通过即列入下一批首项；翻唱端再发生任一跨 runtime 兼容事件时提前启动**——若统一则 P-11/发现 5 防护项整体退役。"},{"oldText":"手测记录点歌命中→成品可播的全链路时延与成功率（差异化主张的验收数字，native 发现 5.1）","newText":"手测记录点歌命中→成品可播的全链路时延与成功率（差异化主张的验收数字，native 发现 5.1）+受众侧度量两项（复核发现 2）：每场直播翻唱点歌命中数、成品完成/播出数（cover_worker 日志落地，为下一批供给投入裁决提供依据）"}]} -->

### Accepted requirements（CEO）

<!-- autoplan-accepted:ceo -->
- 用户规格 §3 全表固化：total_epoch=20/save_every=8（D4 终门确认项）/f0=true/v2/40k/CPU 进程数=10/rmvpe_gpu/spk_id=0/实验名前端设置走 voice_profiles；验证=TST-1a DEFAULTS 断言。
- 三功能范围：RVC 六步训练管线（T-1~T-7，对齐 GSV 深化模板七项工程能力）、实时变声保留三层架构+推理类升级 rtrvc+参数链闭合+档案接入（R-1~R-4）、歌曲翻唱成品修复+档案专属语义+前端模块（C-1~C-4）；验证=TST-1a/1b+TST-2a/2b。
- S-1 副本迁移含 .env 三键+ffmpeg.exe+logs/mute 落位、rvc_models 基根=aicovergen 根（outside 发现 2）；S-2 执行环境矩阵四行+翻唱解释器显式 config 键（outside 发现 3）。
- 导出规则=训练终态无后缀 {exp}.pth 优先（outside 发现 4）；T-6 py39 双冒烟（.pth+faiss.read_index，outside 发现 5）；T-4 -c 0 维持现状（outside 发现 8）。
- 翻唱音高映射=-p 直传八度值（outside 发现 1）；档案选择持久化键=config choose_song.cover_profile_id（outside 发现 7）。
- NOT in scope 七项（§5）+cherry-pick 否决四项（0G）为范围边界；D1（变声保留独立进程）/D2（runtime312+三重风险缓解）/D5（翻唱档案语义）/D6（CLI 复刻路线）为裁决记录。
- 验收标准五条（§4 TST 节）：训练链跑通+步间显存回落、变声档案/参数生效、翻唱成品正确+前端可见、TST 全过、旧路径全关闭。
<!-- /autoplan-accepted:ceo -->
