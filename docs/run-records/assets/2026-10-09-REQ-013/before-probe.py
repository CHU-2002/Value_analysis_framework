import sys, json, subprocess, time
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
from gui_walkthrough import DevTools, _resolve_browser, _free_port, _wait_for_target
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
port = _free_port()
p = subprocess.Popen([_resolve_browser(None), '--headless=new', '--disable-gpu', '--no-first-run', f'--user-data-dir={out}/profile', f'--remote-debugging-port={port}', '--window-size=1440,1000', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    target = _wait_for_target(port); c = DevTools(target['webSocketDebuggerUrl'])
    c.send('Page.enable'); c.send('Runtime.enable')
    c.send('Page.navigate', url='http://127.0.0.1:8873/#agent')
    time.sleep(2)
    c.click_selector('[data-command="agent_update_analysis"]')
    c.type_into('[data-param="ticker"]', '600887.SH'); time.sleep(1)
    state = c.evaluate("(() => ({text: document.querySelector('.command-form').innerText, disabled: document.querySelector('.command-form button[type=submit]').disabled}))()")
    failures = []
    if '/update-analysis 600887.SH' not in state['text']: failures.append('提交前缺完整的 agent 命令行')
    if '消耗模型额度' not in state['text']: failures.append('选定动作后缺额度告知')
    c.evaluate("(() => {const n=document.querySelector('[data-param=ticker]'); n.value='999999.SH'; n.dispatchEvent(new Event('input',{bubbles:true}));})()")
    time.sleep(1)
    if not c.evaluate("document.querySelector('.command-form button[type=submit]').disabled"): failures.append('目录不匹配时提交仍可用')
    c.screenshot(out / 'preflight.png')
    result = {'failures': failures, 'state': state}; (out / 'observations.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False)); sys.exit(bool(failures))
finally: p.terminate(); p.wait(timeout=10)
