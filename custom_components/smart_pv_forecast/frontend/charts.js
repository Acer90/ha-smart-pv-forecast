import { escapeHTML, infoDetails } from "./i18n.js";

const COLORS = ["#00897b", "#2684ff", "#b35bdb", "#e28a18", "#d95270", "#63863e"];
const fmt = (value, language, decimals = 0) => value === null || value === undefined
  ? "—" : new Intl.NumberFormat(language, { maximumFractionDigits: decimals }).format(value);

export function chartSVG(series, maximum, unit, label, chartID) {
  const width = 640, height = 240, left = 48, right = 12, top = 14, bottom = 30;
  const x = (hour) => left + hour / 24 * (width - left - right);
  const y = (value) => height - bottom - value / maximum * (height - top - bottom);
  const grid = [0, maximum / 2, maximum].map((value) =>
    `<line class="grid" x1="${left}" x2="${width - right}" y1="${y(value)}" y2="${y(value)}"/>
     <text x="${left - 8}" y="${y(value) + 4}" text-anchor="end">${Math.round(value)}</text>`
  ).join("");
  const ticks = [0, 4, 8, 12, 16, 20, 24].map((hour) =>
    `<text x="${x(hour)}" y="${height - 8}" text-anchor="middle">${String(hour).padStart(2, "0")}</text>`
  ).join("");
  const curves = series.map((series) => {
    if (series.stacked) return "";
    let d = "", connected = false;
    series.values.forEach((value, hour) => {
      if (value === null || value === undefined || !Number.isFinite(value)) { connected = false; return; }
      d += `${connected ? "L" : "M"}${x(hour + 0.5)},${y(value)} `;
      connected = true;
    });
    const dots = series.values.map((value, hour) => Number.isFinite(value) && (series.measured
      || (!Number.isFinite(series.values[hour - 1]) && !Number.isFinite(series.values[hour + 1])))
      ? `<circle cx="${x(hour + 0.5)}" cy="${y(value)}" r="3" fill="${series.color}"/>` : "").join("");
    return `<path d="${d}" fill="none" stroke="${series.color}" stroke-width="${chartID === "total" ? 1.5 : 2.8}" ${series.dashed ? 'stroke-dasharray="6 4"' : ""}/>${dots}`;
  }).join("");
  const stacks = series.filter(item => item.stacked);
  const bars = Array.from({ length: 24 }, (_, hour) => {
    if (!stacks.length || stacks.some(item => !Number.isFinite(item.values[hour]))) return "";
    let bottomValue = 0;
    return stacks.map((item, index) => {
      const value = item.values[hour], topValue = bottomValue + value;
      const bar = `<rect class="measured-bar" data-inverter="${index}" data-bar-hour="${hour}"
        x="${x(hour + 0.15)}" y="${y(topValue)}" width="${x(0.7) - x(0)}"
        height="${y(bottomValue) - y(topValue)}" fill="${item.color}" fill-opacity="0.75"/>`;
      bottomValue = topValue;
      return bar;
    }).join("");
  }).join("");
  const hits = Array.from({ length: 24 }, (_, hour) =>
    `<rect class="hit" data-chart="${chartID}" data-hour="${hour}" tabindex="0"
       aria-label="${String(hour).padStart(2, "0")}:00" x="${x(hour)}" y="${top}"
       width="${x(1) - x(0)}" height="${height - top - bottom}"/>`
  ).join("");
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" aria-label="${escapeHTML(label)}">
    <text x="${left}" y="10">${unit}</text>${grid}${ticks}${bars}${curves}${hits}</svg>`;
}

export function renderForecastDays(days, t, language, settings = {}, compact = false) {
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
    return `<div class="forecast-row ${index === 0 ? "forecast-today" : ""}">
      <div class="forecast-label"><strong>${escapeHTML(label)}</strong>${!compact ? `${index === 0 ? `<small>${t.today}</small>` : ""}
        <small>${escapeHTML(day.date)}</small>` : ""}</div>
      <div class="forecast-track" role="img" aria-label="${escapeHTML(`${day.date}: ${caption}${partial ? ` · ${t.forecastPartial}` : value === null ? ` · ${t.forecastMissing}` : ""}`)}">
        <div class="forecast-fill" style="width:${value === null ? 0 : 100 * value / maximum}%;--delay:${index * 65}ms">
          <span class="forecast-red" style="width:${red}%"></span><span class="forecast-orange" style="width:${orange}%"></span>
          <span class="forecast-green" style="width:${green}%"></span>
        </div>
      </div><div class="forecast-value"><strong>${caption}</strong>
        ${partial ? `<small>${t.forecastPartial}</small>` : value === null ? `<small>${t.forecastMissing}</small>` : ""}</div>
    </div>`;
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
    const maximum = Math.max(100, ...array.hours.map((h) => h.baseline_w || 0),
      ...strings.flatMap(string => string.values.map(value => value || 0)),
      ...Array.from({ length: 24 }, (_, hour) => (array.inverters || [])
        .reduce((sum, inverter) => sum + (inverter.values[hour] || 0), 0)));
    const scale = Math.ceil(maximum / 100) * 100;
    const series = [
      { values: array.hours.map((h) => h.baseline_w), color: "#8999a6", dashed: true },
      { values: array.hours.map((h) => h.shaded_w), color },
      ...strings.filter(string => !string.total).map(string => ({
        values: string.inverter ? string.values.map((value, hour) =>
          Number.isFinite(array.measurement?.values[hour]) ? value : null) : string.values,
        color: stringColors[strings.indexOf(string)], measured: true, stacked: !!string.inverter,
      })),
    ];
    const rows = array.hours.map((h) =>
      `<tr><td>${String(h.hour).padStart(2, "0")}:00–${String(h.hour + 1).padStart(2, "0")}:00</td>
       <td>${format(h.baseline_w)} W</td><td>${format(h.shaded_w)} W</td>
       <td>${format(h.reduction_percent, 1)} %</td>${strings.map(string => `<td>${format(string.values[h.hour])} W</td>`).join("")}</tr>`
    ).join("");
    return `<article class="card chart-card">
      <div class="row spread"><h2>${escapeHTML(array.name)}</h2></div>
      <div class="stats">
        <div class="stat"><strong>${format(array.baseline_wh / 1000, 2)} kWh</strong><span>${t.baseline}</span></div>
        <div class="stat"><strong>${format(array.shaded_wh / 1000, 2)} kWh</strong><span>${t.shaded}</span></div>
        <div class="stat"><strong>${format(array.reduction_percent, 1)} %</strong><span>${t.loss}</span></div>
      </div>
      <div class="legend"><span><i class="swatch dashed"></i>${t.baseline}</span>
        <span><i class="swatch" style="--color:${color}"></i>${t.shaded}</span>
        ${strings.map((string, i) => `<span>${!string.total ? `<i class="swatch" style="--color:${stringColors[i]}"></i>` : ""}${escapeHTML(measurementLabel(string, t))}</span>`).join("")}</div>
      ${chartSVG(series, scale, "W", array.name, id)}
      <p class="chart-tooltip" data-tooltip="${id}" role="status" hidden></p>
      ${infoDetails(t.measurementHelp, `${t.tapChart}${id !== "total" ? `\n\n${t.arrayMeasurementHint}${!array.strings?.length ? `\n\n${t.arrayStringsMissing}` : ""}` : ""}`)}
      <details><summary>${t.hourly}</summary><div class="table-scroll">
        <table><thead><tr><th>${t.hour}</th><th>${t.baseline}</th><th>${t.shaded}</th><th>${t.loss}</th>
        ${strings.map(string => `<th>${escapeHTML(measurementLabel(string, t))}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>
      </div></details>
    </article>`;
}

export function renderAnalysis(data, t, language, settings = {}) {
  if (!data || !data.arrays.length) return `<div class="card"><p>${t.noData}</p></div>`;
  const cards = data.arrays.map((array, index) => powerCard(array, index, COLORS[index % COLORS.length], t, language)).join("");
  const total = data.total ? powerCard({ ...data.total, name: t.all }, "total", "#00897b", t, language) : "";
  const totalStatus = data.totalMeasurementStatus || (data.total?.measurement && !data.total.measurement.values.some(value => value !== null) ? "no_data" : null);
  return `${renderForecastDays(data.forecast_days, t, language, settings)}<div class="analysis-head">
      <div class="row spread"><h2>${data.date} · ${t.localTime} ${escapeHTML(data.timezone)}</h2>
        <span class="badge">${t.sources}: ${data.sources.map(escapeHTML).join(" + ")}</span></div>
      ${infoDetails(t.measurementHelp, `${t.analysisHint}${data.arrays.some(array => array.strings?.length) ? `\n\n${t.historyHint}` : ""}`)}
      ${data.history_until ? `<p class="muted">${t.historyUntil}: ${escapeHTML(new Intl.DateTimeFormat(language, {
        timeZone: data.timezone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
      }).format(new Date(data.history_until)))}</p>` : ""}
      ${data.historyError ? `<p role="alert">${escapeHTML(data.historyError)}</p>` : ""}
      ${data.historyMissing ? `<p role="status">${t.historyMissing}</p>` : ""}
    </div>
    <section class="total-analysis">${total}
      ${infoDetails(t.measurementHelp, t.totalMeasurementHint)}
      ${totalStatus ? `<p role="status">${t[`total_measurement_${totalStatus}`]}</p>` : ""}
    </section><div class="charts">${cards}</div>`;
}

export function showChartTooltip(root, data, t, language, chart, hour) {
    const label = `${String(hour).padStart(2, "0")}:00–${String(hour + 1).padStart(2, "0")}:00`;
    const array = chart === "total" ? data.total : data.arrays[Number(chart)];
    let message = `${t.baseline}: ${fmt(array.hours[hour].baseline_w, language)} W · ${t.shaded}: ${fmt(array.hours[hour].shaded_w, language)} W · ${fmt(array.hours[hour].reduction_percent, language, 1)} %`;
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
  const show = (event) => {
    const target = event.target.closest("[data-hour]");
    if (!target) return;
    showChartTooltip(root, data, t, language, target.dataset.chart, Number(target.dataset.hour));
  };
  root.addEventListener("pointermove", show);
  root.addEventListener("click", show);
  root.addEventListener("focusin", show);
  return () => {
    root.removeEventListener("pointermove", show);
    root.removeEventListener("click", show);
    root.removeEventListener("focusin", show);
  };
}
