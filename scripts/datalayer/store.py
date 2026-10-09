"""统一原始数据仓：SQLite 单文件 + append-only 的 `manifest.jsonl`（`REQ-011` 的 `AC-3`）。

为什么是 SQLite（owner 已定的存储结构决策，见 `REQ-011` 的「备注」）：

- 按 (标的, 数据集, 期次, 参数) 建**唯一索引**天然去重，不是「先查后写」的竞态；
- 单条写入是一个事务，**中断不留半条记录**；
- 单文件即可**整体拷走**（`.db` + `manifest.jsonl`）；
- 跨公司聚合查询直接可写，为将来的选股/组合/多公司对比铺路。

资产语义（沿用 `REQ-009.4` 的 `AC-4.3`）：**不过期、不主动删**；
删除只有一个显式动作 + 二次确认（`wipe`），且 `manifest.jsonl` 只追加、不改写历史。

兼容期双写（`DATA_LAYER_PLAN` §10.2）：写仓的同时继续产出
`manifest.jsonl` 与 `batches/<batch_id>.json`，于是 GUI 的三个采集面板读路径不变；
`REQ-012.4` 切到仓之后再降级为「只写 manifest.jsonl」。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import RESULT_KINDS, SCHEMA_VERSION, SHAPES
from .dataframe_codec import decode_frame, encode_frame
from .errors import StoreUnavailable
from .registry import describe

# 凭据类入参永不落盘（`AC-3` / `AC-4.7`）——参数只留指纹与可复算的规范化 JSON。
CREDENTIAL_KEYS = (
    "token", "secret", "password", "passwd", "api_key", "apikey", "authorization",
    "access_key", "access_token", "private_key", "auth",
)

# **投影类**入参不进唯一键：它们只决定「要哪几列、要哪一段窗口、返回多少行」，
# 不改变这份数据的语义。把它们算进键，会出现「拉取时的 fields 与调用点的 fields
# 差一个字段就永远读不到仓里的数据」——离线重建会静默变成一片「数据缺失」。
# 读取路径据此做投影与窗口裁剪，并且**窗口更窄时判为未命中**（缺口可见，不糊弄）。
PROJECTION_KEYS = ("fields", "start_date", "end_date", "limit")

# 记录表的列顺序（INSERT 与 SELECT 共用，避免两处漂移）。
COLUMNS = (
    "ticker", "dataset", "period", "param_key", "params_json", "shape", "period_type",
    "cumulative", "result", "error_excerpt", "columns_json", "rows_json", "content_sha256",
    "bytes", "fetched_at", "token_fingerprint", "tier_label", "quota_profile",
    "framework_version", "batch_id",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS raw_record (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker            TEXT    NOT NULL,
  dataset           TEXT    NOT NULL,
  period            TEXT    NOT NULL,
  param_key         TEXT    NOT NULL,
  params_json       TEXT    NOT NULL,
  shape             TEXT    NOT NULL,
  period_type       TEXT    NOT NULL,
  cumulative        INTEGER NOT NULL,
  result            TEXT    NOT NULL,
  error_excerpt     TEXT,
  columns_json      TEXT    NOT NULL,
  rows_json         TEXT    NOT NULL,
  content_sha256    TEXT    NOT NULL,
  bytes             INTEGER NOT NULL,
  fetched_at        TEXT    NOT NULL,
  token_fingerprint TEXT    NOT NULL,
  tier_label        TEXT,
  quota_profile     TEXT,
  framework_version TEXT    NOT NULL,
  batch_id          TEXT,
  UNIQUE (ticker, dataset, period, param_key)
);
CREATE INDEX IF NOT EXISTS raw_record_lookup ON raw_record (dataset, ticker, period_type);
CREATE INDEX IF NOT EXISTS raw_record_result ON raw_record (result);
CREATE INDEX IF NOT EXISTS raw_record_gap    ON raw_record (result) WHERE result NOT IN ('ok', 'empty');

CREATE TABLE IF NOT EXISTS universe (
  ticker       TEXT PRIMARY KEY,
  display_name TEXT    NOT NULL,
  market       TEXT    NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 1,
  tier         TEXT,
  note         TEXT,
  updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS batch (
  batch_id      TEXT PRIMARY KEY,
  profile       TEXT NOT NULL,
  status        TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  heartbeat_at  TEXT,
  owner_pid     INTEGER,
  estimate      INTEGER NOT NULL,
  targets_json  TEXT NOT NULL,
  progress_json TEXT NOT NULL,
  usage_json    TEXT NOT NULL,
  summary_json  TEXT
);

CREATE TABLE IF NOT EXISTS batch_target (
  batch_id      TEXT NOT NULL,
  target_key    TEXT NOT NULL,
  ticker        TEXT,
  dataset       TEXT,
  period        TEXT,
  params_json   TEXT,
  result        TEXT,
  error_excerpt TEXT,
  updated_at    TEXT,
  PRIMARY KEY (batch_id, target_key),
  FOREIGN KEY (batch_id) REFERENCES batch(batch_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


class SaveResult(NamedTuple):
    record: dict | None
    created: bool
    skipped: bool


def sanitize_params(params: dict | None) -> dict:
    """去掉凭据类键，保留其余入参（含 ``None`` 值，它是语义的一部分）。"""

    if not params:
        return {}
    return {
        str(key): value
        for key, value in params.items()
        if str(key).lower() not in CREDENTIAL_KEYS
    }


def key_params(params: dict | None) -> dict:
    """唯一键用的入参：剔除凭据类键、**投影类**键与 ``period``。

    `period` 不进指纹是因为它已经是一列（`raw_record.period`）：同一个接口在同一标的上
    会按多个期次各留一条记录，指纹应当是「同一类请求」的标识，而不是单条记录的标识。
    这样读取路径才能把多个期次的记录**组合**成调用点要的整段历史
    （`tushare_modules` 里的报告期接口都是一次调用取回整段历史，从不按期次过滤）。
    """

    return {
        str(key): value
        for key, value in sanitize_params(params).items()
        if str(key).lower() not in PROJECTION_KEYS and str(key).lower() != "period"
    }


def semantic_params(params: dict | None) -> dict:
    """请求里的**语义**入参（既不是凭据、也不是投影、也不是 period）。

    读取路径用它判断「仓里的这条记录能不能服务这次请求」，并在需要时按同名列过滤行。
    """

    return {
        str(key): value
        for key, value in sanitize_params(params).items()
        if str(key).lower() not in PROJECTION_KEYS
        and str(key).lower() not in ("period", "ts_code", "ticker")
    }


def canonical_params(params: dict | None) -> str:
    """规范化 JSON：排序键、紧凑分隔符、不转义非 ASCII——指纹可复算的前提。

    存进 `params_json` 的是**剔除凭据后**的入参（保留投影类键，便于审计与窗口覆盖判断）。
    """

    return json.dumps(sanitize_params(params), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def param_key(params: dict | None) -> str:
    """``sha256(规范化唯一键入参)[:16]``。

    与既有 `webui/archive/batch.py::_target_key` 是**同一个目标 → 同一个 key** 的要求，
    差别只在截断长度（那边是整个目标 dict 的全长摘要，用于批次内进度对齐）。
    """

    return hashlib.sha256(
        json.dumps(key_params(params), sort_keys=True, ensure_ascii=False,
                   separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:16]


def target_key(target: dict) -> str:
    """批次内目标的稳定键。

    算法**逐字**沿用 `webui/archive/batch.py::_target_key`：批次升级到仓之后，
    旧批次 JSON 里的 `completed` 键仍要能被认出来（断点续跑不重拉），
    由 `tests/test_data_pull.py` 断言两边一致，防止有人单方面改掉。
    """

    canonical = json.dumps(target, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# 缺口原因的人话标签（与 `datalayer.gaps.RESULT_KINDS` 一一对应）。
GAP_RESULT_LABELS = {
    "no_permission": "无权限",
    "rate_limited": "频率受限",
    "error": "接口错误",
}


def rows_for_request(frame, request: dict, record_params: dict):
    """按请求的语义入参裁剪行；无法确认服务能力时返回 ``None``。

    规则：请求里的每个语义入参，记录自己的入参已经钉住同一个值就无需按列过滤；
    否则必须在帧里找到同名列并确认该值存在——找不到就说明这条记录服务不了这次请求
    （宁可报缺口，也不拿「没带过滤条件的响应」去冒充带过滤条件的请求，
    例如旧的 `fina_mainbz` 记录没带 `type="P"`、响应里也没有 `type` 列）。
    """

    for key, value in (request or {}).items():
        if key in record_params and str(record_params[key]) == str(value):
            continue
        if key not in frame.columns:
            return None
        values = frame[key].astype(str)
        if str(value) not in set(values):
            return None
        frame = frame[values == str(value)]
    return frame


def _framework_version() -> str:
    from . import __version__

    return __version__


def record_serves(record_params: dict, request_params: dict) -> bool:
    """仓里的这条记录能不能服务这次请求（参数层面的判断，不看帧内容）。

    规则：请求里的每个语义入参，要么记录里就是同一个值，要么记录里**没有**这个参数
    ——后一种情况由读取路径在帧里找同名列再过滤（例如拉取时省略了 `report_type`，
    而调用点要 `report_type="1"`；接口返回的帧里带 `report_type` 列）。
    记录多出的参数不算冲突：一次「不带 `report_type`」的请求可以由
    `report_type=1` 与 `report_type=6` 两条记录组合服务。
    """

    for key, value in (request_params or {}).items():
        if key in record_params and str(record_params[key]) != str(value):
            return False
    return True


class DataStore:
    """仓的门面：读写记录、标的宇宙、批次进度、导出与统计。"""

    def __init__(self, root, *, clock=None, framework_version=None):
        self.root = Path(root).expanduser()
        self.db_path = self.root / "store.db"
        self.manifest_path = self.root / "manifest.jsonl"
        self.batches_dir = self.root / "batches"
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.framework_version = framework_version or _framework_version()
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 连接

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.batches_dir.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA_SQL)
            connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise StoreUnavailable(
                f"原始仓不可用：{self.root}（{type(exc).__name__}: {exc}）",
            ) from exc
        for path, mode in ((self.root, 0o700), (self.db_path, 0o600)):
            try:
                os.chmod(path, mode)
            except OSError:  # pragma: no cover - 权限收紧失败不该让仓不可用
                pass
        self._conn = connection
        self._meta_set(connection, "schema_version", SCHEMA_VERSION)
        self._meta_set(connection, "created_by", f"datalayer {self.framework_version}")
        return connection

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    @contextmanager
    def transaction(self):
        """一个事务；异常整体回滚（写入是原子的）。"""

        connection = self._connect()
        with self._lock:
            try:
                connection.execute("BEGIN")
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    # ------------------------------------------------------------------ meta

    @staticmethod
    def _meta_set(connection, key: str, value: str) -> None:
        connection.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        connection.commit()

    def meta_get(self, key: str, default=None):
        row = self._connect().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row is not None else default

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._meta_set(self._connect(), key, str(value))

    # ------------------------------------------------------------------ 写入

    def save_raw(self, record: dict, *, on_conflict: str = "replace") -> SaveResult:
        """写入一条原始记录。

        ``on_conflict="skip"`` 用于迁移的幂等语义：同键已存在时**不动**既有记录，
        返回 ``skipped=True``（冲突由调用方比对 `content_sha256` 后决定是否报告）。
        """

        if on_conflict not in ("replace", "skip"):
            raise ValueError(f"未知的冲突策略：{on_conflict!r}")
        data = self._normalize(record)
        connection = self._connect()
        with self._lock:
            existing = self._select(connection, data["ticker"], data["dataset"], data["period"],
                                    data["param_key"])
            if existing is not None and on_conflict == "skip":
                return SaveResult(self._row(existing), created=False, skipped=True)
            with self.transaction() as transaction:
                transaction.execute(
                    f"INSERT INTO raw_record ({', '.join(COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in COLUMNS)}) "
                    "ON CONFLICT(ticker, dataset, period, param_key) DO UPDATE SET "
                    + ", ".join(f"{column} = excluded.{column}" for column in COLUMNS
                                if column not in ("ticker", "dataset", "period", "param_key")),
                    tuple(data[column] for column in COLUMNS),
                )
        stored = self.find(data["ticker"], data["dataset"], data["period"],
                           param_digest=data["param_key"])
        self.append_manifest(stored)
        return SaveResult(stored, created=existing is None, skipped=False)

    def write_frame(self, *, ticker: str, dataset: str, period: str, params: dict,
                    frame, result: str, cumulative: bool | None = None,
                    shape: str | None = None, period_type: str | None = None,
                    error_excerpt: str | None = None, token_fingerprint: str = "",
                    tier_label: str = "", quota_profile: str = "", batch_id: str | None = None,
                    fetched_at: str | None = None, on_conflict: str = "replace") -> SaveResult:
        """把一个 DataFrame 写进仓（口径字段默认由注册表推出）。"""

        encoded = encode_frame(frame)
        derived_shape, derived_type, derived_cumulative = describe(dataset, period)
        record = {
            "ticker": ticker,
            "dataset": dataset,
            "period": period,
            "params": params,
            "shape": shape or derived_shape,
            "period_type": period_type or derived_type,
            "cumulative": derived_cumulative if cumulative is None else cumulative,
            "result": result,
            "error_excerpt": error_excerpt,
            **encoded,
            "fetched_at": fetched_at or self._now(),
            "token_fingerprint": token_fingerprint,
            "tier_label": tier_label,
            "quota_profile": quota_profile,
            "framework_version": self.framework_version,
            "batch_id": batch_id,
        }
        return self.save_raw(record, on_conflict=on_conflict)

    def append_manifest(self, record: dict) -> None:
        """append-only 的人读/审计清单（字段兼容既有 `~/turtle_archive/manifest.jsonl`）。"""

        if not record:
            return
        entry = {
            "schema": "datalayer.raw_record",
            "schema_version": SCHEMA_VERSION,
            "source": "datalayer.store",
            "ticker": record["ticker"],
            "dataset": record["dataset"],
            "period": record["period"],
            "api": {"name": record["dataset"], "params": record["params"]},
            "shape": record["shape"],
            "period_type": record["period_type"],
            "cumulative": record["cumulative"],
            "result": record["result"],
            "error_excerpt": record["error_excerpt"],
            "content_sha256": record["content_sha256"],
            "bytes": record["bytes"],
            "fetched_at": record["fetched_at"],
            "token_fingerprint": record["token_fingerprint"],
            "tier_label": record["tier_label"],
            "quota_profile": record["quota_profile"],
            "framework_version": record["framework_version"],
            "batch_id": record["batch_id"],
        }
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.manifest_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            raise StoreUnavailable(f"审计清单不可写：{self.manifest_path}") from exc

    # ------------------------------------------------------------------ 读取

    def find_family(self, ticker: str, dataset: str, *, params: dict | None = None,
                    period: str | None = None, include_rows: bool = True) -> list[dict]:
        """同一 (标的, 数据集) 下**可能服务这次请求**的全部记录（按期次倒序）。

        这是「仓是超集」的落点：报告期接口一次调用取回整段历史，而拉取按期次各留一条，
        所以一次读取要把若干条组合起来。过滤分两步（见 :func:`record_serves`）：
        先在 SQL 里按标的/数据集/结果收敛，再在 Python 里按参数兼容性收敛。
        """

        request = semantic_params(params)
        rows = self._connect().execute(
            "SELECT * FROM raw_record WHERE ticker = ? AND dataset = ? "
            "AND result IN ('ok', 'empty') AND (? IS NULL OR period = ?) "
            "ORDER BY period DESC, fetched_at DESC",
            (ticker, dataset, period, period),
        ).fetchall()
        records = [self._row(row, include_rows=include_rows) for row in rows]
        return [record for record in records
                if record_serves(record.get("params") or {}, request)]

    def find(self, ticker: str, dataset: str, period: str, *, params: dict | None = None,
             param_digest: str | None = None, include_rows: bool = True) -> dict | None:
        """按唯一键取一条记录；``params`` 与 ``param_digest`` 二者给一个即可。"""

        key = param_digest if param_digest is not None else (
            param_key(params) if params is not None else None
        )
        if key is None:
            raise ValueError("find() 需要 params 或 param_digest")
        row = self._select(self._connect(), ticker, dataset, period, key)
        return self._row(row, include_rows=include_rows) if row is not None else None

    def has(self, target: dict) -> bool:
        return self.result_of(target) is not None

    def result_of(self, target: dict) -> str | None:
        """上一次抓取的结果枚举；没有记录返回 ``None``（= 缺口）。

        `None` 与 `empty` 是两回事：前者是「还没拉过」，后者是「拉到了但确实为空」。
        两者都不该在「补齐缺口」时被当成未完成——`empty` 已在仓里，按 `AC-6` 的去重语义跳过。
        """

        row = self._connect().execute(
            "SELECT result FROM raw_record WHERE ticker = ? AND dataset = ? AND period = ? "
            "AND param_key = ?",
            (str(target.get("ticker", "")), str(target.get("dataset", "")),
             str(target.get("period", "")), param_key(target.get("params", {}))),
        ).fetchone()
        return row["result"] if row is not None else None

    def gap_reason(self, ticker: str, dataset: str, *, params: dict | None = None,
                   period: str | None = None) -> dict | None:
        """仓里对**这次读取**记下的失败原因（无权限 / 限频 / 错误）。

        离线重建拿它把缺口说清楚：联网路径在同样的失败下会渲染
        「数据缺失（Tushare yc_cb 接口未授权；当前账号权限不足）」，重建若只写「数据缺失」
        就丢掉了一半信息（`AC-9` 实跑观察项①）。返回 ``None`` = 仓里没有能解释这次缺口的记录
        （例如「压根没拉过」，那时写「数据缺失」才是诚实的）。
        """

        request = semantic_params(params)
        rows = self._connect().execute(
            "SELECT * FROM raw_record WHERE ticker = ? AND dataset = ? "
            "AND result NOT IN ('ok', 'empty') AND (? IS NULL OR period = ?) "
            "ORDER BY period DESC, fetched_at DESC",
            (ticker, dataset, period, period),
        ).fetchall()
        for row in rows:
            record = self._row(row, include_rows=False)
            if record_serves(record.get("params") or {}, request):
                return {
                    "result": record["result"],
                    "label": GAP_RESULT_LABELS.get(record["result"], record["result"]),
                    "excerpt": record.get("error_excerpt") or "",
                }
        return None

    def serves_target(self, target: dict) -> bool:
        """目标**现在**能不能真的由仓服务（读取路径的判据，不只是「上次结果 ok」）。

        `result_of` 回答的是「这条记录当时拉成功了吗」，而读取路径还要过两关：
        语义入参在帧里核得出来（`rows_for_request`）、时间窗口不比请求更窄。
        独立复核 B3/B5 在真实仓上抓到：只看 `result` 会让 `--only-gaps` 判某个目标
        「完备」（于是永远补不上），同时离线重建又把它报成缺口——同一个仓两个矛盾的结论。
        这里把两关都过一遍，缺口判定因此与读取路径同源。
        """

        from .dataframe_codec import decode_frame

        params = dict(target.get("params") or {})
        ticker = str(target.get("ticker") or params.get("ts_code") or "")
        dataset = str(target.get("dataset") or "")
        period = str(target.get("period") or "")
        records = self.find_family(ticker, dataset, params=params,
                                   period=None if period in ("", "latest") else period)
        if not records:
            return False
        if not self._window_covers(records, params):
            return False
        request = semantic_params(params)
        requested_fields = [item.strip() for item in str(params.get("fields") or "").split(",")
                            if item.strip()]
        for record in records:
            frame = decode_frame(record["columns_json"], record["rows_json"])
            if frame.empty:
                # 「确实为空」也算有记录（与 `REQ-009.4` 的 `AC-4.5` 一致）。
                return True
            if requested_fields and not any(name in frame.columns for name in requested_fields):
                # 与 `_replay` 的投影规则同源：请求的列一列都不在帧里 → 这次读取会判未命中。
                continue
            if rows_for_request(frame, request, record.get("params") or {}) is not None:
                return True
        return False

    @staticmethod
    def _window_covers(records: list[dict], params: dict) -> bool:
        """请求的起点只要不比仓里的起点更早，就算覆盖（终点是「数据截至」，不参与判定）。"""

        requested_start = str(params.get("start_date") or "")
        if not requested_start:
            return True
        starts = [str((record.get("params") or {}).get("start_date") or "") for record in records]
        known = [value for value in starts if value]
        return not known or min(known) <= requested_start

    def records(self, *, ticker: str | None = None, dataset: str | None = None,
                period: str | None = None, period_type: str | None = None,
                shape: str | None = None, result: str | None = None,
                result_in=None, include_rows: bool = False, limit: int | None = None) -> list[dict]:
        clauses, values = [], []
        for column, value in (("ticker", ticker), ("dataset", dataset), ("period", period),
                              ("period_type", period_type), ("shape", shape), ("result", result)):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        if result_in is not None:
            placeholders = ", ".join("?" for _ in result_in)
            clauses.append(f"result IN ({placeholders})")
            values.extend(result_in)
        sql = "SELECT * FROM raw_record"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ticker, dataset, period, param_key"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self._connect().execute(sql, tuple(values)).fetchall()
        return [self._row(row, include_rows=include_rows) for row in rows]

    def frame(self, record_or_ticker, dataset: str | None = None, period: str | None = None,
              params: dict | None = None):
        """记录（或唯一键）→ DataFrame。

        传进来的记录若是 `include_rows=False` 取的（如 `records()` 的默认形态），
        这里按唯一键把行读回来——调用方不该为了拿一帧而记住哪个查询带了行。
        """

        if isinstance(record_or_ticker, dict):
            record = record_or_ticker
        else:
            record = self.find(record_or_ticker, dataset, period, params=params)
        if record is None:
            return None
        if "rows_json" not in record:
            record = self.find(record["ticker"], record["dataset"], record["period"],
                               param_digest=record["param_key"]) or record
        if "rows_json" not in record:
            return None
        return decode_frame(record["columns_json"], record["rows_json"])

    # ------------------------------------------------------------------ 台账/导出/统计

    def completeness(self, *, ticker: str | None = None) -> dict:
        """按仓的实际情况算完备度（复用既有结果分类，`AC-4.5`）。"""

        from .gaps import completeness as _completeness

        return _completeness(self.records(ticker=ticker))

    def gaps(self, *, ticker: str | None = None, dataset: str | None = None) -> list[dict]:
        rows = self.records(ticker=ticker, dataset=dataset, include_rows=False)
        return [row for row in rows if row["result"] not in ("ok", "empty")]

    def stats(self) -> dict:
        connection = self._connect()
        total = connection.execute("SELECT COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS b "
                                   "FROM raw_record").fetchone()
        by_result = {row["result"]: row["n"] for row in connection.execute(
            "SELECT result, COUNT(*) AS n FROM raw_record GROUP BY result ORDER BY result")}
        by_dataset = {row["dataset"]: row["n"] for row in connection.execute(
            "SELECT dataset, COUNT(*) AS n FROM raw_record GROUP BY dataset ORDER BY dataset")}
        by_period_type = {row["period_type"]: row["n"] for row in connection.execute(
            "SELECT period_type, COUNT(*) AS n FROM raw_record GROUP BY period_type "
            "ORDER BY period_type")}
        universe = connection.execute("SELECT COUNT(*) AS n FROM universe").fetchone()["n"]
        batches = connection.execute("SELECT COUNT(*) AS n FROM batch").fetchone()["n"]
        return {
            "schema_version": self.meta_get("schema_version", SCHEMA_VERSION),
            "store": str(self.db_path),
            "records": total["n"],
            "bytes": total["b"],
            "by_result": by_result,
            "by_dataset": by_dataset,
            "by_period_type": by_period_type,
            "universe": universe,
            "batches": batches,
            "framework_version": self.framework_version,
        }

    def export(self, out_dir, *, ticker: str | None = None) -> int:
        """把仓导出成一棵可读 JSON 树（与 `REQ-009.4` 的旧布局一致），用于评审与跨机对比。

        只读操作：不删、不改仓里的任何字节。
        """

        target_root = Path(out_dir).expanduser()
        target_root.mkdir(parents=True, exist_ok=True)
        count = 0
        manifest_entries = []
        for record in self.records(ticker=ticker, include_rows=True):
            folder = target_root / record["ticker"] / record["dataset"]
            folder.mkdir(parents=True, exist_ok=True)
            stem = record["period"] if record["period"] != "latest" else "latest"
            suffix = "" if record["param_key"] == param_key({"ts_code": record["ticker"]}) else \
                f"_{record['param_key'][:8]}"
            data_path = folder / f"{stem}{suffix}.json"
            data_path.write_text(record["rows_json"], encoding="utf-8")
            meta = {key: value for key, value in record.items()
                    if key not in ("rows_json", "columns_json")}
            meta["columns"] = json.loads(record["columns_json"])
            (folder / f"{stem}{suffix}.meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            manifest_entries.append({**meta, "file": data_path.relative_to(target_root).as_posix()})
            count += 1
        with (target_root / "manifest.jsonl").open("w", encoding="utf-8") as stream:
            for entry in manifest_entries:
                stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return count

    def wipe(self, confirm: str) -> dict:
        """显式删除仓内记录（资产语义：删除必须显式动作 + 二次确认）。

        `confirm` 必须**逐字**等于 ``"WIPE"``；`manifest.jsonl` 只追加一条 wipe 记录，
        不改写历史行。
        """

        if confirm != "WIPE":
            raise ValueError("删除原始仓需要二次确认：--wipe 与 --confirm WIPE 同时给出")
        connection = self._connect()
        with self.transaction() as transaction:
            raw = transaction.execute("DELETE FROM raw_record").rowcount
            targets = transaction.execute("DELETE FROM batch_target").rowcount
            batches = transaction.execute("DELETE FROM batch").rowcount
        entry = {"schema": "datalayer.wipe", "schema_version": SCHEMA_VERSION,
                 "at": self._now(), "records": raw, "batch_targets": targets, "batches": batches}
        try:
            with self.manifest_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:  # pragma: no cover
            pass
        return entry

    # ------------------------------------------------------------------ 批次（兼容期双写）

    def append_batch(self, batch: dict) -> None:
        """批次快照写两处：SQLite（断点续跑与查询）+ `batches/<id>.json`（GUI 兼容）。"""

        batch_id = str(batch.get("batch_id", ""))
        if not batch_id:
            raise ValueError("批次缺少 batch_id")
        connection = self._connect()
        completed = batch.get("completed") or {}
        with self.transaction() as transaction:
            transaction.execute(
                "INSERT INTO batch (batch_id, profile, status, created_at, heartbeat_at, owner_pid,"
                " estimate, targets_json, progress_json, usage_json, summary_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(batch_id) DO UPDATE SET profile = excluded.profile, "
                "status = excluded.status, heartbeat_at = excluded.heartbeat_at, "
                "owner_pid = excluded.owner_pid, estimate = excluded.estimate, "
                "targets_json = excluded.targets_json, progress_json = excluded.progress_json, "
                "usage_json = excluded.usage_json, summary_json = excluded.summary_json",
                (batch_id, batch.get("profile", ""), batch.get("status", "pending"),
                 batch.get("created_at") or self._now(), batch.get("heartbeat_at"),
                 batch.get("owner_pid"), int(batch.get("estimate", len(batch.get("targets", [])))),
                 json.dumps(batch.get("targets", []), ensure_ascii=False, sort_keys=True),
                 json.dumps(batch.get("progress", {}), ensure_ascii=False, sort_keys=True),
                 json.dumps(batch.get("usage", {}), ensure_ascii=False, sort_keys=True),
                 json.dumps(batch.get("summary"), ensure_ascii=False, sort_keys=True)
                 if batch.get("summary") is not None else None),
            )
            transaction.execute("DELETE FROM batch_target WHERE batch_id = ?", (batch_id,))
            for key, item in completed.items():
                transaction.execute(
                    "INSERT INTO batch_target (batch_id, target_key, ticker, dataset, period,"
                    " params_json, result, error_excerpt, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (batch_id, key, item.get("ticker"), item.get("dataset"), item.get("period"),
                     json.dumps(item.get("params") or {}, ensure_ascii=False, sort_keys=True),
                     item.get("result"), item.get("error_excerpt"), self._now()),
                )
        try:
            self.batches_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.batches_dir / f".{batch_id}.tmp"
            temporary.write_text(json.dumps(batch, ensure_ascii=False, sort_keys=True),
                                 encoding="utf-8")
            os.replace(temporary, self.batches_dir / f"{batch_id}.json")
        except OSError as exc:
            raise StoreUnavailable(f"批次进度不可写：{self.batches_dir}") from exc

    def load_batch(self, batch_id: str) -> dict | None:
        """读批次：先看仓，再回退到旧的 `batches/<id>.json`（升级前的批次要能续跑）。"""

        connection = self._connect()
        row = connection.execute("SELECT * FROM batch WHERE batch_id = ?", (str(batch_id),)).fetchone()
        if row is None:
            legacy = self.batches_dir / f"{batch_id}.json"
            if not legacy.is_file():
                return None
            try:
                batch = json.loads(legacy.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            self.append_batch(batch)
            return batch
        completed = {}
        for item in connection.execute(
                "SELECT * FROM batch_target WHERE batch_id = ? ORDER BY target_key", (str(batch_id),)):
            completed[item["target_key"]] = {
                "ticker": item["ticker"], "dataset": item["dataset"], "period": item["period"],
                "params": json.loads(item["params_json"] or "{}"),
                "result": item["result"], "error_excerpt": item["error_excerpt"],
            }
        return {
            "batch_id": row["batch_id"],
            "profile": row["profile"],
            "status": row["status"],
            "created_at": row["created_at"],
            "heartbeat_at": row["heartbeat_at"],
            "owner_pid": row["owner_pid"],
            "estimate": row["estimate"],
            "targets": json.loads(row["targets_json"]),
            "progress": json.loads(row["progress_json"]),
            "usage": json.loads(row["usage_json"]),
            "summary": json.loads(row["summary_json"]) if row["summary_json"] else None,
            "completed": completed,
        }

    def list_batches(self, limit: int = 20) -> list[dict]:
        rows = self._connect().execute(
            "SELECT batch_id FROM batch ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [self.load_batch(row["batch_id"]) for row in rows]

    # ------------------------------------------------------------------ 内部

    def _now(self) -> str:
        return self.clock().astimezone(timezone.utc).isoformat()

    @staticmethod
    def _select(connection, ticker, dataset, period, key):
        return connection.execute(
            "SELECT * FROM raw_record WHERE ticker = ? AND dataset = ? AND period = ? "
            "AND param_key = ?", (ticker, dataset, period, key)).fetchone()

    def _normalize(self, record: dict) -> dict:
        data = dict(record)
        data["ticker"] = str(data.get("ticker", ""))
        data["dataset"] = str(data.get("dataset", ""))
        data["period"] = str(data.get("period", "")) or "latest"
        if not data["dataset"]:
            # 空的 ticker 是**合法**的：`us_daily` 这类全市场快照本来就没有单一标的
            # （调用点不传 ts_code），按「一条记录 = 一次调用」存下来。
            raise ValueError("原始记录必须带 dataset")
        params = data.pop("params", None)
        data["params_json"] = data.get("params_json") or canonical_params(params)
        data["param_key"] = data.get("param_key") or param_key(params or json.loads(data["params_json"]))
        shape, period_type, cumulative = describe(data["dataset"], data["period"])
        data["shape"] = data.get("shape") or shape
        data["period_type"] = data.get("period_type") or period_type
        data["cumulative"] = int(bool(data.get("cumulative", cumulative)))
        data["result"] = str(data.get("result") or "ok")
        if data["result"] not in RESULT_KINDS:
            raise ValueError(f"未知的结果枚举：{data['result']!r}")
        if data["shape"] not in SHAPES:
            raise ValueError(f"未知的记录形状：{data['shape']!r}")
        data.setdefault("error_excerpt", None)
        data["columns_json"] = data.get("columns_json") or "[]"
        data["rows_json"] = data.get("rows_json") or "[]"
        if not data.get("content_sha256"):
            data["content_sha256"] = hashlib.sha256(
                data["rows_json"].encode("utf-8")).hexdigest()
        if data.get("bytes") is None:
            data["bytes"] = len(data["rows_json"].encode("utf-8"))
        data["fetched_at"] = data.get("fetched_at") or self._now()
        data["token_fingerprint"] = data.get("token_fingerprint") or ""
        data.setdefault("tier_label", "")
        data.setdefault("quota_profile", "")
        data["framework_version"] = data.get("framework_version") or self.framework_version
        data.setdefault("batch_id", None)
        return data

    @staticmethod
    def _row(row: sqlite3.Row, *, include_rows: bool = True) -> dict:
        record = {key: row[key] for key in row.keys()}
        record["cumulative"] = bool(record.get("cumulative"))
        try:
            record["params"] = json.loads(record.get("params_json") or "{}")
        except json.JSONDecodeError:  # pragma: no cover - 仓外被改坏时也不该炸掉读路径
            record["params"] = {}
        record.pop("id", None)
        if not include_rows:
            record.pop("rows_json", None)
        return record
