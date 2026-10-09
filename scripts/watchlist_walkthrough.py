#!/usr/bin/env python3
"""REQ-014 真实浏览器走查；服务必须指向沙箱仓，产物目录只读。"""
from __future__ import annotations
import argparse
import json
import subprocess
import time
from pathlib import Path

from gui_walkthrough import DevTools, _free_port, _resolve_browser, _wait_for_target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--probe-only", action="store_true", help="只核对入口，用于实现前后对照")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    browser = _resolve_browser(None)
    process = subprocess.Popen([browser, "--headless=new", "--disable-gpu", "--no-first-run",
                                "--no-default-browser-check", "--disable-crash-reporter",
                                f"--user-data-dir={out / 'browser-profile'}",
                                f"--remote-debugging-port={port}", "--remote-allow-origins=*",
                                "--window-size=1440,1100", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures, observations = [], []
    def check(ok, message):
        observations.append({"passed": bool(ok), "observation": message})
        if not ok:
            failures.append(message)
    try:
        target = _wait_for_target(port)
        if not target:
            raise RuntimeError("浏览器未启动")
        cdp = DevTools(target["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.goto(args.base + "/#data")
        def text(selector):
            return cdp.evaluate(f"document.querySelector({json.dumps(selector)})?.textContent || ''")
        def wait_for(script, timeout=15):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if cdp.evaluate(script):
                    return True
                time.sleep(.1)
            return False
        def shot(name):
            cdp.screenshot(out / name)
        def card(operation):
            return f'[data-action-id="universe.{operation}"]'
        def submit(operation):
            cdp.click_selector(card(operation) + " [data-action-run]")
            if operation == "remove":
                cdp.click_selector("[data-confirm-accept]")
            return wait_for(f"!!document.querySelector({json.dumps(card(operation) + ' .watchlist-summary')}) && !document.querySelector({json.dumps(card(operation) + ' [data-action-run]')})?.disabled")
        def fill(identifier):
            for name, value in (("ticker", identifier), ("name", "工商银行")):
                selector = card("add") + f' [data-action-input="{name}"]'
                cdp.evaluate(f"document.querySelector({json.dumps(selector)}).value = ''")
                cdp.type_into(selector, value)
        def clear_result(operation):
            cdp.evaluate(f"document.querySelector({json.dumps(card(operation) + ' .action-result')}).replaceChildren()")
        def copy_scan():
            copies = cdp.evaluate("[...document.querySelectorAll('[data-action-id^=\"universe.\"]')].map(n=>n.textContent)")
            check(all(not any(s in t for s in ('--', '/api/', 'python ', 'make ', 'output/', 'record.json')) for t in copies),
                  "清单动作渲染后的文案不含命令、开关、路径或接口路径")
        guide = text('[data-panel-id="data.universe"]')
        check("添加公司" in guide and "从既有产物导入" in guide, "空清单引导指向添加与导入入口")
        for operation in ("add", "import", "remove"):
            check(cdp.evaluate(f"!!document.querySelector({json.dumps(card(operation) + ' [data-action-run')})") ,
                  f"同一数据页存在 {operation} 的可点动作元素")
        shot("01-empty-entries.png")
        if not args.probe_only and not failures:
            for identifier in ("601398.SH", "601398", "601398_工商银行"):
                fill(identifier)
                check(submit("add"), f"通过真实输入与点击添加 {identifier}")
                check("601398" in text('[data-panel-id="data.universe"]'), "添加成功同页刷新清单")
                check(cdp.evaluate("!![...document.querySelectorAll('#company-select option')].find(n=>n.value==='601398.SH')"),
                      "添加成功同页刷新公司选择器")
                clear_result("add")
            shot("02-added.png")
            cdp.goto(args.base + "/#home", settle=1)
            check("601398" in text('[data-panel-id="home.universe"]'), "工作台刷新后显示新公司")
            cdp.goto(args.base + "/#companies", settle=1)
            check("601398" in text('[data-panel-id="companies.list"]'), "公司列表显示尚未有产物的新公司")
            cdp.goto(args.base + "/#data", settle=1)
            check(submit("import"), "真实点击从既有产物导入并显示结果")
            first = text(card("import") + " .action-result")
            check("导入 1 家" in first and "跳过" in first, f"真实产物首轮结果：{first}")
            check("600887" in text('[data-panel-id="data.universe"]'), "导入后清单刷新出既有公司")
            shot("03-imported.png")
            clear_result("import")
            check(submit("import"), "重复点击导入并显示结果")
            second = text(card("import") + " .action-result")
            check("导入 0 家" in second and "已在自选股清单里" in second, f"重复导入结果：{second}")
            copy_scan()
            selector = card("remove") + ' [data-action-input="ticker"]'
            cdp.evaluate(f"document.querySelector({json.dumps(selector)}).value='600887.SH'")
            cdp.click_selector(card("remove") + " [data-action-run]")
            check(bool(text("[data-confirm]")), "移除前弹窗说明所有产物保留")
            cdp.click_selector("[data-confirm-cancel]")
            check("600887" in text('[data-panel-id="data.universe"]'), "取消移除不改变清单")
            check(submit("remove"), "确认移除已导入的公司")
            check("600887" not in text('[data-panel-id="data.universe"]'), "移除后同页刷新清单")
            check("产物均已保留" in text(card("remove")), "移除结果明确说明保留产物")
            shot("04-removed.png")
    except Exception as exc:
        failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    result = {"base": args.base, "browser": browser, "observations": observations, "failures": failures}
    (out / "observations.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "observations.md").write_text("# REQ-014 浏览器实跑\n\n" + "\n".join(
        f"- {'通过' if item['passed'] else '失败'}：{item['observation']}" for item in observations
    ) + "\n\nfailures: " + json.dumps(failures, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
