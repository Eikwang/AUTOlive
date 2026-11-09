# -*- coding: UTF-8 -*-
"""
声音模型统一管理数据层（2026-11-07 模型管理批次，用户诉求）。

背景：模型文件管理与档案选择解耦——此前「文本转语音-声音档案」的删除按钮
删的是档案记录（模型文件不动）且 UI 不刷新（感知"不能生效"）；变声/翻唱
只有模型选择没有管理。本模块提供全链清单与删除：

- 清单：GSV=权重对（GPT_weights_v2ProPlus/{exp}-eN.ckpt +
  SoVITS_weights_v2ProPlus/{exp}_eN_sM.pth 按实验名配对）；RVC=models/rvc/
  主件（{exp}.pth+{exp}.index）。每组标注引用档案数与当前使用态。
- 删除（delete_voice_model）：全链清理——GSV 权重对 / RVC 四面（models/rvc
  主件 + assets/weights 训练终态 + assets/indices 特征索引 + 翻唱消费面
  autolive_{exp} 硬链目录）；**引用档案记录整条删除**（2026-11-09 用户裁决：
  档案=模型的档案，模型没了档案无意义，不留死记录）；GSV 当前冷启动模型
  （gpt_sovits.params 指向）保护拒绝。

单写者约束：voice_profiles 节写仍经 voice_profiles（E-4）。
"""
import os
import re
import shutil
from typing import Dict, List

from utils.autolive_paths import config_get
from utils.my_log import logger
from utils.voice_profiles import delete_profile, load_profiles

# GSV 产物命名（train_voice_pipeline：{exp}-e{N}.ckpt / {exp}_e{N}_s{M}.pth，
# T-3"仅保存最新"保证通常单对；外部拷入多对时按组全删）
_GSV_CKPT_RE = re.compile(r"^(.+)-e\d+\.ckpt$")
_GSV_PTH_RE = re.compile(r"^(.+)_e\d+_s\d+\.pth$")


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(str(p or "")))


def _models_home(config) -> str:
    return config_get(config, "paths", "autolive_home") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))


def list_voice_models(config, gsv_root: str = None) -> Dict[str, List[dict]]:
    """全链声音模型清单。

    Args:
        config: 双形态 config。
        gsv_root: GPT-SoVITS 根（默认 apps/gpt-sovits——webui 侧无 AutolivePaths
            时由调用方传 paths.gpt_sovits_root）。

    Returns:
        {"gsv": [{exp, ckpt, pth, size_mb, ref_profiles, current}, ...],
         "rvc": [{exp, pth, index, size_mb, ref_profiles}, ...]}
        current=True 表示 gpt_sovits.params（冷启动/页面换模后的当前值）指向
        该组——删除被保护拒绝。
    """
    home = _models_home(config)
    gsv_root = gsv_root or os.path.join(home, "apps", "gpt-sovits")
    profiles = load_profiles() or {}
    params = config_get(config, "gpt_sovits", "params") or {}
    cur_gpt = _norm(params.get("gpt_model_path"))
    cur_vits = _norm(params.get("sovits_model_path"))

    # ---- GSV：权重对按实验名配对 ----
    gpt_w = os.path.join(gsv_root, "GPT_weights_v2ProPlus")
    vits_w = os.path.join(gsv_root, "SoVITS_weights_v2ProPlus")
    ckpts, pths = {}, {}
    for d, rx, bucket in ((gpt_w, _GSV_CKPT_RE, ckpts), (vits_w, _GSV_PTH_RE, pths)):
        if os.path.isdir(d):
            for fn in os.listdir(d):
                m = rx.match(fn)
                if m:
                    bucket[m.group(1)] = os.path.join(d, fn)
    gsv = []
    for exp in sorted(set(ckpts) | set(pths), key=str.casefold):
        ckpt, pth = ckpts.get(exp, ""), pths.get(exp, "")
        files = [f for f in (ckpt, pth) if f]
        refs = sorted(
            pid for pid, pr in profiles.items()
            if any(pr.get(k) and _norm(pr[k]) in {_norm(f) for f in files}
                   for k in ("gpt_model", "sovits_model")))
        gsv.append({
            "exp": exp, "ckpt": ckpt, "pth": pth,
            "size_mb": round(sum(os.path.getsize(f) for f in files if os.path.isfile(f)) / 1048576, 1),
            "ref_profiles": refs,
            "current": bool(files) and (_norm(ckpt) == cur_gpt or _norm(pth) == cur_vits),
        })

    # ---- RVC：models/rvc 主件面（档案权威面；assets/indices 硬链与翻唱
    #      消费面是副本，删除时全链清理，清单不重复展示） ----
    rvc_dir = os.path.join(home, "models", "rvc")
    rvc = []
    if os.path.isdir(rvc_dir):
        for fn in sorted(os.listdir(rvc_dir), key=str.casefold):
            if not fn.endswith(".pth"):
                continue
            exp = fn[:-4]
            pth = os.path.join(rvc_dir, fn)
            index = os.path.join(rvc_dir, exp + ".index")
            refs = sorted(
                pid for pid, pr in profiles.items()
                if (pr.get("rvc_model") and _norm(pr["rvc_model"]) == _norm(pth))
                or (pr.get("rvc_index") and _norm(pr["rvc_index"]) == _norm(index)
                    and os.path.isfile(index)))
            size = os.path.getsize(pth) if os.path.isfile(pth) else 0
            if os.path.isfile(index):
                size += os.path.getsize(index)
            rvc.append({
                "exp": exp, "pth": pth, "index": index if os.path.isfile(index) else "",
                "size_mb": round(size / 1048576, 1),
                "ref_profiles": refs,
            })
    return {"gsv": gsv, "rvc": rvc}


def _unlink_safe(path: str, deleted: List[str], kept: List[str], errors: List[str]):
    try:
        if os.path.isfile(path):
            os.remove(path)
            deleted.append(path)
        elif path:
            kept.append(path)
    except OSError as e:
        errors.append(f"{path}: {e}")


def delete_voice_model(kind: str, exp: str, config, gsv_root: str = None) -> dict:
    """删除一组声音模型（全链+档案联动）。返回结构化结果，不抛异常。

    保护：kind=gsv 且 gpt_sovits.params（冷启动当前）指向该组任一文件 →
    ok=False 拒绝（提示先在文本转语音页切换档案）；RVC 无 params 直指面，
    变声运行中文件被占用时 OSError 收集进 errors（其余文件照删）。
    """
    home = _models_home(config)
    gsv_root = gsv_root or os.path.join(home, "apps", "gpt-sovits")
    deleted: List[str] = []
    kept: List[str] = []
    errors: List[str] = []
    targets: List[str] = []

    if kind == "gsv":
        params = config_get(config, "gpt_sovits", "params") or {}
        ckpts = [os.path.join(gsv_root, "GPT_weights_v2ProPlus", f)
                 for f in _dir_glob(gsv_root, "GPT_weights_v2ProPlus",
                                    rf"^{re.escape(exp)}-e\d+\.ckpt$")]
        pths = [os.path.join(gsv_root, "SoVITS_weights_v2ProPlus", f)
                for f in _dir_glob(gsv_root, "SoVITS_weights_v2ProPlus",
                                   rf"^{re.escape(exp)}_e\d+_s\d+\.pth$")]
        targets = ckpts + pths
        hit = {_norm(t) for t in targets if os.path.isfile(t)} & {
            _norm(params.get("gpt_model_path")), _norm(params.get("sovits_model_path"))}
        if hit:
            return {"ok": False, "deleted": [], "kept": [], "errors": [],
                    "unlinked_profiles": [],
                    "error": "该模型正被 GPT-SoVITS 使用（文本转语音当前选用/冷启动模型）"
                             "——请先在「文本转语音」页切换其它声音档案后再删除"}
    elif kind == "rvc":
        targets = [os.path.join(home, "models", "rvc", f"{exp}.pth"),
                   os.path.join(home, "models", "rvc", f"{exp}.index"),
                   os.path.join(home, "apps", "rvc", "assets", "weights", f"{exp}.pth")]
        targets += [os.path.join(home, "apps", "rvc", "assets", "indices", f)
                    for f in _dir_glob(home, os.path.join("apps", "rvc", "assets", "indices"),
                                       rf"^added_.+_{re.escape(exp)}_.+\.index$")]
        # 翻唱消费面：autolive_{exp} 硬链目录（大小写归一匹配）
        cover_root = os.path.join(home, "apps", "aicovergen", "rvc_models")
        if os.path.isdir(cover_root):
            for d in os.listdir(cover_root):
                if d.lower() == f"autolive_{exp}".lower():
                    targets.append(os.path.join(cover_root, d))
    else:
        return {"ok": False, "error": f"未知模型类型: {kind}", "deleted": [],
                "kept": [], "errors": [], "unlinked_profiles": []}

    cover_dirs = [t for t in targets if os.path.isdir(t)]
    for d in cover_dirs:
        try:
            shutil.rmtree(d)
            deleted.append(d)
        except OSError as e:
            errors.append(f"{d}: {e}")
    for f in targets:
        if f not in cover_dirs:
            _unlink_safe(f, deleted, kept, errors)

    # 档案联动：引用被删文件的档案记录整条删除（2026-11-09 用户裁决——
    # 档案=模型的档案（模型对+参考音频+提示文本），模型没了档案无意义；
    # 重新训练会经 export 重建档案。此前"清字段留记录"产生死档案
    #（isfile 过滤隐藏但记录层堆积），用户要求彻底删除）。
    unlinked = []
    if deleted:
        deleted_set = {_norm(d) for d in deleted}
        for pid, pr in (load_profiles() or {}).items():
            hit = any(pr.get(field) and _norm(pr[field]) in deleted_set
                      for field in ("gpt_model", "sovits_model", "rvc_model", "rvc_index"))
            if hit:
                delete_profile(pid)
                unlinked.append(pid)
                logger.info(f"[voice-models] 档案 {pid} 引用已删模型，记录已整条删除")

    ok = not errors and (bool(deleted) or bool(kept))
    logger.info(f"[voice-models] 删除 {kind}/{exp}: 删 {len(deleted)} 件"
                f"（解除引用 {len(unlinked)} 档案）" + (f"，错误 {errors}" if errors else ""))
    return {"ok": ok, "deleted": deleted, "kept": kept, "errors": errors,
            "unlinked_profiles": unlinked,
            "error": "；".join(errors[:2]) if errors else ""}


def _dir_glob(root: str, rel: str, pattern: str) -> List[str]:
    d = os.path.join(root, rel)
    if not os.path.isdir(d):
        return []
    rx = re.compile(pattern)
    return [f for f in os.listdir(d) if rx.match(f)]
