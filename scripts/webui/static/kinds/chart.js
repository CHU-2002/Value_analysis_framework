// kind=chart：读 `options.chart`（type / x / series / toolbar），用 chart_core 画。
// 这里只做「按声明分发 + 工具栏」，绘制质量全部落在 chart_core（AC-7：一处修好、所有图受益）。
import { api, selection, setHash } from "/app.js";
import {
  downloadPng, renderChart, seriesToTsv,
} from "/kinds/chart_core.js";

function toolbar(container, panel, data, chart, onBasisChange) {
  const options = (panel.options && panel.options.chart && panel.options.chart.toolbar) || {};
  const bar = document.createElement("div");
  bar.className = "chart-toolbar";
  const bases = data.bases || [];
  if (options.basis !== false && bases.length > 1) {
    const label = document.createElement("span");
    label.className = "panel-note";
    label.textContent = "口径：";
    const select = document.createElement("select");
    select.dataset.chartBasis = "1";
    for (const basis of bases) {
      const option = document.createElement("option");
      option.value = basis;
      option.textContent = `${data.basis_labels[basis] || basis}（${data.basis_kind[basis] || ""}）`;
      if ((data.basis || "annual") === basis) option.selected = true;
      select.append(option);
    }
    select.onchange = () => onBasisChange(select.value);
    bar.append(label, select);
  } else if (bases.length === 1) {
    // 只有一种口径也要写明是哪一种（`AC-7` 要求在图上标明单位与累计/单期）。
    const only = document.createElement("span");
    only.className = "panel-note";
    only.dataset.chartBasisLabel = bases[0];
    only.textContent = `口径：${data.basis_labels[bases[0]] || bases[0]}`
      + `（${data.basis_kind[bases[0]] || ""}）`;
    bar.append(only);
  } else if (data.basis) {
    const fallback = document.createElement("span");
    fallback.className = "panel-note";
    fallback.dataset.chartBasisLabel = data.basis;
    fallback.textContent = `口径：${(data.basis_labels || {})[data.basis] || data.basis}`;
    bar.append(fallback);
  }
  if (data.unit) {
    const unit = document.createElement("span");
    unit.className = "panel-note";
    unit.dataset.chartUnit = data.unit;
    unit.textContent = `单位：${data.unit}`;
    bar.append(unit);
  }
  const legendNote = document.createElement("span");
  legendNote.className = "panel-note";
  legendNote.textContent = "缺失值断开折线并标空心点，不画成 0";
  bar.append(legendNote);
  if (options.export !== false) {
    const png = document.createElement("button");
    png.type = "button";
    png.dataset.chartExport = "png";
    png.textContent = "导出图片";
    png.onclick = () => {
      const canvases = [...container.querySelectorAll("canvas")];
      const output = document.createElement("canvas");
      output.width = Math.max(320, ...canvases.map(c => c.width));
      output.height = 90 + canvases.reduce((sum, c) => sum + c.height, 0);
      const ctx = output.getContext("2d"); ctx.fillStyle = "white"; ctx.fillRect(0, 0, output.width, output.height);ctx.fillStyle = "#333";ctx.font = "16px sans-serif";ctx.fillText(`${data.company || "未知公司"} · 财务${data.basis || "annual"} · ${data.unit || ""}`, 12, 22);ctx.fillText(`显示期次与指标见各图，来源数据时点见期次；${JSON.stringify(data.meta || {}).slice(0, 150)}`, 12, 52);let top = 90;canvases.forEach(c => {ctx.drawImage(c, 0, top); top += c.height;});downloadPng(output, `${data.company || "company"}-${panel.id}-${data.basis || "annual"}`);
    };
    const tsv = document.createElement("button");
    tsv.type = "button";
    tsv.dataset.chartExport = "tsv";
    tsv.textContent = "复制数据";
    tsv.onclick = async () => {
      try {
        await navigator.clipboard.writeText(seriesToTsv(chart.current ? chart.current.labels : data.labels || [], chart.current ? chart.current.series : data.series || []));
        tsv.textContent = "已复制";
      } catch (_) {
        tsv.textContent = "请手动复制";
      }
    };
    bar.append(png, tsv);
  }
  container.append(bar);
}

export async function render(container, panel, data) {
  if (data && data.kind === "research_timeline") {
    const research = await import("/kinds/research_chart.js");
    return research.render(container, panel, data);
  }
  if (!data) {
    container.textContent = "暂无数据";
    return;
  }
  const chartData = {
    labels: data.labels || [],
    series: data.series || [],
    type: (panel.options && panel.options.chart && panel.options.chart.type) || "line",
    unit: data.unit,
    basisLabel: (data.basis_labels || {})[data.basis] || "",
    basisKind: (data.basis_kind || {})[data.basis] || "",
  };
  const chartHost = document.createElement("div");
  container.append(chartHost);
  const chart = renderChart(chartHost, chartData);
  const reload = async (basis) => {
    if (!selection.company) return;
    const params = new URLSearchParams({ company: selection.company, basis });
    window.location.hash = `charts?${params.toString()}`;
  };
  toolbar(container, panel, data, chart, reload);
  // Financial time periods are independent of trading cycles. Split scales prevent
  // revenue from flattening profit; every control also updates the exact-value table.
  const options = document.createElement("div");
  options.className = "chart-toolbar";
  const visible = new Set((data.series || []).map((_, i) => i));
  const begin = document.createElement("select"); begin.setAttribute("aria-label", "财务开始期次");
  const end = document.createElement("select"); end.setAttribute("aria-label", "财务结束期次");
  (data.labels || []).forEach((label, index) => {
    for (const select of [begin, end]) { const option = document.createElement("option"); option.value = index; option.textContent = label; select.append(option); }
  });
  end.value = Math.max(0, (data.labels || []).length - 1);
  options.append("财务期次范围：", begin, "至", end);
  const tableDetails = document.createElement("details");
  const summary = document.createElement("summary"); summary.textContent = "财务数值表 / 缺失原因"; tableDetails.append(summary);
  const tableWrap = document.createElement("div"); tableWrap.className = "panel-table"; tableWrap.style.overflow = "auto"; tableWrap.tabIndex = 0; tableDetails.append(tableWrap);
  const exportData = document.createElement("button"); exportData.type = "button"; exportData.textContent = "导出当前财务 CSV";
  let current = chartData;
  function update() {
    const from = Number(begin.value), to = Number(end.value);
    current = {...chartData, labels: (data.labels || []).slice(from, to + 1), series: (data.series || []).filter((_, index) => visible.has(index)).map(s => ({...s, values: s.values.slice(from, to + 1)}))};
    chart.current = current;
    chartHost.replaceChildren();
    // Independent plots are the default even for a modest scale gap.
    current.series.forEach(series => { const region = document.createElement("div"); chartHost.append(region); renderChart(region, {...current, series: [series]}); });
    if (!current.series.length) { chartHost.textContent = "指标已隐藏，可重新开启"; }
    const table = document.createElement("table"); const head = document.createElement("tr");
    ["期次", ...current.series.map(s => s.name)].forEach(name => {const th=document.createElement("th");th.textContent=name;head.append(th);}); table.append(head);
    current.labels.forEach((label, index) => { const row=document.createElement("tr");[label,...current.series.map(s=>s.values[index] ?? "—（缺失，不能换算/未提供）")].forEach(value=>{const cell=document.createElement("td");cell.textContent=String(value);row.append(cell);});table.append(row); });
    tableWrap.replaceChildren(table);
    if (data.missing_reasons) { const reasons=document.createElement("pre");reasons.textContent=JSON.stringify(data.missing_reasons,null,2);reasons.style.whiteSpace="pre-wrap";tableWrap.append(reasons); }
  }
  (data.series || []).forEach((series,index)=>{ const label=document.createElement("label");const box=document.createElement("input");box.type="checkbox";box.checked=true;box.onchange=()=>{box.checked?visible.add(index):visible.delete(index);update();};label.append(box,series.name);options.append(label); });
  begin.onchange=end.onchange=update;
  exportData.onclick=()=>{ const quote=x=>`"${String(x ?? "").replaceAll('"','""')}"`;const metadata=`公司=${data.company || "未知"};财务口径=${data.basis};期间=${current.labels[0] || "无"}至${current.labels.at(-1) || "无"};单位=${data.unit};指标=${current.series.map(s=>s.name).join("/")};来源=${JSON.stringify(data.meta || {})}`;const rows=[metadata,["期次",...current.series.map(s=>s.name)].map(quote).join(","),...current.labels.map((label,i)=>[label,...current.series.map(s=>s.values[i])].map(quote).join(","))];const link=document.createElement("a");const url=URL.createObjectURL(new Blob(["\uFEFF"+rows.join("\r\n")],{type:"text/csv;charset=utf-8"}));link.href=url;link.download=`${data.company || "company"}-${data.basis || "annual"}-financial.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),0); };
  options.append(exportData); container.append(options, tableDetails); update();
  if (data.source_note) {const source=document.createElement("p");source.className="panel-note";source.textContent=data.source_note;container.append(source);}
  if (data.empty_hint) {
    const note = document.createElement("p");
    note.className = "panel-note";
    note.textContent = data.empty_hint;
    container.append(note);
  }
}

export { seriesToTsv, downloadPng };
