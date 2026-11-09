# -*- coding: UTF-8 -*-
"""
数字人模型统一管理数据层（2026-11-09 用户设计二轮：以角色名为单位，
只管理微调训练后的模型）。

用户裁决语义：
- "数字人模型"=训练主播流程的微调训练结果（底模 Generator + 口型
  audio2lip），以角色名为单位保存于 `edtalk/ckpt_models/{role}/`——
  **不含**预处理推理帧/素材（那是部署数据包与画面素材管理的领域）。
- 训练收尾自动清中间文件（中间 iter ckpt/预览图 step_*.jpg/tfevents/
  口型微调时间戳目录 logs/Audio2Lip/{role}）——最终模型落位后清理。
- 画面设置选角色（config.edtalk_realtime.character_dir，2026-10-31 退役
  为内部量，本管理面受控接管切换写入）→ 服务按新角色加载（重启生效）。
- 删除角色=删该角色全部（模型目录+部署数据包+训练工作区+训练日志残余
  +进度），当前启用角色保护拒绝。

角色名两套大小写的历史现实：部署数据包名=config character_dir（"wy"）、
训练产物目录名=role_name（"WY"）——关联与删除一律 casefold 兜底。
"""
import os
import shutil
from typing import Dict, List

from utils.my_log import logger

# 部署数据包的推理子目录（apply_to_digital_human 的 junction 集合）
_JUNCTION_SUBDIRS = ("orifra", "count", "metadata", "facemark", "gaze_data")


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _edtalk_root(config, edtalk_root: str = None) -> str:
    if edtalk_root:
        return edtalk_root
    try:
        from frontend.config.paths import get_base_path
        return os.path.join(str(get_base_path()), "edtalk")
    except Exception:
        return os.path.join(_repo_root(), "edtalk")


def _train_root(config, edtalk_root: str = None) -> str:
    """训练工作区根（data/edtalk_train——get_edtalk_dir 同源约定）。"""
    return os.path.join(os.path.dirname(_edtalk_root(config, edtalk_root)),
                        "data", "edtalk_train")


def _character_dir_name(config) -> str:
    ed = (config or {}).get("edtalk_realtime", {}) or {}
    return str(ed.get("character_dir") or "wy")


def _dir_size(root: str) -> int:
    """目录字节总量（不跟随 junction——walk 中剔除 junction 子目录）。"""
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not os.path.isjunction(os.path.join(dirpath, d))]
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def _find_casefold(parent: str, name: str) -> str:
    """父目录下 casefold 匹配的子目录（角色名两套大小写的历史兜底）。"""
    if not os.path.isdir(parent):
        return ""
    for d in os.listdir(parent):
        if d.casefold() == str(name).casefold() and os.path.isdir(
                os.path.join(parent, d)):
            return os.path.join(parent, d)
    return ""


def _resolve_data_pkg(edtalk_dir: str, role: str) -> str:
    """角色的部署数据包目录（edtalk/{casefold role}）。"""
    return _find_casefold(edtalk_dir, role)


def list_digital_human_models(config, edtalk_root: str = None) -> Dict[str, List[dict]]:
    """角色模型清单（模型视角，以角色名为单位）。

    角色来源=ckpt_models/{role}（训练结果）∪ 部署数据包（edtalk/ 下含
    checkpoint/new.pt）∪ 训练工作区（data/edtalk_train/{role}）——casefold
    归并。字段：
    - role/model_gb：角色名/模型目录实体大小（ckpt_models/{role}）
    - generator_ok：checkpoint/ 下存在 .pt（底模产物）
    - audio2lip_ok：ckpt_models/{role}/audio2lip.pt（统一落位后）
    - deployed：部署数据包存在且 checkpoint/new.pt 在（服务可加载）
    - cleanup_pending：存在中间文件（非最终 iter ckpt/预览图/tfevents/
      口型时间戳目录）——训练收尾清理未跑（历史训练遗留）
    - current：config.edtalk_realtime.character_dir 指向（画面设置消费）
    """
    edtalk_dir = _edtalk_root(config, edtalk_root)
    train_root = _train_root(config, edtalk_root)
    current_name = _character_dir_name(config)

    roles: Dict[str, str] = {}  # casefold -> 显示名（ckpt_models 优先）
    ck_root = os.path.join(edtalk_dir, "ckpt_models")
    if os.path.isdir(ck_root):
        for d in os.listdir(ck_root):
            if os.path.isdir(os.path.join(ck_root, d)):
                roles.setdefault(d.casefold(), d)
    if os.path.isdir(edtalk_dir):
        for d in os.listdir(edtalk_dir):
            pkg = os.path.join(edtalk_dir, d)
            if os.path.isdir(pkg) and os.path.isfile(
                    os.path.join(pkg, "checkpoint", "new.pt")):
                roles.setdefault(d.casefold(), d)
    if os.path.isdir(train_root):
        for d in os.listdir(train_root):
            if os.path.isdir(os.path.join(train_root, d)) and d != "train_progress":
                roles.setdefault(d.casefold(), d)

    out: List[dict] = []
    for key in sorted(roles, key=str.casefold):
        name = roles[key]
        model_dir = _find_casefold(ck_root, name)
        model_gb = round(_dir_size(model_dir) / 1073741824, 2) if model_dir else 0.0
        ck_dir = os.path.join(model_dir, "checkpoint") if model_dir else ""
        pts = sorted(f for f in (os.listdir(ck_dir) if os.path.isdir(ck_dir) else [])
                     if f.endswith(".pt"))
        pkg = _resolve_data_pkg(edtalk_dir, name)
        deployed = bool(pkg) and os.path.isfile(os.path.join(pkg, "checkpoint", "new.pt"))
        # 中间文件盘点（cleanup_pending 量化）
        intermediates = 0
        if ck_dir and os.path.isdir(ck_dir):
            intermediates += max(0, len(pts) - 1)  # 非最终 iter ckpt
            intermediates += len([f for f in os.listdir(ck_dir)
                                  if f.startswith("step_") and f.endswith(".jpg")])
            intermediates += len(os.listdir(os.path.join(model_dir, "log"))
                                 ) if os.path.isdir(os.path.join(model_dir, "log")) else 0
        if os.path.isdir(os.path.join(edtalk_dir, "logs", "Audio2Lip", name)):
            intermediates += 1  # 口型微调时间戳目录残余
        out.append({
            "role": name,
            "model_dir": model_dir,
            "model_gb": model_gb,
            "generator_ok": bool(pts),
            "audio2lip_ok": bool(model_dir) and os.path.isfile(
                os.path.join(model_dir, "audio2lip.pt")),
            "deployed": deployed,
            "cleanup_pending": intermediates > 0,
            "intermediate_count": intermediates,
            "workspace_exists": bool(_find_casefold(train_root, name)),
            "current": name.casefold() == current_name.casefold(),
        })
    return {"models": out, "current": current_name}


def validate_switch(config, role: str, edtalk_root: str = None) -> str:
    """画面设置切换校验（空串=可切换；current 重复启用返回空串）。

    服务消费面=部署数据包（推理帧 junction+checkpoint 权重），校验与其
    构造需求对齐：orifra 有素材段+checkpoint/new.pt。
    """
    current_name = _character_dir_name(config)
    if str(role).casefold() == current_name.casefold():
        return ""
    pkg = _resolve_data_pkg(_edtalk_root(config, edtalk_root), str(role))
    orifra = os.path.join(pkg, "orifra") if pkg else ""
    if not orifra or not os.path.isdir(orifra):
        return (f"角色 {role} 尚未部署推理数据（缺数据包 orifra）——"
                "请先完成该角色的训练部署，或在『画面设置』选择其它角色")
    try:
        segments = [d for d in os.listdir(orifra)
                    if os.path.isdir(os.path.join(orifra, d))]
    except OSError:
        segments = []
    if not segments:
        return f"角色 {role} 数据包 orifra 无素材段——无法启用"
    if not os.path.isfile(os.path.join(pkg, "checkpoint", "new.pt")):
        return f"角色 {role} 数据包缺 checkpoint/new.pt（口型生成权重）——无法启用"
    return ""


def cleanup_training_intermediates(role: str, edtalk_dir: str = None) -> dict:
    """训练收尾中间文件清理（统一保存语义：角色目录只留最终模型）。

    ① ckpt_models/{role}/checkpoint/：保留 iter 最大的 .pt（最终底模），
       其余中间 iter ckpt 与 step_*.jpg 预览图删除；log/ tfevents 删除
    ② logs/Audio2Lip/{role}/：整删（口型微调时间戳目录/日志/中间 ckpt——
       最终 a2l 已由部署链落位 ckpt_models/{role}/audio2lip.pt）

    仅在部署成功后调用（失败保留现场供手动兜底）。返回 freed_mb/summary，
    不抛异常。casefold 兜底（数据包 wy ↔ 训练目录 WY）。
    """
    if edtalk_dir is None:
        try:
            from frontend.config.paths import get_base_path
            edtalk_dir = os.path.join(str(get_base_path()), "edtalk")
        except Exception:
            edtalk_dir = os.path.join(_repo_root(), "edtalk")
    freed = {"mb": 0.0}
    removed: List[str] = []

    def _rm_file(path: str):
        try:
            size = os.path.getsize(path)
            os.remove(path)
            removed.append(path)
            freed["mb"] += size / 1048576
        except OSError:
            pass

    def _rm_tree(path: str, label: str):
        if not path or not os.path.isdir(path):
            return
        size = _dir_size(path)
        try:
            shutil.rmtree(path)
            removed.append(path)
            freed["mb"] += size / 1048576
        except OSError as e:
            logger.warning(f"[digital-human-models] 清理 {label} 失败: {e}")

    model_dir = _find_casefold(os.path.join(edtalk_dir, "ckpt_models"), role)
    ck_dir = os.path.join(model_dir, "checkpoint") if model_dir else ""
    if os.path.isdir(ck_dir):
        pts = sorted(f for f in os.listdir(ck_dir) if f.endswith(".pt"))
        for fn in pts[:-1]:  # 最终 iter（排序最大）保留
            _rm_file(os.path.join(ck_dir, fn))
        for fn in os.listdir(ck_dir):
            if fn.startswith("step_") and fn.endswith(".jpg"):
                _rm_file(os.path.join(ck_dir, fn))
        _rm_tree(os.path.join(model_dir, "log"), "tfevents")
    _rm_tree(_find_casefold(os.path.join(edtalk_dir, "logs", "Audio2Lip"), role),
             "口型微调产物")

    summary = f"{len(removed)} 项"
    logger.info(f"[digital-human-models] {role} 中间文件清理：{summary}"
                f"（释放 {freed['mb']:.0f}MB）")
    return {"ok": True, "removed": removed, "freed_mb": round(freed["mb"], 1),
            "summary": summary}


def delete_digital_human_model(config, role: str, edtalk_root: str = None) -> dict:
    """删除一个角色（全链：模型目录+部署数据包+训练工作区+训练日志残余+进度）。

    junction 处置：先 os.rmdir 摘除数据包内 junction（只删链接不删目标），
    再独立 rmtree 数据包与训练工作区目标。保护：current 启用中拒绝。
    返回结构化结果，不抛异常。
    """
    edtalk_dir = _edtalk_root(config, edtalk_root)
    train_root = _train_root(config, edtalk_root)
    role = str(role)
    current_name = _character_dir_name(config)
    if role.casefold() == current_name.casefold():
        return {"ok": False, "error": "该角色为当前启用（画面设置-数字人角色）——"
                "请先切换到其它角色后再删除",
                "deleted": [], "errors": [], "freed_gb": 0.0}

    deleted: List[str] = []
    errors: List[str] = []
    freed = {"gb": 0.0}

    def _rmtree(path: str, label: str):
        if not path or not os.path.isdir(path):
            return
        size = _dir_size(path)
        try:
            shutil.rmtree(path)
            deleted.append(path)
            freed["gb"] += size / 1073741824
        except OSError as e:
            errors.append(f"{label}: {e}")

    pkg = _resolve_data_pkg(edtalk_dir, role)
    # ① 部署数据包：先摘 junction（只删链接），再整删
    if pkg:
        for sub in _JUNCTION_SUBDIRS:
            p = os.path.join(pkg, sub)
            try:
                if os.path.isjunction(p):
                    os.rmdir(p)
            except OSError as e:
                errors.append(f"junction {sub}: {e}")
        _rmtree(pkg, "部署数据包")
    # ② 模型目录（微调训练结果）
    _rmtree(_find_casefold(os.path.join(edtalk_dir, "ckpt_models"), role), "模型目录")
    # ③ 训练工作区（预处理帧/原始素材——GB 级大头）
    _rmtree(_find_casefold(train_root, role), "训练工作区")
    # ④ 口型微调日志残余
    _rmtree(_find_casefold(os.path.join(edtalk_dir, "logs", "Audio2Lip"), role),
            "口型微调产物")
    # ⑤ 训练进度
    progress = _find_casefold(os.path.join(edtalk_dir, "train_progress"), role)
    _rmtree(progress, "训练进度")
    for pid_name in (f"{role}.json",):
        p = os.path.join(edtalk_dir, "train_progress", pid_name)
        if os.path.isfile(p):
            try:
                os.remove(p)
                deleted.append(p)
            except OSError as e:
                errors.append(f"训练进度: {e}")

    ok = not errors
    logger.info(f"[digital-human-models] 删除角色 {role}: 删 {len(deleted)} 项"
                f"（释放 {freed['gb']:.1f}GB）" + (f"，错误 {errors}" if errors else ""))
    return {"ok": ok, "deleted": deleted, "errors": errors,
            "freed_gb": round(freed["gb"], 2),
            "error": "；".join(errors[:2]) if errors else ""}
