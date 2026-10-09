"""Reproduce REQ-013's explicitly authorized real CLI run in a fresh sandbox.

Run from the repository root; --prepare copies existing company inputs and
workflow resources. It never edits the real company directory or global CLI
configuration. Raw logs remain under ignored output/; do not publish them.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--prepare', action='store_true')
parser.add_argument('--sandbox', default='output/.live_req013_reproduce')
parser.add_argument('--model', default='gpt-5.5')
parser.add_argument('--label', default='real-run')
args = parser.parse_args()
root = Path.cwd().resolve()
out = (root / args.sandbox).resolve()
if not out.is_relative_to(root / 'output'):
    parser.error('sandbox must be under repository output/')
live = out / 'workspace'
if args.prepare:
    if live.exists():
        parser.error('prepare requires a new sandbox; preserve prior evidence')
    live.mkdir(parents=True)
    for name in ('scripts', 'shared', 'strategies', 'docs', 'prompts',
                 '.claude/commands', '.opencode/commands'):
        shutil.copytree(root / name, live / name, symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(root / 'output/600887_伊利', live / 'output/600887_伊利')
    (live / '.venv').symlink_to(root / '.venv', target_is_directory=True)
if not live.is_dir():
    parser.error('prepare the sandbox first')
cli = shutil.which('codex')
if not cli:
    parser.error('install and log in to Codex first')
launcher = live / 'tools/codex'
launcher.parent.mkdir(exist_ok=True)
launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(cli)
                    + ' -c ' + shlex.quote('model=' + json.dumps(args.model))
                    + ' -c sandbox_workspace_write.network_access=true'
                    + ' -c ' + shlex.quote('shell_environment_policy.inherit="all"')
                    + ' "$@"\n')
launcher.chmod(0o755)
sys.path.insert(0, str(root / 'scripts'))
from config import _load_env_file
_load_env_file()
env = dict(os.environ)
env['PATH'] = str(root / '.venv/bin') + os.pathsep + env['PATH']
env['TURTLE_ARCHIVE_ROOT'] = str(live / 'archive')
env['PYTHONUNBUFFERED'] = '1'
command = [str(root / '.venv/bin/python'), 'scripts/agent_action.py',
           '--action', 'update-analysis', '--ticker', '600887.SH',
           '--timeout', '1800', '--cli', str(launcher)]
log_path = out / (args.label + '.log')
if log_path.exists():
    parser.error('choose a new label; preserve prior evidence')
started = time.time()
with log_path.open('w') as log:
    result = subprocess.run(command, cwd=live, env=env, stdout=log,
                            stderr=subprocess.STDOUT)
usage = []
for line in log_path.read_text().splitlines():
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if event.get('type') == 'turn.completed':
        usage.append(event.get('usage'))
summary = dict(command=command, model=args.model, archive=str(live / 'archive'),
               elapsed_seconds=round(time.time() - started, 1),
               exit_code=result.returncode, usage=usage)
(out / (args.label + '-summary.json')).write_text(
    json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False))
raise SystemExit(result.returncode)
