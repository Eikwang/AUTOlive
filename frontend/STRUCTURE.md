# AUTOlive 项目结构

## 目录结构

```
frontend/
├── __init__.py                    # 主包初始化
├── main.py                        # 主入口文件
├── config/                        # 配置模块
│   ├── __init__.py
│   ├── settings.py                # 配置管理
│   └── paths.py                   # 路径管理
├── utils/                         # 工具模块
│   ├── __init__.py
│   ├── common.py                  # 通用工具
│   └── helpers.py                 # 辅助函数
├── ui/                            # UI模块
│   ├── __init__.py
│   ├── layout.py                  # UI布局
│   ├── components/                # UI组件
│   │   ├── __init__.py            # 组件导出
│   │   ├── navigation.py          # 导航标签页组件
│   │   ├── theme.py               # 主题管理组件
│   │   ├── search.py              # 搜索过滤组件
│   │   ├── control_buttons.py     # 控制按钮组件
│   │   ├── login.py               # 登录表单组件
│   │   ├── expansion.py           # 折叠面板组件
│   │   └── COMPONENTS.md          # 组件文档
│   └── tabs/                      # 标签页模块
│       ├── __init__.py
│       ├── common_config.py       # 通用配置标签页
│       ├── llm.py                 # 大语言模型标签页
│       ├── tts.py                 # 文本转语音标签页
│       ├── svc.py                 # 变声标签页
│       ├── visual_body.py         # 画面设置标签页（EDTalk 实时推理配置+运行控制）
│       ├── train_streamer.py      # 训练主播标签页（一键全链路训练）
│       ├── copywriting.py         # 文案标签页
│       ├── talk.py                # 聊天标签页
│       ├── image_recognition.py   # 图像识别标签页
│       ├── integral.py            # 积分标签页
│       ├── assistant_anchor.py    # 助播标签页
│       ├── translate.py           # 翻译标签页
│       ├── serial.py              # 串口标签页
│       ├── data_analysis.py       # 数据分析标签页
│       └── web.py                 # 页面配置标签页
└── styles/                        # 样式模块
    ├── __init__.py
    └── themes.py                  # 主题管理
```

## 模块说明

### 配置模块 (config/)
- **settings.py**: 配置管理类，负责读取和写入配置文件
- **paths.py**: 路径管理类，处理文件路径和目录结构

### 工具模块 (utils/)
- **common.py**: 通用工具类，提供常用的工具方法
- **helpers.py**: 辅助函数模块，包含各种辅助功能

### UI模块 (ui/)
- **layout.py**: UI布局管理器，负责创建主界面布局

#### UI组件 (ui/components/)
提供可复用的UI组件：
- **NavigationTabs**: 导航标签页组件
- **ThemeManager**: 主题管理组件
- **SearchFilter**: 搜索过滤组件
- **ControlButtons**: 控制按钮组件
- **LoginForm**: 登录表单组件
- **ExpansionPanel**: 折叠面板组件

#### 标签页模块 (ui/tabs/)
各个功能标签页的实现：
- **common_config.py**: 通用配置标签页
- **llm.py**: 大语言模型配置标签页
- **tts.py**: 文本转语音配置标签页
- **svc.py**: 变声配置标签页
- **visual_body.py**: 画面设置配置标签页（驱动类型/EDTalk 配置/运行控制区）
- **train_streamer.py**: 训练主播标签页（预处理+微调+评估+口型训练一键编排）
- **copywriting.py**: 文案配置标签页
- **talk.py**: 聊天配置标签页
- **image_recognition.py**: 图像识别配置标签页
- **integral.py**: 积分配置标签页
- **assistant_anchor.py**: 助播配置标签页
- **translate.py**: 翻译配置标签页
- **serial.py**: 串口配置标签页
- **data_analysis.py**: 数据分析标签页
- **web.py**: 页面配置标签页

### 样式模块 (styles/)
- **themes.py**: 主题管理，负责样式配置

## 依赖关系

```
main.py
  ├── config.settings
  ├── config.paths
  ├── utils.common
  ├── ui.layout
  │   ├── ui.components.navigation
  │   ├── ui.components.theme
  │   └── ui.components.control_buttons
  └── ui.tabs.*
```

## 设计原则

1. **高内聚低耦合**: 每个模块专注于单一职责
2. **可复用组件**: UI组件设计为可复用的独立单元
3. **配置驱动**: 通过配置文件控制UI样式和行为
4. **模块化**: 功能按模块组织，便于维护和扩展
## 内置字幕页（frontend/web_captions/，2026-09-25 整合新增）

| 文件 | 内容 | git 处置 |
|---|---|---|
| web_captions/index.html | 字幕显示页 DOM（#subtitle_bg > #subtitle + 状态横幅） | 入库 |
| web_captions/index.js | 渲染逻辑（渐显/打字机/隐藏）+XSS 转义+D1 初始同步+断线自检+透明语义 | 入库 |
| web_captions/index.css | 字幕样式子集（用户配置驱动，不走 design_tokens——面向 OBS 浏览器源） | 入库 |
| web_captions/socket.io.js | 本地打包 socket.io 客户端（源项目复制，避免 CDN 依赖） | 入库 |

服务挂载：主系统进程 utils/web_server.py（路由 /captions、/captions/{fname}、
socket.io /captions_ws/socket.io、/builtin_status、/builtin_control）。
