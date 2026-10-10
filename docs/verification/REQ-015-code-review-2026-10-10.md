---
reviewer: independent adversarial-review agent (/root/adversarial_review)
independence: independent
requirements: [REQ-015, REQ-013, REQ-013.3, REQ-009.3]
full-suite: "未执行；相关回归 218 passed，不能替代 make verify"
record-type: code-review
reviewed-head: e027bf5d97d4abf83624e5d00b4584552e03148b
---

# REQ-015 独立对抗式代码审查记录（2026-10-10）

结论：本轮发现四个可复现代码阻断，已登记 REQ-015 T12～T15，并在候选
`e027bf5` 独立复验通过；在下述代码审查范围内未发现残留阻断。
本记录**不宣称全部 AC 已验收**，不推进需求状态，不替代真实浏览器、真实采集、
真实 agent 成功生成或最终独立验收报告。

审查者未参与产品实现、未修改程序或测试；先只读审查并向实现者报告反例，
由主线独立修复，再读取候选和运行复验。本次只创建这份审查记录。
所有额外数据只在 `TemporaryDirectory` 或内存中构造；未调用真实 agent、远程数据源或 token，
未修改真实存档或 HOME 中既有产物。

## 范围与环境

- 初始审查基线：`b923637` 及当时 T11 失败摘要候选；四个原始反例均在修复前实际执行/核对。
- 修复复验：`e027bf5`；数据窗口修复包含 `9513c21`、`b4d58d8`、`4ced3f1`。
- 仓库：`/Users/everett.li/Project/Turtle_investment_framework`。
- 环境：macOS / Darwin arm64，仓库 `.venv` Python 3.12.13，Node v24.14.1。
- 阅读父需求与五片全部 AC、REQ-013 父与 .3、REQ-009.3 的缓存和安全边界；检查 URL
  公司/版本/任务绑定、异步响应、原始源快照和派生缓存、OHLC/排名/导出、报告下载与渲染、
  agent 模型白名单/实际模型回报/产物判定、互斥与旧插件缺省声明兼容。
- 通用 NavItem `placement/actions` 已有 owner 授权；没有用更新指纹替代批准。

## 四个失败反例与复验

| 登记 | 修复前失败证据及影响 | 候选修复与独立复验 |
|---|---|---|
| T12 · 周期末估值 | `research_timeline.py` 原 `daily_rows` 只遍历价格日期，`aggregate` 取最后价格行。价格仅有 2026-01-29；每日估值含 01-29 PE10 与 01-30 PE20；日历确认两天开市。月线返回 `end=01-29, PE=10, ohlc_reason=''`，另列 01-30 缺行情。没有遵守 AC-1.3 的期间末交易日取值/留缺。 | 当前 `daily_rows` 合并价格与估值日期，`timeline_payload` 对日历证明的已发生、非停牌缺行情日留空；解析器版本升为 2。返回 `end=01-30, PE=20, rank=50, close=null` 且 K 线有缺字段原因；末日估值不存在则 PE/排名均 null。停牌日不冒充交易，未来 02-02 不补，真实行情计数仍 1。当前见 `scripts/webui/plugins/research_timeline.py:70`、`:162`。 |
| T13 · 下载绕过凭据脱敏 | `report_reader.content` 原先直接 Base64 编码原文件。报告含已知假凭据 `mock_secret_1234567890`；将结果按真实响应出口 `redact(json.dumps(payload), secrets)` 处理后，JSON 明文无凭据，但解码下载仍含原凭据。`report.js` 直接打开/下载这些字节，违反 AC-6 与 AC-3.4 安全边界。 | Base64 前检查已知凭据并返回可读 BadRequest，拒绝导出而不改写原材料。相同假凭据反例现在被拒绝；普通报告下载字节与原文件一致。当前见 `scripts/webui/plugins/report_reader.py:273`。 |
| T14 · 声明产物不完整仍成功 | 原 `_action_reports('update-analysis', ['qualitative_report.md'])` 非空，包装脚本和产物 API 都据此认为产出成功，漏掉变化报告。固定流程 `.claude/commands/update-analysis.md:112`、`:116`、`:144` 明确要求变化报告，确认文案同样声明，违反 AC-2.4。 | `_missing_reports` 对更新分析同时要求商业质量与变化报告。实际包装脚本调用隔离假 Claude：CLI 退出 0 且只写商业报告，包装退出 6；audit 保留 CLI 退出 0 并记录 `missing_reports=['变化报告']`，API使用同一判定并给出缺项。当前见 `scripts/agent_action.py:315` 和 `scripts/webui/plugins/agent_report.py`。 |
| T15 · 旧提交响应重绑公司 | 直接运行实际 `agent_form.js` 源码，以内存 DOM 与延迟 API Promise 复现：A=600887.SH 提交尚未返回，切到 B=000858.SZ 并卸载 A 表单，再返回 A-job。原回调仍执行 `setHash('agent', {company:B, job:A-job})`，违反父 AC-1/.2 AC-2.4。 | 捕获提交时公司与参数；返回时检查表单仍挂载及公司未变。相同延迟反例现在 `resultingHash=[]`；正常 A 提交仍产生 A/company 与 A-job URL。两种情况 POST 均只有 1 次；卸载后不重新预检/抢回页面。当前见 `scripts/webui/static/kinds/agent_form.js:52`。 |

T11 摘要对照也通过：包含“从环境读取 TUSHARE_TOKEN”的正常说明加真实额度错误，
仍判“模型额度已用尽”；真实 `NO_TOKEN: 未配置 Tushare token` 仍判“没有可用的数据源凭据”。
原始日志不改写，不自动重跑。

## 可复制复验命令

以下均从仓库根目录执行，使用本机离线假数据/假 CLI。

```bash
.venv/bin/python -m pytest tests/test_webui_views.py tests/test_agent_action.py tests/test_console_actions.py tests/test_console_context.py tests/test_console_data_page.py tests/test_console_views.py tests/test_data_store.py tests/test_data_pull.py -q
```

实际输出：`218 passed in 41.63s`，退出码 0。另一次聚焦执行
`tests/test_webui_views.py tests/test_agent_action.py` 为 `59 passed in 6.51s`，退出码 0。
这些是相关回归，**不是全量 make verify 或覆盖率门禁结论**。

独立 Python 反例复验（直接使用实际产品函数与包装脚本，假凭据不取自环境）：

```bash
PYTHONPATH=scripts .venv/bin/python - <<'PY'
import base64, json, tempfile, subprocess, sys
from pathlib import Path
from datetime import date
from webui.plugins.research_timeline import daily_rows, timeline_payload
from webui.config import Config
from webui.core.registry import build_registry
from webui.core.routes import install_core_routes
from webui.core.context import RequestContext
from webui.core.errors import BadRequest
from webui.core.jobs import Job, JobRunner, FAILED
from webui.datastore import DataStore
from webui.plugins import companies, report_reader
prices = [{'trade_date':'20260129','open':10,'high':12,'low':9,'close':11,'vol':2}]
vals = [{'trade_date':'20260129','pe_ttm':10,'pb':1}, {'trade_date':'20260130','pe_ttm':20,'pb':2}]
cal = [{'cal_date':x,'is_open':1} for x in ('20260129','20260130','20260202')]
opts = dict(company='600887.SH',cycle='month',range='all',window='all',as_of=date(2026,2,1),calendar_rows=cal)
p = timeline_payload(daily_rows(prices, vals, []), **opts); q = p['points'][-1]
assert (q['end'],q['pe_ttm'],q['pe_ttm_rank'],q['close']) == ('2026-01-30',20,50,None)
assert q['ohlc_reason'] and p['coverage']['daily_samples'] == 1
assert all(x['end'] < '2026-02-02' for x in p['points'])
p = timeline_payload(daily_rows(prices, vals[:1], []), **opts); q = p['points'][-1]
assert (q['end'],q['pe_ttm'],q['pe_ttm_rank']) == ('2026-01-30',None,None)
p = timeline_payload(daily_rows(prices, vals[:1], []), suspension_rows=[{'trade_date':'20260130','suspend_type':'S'}], **opts)
assert p['points'][-1]['end'] == '2026-01-29' and p['points'][-1]['close'] == 11
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    config = Config(host='127.0.0.1',port=0,output_root=root/'output',cache_dir=root/'output'/'.cache',archive_root=root/'archive')
    reg = build_registry(); install_core_routes(reg, config); reg.config = config
    companies.contribute(reg); reg.datastore = DataStore(config, spec_lookup=reg.dataset_spec)
    base = root/'output'/'111111_甲'; base.mkdir(parents=True)
    (base/'qualitative_report.md').write_text('# safe report')
    secret = 'mock_secret_1234567890'
    ctx = RequestContext(method='GET',path='/',query={},config=config,registry=reg,secrets=(secret,))
    safe = report_reader.content(ctx,'111111_甲',companies._artifact_id('qualitative_report.md'))
    assert base64.b64decode(safe['base64']) == b'# safe report'
    (base/'qualitative_report.md').write_text('# report\n' + secret)
    try:
        report_reader.content(ctx,'111111_甲',companies._artifact_id('qualitative_report.md'))
    except BadRequest as error:
        assert '已知凭据' in str(error)
    else:
        raise AssertionError('secret leaked')
    company = root/'output'/'600887_甲'; company.mkdir()
    fake = root/'fake-claude'
    fake.write_text('#!' + sys.executable + '\nfrom pathlib import Path\nPath(' + repr(str(company/'qualitative_report.md')) + ').write_text("# primary only")\n')
    fake.chmod(0o755)
    run = subprocess.run([sys.executable,'scripts/agent_action.py','--action','update-analysis','--ticker','600887.SH','--cli',str(fake),'--backend','claude','--model','sonnet','--output-root',str(root/'output'),'--require-report'], capture_output=True,text=True,env={'PATH':'/usr/bin:/bin'},timeout=15)
    assert run.returncode == 6 and '变化报告' in run.stdout
    audit = json.loads(next((root/'output'/'.agent_audit').glob('*.json')).read_text())
    assert audit['exit_code'] == 0 and audit['missing_reports'] == ['变化报告']
job = Job('mock','demo','mock',[],{},status=FAILED,exit_code=1)
job.log.extend(['TUSHARE_TOKEN is loaded from environment', "You've hit your usage limit. Try again at 9:16PM"])
assert '额度已用尽' in JobRunner._summarise(None,job)['what']
job.log.clear(); job.log.append('NO_TOKEN: 未配置 Tushare token')
assert '数据源凭据' in JobRunner._summarise(None,job)['what']
print('PASS: terminal valuation available/missing, suspension/future guards, real-price count, safe download/known-secret rejection, fake CLI 0+missing change => 6, quota and NO_TOKEN summaries')
PY
```

实际输出与上述 PASS 行一致，退出码 0。

独立 JS 异步反例复验（读取实际源码，内存 DOM/API 仅为测试替身，不修改产品或运行环境）：

```bash
node --input-type=module - <<'JS'
import fs from 'node:fs'; import assert from 'node:assert/strict';
const source = fs.readFileSync('scripts/webui/static/kinds/agent_form.js','utf8').replace(/^import.*\n/m,'').replace('export async function render','async function render');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this._value='';this.connected=true;}
  append(...nodes){for(const n of nodes)if(n&&typeof n==='object'){n.parent=this;this.children.push(n);}}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  setAttribute(){} remove(){this.connected=false;}
  get isConnected(){return this.connected&&(!this.parent||this.parent.isConnected);}
  get value(){return this._value||(this.tag==='select'?this.children[0]?.value:'')||'';}
  set value(v){this._value=v;}
}
async function probe(switchCompany){
  const document={createElement:tag=>new Element(tag)},frames=[],selection={company:'600887.SH'},hashes=[];
  let resolvePost,posted,posts=0;
  const api=async(path,options)=>{
    if(path.startsWith('/api/v1/agent/preflight'))return {data:{ready:true,command_line:'mock'}};
    if(path==='/api/v1/jobs'){posts++;posted=JSON.parse(options.body);return await new Promise(r=>resolvePost=r);}
    if(path.endsWith('/artifacts'))return {data:{actual_model:'mock',links:[]}};
    return {data:{status:'finished',params:posted.params,argv:[],log:[],exit_code:0}};
  };
  const render=new Function('api','selection','setHash','document','requestAnimationFrame','window',source+'\nreturn render;')(api,selection,(...args)=>hashes.push(args),document,cb=>frames.push(cb),{confirm:()=>true});
  const container=new Element('div');
  await render(container,{}, {company:'600887.SH',commands:[{id:'agent_update_analysis',title:'更新分析报告'}],agents:[{id:'codex',title:'Codex',installed:true,models:[],source:'mock',default:{model:'mock',source:'mock'}}]});
  frames[0](); await new Promise(r=>setTimeout(r,0));
  const pending=container.children[0].onsubmit({preventDefault(){}});
  if(switchCompany){selection.company='000858.SZ';container.connected=false;}
  resolvePost({data:{id:'A-job'}}); await pending;
  assert.equal(posts,1); assert.equal(posted.params.ticker,'600887.SH');
  assert.deepEqual(hashes,switchCompany?[]:[['agent',{company:'600887.SH',job:'A-job'}]]);
  return {switchCompany,submittedCompany:posted.params.ticker,resultingHash:hashes,posts};
}
console.log(JSON.stringify([await probe(true),await probe(false)]));
JS
```

实际输出：

```json
[{"switchCompany":true,"submittedCompany":"600887.SH","resultingHash":[],"posts":1},{"switchCompany":false,"submittedCompany":"600887.SH","resultingHash":[["agent",{"company":"600887.SH","job":"A-job"}]],"posts":1}]
```

退出码 0。修复前同一切公司反例输出 `company=000858.SZ, job=A-job`，已报告后登记 T15。

## 待独立实跑判定

本报告完成时，T8 初次挂载读取的真实 GUI 复验仍待最终 QA 完成；后台浏览器页的
`requestAnimationFrame` 可能暂停，不能用本报告的内存 DOM/mock 代替真实挂载观察。
两种视口、键盘路径、导出文件视觉/元信息、真实日期覆盖与批次解释、至少一次真实
agent 成功生成和实际模型/本次产物归属，均由最终独立验收报告判定。
额度失败但产生报告的既有真实调用不能因本报告而改判成功。
