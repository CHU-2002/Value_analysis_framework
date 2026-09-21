"""数据层：数据集声明 → 解析 → 缓存 → 查询（AC-3.4）。

三层模型（设计文档 §8）：

| 层 | 在哪 | 生命周期 |
|----|------|----------|
| 源层 | `output/**` 既有产物 | 既有流程写，本层**只读** |
| 派生缓存层 | `{cache_dir}/{dataset}/…` | 由指纹决定命中/失效；可整体删除，删了只是变慢 |
| 请求内存层 | `Session` | 一次请求内共享，请求结束即释放 |

浏览路径**永不联网**（AC-3.5）：本模块只读本地文件，没有任何 HTTP 客户端。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from .. import __version__
from ..core.errors import ArtifactMissing, BadRequest, ParseFailed, PathOutsideRoot, WebUIError
from .cache import CacheStore, compute_fingerprint, normalize_params, source_digest
from .parsers import get_parser


class Session:
    """单请求内存层：同一次请求里同一分片只解析一次（页面同时要 3 张图时省两次解析）。"""

    def __init__(self) -> None:
        self.memo: dict = {}

    def get(self, marker):
        return self.memo.get(marker)

    def put(self, marker, value) -> None:
        self.memo[marker] = value


class DataStore:
    def __init__(self, config, *, spec_lookup, cache: CacheStore | None = None, clock=None):
        self.config = config
        self.cache = cache if cache is not None else CacheStore(config.cache_dir)
        self._spec_lookup = spec_lookup
        self._clock = clock or (
            lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        )
        self.cache.cleanup_tmp()   # 清掉上次崩溃留下的 .tmp

    # ---------------------------------------------------------------- 对外

    def session(self) -> Session:
        return Session()

    def get(self, name: str, *, base=None, params=None, session: Session | None = None):
        """返回 `(data, meta)`；`meta` 说明这份数据从哪来、是否命中缓存（供界面显示新鲜度）。"""
        spec = self._spec_lookup(name)
        params = params or {}
        sources, digests = self._resolve_sources(spec, base)
        fingerprint = compute_fingerprint(
            parser_version=spec.parser_version,
            params=params,
            sources=digests,
            schema_version=spec.schema_version,
        )
        marker = (name, fingerprint)

        if session is not None:
            memoized = session.get(marker)
            if memoized is not None:
                data, meta = memoized
                return data, {**meta, "cached": True, "from_session": True}

        hit = self.cache.read(name, self._key_for(spec, params, fingerprint), fingerprint)
        if hit is not None:
            data, meta = hit
            meta = {**meta, "cached": True, "from_session": False}
            if session is not None:
                session.put(marker, (data, meta))
            return data, meta

        # 双重检查 + 分片锁：并发请求同一分片时只解析一次
        with self.cache.lock_for(name, self._key_for(spec, params, fingerprint)):
            hit = self.cache.read(name, self._key_for(spec, params, fingerprint), fingerprint)
            if hit is not None:
                data, meta = hit
                meta = {**meta, "cached": True, "from_session": False}
                if session is not None:
                    session.put(marker, (data, meta))
                return data, meta

            parser = get_parser(spec.parser)
            try:
                data = parser(sources, dict(params))
            except WebUIError:
                raise
            except Exception as exc:  # noqa: BLE001（解析失败要有错误码，不能漏成 500 堆栈）
                # message 保持不含异常原文与路径（4xx 不该把文件系统细节回给调用方，D9）；
                # 原文进 hint，排障时仍看得到。
                raise ParseFailed(
                    f"解析数据集 {name!r} 失败（{type(exc).__name__}）",
                    hint=f"{exc}｜若是数据格式变化，请更新解析器并把它对应的 parser_version +1。",
                ) from exc

            meta = {
                "dataset": name,
                "parser": spec.parser,
                "parser_version": spec.parser_version,
                "params": normalize_params(params),
                "sources": [str(name_) for name_, _ in digests],
                "source_digests": {str(name_): digest for name_, digest in digests},
                "generated_at": self._clock(),
                "framework_version": __version__,
                "cached": False,
                "from_session": False,
            }
            recorded = self.cache.write(
                name, self._key_for(spec, params, fingerprint), fingerprint, data, meta
            )
            meta = {**meta, **recorded}
            if session is not None:
                session.put(marker, (data, meta))
            return data, meta

    def invalidate(self, name: str | None = None) -> int:
        """丢派生缓存（**不联网、不重新采集**，只是下次访问重算）。"""
        return self.cache.clear(name)

    # ---------------------------------------------------------------- 内部

    def _key_for(self, spec, params, fingerprint: str) -> str:
        if callable(spec.key):
            return str(spec.key(dict(params)))
        return f"{normalize_params(params)}|{fingerprint[-12:]}"

    def _resolve_sources(self, spec, base) -> tuple:
        if not spec.sources:
            return [], []
        if base is None:
            raise BadRequest(
                f"数据集 {spec.name!r} 需要 base 目录才能解析源文件",
                hint="面板应把公司目录作为 base 传进来。",
            )
        # 路径 jail 必须落在**真正读文件**的入口上：数据层只读 output/ 之下的东西。
        # 只靠插件自己守规矩不够——`safe_join` 原本只覆盖静态资源（独立验收 D6）。
        base_path = Path(base).resolve()
        root = Path(self.config.output_root).resolve()
        if base_path != root and not base_path.is_relative_to(root):
            raise PathOutsideRoot(
                "数据层拒绝读取 output/ 之外的目录",
                hint="base 必须是配置里 output 根（config.output_root）之下的子目录。",
            )
        found: list = []
        for pattern in spec.sources:
            found.extend(sorted(base_path.glob(pattern)))
        existing = [path for path in found if path.is_file()]
        if not existing:
            raise ArtifactMissing(
                f"数据集 {spec.name!r} 没有找到源文件",
                hint=f"按这些模式在 base 目录下查找：{list(spec.sources)}",
            )
        digests = [
            (str(path.relative_to(base_path)) if base_path in path.parents else path.name,
             source_digest(path, spec.hash_strategy))
            for path in existing
        ]
        return existing, digests
