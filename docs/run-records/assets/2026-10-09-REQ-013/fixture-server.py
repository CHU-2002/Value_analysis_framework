import os,sys
from pathlib import Path
root=Path.cwd();sys.path.insert(0,str(root/'scripts'))
from webui.config import Config
from webui.__main__ import build_application
from webui.core.server import WebUIServer
fixture=root/os.environ.get('REQ013_GUI_ROOT','output/.live_req013_ui'); fixture.mkdir(parents=True,exist_ok=True)
company=fixture/'output/600887_伊利';company.mkdir(parents=True,exist_ok=True)
cli=fixture/'fake-cli'
cli.write_text('#!'+sys.executable+'\nfrom pathlib import Path\nimport json\nr=Path('+repr(str(company))+')/"runs"/"gui-run"\nr.mkdir(parents=True,exist_ok=True)\n(r/"run.json").write_text(json.dumps({"ticker":"600887.SH","run_id":"gui-run"}))\n(r/"qualitative_report.md").write_text("# GUI report")\n(r/"change_report_2026H1.md").write_text("# GUI changes")\nprint("GUI fake CLI completed",flush=True)\n');cli.chmod(0o755)
os.environ['AGENT_CLI']=str(cli)
config=Config(port=int(os.environ.get('REQ013_GUI_PORT','8874')),output_root=fixture/'output',cache_dir=fixture/'cache',archive_root=fixture/'archive',open_browser=False)
registry,_=build_application(config)
with WebUIServer(config,registry) as server:
 print(server.base_url,flush=True)
 try: import threading;threading.Event().wait()
 finally: registry.jobs.shutdown()
