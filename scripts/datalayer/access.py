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
from .store import rows_for_request, sanitize_params, semantic_params

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


# 「这条记录能不能服务这次请求」的唯一实现放在 store 里（`rows_for_request`），
# 因为 `DataStore.serves_target()` 与读取路径必须同源——两处各判一次就会像独立复核
# B3/B5 那样出现「同一个仓两个矛盾的结论」。
_filter_rows = rows_for_request


def _compose(frames: list):
    """多条记录合成一帧：**记录内部一行不删**，只在记录之间按期次择一。

    独立复核 B1 抓到的真实缺陷：早先的实现对单条记录也按日期列 `drop_duplicates`，
    把 `top10_holders` 的 1082 行（同一期次 10 行）压成 1 行、`fina_mainbz` 的多行压成
    「数据缺失」——一次响应内部的「同一个日期多行」是数据本身（十大股东、主营构成），
    不是重复。

    `frames` 是 ``(优先级, DataFrame)`` 列表，优先级小的先用：**明确期次的记录**优先于
    `period="latest"` 的整段历史记录（后者可能只带了某个期次的一行，不能拿它盖掉那一期），
    同组内列数多的优先。某个期次一旦由前面的记录提供，后面的记录就不再提供同一期次。
    """

    import pandas as pd

    if len(frames) == 1:
        return frames[0][1]
    ordered = [frame for _, frame in sorted(frames, key=lambda item: item[0])]
    date_column = _date_column(ordered[0])
    if date_column is None:
        return pd.concat(ordered, ignore_index=True, sort=False)
    kept: list = []
    covered: set = set()
    for frame in ordered:
        values = frame[date_column].astype(str)
        fresh = frame[~values.isin(covered)]
        if not fresh.empty:
            kept.append(fresh)
            covered.update(values.unique())
    combined = pd.concat(kept, ignore_index=True, sort=False)
    return combined.sort_values(date_column, ascending=False, kind="stable")


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
        empties = []
        for record in records:
            frame = decode_frame(record["columns_json"], record["rows_json"])
            if frame.empty:
                empties.append(frame)
                continue
            filtered = _filter_rows(frame, request, record.get("params") or {})
            if filtered is None:
                continue
            period = str((record.get("params") or {}).get("period") or record.get("period") or "")
            # 优先级：明确期次的记录（0）先于 latest 的整段历史记录（1）；同组列数多的先。
            priority = (1 if period in ("", "latest") else 0, -len(filtered.columns))
            frames.append((priority, filtered))
        if not frames:
            if empties:
                # 「确实为空」也是信息（`REQ-009.4` 的 `AC-4.5`）：空结果直接重放。
                return empties[0]
            return None

        combined = _compose(frames)
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
        # 档位标签是**人的记录**（账号档位），不是这次调用的产物：不带标签的刷新
        # （如临时 `--force`）不该把仓里已有的标签抹成空（独立复核 N10）。
        # 只在**同一个 token** 的记录上继承：换了账号却不重新填标签时留空是诚实的，
        # 把旧账号的标签挂到新账号买的数据上则不是（复核者建议的收口）。
        tier_label = self.tier_label
        if not tier_label:
            existing = self.store.find(ticker, dataset, period, params=params, include_rows=False)
            if existing and str(existing.get("token_fingerprint") or "") == \
                    token_fingerprint(self.token):
                tier_label = str(existing.get("tier_label") or "")
        self.store.write_frame(
            ticker=ticker, dataset=dataset, period=period, params=params, frame=frame,
            result=result,
            error_excerpt=redact(error_excerpt or "", (self.token,)) or None,
            token_fingerprint=token_fingerprint(self.token),
            tier_label=tier_label, quota_profile=self.quota_profile,
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
        """连接类错误才值得「重建客户端再试」。

        `PermissionError` 是 `OSError` 的子类，但它说的是**本机权限**（只读 HOME 等），
        重建远程客户端既解决不了问题、还会真的发出去一次请求——独立复核 N6 就是踩到
        这个（假客户端抛 PermissionError，结果重建了真实客户端并出站）。
        """

        if isinstance(exc, PermissionError):
            return False
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
