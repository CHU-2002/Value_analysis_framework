#!/usr/bin/env python3
"""Independent REQ-015 browser evidence; no product stubs, no model submission.

Run against a sandbox console. Downloads and screenshots stay in --out.
Native CDP mouse/keyboard interactions exercise the mounted UI.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import time
from gui_walkthrough import DevTools, _free_port, _resolve_browser, _wait_for_target


class DialogDevTools(DevTools):
    """Handle the browser's native confirmation dialog while awaiting CDP replies."""
    dialog_decision = False
    dialogs = None
    def send(self, method, **params):
        self._seq += 1
        request_id = self._seq
        self._ws.send(json.dumps({'id': request_id, 'method': method, 'params': params}))
        while True:
            message = json.loads(self._ws.recv())
            if message.get('method') == 'Page.javascriptDialogOpening':
                if self.dialogs is None:
                    self.dialogs = []
                self.dialogs.append(message['params']['message'])
                self._seq += 1
                self._ws.send(json.dumps({'id': self._seq, 'method': 'Page.handleJavaScriptDialog', 'params': {'accept': self.dialog_decision}}))
            if message.get('id') != request_id:
                continue
            if 'error' in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            return message.get('result', {})


class ResearchWalkthrough:
    def __init__(self, cdp, base, out):
        self.cdp, self.base, self.out = cdp, base.rstrip('/'), out
        self.observations = {'base': base, 'failures': [], 'checks': []}

    def record(self, name, actual, passed=True):
        self.observations['checks'].append({'name': name, 'passed': bool(passed), 'actual': actual})
        (self.out/'progress.json').write_text(json.dumps(self.observations,ensure_ascii=False,indent=2))
        if not passed:
            self.observations['failures'].append(name)

    def goto(self, page, company='600887.SH', extra=''):
        self.cdp.goto(f'{self.base}/#{page}?company={company}{extra}', settle=1.5)

    def text(self, selector='#panels'):
        return self.cdp.evaluate(f'document.querySelector({json.dumps(selector)})?.innerText || ""')

    def key(self, key, code=None, modifiers=0):
        for kind in ['keyDown', 'keyUp']:
            self.cdp.send('Input.dispatchKeyEvent', type=kind, key=key, code=code or key, modifiers=modifiers,
                          windowsVirtualKeyCode={'Enter':13,'Home':36,'ArrowDown':40,'ArrowRight':39,'ArrowLeft':37,'Tab':9}.get(key,0))

    def choose(self, selector, value):
        index = self.cdp.evaluate(f'Array.from(document.querySelector({json.dumps(selector)})?.options || []).findIndex(x=>x.value==={json.dumps(value)})')
        if index < 0:
            raise RuntimeError(f'option unavailable: {selector} {value}')
        self.cdp.evaluate(f'document.querySelector({json.dumps(selector)}).focus()')
        label = self.cdp.evaluate(f'document.querySelector({json.dumps(selector)}).options[{index}].textContent')
        for char in label:
            self.cdp.send('Input.dispatchKeyEvent', type='char', text=char)
        time.sleep(1.3)
        if 'research-control' in selector or any(word in selector for word in ['交易周期','显示范围','分位统计窗口','价格复权']):
            for _ in range(60):
                if '正在读取本地历史' not in self.text('.research-chart [role="status"]'):break
                time.sleep(.25)
        actual = self.cdp.evaluate(f'document.querySelector({json.dumps(selector)})?.value')
        if actual != value:
            raise RuntimeError(f'native select did not choose {value}: {actual}')

    def button(self, text, parent='#panels'):
        selector = self.cdp.evaluate(f'''(()=>{{const x=[...document.querySelectorAll({json.dumps(parent+' button')})].find(x=>x.textContent==={json.dumps(text)});if(!x)return null;x.dataset.qaClick="1";return '[data-qa-click="1"]';}})()''')
        if not selector or not self.cdp.click_selector(selector):
            raise RuntimeError(f'button absent: {text}')
        self.cdp.evaluate('document.querySelector("[data-qa-click]")?.removeAttribute("data-qa-click")')
        time.sleep(.25)

    def toggle_indicator(self, label):
        selector = self.cdp.evaluate(f'''(()=>{{const x=[...document.querySelectorAll(".research-chart label")].find(x=>x.textContent.includes({json.dumps(label)}));if(!x)return null;const input=x.querySelector("input[type=checkbox]");if(!input)return null;input.dataset.qaToggle="1";return '[data-qa-toggle="1"]';}})()''')
        if not selector or not self.cdp.click_selector(selector):
            raise RuntimeError(f'indicator checkbox absent: {label}')
        self.cdp.evaluate('document.querySelector("[data-qa-toggle]")?.removeAttribute("data-qa-toggle")')
        time.sleep(.5)

    def screenshot(self, name):
        self.cdp.screenshot(self.out/(name+'.png'))

    def rows(self):
        return self.cdp.evaluate('[...document.querySelectorAll(".research-table tbody tr")].map(x=>[...x.cells].map(c=>c.textContent))')

    def row_summary(self):
        return self.cdp.evaluate('''(()=>{const rows=[...document.querySelectorAll(".research-table tbody tr")];const first=rows[0],last=rows[rows.length-1];return {count:rows.length,first:first?[first.cells[0]?.textContent,first.cells[1]?.textContent]:[],last:last?[last.cells[0]?.textContent,last.cells[1]?.textContent]:[]}})()''')

    def band_values(self, metric):
        return self.cdp.evaluate(f'''(()=>{{const headers=[...document.querySelectorAll(".research-table th")].map(x=>x.textContent);const row=document.querySelectorAll(".research-table tbody tr");const cells=[...(row[row.length-1]?.cells||[])].map(x=>x.textContent);const names={json.dumps({"PE(TTM)":"PE","PB":"PB","PS(TTM)":"PS"}[metric])};return {{low:cells[headers.indexOf(names+"低估P25")],high:cells[headers.indexOf(names+"高估P75")],lastDate:cells[headers.indexOf("末交易日")]}}}})()''')

    def downloads(self):
        time.sleep(.5)
        return [p for p in self.out.glob('downloads/*') if p.is_file() and not p.name.endswith('.crdownload')]

    def charts(self, width):
        self.goto('charts')
        self.record(f'{width}: default day / one year',self.cdp.evaluate('[...document.querySelectorAll("[data-research-control]")].map(x=>[x.dataset.researchControl,x.value])'), '243 根' in self.text() or bool(self.rows()))
        initial = self.rows()
        self.button('放大'); zoomed=self.rows()
        self.record(f'{width}: zoom reduces visible candles', [len(initial),len(zoomed)],0<len(zoomed)<len(initial))
        self.button('向后平移'); panned=self.rows()
        self.record(f'{width}: pan changes actual dates', [zoomed[0][:2],panned[0][:2]],zoomed[0][:2]!=panned[0][:2])
        point=self.cdp.center_of('[data-research-canvas]');self.record(f'{width}: wheel target',point,bool(point));self.cdp.move(point['x'],point['y'])
        hover_target=self.cdp.evaluate('document.querySelector("[data-research-canvas]")?.matches(":hover")')
        self.record(f'{width}: wheel target entered',hover_target,hover_target is True)
        self.cdp.send('Input.dispatchMouseEvent',type='mouseWheel',x=point['x'],y=point['y'],deltaX=0,deltaY=-120)
        wheel_rows=self.row_summary();self.record(f'{width}: wheel zoom',wheel_rows,wheel_rows['count']<len(panned))
        before=self.rows();self.cdp.send('Input.dispatchMouseEvent',type='mousePressed',x=point['x'],y=point['y'],button='left',buttons=1,clickCount=1)
        self.cdp.send('Input.dispatchMouseEvent',type='mouseMoved',x=point['x']-100,y=point['y'],button='left',buttons=1)
        self.cdp.send('Input.dispatchMouseEvent',type='mouseReleased',x=point['x']-100,y=point['y'],button='left',buttons=0,clickCount=1)
        self.record(f'{width}: drag pan',self.rows()[0][:2],before[0][:2]!=self.rows()[0][:2])
        self.button('重置');self.record(f'{width}: reset',len(self.rows()),self.rows()==initial)
        self.cdp.click_selector('[data-research-canvas]');self.key('ArrowRight')
        readout=self.text('.research-readout');self.record(f'{width}: keyboard linked exact readout',readout,'开 ' in readout and 'PE(TTM)' in readout and '来源' in readout)
        self.record(f'{width}: PE five-year valuation bands',readout,
                    '低估线 P25' in readout and '高估线 P75' in readout and '近5年样本' in readout)
        values=self.band_values('PE(TTM)')
        self.record(f'{width}: real PE P25/P75 observations',values,
                    bool(values.get('lastDate')) and values.get('low') not in (None,'—（缺失）') and values.get('high') not in (None,'—（缺失）'))
        for label,metric in [('PB · 倍','PB'),('PS(TTM) · 倍','PS(TTM)')]:
            self.toggle_indicator(label)
            self.cdp.click_selector('[data-research-canvas]');self.key('ArrowRight')
            readout=self.text('.research-readout')
            self.record(f'{width}: {metric} five-year valuation bands',readout,
                        metric in readout and '低估线 P25' in readout and '高估线 P75' in readout)
            values=self.band_values(metric)
            self.record(f'{width}: real {metric} P25/P75 observations',values,
                        bool(values.get('lastDate')) and values.get('low') not in (None,'—（缺失）') and values.get('high') not in (None,'—（缺失）'))
        self.screenshot(f'{width}-valuation-bands')
        for cycle in ['week','month','quarter','year','day']:
            self.choose('[aria-label="交易周期"]',cycle)
            self.record(f'{width}: native cycle {cycle}',{'first':self.rows()[0][:2],'count':len(self.rows()),'hash':self.cdp.evaluate('location.hash')},len(self.rows())>0)
        self.choose('[aria-label="显示范围"]','all')
        allrows=self.row_summary();self.record(f'{width}: actual historical range',allrows,allrows['count']>1000)
        self.choose('[aria-label="分位统计窗口"]','3');self.record(f'{width}: window persisted',self.cdp.evaluate('location.hash'),'window=3' in self.cdp.evaluate('location.hash'))
        self.choose('[aria-label="交易周期"]','month')
        self.button('导出 CSV');self.button('导出 TSV');self.button('导出图片')
        files=self.downloads();export=[p for p in files if p.suffix in ['.csv','.tsv'] and '-month' in p.name]
        headers=[p.read_text(encoding='utf-8-sig').splitlines()[0] for p in export]
        self.record(f'{width}: timeline exports current metadata',headers,len(headers)>=2 and all('交易周期=月' in h and '分位窗口=近3年' in h and '600887.SH' in h for h in headers))
        self.screenshot(f'{width}-chart-interaction')
        basis='[data-chart-basis]'
        for value in ['half','quarter','annual']:
            # The source's actual option values differ between annual/halfyear/single-quarter.
            options=self.cdp.evaluate(f'[...document.querySelector({json.dumps(basis)})?.options || []].map(x=>[x.value,x.textContent])')
            candidates=[x for x in options if {'half':'半年','quarter':'单季','annual':'年度'}[value] in x[1]]
            if candidates:
                self.choose(basis,candidates[0][0]);self.record(f'{width}: financial {value}',self.cdp.evaluate(f'document.querySelector({json.dumps(basis)}).value'))
        self.button('导出当前财务 CSV');self.button('导出图片','.panel:not(:has(.research-chart))')
        financial=[p for p in self.downloads() if p.name.endswith('-financial.csv')]
        header=financial[0].read_text(encoding='utf-8-sig').splitlines()[0] if financial else ''
        self.record(f'{width}: T10 financial export metadata',header,all(k in header for k in ['公司=','财务口径=','单位=','显示期次=','指标=','数据期次=','获取时点=','源版本=']))

    def reader(self,width):
        self.goto('report');chosen=self.cdp.evaluate('document.querySelector(".report-reader")?.dataset.artifact')
        report_order=self.cdp.evaluate('(()=>{const s=document.querySelector("[aria-label=报告类型]");return {selected:s?.value,options:[...(s?.options||[])].map(x=>[x.value,x.textContent])}})()')
        self.record(f'{width}: value analysis is report default and first',report_order,
                    report_order.get('selected')=='value' and bool(report_order.get('options')) and report_order['options'][0][0]=='value')
        if not chosen:
            for _ in range(40):
                versions=self.cdp.evaluate('[...document.querySelector("[aria-label=报告版本]")?.options||[]].map(x=>[x.value,x.textContent])')
                if len(versions)>1:break
                time.sleep(.25)
            if len(versions)>1:
                self.choose('[aria-label="报告版本"]',versions[1][0]);time.sleep(.5)
                chosen=self.cdp.evaluate('document.querySelector(".report-reader")?.dataset.artifact')
        version_options=self.cdp.evaluate('[...document.querySelector("[aria-label=报告版本]")?.options||[]].map(x=>x.value)')
        self.record(f'{width}: value history can be selected when no publish pointer resolves',
                    {'chosen':chosen,'options':version_options},bool(chosen))
        if not chosen:
            return
        toc=self.cdp.evaluate('[...document.querySelectorAll(".report-toc button")].map(x=>[x.textContent,x.dataset.anchor])')
        self.record(f'{width}: real headings unique anchors',toc,bool(toc) and len({x[1] for x in toc})==len(toc))
        self.cdp.click_selector('.report-toc button',min(3,len(toc)-1));focused=self.cdp.evaluate('document.activeElement.id')
        self.record(f'{width}: TOC focuses actual heading',focused,focused==toc[min(3,len(toc)-1)][1])
        self.cdp.type_into('[aria-label="文内查找"]','伊利');found=self.text('.report-find-count');self.button('下一项');nextitem=self.text('.report-find-count')
        self.record(f'{width}: search count / next', [found,nextitem],found!=nextitem and '项' in found)
        self.button('上一项');self.record(f'{width}: search previous',self.text('.report-find-count'),self.text('.report-find-count')==found)
        self.cdp.click_selector('[aria-label="文内查找"]');self.key('a','KeyA',4);self.cdp.send('Input.insertText',text='ZZZ_NO_MATCH_QA');self.record(f'{width}: no-match search',self.text('.report-find-count'),'无匹配' in self.text('.report-find-count'))
        self.button('专注阅读');self.record(f'{width}: focus mode',self.cdp.evaluate('document.body.classList.contains("report-focus")'),self.cdp.evaluate('document.body.classList.contains("report-focus")'));self.button('退出专注')
        self.button('比较正文');time.sleep(.5);diff=self.text('.report-diff-result');self.record(f'{width}: real version body comparison',diff[:900],'左版' in diff and '右版' in diff)
        self.button('下载当前版本');time.sleep(.5);self.record(f'{width}: selected version download',self.text('.report-transfer-status'),'已下载' in self.text('.report-transfer-status'))
        self.screenshot(f'{width}-reader-interaction');self.cdp.reload(settle=1.5);self.record(f'{width}: refresh same report',self.cdp.evaluate('document.querySelector(".report-reader")?.dataset.artifact'),chosen==self.cdp.evaluate('document.querySelector(".report-reader")?.dataset.artifact'))
        self.choose('[aria-label="报告类型"]','material');self.record(f'{width}: source materials separated',self.text('.report-reader')[:800],'源材料' in self.text('.report-reader'))
        versions=self.cdp.evaluate('[...document.querySelector("[aria-label=报告版本]").options].map(x=>[x.value,x.textContent])')
        if len(versions)>1:
            self.choose('[aria-label="报告版本"]',versions[1][0]);self.record(f'{width}: non-Markdown no failed renderer',self.text('.report-reader')[:900],'report-body' not in self.cdp.evaluate('document.querySelector(".report-reader").innerHTML'))

    def collection(self,width):
        self.goto('data');
        self.cdp.evaluate('requestAnimationFrame(()=>window.__qaFrameObservation={time:performance.now(),connected:document.querySelector(".collection-workspace")?.isConnected})')
        for _ in range(30):
            if '计划已生成' in self.text('.collection-workspace'):break
            time.sleep(.3)
        text=self.text('.collection-workspace')
        self.record(f'{width}: mount diagnostics',self.cdp.evaluate('({connected:document.querySelector(".collection-workspace")?.isConnected,visibility:document.visibilityState,ready:document.readyState,raf:window.__qaFrameObservation||null})'))
        self.record(f'{width}: T8 initial plan mounted',text[:1600],'计划已生成' in text and '分母范围' in text)
        self.screenshot(f'{width}-initial-plan')
        if '确认采集（联网、消耗配额）' in text:
            self.button('确认采集（联网、消耗配额）','.collection-workspace');time.sleep(.2)
        self.record(f'{width}: cancel preserves preflight',self.text('.collection-workspace')[:220],'尚未产生数据源请求' in self.text('.collection-workspace'))
        self.goto('agent',extra='&job=job-27a2d1decbff');time.sleep(.7);result=self.text('.agent-job-result')
        self.record(f'{width}: T8 restored job mounted',self.text('#panels')[-1800:],'job-27a2d1decbff' in self.text() and ('结束' in self.text() or '额度' in self.text() or '失败' in self.text()))
        self.screenshot(f'{width}-restored-job')

    def navigation(self,width):
        for page,label in [('research','company overview'),('charts','chart'),('report','report')]:
            self.goto(page)
            actions=self.cdp.evaluate('document.querySelector("#page-actions")?.innerText||""')
            self.record(f'{width}: {label} exposes buy/sell plan action',actions,'生成买卖计划' in actions)
        self.goto('research')
        link=self.cdp.evaluate('''(()=>{const x=[...document.querySelectorAll("#page-actions button")].find(x=>x.textContent==="生成买卖计划");if(!x)return null;x.dataset.qaClick="1";return '[data-qa-click="1"]';})()''')
        if link and self.cdp.click_selector(link):
            self.cdp.evaluate('document.querySelector("[data-qa-click]")?.removeAttribute("data-qa-click")')
            time.sleep(1)
            plan=self.cdp.evaluate('''(()=>{const card=document.querySelector('[data-action-id="company.buy_sell_plan"]');const action=card?.querySelector('[data-action-run]');return {present:!!card,focused:card?.classList.contains("action-card-focused"),active:document.activeElement?.dataset?.actionRun||"",enabled:card?.dataset?.enabled,button:action?.textContent,effects:card?.querySelector(".action-effects")?.textContent||""}})()''')
            contract=self.cdp.evaluate('''fetch("/api/v1/actions?company=600887.SH").then(r=>r.json()).then(x=>{const a=(x.data?.actions||[]).find(y=>y.id==="company.buy_sell_plan");return {danger:a?.danger,confirm:a?.confirm,effects:a?.effects,blockers:a?.blockers}})''')
            self.record(f'{width}: buy/sell plan stays in existing confirmed action',{'card':plan,'contract':contract},
                        bool(plan.get('present')) and bool(plan.get('focused')) and plan.get('active')=='company.buy_sell_plan'
                        and contract.get('danger') is True and bool(contract.get('confirm'))
                        and bool(contract.get('effects',{}).get('network')) and bool(contract.get('effects',{}).get('quota')))
            self.screenshot(f'{width}-buy-plan-entry')
        else:
            self.record(f'{width}: buy/sell plan action navigation', 'company CTA did not open actions',False)
        self.goto('research')
        nav=self.cdp.evaluate('[...document.querySelectorAll("#nav > a")].map(x=>x.textContent)')
        self.record(f'{width}: primary navigation hierarchy',nav,len(nav)==3 and all(word in label for word,label in zip(['工作台','公司','任务'],nav)))
        for page in ['charts','report','data','research']:
            selector=f'#context-nav a[href^="#{page}?"]'
            self.cdp.evaluate(f'document.querySelector({json.dumps(selector)}).focus()')
            self.key('Enter');time.sleep(1.5)
            self.record(f'{width}: keyboard one-step context {page}',self.cdp.evaluate('location.hash'),self.cdp.evaluate('location.hash').startswith('#'+page+'?company=600887.SH'))
        company_b=self.cdp.evaluate('[...document.querySelector("#company-select").options].find(x=>x.value.startsWith("000858"))?.value')
        self.choose('#company-select',company_b)
        self.record(f'{width}: company switch B',self.cdp.evaluate('location.hash'),'company=000858' in self.cdp.evaluate('location.hash'))
        self.cdp.reload(settle=1.5);self.record(f'{width}: B reload persisted',self.cdp.evaluate('document.querySelector("#company-select").value'),self.cdp.evaluate('document.querySelector("#company-select").value').startswith('000858'))
        self.goto('research','600887.SH');self.goto('research',company_b);self.cdp.evaluate('history.back()');time.sleep(1.5);self.record(f'{width}: back restores A',self.cdp.evaluate('location.hash'),'company=600887.SH' in self.cdp.evaluate('location.hash'))
        self.cdp.evaluate('history.forward()');time.sleep(1.5);self.record(f'{width}: forward restores B',self.cdp.evaluate('location.hash'),'company=000858' in self.cdp.evaluate('location.hash'))
        for page in ['charts','report','data']:
            self.goto(page,company_b);self.record(f'{width}: B bound {page}',self.text()[:250],self.cdp.evaluate('document.querySelector("#company-select").value').startswith('000858'))
        self.goto('report','000001.SZ');self.record(f'{width}: company no-report next step',self.text()[:550],'尚无此类型报告' in self.text())
        self.goto('charts','000001.SZ');self.record(f'{width}: company no-data next step',self.text()[:700],('无本地' in self.text() or '无数据' in self.text() or '不能生成' in self.text() or '缺少本地' in self.text() or '没有本地' in self.text()))
        self.screenshot(f'{width}-no-data')

    def duplicate_reader(self,width):
        self.goto('report','000002.SZ')
        self.choose('[aria-label="报告类型"]','business')
        versions=self.cdp.evaluate('[...document.querySelector("[aria-label=报告版本]")?.options||[]].filter(x=>x.value).map(x=>x.value)')
        if versions:self.choose('[aria-label="报告版本"]',versions[0])
        headings=self.cdp.evaluate('[...document.querySelectorAll(".report-toc button")].filter(x=>x.textContent==="重复标题").map(x=>x.dataset.anchor)')
        self.record(f'{width}: duplicate-heading controlled fixture',headings,len(headings)==2 and headings[0]!=headings[1])
        if not headings:return
        self.cdp.click_selector('.report-toc button[data-anchor="'+headings[-1]+'"]')
        self.record(f'{width}: second duplicate keyboard focus',self.cdp.evaluate('document.activeElement.id'),self.cdp.evaluate('document.activeElement.id')==headings[-1])
        self.record(f'{width}: technical JSON folded',self.cdp.evaluate('[...document.querySelectorAll(".report-body details")].map(x=>x.open)'),self.cdp.evaluate('!!document.querySelector(".report-body details") && !document.querySelector(".report-body details").open'))
        self.screenshot(f'{width}-duplicate-headings')

    def run(self, viewports=((1440,900),(1024,768))):
        for width,height in viewports:
            self.cdp.send('Emulation.setDeviceMetricsOverride',width=width,height=height,deviceScaleFactor=1,mobile=False)
            self.cdp.reload(settle=1.2)
            for name,fn in [('charts',self.charts),('reader',self.reader),('collection',self.collection),('navigation',self.navigation),('duplicate_reader',self.duplicate_reader)]:
                try:
                    fn(width)
                except Exception as exc:
                    self.record(f'{width}: {name} execution',str(exc),False)
            self.record(f'{width}: no page overflow',self.cdp.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})'),self.cdp.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        return self.observations


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base',required=True);parser.add_argument('--out',required=True);parser.add_argument('--baseline',action='store_true');args=parser.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True);(out/'downloads').mkdir(exist_ok=True)
    browser=_resolve_browser(None)
    observations={'base':args.base,'failures':[],'checks':[],'browser':browser,'viewport_runs':[]}
    for width,height in [(1440,900),(1024,768)]:
        port=_free_port()
        proc=subprocess.Popen([browser,'--headless=new','--disable-gpu','--no-first-run',f'--remote-debugging-port={port}',f'--user-data-dir={out / f"profile-{width}"}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            target=_wait_for_target(port)
            if not target:raise RuntimeError('browser did not expose DevTools')
            cdp=DialogDevTools(target['webSocketDebuggerUrl']);cdp.send('Page.enable');cdp.send('Browser.setDownloadBehavior',behavior='allow',downloadPath=str(out/'downloads'))
            result=ResearchWalkthrough(cdp,args.base,out).run(((width,height),))
            observations['checks'].extend(result['checks'])
            observations['failures'].extend(result['failures'])
            observations['viewport_runs'].append({'width':width,'height':height,'checks':len(result['checks']),'failures':result['failures']})
        except Exception as exc:
            observations['failures'].append(f'{width}: browser initialization: {exc}')
            observations['viewport_runs'].append({'width':width,'height':height,'failures':[str(exc)]})
        finally:
            proc.terminate();proc.wait(timeout=10)
    (out/'observations.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'failures':observations['failures'],'checks':len(observations.get('checks',[])),'evidence':str(out)},ensure_ascii=False));return int(bool(observations['failures']))


if __name__=='__main__':
    raise SystemExit(main())
