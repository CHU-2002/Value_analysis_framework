#!/usr/bin/env python3
"""CLI validator for LLM-produced investment result JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evidence import validate_result_evidence
from .schema import load_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate investment result JSON")
    parser.add_argument("path")
    parser.add_argument("--allow-legacy", action="store_true", help="allow missing evidence references")
    parser.add_argument("--evidence-index", help="verify exact quotes, locators and run identity against this index")
    args = parser.parse_args()
    try:
        result = load_result(args.path, strict=not args.allow_legacy)
        if args.evidence_index:
            index = json.loads(Path(args.evidence_index).read_text(encoding="utf-8"))
            errors = validate_result_evidence(result, index)
            if isinstance(index, dict):
                run = index.get("run")
                if not isinstance(run, dict) or run.get("run_id") != result["run"]["run_id"]:
                    errors.append("evidence index run_id does not match result")
                if index.get("subject") != result["subject"]:
                    errors.append("evidence index subject does not match result")
            if errors:
                raise ValueError("Invalid result evidence:\n- " + "\n- ".join(errors))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Valid result: {args.path}")


if __name__ == "__main__":
    main()
