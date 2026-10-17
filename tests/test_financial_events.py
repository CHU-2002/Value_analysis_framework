"""Source-grounded discovery, audit and report contracts; offline model fixtures."""
# 覆盖需求：REQ-017 —— AC-1…9 完整阅读、开放候选、口径/引用复核、降级与读者报告
import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts.results import financial_events as fe


def _materials(tmp_path):
    (tmp_path / 'original.txt').write_text('新材料股份2026年半年度报告。合并口径人民币元。\n新产品退款准备从100元增至400元，现金尚未支付。\f管理层说明：本期升级保修标准。准备增加不代表全部利润变化。')
    spec = {'subject': {'ticker': '600001.SH', 'company': '新材料股份', 'market': 'CN'},
            'report_period': '2026H1', 'missing_materials': ['未取得独立可比期及监管检索目录'],
            'documents': [{'id': 'report', 'title': '新材料股份2026年半年度报告', 'date': '2026-08-20',
                           'url': 'https://example.org/original.pdf', 'ticker': '600001.SH',
                           'period': '2026H1', 'kind': 'report', 'path': 'original.txt'}]}
    (tmp_path / 'materials.json').write_text(json.dumps(spec, ensure_ascii=False))
    return fe.load_materials(tmp_path / 'materials.json')


def _candidate():
    return {'id': 'report:1-refund', 'title': '新产品退款准备增加', 'priority': 'high', 'period': '2026H1',
            'disclosed_at': '2026-08-20', 'explanation': '合并退款准备由100元增至400元，尚未支付现金。',
            'terminology': '退款准备是预先计入可能退给客户的钱，值得核对估计是否足够。',
            'importance': '增加300元，可能影响利润与负债。', 'amount': '合并口径人民币元：100→400；增加300。',
            'impact': '可能影响利润及负债；现金尚未支付，税后及归母影响未知。',
            'limits': '不能等同全部利润变动，实际退款及税费影响未知。', 'status': 'disclosed',
            'field_statuses': {key: ('inference' if key in ('importance', 'impact', 'limits') else 'disclosed') for key in fe.FIELDS},
            'references': [{'page_id': 'report:1', 'quote': '新产品退款准备从100元增至400元，现金尚未支付。'}]}


class Model:
    def __init__(self, event=None, checks=None, omit=False):
        self.event = event or _candidate()
        self.checks = checks or {key: True for key in fe.AUDIT_SCHEMA['properties']['checks']['properties']}
        self.calls, self.omit = [], omit

    def __call__(self, stage, payload, schema):
        self.calls.append((stage, copy.deepcopy(payload)))
        if stage == 'read':
            return {'read_pages': sorted({p['id'] for p in payload['pages']}),
                    'notes': '完整两页，跨表口径待复核', 'candidates': [copy.deepcopy(self.event)]}
        if stage == 'review':
            return {'events': [] if self.omit else [copy.deepcopy(self.event)], 'excluded': []}
        if stage == 'repair':
            return copy.deepcopy(self.event)
        return {'verdict': 'verified', 'reason': '按原文复核通过', 'checks': self.checks}


def test_full_material_reading_and_open_type_survives_to_report(tmp_path):
    m = _materials(tmp_path)
    model = Model()
    output = tmp_path / 'result'
    payload = fe.discover(m, model, output)
    assert [stage for stage, _ in model.calls] == ['read', 'review', 'audit']
    assert ''.join(p['text'] for p in model.calls[0][1]['pages']) == ''.join(p['text'] for p in m['pages'].values())
    assert payload['coverage']['read_pages'] == ['report:1', 'report:2']
    report = (output / 'major_events.md').read_text()
    assert '退款准备增加' in report and '100→400' in report
    assert '现金尚未支付' in report and '税后及归母影响未知' in report
    assert 'https://example.org/original.pdf' in report and '文本第1页' in report
    assert '未取得独立可比期' in report and '不能等同全部利润变动' in report
    assert len(payload['material_digest']) == 64
    assert fe._period_keys('2025年一季度、2025年半年度、2025年前三季度') == fe._period_keys('2025Q1/H1/Q3')
    assert fe._period_keys('涉及2000—2009年行为') == fe._period_keys('2000-2009历史披露')


def test_overlong_pages_are_read_in_full_and_missing_pages_are_explicit(tmp_path):
    m = _materials(tmp_path)
    m['pages']['report:1']['text'] = '完整正文' * 1600
    packets = fe.reading_packets(m, 2000)
    assert ''.join(p['text'] for packet in packets for p in packet if p['id'] == 'report:1') == '完整正文' * 1600
    assert len(packets) > 1
    with pytest.raises(ValueError, match='at least'):
        fe.reading_packets(m, 100)
    (tmp_path / 'original.txt').write_text('扫描空页\f' + '有效原文' * 30)
    result = fe.load_materials(tmp_path / 'materials.json')
    assert 'report:1' not in result['pages'] and any('未完成阅读' in gap for gap in result['gaps'])


def test_missing_original_wrong_company_and_duplicate_source_fail_closed(tmp_path):
    _materials(tmp_path)
    path = tmp_path / 'materials.json'
    spec = json.loads(path.read_text())
    spec['documents'][0]['ticker'] = 'OTHER'
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match='no readable'):
        fe.load_materials(path)
    spec['documents'][0]['ticker'] = '600001.SH'
    spec['documents'][0]['path'] = 'unavailable.pdf'
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match='no readable'):
        fe.load_materials(path)
    spec['documents'].append(spec['documents'][0])
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match='unique'):
        fe.load_materials(path)


def test_unsupported_quote_cannot_be_published_as_verified(tmp_path):
    m = _materials(tmp_path)
    candidate = _candidate()
    candidate['references'][0]['quote'] = '不存在的原文，数字及原因都是编造出来的。'
    payload = fe.discover(m, Model(candidate), tmp_path / 'out')
    assert not payload['events'] and payload['unverified'][0]['disposition'] == 'unverified'
    assert '引用不在原始页' in payload['unverified'][0]['reason']
    report = (tmp_path / 'out/major_events.md').read_text()
    assert '尚无完成原文复核' in report and '不存在的原文' not in report


def test_numeric_period_or_attribution_audit_failure_downgrades(tmp_path):
    m = _materials(tmp_path)
    for field in ('numbers_and_basis', 'period', 'attribution', 'company', 'plain_language', 'evidence'):
        model = Model(checks={key: key != field for key in fe.AUDIT_SCHEMA['properties']['checks']['properties']})
        payload = fe.discover(m, model, tmp_path / field)
        assert not payload['events'] and payload['unverified']
    candidate = _candidate()
    candidate['period'] = '2025FY'
    payload = fe.discover(m, Model(candidate), tmp_path / 'wrong-period')
    assert not payload['events'] and '期间/披露日' in payload['unverified'][0]['reason']


def test_correction_cannot_be_upgraded_to_legal_finding(tmp_path):
    m = _materials(tmp_path)
    candidate = _candidate()
    candidate['explanation'] = '公司存在虚假记载，应当卖出。'
    candidate['status'] = 'regulator_confirmed'
    errors = fe.citation_errors(candidate, m)
    assert any('权威原始认定' in error for error in errors)
    assert any('买卖建议' in error for error in errors)
    doc = m['documents'][0]
    doc.update(kind='regulator', authority='监管机关', decision_status='作出决定，后续救济未知', behavior='历史披露行为')
    m['pages']['report:1']['text'] += '监管机关明确认定历史披露存在虚假记载，不涉及本期更正。'
    candidate['explanation'] = '监管机关对历史披露行为认定虚假记载；不能挪用至本期。'
    candidate['references'].append({'page_id': 'report:1', 'quote': '监管机关明确认定历史披露存在虚假记载，不涉及本期更正。'})
    assert fe.citation_errors(candidate, m) == []


def test_silent_omission_and_unread_pages_abort_publication(tmp_path):
    m = _materials(tmp_path)
    with pytest.raises(ValueError, match='silently omitted'):
        fe.discover(m, Model(omit=True), tmp_path / 'omit')
    model = Model()
    def unread(stage, payload, schema):
        result = model(stage, payload, schema)
        if stage == 'read':
            result['read_pages'] = []
        return result
    with pytest.raises(ValueError, match='every supplied page'):
        fe.discover(m, unread, tmp_path / 'unread')
    assert not (tmp_path / 'omit/report_events.json').exists()
    assert not (tmp_path / 'unread/major_events.md').exists()


def test_exclusion_and_unverified_status_remain_reviewable(tmp_path):
    model = Model()
    def exclude(stage, payload, schema):
        result = model(stage, payload, schema)
        if stage == 'review':
            return {'events': [], 'excluded': [{'id': model.event['id'], 'reason': '原文仅说明会计估计，缺少重大性依据'}]}
        return result
    output = tmp_path / 'out'
    payload = fe.discover(_materials(tmp_path), exclude, output)
    assert not payload['events']
    assert '缺少重大性依据' in (output / 'major_events.md').read_text()


def test_model_runtime_uses_structured_read_only_invocation_and_sanitizes_errors(tmp_path, monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        result = Path(args[args.index('--output-last-message') + 1])
        result.write_text(json.dumps({'answer': 'ok'}))
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(fe.subprocess, 'run', run)
    assert fe.CodexModel(model='gpt-6-luna', transport='https')('audit', {'original': 'data'}, {}) == {'answer': 'ok'}
    args, kwargs = calls[0]
    assert args[args.index('--sandbox') + 1] == 'read-only' and '--ephemeral' in args
    assert 'features.shell_tool=false' in args and args[-1] == '-'
    assert 'model_providers.turtle_events_https.supports_websockets=false' in args
    assert 'model_providers.turtle_events_https.requires_openai_auth=true' in args
    assert 'data' in kwargs['input'] and kwargs['timeout'] == 600
    def failed(*args, **kwargs):
        raise subprocess.TimeoutExpired('SECRET', 1)
    monkeypatch.setattr(fe.subprocess, 'run', failed)
    with pytest.raises(RuntimeError, match='no report published') as exc:
        fe.CodexModel(model='gpt-5.5')('read', {}, {})
    assert 'SECRET' not in str(exc.value)


def test_pipeline_cli_and_change_report_keep_coverage_and_leads(tmp_path, monkeypatch, capsys):
    _materials(tmp_path)
    monkeypatch.setattr(fe, 'CodexModel', lambda *args: Model())
    output = tmp_path / 'events'
    fe.main(['--materials', str(tmp_path / 'materials.json'), '--output-dir', str(output)])
    assert '已复核事项 1' in capsys.readouterr().out
    from scripts.results.report_presentation import main
    context = {'schema': 'investment.change_report_context', 'subject': _materials(tmp_path)['subject'],
               'report_period': '2026H1', 'comparable_period': '2025H1'}
    path = tmp_path / 'context.json'
    path.write_text(json.dumps(context))
    body = tmp_path / 'body.md'
    body.write_text('原报告的详细分析')
    report = tmp_path / 'report.md'
    main(['--context', str(path), '--events', str(output / 'report_events.json'), '--body', str(body), '--output', str(report)])
    assert '材料覆盖与限制' in report.read_text() and '原报告的详细分析' in report.read_text()
    assert '税后及归母影响未知' in report.read_text()


def test_neutral_denials_do_not_become_legal_allegations(tmp_path):
    m = _materials(tmp_path)
    for sentence in ('不能认定为财务造假', '本期更正不等于财务造假', '不能据此证明存在虚假记载', '尚未确认信息披露违法'):
        candidate = _candidate()
        candidate['explanation'] = sentence
        assert fe.citation_errors(candidate, m) == []
    candidate['explanation'] = '不能认定为财务造假，但公司另一事项存在虚假记载。'
    assert any('虚假记载' in error for error in fe.citation_errors(candidate, m))


def test_failed_rerun_prevents_stale_event_consumption(tmp_path):
    m = _materials(tmp_path)
    output = tmp_path / 'events'
    fe.discover(m, Model(), output)
    from scripts.results.report_presentation import load_events
    assert load_events(output / 'report_events.json', m)
    with pytest.raises(ValueError, match='silently omitted'):
        fe.discover(m, Model(omit=True), output)
    with pytest.raises(ValueError, match='incomplete'):
        load_events(output / 'report_events.json', m)
    assert json.loads((output / 'run_state.json').read_text())['status'] == 'failed'


def test_official_example_snapshots_preserve_amounts_and_separate_historical_decision(tmp_path):
    """AC-6/7/8: fixed official excerpts, no network or live model in CI."""
    base = Path(__file__).parent / 'fixtures/financial_events'
    yili = json.loads((base / 'yili-evidence.json').read_text())
    wuliangye = json.loads((base / 'wuliangye-evidence.json').read_text())
    m = _materials(tmp_path)
    ydoc = {'id': 'current', 'title': yili['title'], 'date': yili['date'], 'url': yili['origin'],
            'ticker': '600887.SH', 'period': '2026H1', 'kind': 'report', 'sha256': yili['sha256'],
            'page_ids': list(yili['pages']), 'status': 'available'}
    m.update(subject={'ticker': '600887.SH', 'company': '伊利股份', 'market': 'CN'}, documents=[ydoc], pages=yili['pages'])
    candidate = _candidate()
    candidate.update(title='资产减值增加及其利润口径边界', disclosed_at='2026-08-27',
                     explanation='公司解释：增加主要与澳优商誉减值及存货减值增加有关。',
                     terminology='商誉减值是收购形成的预期价值被判断无法收回而确认费用。',
                     amount='资产减值损失2,455,875,173.02元，其中商誉减值1,546,544,866.49元，合并税前口径。',
                     impact='税前损失不能直接等同归母净利润下降，税费及少数股东影响未知。',
                     references=[{'page_id': pid, 'quote': p['text']} for pid, p in yili['pages'].items()])
    payload = fe.discover(m, Model(candidate), tmp_path / 'yili')
    assert payload['events']
    report = (tmp_path / 'yili/major_events.md').read_text()
    assert '2,455,875,173.02' in report and '1,546,544,866.49' in report
    assert '商誉减值及存货减值' in report and '税前损失不能直接等同归母净利润下降' in report
    m.update(subject={'ticker': '000858.SZ', 'company': '五粮液', 'market': 'CN'},
             report_period='2025Q3', documents=wuliangye['documents'], pages=wuliangye['pages'])
    events = []
    for doc in wuliangye['documents']:
        e = _candidate()
        e.update(id=doc['id'], period=doc['period'], disclosed_at=doc['date'],
                 title='前期会计差错更正' if doc['kind'] == 'announcement' else '历史监管处罚',
                 explanation='更正2025年部分报表项目；现金流量表列示不受影响。' if doc['kind'] == 'announcement'
                 else '证监会认定历史信息披露违法行为，作出行政处罚决定。后续救济结果未知。',
                 status='disclosed' if doc['kind'] == 'announcement' else 'regulator_confirmed',
                 limits='不能将历史处罚挪用于2025年更正，也不能将更正等同造假。',
                 references=[{'page_id': pid, 'quote': p['text']} for pid, p in wuliangye['pages'].items()
                             if p['document_id'] == doc['id']])
        events.append(e)
    def fixture_model(stage, payload, schema):
        if stage == 'read':
            return {'read_pages': list(m['pages']), 'notes': '固定来源跨期分开', 'candidates': events}
        if stage == 'review':
            return {'events': events, 'excluded': []}
        return {'verdict': 'verified', 'reason': '固定夹具复核',
                'checks': {key: True for key in fe.AUDIT_SCHEMA['properties']['checks']['properties']}}
    payload = fe.discover(m, fixture_model, tmp_path / 'wuliangye')
    assert len(payload['events']) == 2
    report = (tmp_path / 'wuliangye/major_events.md').read_text()
    assert '2026-04-30' in report and '2011-04-29' in report
    assert '现金流量表列示不受影响' in report and '后续救济结果未知' in report


def test_resume_reuses_only_exact_validated_responses_after_model_disconnect(tmp_path):
    m = _materials(tmp_path)
    model = Model()
    def disconnected(stage, payload, schema):
        if stage == 'audit':
            raise RuntimeError('connection interrupted')
        return model(stage, payload, schema)
    output = tmp_path / 'resume'
    with pytest.raises(RuntimeError, match='connection interrupted'):
        fe.discover(m, disconnected, output)
    recovered = Model()
    payload = fe.discover(m, recovered, output, resume=True)
    assert payload['events'] and [s for s, _ in recovered.calls] == ['audit']
    altered = copy.deepcopy(m)
    altered['pages']['report:1']['text'] += '新版本'
    with pytest.raises(ValueError, match='fingerprint'):
        fe.discover(altered, Model(), output, resume=True)
    cache = next((output / 'responses').glob('read-*'))
    cached = json.loads(cache.read_text())
    cached['response']['notes'] = 'tampered'
    cache.write_text(json.dumps(cached))
    with pytest.raises(ValueError, match='response changed'):
        fe.discover(m, Model(), output, resume=True)
    assert json.loads((output / 'run_state.json').read_text())['status'] == 'failed'


def test_comparative_period_and_scope_context_are_checked_against_full_original(tmp_path):
    m = _materials(tmp_path)
    m['pages']['report:2']['text'] = '合并资产负债表\n单位：人民币元。比较期间2026H1、2025H1；退款准备100元、400元。'
    candidate = _candidate()
    candidate['period'] = '2026H1/2025H1'
    assert fe.citation_errors(candidate, m) == []
    model = Model(candidate)
    fe.discover(m, model, tmp_path / 'compare')
    audit_payload = next(p for stage, p in model.calls if stage == 'audit')
    assert 'report:2' in audit_payload['original_pages']
    candidate['period'] = '2025H1'
    assert any('期间/披露日' in e for e in fe.citation_errors(candidate, m))
    candidate['period'] = '2026H1/2024FY'
    model = Model(candidate, checks={k: k != 'period' for k in fe.AUDIT_SCHEMA['properties']['checks']['properties']})
    assert not fe.discover(m, model, tmp_path / 'unsupported')['events']


def test_explicit_response_reuse_requires_identical_model_request_and_keeps_provenance(tmp_path, monkeypatch):
    m = _materials(tmp_path)
    donor = tmp_path / 'donor'
    current_lookup = fe._audit_pages
    monkeypatch.setattr(fe, '_audit_pages', lambda materials, event: {'report:1': materials['pages']['report:1']})
    prior = Model()
    fe.discover(m, prior, donor)
    monkeypatch.setattr(fe, '_audit_pages', current_lookup)
    identity = json.loads((donor / 'run_fingerprint.json').read_text())
    identity['pipeline'] = 'prior-local-validator'
    schemas = {'read': fe.READ_SCHEMA, 'review': fe.REVIEW_SCHEMA, 'audit': fe.AUDIT_SCHEMA}
    for f in list((donor / 'responses').glob('*.json')):
        envelope = json.loads(f.read_text())
        stage = f.name.split('-', 1)[0]
        request = next(payload for name, payload in prior.calls if name == stage)
        digest = fe._hash({'identity': identity, 'stage': stage, 'request': request, 'schema': schemas[stage]})
        envelope['request_digest'] = digest
        moved = f.rename(f.with_name(f'{stage}-{digest}.json'))
        moved.write_text(json.dumps(envelope))
    (donor / 'run_fingerprint.json').write_text(json.dumps(identity))
    with pytest.raises(ValueError, match='resume fingerprint'):
        fe.discover(m, Model(), donor, resume=True)
    model = Model()
    target = tmp_path / 'target'
    fe.discover(m, model, target, reuse_responses_from=donor)
    assert [stage for stage, _ in model.calls] == ['audit']
    envelopes = [json.loads(f.read_text()) for f in (target / 'responses').glob('*.json')]
    origins = [entry['origin'] for entry in envelopes if 'origin' in entry]
    assert len(origins) == 2 and all(o['fingerprint'] == identity for o in origins)
    changed = copy.deepcopy(m)
    changed['pages']['report:1']['text'] += 'different source'
    with pytest.raises(ValueError, match='donor.*mismatch'):
        fe.discover(changed, Model(), tmp_path / 'different', reuse_responses_from=donor)
    different_model = Model()
    different_model.settings = {'backend': 'another model'}
    with pytest.raises(ValueError, match='donor.*mismatch'):
        fe.discover(m, different_model, tmp_path / 'other-model', reuse_responses_from=donor)
    envelope_path = next((donor / 'responses').glob('read-*'))
    envelope = json.loads(envelope_path.read_text())
    envelope['response']['notes'] = 'tampered'
    envelope_path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match='donor model response changed'):
        fe.discover(m, Model(), tmp_path / 'tampered', reuse_responses_from=donor)


def test_second_plain_language_repair_still_requires_complete_new_audit(tmp_path):
    m = _materials(tmp_path)
    candidate = _candidate()
    candidate['terminology'] = '以预期计量确认一项预计负债。'
    repairs = []
    def model(stage, payload, schema):
        if stage in ('read', 'review'):
            return Model(candidate)(stage, payload, schema)
        if stage == 'repair':
            revised = copy.deepcopy(payload['candidate'])
            repairs.append(payload['audit']['reason'])
            revised['terminology'] = '合同负债需计量。' if len(repairs) == 1 else '退款准备是可能退给客户的钱，先计入费用提醒预计支出。'
            return revised
        assert '不能只用另一个未解释的专业词' in payload['reader_standard']
        understandable = '可能退给客户的钱' in payload['candidate']['terminology']
        return {'verdict': 'verified' if understandable else 'unverified',
                'reason': '通俗解释已补齐' if understandable else '术语仍只有技术语言',
                'checks': {k: (understandable if k == 'plain_language' else True)
                           for k in fe.AUDIT_SCHEMA['properties']['checks']['properties']}}
    output = tmp_path / 'language'
    result = fe.discover(m, model, output)
    assert result['events'] and len(repairs) == 2
    audits = json.loads((output / 'audits.json').read_text())
    assert [a['verdict'] for a in audits] == ['unverified', 'unverified', 'verified']
    assert audits[0]['candidate']['terminology'] == '以预期计量确认一项预计负债。'
    assert (output / 'candidate-001/repair-1.json').is_file() and (output / 'candidate-001/repair-2.json').is_file()
