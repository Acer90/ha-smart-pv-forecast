import { escapeHTML, infoDetails } from "./i18n.js";

const COLORS = ["#00897b", "#2684ff", "#b35bdb", "#e28a18", "#d95270", "#63863e"];
const seriesVisibility = new WeakMap();
const CHART = { width: 640, height: 240, left: 48, right: 12, top: 14, bottom: 30 };
const fmt = (value, language, decimals = 0) => !Number.isFinite(value)
  ? "—" : new Intl.NumberFormat(language, { maximumFractionDigits: decimals }).format(value);

const chartX = hour => CHART.left + hour / 24 * (CHART.width - CHART.left - CHART.right);
const chartY = (value, maximum) => CHART.height - CHART.bottom
  - value / maximum * (CHART.height - CHART.top - CHART.bottom);
const chartMaximum = value => Math.max(100, Math.ceil(value / 100) * 100);

function scaleForSeries(series) {
  let maximum = 0;
  for (const item of series.filter(item => !item.stacked && item.visible !== false)) {
    for (const value of item.values) {
      if (Number.isFinite(value)) maximum = Math.max(maximum, value);
    }
  }
  const stacks = series.filter(item => item.stacked);
  const visibleStacks = stacks.filter(item => item.visible !== false);
  for (let hour = 0; hour < 24; hour++) {
    if (stacks.some(item => !Number.isFinite(item.values[hour]))) continue;
    const total = visibleStacks.reduce((sum, item) => sum + item.values[hour], 0);
    maximum = Math.max(maximum, total);
  }
  return chartMaximum(maximum);
}

export function chartSVG(series, maximum, unit, label, chartID) {
  const grid = [0, maximum / 2, maximum].map((value, index) =>
    `<line class="grid" data-grid-level="${index}" x1="${CHART.left}" x2="${CHART.width - CHART.right}" y1="${chartY(value, maximum)}" y2="${chartY(value, maximum)}"/>
     <text class="chart-y-label" data-grid-level="${index}" x="${CHART.left - 8}" y="${chartY(value, maximum) + 4}" text-anchor="end">${Math.round(value)}</text>`
  ).join("");
  const ticks = [0, 4, 8, 12, 16, 20, 24].map((hour) =>
    `<text x="${chartX(hour)}" y="${CHART.height - 8}" text-anchor="middle">${String(hour).padStart(2, "0")}</text>`
  ).join("");
  const curves = series.map((series, index) => {
    const key = series.key || `series-${index}`;
    if (series.stacked) return "";
    const values = series.values.map(value => Number.isFinite(value) ? String(value) : "").join(",");
    let d = "", connected = false;
    series.values.forEach((value, hour) => {
      if (value === null || value === undefined || !Number.isFinite(value)) { connected = false; return; }
      d += `${connected ? "L" : "M"}${chartX(hour + 0.5)},${chartY(value, maximum)} `;
      connected = true;
    });
    const dots = series.values.map((value, hour) => Number.isFinite(value) && (series.measured
      || (!Number.isFinite(series.values[hour - 1]) && !Number.isFinite(series.values[hour + 1])))
      ? `<circle data-point-hour="${hour}" data-point-value="${value}" cx="${chartX(hour + 0.5)}" cy="${chartY(value, maximum)}" r="3" fill="${series.color}"/>` : "").join("");
    return `<g class="series-plot" data-series-chart="${chartID}" data-series="${key}" data-values="${values}" style="${series.visible === false ? "display:none" : ""}">
      <path d="${d}" fill="none" stroke="${series.color}" stroke-width="${chartID === "total" ? 1.5 : 2.8}" ${series.dashed ? 'stroke-dasharray="6 4"' : ""}/>${dots}</g>`;
  }).join("");
  const stacks = series.filter(item => item.stacked);
  const bars = Array.from({ length: 24 }, (_, hour) => {
    if (!stacks.length || stacks.some(item => !Number.isFinite(item.values[hour]))) return "";
    let bottomValue = 0;
    return stacks.map((item, index) => {
      const value = item.values[hour], topValue = bottomValue + value;
      const bar = `<g class="series-plot" data-series-chart="${chartID}" data-series="${item.key}" style="${item.visible === false ? "display:none" : ""}">
        <rect class="measured-bar" data-inverter="${index}" data-bar-hour="${hour}" data-value="${value}"
        x="${chartX(hour + 0.15)}" y="${chartY(topValue, maximum)}" width="${chartX(0.7) - chartX(0)}"
        height="${chartY(bottomValue, maximum) - chartY(topValue, maximum)}" fill="${item.color}" fill-opacity="0.75"/></g>`;
      bottomValue = topValue;
      return bar;
    }).join("");
  }).join("");
  const hits = Array.from({ length: 24 }, (_, hour) =>
    `<rect class="hit" data-chart="${chartID}" data-hour="${hour}" tabindex="0"
       aria-label="${String(hour).padStart(2, "0")}:00" x="${chartX(hour)}" y="${CHART.top}"
       width="${chartX(1) - chartX(0)}" height="${CHART.height - CHART.top - CHART.bottom}"/>`
  ).join("");
  return `<svg class="chart" data-chart-id="${chartID}" data-chart-maximum="${maximum}" viewBox="0 0 ${CHART.width} ${CHART.height}" aria-label="${escapeHTML(label)}">
    <text x="${CHART.left}" y="10">${unit}</text>${grid}${ticks}${bars}${curves}${hits}</svg>`;
}

function updateChartScale(svg) {
  let visibleMaximum = 0;
  const stackedTotals = Array(24).fill(0);
  for (const plot of svg.querySelectorAll(".series-plot")) {
    if (plot.style.display === "none") continue;
    const bars = plot.querySelectorAll(".measured-bar");
    if (bars.length) {
      for (const bar of bars) {
        stackedTotals[Number(bar.dataset.barHour)] += Number(bar.dataset.value);
      }
      continue;
    }
    for (const value of (plot.dataset.values || "").split(",")) {
      if (value !== "" && Number.isFinite(Number(value))) {
        visibleMaximum = Math.max(visibleMaximum, Number(value));
      }
    }
  }
  visibleMaximum = Math.max(visibleMaximum, ...stackedTotals);
  const maximum = chartMaximum(visibleMaximum);
  svg.dataset.chartMaximum = String(maximum);

  for (const line of svg.querySelectorAll(".grid[data-grid-level]")) {
    const value = maximum * Number(line.dataset.gridLevel) / 2;
    const y = chartY(value, maximum);
    line.setAttribute("y1", String(y));
    line.setAttribute("y2", String(y));
  }
  for (const label of svg.querySelectorAll(".chart-y-label[data-grid-level]")) {
    const value = maximum * Number(label.dataset.gridLevel) / 2;
    label.textContent = String(Math.round(value));
    label.setAttribute("y", String(chartY(value, maximum) + 4));
  }

  for (const plot of svg.querySelectorAll(".series-plot[data-values]")) {
    let d = "", connected = false;
    (plot.dataset.values || "").split(",").forEach((rawValue, hour) => {
      if (rawValue === "" || !Number.isFinite(Number(rawValue))) {
        connected = false;
        return;
      }
      d += `${connected ? "L" : "M"}${chartX(hour + 0.5)},${chartY(Number(rawValue), maximum)} `;
      connected = true;
    });
    plot.querySelector("path")?.setAttribute("d", d);
  }
  for (const point of svg.querySelectorAll("[data-point-hour][data-point-value]")) {
    point.setAttribute("cy", String(chartY(Number(point.dataset.pointValue), maximum)));
  }

  for (let hour = 0; hour < 24; hour++) {
    const bars = [...svg.querySelectorAll(`.measured-bar[data-bar-hour="${hour}"]`)]
      .sort((a, b) => Number(a.dataset.inverter) - Number(b.dataset.inverter));
    let bottom = 0;
    for (const bar of bars) {
      if (bar.closest(".series-plot").style.display === "none") continue;
      const top = bottom + Number(bar.dataset.value);
      bar.setAttribute("y", String(chartY(top, maximum)));
      bar.setAttribute("height", String(chartY(bottom, maximum) - chartY(top, maximum)));
      bottom = top;
    }
  }
}

function seriesButton(key, label, color, dashed, chartID, visible, t) {
  return `<button class="series-toggle" type="button" data-series-chart="${chartID}" data-series="${key}"
    data-visible="${visible}" data-label="${escapeHTML(label)}" data-hide-label="${escapeHTML(t.hideSeries)}"
    data-show-label="${escapeHTML(t.showSeries)}" aria-pressed="${visible}"
    aria-label="${escapeHTML(`${visible ? t.hideSeries : t.showSeries}: ${label}`)}">
    ${color ? `<i class="swatch ${dashed ? "dashed" : ""}" style="--color:${color}"></i>` : ""}
    <span>${escapeHTML(label)}</span></button>`;
}

export function weatherFetchInfo(data, t, language) {
  const fetched = Object.entries(data.source_fetch_times || {});
  if (!fetched.length) return "";
  const format = new Intl.DateTimeFormat(language, {
    timeZone: data.timezone, dateStyle: "short", timeStyle: "short",
  });
  const sources = fetched.map(([source, timestamp]) =>
    `${escapeHTML(source)}: ${escapeHTML(format.format(new Date(timestamp)))}`).join(" · ");
  return `<p class="muted">${escapeHTML(t.weatherFetchedAt)}: ${sources}</p>`;
}

export function renderForecastDays(days, t, language, settings = {}, compact = false, selectedDate = null) {
  if (!days?.length) return "";
  days = days.slice(0, settings.open_meteo_forecast_days ?? 7);
  const low = settings.forecast_low_kwh ?? 15, high = settings.forecast_high_kwh ?? 25;
  const maximum = Math.max(high, 5, Math.ceil(Math.max(...days.map(day => (day.shaded_wh || 0) / 1000)) / 5) * 5);
  const weekday = new Intl.DateTimeFormat(language, { weekday: "short", timeZone: "UTC" });
  const yieldFormat = new Intl.NumberFormat(language, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const rows = days.map((day, index) => {
    const value = day.shaded_wh === null ? null : day.shaded_wh / 1000;
    const partial = value !== null && day.covered_hours < day.expected_hours;
    const red = value ? Math.min(value, low) / value * 100 : 0;
    const orange = value ? Math.max(0, Math.min(value, high) - low) / value * 100 : 0;
    const green = value ? Math.max(0, value - high) / value * 100 : 0;
    const label = weekday.format(new Date(`${day.date}T12:00:00Z`));
    const caption = `${partial ? "≥ " : ""}${value === null ? "—" : yieldFormat.format(value)} kWh`;
    const selected = selectedDate === day.date;
    return `${selectedDate ? `<button type="button" class="forecast-row ${index === 0 ? "forecast-today" : ""} ${selected ? "selected" : ""}"
        data-forecast-date="${escapeHTML(day.date)}" aria-pressed="${selected}"
        aria-label="${escapeHTML(`${day.date}: ${caption}${partial ? ` · ${t.forecastPartial}` : value === null ? ` · ${t.forecastMissing}` : ""}`)}">`
      : `<div class="forecast-row ${index === 0 ? "forecast-today" : ""}">`}
      <div class="forecast-label"><strong>${escapeHTML(label)}</strong>${!compact ? `${index === 0 ? `<small>${t.today}</small>` : ""}
        <small>${escapeHTML(day.date)}</small>` : ""}</div>
      <div class="forecast-track" ${selectedDate ? "aria-hidden=\"true\"" : `role="img" aria-label="${escapeHTML(`${day.date}: ${caption}${partial ? ` · ${t.forecastPartial}` : value === null ? ` · ${t.forecastMissing}` : ""}`)}"`}>
        <div class="forecast-fill" style="width:${value === null ? 0 : 100 * value / maximum}%;--delay:${index * 65}ms">
          <span class="forecast-red" style="width:${red}%"></span><span class="forecast-orange" style="width:${orange}%"></span>
          <span class="forecast-green" style="width:${green}%"></span>
        </div>
      </div><div class="forecast-value"><strong>${caption}</strong>
        ${partial ? `<small>${t.forecastPartial}</small>` : value === null ? `<small>${t.forecastMissing}</small>` : ""}</div>
    ${selectedDate ? "</button>" : "</div>"}`;
  }).join("");
  return `<section class="card forecast-overview" aria-label="${t.forecastDays}">
    <div class="row spread"><h2>${t.forecastDays}</h2>${infoDetails(t.help, t.forecastDaysHint)}</div>
    <div class="forecast-rows">${rows}</div>
    <p class="forecast-key"><span class="forecast-red"></span>${t.forecastRed}: 0–${fmt(low, language, 1)} kWh
      <span class="forecast-orange"></span>${t.forecastOrange}: ${fmt(low, language, 1)}–${fmt(high, language, 1)} kWh
      <span class="forecast-green"></span>${t.forecastGreen}: &gt; ${fmt(high, language, 1)} kWh</p>
  </section>`;
}

const measurements = array => [
  ...(array.strings || []),
  ...(array.inverters || []),
  ...(array.measurement ? [{ ...array.measurement, total: true }] : []),
];
const measurementLabel = (string, t) => string.total ? t.measuredTotal
  : `${string.inverter ? t.measuredInverter : t.measuredString}: ${string.label}`;

export function powerCard(array, id, color, t, language) {
  if (id === "total") color = "#1976d2";
  const format = (v, decimals = 0) => fmt(v, language, decimals);
  const strings = measurements(array);
  const stringColors = strings.map((_, i) => `hsl(${(i * 137.508 + 30) % 360} 65% 42%)`);
  const series = [
    { key: "max-potential", values: array.hours.map((h) => h.max_potential_w), color: "#e28a18", dashed: true, visible: false },
    { key: "baseline", values: array.hours.map((h) => h.baseline_w), color: "#8999a6", dashed: true },
    { key: "shaded", values: array.hours.map((h) => h.shaded_w), color },
    ...strings.map((string, index) => ({ string, index })).filter(({ string }) => !string.total).map(({ string, index }) => ({
      key: `measurement-${index}`,
      values: string.inverter ? string.values.map((value, hour) =>
        Number.isFinite(array.measurement?.values[hour]) ? value : null) : string.values,
      color: stringColors[strings.indexOf(string)], measured: true, stacked: !!string.inverter,
    })),
  ];
  const scale = scaleForSeries(series);
  const rows = array.hours.map((h) =>
    `<tr><td>${String(h.hour).padStart(2, "0")}:00–${String(h.hour + 1).padStart(2, "0")}:00</td>
     <td>${format(h.max_potential_w)} W</td><td>${format(h.baseline_w)} W</td><td>${format(h.shaded_w)} W</td>
     <td>${format(h.reduction_percent, 1)} %</td>${strings.map(string => `<td>${format(string.values[h.hour])} W</td>`).join("")}</tr>`
  ).join("");
  const formatKwh = (wh) => Number.isFinite(wh) ? `${format(wh / 1000, 2)} kWh` : "— kWh";
  const measurement = id === "total" ? array.measurement : null;
  const measuredHours = measurement?.values.map((value, hour) => {
    const minutes = measurement.minutes[hour];
    return Number.isFinite(value) && Number.isFinite(minutes) && minutes > 0
      ? { value, minutes } : null;
  }).filter(Boolean) || [];
  const measuredEnergy = measuredHours.length
    ? measuredHours.reduce((total, hour) => total + hour.value * hour.minutes / 60, 0)
    : null;
  const measuredMinutes = measuredHours.reduce((total, hour) => total + hour.minutes, 0);
  const elapsedMinutes = measurement?.elapsedMinutes?.reduce((total, minutes) =>
    total + (Number.isFinite(minutes) ? minutes : 0), 0) || 0;
  const measurementPartial = measuredEnergy !== null && measuredMinutes < elapsedMinutes - 1e-6;
  return `<article class="card chart-card">
      <div class="row spread"><h2>${escapeHTML(array.name)}</h2></div>
      <div class="stats">
        <div class="stat">${id === "total"
          ? `<strong>${measuredEnergy === null ? "—" : `${measurementPartial ? "≥ " : ""}${format(measuredEnergy / 1000, 2)}`} kWh</strong>
             <span>${t.measuredYield}</span><small>${formatKwh(array.max_potential_wh)} · ${t.maxPotential}</small>`
          : `<strong>${formatKwh(array.max_potential_wh)}</strong><span>${t.maxPotential}</span>`}</div>
        <div class="stat"><strong>${formatKwh(array.baseline_wh)}</strong><span>${t.baseline}</span>
          <small>${formatKwh(array.cloud_loss_wh)} · ${t.cloudLoss}</small></div>
        <div class="stat"><strong>${formatKwh(array.shaded_wh)}</strong><span>${t.shaded}</span>
          <small>${formatKwh(array.shading_loss_wh)} · ${format(array.reduction_percent, 1)} % · ${t.shadingLoss}</small></div>
      </div>
      <div class="legend">${series.slice(0, 3).map(item =>
        seriesButton(item.key, item.key === "max-potential" ? t.maxPotential
          : item.key === "baseline" ? t.baseline : t.shaded,
        item.color, item.dashed, id, item.visible !== false, t)).join("")}
        ${strings.map((string, index) => string.total
          ? `<span>${escapeHTML(measurementLabel(string, t))}</span>`
          : seriesButton(`measurement-${index}`, measurementLabel(string, t), stringColors[index],
            false, id, true, t)).join("")}</div>
      ${chartSVG(series, scale, "W", array.name, id)}
      <p class="chart-tooltip" data-tooltip="${id}" role="status" hidden></p>
      ${infoDetails(t.measurementHelp, `${t.tapChart}${id !== "total" ? `\n\n${t.arrayMeasurementHint}${!array.strings?.length ? `\n\n${t.arrayStringsMissing}` : ""}` : ""}`)}
      <details><summary>${t.hourly}</summary><div class="table-scroll">
        <table><thead><tr><th>${t.hour}</th><th>${t.maxPotential}</th><th>${t.baseline}</th><th>${t.shaded}</th><th>${t.shadingLoss}</th>
        ${strings.map(string => `<th>${escapeHTML(measurementLabel(string, t))}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>
      </div></details>
    </article>`;
}

export function renderAnalysis(data, t, language, settings = {}, selectedDate = data?.date) {
  if (!data || !data.arrays.length) return `<div class="card"><p>${t.noData}</p></div>`;
  const cards = data.arrays.map((array, index) => powerCard(array, index, COLORS[index % COLORS.length], t, language)).join("");
  const total = data.total ? powerCard({ ...data.total, name: t.all }, "total", "#00897b", t, language) : "";
  const totalStatus = data.totalMeasurementStatus || (data.total?.measurement && !data.total.measurement.values.some(value => value !== null) ? "no_data" : null);
  return `${renderForecastDays(data.forecast_days, t, language, settings, false, selectedDate)}<div class="analysis-head">
      <div class="row spread"><h2>${data.date} · ${t.localTime} ${escapeHTML(data.timezone)}</h2>
        <span class="badge">${t.sources}: ${data.sources.map(escapeHTML).join(" + ")}</span></div>
      ${infoDetails(t.measurementHelp, `${t.analysisHint}${data.arrays.some(array => array.strings?.length) ? `\n\n${t.historyHint}` : ""}`)}
      ${data.history_until ? `<p class="muted">${t.historyUntil}: ${escapeHTML(new Intl.DateTimeFormat(language, {
        timeZone: data.timezone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
      }).format(new Date(data.history_until)))}</p>` : ""}
      ${weatherFetchInfo(data, t, language)}
      ${data.historyError ? `<p role="alert">${escapeHTML(data.historyError)}</p>` : ""}
      ${data.historyMissing ? `<p role="status">${t.historyMissing}</p>` : ""}
    </div>
    <section class="total-analysis">${total}
      ${infoDetails(t.measurementHelp, t.totalMeasurementHint)}
      ${totalStatus ? `<p role="status">${t[`total_measurement_${totalStatus}`]}</p>` : ""}
    </section><div class="charts">${cards}</div>`;
}

export function wireForecastSelection(root, onSelect) {
  const select = (event) => {
    const button = event.target.closest("[data-forecast-date]");
    if (button && root.contains(button)) onSelect(button.dataset.forecastDate);
  };
  root.addEventListener("click", select);
  return () => root.removeEventListener("click", select);
}

export function showChartTooltip(root, data, t, language, chart, hour) {
    const label = `${String(hour).padStart(2, "0")}:00–${String(hour + 1).padStart(2, "0")}:00`;
    const array = chart === "total" ? data.total : data.arrays[Number(chart)];
    let message = `${t.maxPotential}: ${fmt(array.hours[hour].max_potential_w, language)} W · ${t.baseline}: ${fmt(array.hours[hour].baseline_w, language)} W · ${t.shaded}: ${fmt(array.hours[hour].shaded_w, language)} W · ${t.shadingLoss}: ${fmt(array.hours[hour].reduction_percent, language, 1)} %`;
    message += measurements(array).map(string =>
      ` · ${measurementLabel(string, t)}: ${fmt(string.values[hour], language)} W`
    ).join("");
    const tooltip = root.querySelector(`[data-tooltip="${chart}"]`);
    if (tooltip.hidden) tooltip.hidden = false;
    if (tooltip.dataset.selectedHour !== String(hour)) tooltip.dataset.selectedHour = String(hour);
    const text = `${label} · ${message}`;
    if (tooltip.textContent !== text) tooltip.textContent = text;
}

export function wireChartInteraction(root, data, t, language) {
  let visibility = seriesVisibility.get(root);
  if (!visibility) seriesVisibility.set(root, visibility = new Map());
  const applyVisibility = (chart, key, visible) => {
    root.querySelectorAll("[data-series-chart]").forEach((element) => {
      if (element.dataset.seriesChart !== chart || element.dataset.series !== key) return;
      if (element.classList.contains("series-plot")) element.style.display = visible ? "" : "none";
      else if (element.classList.contains("series-toggle")) {
        element.setAttribute("aria-pressed", String(visible));
        element.setAttribute("aria-label", `${visible ? element.dataset.hideLabel : element.dataset.showLabel}: ${element.dataset.label}`);
      }
    });
    const svg = [...root.querySelectorAll(".chart")].find(item => item.dataset.chartId === chart);
    if (svg) updateChartScale(svg);
  };
  root.querySelectorAll(".series-toggle").forEach((button) => {
    const { seriesChart: chart, series: key } = button.dataset;
    const stateKey = `${chart}:${key}`;
    if (!visibility.has(stateKey)) visibility.set(stateKey, button.dataset.visible === "true");
    applyVisibility(chart, key, visibility.get(stateKey));
  });
  const toggle = (event) => {
    const button = event.target.closest(".series-toggle");
    if (!button) return;
    const { seriesChart: chart, series: key } = button.dataset;
    const stateKey = `${chart}:${key}`;
    const visible = !visibility.get(stateKey);
    visibility.set(stateKey, visible);
    applyVisibility(chart, key, visible);
  };
  const show = (event) => {
    const target = event.target.closest("[data-hour]");
    if (!target) return;
    showChartTooltip(root, data, t, language, target.dataset.chart, Number(target.dataset.hour));
  };
  root.addEventListener("click", toggle);
  root.addEventListener("pointermove", show);
  root.addEventListener("click", show);
  root.addEventListener("focusin", show);
  return () => {
    root.removeEventListener("click", toggle);
    root.removeEventListener("pointermove", show);
    root.removeEventListener("click", show);
    root.removeEventListener("focusin", show);
  };
}
