import sys,json,subprocess,time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'scripts'))
from gui_walkthrough import DevTools,_resolve_browser,_free_port,_wait_for_target
out=Path('output/.live_req013_ui_final_evidence');port=_free_port()
p=subprocess.Popen([_resolve_browser(None),'--headless=new','--disable-gpu',f'--user-data-dir={out}/history-profile',f'--remote-debugging-port={port}','--window-size=1440,1000','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
 c=DevTools(_wait_for_target(port)['webSocketDebuggerUrl']);c.send('Page.enable');c.send('Runtime.enable');c.send('Page.navigate',url='http://127.0.0.1:8875/#commands');time.sleep(2)
 n=c.evaluate("document.querySelectorAll('.job-artifacts a').length")
 c.send('Page.reload');time.sleep(2)
 after=c.evaluate("document.querySelectorAll('.job-artifacts a').length")
 result={'before_reload':n,'after_reload':after,'failures':[] if n>=3 and after>=3 else ['history artifact links missing'],'exit_code':int(n<3 or after<3)}
 c.screenshot(out/'05-history-after-reload.png');(out/'history-observations.json').write_text(json.dumps(result,indent=2));print(json.dumps(result));sys.exit(result['exit_code'])
finally:p.terminate();p.wait(timeout=10)
