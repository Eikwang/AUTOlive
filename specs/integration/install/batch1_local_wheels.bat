@echo off
REM 批次1：本地 wheel 安装（Eng B4/B5 修订：--no-deps + 手工补依赖 + ABI 立验）
REM wheel 源：D:\AI\AICoverGen\dependencies\wheels\（cp312 win_amd64）
set PY=D:\AI\EDTalk\runtime312\python.exe
set W=D:\AI\AICoverGen\dependencies\wheels
set C=D:\AI\install\constraints.txt

echo [1/4] 本地 wheel（--no-deps，防解析器拉坏依赖）
"%PY%" -m pip install --no-deps "%W%\fairseq-0.12.3.1-cp312-cp312-win_amd64.whl" "%W%\diffq-0.2.4-cp312-cp312-win_amd64.whl" "%W%\pyworld-0.3.4-cp312-cp312-win_amd64.whl" || goto :err

echo [2/4] fairseq 手工补依赖（constraints 保护基线）
"%PY%" -m pip install -c "%C%" omegaconf hydra-core sacrebleu bitarray sacremoses || goto :err

echo [3/4] ABI 立验
"%PY%" -c "import fairseq, pyworld, diffq; print('ABI OK', fairseq.__version__)"

echo [4/4] 批次1完成
exit /b 0
:err
echo 批次1失败：检查 wheel 标签/约束冲突后重跑
exit /b 1
