"""Local, reviewable CLI model catalog; availability is never account entitlement."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / 'config' / 'agent_models.json'


def catalog():
    payload = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    return payload['agents']


def validate_model(backend, model):
    if not model or model == 'default':
        return None
    agent = next((a for a in catalog() if a['id'] == backend), None)
    entry = next((m for m in agent['models'] if m['id'] == model), None) if agent else None
    if not entry or entry.get('compatibility') == 'unavailable':
        raise ValueError('非法或已知不兼容的 agent / 模型组合，请从模型目录重新选择。')
    return model


def default_model(backend):
    """Only an explicit local setting is evidence; implicit defaults remain unknown."""
    if backend == 'claude':
        value = os.environ.get('ANTHROPIC_MODEL')
        if value and re.fullmatch(r'[A-Za-z0-9_.:-]+', value):
            return {'model': value, 'source': 'ANTHROPIC_MODEL 环境配置（不等于账号授权）'}
        path = Path.home() / '.claude' / 'settings.json'
        try:
            value = json.loads(path.read_text()).get('model')
        except (OSError, ValueError, AttributeError):
            value = None
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]+', value):
            return {'model': value, 'source': 'Claude 本地 settings.json（未验证账号授权）'}
    else:
        path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
        try:
            # Only the top-level setting; do not read credentials or guess a profile model.
            top = path.read_text().split('\n[', 1)[0]
            match = re.search(r'^model\s*=\s*[\"\']([A-Za-z0-9_.:-]+)[\"\']', top, re.M)
            if match:
                return {'model': match[1], 'source': 'Codex 本地 config.toml（CLI 最终解析以运行回报为准）'}
        except OSError:
            pass
    return {'model': None, 'source': '默认模型未知；沿用 CLI 本次默认，运行后核对回报'}


def validate_default(backend, model):
    if backend not in ("codex", "claude") or model != "default":
        return
    known = default_model(backend).get("model")
    agent = next((a for a in catalog() if a["id"] == backend), None)
    entry = next((m for m in agent["models"] if m["id"] == known), None) if agent else None
    if entry and entry.get("compatibility") == "unavailable":
        raise ValueError("CLI 默认模型已知不兼容当前登录方式，请显式选择另一目录模型；不会自动替换。")
