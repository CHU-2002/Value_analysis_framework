"""Two real processes exercise shared pause and restart; external provider is mocked."""
import sys, os, json, subprocess, time
from pathlib import Path
from types import SimpleNamespace
sys.path[:0] = ['scripts', 'tests']
from datalayer.store import DataStore
from test_data_pull import _sample_targets, _run, _FakePro
from webui.plugins.research_data import CollectionRunner
root = Path(os.environ.get('REQ016_IPC_ROOT', 'output/.live_req016_acceptance/pause-after')).resolve()
root.mkdir(parents=True, exist_ok=True)
store = DataStore(root / 'archive')
targets = _sample_targets()
if len(sys.argv) > 1:
    calls = []
    def provider(name):
        calls.append(name)
        if sys.argv[1] == 'first':
            (root / 'worker-ready').write_text(str(os.getpid()))
            time.sleep(2)
        return getattr(_FakePro(), name)(**next(t for t in targets if t['dataset'] == name)['params'])
    try:
        result = _run(store, targets, _FakePro(provider), 'INDEPENDENT-IPC')
    except KeyboardInterrupt:
        result = store.load_batch('INDEPENDENT-IPC')
    (root / f'{sys.argv[1]}.json').write_text(json.dumps({'calls': calls, 'status': result['status'], 'completed': len(result['completed']), 'progress': result['progress']}))
    sys.exit(0)
if store.load_batch('INDEPENDENT-IPC'):
    raise RuntimeError('Use a fresh evidence directory to avoid overwriting history')
meta = {'inputs': {'scope': 'all_a', 'snapshot_id': 'unchanged'}, 'directory': {'snapshot_id': 'unchanged'}}
store.append_batch({'batch_id': 'INDEPENDENT-IPC', 'status': 'pending', 'profile': 'frugal', 'targets': targets, 'completed': {}, 'progress': meta})
worker = subprocess.Popen([sys.executable, __file__, 'first'])
for _ in range(100):
    if (root / 'worker-ready').exists(): break
    time.sleep(.1)
else: raise RuntimeError('worker did not start')
runner = CollectionRunner(SimpleNamespace(config=SimpleNamespace(archive_root=store.root)))
reply = runner.pause('INDEPENDENT-IPC')
worker.wait(timeout=20)
paused = store.load_batch('INDEPENDENT-IPC')
first = json.loads((root / 'first.json').read_text())
resume = subprocess.run([sys.executable, __file__, 'resume'], check=True)
second = json.loads((root / 'resume.json').read_text())
checks = {'external_owner': int((root / 'worker-ready').read_text()) != os.getpid(), 'no_active_gui_thread': runner.active == {},
          'paused_after_first': first['status'] == 'paused' and first['completed'] == 1,
          'resume_only_remaining': second['status'] == 'done' and len(second['calls']) == len(targets)-1,
          'snapshot_preserved': second['progress']['directory'] == meta['directory'] and second['progress']['inputs'] == meta['inputs']}
result = {'checks': checks, 'reply': reply, 'first': first, 'resume': second, 'failures': [k for k,v in checks.items() if not v]}
(root / 'observations.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False));sys.exit(bool(result['failures']))
