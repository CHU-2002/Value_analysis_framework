"""Controlled long prefetch: real product/server/browser, mocked data provider only."""
import sys,json,threading,time,argparse
from pathlib import Path
from types import SimpleNamespace
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--root',required=True);p.add_argument('--port',type=int,required=True);p.add_argument('--dataset',choices=['income','daily'],default='income');a=p.parse_args()
source=Path(a.source).resolve();root=Path(a.root).resolve();root.mkdir(parents=True,exist_ok=True);sys.path.insert(0,str(source/'scripts'))
from webui.config import Config
from webui.__main__ import build_application
from webui.core.server import WebUIServer
from datalayer.store import DataStore
from datalayer.pull import PullBatch
from datalayer.market_bulk import prefetch
from tushare_collector import TushareClient
import pandas as pd
config=Config(output_root=root/'companies',archive_root=root/'archive',cache_dir=root/'cache',port=a.port,open_browser=False)
registry,_=build_application(config)
targets=[{'ticker':f'60000{i}.SH','dataset':'income','period':'20260630','params':{'ts_code':f'60000{i}.SH','period':'20260630','report_type':'1','fields':'ts_code,end_date,report_type,revenue'}} for i in range(6)]
if a.dataset == 'daily':
 targets=[{'ticker':f'60000{i}.SH','dataset':'daily','period':'latest','params':{'ts_code':f'60000{i}.SH','start_date':'20260101','end_date':'20260101','fields':'ts_code,trade_date,close'}} for i in range(6)]
store=DataStore(config.archive_root)
store.append_batch({'batch_id':'controlled-bulk','status':'pending','profile':'frugal','targets':targets,'completed':{},'progress':{'inputs':{'scope':'all_a'},'directory':{'snapshot_id':'controlled-only','fetched_at':'2026-10-13T00:00:00Z'}}})
def worker():
 own=DataStore(config.archive_root);client=TushareClient('mock-token',store=own,rate_limit_seconds=0,retry_delay=0)
 def income_vip(**params):
  (root/'request.json').write_text(json.dumps({'dataset':'income_vip' if a.dataset=='income' else 'daily',**params}))
  if params['offset']==0:
   time.sleep(90)
   return pd.DataFrame([{'ts_code':t['ticker'],'end_date':'20260630','report_type':'1','revenue':1} for t in targets]) if a.dataset=='income' else pd.DataFrame([{'ts_code':t['ticker'],'trade_date':params['trade_date'],'close':1} for t in targets])
  return pd.DataFrame()
 client.pro=SimpleNamespace(income_vip=income_vip,daily=income_vip)
 def bulk(current,access,should_pause):return prefetch(own,targets,access,whole_market=True,max_requests=6,should_pause=should_pause)
 try:PullBatch(own,targets,'frugal',client._access,token='mock-token',pre_collect=bulk).run(batch_id='controlled-bulk',confirm=True)
 except KeyboardInterrupt:pass
threading.Thread(target=worker,daemon=True).start()
WebUIServer(config,registry).serve_forever()
