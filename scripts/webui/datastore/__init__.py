"""数据层：数据集声明 → 解析 → 缓存 → 查询（AC-3.4）。只读本地文件，**没有任何 HTTP 客户端**。"""

from . import parsers
from .cache import CacheStore, compute_fingerprint, sanitize_key, source_digest
from .datasets import DataStore, Session

__all__ = [
    "CacheStore",
    "DataStore",
    "Session",
    "compute_fingerprint",
    "sanitize_key",
    "source_digest",
    "parsers",
]
