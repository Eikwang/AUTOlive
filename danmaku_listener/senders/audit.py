"""发送审计日志（AutoDanmu T4——权威对账记录，R6/R25/F9）

- JSONL 追加写：ts/platform/room_id/request_id/content/status/reason_code/source/dry_run
- fail-closed（R25）：intent 落盘失败 → 实发路径拒绝发送；dry-run 路径 fail-open+告警
- 幂等索引持久化（F9）：request_id → 结果摘要，temp+rename 原子写（F7 纪律）；
  索引缺失时从审计文件重建（重启语义）
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger


class SendAuditLog:
    """发送审计（append-only JSONL + 幂等索引）"""

    def __init__(self, audit_file: str, index_file: Optional[str] = None):
        self._path = Path(audit_file)
        self._index_path = Path(index_file) if index_file else None
        self._idempotency: dict[str, dict] = {}
        self._load_index()

    # ---- 幂等索引（F9）----

    def _load_index(self) -> None:
        """加载持久化索引；缺失/损坏时从审计文件重建"""
        if self._index_path and self._index_path.is_file():
            try:
                self._idempotency = json.loads(self._index_path.read_text(encoding="utf-8"))
                if isinstance(self._idempotency, dict):
                    return
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[send-audit] index unreadable, rebuilding: {e}")
        self._idempotency = {}
        self._rebuild_from_audit()

    def _rebuild_from_audit(self) -> None:
        """从审计 JSONL 重建 request_id → 结果摘要（F9 重启语义）"""
        if not self._path.is_file():
            return
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = row.get("request_id")
                if rid and row.get("kind") == "result":
                    self._idempotency[rid] = {
                        "status": row.get("status"),
                        "reason_code": row.get("reason_code"),
                        "sent_at": row.get("sent_at"),
                    }
            self._persist_index()
            logger.info(f"[send-audit] idempotency index rebuilt: {len(self._idempotency)} entries")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[send-audit] rebuild failed: {e}")

    def _persist_index(self) -> None:
        """索引原子落盘（temp+rename，F7 纪律）"""
        if not self._index_path:
            return
        try:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(self._index_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._idempotency, fh, ensure_ascii=False)
            os.replace(tmp, self._index_path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[send-audit] index persist failed: {e}")

    def lookup(self, request_id: str) -> Optional[dict]:
        """查询历史结果（R18 幂等）"""
        return self._idempotency.get(request_id)

    # ---- 审计行 ----

    def append(self, row: dict[str, Any]) -> bool:
        """追加一行审计；False=写失败（调用方按 R25 fail-closed 处置）"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            row = {"ts": int(time.time()), **row}
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            if row.get("kind") == "result" and row.get("request_id"):
                self._idempotency[row["request_id"]] = {
                    "status": row.get("status"),
                    "reason_code": row.get("reason_code"),
                    "sent_at": row.get("sent_at"),
                    # 评审 redteam#5：对账回执需要 triage 上下文（detail/fix_hint）——
                    # 中止场景下运维从行内即可定位，不必翻 JSONL（索引体积换闭环）
                    "detail": (row.get("detail") or "")[:160],
                    "fix_hint": (row.get("fix_hint") or "")[:160],
                }
                self._persist_index()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[send-audit] append failed: {e}")
            return False
