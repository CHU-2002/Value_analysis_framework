"""取数门面 `DataAccess`：**唯一**的远程访问收口点（`DATA_LAYER_PLAN` §6）。

原来所有取数都从 `TushareClient._safe_call` 出去——那是唯一收口点，所以本模块把
`_safe_call` 的全部职责（VIP 路由 / `MAX_RETRIES` 重试 / 连接错误重建客户端 /
永久错误不重试 / 限流节奏）**搬过来**，并追加仓的读写。`_safe_call` 只剩一行转调。

三种模式（`AC-5` 的关键）：

===========  ====================  =========================
模式          命中仓                未命中
===========  ====================  =========================
`online`     直接返回（记命中）     联网拉 → 写仓 → 返回
`refresh`    忽略，重拉并覆盖写      同上（`--force`）
`offline`    直接返回              抛 `DataMissing`，**不联网**
===========  ====================  =========================

散在各处的 `self.pro.*` 只要漏一处，离线重建就会**静默**缺数据，所以
`tests/test_data_store.py` 用 AST 断言 `self.pro.` 只出现在 `_new_pro_api` 与
`TushareClient` 的若干处，而不是靠自觉。
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from .dataframe_codec import decode_frame
from .gaps import DONE_KINDS, classify_result
from .registry import UnknownDataset, period_of
from .security import redact, token_fingerprint
from .store import sanitize_params, semantic_params

MODE_ONLINE = "online"
MODE_REFRESH = "refresh"
MODE_OFFLINE = "offline"
MODES = (MODE_ONLINE, MODE_REFRESH, MODE_OFFLINE)


def _field_list(value) -> list[str]:
    """``"a,b,c"`` → ``["a","b","c"]``（空/缺省 → ``[]``）。"""

    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


DATE_COLUMNS = ("trade_date", "end_date", "ann_date", "cal_date")


def _date_column(frame):
    return next((name for name in DATE_COLUMNS if name in frame.columns), None)


def _filter_rows(frame, request: dict, record_params: dict):
    """按请求的语义入参过滤行；无法确认服务能力时返回 ``None``。

    记录自己的入参已经钉住了这个键（如拉取时就带了 `type="P"`）时无需按列过滤；
    否则必须在帧里找到同名列并确认该值存在——找不到就说明这条记录服务不了这次请求。
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


def _compose(frames: list):
    """多条记录合并成一帧：列多的优先，再按日期倒序去重（同一天只留一份）。"""

    import pandas as pd

    if len(frames) == 1:
        combined = frames[0]
    else:
        ordered = [frame for _, frame in sorted(
            ((len(frame.columns), frame) for frame in frames), key=lambda item: -item[0])]
        combined = pd.concat(ordered, ignore_index=True, sort=False)
    date_column = _date_column(combined)
    if date_column:
        combined = combined.sort_values(date_column, ascending=False, kind="stable")
        combined = combined.drop_duplicates(subset=[date_column], keep="first")
    return combined


class DataMissing(RuntimeError):
    """离线模式下仓内没有这条记录（**不是**「忘了拉」，所以拒绝联网）。"""

    code = "DATA_MISSING"

    def __init__(self, dataset: str, params: dict):
        self.dataset = dataset
        self.params = dict(params or {})
        self.message = f"仓内没有 {dataset} 的记录，离线模式不联网：{self.params}"
        super().__init__(self.message)


class DataUnavailable(RuntimeError):
    """重试耗尽后的取数失败。`cause` 保留最后一次的异常，供结果分类与摘要。"""

    code = "DATA_UNAVAILABLE"

    def __init__(self, dataset: str, effective_name: str, retries: int, cause):
        self.dataset = dataset
        self.effective_name = effective_name
        self.retries = retries
        self.cause = cause
        self.message = (f"Tushare API '{effective_name}' failed after {retries} retries: {cause}")
        super().__init__(self.message)


class DataAccess:
    """远程取的每一次调用都从这里过。"""

    def __init__(self, store=None, *, client=None, token: str = "", mode: str = MODE_ONLINE,
                 tier_label: str = "", quota_profile: str = "", clock=None,
                 batch_id: str | None = None, rate_limit_seconds: float = 0.5,
                 retry_delay: float | None = None):
        if mode not in MODES:
            raise ValueError(f"未知取数模式：{mode!r}（可用：{', '.join(MODES)}）")
        self.store = store
        self.client = client
        self.token = str(token or "")
        self.mode = mode
        self.tier_label = tier_label
        self.quota_profile = quota_profile
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.batch_id = batch_id
        self.rate_limit_seconds = rate_limit_seconds
        self.retry_delay = retry_delay
        # 计数与缺口：批次报告的「命中存档」与离线重建的缺口清单都从这里读。
        self.archived_hits = 0
        self.remote_calls = 0
        self.gaps: list[dict] = []
        # 未在注册表声明的接口（如 `--extra-fields` 临时指定的接口）：
        # 能用，但**不落仓**——不编造一个假的口径字段。拉取范围由「扫描 + 注册表门禁」守住。
        self.undeclared: set[str] = set()

    # ------------------------------------------------------------------ 主入口

    def call(self, dataset: str, **params):
        """取一个数据集；返回 DataFrame（可能为空）。"""

        params = sanitize_params(params)
        ticker = str(params.get("ts_code") or params.get("ticker") or "")
        try:
            period = period_of(dataset, params)
        except UnknownDataset:
            # 声明缺失：不读仓也不写仓，其余行为与已声明接口一致。
            self.undeclared.add(dataset)
            if self.mode == MODE_OFFLINE:
                raise DataMissing(dataset, params)
            return self._invoke(dataset, params)

        if self.mode != MODE_REFRESH and self.store is not None:
            explicit_period = str(params.get("period") or "")
            records = self.store.find_family(ticker, dataset, params=params,
                                             period=explicit_period or None)
            if records:
                frame = self._replay(records, params)
                if frame is not None:
                    self.archived_hits += 1
                    return frame

        if self.mode == MODE_OFFLINE:
            missing = DataMissing(dataset, params)
            self.gaps.append({"dataset": dataset, "period": period, "params": params})
            raise missing

        return self._fetch_and_store(dataset, params, ticker, period)

    @staticmethod
    def store_param_json(params: dict) -> str:
        import json

        return json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)

    # ------------------------------------------------------------------ 命中仓时的重放

    @staticmethod
    def _replay(records: list[dict], params: dict):
        """把仓里的若干条记录**组合**成这次请求要的 DataFrame；服务不了时返回 ``None``。

        四步：① 逐条解码并按请求的语义入参过滤行（如 `report_type="1"`）；
        ② 多条记录按日期列合并去重（拉取按期次各留一条，调用点要的是整段历史）；
        ③ 按请求投影字段（缺失的列说明接口本来就没给，与联网路径一致）；
        ④ 按请求裁剪时间窗口与行数。
        """

        request = semantic_params(params)
        frames = []
        for record in records:
            frame = decode_frame(record["columns_json"], record["rows_json"])
            if frame.empty:
                frames.append(frame)
                continue
            filtered = _filter_rows(frame, request, record.get("params") or {})
            if filtered is None:
                continue
            frames.append(filtered)
        if not frames:
            return None
        non_empty = [frame for frame in frames if not frame.empty]
        if not non_empty:
            # 「确实为空」也是信息（`REQ-009.4` 的 `AC-4.5`）：空结果直接重放。
            return frames[0]

        combined = _compose(non_empty)
        date_column = _date_column(combined)

        requested_period = str(params.get("period") or "")
        if requested_period and date_column:
            values = combined[date_column].astype(str)
            if requested_period in set(values):
                combined = combined[values == requested_period]

        requested_fields = _field_list(params.get("fields"))
        if requested_fields:
            available = [name for name in requested_fields if name in combined.columns]
            if not available:
                return None
            combined = combined[available]

        requested_start = str(params.get("start_date") or "")
        requested_end = str(params.get("end_date") or "")
        if requested_start or requested_end:
            starts = [str((record.get("params") or {}).get("start_date") or "")
                      for record in records]
            known = [value for value in starts if value]
            if requested_start and known and min(known) > requested_start:
                # 仓里的起点更晚 = 历史更短：覆盖不了这次请求。
                return None
            if date_column:
                values = combined[date_column].astype(str)
                if requested_start:
                    combined = combined[values >= requested_start]
                if requested_end:
                    values = combined[date_column].astype(str)
                    combined = combined[values <= requested_end]

        limit = params.get("limit")
        if limit is not None:
            try:
                combined = combined.head(int(limit))
            except (TypeError, ValueError):
                pass
        return combined.reset_index(drop=True)

    # ------------------------------------------------------------------ 取数与落盘

    def _fetch_and_store(self, dataset, params, ticker, period):
        try:
            frame = self._invoke(dataset, params)
        except DataUnavailable as exc:
            result, excerpt = classify_result(error=exc.cause)
            self._persist(ticker, dataset, period, params, None, result, excerpt)
            raise
        result, _ = classify_result(data=frame)
        self._persist(ticker, dataset, period, params, frame, result, None)
        return frame

    def _persist(self, ticker, dataset, period, params, frame, result, error_excerpt):
        if self.store is None:
            return
        self.store.write_frame(
            ticker=ticker, dataset=dataset, period=period, params=params, frame=frame,
            result=result,
            error_excerpt=redact(error_excerpt or "", (self.token,)) or None,
            token_fingerprint=token_fingerprint(self.token),
            tier_label=self.tier_label, quota_profile=self.quota_profile,
            batch_id=self.batch_id, fetched_at=self.clock().astimezone(timezone.utc).isoformat(),
        )

    def _invoke(self, dataset, params):
        """真的联网调用——重试、限流、VIP 路由、连接重建全部在这里。"""

        client = self.client
        if client is None:
            raise DataUnavailable(dataset, dataset, 0, RuntimeError("在线取数需要 Tushare 客户端"))
        effective_name = dataset
        if getattr(client, "_vip_mode", False):
            effective_name = self._vip_map().get(dataset, dataset)

        retries = int(getattr(client, "MAX_RETRIES", 5))
        retry_delay = self.retry_delay if self.retry_delay is not None else \
            float(getattr(client, "RETRY_DELAY", 2.0))
        last_err = None
        for attempt in range(1, retries + 1):
            self._pace()
            self.remote_calls += 1
            try:
                return getattr(client.pro, effective_name)(**params)
            except Exception as exc:  # noqa: BLE001（分类与重试规则见下）
                last_err = exc
                if self._is_permanent(exc):
                    # 权限类错误重试无意义（F3）：立即放弃，不占用 5 次重试。
                    print(f"{effective_name}: permanent error ({exc}); not retrying", file=sys.stderr)
                    break
                if attempt < retries:
                    if self._is_connection_error(exc):
                        print(f"[retry {attempt}/{retries}] {effective_name}: connection error, "
                              "re-creating API client...", file=sys.stderr)
                        client.pro = client._new_pro_api()
                        self._apply_broker(client)
                    else:
                        print(f"[retry {attempt}/{retries}] {effective_name}: {exc}", file=sys.stderr)
                    time.sleep(retry_delay * attempt)
        raise DataUnavailable(dataset, effective_name, retries, last_err)

    def _pace(self):
        if self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds)

    @staticmethod
    def _is_permanent(exc) -> bool:
        from tushare_modules.infrastructure import is_permanent_api_error

        return is_permanent_api_error(exc)

    @staticmethod
    def _is_connection_error(exc) -> bool:
        return isinstance(exc, (ConnectionError, OSError)) or \
            "RemoteDisconnected" in type(exc).__name__ or \
            "ConnectionAborted" in str(exc) or \
            "RemoteDisconnected" in str(exc)

    @staticmethod
    def _vip_map() -> dict:
        from tushare_modules import _VIP_MAP

        return _VIP_MAP

    @staticmethod
    def _apply_broker(client) -> None:
        """连接重建后把 broker 参数重新打到新的 pro 客户端上。

        优先交给客户端自己（`TushareClient._apply_broker_hacks`）：那里读的是
        `tushare_collector.get_api_url`，测试与该模块的既有打桩点才对得上。
        """

        hook = getattr(client, "_apply_broker_hacks", None)
        if callable(hook):
            hook()
            return
        from config import get_api_url

        api_url = get_api_url()
        if api_url:
            client.pro._DataApi__token = client.token
            client.pro._DataApi__http_url = api_url
