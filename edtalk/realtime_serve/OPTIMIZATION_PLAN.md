# 实时推理服务帧率优化方案

## 问题诊断

### 根因分析

当前 `sequence_frame_pack.py` 的懒加载设计存在严重的 I/O 瓶颈：

1. **每帧都触发磁盘读取**：`get_frame()` 每次调用都执行 `cv2.imread()` 读取 orifra 帧（~125KB/帧），25fps = 每秒25次磁盘随机读取
2. **LRU 缓存命中率低**：crop 帧的 LRU 缓存仅512帧（~20秒），音频驱动的 `lip_idx` 可能跳转导致缓存失效
3. **每帧 CPU→GPU 传输**：cache miss 时需要 `cv2.imread` + numpy→tensor + CPU→GPU 传输，延迟不可控
4. **orifra 完全无缓存**：每次都要从磁盘读取1080p JPEG 并解码

### 性能数据

| 项目 | 当前开销 | 目标 |
|------|---------|------|
| orifra 磁盘读取 | 每帧1次 (~125KB) | 批量预加载 |
| crop 帧加载 | 每帧1次 (cache miss时) | 批量预加载到GPU |
| 帧间延迟 | 40-80ms+ (I/O阻塞) | <5ms (内存读取) |
| 实际帧率 | 个位数 fps | 稳定25fps |

## 优化方案：批量滑动窗口预加载

### 核心思路

将"逐帧懒加载"改为"批量预加载 + 滑动窗口"：

```
时间线：
[=== Batch 0 (帧0-99) ===][=== Batch 1 (帧100-199) ===][=== Batch 2 (帧200-299) ===]...
     ↑ 当前渲染                    ↑ 后台预加载
```

- **窗口大小**：200帧（2个批次 ×100帧）
- **当前批次**：正在渲染的100帧，数据已在内存/GPU
- **预加载批次**：后台线程加载下一个100帧
- **循环加载**：当渲染跨过批次边界时，触发下一批次加载

### 内存预算

| 数据类型 | 每帧大小 | 200帧总计 | 说明 |
|---------|---------|----------|------|
| orifra (CPU) | ~125KB | ~25MB | 1080p JPEG 解码后 numpy |
| crop tensor (GPU) | ~786KB | ~157MB | 256×256×3 float32 |
| metadata/facemark | 已常驻 | 已常驻 | 启动时加载 |
| **总计** | | **~182MB** | RTX 4080S 16GB 完全够用 |

## 实现计划

### 步骤1：新增 `BatchPreloader` 类

在 `sequence_frame_pack.py` 中新增批量预加载器：

```python
class BatchPreloader:
    """批量滑动窗口预加载器

    维护两个批次的帧数据：
    - current_batch: 正在使用的批次（内存/GPU 中）
    - next_batch: 后台预加载的批次

    当渲染帧跨过批次边界时，自动触发下一批次加载。
    """

    BATCH_SIZE = 100  # 每批次帧数
    NUM_BATCHES = 2   # 窗口中的批次数（当前 + 预加载）

    def __init__(self, character_data, device='cuda'):
        self._character_data = character_data
        self._device = device

        # 双缓冲批次
        self._batches = [None, None]  # [current, next]
        self._batch_start_indices = [-1, -1]  # 每批次的起始帧号
        self._current_batch_idx = 0  # 0 或 1

        # 预加载线程
        self._preload_thread = None
        self._preload_lock = threading.Lock()
        self._preload_event = threading.Event()

    def _get_batch_for_frame(self, frame_idx):
        """检查 frame_idx 是否在当前窗口中"""
        ...

    def ensure_loaded(self, frame_idx):
        """确保 frame_idx 对应的帧数据可用

        如果 frame_idx 在当前批次中 → 直接返回
        如果 frame_idx 在预加载批次中 → 切换批次，触发新预加载
        如果都不在 → 同步加载 + 触发预加载
        """
        ...

    def _preload_batch_async(self, start_idx):
        """后台线程：预加载从 start_idx 开始的 BATCH_SIZE 帧"""
        ...

    def get_frame(self, idx):
        """获取帧数据（从预加载缓存中读取）"""
        ...
```

### 步骤2：修改 `CharacterDataPack`

1. 新增 `get_frames_batch(segment, start_idx, count)` 方法，批量读取帧数据
2. 在 `__init__` 中初始化 `BatchPreloader`
3. 修改 `get_frame()` 使用预加载器

### 步骤3：修改 `RenderLoop`

1. 在 `_process_batch()` 中调用 `character_data.ensure_loaded(lip_idx)`
2. 确保预加载器在切换 segment 时重置

### 步骤4：优化 crop tensor 传输

1. 使用 `torch.cuda.Stream` 异步传输 crop tensors 到 GPU
2. 批量 `torch.stack()` 而非逐帧 `.unsqueeze(0)`

## 关键文件变更

| 文件 | 变更内容 |
|------|---------|
| `realtime_serve/sequence_frame_pack.py` | 新增 `BatchPreloader`，修改 `CharacterDataPack` |
| `realtime_serve/render_loop.py` | 调用 `ensure_loaded()` 预加载触发 |
| `realtime_serve/config.py` | 新增 `batch_size`、`num_batches` 配置项 |

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| segment 切换时预加载延迟 | 首帧延迟~200ms | 切换时同步加载第一个批次 |
| 内存不足（极端情况） | OOM | 可配置 batch_size，降级到100帧窗口 |
| 预加载线程与渲染线程竞争 | 帧率波动 | 使用独立 CUDA stream + 锁保护 |

## 验证方案

1. **帧率测试**：运行服务，监控日志中的 FPS 输出，应稳定在24-26fps
2. **内存测试**：监控 GPU 显存使用，确认不超过预算
3. **同步测试**：播放音频，确认唇形同步无漂移
4. **压力测试**：连续运行5分钟，确认无内存泄漏或帧率衰减
