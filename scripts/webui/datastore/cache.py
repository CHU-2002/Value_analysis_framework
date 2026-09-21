"""派生缓存：指纹、命中、原子写、失效（AC-3.4）。

三条规矩：

1. **指纹 = sha256(解析器版本 ‖ 规范化参数 ‖ 每个源文件的内容摘要 ‖ schema 版本)**，
   任一变化就自动失效——尤其是**解析器逻辑一改（`parser_version` +1）旧缓存就作废**，
   否则会拿着旧口径的数字当新结果；
2. **原子写**：先写 `.tmp` 再 `os.replace`，进程被杀也不会留下半截 JSON；
3. **同一分片一把锁**：并发请求同一数据集只解析一次，其余等结果（省 CPU，也避免写坏）。

用文件内容摘要而不是 mtime：`data_pack_market.md` 只有 24KB、报告 50KB，全量 sha256 亚毫秒级，
但能避免「git checkout / 复制文件导致 mtime 变了、内容没变」的假失效；将来的大二进制用 `stat` 策略。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from ..core.errors import ParseFailed

CACHE_SCHEMA_VERSION = "1.0"
_CHUNK = 1 << 16


def _short_hash(text: str, length: int = 8) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def sanitize_key(key: str) -> str:
    """把缓存分片键变成安全文件名：保留可读部分，再接一段原键的短哈希防碰撞。"""
    readable = "".join(char if (char.isalnum() or char in "-_.") else "_" for char in str(key))
    readable = readable.strip("._")[:60] or "key"
    return f"{readable}-{_short_hash(str(key))}"


def normalize_params(params) -> str:
    """把参数规范成稳定字符串（排序、去空白），保证「同输入 → 同指纹」。"""
    if not params:
        return "{}"
    return json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str)


def source_digest(path: Path, strategy: str = "sha256") -> str:
    """源文件摘要。`sha256` 看内容，`stat` 看 size+mtime（留给将来的大二进制）。"""
    if strategy == "stat":
        info = path.stat()
        return f"stat:{info.st_size}:{info.st_mtime_ns}"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def compute_fingerprint(
    *,
    parser_version: int,
    params,
    sources,
    schema_version: str = CACHE_SCHEMA_VERSION,
) -> str:
    """`sources` 是 [(相对路径或名字, 摘要)] 的序列。"""
    payload = json.dumps(
        {
            "parser_version": int(parser_version),
            "params": json.loads(normalize_params(params)),
            "sources": sorted((str(name), str(digest)) for name, digest in sources),
            "schema_version": schema_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CacheStore:
    """缓存读写。目录结构：`{root}/{dataset}/{key}.json` + 同名 `.meta.json`。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._locks: dict = {}
        self._guard = threading.Lock()

    # ---------------------------------------------------------------- 路径

    def entry_paths(self, dataset: str, key: str) -> tuple:
        directory = self.root / sanitize_key(dataset)
        stem = sanitize_key(key)
        return directory / f"{stem}.json", directory / f"{stem}.meta.json"

    def lock_for(self, dataset: str, key: str) -> threading.Lock:
        marker = f"{dataset}\x00{key}"
        with self._guard:
            lock = self._locks.get(marker)
            if lock is None:
                lock = threading.Lock()
                self._locks[marker] = lock
            return lock

    # ---------------------------------------------------------------- 读写

    def read(self, dataset: str, key: str, fingerprint: str):
        """命中返回 (data, meta)；未命中或指纹不符返回 None。"""
        data_path, meta_path = self.entry_paths(dataset, key)
        if not data_path.is_file() or not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(meta, dict) or meta.get("fingerprint") != fingerprint:
            return None
        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data, meta

    def write(self, dataset: str, key: str, fingerprint: str, data, meta: dict) -> dict:
        """原子写：数据与元信息各写一次 `.tmp` 再 replace。"""
        data_path, meta_path = self.entry_paths(dataset, key)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        recorded = dict(meta)
        recorded["fingerprint"] = fingerprint
        recorded["cache_schema_version"] = CACHE_SCHEMA_VERSION
        self._atomic_write(data_path, json.dumps(data, ensure_ascii=False))
        self._atomic_write(meta_path, json.dumps(recorded, ensure_ascii=False, indent=2))
        return recorded

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise ParseFailed(
                f"缓存写入失败：{path}",
                hint=f"检查目录权限与磁盘空间：{exc}",
            ) from exc

    # ---------------------------------------------------------------- 维护

    def clear(self, dataset: str | None = None) -> int:
        """清派生缓存。删了只是变慢（下次自动重建），不会丢任何源数据。"""
        target = self.root / sanitize_key(dataset) if dataset else self.root
        if not target.exists():
            return 0
        removed = 0
        for path in sorted(target.rglob("*"), reverse=True):
            if path.is_file() and (path.suffix == ".json" or path.name.endswith(".tmp")):
                path.unlink(missing_ok=True)
                removed += 1
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        return removed

    def cleanup_tmp(self) -> int:
        """清掉崩溃留下的 `.tmp`（启动时跑一次）。"""
        if not self.root.exists():
            return 0
        removed = 0
        for path in self.root.rglob("*.tmp"):
            path.unlink(missing_ok=True)
            removed += 1
        return removed

    def stats(self) -> dict:
        entries = list(self.root.rglob("*.meta.json")) if self.root.exists() else []
        return {"root": str(self.root), "entries": len(entries)}
