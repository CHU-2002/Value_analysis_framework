import sys,json,subprocess,time
from pathlib import Path
sys.path.insert(0,str(Path('scripts').resolve()))
from datalayer.store import DataStore
from datalayer.universe import Universe
from gui_research_walkthrough import DialogDevTools,ResearchWalkthrough
from gui_walkthrough import _free_port,_resolve_browser,_wait_for_target
root=Path('output/.live_req016_acceptance').resolve();s=DataStore(root/'archive');u=Universe(s);u.add('600887.SH','计划验收伊利',tier='frugal');out=root/'gui-plan';out.mkdir(exist_ok=True)
port=_free_port();b=subprocess.Popen([_resolve_browser(None),'--headless=new','--disable-gpu','--no-first-run',f'--remote-debugging-port={port}',f'--user-data-dir={out/"profile"}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
 c=DialogDevTools(_wait_for_target(port)['webSocketDebuggerUrl']);c.send('Page.enable');w=ResearchWalkthrough(c,'http://127.0.0.1:8787',out);plans=[]
 for company in ['600887.SH','000858.SZ']:
  w.goto('data',company);time.sleep(1);plans.append(c.evaluate('(async()=>{let r=await fetch("/api/v1/research-data/plan?scope=watchlist&periods=20251231&company='+company+'");return (await r.json()).data})()'))
 w.record('AC2 strict enabled target set / current company independence',[[r['ticker'] for r in p['companies']] for p in plans],all([r['ticker'] for r in p['companies']]==['600887.SH'] for p in plans) and plans[0]['digest']==plans[1]['digest'])
 text=w.text('.collection-workspace');w.record('AC4 plan labels/unknown reasons',text,all(x in text for x in ['目标公司 1 家','新增请求预估','将复用','未知：账号接口权限未验证','未知：数据源未提供此账号剩余配额','财报','行情与估值','期次','目录时点']))
 before=s.list_batches();w.button('确认采集（联网、消耗配额）','.collection-workspace');time.sleep(.5)
 w.record('AC5 native cancellation dialog shown',c.dialogs,bool(c.dialogs and '确认联网采集' in c.dialogs[-1]));w.record('AC5 canceled creates zero batches',s.list_batches(),before==s.list_batches()==[]);w.screenshot('plan-after-cancel')
 result=w.observations;(out/'observations.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'checks':len(result['checks']),'failures':result['failures']},ensure_ascii=False));raise SystemExit(bool(result['failures']))
finally:b.terminate();b.wait()
