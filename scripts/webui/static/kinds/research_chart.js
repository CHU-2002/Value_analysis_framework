// REQ-015.1: one canvas / one time coordinate for OHLC, volume and valuation.
import { api } from "/app.js";
import { resizeCanvas, formatTick, thinLabels, downloadPng } from "/kinds/chart_core.js";

const adjustmentNames = {none:"不复权",forward:"前复权",backward:"后复权"};
const windowName = value => value === "all" ? "全部截至当日" : `近${value}年截至当日`;
const cycleNames = {day:"日",week:"周",month:"月",quarter:"季",year:"年"};
const fieldNames = {pe_ttm:"PE(TTM) · 倍",pb:"PB · 倍",pe_ttm_rank:"PE 历史百分位 · %",pb_rank:"PB 历史百分位 · %"};
const columns = ["start","end","open","high","low","close","vol","pe_ttm","pe_ttm_rank","pb","pb_rank","ma5","ma10","ma20","ma60"];
const titles = ["首交易日","末交易日","开","高","低","收","成交量(手)","PE(TTM)","PE百分位(%)","PB","PB百分位(%)","MA5","MA10","MA20","MA60"];
const finite = value => typeof value === "number" && Number.isFinite(value);
const valueText = value => value === null || value === undefined ? "—（缺失）" : String(value);
function element(tag, text, cls) { const node=document.createElement(tag); if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node; }
function download(text, name, type) { const url=URL.createObjectURL(new Blob([text],{type})); const a=element("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),0); }
function metadata(data, visible, indicators, mas) {
  return `公司=${data.company}; 交易周期=${cycleNames[data.settings.cycle]}; 显示=${visible[0]?.start || "无"}至${visible.at(-1)?.end || "无"}; 复权=${adjustmentNames[data.settings.adjustment]}; 分位窗口=${windowName(data.settings.window)}; 指标=${[...indicators,...mas].join(",")}; 数据时点=${data.coverage.end || "无"}; 来源=${JSON.stringify(data.sources)}; 财务口径=独立财务区`;
}
export async function render(container, panel, initial) {
  if(!document.querySelector('link[data-research-chart]')) {const css=element("link");css.rel="stylesheet";css.href="/kinds/research_chart.css";css.dataset.researchChart="1";document.head.append(css);}
  container.classList.add("research-chart");
  let data=initial, hover=null, hoverY=null, first=0, count=data.points.length, loading=0;
  const indicators=new Set(["pe_ttm","pe_ttm_rank"]), mas=new Set(["ma5","ma10","ma20"]);
  const controls=element("div",undefined,"research-controls");container.append(controls);
  const status=element("p",undefined,"research-info");status.setAttribute("role","status");container.append(status);
  const explanation=element("p","交易周期按自然周/月/季/年聚合 OHLC；均线按当前 K 线根数。显示范围只裁剪图形；分位始终用截至当日的有效日样本（严格小于，包含当日，等值不算更小）。复权只改变价格和均线；量与真实每日估值保持原口径。", "research-info");container.append(explanation);
  const selectors={};
  function select(key,label,choices){const wrap=element("label",label);const node=element("select");node.dataset.researchControl=key;node.setAttribute("aria-label",label);for(const [value,text] of choices){const option=element("option",text);option.value=value;node.append(option);}node.value=data.settings[key];node.onchange=()=>reload();selectors[key]=node;wrap.append(node);controls.append(wrap);return node;}
  select("cycle","交易周期",Object.entries(cycleNames));
  select("range","显示范围",[["1","近1年"],["3","近3年"],["5","近5年"],["all","全部"],["custom","自定义"]]);
  select("adjustment","价格复权",[["none","不复权"],["forward","前复权"],["backward","后复权"]]);
  select("window","分位统计窗口",[["3","近3年"],["5","近5年"],["10","近10年"],["all","全部截至该日"]]);
  for(const key of ["start","end"]){const wrap=element("label",key==="start"?"开始日期":"结束日期");const node=element("input");node.type="date";node.value=data.settings[key];node.onchange=()=>{selectors.range.value="custom";reload();};selectors[key]=node;wrap.append(node);controls.append(wrap);}
  async function reload(){const request=++loading; const query=new URLSearchParams({company:data.company});for(const [key,node]of Object.entries(selectors))query.set(key,node.value);status.textContent="正在读取本地历史…";
    try {const result=await api(`/api/v1/research/charts?${query}`);if(request!==loading||!container.isConnected)return;data=result.data;first=0;count=data.points.length;hover=null;const hash=new URLSearchParams(window.location.hash.split("?")[1]||"");for(const [key,value]of query)hash.set(key,value);history.replaceState(null,"",`#${window.location.hash.slice(1).split("?")[0]}?${hash}`);refresh();}catch(error){status.textContent=error.message;}}
  const toggleBar=element("div",undefined,"research-controls");container.append(toggleBar);
  function checkbox(key,label,set){const wrap=element("label",label);const check=element("input");check.type="checkbox";check.checked=set.has(key);check.onchange=()=>{check.checked?set.add(key):set.delete(key);refresh();};wrap.append(check);toggleBar.append(wrap);}
  for(const [key,label]of Object.entries(fieldNames))checkbox(key,label,indicators);
  for(const n of [5,10,20,60])checkbox(`ma${n}`,`MA${n}`,mas);
  const buttons=element("div",undefined,"research-controls");container.append(buttons);
  function button(label,action){const node=element("button",label);node.type="button";node.onclick=action;buttons.append(node);return node;}
  function visible(){return data.points.slice(first,first+count);}
  function zoom(factor){if(!data.points.length)return;const center=first+count/2;count=Math.max(1,Math.min(data.points.length,Math.round(count*factor)));first=Math.max(0,Math.min(data.points.length-count,Math.round(center-count/2)));hover=null;refresh();}
  function pan(direction){first=Math.max(0,Math.min(data.points.length-count,first+direction*Math.max(1,Math.floor(count/4))));hover=null;refresh();}
  button("放大",()=>zoom(.65));button("缩小",()=>zoom(1.5));button("向前平移",()=>pan(-1));button("向后平移",()=>pan(1));button("重置",()=>{first=0;count=data.points.length;hover=null;refresh();});
  const canvas=element("canvas");canvas.dataset.researchCanvas="1";canvas.tabIndex=0;canvas.setAttribute("aria-label","联动行情图；左右键移动光标，+/-缩放，Shift加左右键平移，Home重置");container.append(canvas);
  const readout=element("div",undefined,"research-readout");readout.setAttribute("aria-live","polite");container.append(readout);
  const details=element("details");details.append(element("summary","数值表（完整精度；点击行或键盘读数）"));const tableWrap=element("div",undefined,"research-table");tableWrap.tabIndex=0;details.append(tableWrap);container.append(details);
  const coverage=element("p",undefined,"research-info");container.append(coverage);const update=element("a","更新数据：到数据页核对计划与权限后确认");container.append(update);
  const sourceDetails=element("details");sourceDetails.append(element("summary","数据来源 / 版本 / 缺口"));const sourceText=element("pre");sourceText.style.whiteSpace="pre-wrap";sourceText.style.overflowWrap="anywhere";sourceDetails.append(sourceText);container.append(sourceDetails);
  button("导出图片",()=>{hover=null;paint();downloadPng(canvas,`${data.company}-${data.settings.cycle}-${data.settings.adjustment}`);});
  for(const [separator,ext,type]of [[",","csv","text/csv;charset=utf-8"],["\t","tsv","text/tab-separated-values;charset=utf-8"]])button(`导出 ${ext.toUpperCase()}`,()=>{
    const quote=value=>{const text=value===null||value===undefined?"":String(value);return separator===","?`"${text.replaceAll('"','""')}"`:text.replaceAll("\t"," ").replaceAll("\n"," ");};
    const shown=columns.filter(key=>![...Object.keys(fieldNames),"ma5","ma10","ma20","ma60"].includes(key)||indicators.has(key)||mas.has(key));
    const rows=visible().map(p=>[...shown.map(key=>p[key]),JSON.stringify(p.pe_ttm_rank_info),JSON.stringify(p.pb_rank_info),JSON.stringify(p.source),p.ohlc_reason]);
    const heads=[...shown.map(key=>titles[columns.indexOf(key)]),"PE分位样本/原因","PB分位样本/原因","来源版本","OHLC缺失原因"];
    download("\uFEFF"+[metadata(data,visible(),indicators,mas),heads.map(quote).join(separator),...rows.map(row=>row.map(quote).join(separator))].join("\r\n"),`${data.company}-${data.settings.cycle}.${ext}`,type);
  });
  let geometry={left:76,width:1};
  function showReadout(){const p=visible()[hover];if(!p){readout.textContent="鼠标悬停、点数值表行或聚焦图表使用左右键，核对同一时点。缺失值不画为零。";return;}
    readout.textContent=`${p.start} — ${p.end}${p.ongoing?" · 进行中":""}\n开 ${valueText(p.open)} / 高 ${valueText(p.high)} / 低 ${valueText(p.low)} / 收 ${valueText(p.close)} 元；量 ${valueText(p.vol)} 手\n`+[...indicators].map(key=>`${fieldNames[key]} ${valueText(p[key])}${key.endsWith("_rank")?`；样本 ${p[key.replace("_rank","_rank_info")].samples}，实际 ${p[key.replace("_rank","_rank_info")].start || "无"} 至 ${p.end}；${p[key.replace("_rank","_rank_info")].reason|| (p[key.replace("_rank","_rank_info")].incomplete?"历史不足完整窗口":"完整窗口")}`:""}`).join("\n")+`\n${p.ohlc_reason}\n来源 ${JSON.stringify(p.source)}`;
  }
  function paint(){const points=visible();const fields=["price","vol",...indicators];const chartHeight=250+(fields.length-1)*135;let metaHeight=100;let {ctx,width,height}=resizeCanvas(canvas,container,chartHeight+metaHeight);ctx.fillStyle="#fff";ctx.fillRect(0,0,width,height);ctx.font="11px system-ui";ctx.fillStyle="#333";
    const meta=[`${data.company} · ${cycleNames[data.settings.cycle]} K · ${adjustmentNames[data.settings.adjustment]} · 分位${windowName(data.settings.window)}`, `显示 ${points[0]?.start||"无"} 至 ${points.at(-1)?.end||"无"} · 数据时点 ${data.coverage.end||"无"}`,`指标 ${[...indicators,...mas].join(" / ")} · 量单位：手 · 价格单位：元`,`来源：Tushare 原始日数据；版本/采集时间见数值表。财务口径独立。`];const wrapped=[];meta.forEach(line=>{let part="";for(const ch of line){if(ctx.measureText(part+ch).width>width-16){wrapped.push(part);part=ch;}else part+=ch;}wrapped.push(part);});metaHeight=wrapped.length*20+20;({ctx,width,height}=resizeCanvas(canvas,container,chartHeight+metaHeight));ctx.fillStyle="#fff";ctx.fillRect(0,0,width,height);ctx.font="11px system-ui";ctx.fillStyle="#333";wrapped.forEach((line,i)=>ctx.fillText(line,8,16+i*20));
    const left=76,right=20,plotWidth=width-left-right;geometry={left,width:plotWidth};const x=index=>left+(index+.5)*plotWidth/Math.max(points.length,1);
    let top=metaHeight;
    fields.forEach(field=>{const h=field==="price"?250:135;const plotTop=top+(field==="price"?44:26),plotBottom=top+h-28;let vals=field==="price"?points.flatMap(p=>[p.high,p.low,...[...mas].map(key=>p[key])]):points.map(p=>p[field]);vals=vals.filter(finite);let min=field.endsWith("rank")?0:Math.min(...vals),max=field.endsWith("rank")?100:Math.max(...vals);if(!vals.length){min=0;max=1;}if(field==="vol")min=0;if(max===min){max+=1;min-=1;}const pad=(max-min)*.06;if(!field.endsWith("rank")){min-=pad;max+=pad;}const y=value=>plotBottom-(value-min)/(max-min)*(plotBottom-plotTop);
      ctx.fillStyle="#333";ctx.fillText(field==="price"?"OHLC · 元（红涨/绿跌，平盘横线）":field==="vol"?"成交量 · 手":fieldNames[field],left,top+12);
      for(let step=0;step<=3;step++){const v=min+(max-min)*step/3;ctx.strokeStyle="#e0e0e0";ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(width-right,y(v));ctx.stroke();ctx.fillStyle="#666";ctx.fillText(formatTick(v),4,y(v)+4);}
      const barWidth=Math.max(1,Math.min(14,plotWidth/Math.max(points.length,1)*.65));
      if(field==="price"||field==="vol")points.forEach((p,i)=>{const up=p.close>p.open,flat=p.close===p.open;ctx.strokeStyle=ctx.fillStyle=flat?"#555":up?"#d33838":"#188450";if(field==="vol"){if(finite(p.vol))ctx.fillRect(x(i)-barWidth/2,y(p.vol),barWidth,Math.max(1,y(0)-y(p.vol)));return;}if(![p.open,p.high,p.low,p.close].every(finite))return;ctx.beginPath();ctx.moveTo(x(i),y(p.high));ctx.lineTo(x(i),y(p.low));ctx.stroke();const bh=Math.abs(y(p.open)-y(p.close));if(flat){ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(x(i)-barWidth/2,y(p.close));ctx.lineTo(x(i)+barWidth/2,y(p.close));ctx.stroke();ctx.lineWidth=1;}else if(up){ctx.fillStyle="#fff";ctx.fillRect(x(i)-barWidth/2,Math.min(y(p.open),y(p.close)),barWidth,Math.max(1,bh));ctx.strokeRect(x(i)-barWidth/2,Math.min(y(p.open),y(p.close)),barWidth,Math.max(1,bh));}else ctx.fillRect(x(i)-barWidth/2,Math.min(y(p.open),y(p.close)),barWidth,Math.max(1,bh));});
      const lines=field==="price"?[...mas]:field==="vol"?[]:[field];lines.forEach((key,si)=>{if(field==="price"){ctx.fillStyle=["#2868ce","#b87d12","#8e43a6","#078d83"][si%4];ctx.fillText(key.toUpperCase(),left+si*64,top+28);}ctx.strokeStyle=["#2868ce","#b87d12","#8e43a6","#078d83"][si%4];ctx.beginPath();let begun=false;points.forEach((p,i)=>{if(!finite(p[key])){begun=false;return;}if(!begun)ctx.moveTo(x(i),y(p[key]));else ctx.lineTo(x(i),y(p[key]));begun=true;});ctx.stroke();});
      if(hover!==null&&points[hover]){ctx.strokeStyle="#777";ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(x(hover),plotTop);ctx.lineTo(x(hover),plotBottom);ctx.stroke();if(hoverY!==null&&hoverY>=plotTop&&hoverY<=plotBottom){ctx.beginPath();ctx.moveTo(left,hoverY);ctx.lineTo(width-right,hoverY);ctx.stroke();}ctx.setLineDash([]);}
      const labels=points.map(p=>data.settings.cycle==="year"?p.end.slice(0,4):p.end);thinLabels(ctx,labels,plotWidth).forEach(i=>{const label=labels[i];const textWidth=ctx.measureText(label).width;ctx.fillStyle="#666";ctx.fillText(label,Math.max(left,Math.min(width-right-textWidth,x(i)-textWidth/2)),top+h-8);});if(!points.length){ctx.fillStyle="#777";ctx.fillText(data.empty_hint||"此范围无数据",left,plotTop+24);}top+=h;
    });showReadout();
  }
  function refresh(){selectors.adjustment.querySelectorAll("option").forEach(option=>{option.disabled=option.value!=="none"&&!data.can_adjust;});status.textContent=data.empty_hint||`${visible().length} 根 K 线；隐藏的指标仍可重新开启。${!data.can_adjust?" 缺少完整复权依据，前/后复权不可用。":""}`;
    coverage.textContent=`真实覆盖 ${data.coverage.start||"无"} — ${data.coverage.end||"无"}；有效日行情 ${data.coverage.daily_samples} 条。${data.coverage.incomplete?"所需范围历史不足，不能冒充完整历史。":""} ${data.coverage.calendar_available?`已识别 ${data.gaps.length} 个交易日缺口（详见来源）。`:"缺少交易日历，休市不补零；无法判定未观察日期是停牌或缺数，请更新日历与停牌记录。"}`;
    update.href=data.update_href;sourceText.textContent=data.source_note+"\n"+JSON.stringify({sources:data.sources,gaps:data.gaps},null,2);
    const shown=columns.filter(key=>![...Object.keys(fieldNames),"ma5","ma10","ma20","ma60"].includes(key)||indicators.has(key)||mas.has(key));const table=element("table"),head=element("tr");shown.forEach(key=>head.append(element("th",titles[columns.indexOf(key)])));const thead=element("thead");thead.append(head);table.append(thead);const body=element("tbody");visible().forEach((p,index)=>{const row=element("tr");row.tabIndex=0;row.setAttribute("aria-label",`${p.start}至${p.end} ${p.ohlc_reason}`);row.onclick=()=>{hover=index;paint();};row.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();hover=index;paint();}};shown.forEach(key=>row.append(element("td",valueText(p[key]))));body.append(row);});table.append(body);tableWrap.replaceChildren(table);paint();}
  canvas.onmousemove=e=>{const rect=canvas.getBoundingClientRect();hoverY=e.clientY-rect.top;hover=Math.max(0,Math.min(visible().length-1,Math.floor((e.clientX-rect.left-geometry.left)/geometry.width*visible().length)));paint();};canvas.onmouseleave=()=>{hover=null;hoverY=null;paint();};
  canvas.onkeydown=e=>{if(["ArrowLeft","ArrowRight","+","-","Home"].includes(e.key))e.preventDefault();if(e.key==="Home"){first=0;count=data.points.length;hover=null;refresh();}else if(e.key==="+")zoom(.65);else if(e.key==="-")zoom(1.5);else if(e.key==="ArrowLeft"||e.key==="ArrowRight"){const step=e.key==="ArrowLeft"?-1:1;if(e.shiftKey)pan(step);else{hover=Math.max(0,Math.min(visible().length-1,(hover===null?0:hover)+step));paint();}}};
  canvas.onwheel=e=>{e.preventDefault();zoom(e.deltaY>0?1.2:.8);};let drag=null;canvas.onpointerdown=e=>{drag=e.clientX;canvas.setPointerCapture(e.pointerId);};canvas.onpointerup=e=>{if(drag!==null&&Math.abs(e.clientX-drag)>20){pan(e.clientX<drag?1:-1);}drag=null;};
  if(typeof ResizeObserver!=="undefined"){const observer=new ResizeObserver(()=>{if(!container.isConnected){observer.disconnect();return;}paint();});observer.observe(container);}refresh();
}
