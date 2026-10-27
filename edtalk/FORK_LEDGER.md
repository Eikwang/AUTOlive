# EDTalk realtime_serve 分叉台账（FORK LEDGER）

> 建立：2026-10-27 画面输出批次（autoplan main-20261027-120103，合成层 A 方案 uc3a）。
> 此前 edtalk/realtime_serve 全部 18 文件与上游 D:\AI\EDTalk md5 逐一相同（pristine 态）。
> 本文件记录 AUTOlive 侧对上游副本的全部偏离——上游再合并时逐项核对（R-eng11 防漂移）。

## 契约先 行（R-eng2 F-H3）

- **四件套数据格式不动**：orifra/count/metadata/facemark 结构与上游一致；
  「服务器无感知」不变量限定于此。
- **backgrounds/ 目录约定**（新增）：`character_dir/backgrounds/<segment>.(jpg|png)`
  = 该段合成 opt-in 信号（文件存在=合成）；`<segment>.json` = 可选几何覆盖
  `{"head_position":[x,y],"head_scale":s,"canvas":[w,h]}`。由 AUTOlive
  materials.py 发布/删除链维护。
- **画布几何单一来源**：AUTOlive config 键 `edtalk_realtime.canvas_w/canvas_h/
  composite_enabled/composite_default_bg` → 启动参数 `--canvas-w/--canvas-h/
  --no-composite/--composite-default-bg` 下发（F-M3；/status composite 回显生效值）。

## 上游文件修改（逐处）

| 文件 | 位置 | 性质 | 内容 |
|---|---|---|---|
| config.py | Config 字段区 | 追加 | `canvas_w/canvas_h/composite_enabled/composite_default_bg` 四字段（默认 1280/720/True/None） |
| main.py | parse_args | 追加 | `--canvas-w/--canvas-h/--no-composite/--composite-default-bg` 四参数 |
| main.py | setup_service 摄像头初始化 | 修改 | 合成层启用时摄像头按画布尺寸打开（原=首段分辨率）；禁用时保持上游语义 |
| main.py | setup_service 组件注入区 | 追加 | CanvasCompositor 构造 + `render_loop.canvas_compositor` 注入（初始化失败→None=直出降级） |
| render_loop.py | :743-752 直通出帧口 | 修改 | orifra 直出前经 `_composite_out()` 改道 |
| render_loop.py | :848-861 blend 出帧口 | 修改 | blend 产物入 FrameBuffer 前经 `_composite_out()` 改道 |
| render_loop.py | :277-283 resolution_change_cb | 守卫 | 合成层在场时跳过逐段分辨率重建（统一画布政策——重建仅会话启动） |
| render_loop.py | _resolve_mask 后 | 追加 | `_composite_out()` 方法（统一出帧出口；异常直出降级） |
| service.py | get_status | 追加 | `status['composite']` 计数回显（stats()） |

## 新增文件

| 文件 | 说明 |
|---|---|
| realtime_serve/canvas_compositor.py | 统一画布合成器：段背景懒加载（每段贴一次）/头部 ROI 每帧一次/专用画布缓冲（出帧=拷贝防撕裂）/contain 适配（非合成素材）/连续异常自动旁路+计数（R-eng11①②④） |
| （AUTOlive 侧）utils/edtalk_realtime/stub_pieces.py | 桩四件套生成器（R-eng4①，经 runtime 解释器执行） |

## 上游同步程序

1. 上游更新后：`python Scripts/copy_edtalk.py` 全量复制 → 本台账逐项核对
   （修改点均为幂等追加/守卫，重放成本 S）；
2. canvas_compositor.py 为 AUTOlive 独有——上游合并不会触碰；
3. 分叉治理升级条件：上游若原生推出全帧推理/画布合成，评估退役本分叉
   （R-ceo9 战略备注——sidecar 资产层不受影响）。
