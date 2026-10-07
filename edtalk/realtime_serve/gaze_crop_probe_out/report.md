# Route-C Probe: Generator Eye Retention

- frame 3000: C(输入端)眼区差 1.67 vs 眼区外 1.57（1.1x）；C'(rendered端)眼区差 8.26（5.3x）
- frame 3010: C(输入端)眼区差 1.65 vs 眼区外 1.53（1.1x）；C'(rendered端)眼区差 8.11（5.3x）
- frame 3020: C(输入端)眼区差 1.57 vs 眼区外 1.46（1.1x）；C'(rendered端)眼区差 7.45（5.1x）
- frame 3030: C(输入端)眼区差 1.76 vs 眼区外 1.63（1.1x）；C'(rendered端)眼区差 8.07（4.9x）

## 目检指引

- crop_gen_*.jpg 四联: 1)原经Gen 2)C输入端纠正经Gen 3)C'rendered后纠正 4)纠正输入参考
- **C 验证**（第1 vs 2列）: 已实测——2列不跟随纠正（Generator 抹除），路线 C 输入端失效
- **C' 验证**（第1 vs 3列）: 3列瞳孔应明显朝向正视（区别于 1 列原视向）
  - C' 有效且画质可接受 → 按 C' 实施（Gen输出后纠正 + mask 扩展双眼贴回）
- 同时检查 3 列眼睛质量：色差/伪影/与邻域融合度（mask 扩展后将以此眼替换原生眼）
