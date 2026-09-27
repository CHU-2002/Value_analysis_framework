"""Thin bridge to the collector's existing rate limited Tushare client."""

from __future__ import annotations

import sys
from pathlib import Path


class TushareAdapter:
    def __init__(self, token):
        if not token:
            raise ValueError("Tushare token is required")
        # The existing client owns endpoint routing, retries and rate limiting.
        scripts_dir = str(Path(__file__).resolve().parents[3])
        added = scripts_dir not in sys.path
        if added:
            sys.path.insert(0, scripts_dir)
        try:
            from tushare_collector import TushareClient
        finally:
            if added:
                sys.path.remove(scripts_dir)

        self.client = TushareClient(token)

    def fetch(self, target):
        return self.client._safe_call(target["dataset"], **target["params"])
