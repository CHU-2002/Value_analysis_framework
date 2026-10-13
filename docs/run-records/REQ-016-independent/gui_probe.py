import sys,json,time,subprocess,argparse
from pathlib import Path
sys.path.insert(0,str(Path('scripts').resolve()))
from gui_research_walkthrough import DialogDevTools,ResearchWalkthrough
from gui_walkthrough import _free_port,_resolve_browser,_wait_for_target
p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--out',required=True);args=p.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
port=_free_port();browser=subprocess.Popen([_resolve_browser(None),'--headless=new','--disable-gpu','--no-first-run',f'--remote-debugging-port={port}',f'--user-data-dir={out/"profile"}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
 cdp=DialogDevTools(_wait_for_target(port)['webSocketDebuggerUrl']);cdp.send('Page.enable');w=ResearchWalkthrough(cdp,args.base,out)
 for company in ['600887.SH','000858.SZ']:
  w.goto('data',company)
  options=cdp.evaluate('[...document.querySelectorAll(".collection-workspace select")][0] ? [...document.querySelectorAll(".collection-workspace select")][0].options.length : 0')
  labels=cdp.evaluate('[...document.querySelectorAll(".collection-workspace select")][0] ? [...document.querySelectorAll(".collection-workspace select")][0].textContent : ""')
  w.record(company+' scope exactly maintained/all-A',[options,labels],options==2 and '已维护公司' in labels and '全部A股' in labels)
  for op in ['enable','disable','edit']:
   found=cdp.evaluate(f'Boolean(document.querySelector(\'[data-action-id="universe.{op}"]\'))');w.record(company+' '+op+' GUI entry',found,found)
  w.screenshot(company.replace('.','-'))
 result=w.observations;(out/'observations.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'checks':len(result['checks']),'failures':result['failures']},ensure_ascii=False));raise SystemExit(bool(result['failures']))
finally:browser.terminate();browser.wait()
