"""Read-only browser evidence for bulk collection current company/group/date."""
import sys,json,subprocess,time,argparse
from pathlib import Path
sys.path.insert(0,str(Path('scripts').resolve()))
from gui_research_walkthrough import DialogDevTools,ResearchWalkthrough
from gui_walkthrough import _free_port,_resolve_browser,_wait_for_target
p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--out',required=True);p.add_argument('--expect-date');args=p.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
port=_free_port();b=subprocess.Popen([_resolve_browser(None),'--headless=new','--disable-gpu','--no-first-run',f'--remote-debugging-port={port}',f'--user-data-dir={out/"profile"}','--window-size=1440,1000','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
 c=DialogDevTools(_wait_for_target(port)['webSocketDebuggerUrl']);c.send('Page.enable');w=ResearchWalkthrough(c,args.base,out);w.goto('data')
 for _ in range(120):
  text=w.text('.collection-workspace');
  if '实际请求 ' in text:break
  time.sleep(.5)
 batch=c.evaluate('document.querySelector("[data-batch]")?.innerText || ""')
 current=[line for line in batch.splitlines() if '当前公司' in line or '当前业务组' in line or '当前日期' in line]
 w.record('bulk current company/group displayed truthfully',current,bool(current and '沪深北A股与境内上市CDR' in current[0] and '当前业务组' in current[0]))
 if args.expect_date:w.record('bulk date matches actual provider request',current,any(args.expect_date in line and '当前交易日期' in line for line in current))
 w.screenshot('bulk-current')
 (out/'observations.json').write_text(json.dumps(w.observations,ensure_ascii=False,indent=2));print(json.dumps(w.observations,ensure_ascii=False));raise SystemExit(bool(w.observations['failures']))
finally:b.terminate();b.wait()
