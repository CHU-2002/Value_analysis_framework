"""Independent reproduction of a same-store, other-process batch pause reply."""
import sys, os, json
from pathlib import Path
sys.path[:0] = ['scripts', 'tests']
from test_console_data_page import make_app, call_route
from datalayer.store import DataStore
root = Path('output/.live_req016_acceptance/external-pause').resolve()
root.mkdir(parents=True, exist_ok=True)
config, registry = make_app(root)
store = DataStore(config.archive_root)
store.append_batch({'batch_id': 'external-owned', 'status': 'running', 'owner_pid': os.getpid(),
    'profile': 'frugal', 'targets': [{'ticker': '600887.SH', 'dataset': 'income', 'period': '20260630', 'params': {}}],
    'completed': {}, 'progress': {'completed': 0, 'total': 1}, 'usage': {}})
reply = call_route(registry, 'POST', '/api/v1/research-data/batches/external-owned/pause', {})
result = {'reply': reply, 'status_after': store.load_batch('external-owned')['status'],
          'active_after': list(registry.collection_runner.active)}
(root / 'observations.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False))
