#!/usr/bin/env python3
"""REQ-013 browser preflight/confirmation/artifact walkthrough; no model call by default.

Run against a console configured with a fake CLI to exercise --submit safely.
Screenshots and observations are retained in --out. The same checks expose the
three missing behaviors in the #77 baseline before the fix.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from gui_walkthrough import DevTools, _free_port, _http_json, _resolve_browser, _wait_for_target


class DialogDevTools(DevTools):
    """Handle native confirmation while the real mouse click awaits its CDP reply."""

    answer = False
    dialogs = 0

    def send(self, method, **params):
        self._seq += 1
        expected = self._seq
        self._ws.send(json.dumps({'id': expected, 'method': method, 'params': params}))
        while True:
            message = json.loads(self._ws.recv())
            if message.get('method') == 'Page.javascriptDialogOpening':
                self.dialogs += 1
                self._seq += 1
                self._ws.send(json.dumps({'id': self._seq, 'method': 'Page.handleJavaScriptDialog',
                                         'params': {'accept': self.answer}}))
            if message.get('id') != expected:
                continue
            if 'error' in message:
                raise RuntimeError(str(message['error']))
            return message.get('result', {})


def wait_for(check, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--base', default='http://127.0.0.1:8765')
    parser.add_argument('--out', required=True)
    parser.add_argument('--ticker', default='600887.SH')
    parser.add_argument('--submit', action='store_true', help='explicitly execute the configured CLI')
    args = parser.parse_args(argv)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    browser = subprocess.Popen([
        _resolve_browser(None), '--headless=new', '--disable-gpu', '--no-first-run',
        f'--user-data-dir={out}/profile', f'--remote-debugging-port={port}',
        '--window-size=1440,1000', 'about:blank',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures, observations = [], []

    def check(ok, label):
        observations.append({'check': label, 'passed': bool(ok)})
        if not ok:
            failures.append(label)

    def jobs():
        return _http_json(args.base + '/api/v1/jobs')['data']['jobs']

    try:
        target = _wait_for_target(port)
        if not target:
            raise RuntimeError('browser did not start')
        c = DialogDevTools(target['webSocketDebuggerUrl'])
        c.send('Page.enable'); c.send('Runtime.enable')
        c.send('Page.navigate', url=args.base + '/#agent')
        wait_for(lambda: c.evaluate("Boolean(document.querySelector('[data-command=agent_update_analysis]'))"))
        c.click_selector('[data-command=agent_update_analysis]')
        c.type_into('[data-param=ticker]', args.ticker)
        wait_for(lambda: c.evaluate("!document.querySelector('.command-form button[type=submit]').disabled"))
        text = c.evaluate("document.querySelector('.command-form').innerText")
        preview = c.evaluate("document.querySelector('.command-argv').textContent")
        check(f'/update-analysis {args.ticker}' in preview, '提交前完整 agent 命令预览')
        check('消耗模型额度' in text, '选定动作后的额度告知')
        check('股票代码' in text and 'ticker *' not in text, '只填写人话标的字段')
        check(c.evaluate("Boolean(document.querySelector('.command-form details'))"), '技术细节折叠')
        c.screenshot(out / '01-valid-preview.png')
        c.evaluate("(() => {const n=document.querySelector('[data-param=ticker]');n.value='999999.SH';n.dispatchEvent(new Event('input',{bubbles:true}));})()")
        wait_for(lambda: c.evaluate("document.querySelector('.command-form').innerText.includes('公司目录')"))
        check(c.evaluate("document.querySelector('.command-form button[type=submit]').disabled"), '目录不匹配时禁用提交')
        c.screenshot(out / '02-unavailable-company.png')
        before = {j['id'] for j in jobs()}
        check(len(before) == len(jobs()), '预检没有创建任务')
        if args.submit:
            c.evaluate("(() => {const n=document.querySelector('[data-param=ticker]');n.value=" + json.dumps(args.ticker) + ";n.dispatchEvent(new Event('input',{bubbles:true}));})()")
            wait_for(lambda: c.evaluate("!document.querySelector('.command-form button[type=submit]').disabled"))
            c.click_selector('.command-form button[type=submit]')
            check(c.dialogs == 1 and {j['id'] for j in jobs()} == before, '拒绝确认不创建任务')
            c.answer = True
            c.click_selector('.command-form button[type=submit]')
            wait_for(lambda: any(j['id'] not in before and j['status'] == 'finished' for j in jobs()))
            new = [j for j in jobs() if j['id'] not in before]
            check(len(new) == 1 and new[0]['exit_code'] == 0, '确认后只创建一个成功任务')
            wait_for(lambda: c.evaluate("Boolean(document.querySelector('.job-artifacts a'))"))
            check(c.evaluate("document.querySelectorAll('.job-artifacts a').length") >= 3,
                  '报告、变化报告与迭代目录链接')
            c.screenshot(out / '03-finished-artifacts.png')
            c.click_selector('.job-artifacts a')
            check(wait_for(lambda: c.evaluate("location.hash.startsWith('#report') && Boolean(document.querySelector('.panel-markdown h1'))")),
                  '真实点击产物链接可阅读报告')
            c.screenshot(out / '04-open-report.png')
        result = {'failures': failures, 'observations': observations, 'exit_code': int(bool(failures))}
        (out / 'observations.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))
        return result['exit_code']
    finally:
        browser.terminate(); browser.wait(timeout=10)


if __name__ == '__main__':
    raise SystemExit(main())
