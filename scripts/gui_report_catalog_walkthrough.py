#!/usr/bin/env python3
"""REQ-016 报告页定向实跑：可用于修复前后同一动作并留下截图/观察。"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from urllib.parse import urlencode

from gui_walkthrough import DevTools, _free_port, _resolve_browser, _wait_for_target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8786")
    parser.add_argument("--company", default="600887_伊利")
    parser.add_argument("--page", default="report")
    parser.add_argument("--artifact", help="可选：固定报告版本 id")
    parser.add_argument("--out", required=True)
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--forbid", action="append", default=[])
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    process = subprocess.Popen([
        _resolve_browser(None), "--headless=new", "--disable-gpu", "--no-first-run",
        f"--user-data-dir={out}/browser-profile", f"--remote-debugging-port={port}",
        "--remote-allow-origins=*", "--window-size=1440,1000", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        target = _wait_for_target(port)
        if target is None:
            raise RuntimeError("浏览器未启动，无法生成实跑证据")
        cdp = DevTools(target["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        query = {"company": args.company}
        if args.artifact:
            query["id"] = args.artifact
        url = f"{args.base.rstrip('/')}/#{args.page}?{urlencode(query)}"
        cdp.goto(url, settle=3)
        text = cdp.evaluate("document.querySelector('#panels').innerText")
        failures = ([f"未找到：{item}" for item in args.require if item not in text]
                    + [f"不应出现：{item}" for item in args.forbid if item in text])
        cdp.screenshot(out / "page.png")
        observation = {"url": url, "text": text, "failures": failures,
                       "require": args.require, "forbid": args.forbid}
        (out / "observations.json").write_text(
            json.dumps(observation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"failures": failures}, ensure_ascii=False))
        return int(bool(failures))
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
