"""人声静音段数据文件（.silences.json）单一契约（2026-11-05 编排优化批次 T5；
2026-11-10 实测反馈批次参数修正→ground truth 校准回滚）。

裁决条款：
- d15：schema={version:1, threshold_db:-40, min_silence_s:3,
  silences:[{start_s,end_s}]}（时间单位秒，浮点）；存放=翻唱成品旁同名
  .silences.json（covers/{safe}__{profile_id}.silences.json）；解析器/切片器/
  单测共同契约。
- **ground truth 校准（2026-11-10 R4c，用户手动校准文件验证）**：-20dB/0.3s
  （用户上轮误拍）产生 34 段（31 个换气碎段误判——0.3s 最短静音把句间换气
  全部检出）；**-40/3.0 在用户校准的 ground truth 下输出=标准答案**（3 段
  真间奏，且 -40/-35/-30/-20 全部一致——阈值不敏感区，最短静音才是分界）。
  检测参数回滚 -40/3.0，并开放 config 键（choose_song.silence_threshold_db /
  silence_min_silence_s，缺省不落盘）供特殊歌曲调参。
  插播窗口粒度与检测标准解耦不变：≥min_interlude_s（消费端，默认 1.0s）的
  静音段才作间奏（切段+插播窗口）。
- manual 标记（R4c）：文件加 `"manual": true` = 用户人工精修——load_silences
  跳过参数一致性校验+消费端跳过重算（人工值全责，不被自动重算覆盖）。
- F-H：解析显式校验 math.isfinite / 0≤start<end / 区间排序去重（Python json
  默认接受 NaN/Infinity 字面量；NaN 参与比较恒 False 静默错乱——违规一律
  按 e12 回退处置，不抛错阻塞点歌链）。
- e12：损坏/半写/畸形文件→返回 None（调用方回退 45s 固定切片+观测桶），
  任何异常不外抛。
- 失效重算（2026-11-10）：文件内 threshold_db/min_silence_s 与当前 DEFAULT
  不一致（含字段缺失）→ load_silences 返回 None——与「文件缺失」「文件
  损坏」三种 None 统一走消费端惰性解析链路重算。**manual=true 豁免**。
- d20/F-C：存量翻唱首次点播惰性解析（后台）由消费端调度；本模块提供
  compute_silences（RMS 扫描）与 write_silences_file（原子写）供生成端
  （cover_worker）与惰性解析共用。生成端以无参方式调用本模块函数→
  常量变更即生成端自动跟随（cover_worker.py 无显式参数）。

静音判定口径：RMS dBFS < threshold_db(-40) 连续 ≥ min_silence_s(3s) 的时段
（窗口 100ms 步进；数字静音 dBFS=-inf 同判）。
"""

import json
import math
import os

from utils.my_log import logger

SCHEMA_VERSION = 1
DEFAULT_THRESHOLD_DB = -40.0
DEFAULT_MIN_SILENCE_S = 3.0
_WINDOW_MS = 100  # RMS 扫描窗口（100ms 步进）


def silences_path_for(full_mix_path: str) -> str:
    """d15 旁挂命名：成品路径 → 同名 .silences.json（covers/{base}.wav →
    covers/{base}.silences.json）。"""
    return os.path.splitext(full_mix_path)[0] + ".silences.json"


def _valid_silences(raw) -> bool:
    """F-H 值域校验：list[{start_s,end_s}]，isfinite+0≤start<end。"""
    if not isinstance(raw, list):
        return False
    for item in raw:
        if not isinstance(item, dict):
            return False
        s, e = item.get("start_s"), item.get("end_s")
        for v in (s, e):
            if not isinstance(v, (int, float)) or isinstance(v, bool) \
                    or not math.isfinite(float(v)):
                return False
        if not (0 <= float(s) < float(e)):
            return False
    return True


def _normalize(silences):
    """F-H：排序+重叠/相接区间合并（生成端重复写入与手编文件的共同防线）。"""
    runs = sorted(((float(i["start_s"]), float(i["end_s"]))
                   for i in silences), key=lambda p: (p[0], p[1]))
    merged = []
    for s, e in runs:
        if merged and s <= merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1][1] = e
        else:
            merged.append([s, e])
    return [{"start_s": s, "end_s": e} for s, e in merged]


def parse_silences_document(doc):
    """schema 校验+规整。合法返回 [{start_s,end_s}]（可为空列表=无间奏）；
    非法返回 None（调用方按 e12 回退）。"""
    if not isinstance(doc, dict):
        return None
    if doc.get("version") != SCHEMA_VERSION:
        return None
    for key in ("threshold_db", "min_silence_s"):
        v = doc.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) \
                or not math.isfinite(float(v)):
            return None
    if not _valid_silences(doc.get("silences")):
        return None
    return _normalize(doc["silences"])


def load_silences(path: str):
    """e12 fail-soft 读取：文件缺失/损坏/半写/畸形 → None（不抛错）。
    合法返回 [{start_s,end_s}]（空列表=合法「无间奏」语义）。

    2026-11-10 失效重算：文件内 threshold_db/min_silence_s 与当前 DEFAULT
    不一致（含字段缺失）→ None——旧参数文件自动失效，统一走消费端惰性
    解析链路重算（本播固定切片回退+后台写回新参数）。日志区分两种 None
    成因（参数不一致 vs 畸形），便于观测检出率。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    parsed = parse_silences_document(doc)
    if parsed is None:
        logger.warning(
            f"[silences] 数据文件畸形已忽略：{path}；problem=配置告警；"
            "cause=schema 校验未过（版本/值域/区间）；fix=删除该文件由惰性解析重建"
            "（或重翻歌曲生成）；本次点歌回退固定切片")
        return None
    if bool(doc.get("manual")):
        # 人工精修豁免（R4c）：全责采用——参数/时间戳均以文件为准，不重算
        # 不覆盖（用户校准路径；上一批实测手动校准被重算覆盖的根治）
        logger.info(f"[silences] 人工精修数据已采用（manual=true，跳过重算）：{path}")
        return parsed
    try:
        if float(doc.get("threshold_db")) != DEFAULT_THRESHOLD_DB \
                or float(doc.get("min_silence_s")) != DEFAULT_MIN_SILENCE_S:
            # 参数不一致（含字段缺失→float(None) TypeError 下方 except 兜底）→
            # 失效重算（三种 None 统一处置）
            logger.info(
                f"[silences] 数据文件参数不一致已失效（trigger=重算）：{path}"
                f"（文件 threshold={doc.get('threshold_db')}/min_s={doc.get('min_silence_s')}"
                f"，当前 DEFAULT={DEFAULT_THRESHOLD_DB}/{DEFAULT_MIN_SILENCE_S}）"
                "——本次点歌固定切片回退，后台按新标准重算写回")
            return None
    except (TypeError, ValueError):
        return None  # 字段缺失/非数值=不一致（parse 已拒畸形，此处兜底）
    return parsed


def _adaptive_threshold(dbs):
    """自适应阈值（R4d，用户指令「结合音频频谱优化识别」）：整曲 RMS 分布
    的「人声峰基准偏移」——按歌自动找分界，替代固定阈值对响度差异的脆弱性
    （轻柔歌人声 RMS 可低于 -40 误判静音；分离残留间奏 RMS 高于 -40 漏检）。

    算法（v1 最大间隙被散值干扰实测选错——改为峰基准）：
    阈值 = P85(RMS) − 35dB，钳制 [-90, -12]。P85=人声峰的代表值（人声窗占
    比通常 <50%，静音/换气窗在低区）——阈值落在人声峰下方 35dB，与换气
    （峰下 10-25dB）和真静音（峰下 60dB+）双方向拉开。分布校验：P85−P40
    <20dB（P95−P05，单峰无静音群）→ None 回退固定 DEFAULT。

    牧马城市 ground truth：P85≈-17 → 阈值≈-52 → 换气（-25~-45）不算静音 ✓、
    真静音（-76~-120）检出 ✓、极弱尾音（-76~-90）检出 ✓。

    Returns: 阈值 float，或 None（单峰/样本不足 → 回退固定 DEFAULT）。"""
    valid = sorted(d for d in dbs if math.isfinite(d))
    if len(valid) < 20:
        return None  # 样本不足（纯伴奏/极短音频）→ 固定阈值兜底
    p05 = valid[int(len(valid) * 0.05)]
    p85 = valid[int(len(valid) * 0.85)]
    if p85 - p05 < 20.0:
        return None  # 分布单峰（无明显静音群）→ 固定阈值兜底
    th = max(-90.0, min(-12.0, p85 - 35.0))
    return th


def _spectrum_silent(audio, start_ms: int, end_ms: int, peak_energy: float) -> bool:
    """频谱能量验证（R4d 二道防线）：候选静音段内**逐 0.5s 子窗**的能量比
    （相对全曲峰值窗能量）<1e-4 占比 ≥90% 才确认真静音——区分「数字静音」
    与「有谱结构的弱信号」（极弱人声/换气呼吸噪声能量比 ~1e-2 不误判）。
    长段容忍 10% 边界过渡窗（首尾淡入淡出子窗能量偏高属正常）。依赖 numpy
    （惰性 import，失败=True 保持候选，不阻塞链路）。"""
    try:
        import numpy as np
        chunk = audio[start_ms:end_ms]
        if len(chunk) == 0:
            return True
        total_windows = 0
        silent_windows = 0
        for start in range(0, len(chunk), 500):
            sub = chunk[start:start + 500]
            if len(sub) == 0:
                continue
            total_windows += 1
            samples = np.array(sub.get_array_of_samples(), dtype=np.float64)
            if samples.size == 0 or peak_energy <= 0:
                silent_windows += 1
                continue
            if float(np.sum(samples ** 2)) / peak_energy < 1e-4:
                silent_windows += 1
        if total_windows == 0:
            return True
        return silent_windows / total_windows >= 0.9
    except Exception:
        return True  # 频谱验证不可用 → 保持 RMS 候选（不阻塞链路）


def _merge_small_gaps(runs, merge_gap_s: float = 3.0):
    """小间隔合并（R4e，用户指令）：相邻静音段之间的人声残留（未清理干净的
    和声/混音）间隔 <merge_gap_s → 合并为一个静音段——间奏不再被和声残留
    切碎（例：[150.0-164.6]+[166.7-215.4] 间隔 2.1s<3s → [150.0-215.4]），
    插播窗口更完整容错更高。仅生成端（compute_silences）调用；manual 人工
    精修文件不经此步骤（人工值全责）。"""
    if not runs or merge_gap_s <= 0:
        return runs
    merged = [dict(runs[0])]
    for seg in runs[1:]:
        if seg["start_s"] - merged[-1]["end_s"] < merge_gap_s:
            # 间隔不足 → 残留并入静音（前段延伸到后段终点）
            merged[-1]["end_s"] = seg["end_s"]
        else:
            merged.append(dict(seg))
    if len(merged) != len(runs):
        logger.info(
            f"[silences] 小间隔合并（<{merge_gap_s}s 的和声/混音残留并入静音）："
            f"{len(runs)} 段 → {len(merged)} 段")
    return merged


def compute_silences(vocal_path: str, threshold_db=DEFAULT_THRESHOLD_DB,
                     min_silence_s: float = DEFAULT_MIN_SILENCE_S,
                     merge_gap_s: float = 3.0):
    """RMS 扫描 vocal 分轨 → [{start_s,end_s}]（生成端/惰性解析共用）。

    2026-11-10 R4d 识别逻辑优化（用户指令：结合频谱）：
    - threshold_db=None → **自适应阈值**（RMS 双峰分离，按歌自动定界——
      解决「其它歌曲响度差异/分离残留」的固定阈值脆弱性）；数值 → 固定
      阈值（config 显式配置的用户 override）。
    - 候选静音段经 **频谱能量验证**（FFT 总能量相对全曲峰值 <1e-4）——
      极弱人声/呼吸噪声不误判（RMS 低的弱信号谱能量并不为零）。
    - min_silence_s=3.0 为 ground truth 验证分界（0.3 产生换气碎段误判）。

    异常外抛由调用方处置（cover_worker 生成端 fail-soft 包裹；惰性解析线程
    自捕获）。纯 CPU：<2s/4min 音频（100ms 窗口 pydub 扫描+候选段 FFT）。"""
    from pydub import AudioSegment

    audio = AudioSegment.from_file(vocal_path)
    total_s = len(audio) / 1000.0
    win = _WINDOW_MS
    n_windows = int(len(audio) // win)

    # ① 逐窗 RMS 预扫（自适应阈值需要全曲分布）
    dbs = []
    for i in range(n_windows):
        chunk = audio[i * win:(i + 1) * win]
        db = chunk.dBFS
        dbs.append(db if math.isfinite(db) else -120.0)  # 数字静音归一化记录
    _eff_th = _adaptive_threshold(dbs) if threshold_db is None else float(threshold_db)
    if _eff_th is None:
        _eff_th = DEFAULT_THRESHOLD_DB
    else:
        logger.info(f"[silences] 自适应阈值={_eff_th:.1f}dB（双峰分离，"
                    f"覆盖固定 {threshold_db if threshold_db is not None else 'None'}）")

    # ② 静音段扫描（自适应/固定阈值）
    runs = []
    run_start = None
    for i in range(n_windows):
        db = dbs[i]
        silent = db < _eff_th
        t0, t1 = i * win / 1000.0, (i + 1) * win / 1000.0
        if silent and run_start is None:
            run_start = t0
        elif not silent and run_start is not None:
            if t0 - run_start >= min_silence_s:
                runs.append({"start_s": round(run_start, 3), "end_s": round(t0, 3)})
            run_start = None
    if run_start is not None and total_s - run_start >= min_silence_s:
        runs.append({"start_s": round(run_start, 3), "end_s": round(total_s, 3)})

    # ③ 频谱能量验证（二道防线）：弱信号（极弱人声/换气）不误判
    if runs:
        try:
            peak = _peak_energy(audio)
            verified = [g for g in runs
                        if _spectrum_silent(audio, int(g["start_s"] * 1000),
                                            int(g["end_s"] * 1000), peak)]
            if len(verified) != len(runs):
                logger.info(
                    f"[silences] 频谱验证剔除 {len(runs) - len(verified)} 个"
                    f"伪静音候选（有谱结构的弱信号）：{len(verified)} 段保留")
            runs = verified
        except Exception:
            pass  # 验证失败保持 RMS 候选
    # ④ 小间隔合并（R4e）：和声/混音残留（<merge_gap_s）并入静音——间奏
    # 不被残留切碎，插播窗口更完整容错
    return _normalize(_merge_small_gaps(_normalize(runs), merge_gap_s))


def _peak_energy(audio) -> float:
    """全曲峰值能量（0.5s 粗粒度最大窗能量——频谱验证的分母）。"""
    import numpy as np
    peak = 0.0
    step = 500
    for start in range(0, len(audio), step):
        chunk = audio[start:start + step]
        if len(chunk) == 0:
            continue
        samples = np.array(chunk.get_array_of_samples(), dtype=np.float64)
        e = float(np.sum(samples ** 2))
        if e > peak:
            peak = e
    return peak


def write_silences_file(path: str, silences, threshold_db: float = DEFAULT_THRESHOLD_DB,
                        min_silence_s: float = DEFAULT_MIN_SILENCE_S) -> bool:
    """原子写 .silences.json（tmp+os.replace——写者矩阵同款防半写）；写前过
    F-H 校验（畸形不入盘）。Returns: True=成功。"""
    doc = {"version": SCHEMA_VERSION, "threshold_db": float(threshold_db),
           "min_silence_s": float(min_silence_s), "silences": silences}
    if parse_silences_document(doc) is None:
        logger.warning(f"[silences] 拒绝写入畸形数据：{path}")
        return False
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        logger.warning(f"[silences] 数据文件写入失败：{path}", exc_info=True)
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False
