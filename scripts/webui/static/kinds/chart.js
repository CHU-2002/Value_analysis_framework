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
    png.onclick = () => downloadPng(chart.canvas, panel.id);
    const tsv = document.createElement("button");
    tsv.type = "button";
    tsv.dataset.chartExport = "tsv";
    tsv.textContent = "复制数据";
    tsv.onclick = async () => {
      try {
        await navigator.clipboard.writeText(seriesToTsv(data.labels || [], data.series || []));
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
  if (!data) {
    container.textContent = "暂无数据";
    return;
  }
  const chart = renderChart(container, {
    labels: data.labels || [],
    series: data.series || [],
    type: (panel.options && panel.options.chart && panel.options.chart.type) || "line",
    unit: data.unit,
    basisLabel: (data.basis_labels || {})[data.basis] || "",
    basisKind: (data.basis_kind || {})[data.basis] || "",
  });
  const reload = async (basis) => {
    if (!selection.company) return;
    const params = new URLSearchParams({ company: selection.company, basis });
    window.location.hash = `charts?${params.toString()}`;
  };
  toolbar(container, panel, data, chart, reload);
  if (data.empty_hint) {
    const note = document.createElement("p");
    note.className = "panel-note";
    note.textContent = data.empty_hint;
    container.append(note);
  }
}

export { seriesToTsv, downloadPng };
