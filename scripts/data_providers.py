"""Select a market data client without coupling analysis to its API token."""

from __future__ import annotations

import os

from config import get_token, validate_stock_code


PROVIDERS = ("tushare", "moomoo")


def create_data_client(provider: str, stock_code: str, *, token: str | None = None):
    code = validate_stock_code(stock_code)
    if provider == "moomoo":
        if not code.endswith(".US"):
            raise ValueError("moomoo 数据源目前只支持美股")
        from moomoo_collector import MoomooClient
        return MoomooClient()
    if provider == "tushare":
        from tushare_collector import TushareClient
        return TushareClient(token if token is not None else get_token())
    raise ValueError(f"不支持的数据源: {provider}")


def configured_provider(explicit: str | None = None) -> str:
    provider = (explicit or os.environ.get("DATA_PROVIDER", "tushare")).lower()
    if provider not in PROVIDERS:
        raise ValueError(f"DATA_PROVIDER 只支持 {', '.join(PROVIDERS)}")
    return provider


def close_data_client(client) -> None:
    close = getattr(client, "close", None)
    if close is not None:
        close()
