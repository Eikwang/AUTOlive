# -*- coding: utf-8 -*-
"""
数字人模型统一管理数据层（2026-11-10 架构改造：tuber 角色库）。

用户设计（三轮演进终态）：
- **tuber 角色库**（仓库根 `tuber/`）：**每角色一个子目录**——checkpoint
  权重部署副本（new.pt+audio2lip.pt）+ 推理帧 junction（orifra/count/
  metadata/facemark/gaze_data → data/edtalk_train/{role}）+ **动作编排
  片段**（orifra/{段} 等同套结构）。**数字人服务/动作编排/模型管理全链
  按此目录消费**。
- **config.edtalk_realtime.character_dir 语义升级**：从"固定目录名"升级为
  "**当前启用角色**"——画面设置选角色=写此键，编排片段落位/服务启动/
  管理清单全链跟随（训练 A/B/C 三个角色 = tuber 下三个子目录，选 A 则
  编排片段进 tuber/A、服务加载 tuber/A）。
- **部署目标=角色包创建**：训练角色名即包名（训练"小王" → `tuber/小王/`）。
- **ckpt_models 保留为训练产物权威库**（管理区块显示/重部署恢复源）；
  训练收尾自动清中间文件只清 ckpt_models/logs 侧。

历史演进：2026-11-09 一轮"数据包视角"（用户否决）→ 二轮"角色模型视角
+单包覆盖"→ 三轮（本版）"tuber 多角色库"。
"""
import os
import shutil
from typing import Dict, List, Optional

from utils.my_log import logger

# 部署数据包的推理子目录（apply_to_digital_human 的 junction 集合）
_JUNCTION_SUBDIRS = ("orifra", "count", "metadata", "facemark", "gaze_data")


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _base_path() -> str:
    try:
        from frontend.config.paths import get_base_path
        return str(get_base_path())
    except Exception:
        return _repo_root()


def tuber_root(tuber_root: str = None) -> str:
    """角色库根：仓库根 tuber/——每角色一个子目录（服务/编排/管理全链消费）。"""
    if tuber_root:
        return tuber_root
    return os.path.join(_base_path(), "tuber")


def _character_dir_name(config) -> str:
    """当前启用角色（config.edtalk_realtime.character_dir，默认 wy）。"""
    ed = (config or {}).get("edtalk_realtime", {}) or {}
    return str(ed.get("character_dir") or "wy")


def resolve_active_character_dir(config, tuber_root_arg: str = None) -> str:
    """当前启用角色的数据包目录：tuber/{启用角色}。"""
    return os.path.join(tuber_root(tuber_root_arg), _character_dir_name(config))


def _edtalk_root() -> str:
    """edtalk 副本根（ckpt_models/logs/train_progress 训练产物侧仍在此）。"""
    try:
        from frontend.config.paths import get_base_path
        return os.path.join(str(get_base_path()), "edtalk")
    except Exception:
        return os.path.join(_repo_root(), "edtalk")


def _train_root(edtalk_dir: str = None) -> str:
    """训练工作区根（data/edtalk_train——get_edtalk_dir 同源约定）。"""
    return os.path.join(os.path.dirname(edtalk_dir or _edtalk_root()), "data", "edtalk_train")


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


def _resolve_data_pkg(role: str, tuber_root_arg: str = None) -> str:
    """角色的部署数据包目录（tuber/{casefold role}）。"""
    return _find_casefold(tuber_root(tuber_root_arg), str(role))


def list_digital_human_models(config, tuber_root_arg: str = None, edtalk_dir: str = None) -> Dict[str, List[dict]]:
    """角色清单（tuber 角色库视角，以角色名为单位）。

    角色来源=tuber 库子目录（角色包）∪ ckpt_models/{role}（训练结果）∪
    训练工作区——casefold 归并。字段：
    - role/model_gb：角色名/权威库实体大小（ckpt_models/{role}）
    - generator_ok/audio2lip_ok：权威库双权重在位
    - deployed：tuber/{role}/checkpoint/new.pt 在（服务可加载）
    - cleanup_pending/intermediate_count：中间文件量化
    - workspace_exists：训练工作区在
    - current：config.edtalk_realtime.character_dir 指向（画面设置消费）
    """
    tb_root = tuber_root(tuber_root_arg)
    edtalk_dir = edtalk_dir or _edtalk_root()
    ck_root = os.path.join(edtalk_dir, "ckpt_models")
    train_root = _train_root(edtalk_dir)
    current_name = _character_dir_name(config)

    roles: Dict[str, str] = {}  # casefold -> 显示名（tuber 库优先）
    if os.path.isdir(tb_root):
        for d in os.listdir(tb_root):
            if os.path.isdir(os.path.join(tb_root, d)):
                roles.setdefault(d.casefold(), d)
    if os.path.isdir(ck_root):
        for d in os.listdir(ck_root):
            if os.path.isdir(os.path.join(ck_root, d)):
                roles.setdefault(d.casefold(), d)
    for d in (os.listdir(train_root) if os.path.isdir(train_root) else []):
        if os.path.isdir(os.path.join(train_root, d)):
            roles.setdefault(d.casefold(), d)

    out: List[dict] = []
    for key in sorted(roles, key=str.casefold):
        name = roles[key]
        model_dir = _find_casefold(ck_root, name)
        model_gb = round(_dir_size(model_dir) / 1073741824, 2) if model_dir else 0.0
        ck_dir = os.path.join(model_dir, "checkpoint") if model_dir else ""
        pts = sorted(f for f in (os.listdir(ck_dir) if os.path.isdir(ck_dir) else [])
                     if f.endswith(".pt"))
        pkg = _resolve_data_pkg(name, tuber_root_arg)
        deployed = bool(pkg) and os.path.isfile(os.path.join(pkg, "checkpoint", "new.pt"))
        intermediates = 0
        if ck_dir and os.path.isdir(ck_dir):
            intermediates += max(0, len(pts) - 1)
            intermediates += len([f for f in os.listdir(ck_dir)
                                  if f.startswith("step_") and f.endswith(".jpg")])
            intermediates += len(os.listdir(os.path.join(model_dir, "log"))
                                 ) if os.path.isdir(os.path.join(model_dir, "log")) else 0
        if os.path.isdir(os.path.join(edtalk_dir, "logs", "Audio2Lip", name)):
            intermediates += 1
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


def validate_switch(config, role: str, tuber_root_arg: str = None) -> str:
    """画面设置切换校验（空串=可切换；current 重复启用返回空串）。

    服务消费面=tuber/{role} 角色包（推理帧 junction+checkpoint 权重），
    校验与其构造需求对齐：orifra 有素材段+checkpoint/new.pt。
    """
    current_name = _character_dir_name(config)
    if str(role).casefold() == current_name.casefold():
        return ""
    pkg = _resolve_data_pkg(str(role), tuber_root_arg)
    orifra = os.path.join(pkg, "orifra") if pkg else ""
    if not orifra or not os.path.isdir(orifra):
        return (f"角色 {role} 尚未部署推理数据（角色库缺 {role} 包或其 orifra）——"
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
        edtalk_dir = _edtalk_root()
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


def delete_digital_human_model(config, role: str, tuber_root_arg: str = None,
                               edtalk_dir: str = None) -> dict:
    """删除一个角色（全链：tuber 角色包+模型权威库+训练工作区+训练日志残余+进度）。

    junction 处置：先 os.rmdir 摘除角色包内 junction（只删链接不删目标），
    再独立 rmtree 角色包与训练工作区目标。保护：current 启用中拒绝。
    返回结构化结果，不抛异常。
    """
    role = str(role)
    edtalk_dir = edtalk_dir or _edtalk_root()
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

    pkg = _resolve_data_pkg(role, tuber_root_arg)
    # ① 角色包：先摘 junction（只删链接），再整删（编排片段随之清除）
    if pkg:
        for sub in _JUNCTION_SUBDIRS:
            p = os.path.join(pkg, sub)
            try:
                if os.path.isjunction(p):
                    os.rmdir(p)
            except OSError as e:
                errors.append(f"junction {sub}: {e}")
        _rmtree(pkg, "角色包")
    # ② 模型权威库（微调训练结果）
    _rmtree(_find_casefold(os.path.join(edtalk_dir, "ckpt_models"), role), "模型目录")
    # ③ 训练工作区（预处理帧/原始素材——GB 级大头）
    _rmtree(_find_casefold(_train_root(edtalk_dir), role), "训练工作区")
    # ④ 口型微调日志残余
    _rmtree(_find_casefold(os.path.join(edtalk_dir, "logs", "Audio2Lip"), role),
            "口型微调产物")
    # ⑤ 训练进度
    progress = _find_casefold(os.path.join(edtalk_dir, "train_progress"), role)
    _rmtree(progress, "训练进度")
    p = os.path.join(edtalk_dir, "train_progress", f"{role}.json")
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
