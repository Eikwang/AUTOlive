# 安装包构建说明（P1-5，首版策略）

> 首版选装（已批准）：捆绑 GPT-SoVITS v2ProPlus 单一底模族 + RVC 底模 + 虚拟声卡驱动；
> 其余底模族与分离模型按需下载（不计入 30 分钟口径）。逐项 SHA-256 校验（S-3）。

## 构成（装后磁盘布局，CEO-F6：AUTOlive 根下 apps/）

```
AUTOlive/               ← 仓库代码（git clone 或 zip）
  apps/
    gpt-sovits/         ← GPT-SoVITS 代码副本（EDTalk 范式：复制为唯一副本）
    rvc/                ← RVC 代码副本（P2 接入）
    aicovergen/         ← AICoverGen 代码副本（P3 接入）
  models/rvc/           ← 共享 RVC 模型（P1-4 已建）
  runtime/              ← 内嵌 python（= runtime312-clone 打包，含环境补丁五条目）
  config/               ← tts_infer yaml（绝对路径按装机根生成）
  start_autolive.bat    ← 启动器（本仓库根）
```

## 构建步骤（人工先行；CI 化为后续演进项）

1. 复制三项目代码进 apps/（参考 Scripts/copy_edtalk.py 范式，复制为唯一副本）。
2. 打包 runtime/：复制 runtime312-clone 并执行 docs/开发环境搭建.md 环境补丁五条目。
3. 生成 config/tts_infer yaml：底模/权重路径按装机根重写为绝对路径。
4. 生成 assets-manifest.json：逐项体积+SHA-256（S-3）+许可（docs/许可核查清单.md）。
5. 安装器检测最低配置（RTX 3060 12GB，CEO-F3），未达标→降级提示可继续（D2）。
6. 首屏出声测试引导 + 画面缺失提示（D11）；安装失败页含日志位置（DX-8/X1）。
7. 版本号目录落盘，回滚=切回旧版本目录（S9）。

## 状态

- [x] 启动器 start_autolive.bat（本仓库根，8086/9880 双探活）
- [ ] apps/ 复制脚本（参照 copy_edtalk.py 泛化——待实施）
- [ ] assets-manifest.json 生成器（待实施）
- [ ] 首台干净机器装机实测（北极星第 1 样本）
