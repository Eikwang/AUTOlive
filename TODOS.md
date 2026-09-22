# TODOS.md

## Deferred Items from /autoplan (2026-09-22) — runtime312 启动脚本切换

1. **api_old.py 与 flask_socketio 处置**（P3, S）— 旧 API 入口是否保留/归档；flask_socketio 仅其使用（runtime312 未装）。全仓库依赖审计发现，非两 webui 入口所需。
2. **users.pth 注入升级为整合正式议题**（P2, S）— runtime312 的 `Lib\site-packages\users.pth` 硬编码注入 6 个 GPT-SoVITS 路径进所有进程，存在模块遮蔽风险；启动脚本 sys.path 断言只是临时保险，应从源头移除或收编管理。
3. **EDTalk 目录硬耦合的结构性解耦**（P3, M）— AI-Vtuber 启动命脉指向 `D:\AI\EDTalk\runtime312`，EDTalk 挪动/重装即断；环境复制/软链/搬迁方案待评估。启动预检已缓解。
4. **UC-1 裁决联动**（P2, —）— webui-bak.py 弃用/适配裁决后：若弃用→归档 webui-bak.py + 适配债销案；若适配→nicegui 3.x 逐处适配（与下方第 1 条 2026-09-02 项合并推进）。

## Deferred Items from /autoplan (2026-09-02) — 六项目统一环境整合

1. **gradio/pydantic 版本 API 差异适配**（P1, M）— 统一环境里 gradio 只能装一个版本、AI-Vtuber 需从 pydantic 1.x 适配 2.x。待统一环境落地后作为"后期适配"第一优先。阻塞：统一环境补装完成。
2. **uv 化重建单一可复现环境**（P2, L）— 用 pyproject+lock 替代 conda+裸 pip 的 379+ 包混合环境（conda 导出 requirements 不可靠）。待统一环境稳定运行 1-2 个月后评估。
3. **opencv 三变体收敛为 opencv-contrib-python**（P3, S）— 当前 opencv-python/-contrib/-headless 并存互覆盖；收敛需先冒烟 EDTalk 推理链。
4. **开发/测试依赖与运行依赖分文件**（P3, M）— 统一环境稳定后整理。
5. **start_all.bat 顶层编排一键启动**（P2, M）— 弹幕→中枢→TTS→数字人全链路一键拉起+健康检查；与 UC-1（编排优先还是环境优先）裁决联动。

## Deferred Items from /autoplan (2026-08-31)

### From CEO Phase
1. **自定义布局支持** — 需要新的配置系统和 UI 组件，超出当前优化范围
2. **配置导入导出功能** — 独立功能，与布局优化无关
3. **拖拽重排功能** — 复杂交互，需要仔细设计

### From Design Phase
4. **高级动画效果** — 复杂交互， defer to future iteration
5. **实时预览功能** — 复杂功能，需要新的架构

### From Eng Phase
6. **虚拟滚动优化** — 性能优化，当列表很大时需要
7. **单元测试覆盖** — 测试基础设施，需要补充配置页面和集成测试

## Priority Notes
- P1 tasks (简化导航栏、优化卡片设计、改进配置页面) are IN SCOPE and ready for implementation
- P2 tasks (搜索功能、主题切换、过渡动画) are IN SCOPE and ready for implementation
- P3 tasks (颜色字体统一、响应式布局、加载状态) are IN SCOPE and ready for implementation
- All items above are DEFERRED and should be addressed in future iterations
