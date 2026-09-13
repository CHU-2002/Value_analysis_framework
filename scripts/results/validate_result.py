#!/usr/bin/env python3
"""CLI validator for LLM-produced investment result JSON."""

from __future__ import annotations

import argparse
import sys

from .schema import load_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate investment result JSON")
    parser.add_argument("path")
    parser.add_argument("--allow-legacy", action="store_true", help="allow missing evidence references")
    args = parser.parse_args()
    try:
        load_result(args.path, strict=not args.allow_legacy)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Valid result: {args.path}")


if __name__ == "__main__":
    main()
