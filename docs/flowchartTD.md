```mermaid
flowchart TD
    %% ==================== 主干图元数据 ====================
    %% 版本：v2.2 —— 2026-10-26 流程优化批次（消息遗忘/去重前移 Door+助播音频分流+编排-数字人口型关联+Listenoff 任务解耦；v2.1 见 git 历史）
    %% 图例：四态语义〔真实/断链/骨架/规划〕v1 定义见 docs/flowchart-detail.md:9-11（归档详图；修订四态语义须迁至活文档）；行号为书写时参考，随重构过期以符号名为准
    %% 纪律：改链必改图 —— 对应批次须同步检查本图节点/边（TODOS 2026-10-20 节 #5；tests/test_flowchart_alignment.py 守卫）
    %% 样式定义
    classDef startNode fill:#FFD3D3,stroke:#FF6666,stroke-width:2px,color:#000;
    classDef listenNode fill:#A8D5FF,stroke:#3399FF,stroke-width:1px,color:#000;
    classDef sysTaskNode fill:#3399FF,stroke:#0066CC,stroke-width:1px,color:#000;
    classDef processNode fill:#D9EDFF,stroke:#99CCFF,stroke-width:1px,color:#000;
    classDef logicNode fill:#FFFFFF,stroke:#333333,stroke-width:1px,color:#000;
    classDef subLogicNode fill:#F0F8FF,stroke:#CCCCCC,stroke-width:1px,color:#000;
    classDef endNode fill:#F4F4F5,stroke:#999999,stroke-width:1px,color:#000;

    %% 主线程
    subgraph 系统主进程
        System([系统启动<br>开始运行]):::startNode
    end

    %% 聊天模块
    subgraph 语音交互模块
        System --> Chat([聊天功能<负责本地用户互动>]):::listenNode
        Chat --> Capture[捕获用户发言<br>文字信息&语音对话]:::listenNode
    end

    %% 弹幕监听与功能触发
    subgraph 弹幕与功能处理模块
        System --> Listening[已开启 <br> 弹幕监听]:::listenNode
        Listening --> HasDM(有弹幕消息):::listenNode
        Listening --> NoDM(无弹幕消息):::listenNode
        HasDM --> Classify[分类处理<br>弹幕信息]:::listenNode
        Classify --> Door(消息限入<br>去除重复<br>遗忘窗口+限时去重<br>timer_handler.process_data<br>全信息源统一入口<br>弹幕/聊天/任务产出均经此):::listenNode
        Door --> Filter[黑名单<br>敏感词过滤]:::listenNode
        Filter --> Keyword[关键词<br>功能触发识别]:::listenNode
        Keyword --> Keymap{是否触发<br>按键映射}:::listenNode
        Keyword --> SongMode[已开启<br>点歌模式]:::subLogicNode
        Keyword --> Points[已开启<br>积分系统]:::subLogicNode
        Points --> Calc(计算积分):::subLogicNode
        Points --> look(触发查询积分):::subLogicNode
        Keymap --> |触发| SendPort(发送串口):::subLogicNode
        SongMode --> Match(匹配成功):::subLogicNode
        Match --> done(消费成功文案):::subLogicNode
        Match  --> sing(播放歌曲音频):::subLogicNode
        SongMode --> miss(匹配失败):::subLogicNode
        miss --> misMatch(消费失败文案):::subLogicNode
        Calc --> Stats
        Keyword --> Stats{数据统计}:::listenNode
        Stats --> |开启| DB[写入数据库]:::listenNode
        Stats --> |关闭| Ignore[忽略数据]:::listenNode
        DB --> Result(数据统计):::listenNode
        Keyword --> Interact(答谢&互动):::listenNode

    end

    %% 系统任务
    subgraph 系统任务模块
        System --> Listenoff[未开启 <br> 弹幕监听]:::listenNode
        Idle[触发闲时任务]:::subLogicNode
        Timed[触发定时任务]:::subLogicNode
        DynamicCopy[触发动态文案]:::subLogicNode
        Listenoff --> Idle
        Listenoff --> Timed
        Listenoff --> DynamicCopy
        NoDM --> Idle
        NoDM --> Timed
        NoDM --> DynamicCopy
        Capture --> Keyword
        Idle --> Keyword
        Timed --> Keyword
        DynamicCopy --> Keyword        
    end

    %% 核心处理流程
    subgraph 信息处理模块
        Queue{待处理<br>消息队列}:::logicNode
        SendPort --> Queue
        sing --> Queue
        done --> Queue
        misMatch --> Queue
        Interact --> Queue
        look --> Queue
        Queue --> Sort(优先级<br>动态排序):::logicNode
        Sort --> TranslateQ{是否翻译}:::logicNode
        TranslateQ --> |开启| Translate[翻译为<br>指定语言]:::logicNode
        TranslateQ --> |关闭| KBQ{是否助播}:::logicNode
        Translate --> KBQ
        KBQ --> |否|SysKB[系统指定<br>知识库]:::logicNode
        KBQ --> |是|AsstKB[助播知识库]:::logicNode
        SysKB --> SysTTS[系统指定<br>TTS]:::logicNode
        AsstKB --> AsstTTS[助播TTS]:::logicNode
        SysTTS --> SpeedQ{是否开启<br>随机语速}:::logicNode
        AsstTTS --> SpeedQ
        SpeedQ --> |开启|ChangeSpeed[调整语速]:::logicNode
        SpeedQ --> |关闭|DefaultSpeed[默认语速]:::logicNode
        ChangeSpeed --> GenerateAudio(生成音频):::logicNode
        DefaultSpeed --> GenerateAudio
        GenerateAudio --> AudioList[待播放<br>音频列表]:::logicNode
    end 
    %% 字幕打印
    subgraph 字幕打印功能
        AudioList --> Subtitle(字幕打印<br>推送点=三个开播精确位<br>playback_manager.py:160-165<br>字幕数据随条目流转):::logicNode
        Subtitle --> |开启|Sync(对齐字幕<br>同步显示):::logicNode 
    end       
    %% 输出与播放模块
    subgraph 音频播放模块
        AvatarAI --> |开启|AvatarPlay[数字人<br>消费音频]:::logicNode
        AudioList --> |系统流程|AvatarAI[数字人处理<br>开=EDTalk 帧锁定推送口型<br>关=本地播放器/pygame]:::logicNode
        AvatarAI --> |关闭|SystemPlay(系统消费音频):::logicNode
        AudioList --> |助播流程<br>assistant_anchor 条目<br>绕过数字人直接外放|SystemPlay
        AvatarAI -.-> |R6 双轨伴奏| Backing[伴奏本地外放<br>playback_manager.py:199-211<br>人声 push_full :241-243]
    end
    %% 画面输出模块
    subgraph 画面播放模块  
        Sort --> Compile{动作编排<br>上级=优先级排序：编排音频经<br>audio_enqueue 回注合成队列<br>触发源=分类分发层事件订阅<br>keyword_hub.py:314·:325<br>feature_handler.py:534}:::logicNode
        Compile --> |不需口型<br>audio_mode=none 纯画面|Frame
        Compile --> |需要口型<br>tts/file 语音步骤|AvatarPlay
        AvatarPlay --> Frame(输出<br>画面&音频&字幕):::logicNode 
        Sync --> Frame
        SystemPlay --> Frame
    end    
```
## v2.2 流程优化批次对账（2026-10-26，用户终图+代码实施）

- 调整项 1（Door 前移）：**代码已实现**——danmaku_consumer.py:494-547 分类分发后即 process_data（限时去重+遗忘窗口，timer_handler.py:269-293），到期 process_last_data 流入 comment_handle（Filter→Keyword）；聊天/任务/语音源同入口（web_server.py:312/task_scheduler.py:114,220,228/conversation_manager.py:68）。图 Door 节点补全信息源注记；Capture/Idle/Timed/DynamicCopy→Keyword 直连边为主干图近似（代码同经 Door）。
- 调整项 2（编排-数字人口型关联）：**代码已实现**——in_mute_window（audio_mode=none=不需口型→纯画面窗口）+tts/file 步骤正常驱动口型（playback_manager.py:212-237+choreographer.py:688-705）；图两路边标签补代码语义。
- 调整项 3（编排上级=优先级排序）：**用户语义裁决**——编排音频经 audio_enqueue 回注合成队列（choreographer.py:582），Sort→Compile 边保留；触发源=分类分发层事件订阅（keyword_hub.py:314/:325+feature_handler.py:534）以注记保留。
- 行为变更（助播分流）：**已实施**——playback_manager 新增 routes_to_edtalk（ASSISTANT_PLAYBACK_TYPES 绕过数字人直接外放，only_play_audio 分流点接线），守卫 4 项入 test_audio_playback_review.py（TestAssistantPlaybackRouting）。
- Listenoff（未开启监听仍触发任务）：**代码已实现**——main.py:260 task_scheduler.start_all() 无条件启动。
- 守卫恢复：用户手改覆盖了 v2.1 图头元数据与注记（T3 断言 4 项红精确预警，native Eng F2 预言场景重演）——本次合并恢复图头三行（v2.2）+Door/Compile/Subtitle/AvatarAI 注记+Backing 节点；T3 断言更新为 v2.2 语义（带标签双路+无标签歧义边守卫，防假绿抽查通过）+ANCHOR_REGISTRY 补 choreographer.py。
- 本批改动文件：docs/flowchartTD.md、utils/audio/playback_manager.py、tests/test_flowchart_alignment.py、tests/test_audio_playback_review.py、TODOS.md（助播行为变更待实播验收：助播文案/音频不再出数字人口型，改系统外放）。
