"""Wrong report period/variant and missing tickers must not complete targets."""
import sys,json,os
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
sys.path.insert(0,'scripts')
from datalayer.store import DataStore
from datalayer.market_bulk import prefetch
from datalayer.access import DataAccess
root=Path(os.environ.get('REQ016_BULK_NEGATIVE_ROOT','output/.live_req016_acceptance/bulk-negative-final'));root.mkdir(parents=True,exist_ok=True)
store=DataStore(root)
targets=[{'ticker':code,'dataset':'income','period':'20260630','params':{'ts_code':code,'period':'20260630','report_type':'1','fields':'ts_code,end_date,report_type,revenue'}} for code in ['600887.SH','600001.SH','600002.SH','600003.SH','600004.SH','600005.SH']]
calls=[]
def response(**params):
 calls.append(params)
 return pd.DataFrame([{'ts_code':'600887.SH','end_date':'20251231','report_type':'1','revenue':10},{'ts_code':'600887.SH','end_date':'20260630','report_type':'4','revenue':20}]) if params['offset']==0 else pd.DataFrame()
client=SimpleNamespace(_vip_mode=False,MAX_RETRIES=1,pro=SimpleNamespace(income_vip=response))
access=DataAccess(store,client=client,token='mock-token',rate_limit_seconds=0,batch_id='negative-test')
report=prefetch(store,targets,access,whole_market=True,max_requests=6)
result={'requests':len(calls),'target_records':sum(len(store.records(ticker=t['ticker'])) for t in targets),'fallback':report['fallback_targets'],'false_complete':any(store.serves_target(t) for t in targets),'source_checkpoint_records':len(store.records(ticker=''))}
assert result['target_records']==0 and result['fallback']==6 and not result['false_complete'],result
(root/'observations.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
