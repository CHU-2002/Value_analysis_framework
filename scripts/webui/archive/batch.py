"""Explicitly invoked, resumable collection batches."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from .gaps import classify_result, completeness
from .quota import PROFILES
from .store import ArchiveStore
from .token import token_fingerprint
from ..core.errors import BatchRunning, NoToken, QuotaConfirmRequired
from ..core.security import redact


class CollectionTarget(dict):
    """JSON-compatible target: ticker, dataset, period, params."""


class ArchiveBatch:
    def __init__(self, store: ArchiveStore, targets, profile, adapter, *, token="", tier_label="", clock=None):
        self.store = store
        self.targets = [CollectionTarget(target) for target in targets]
        if profile not in PROFILES:
            raise ValueError(f"未知配额档案：{profile}")
        self.profile = profile
        self.adapter = adapter
        self.token = token
        self.tier_label = tier_label
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def create_id():
        return uuid.uuid4().hex

    def run(self, *, batch_id=None, confirm=False, force=False):
        if not self.token:
            raise NoToken("未配置 Tushare token", hint="设置 TUSHARE_TOKEN 或在项目 .env 中配置后重试。")
        if not self.targets:
            raise ValueError("采集目标清单不能为空")
        batch_id = batch_id or self.create_id()
        estimate = len(self.targets)
        if not confirm:
            raise QuotaConfirmRequired(
                f"本批（{self.profile}）预计 {estimate} 次请求，需要显式确认。",
                hint="复核调用量后使用 --yes 确认。",
            )
        batch = self.store.load_batch(batch_id) or {
            "batch_id": batch_id, "profile": self.profile, "targets": self.targets,
            "status": "pending", "completed": {}, "created_at": self.clock().isoformat(),
            "estimate": estimate, "progress": {"completed": 0, "total": len(self.targets)},
            "usage": {"new_requests": 0, "archive_hits": 0},
        }
        if batch.get("profile") != self.profile:
            raise ValueError("恢复批次的配额档案与原批次不一致")
        batch.setdefault("progress", {"completed": len(batch["completed"]), "total": len(batch["targets"])})
        if batch.get("status") == "running" and _pid_alive(batch.get("owner_pid")):
            raise BatchRunning("该采集批次正在运行", hint="等待当前批次结束，或确认旧进程已退出后再恢复。")
        batch.update(status="running", owner_pid=os.getpid(), heartbeat_at=self.clock().isoformat())
        self.store.append_batch(batch)
        lock_path = self.store.root / "batches" / f"{batch_id}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            try:
                lock_pid = int(lock_path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                lock_pid = None
            if _pid_alive(lock_pid):
                raise BatchRunning("该采集批次正在运行", hint="等待当前批次结束。") from exc
            lock_path.unlink(missing_ok=True)
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as retry_exc:
                raise BatchRunning("该采集批次正在运行", hint="等待当前批次结束。") from retry_exc
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        batch.setdefault("usage", {"new_requests": 0, "archive_hits": 0})
        try:
            for target in batch["targets"]:
                key = _target_key(target)
                existing = batch["completed"].get(key)
                if existing and not force:
                    continue
                if not force and self.store.has(target):
                    _, meta = self.store.read(target)
                    result = meta.get("result", "ok")
                    if result in ("ok", "empty"):
                        batch["completed"][key] = {**_target_summary(target), "result": result,
                                                   "error_excerpt": meta.get("error_excerpt")}
                        batch["usage"]["archive_hits"] += 1
                        batch["progress"]["completed"] = len(batch["completed"])
                        self.store.append_batch(batch)
                        continue
                batch["usage"]["new_requests"] += 1
                self.store.append_batch(batch)
                try:
                    data = self.adapter.fetch(target)
                    result, excerpt = classify_result(data=data)
                except Exception as exc:  # provider errors are recorded per target; batch continues
                    result, excerpt = classify_result(error=exc)
                    excerpt = redact(excerpt or "", (self.token,)) or None
                    data = {"error": excerpt}
                self.store.save(
                    target, data, result=result, error_excerpt=excerpt,
                    token_fingerprint=token_fingerprint(self.token), tier_label=self.tier_label,
                    quota_profile=self.profile, token=self.token,
                )
                batch["completed"][key] = {**_target_summary(target), "result": result,
                                           "error_excerpt": excerpt}
                batch["progress"]["completed"] = len(batch["completed"])
                batch["heartbeat_at"] = self.clock().isoformat()
                self.store.append_batch(batch)
            summary = completeness(batch["completed"].values())
            batch.update(status="partial" if summary["gaps"] else "done", owner_pid=None,
                         heartbeat_at=self.clock().isoformat(), summary=summary,
                         usage={**batch["usage"],
                                "failures": summary["counts"]["error"] + summary["counts"]["rate_limited"],
                                "no_permission": summary["counts"]["no_permission"]})
            self.store.append_batch(batch)
            return batch
        except KeyboardInterrupt:
            batch.update(status="paused", owner_pid=None, heartbeat_at=self.clock().isoformat(),
                         progress={"completed": len(batch["completed"]), "total": len(batch["targets"])})
            self.store.append_batch(batch)
            raise
        except Exception:
            batch.update(status="failed", owner_pid=None, heartbeat_at=self.clock().isoformat(),
                         progress={"completed": len(batch["completed"]), "total": len(batch["targets"])})
            self.store.append_batch(batch)
            raise
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def _target_key(target):
    import hashlib
    import json

    canonical = json.dumps(target, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _target_summary(target):
    return {key: target.get(key) for key in ("ticker", "dataset", "period", "params")}


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True
