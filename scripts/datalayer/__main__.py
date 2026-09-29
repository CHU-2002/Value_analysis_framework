"""`python -m scripts.datalayer` 的入口（与 `scripts/webui` 同一套习惯）。"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
