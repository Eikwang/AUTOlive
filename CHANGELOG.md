# Changelog

AUTOlive 变更日志（Keep a Changelog 格式，随批次更新）。
项目无独立版本号（直接 main 落地），以日期批次为节奏锚点。

## [Unreleased]

### 2026-10-27（助播并行消费线完整化批次，/autoplan main-20261026-214845 终门 rec 批准）

- **T0 主匹配器热修（独立先行提交 19df2ca6）**：`Common.calculate_similarity`
  此前从未存在——find_answer/find_similar_answer 调用恒 AttributeError 被吞，
  主知识库文本匹配运行时 100% 失败（完美命中也 None；chat_type=local_qa 全量
  未命中、LLM 托底静默失效）。新增归一化相似度+精确短路；find_answer/
  find_similar_answer 合并单实现（两函数体本完全相同）。守卫 13 项。
- **触发类型 v2**：`assistant_anchor.type` 收敛为十项合法值（AI对话=原 comment
  改名、聊天=新增（含聊天页消息与复读内容——复读归拢，勾选后复读走助播外放
  不再抢占数字人、未勾选维持 999 原链）、念弹幕/礼物/入场/关注/定时任务/
  闲时任务/积分/点歌）；本地问答-音频/图像识别定时任务/按键映射移除。存量
  type 经 settings._ensure_defaults 幂等迁移（comment→ai_chat、reread/
  reread_top_priority→chat、三项删除；未配置=空集行为零变化）。
- **十生产端真实路由**：新增 `utils/handlers/assistant_router.py` 中央路由助手
  （数据驱动路由表 (产线,来源)→勾选键；此前 14 选项仅 song 一项接线）——
  comment LLM 回复、复读、答谢三件、定时任务、闲时任务 text 类、积分（先行
  判定不被 AI对话 误捕获）、念弹幕回放、聊天页 chatbox 消息、主 KB 命中产线
  （来源勾选时改判助播）、点歌文案（R4 收编）全部接线，勾选即生效。
- **助播知识库闭环**（此前 local_qa 配置零消费）：音频匹配（标题三级归一：
  精确→包含→相似度；档位映射 包含关系=①+②/相似度匹配=①+②+③）→文本匹配
  （一问一答 TXT 单格式：`问题|答案`，[1|2] 随机括号兼容切分）→主 KB（门控）→
  LLM 原链；匹配器升级 jieba 分词混合评分（max(字符, 词集 Jaccard×长度惩罚)，
  词条加载时预分词随 mtime 缓存；jieba 不可用自动降级纯字符）。
- **助播音色接线**（此前 audio_synthesis_type 零消费）：助播消息按
  assistant_anchor.audio_synthesis_type 合成（连字符值规范化 edge-tts→edge_tts，
  非法/空回落主链音色告警一次）；音色下拉对齐主链选项集。
- **UI 修复与观测**：触发类型 checkbox 补落盘（原实现无 _auto_save 无
  on_value_change，勾选重启即失效——F2）；文本匹配格式下拉退役 json 项（F6
  实证两格式同一加载器，存量迁移 text）；路由观测面板（最近决策+累计计数，
  新增只读端点 GET /assistant_anchor/route-stats）。
- **实施中发现并顺带修复**：①`local_qa_render` 调用不存在的 random_bracket_value
  （实际为 brackets_text_randomize）——[1|2] 随机语法自始静默失效（F11）；②
  念弹幕缓冲 task_data["read_comment"] 零生产者（timer 回放永远空转）——恢复
  弹幕链生产者（过滤后弹幕入缓冲，上限 500）。③主 local_qa 阈值标定：混合
  评分语料分布 high 0.727-0.909 / miss 0.000-0.429，example 默认 0.8→0.6
  （存量用户显式值不动）。
- 守卫：tests/test_assistant_anchor_routing.py 64 项（路由矩阵/冲突组合/来源
  矩阵/KB 适用集/音色回落/迁移幂等/加载器对抗样本/音频三档/生产点穷尽性 ast
  扫描 D12/checkbox 落盘/性能冒烟 1000 词条<50ms）+ test_matcher_hotfix.py
  13 项。实播验收清单：高并发（≥20 条/分钟）重叠可接受度+助播合成排队 ≤10s
  门槛+复读勾选前后对照。

### 2026-10-26（字幕打印配置优化批次）

- **字幕时长对齐音频有效性审查**：确认 keep_time_align_audio 全链路有效——builtin
  （开播回调载荷三键含 voice_path）/EDTalk/外部播放器/pygame 四推送路径均携带
  voice_path，`_compute_timing` 取实际音频时长（max(音频时长, 自动估算) 保底）且
  start_delay=0（对齐开播）；soundfile 缺失或读失败降级自动估算（既有 M5 语义）。
- **字幕页地址输入移除**：桌面字幕窗口可直接被 OBS 窗口捕捉，无需手拼地址——
  参数卡移除地址回显/复制/浏览器源指引（/captions 网页本身保留，不受影响）。
- **桌面窗口连接状态字隐藏**：连接成功后状态栏自动隐藏（原常驻「已连接」绿字
  会被 OBS 窗口捕捉拍到）；状态栏仅承载连接中/断开/未开启等异常态。守卫 2 项。
- **样式配置下拉化**：字体/字重/文字颜色改下拉（固定常见项，防自由文本写坏
  样式值）；字幕背景色+页面背景色合并为「背景色」（同值双写 bg_color/
  body_bg_color——字幕条底与窗口/页面底统一；透明=none，桌面窗口回退绿幕底
  利于色键抠像）；新增「描边色」（subtitle_stroke_color，网页
  -webkit-text-stroke 颜色；桌面窗口 tk 不支持描边，仅网页生效）。存量配置经
  _ensure_defaults 自动补键。

### 2026-10-24（第二批：待办收尾批次）

- **reread_top_priority 生产端接线**（TODOS 音频批次节 #1）：弹幕链复读触发段
  （高优词表→999 暂停-续播/普通词表→原队列，命中不走 LLM）+filter_config 复读触发卡
  （enable+双词表）+settings reread 节 defaults（此前 config 无此节复读整体死链）+
  聊天页三按钮接线（发送/直接复读/直接复读-插队首→POST core /send）。守卫 5 项。
- **LLM 熔断首连败停顿窗口降档**（TODOS 基础功能节 #2）：LLMBreaker 首败双权重
  （2 次失败即开路，最长停顿 3×超时→2×超时；half-open 探测兜底误开路）。
- **D-15 首启退役键面告知**（TODOS 基础功能节 #9）：`_RETIRED_CONFIG_KEYS` 四键面
  扫描→首启 notify+log 一次性告知（标志防重复）。守卫 2 项。
- **任务 12 长回复切分丢数据根修**（TODOS 音频批次节）：切分段 split_group 组绑定+
  入队前容量预检 warning+四丢弃/取代点组上下文附注（info→warning，丢段可定位）。
  守卫 2 项。
- **v2 backend=api 兜底数据链收敛**（TODOS 基础功能节 #10 评估落地）：死功能实锤
  （UI 参数卡退役+config 无子节存量）——旧六分支退役为 v2 兜底单通道（params
  白名单装配，与 inproc 同源）+synthesis_manager 死透传键清理。
- **gpt_sovits 引擎懒启动评估**（TODOS 基础功能节 #4）：结论=维持无条件启动不实施
  （显存诉求正解=backend=api 外置；首调 10~60s 延迟不可接受；回收致 TTS 断供窗口）。

### 2026-10-24

- **T10 语音对话链接通**（46ae0483）：ASR 引擎适配层（`utils/asr_engine.py`，对齐项目既有
  funasr/fasterwhisper 实现——本地 faster-whisper-large-v3-turbo 零下载+HF 镜像+混合语言路由
  （中文检测→FunASR paraformer 回退））；录音重构为标志式（单击 Q 开始/S 停止交互模型）；
  main.py 停止录音→ASR→对话链串接；`do_listen_and_comment` 连续模式填充（VAD→唤醒→对话链）。
  守卫 50 项，全量 1004 passed。
- **F-1a/F-1b 前端守卫**（34fcf86c）：`test_frontend_style_guards.py`——ast 样式守卫
  （裸色值清零+Tailwind 调色类禁用，实测抓到 login.py:56 跨行漏网）+跨 tab 参数唯一渲染
  断言（白名单制，edtalk_realtime 三键登记）。全量 1009 passed。
- **上游 DanmakuListener replay**（上游 4f4f32a+749768c，已推上游 origin/main）：礼物计价元口径
  G1-G6+发送屏蔽词词表分离回放上游；2 项上游旧断言元口径更新；7 张旧对照表删除收编。
  上游全量 763 passed。临时事实源状态解除。
- **UI-16 四轮验收**（947bed33/05b42620/67b44e04/46056116）：16 项界面调整与修复——暗色适配
  组件类级全修（hint-callout 令牌类）+聊天页 P1 加载崩溃修复+动态文案现代化（fastrag 通道+
  文案直输+存量迁移）+念弹幕/答谢周期合并+过滤开关迁移（D-24 防呆保留）+双页退役+聊天记录
  语义修正（chat_source 来源标记，弹幕链过滤）+落盘归一化透传修复+存量损坏文本自动重组。
  守卫 0→45 项。
- **svc 排障批**（9db4c5e2）：变声页签服务就绪自愈（枚举失败自愈+行动指引）。

### 2026-10-23

- **点歌模式上下级流程接入重构**（99d53dd5）：礼物驱动点歌+gift_song_map 断链接线+四文案
  助播路由+双轨播放。
- **TODOS 待办处理批次**（31708ac1）：UC-2 写库顺序双链实施（违禁弹幕不入库）+实测修复+
  前端卫生，20 项处置。

### 2026-10-22

- **实时变声重构**（354a3cff）：主流程变声退役+独立设备到设备（rvc-vc）+gui_v1 参数全集对齐。
- **动作编排补完**（713ad508）：热启用死路修复+计数窗口+模拟触发端点+观测补全。
- **音频播放审查批次**（1f10f1bc/c917a5dc）：队列四规则+999 暂停续播+变速迁移收尾。
- **字幕打印审查批次**（99680070）：F1 改名+F2 门控收尾+F3 开播回调独立注册点。
- **主干图 v2 分层对齐**（0cf3b0de）：13 差异终门裁决修正+详图归档。
- **弹幕监听模块批次**（34f70bbc）：平台配置页发送行内化+礼物计价元口径归一。
- **基础功能版块**（94acf03c）：LLM 三通道+托底熔断+语言过滤+模板接线+metahuman 退役。
- **日志功能全链退役**（ac8f1e53）：log_config 配置面+三配置源+双写入链+T8 漂移守卫。

### 2026-10-21

- **死代码清理批次**（f0aa2976/24ea2aaf/81d7b6d2）：24 对象移除+仓库卫生门禁+批次验证门模板。
- **断链批次收官**（2ec9ddd2/4075754c/1deb6d75/426204c6/c13ade20）：T5 按键映射/T7 黑名单/
  T8 闲时任务/T9 动态文案/T6 答谢生产端全恢复。
- **动作编排器落地**（12606cad/81dfe182）：事件驱动画面序列+情绪分级+管理页。

### 2026-10-20

- **流程图审查批次**（8c62f397）：T1-T3 图-码对齐重绘+lint 门禁（主干图对齐起点）。

## 历史批次索引

2026-09-24～2026-10-19 的整合浪潮（项目更名 AUTOlive/EDTalk 整合/DanmakuListener 进程内化/
音频三项目/GPT-SoVITS 深化/RVC 深化/实时变声等）见 `docs/开发记录/2026-10-12_整合阶段改动盘点.md`
与 git log；批次审查记录见 `~/.gstack/projects/Eikwang-AUTOlive/`。
