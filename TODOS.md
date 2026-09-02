# TODOS.md

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
