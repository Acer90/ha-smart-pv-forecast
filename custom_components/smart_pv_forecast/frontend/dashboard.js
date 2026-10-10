import { TEXT, escapeHTML, infoDetails } from "./i18n.js";
import { renderForecastDays, powerCard, wireChartInteraction, showChartTooltip } from "./charts.js";
import { loadSiteHistory } from "./history.js";
import { updateRenderedContent, resetRenderedContent } from "./render.js";

const caches = new WeakMap();
const isPower = type => type === "custom:smart-pv-power-card";
const languageOf = hass => hass?.language?.startsWith("de") ? "de" : "en";
const labels = {
  de: {
    yield: "Solarprognose · Ertragsprognose", power: "Solarprognose · Leistungsdiagramm",
    site: "Standort", auto: "Automatisch bei einem Standort", array: "Diagramm",
    total: "Gesamtanlage", choose: "Bitte einen Standort im Karteneditor auswählen.",
    missing: "Der konfigurierte Standort oder das Array ist nicht mehr vorhanden.",
    noData: "Noch keine Prognosedaten verfügbar.", loading: "Lädt Solarprognose …",
    compact: "Kompaktmodus",
  },
  en: {
    yield: "Solar forecast · yield forecast", power: "Solar forecast · power chart",
    site: "Site", auto: "Automatic for a single site", array: "Chart",
    total: "Whole installation", choose: "Please select a site in the card editor.",
    missing: "The configured site or array no longer exists.",
    noData: "No forecast data available yet.", loading: "Loading solar forecast …",
    compact: "Compact mode",
  },
};

function cached(hass, key, fetch) {
  const owner = hass.connection || hass;
  let cache = caches.get(owner);
  if (!cache) caches.set(owner, cache = new Map());
  const previous = cache.get(key);
  if (previous && Date.now() - previous.time < 45000) return previous.promise;
  const item = { time: Date.now(), promise: null };
  item.promise = Promise.resolve().then(fetch).catch(error => {
    if (cache.get(key) === item) cache.delete(key);
    throw error;
  });
  cache.set(key, item);
  return item.promise;
}

export function dashboardSites(hass) {
  return cached(hass, "sites", () => hass.callWS({ type: "smart_pv_forecast/dashboard_sites" }));
}

export async function dashboardData(hass, config) {
  const sites = await dashboardSites(hass);
  const site = config.entry_id ? sites.find(s => s.entry_id === config.entry_id)
    : sites.length === 1 ? sites[0] : null;
  const text = labels[languageOf(hass)];
  if (!site) throw Error(config.entry_id ? text.missing : text.choose);
  const data = await cached(hass, `analysis:${site.entry_id}:${site.revision}`, () =>
    hass.callWS({ type: "smart_pv_forecast/dashboard_analysis", entry_id: site.entry_id }));
  if (data.revision !== site.revision) throw Error(TEXT[languageOf(hass)].pending);
  if (!isPower(config.type)) return { site, data };
  let history;
  try {
    history = await cached(hass, `history:${site.entry_id}:${site.revision}:${data.date}`, () =>
      loadSiteHistory(hass, site, data));
  } catch (error) {
    return { site, data: { ...data, historyError: `${TEXT[languageOf(hass)].historyError}: ${error.message || error}` } };
  }
  return {
    site,
    data: {
      ...data,
      arrays: data.arrays.map((array, index) => ({ ...array, strings: history.strings[index] })),
      total: data.total ? { ...data.total, measurement: history.total, inverters: history.inverters } : null,
      totalMeasurementStatus: history.totalStatus,
      history_until: history.until,
    },
  };
}

const stylesheet = `<link rel="stylesheet" href="${new URL("./styles.css", import.meta.url)}">
  <style>
    :host { height:auto; background:transparent; }
    ha-card { display:block; overflow:hidden; }
    .card { border:0; border-radius:0; margin:0; box-shadow:none; }
    .message, .card-footer { padding:8px 12px; }
    .message:empty { display:none; }
    .error { color:var(--error-color,#c62828); }
    .forecast-rows { grid-template-columns:65px minmax(0,1fr) max-content; gap:8px; }
    .forecast-track { min-width:20px; }
    .compact .forecast-overview { padding:8px 10px; }
    .compact .forecast-overview > .row, .compact .forecast-key { display:none; }
    .compact .forecast-rows { grid-template-columns:38px minmax(0,1fr) max-content; gap:5px 8px; margin:0; }
    .compact .forecast-track { height:15px; }
    .compact .forecast-label, .compact .forecast-value {
      font-family:var(--ha-font-family-body, var(--paper-font-body1_-_font-family, inherit));
      font-size:var(--ha-font-size-m, 14px);
      font-weight:var(--ha-font-weight-normal, 400);
    }
    .compact .forecast-label strong, .compact .forecast-value strong { font-weight:inherit; }
    .compact .forecast-label small { display:none; }
    .compact .forecast-value small { font-size:12px; }
    .compact-toggle { display:flex; align-items:center; gap:8px; min-height:44px; cursor:pointer; }
    .compact-toggle input { width:20px; height:20px; }
  </style>`;

class SolarCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `${stylesheet}<ha-card></ha-card>`;
    this.generation = 0;
    this.onVisible = () => { if (!document.hidden) this.refresh(); };
  }
  setConfig(config) {
    if (!config || !["custom:smart-pv-yield-card", "custom:smart-pv-power-card"].includes(config.type)
      || (config.entry_id !== undefined && typeof config.entry_id !== "string")
      || (config.compact !== undefined && typeof config.compact !== "boolean")
      || (config.array !== undefined && config.array !== "total"
        && (!Number.isInteger(config.array) || config.array < 0))) throw Error("Invalid Solarprognose card configuration");
    if (JSON.stringify(this.config) === JSON.stringify(config)) return;
    this.config = { ...config };
    this.shadowRoot.querySelector("ha-card").classList.toggle("compact", !isPower(config.type) && config.compact === true);
    this.result = null;
    resetRenderedContent(this.shadowRoot.querySelector("ha-card"));
    this.generation++;
    if (this._hass && this.isConnected) this.refresh();
  }
  set hass(hass) {
    const changed = this._hass?.connection !== hass.connection
      || languageOf(this._hass) !== languageOf(hass);
    this._hass = hass;
    if (this.isConnected && this.config && (!this.started || changed)) this.refresh();
  }
  get hass() { return this._hass; }
  connectedCallback() {
    document.addEventListener("visibilitychange", this.onVisible);
    this.clock = setInterval(() => { if (!document.hidden) this.refresh(); }, 60000);
    this.refresh();
  }
  disconnectedCallback() {
    this.generation++;
    this.started = false;
    clearInterval(this.clock);
    this.unwire?.();
    document.removeEventListener("visibilitychange", this.onVisible);
  }
  getCardSize() { return isPower(this.config?.type) ? 7 : this.config?.compact ? 3 : 4; }
  getGridOptions() { return { columns: 12, rows: isPower(this.config?.type) ? 8 : 5 }; }
  async refresh() {
    if (!this.config || !this._hass || !this.isConnected) return;
    this.started = true;
    const generation = ++this.generation;
    const language = languageOf(this._hass), t = TEXT[language], text = labels[language];
    if (!this.result) this.shadowRoot.querySelector("ha-card").innerHTML = `<p class="message" role="status">${text.loading}</p>`;
    try {
      const result = await dashboardData(this._hass, this.config);
      if (generation !== this.generation || !this.isConnected) return;
      const { data, site } = result;
      let content;
      if (!isPower(this.config.type)) {
        if (!data.forecast_days?.some(day => day.shaded_wh !== null)) throw Error(text.noData);
        content = renderForecastDays(data.forecast_days, t, language, site.settings, this.config.compact === true);
      } else {
        const index = this.config.array ?? "total";
        const array = index === "total" ? data.total : data.arrays[index];
        if (!array) throw Error(text.missing);
        content = powerCard(index === "total" ? { ...array, name: t.all } : array,
          index, "#00897b", t, language);
        const missing = data.historyError ? null : index === "total"
          ? data.totalMeasurementStatus || (!array.measurement?.values.some(v => v !== null) ? "no_data" : null)
          : array.strings?.some(string => !string.values.some(v => v !== null)) ? "strings" : null;
        content += `<div class="card-footer">${infoDetails(t.measurementHelp, index === "total" ? t.totalMeasurementHint : t.historyHint)}
          ${data.historyError ? `<p role="alert">${escapeHTML(data.historyError)}</p>` : ""}
          ${missing ? `<p role="status">${missing === "strings" ? t.historyMissing : t[`total_measurement_${missing}`]}</p>` : ""}
          ${data.history_until ? `<p class="muted">${escapeHTML(data.date)} · ${t.historyUntil}: ${escapeHTML(new Intl.DateTimeFormat(language, {
            timeZone: data.timezone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
          }).format(new Date(data.history_until)))}</p>` : ""}</div>`;
      }
      this.result = result;
      this.shadowRoot.querySelector(".message")?.remove();
      updateRenderedContent(this.shadowRoot.querySelector("ha-card"), content);
      this.unwire?.();
      this.unwire = isPower(this.config.type) ? wireChartInteraction(this.shadowRoot, data, t, language) : null;
      const tooltip = this.shadowRoot.querySelector(".chart-tooltip:not([hidden])");
      if (tooltip) showChartTooltip(this.shadowRoot, data, t, language,
        tooltip.dataset.tooltip, Number(tooltip.dataset.selectedHour));
      else {
        const focused = this.shadowRoot.activeElement;
        if (focused?.dataset.hour !== undefined) showChartTooltip(this.shadowRoot, data, t, language,
          focused.dataset.chart, Number(focused.dataset.hour));
      }
    } catch (error) {
      if (generation !== this.generation || !this.isConnected) return;
      const message = `${t.error}: ${error.message || error}`;
      const card = this.shadowRoot.querySelector("ha-card");
      let status = card.querySelector(".message");
      if (!status) { status = document.createElement("p"); status.className = "message"; card.prepend(status); }
      status.classList.add("error"); status.setAttribute("role", "alert"); status.textContent = message;
    }
  }
}

class SolarEditor extends HTMLElement {
  constructor() { super(); this.attachShadow({ mode: "open" }); this.generation = 0; }
  setConfig(config) { this.config = { ...config }; this.update(); }
  set hass(hass) { this._hass = hass; if (!this.sites || languageOf(hass) !== this.language) this.update(); }
  async update() {
    if (!this.config || !this._hass) return;
    const generation = ++this.generation;
    this.language = languageOf(this._hass);
    const text = labels[this.language];
    try {
      this.sites = await dashboardSites(this._hass);
      if (generation !== this.generation) return;
      const site = this.sites.find(s => s.entry_id === this.config.entry_id)
        || (!this.config.entry_id && this.sites.length === 1 ? this.sites[0] : null);
      this.shadowRoot.innerHTML = `${stylesheet}<div class="card">
        <label>${text.site}<select id="site"><option value="">${text.auto}</option>
          ${this.config.entry_id && !site ? `<option selected value="${escapeHTML(this.config.entry_id)}">${text.missing}</option>` : ""}
          ${this.sites.map(s => `<option value="${escapeHTML(s.entry_id)}" ${s.entry_id === this.config.entry_id ? "selected" : ""}>${escapeHTML(s.name)}</option>`).join("")}
        </select></label>
        ${isPower(this.config.type) ? `<label>${text.array}<select id="array"><option value="total">${text.total}</option>
          ${Number.isInteger(this.config.array) && !site?.pv_arrays[this.config.array] ? `<option selected value="${this.config.array}">${text.missing}</option>` : ""}
          ${(site?.pv_arrays || []).map((array, index) => `<option value="${index}" ${index === this.config.array ? "selected" : ""}>${escapeHTML(array.name)}</option>`).join("")}
        </select></label>` : `<label class="compact-toggle"><input id="compact" type="checkbox" ${this.config.compact ? "checked" : ""}>${text.compact}</label>`}
      </div>`;
      this.shadowRoot.getElementById("site").onchange = event => {
        this.config = { ...this.config, entry_id: event.target.value };
        if (isPower(this.config.type)) this.config.array = "total";
        this.changed(); this.update();
      };
      const array = this.shadowRoot.getElementById("array");
      if (array) array.onchange = event => {
        this.config = { ...this.config, array: event.target.value === "total" ? "total" : Number(event.target.value) };
        this.changed();
      };
      const compact = this.shadowRoot.getElementById("compact");
      if (compact) compact.onchange = event => {
        this.config = { ...this.config, compact: event.target.checked };
        this.changed();
      };
    } catch (error) {
      if (generation !== this.generation) return;
      this.shadowRoot.innerHTML = `${stylesheet}<p class="message error" role="alert">${escapeHTML(`${TEXT[this.language].error}: ${error.message || error}`)}</p>`;
    }
  }
  changed() {
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this.config }, bubbles: true, composed: true }));
  }
}

class YieldCard extends SolarCard {
  static getConfigElement() { return document.createElement("smart-pv-card-editor"); }
  static getStubConfig() { return { type: "custom:smart-pv-yield-card" }; }
}
class PowerCard extends SolarCard {
  static getConfigElement() { return document.createElement("smart-pv-card-editor"); }
  static getStubConfig() { return { type: "custom:smart-pv-power-card", array: "total" }; }
}
customElements.define("smart-pv-card-editor", SolarEditor);
customElements.define("smart-pv-yield-card", YieldCard);
customElements.define("smart-pv-power-card", PowerCard);
window.customCards ||= [];
window.customCards.push(
  { type: "smart-pv-yield-card", name: "Solarprognose · Ertragsprognose", description: "Mehrtagesprognose mit farbigen Ertragsbalken", preview: true },
  { type: "smart-pv-power-card", name: "Solarprognose · Leistungsdiagramm", description: "Gesamtanlage oder Array mit einzelnen PV-String-Messkurven", preview: true },
);
