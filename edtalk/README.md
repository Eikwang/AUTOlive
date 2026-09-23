# EDTalk 运行副本（AUTOlive/edtalk）

> 本目录为 **AUTOlive 唯一运行副本**（由 `scripts/copy_edtalk.py` 从 `D:\AI\EDTalk` 复制生成，
> 见 MANIFEST.md）。EDTalk 原目录降为开发存档——不要在原目录修改运行代码。
> 解释器统一使用 `D:\AI\EDTalk\runtime312\python.exe`（可在画面设置页 `interpreter_path` 覆盖）。

## 三入口冒烟（复制后立即执行）

```bat
D:\AI\EDTalk\runtime312\python.exe -m realtime_serve.main --no-unitycapture
D:\AI\EDTalk\runtime312\python.exe train_fine_tune.py --help
edtalk\fastrag\server.exe   （启动后 GET http://127.0.0.1:11420/health 探活，然后关闭）
```

- 冒烟 1 成功标志：日志出现 preroll/preload 相关输出且无 ImportError/ModuleNotFoundError
  （失败处置：①相对导入断裂→检查是否在 edtalk/ 目录下运行；②路径假设断裂→对照
  `import datasets; print(datasets.__file__)` 确认模块解析到本副本而非 EDTalk 旧树 .pth 暗门）
- 冒烟 2 成功标志：输出 usage 帮助且无 import 错误
- 冒烟 3 成功标志：/health 返回 `{"status":"ok",...}`

## 迷你训练冒烟（一键训练按钮解锁门）

对小样本目录（1-2 个短视频，放 `original_videos/`）执行：

```bat
D:\AI\EDTalk\runtime312\python.exe data_preprocess\data_preprocess_for_train\run_preprocess.py --base_dir <样本目录> --steps 1,2
D:\AI\EDTalk\runtime312\python.exe train_fine_tune.py --datapath <角色> --exp_name <角色>_mini --only_fine_tune_dec --iter 50
```

**量化通过判据**：① 各 lmdb/lmdb_features 目录非空；② `video/`、`mel/`、`orifra/` 等产出目录有文件；
③ 50 iter loss 有下降趋势（无 NaN）；④ CUDA 链路无 OOM/初始化错误。
通过后（或以 config.json 手工键 `train_streamer.smoke_passed: true` 旁路——仅限知晓风险者）一键训练按钮解锁。

## config.edtalk_realtime → realtime_serve 启动参数映射

| config 键 | CLI 参数 | 默认 | 说明 |
|---|---|---|---|
| character_dir | `--character-dir` | wy | 素材目录名（大小写敏感） |
| target_fps | `--target-fps` | 25 | 仅约束输出帧率 |
| buffer_frames | `--buffer-frames` | 16 | 输出缓冲容量 |
| preroll_frames | `--preroll-frames` | 8 | 预填充帧数 |
| face_repair | `--face-repair <on\|adaptive>` | （off 不传） | off 启动后热切 409 |
| gaze_correction | `--gaze-correction <on\|adaptive>` | （off 不传） | off 启动后热切 409 |
| gaze_convergence | `--gaze-convergence` | 0.05 | [-1,1] |
| segments | `--segments auto\|<列表>` | auto | 段名大小写敏感 |
| host | `--host` | 127.0.0.1 | 默认回环 |
| port | `--port` | 8000 | 三处统一引用 |
| api_token | `--api-token` | （空不传） | Bearer 鉴权 |
| interpreter_path | （解释器本体） | runtime312 | 启动/训练前校验存在性 |

## 训练命令映射（14 步 → 4 大阶段）

| 阶段 | 命令 | 说明 |
|---|---|---|
| 1 预处理编排 | `run_preprocess.py --base_dir <用户目录>` | PIPELINE_STEPS=11 步为唯一事实来源（含 prepare_gaze_data） |
| 2 底模微调 | `train_fine_tune.py --datapath <角色> --exp_name <角色> --only_fine_tune_dec --iter 30000` | 经济截断点 |
| 3 评估 | `tools/eval_finetune.py --cand-ckpt ckpt_models/<角色>/checkpoint/30000.pt --datapath <角色>` | 解析 eval_results.jsonl；门槛 MAE≤11.1px/贴回≤3.6px/SSIM降幅≤0.02/开合比0.8-1.25 |
| 4 口型微调 | `train\train_audio2mouth.py --data_path <角色> --resume_ckpt ckpt_models/<角色>/checkpoint/30000.pt --audio2lip_ckpt ckpts/Audio2Lip.pt + 定稿配方` | 配方见 utils/train_streamer/pipeline.py AUDIO2MOUTH_ARGS |

## fastrag

- server.exe / build-index.exe 在 fastrag/ 目录下运行（相对路径假设）
- 端口：config.fastrag.port → `FASTRAG__SERVER__PORT` 环境变量注入（**不写 Config.toml**，tomllib 只读）
- 安全：server.rs 硬编码绑定 0.0.0.0 且无鉴权——copy_edtalk.py 已添加 netsh 入站阻断 11420
  规则（本机回环不受影响）。**此为本机自用的已接受风险**；如需局域网服务请自行改 Rust 源码加鉴权。
- LLM 人设：fastrag/Config.toml 的 `prompt.system_role`

## edtalk 模式下的语义变化清单（native E6）

| 既有功能 | edtalk_realtime 激活时的行为 |
|---|---|
| 弹幕打断（stop_current_audio） | 只作用于本地 mixer/audio_player；EDTalk 端无 flush-audio 契约端点，打断**不会**停止其音频播放 |
| send_audio_play_info_to_callback | 推送模式下播放完成回调被跳过（本地不播放） |
| 音频变速（audio_random_speed） | 变速照常，但推送的是**变速后文件**（EDTalk 收到的是最终音频） |
| 本地字幕/洛曦联动 | 不受影响（在播放分支之前执行） |

## 常见故障排查

| 现象 | problem / cause / fix |
|---|---|
| 拉起日志"端口被占用" | 端口冲突 / 被未知服务占用 / 改画面设置页『端口』或结束占用进程 |
| 拉起日志"解释器不存在" | 解释器缺失 / runtime312 变动 / 改『解释器路径』配置 |
| 推送健康度"N 次失败" | 服务未连接 / 服务未启动或崩溃 / 看 /status 与服务日志，确认后计数自动清零 |
| 训练第 1 步即失败 | 目录结构不符 / 原始视频未放 original_videos/ / 按数据集处理流程组织 |
| 构建知识库失败 | knowledgebase 为空或文件锁 / 先放入 .md 并停止 server 再构建（UI 按钮已自动处理） |
