"""数据集注册表：每个远程接口的 shape / 期次语义 / 累计语义 / 档位归属。

为什么要有这份**手写**声明：期次与累计口径是**人工判断**出来的业务语义
（Tushare 不同接口对「累计」的定义并不一致），不能从接口名推出来。凡是扫描到的接口
都必须在这里显式声明，漏声明由 `tests/test_data_store.py` 直接判红
（`DATA_LAYER_PLAN` §4.4.2 的「人工判断 + 测试锁定」）。

这里**不含任何期次解析**：`20260630 ↔ 2026H1` 的换算一律走
`scripts/periods.py`（`ARCHITECTURE.md` 明写它是期次标识的唯一权威）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from periods import end_date_to_period_type  # noqa: E402  （scripts/ 由 datalayer/__init__ 放进 sys.path）

# shape：一条记录长什么样。
PERIOD_REPORT = "period_report"   # 一次调用返回一张按报告期的表（利润表/资产负债表/…）
TIMESERIES = "timeseries"         # 一次调用返回一段按交易日/事件的序列
SNAPSHOT = "snapshot"             # 一次调用返回一份当期快照

# 档位：`frugal` = 低配额必需子集；`bulk` = 扫描得到的全集（`DATA_LAYER_PLAN` §5.2）。
FRUGAL = "frugal"
BULK = "bulk"


@dataclass(frozen=True)
class DatasetSpec:
    """一个远程接口的语义声明。"""

    name: str
    shape: str
    cumulative: bool
    label: str = ""
    # 是否按「期次范围」逐期枚举；False 表示一次调用取回整段历史（period 记为 latest）。
    enumerate_periods: bool = False
    tiers: tuple[str, ...] = field(default=(BULK,))
    # 会改变内容的**语义**入参取值（如利润表的 report_type=1 合并 / 6 母公司）。
    # 拉取要按这些取值各拉一条，否则离线重建会有小节「数据缺失」而看不出原因。
    variants: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # 时间窗口型接口的窗口大小（`start_date`/`end_date` 由调用点按「距今多久」算出来，
    # 静态扫不到具体日期）。声明的是**代码需要的最大窗口**：仓里的窗口不小于调用点即可
    # 覆盖调用点的请求，窗口更窄时读取路径判为未命中（缺口可见，不静默糊弄）。
    window: tuple[tuple[str, int], ...] = ()


def _spec(name, shape, cumulative, label, *, enumerate_periods=False, tiers=(BULK,),
          variants=(), window=()):
    return DatasetSpec(name=name, shape=shape, cumulative=cumulative, label=label,
                       enumerate_periods=enumerate_periods, tiers=tuple(tiers),
                       variants=tuple(variants), window=tuple(window))


_FRUGAL = (FRUGAL, BULK)
_BULK_ONLY = (BULK,)

_SPECS = (
    # --- 快照型：一次调用一份当期数据 ---
    _spec("stock_basic", SNAPSHOT, False, "A 股基本信息", tiers=_FRUGAL),
    _spec("hk_basic", SNAPSHOT, False, "港股基本信息", tiers=_BULK_ONLY),
    _spec("us_basic", SNAPSHOT, False, "美股基本信息", tiers=_BULK_ONLY),
    _spec("yc_cb", SNAPSHOT, False, "可转债曲线（时点）",
          window=(("months", 1),)),
    # --- 时间序列型：一次调用一段序列，period 记为 latest ---
    _spec("daily", TIMESERIES, False, "日线行情", tiers=_FRUGAL, window=(("years", 1),)),
    _spec("daily_basic", TIMESERIES, False, "每日指标"),
    _spec("weekly", TIMESERIES, False, "周线行情", window=(("years", 10),)),
    _spec("hk_daily", TIMESERIES, False, "港股日线行情", window=(("years", 1),)),
    _spec("us_daily", TIMESERIES, False, "美股全市场日线快照"),
    _spec("dividend", TIMESERIES, False, "分红送股（按事件序列）"),
    _spec("top10_holders", TIMESERIES, False, "前十大股东（按报告期序列）"),
    _spec("pledge_stat", TIMESERIES, False, "股权质押统计（按周序列）"),
    _spec("repurchase", TIMESERIES, False, "股票回购（按公告序列）"),
    # --- 报告期型：可（且默认）按期次逐期拉取 ---
    _spec("income", PERIOD_REPORT, True, "合并利润表", enumerate_periods=True, tiers=_FRUGAL,
          variants=(("report_type", ("1", "6")),)),   # 1 合并报表 / 6 母公司报表
    _spec("balancesheet", PERIOD_REPORT, False, "合并资产负债表", enumerate_periods=True,
          tiers=_FRUGAL, variants=(("report_type", ("1", "6")),)),
    _spec("cashflow", PERIOD_REPORT, True, "现金流量表", enumerate_periods=True, tiers=_FRUGAL,
          variants=(("report_type", ("1",)),)),
    _spec("fina_indicator", PERIOD_REPORT, True, "财务指标", enumerate_periods=True, tiers=_FRUGAL),
    _spec("fina_audit", PERIOD_REPORT, False, "审计意见", enumerate_periods=True),
    _spec("fina_mainbz", PERIOD_REPORT, True, "主营业务构成", enumerate_periods=True,
          variants=(("type", ("P",)),)),               # P 按产品 / B 按地区
    _spec("hk_income", PERIOD_REPORT, True, "港股利润表", enumerate_periods=True),
    _spec("hk_balancesheet", PERIOD_REPORT, False, "港股资产负债表", enumerate_periods=True),
    _spec("hk_cashflow", PERIOD_REPORT, True, "港股现金流量表", enumerate_periods=True),
    _spec("hk_fina_indicator", PERIOD_REPORT, True, "港股财务指标", enumerate_periods=True),
    _spec("us_income", PERIOD_REPORT, True, "美股利润表", enumerate_periods=True),
    _spec("us_balancesheet", PERIOD_REPORT, False, "美股资产负债表", enumerate_periods=True),
    _spec("us_cashflow", PERIOD_REPORT, True, "美股现金流量表", enumerate_periods=True),
    _spec("us_fina_indicator", PERIOD_REPORT, True, "美股财务指标", enumerate_periods=True),
)

DATASETS: dict[str, DatasetSpec] = {spec.name: spec for spec in _SPECS}

# 档位 → 接口清单。`bulk` 是「扫描到的全集」这一语义的落点，
# 由 `tests/test_data_pull.py` 断言它确实覆盖 `endpoints.scan_safe_calls()`。
PROFILES: dict[str, tuple[str, ...]] = {
    tier: tuple(spec.name for spec in _SPECS if tier in spec.tiers)
    for tier in (FRUGAL, BULK)
}


class UnknownDataset(KeyError):
    """接口没有在注册表里声明——新增取数代码必须显式声明口径。"""


def spec_for(dataset: str) -> DatasetSpec:
    try:
        return DATASETS[dataset]
    except KeyError as exc:
        raise UnknownDataset(
            f"数据集 {dataset!r} 未在 datalayer.registry 声明（shape / 期次语义 / 累计语义）"
        ) from exc


def period_of(dataset: str, params: dict) -> str:
    """目标/调用参数 → 仓里的 ``period`` 字段。

    逐期枚举的报告期接口用参数里的 ``period``；其余一律 ``latest``
    （真·全量时间序列由接口自身的日期范围决定，范围本身记在 ``params_json`` 里）。
    """

    spec = spec_for(dataset)
    if spec.enumerate_periods:
        value = str(params.get("period", "") or "").strip()
        if value:
            return value
    return "latest"


def describe(dataset: str, period: str) -> tuple[str, str, bool]:
    """``(shape, period_type, cumulative)`` —— 记录的三个口径字段。

    ``period_type`` 由 ``scripts/periods.py`` 的换算推出（不自己判 ``endswith``）；
    时点型看 shape，报告期型看期次，其余是时间序列。
    """

    spec = spec_for(dataset)
    if spec.shape == PERIOD_REPORT:
        period_type = end_date_to_period_type(period)
        if period_type == "unknown":
            # 报告期型接口被以 latest 调用（一次取回整段历史）时不假装知道口径。
            period_type = "series"
    elif spec.shape == SNAPSHOT:
        period_type = "point"
    else:
        period_type = "series"
    return spec.shape, period_type, spec.cumulative


def profile_names() -> tuple[str, ...]:
    return tuple(PROFILES)


def profile_datasets(profile: str) -> tuple[str, ...]:
    try:
        return PROFILES[profile]
    except KeyError as exc:
        raise ValueError(f"未知配额档案：{profile}") from exc


def variant_combinations(dataset: str) -> list[dict]:
    """接口的语义变体组合（笛卡尔积）；没有声明变体时返回 ``[{}]``。"""

    spec = spec_for(dataset)
    combinations: list[dict] = [{}]
    for name, values in spec.variants:
        combinations = [{**combination, name: value}
                        for combination in combinations for value in values]
    return combinations


def window_params(dataset: str, today=None) -> dict:
    """时间窗口型接口的 ``start_date`` / ``end_date``（按 ``window`` 声明算出来）。"""

    spec = spec_for(dataset)
    if not spec.window:
        return {}
    from datetime import date

    from pandas import DateOffset, Timestamp

    end = Timestamp(today or date.today())
    offsets = {unit: value for unit, value in spec.window}
    start = end - DateOffset(**offsets)
    return {"start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}
