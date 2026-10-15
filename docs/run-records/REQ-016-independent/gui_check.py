import sys,json,subprocess,time,os,hashlib
from pathlib import Path
sys.path.insert(0,str(Path('scripts').resolve()))
from datalayer.store import DataStore
from datalayer.universe import Universe
from gui_research_walkthrough import ResearchWalkthrough,DialogDevTools
from gui_walkthrough import _free_port,_resolve_browser,_wait_for_target
import pandas as pd
root=Path('output/.live_req016_acceptance').resolve();out=root/'gui-after';out.mkdir(exist_ok=True)
assets=root/'companies';company=assets/'600887_伊利';company.mkdir(parents=True,exist_ok=True)
(company/'record.json').write_text(json.dumps({'subject':{'ticker':'600887.SH'}}));(company/'600887_价值分析报告.md').write_text('# 保留报告\n测试证据')
store=DataStore(root/'archive');u=Universe(store);u.add('600887.SH','伊利股份');u.add('000858.SZ','五粮液',enabled=False)
store.write_frame(ticker='600887.SH',dataset='income',period='20251231',params={},frame=pd.DataFrame([{'revenue':123}]),result='ok')
files={str(p):p.read_bytes() for p in company.rglob('*') if p.is_file()};records=store.records()
port=_free_port();env=dict(os.environ,WEBUI_OUTPUT_ROOT=str(assets),WEBUI_CACHE_DIR=str(root/'cache'),WEBUI_ARCHIVE_ROOT=str(root/'archive'))
server=subprocess.Popen([sys.executable,'-m','scripts.webui','--port',str(port),'--no-browser'],env=env,stdout=open(root/'server.log','w'),stderr=subprocess.STDOUT)
bp=_free_port();browser=subprocess.Popen([_resolve_browser(None),'--headless=new','--disable-gpu','--no-first-run',f'--remote-debugging-port={bp}',f'--user-data-dir={out/"profile"}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
checks=None
try:
 target=_wait_for_target(bp);cdp=DialogDevTools(target['webSocketDebuggerUrl']);cdp.send('Page.enable');w=ResearchWalkthrough(cdp,f'http://127.0.0.1:{port}',out)
 time.sleep(1);w.goto('data');w.screenshot('initial');w.record('AC1 scope options',cdp.evaluate('[...document.querySelectorAll("select")].map(x=>[x.name,[...x.options].map(o=>o.textContent)])'))
 def operate(op,name='',tier=''):
  card=f'[data-action-id="universe.{op}"]'
  w.goto('data',extra=f'&focus_action=universe.{op}')
  if name:
   selector=card+' [name="name"]';cdp.click_selector(selector);cdp.send('Input.insertText',text=name)
  if tier:w.choose(card+' [name="tier"]',tier)
  cdp.click_selector(card+' [data-action-run]')
  if op=='remove':cdp.click_selector('[data-confirm-accept]')
  for i in range(50):
   time.sleep(.2)
   if '状态：finished' in w.text(card):break
  w.record(op+' finished',w.text(card),'状态：finished' in w.text(card));w.screenshot(op)
 operate('edit','验收伊利','frugal');cdp.reload(settle=1)
 w.record('edit persists GUI',w.text(),'验收伊利' in w.text() and '基础必需数据' in w.text())
 operate('disable');w.goto('charts');state=w.text('#company-watchlist-status');w.record('disabled company page state',state,'已停用' in state and '基础必需数据' in state);w.screenshot('disabled-company-page')
 w.record('disabled remains list',u.entries(),len(u.entries())==2 and not u.get('600887.SH')['enabled'])
 operate('enable');w.goto('charts');state=w.text('#company-watchlist-status');w.record('enabled company state',state,'已启用' in state and '验收伊利' in cdp.evaluate('document.body.innerText'))
 operate('remove');w.record('remove only configuration',u.entries(),u.get('600887.SH') is None)
 w.record('raw and report bytes preserved',len(store.records()),store.records()==records and all(Path(p).read_bytes()==b for p,b in files.items()))
 checks=w.observations
except Exception as e:
 checks=checks or {'checks':[],'failures':[]};checks['failures'].append(str(e))
finally:
 browser.terminate();server.terminate();browser.wait();server.wait()
 (out/'observations.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2));print(json.dumps(checks,ensure_ascii=False,indent=2))
 raise SystemExit(bool(checks['failures']))
