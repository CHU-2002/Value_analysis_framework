"""Durable raw response storage, separate from the derived WebUI cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from ..core.errors import ArchiveUnwritable
from ..core.security import redact
from ..core.security import is_within

_SAFE_PART = re.compile(r"^[A-Za-z0-9._-]+$")


def _json_bytes(value):
    if hasattr(value, "to_json"):
        value = json.loads(value.to_json(orient="records"))
    elif hasattr(value, "to_dict"):
        value = value.to_dict(orient="records")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


class ArchiveStore:
    def __init__(self, root, *, clock=None):
        self.root = Path(root).expanduser().resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _part(value, label):
        value = str(value)
        if not _SAFE_PART.fullmatch(value) or value in (".", ".."):
            raise ValueError(f"非法{label}：{value!r}")
        return value

    def has(self, target):
        data_path, meta_path = self._paths(target)
        return data_path.is_file() and meta_path.is_file()

    def read(self, target):
        data_path, meta_path = self._paths(target)
        return json.loads(data_path.read_text(encoding="utf-8")), json.loads(meta_path.read_text(encoding="utf-8"))

    def save(self, target, data, *, result="ok", token_fingerprint="", tier_label="", quota_profile="", error_excerpt=None, token=""):
        data_path, meta_path = self._paths(target)
        raw = _json_bytes(data)
        if token:
            raw = redact(raw.decode("utf-8"), (token,)).encode("utf-8")
            error_excerpt = redact(error_excerpt or "", (token,)) or None
        now = self.clock().astimezone(timezone.utc).isoformat()
        meta = {
            "schema": "webui.archive.record", "schema_version": "1.0",
            "dataset": target["dataset"], "ticker": target["ticker"], "period": target["period"],
            "api": {"name": target["dataset"], "params": target["params"]},
            "fetched_at": now, "token_fingerprint": token_fingerprint,
            "tier_label": tier_label, "quota_profile": quota_profile,
            "framework_version": __version__, "result": result,
            "error_excerpt": error_excerpt, "content_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
        try:
            _atomic_write(data_path, raw)
            _atomic_write(meta_path, json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            entry = {**meta, "file": data_path.relative_to(self.root).as_posix()}
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / "manifest.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            raise ArchiveUnwritable("原始存档不可写", hint="检查存档根目录权限与可用空间。") from exc
        return meta

    def append_batch(self, batch):
        path = self.root / "batches" / f"{self._part(batch['batch_id'], '批次 ID')}.json"
        try:
            _atomic_write(path, json.dumps(batch, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        except OSError as exc:
            raise ArchiveUnwritable("批次进度不可写", hint="检查存档根目录权限与可用空间。") from exc

    def load_batch(self, batch_id):
        path = self.root / "batches" / f"{self._part(batch_id, '批次 ID')}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def _paths(self, target):
        ticker = self._part(target["ticker"], "标的")
        dataset = self._part(target["dataset"], "数据集")
        period = self._part(target["period"], "期次")
        folder = self.root / ticker / dataset
        params = target.get("params", {})
        expected = {"ts_code": ticker}
        if period != "latest":
            expected["period"] = period
        suffix = ""
        if params != expected:
            param_hash = hashlib.sha256(
                json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:8]
            suffix = f"_{param_hash}"
        data_path = folder / f"{period}{suffix}.json"
        meta_path = folder / f"{period}{suffix}.meta.json"
        if not is_within(data_path.resolve(), self.root) or not is_within(meta_path.resolve(), self.root):
            raise ValueError("存档路径符号链接越出存档根")
        return data_path, meta_path
