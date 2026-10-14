# -*- coding: utf-8 -*-
"""AUTOlive 自有切分（深化整合）：上游 slicer2 静音切分 + max_sec 硬上限，一道流程到位。

与上游 tools/slice_audio.py 的 11 argv 兼容，末尾追加可选 max_sec（默认 8）：
chunk 超 max_sec 时在其内部按最低能量点细分（静音优先、硬切兜底），
保证全部产物 <= max_sec——不做事后再扫描再切分的第二道流程（2026-10-14 用户裁决）。

背景：GPT(AR) 训练片段 >10s 会 OOM；歌曲干声等连续音频绕过静音切分
（threshold 只认静音），上限必须在切分流程内一次到位。

用法（cwd=gsv_root，参数与上游一致）：
    python utils/smart_slice_audio.py <inp> <opt_root> <threshold> <min_length>
        <min_interval> <hop_size> <max_sil_kept> <_max> <alpha> <i_part> <all_parts> [max_sec]
"""
import os
import sys
import traceback

import numpy as np
from scipy.io import wavfile

sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
from slicer2 import Slicer       # noqa: E402  上游静音切分类（复用，不重建）
from my_utils import load_audio  # noqa: E402


def _subdivide(chunk, sr, max_sec):
    """超长 chunk 细分生成器：0.95*max_sec 窗口、窗口尾部 1.5s 内最低能量点
    优先切（静音处），无静音则硬切兜底；子段 >=1s 防碎片；
    cut 严格钳制在 max_sec 内（静音搜索向后看 1.5s 但不得越上限——2026-10-14
    实测 9.1s 产物即源于 argmin 落在 end+edge 处）。"""
    n = len(chunk)
    win = int(max_sec * 0.95 * sr)
    edge = int(1.5 * sr)
    hard = int(max_sec * sr)
    idx = 0
    while idx < n:
        end = min(idx + win, n)
        if end < n - edge:
            seg = chunk[max(0, end - edge):min(n, end + edge)]
            energy = np.convolve(seg ** 2, np.ones(320) / 320, "same")
            cut = int(max(0, end - edge) + np.argmin(energy))
            cut = max(cut, idx + sr)
            cut = min(cut, idx + hard)  # 硬上限：静音搜索不得使产物超 max_sec
        else:
            cut = end
        yield chunk[idx:cut]
        idx = cut


def slice(inp, opt_root, threshold, min_length, min_interval, hop_size,
          max_sil_kept, _max, alpha, i_part, all_part, max_sec=8.0):
    os.makedirs(opt_root, exist_ok=True)
    if os.path.isfile(inp):
        inputs = [inp]
    elif os.path.isdir(inp):
        inputs = [os.path.join(inp, name) for name in sorted(os.listdir(inp))]
    else:
        return "输入路径存在但既不是文件也不是文件夹"
    slicer = Slicer(
        sr=32000, threshold=int(threshold), min_length=int(min_length),
        min_interval=int(min_interval), hop_size=int(hop_size),
        max_sil_kept=int(max_sil_kept),
    )
    _max = float(_max)
    alpha = float(alpha)
    max_sec = float(max_sec)
    for inp_path in inputs[int(i_part)::int(all_part)]:
        try:
            name = os.path.basename(inp_path)
            audio = load_audio(inp_path, 32000)
            for chunk, start, _end in slicer.slice(audio):
                pieces = list(_subdivide(chunk, 32000, max_sec)) \
                    if len(chunk) > max_sec * 32000 else [chunk]
                for pi, piece in enumerate(pieces):
                    tmp_max = np.abs(piece).max()
                    if tmp_max > 1:  # 上游同款响度混合（tmp_max==0 跳过防除零）
                        piece = piece / tmp_max
                        piece = (piece * (_max * alpha)) + (1 - alpha) * piece
                    # 命名唯一性：chunk 起点 + 段内序号（细分后同一 chunk 多段）
                    wavfile.write(
                        "%s/%s_%010d_%010d.wav" % (opt_root, name, start + pi, start + pi + len(piece)),
                        32000,
                        (np.clip(piece, -1, 1) * 32767).astype(np.int16),
                    )
        except Exception:
            print(inp_path, "->fail->", traceback.format_exc())
    return "执行完毕，请检查输出文件"


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) >= 12:
        print(slice(*argv[:11], max_sec=float(argv[11])))
    else:
        print(slice(*argv))
