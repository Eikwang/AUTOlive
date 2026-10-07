# 实时推理处理流程审查报告

## 用户期望流程 vs 当前实现

| 步骤 | 期望流程 | 当前实现 | 状态 |
|------|---------|---------|------|
| 1 | 预加载 facemask/metadata | 启动时加载 metadata + facemark numpy | ✅ 已实现 |
| 2 | 预加载 crop 帧（batch） | BatchPreloader 批量加载 | ✅ 已实现 |
| 3 | 预加载 ori 帧（batch） | BatchPreloader 批量加载 | ✅ 已实现 |
| 4 | 静音音频持续推理口型 | 按需调用 `_get_idle_lip_coeff()` | ⚠️ 逻辑正确但非持续推理 |
| 5 | 视线纠正（接口占位） | **完全缺失** | ❌ 无接口 |
| 6 | 面部修复 | 模型已加载但**代码被注释掉** | ❌ 未启用 |
| 7 | facemask 添加遮罩 | composite() 内部实时生成 mask | ⚠️ 每帧重新计算 |
| 8 | metadata 贴回 ori 帧 | composite() 内部完成 | ✅ 已实现 |
| 9 | ori 帧与音频同步输出 | wall-clock 音频驱动 + UnityCapture | ✅ 已实现 |

## 关键问题

### 问题1：面部修复被禁用（代码第388-390行, 第440-442行）

```python
# [DISABLED] Face repair bypassed for sync testing
# if self.face_repair is not None and rendered_np is not None:
#     rendered_np = self.face_repair.repair(rendered_np)
```

模型在启动时加载占用 GPU 显存，但从未使用。要么启用，要么不加载。

### 问题2：视线纠正完全缺失

用户明确要求"保留功能接口占位"，但代码中没有任何相关接口、配置项或占位代码。

### 问题3：facemask 每帧重新生成

`_create_mouth_mask()` 每帧执行两次距离变换（256×256），耗时5-10ms。虽然已有缓存机制（landmark移动>3px才重算），但 mask 生成逻辑本身就在 composite 热路径中。

**更优方案**：facemask 应该作为独立的预处理步骤，在 BatchPreloader 中预计算并缓存。facemark 数据在启动时就已加载，mask 可以在 batch 加载时一并生成。

### 问题4：composite() 职责过重

当前 composite() 在一个函数中完成了：
1. mask 生成（距离变换 + 高斯模糊）
2. resize 256→1268
3. alpha 混合
4. 坐标映射 + 边界裁剪

这些应该拆分为独立步骤，便于：
- 单独优化每个步骤
- 在不同位置插入视线纠正/面部修复
- 预计算可预计算的部分

### 问题5：Generator 推理是最大性能瓶颈

从日志数据看：
```
gen=20-50ms (偶尔139ms)
comp=43-84ms
post=1-43ms (CUDA同步抖动)
```

Generator 的 encoder+decoder 前向传播每帧都要执行，无法避免。但 composite 中的 mask 生成和 resize 可以预计算。

## 建议重构方案

### 步骤1：拆分处理管道为独立阶段

```
当前（耦合）：
  get_frame() → infer_frame() → composite() → send_frame()

重构后（解耦）：
  get_frame() → infer_frame() → [gaze_correct()] → [face_repair()] → apply_mask() → composite_to_ori() → send_frame()
```

### 步骤2：预计算 mask（从 composite 热路径移出）

在 BatchPreloader 加载 batch 时，同时预计算每帧的 mask：
- facemark 数据已在启动时加载
- mask 可以在 batch 加载线程中并行生成
- 每帧从预加载 buffer 中直接读取 mask，无需实时计算

### 步骤3：启用面部修复

取消注释 face_repair 代码，或添加配置开关：
```python
if self.face_repair_enabled and self.face_repair is not None:
    rendered_np = self.face_repair.repair(rendered_np)
```

### 步骤4：添加视线纠正接口

```python
class GazeCorrector:
    """视线纠正占位接口。"""
    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def correct(self, frame: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return frame
        # TODO: 实现视线纠正
        return frame
```

### 步骤5：Config 添加功能开关

```python
face_repair_enabled: bool = False
gaze_correction_enabled: bool = False
```
