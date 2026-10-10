import { TEXT, escapeHTML, infoDetails } from "./i18n.js";
import { renderAnalysis, wireChartInteraction, showChartTooltip } from "./charts.js";
import { updateRenderedContent } from "./render.js";
import { areaCenter, surfaceProfile, localPoint, geographicPoint } from "./geometry.js";
import { Scene3D } from "./scene3d.js";
import { loadGoogleMaps } from "./google-map.js";
import { loadSiteHistory } from "./history.js";
export { surfaceProfile } from "./geometry.js";

const MAX_MAP_ZOOM = 22;
const MAX_TILE_ZOOM = 19;

export function project(point, zoom) {
  const size = 256 * 2 ** zoom;
  const sin = Math.sin(Math.max(-85, Math.min(85, Number(point.latitude))) * Math.PI / 180);
  return { x: (Number(point.longitude) + 180) / 360 * size,
    y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * size };
}
export function unproject(point, zoom) {
  const size = 256 * 2 ** zoom;
  return {
    latitude: Math.max(-85, Math.min(85, Math.atan(Math.sinh(Math.PI * (1 - 2 * point.y / size))) * 180 / Math.PI)),
    longitude: ((point.x / size * 360) % 360 + 360) % 360 - 180,
  };
}

class SmartPVConfigurator extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.zoom = 19;
    this.sites = [];
    this.selected = null;
    this.tab = "analysis";
    this.mode = "pan";
    this.mapView = "2d";
    this.mapLayer = "osm";
    this.mapLayerGeneration = 0;
    this.points = [];
    this.draftHeight = 5;
    this.dirty = false;
    this.busy = false;
    this.tiles = new Map();
    this.pointers = new Map();
    this.loadGeneration = 0;
    this.analysisGeneration = 0;
    this.previewGeneration = 0;
    this.previewLive = true;
    this.previewHeight = null;
    this.previewWhen = "";
    this.showShadows = true;
    this._beforeUnload = (event) => {
      if (this.dirty || this.mode === "draw") { event.preventDefault(); event.returnValue = ""; }
    };
    this._refreshAnalysis = () => {
      if (!document.hidden && this.isConnected && this.tab === "analysis" && this.site
        && !this.busy && !this.dirty && !this.analysisLoading) this.loadAnalysis();
    };
  }

  set hass(value) {
    this._hass = value;
    const language = value.language?.startsWith("de") ? "de" : "en";
    const changed = language !== this.language;
    this.language = language;
    this.t = TEXT[language];
    if (!this.ready && this.isConnected) this.initialize();
    else if (changed && this.ready) this.render();
    else if (this.ready) this.updateMeasuredStates();
  }
  get hass() { return this._hass; }

  connectedCallback() {
    window.addEventListener("beforeunload", this._beforeUnload);
    document.addEventListener("visibilitychange", this._refreshAnalysis);
    if (this._hass && !this.ready) this.initialize();
    else if (this.ready) {
      this.observeMap();
      this.setTab("analysis");
    }
    this.previewClock = setInterval(() => {
      if (this.previewLive && this.tab === "map" && this.site) this.queuePreview(0);
    }, 60000);
    this.analysisClock = setInterval(this._refreshAnalysis, 60000);
    if (this.ready && this.site) this.queuePreview(0);
  }
  disconnectedCallback() {
    window.removeEventListener("beforeunload", this._beforeUnload);
    document.removeEventListener("visibilitychange", this._refreshAnalysis);
    this.observer?.disconnect();
    this.scene3d?.dispose();
    clearTimeout(this.analysisTimer);
    clearTimeout(this.previewTimer);
    clearInterval(this.previewClock);
    clearInterval(this.analysisClock);
    this.unwireCharts?.();
    this.previewGeneration++;
    this.loadGeneration++;
    this.pointers.clear();
    this.mapLayerGeneration++;
  }
  async initialize() { this.ready = true; this.render(); await this.load(); }

  status(message, error = false) {
    this.message = message;
    this.messageError = error;
    const node = this.shadowRoot.getElementById("status");
    node.textContent = message;
    node.classList.toggle("error", error);
  }

  async load() {
    if ((this.dirty || this.mode === "draw") && !window.confirm(this.t.discard)) return;
    this.busy = true; this.status(this.t.loading); this.updateButtons();
    try {
      const sites = await this._hass.callWS({ type: "smart_pv_forecast/sites" });
      const entryID = this.site?.entry_id;
      this.sites = sites;
      this.useSite(sites.find((s) => s.entry_id === entryID) || sites[0]);
      this.status(this.site ? "" : this.t.empty);
    } catch (error) { this.status(`${this.t.error}: ${error.message || error}`, true); }
    finally { this.busy = false; this.renderLists(); this.updateButtons(); }
    if (this.site) await this.loadAnalysis();
  }

  useSite(site) {
    this.mapLayer = "osm"; this.googleMap = null; this.mapLayerGeneration++;
    this.loadGeneration++;
    clearTimeout(this.analysisTimer);
    this.site = site ? structuredClone(site) : null;
    if (this.site) for (const array of this.site.pv_arrays) array.areas ||= [];
    if (this.site) {
      this.site.inverters ||= [];
      this.site.settings ||= { damping_morning: 0, damping_evening: 0, horizon: 0,
        open_meteo_forecast_days: 7, daily_yield_sensor_days: 4, inverter_size: null };
      this.site.settings.forecast_low_kwh ??= 15;
      this.site.settings.forecast_high_kwh ??= 25;
    }
    this.selected = null; this.mode = "pan"; this.points = [];
    this.dirty = false; this.analysis = null; this.analysisMessage = "";
    this.analysisLoading = false;
    this.preview = null; this.previewMessage = "";
    this.previewLive = true; this.previewHeight = null; this.previewWhen = "";
    clearTimeout(this.previewTimer); this.previewGeneration++;
    this.message = ""; this.messageError = false;
    if (site) this.center = project(site, this.zoom);
    this.render();
    if (this.site) this.queuePreview(0);
  }

  render() {
    this.googleMap = null; this.mapLayer = this.site?.google_maps_api_key ? "google" : "osm"; this.mapLayerGeneration++;
    this.observer?.disconnect();
    this.scene3d?.dispose();
    this.tiles.clear();
    const t = this.t;
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="${new URL("./styles.css", import.meta.url)}">
      <header>
        <div class="brand"><h1>${t.title}</h1><span id="summary" class="muted"></span>
          <span id="dirty" class="badge unsaved" hidden>${t.dirty}</span></div>
        <nav class="header-navigation"><label id="site-picker" ${this.sites.length > 1 ? "" : "hidden"}>${t.site}<select id="sites">${this.sites.map((s) =>
        `<option value="${escapeHTML(s.entry_id)}" ${s.entry_id === this.site?.entry_id ? "selected" : ""}>${escapeHTML(s.name)}</option>`
      ).join("")}</select></label>
        <div class="tabs" role="tablist">
          <button id="tab-analysis" role="tab" aria-controls="analysis-view">${t.analysisTab}</button>
          <button id="tab-map" role="tab" aria-controls="map-view">${t.mapTab}</button>
          <button id="tab-settings" role="tab" aria-controls="settings-view">${t.settingsTab}</button>
        </div></nav><div class="header-actions">
          <button id="save" class="primary">${t.save}</button>
        </div>
      </header><p id="status" role="status" aria-live="polite"></p>
      <main id="map-view" class="workspace" role="tabpanel">
        <aside><section class="card"><h2>${t.arrays}</h2><div id="arrays" class="list"></div>
          <button id="add-array" class="wide">${t.addArray}</button></section>
          <section class="card"><h2>${t.objects}</h2><div id="objects" class="list"></div>
          <button id="add-object" class="wide">${t.addObject}</button></section>
        </aside>
        <section class="map-column">
          <section class="preview-controls" aria-label="${t.shadowPreview}">
            <button id="preview-live" aria-pressed="${this.previewLive}">${t.previewLive}</button>
            <label class="preview-date">${t.previewDate}<input id="preview-date" type="datetime-local"
              min="2000-01-01T00:00" max="2100-12-31T23:59" required value="${escapeHTML(this.previewWhen)}"></label>
            <label class="preview-height">${t.previewHeight}<input id="preview-height" type="number" min="0" max="1000" step="any" required></label>
            <button id="preview-auto" aria-pressed="${this.previewHeight === null}">${t.previewAuto}</button>
            <label class="preview-toggle"><input id="preview-visible" type="checkbox" ${this.showShadows ? "checked" : ""}>${t.previewVisible}</label>
            ${infoDetails(t.help, `${t.previewPlaneHint}\n\n${t.previewDraft}`)}
            <p id="preview-info" role="status" aria-live="polite"></p>
          </section>
          <div id="draw-toolbar" class="draw-toolbar" hidden>
            <p id="mode"></p><label id="draft-height-label">${t.pointHeight}<input id="draft-height" type="number" min="0.01" max="1000" step="0.01" value="${this.draftHeight}"></label>
            <button id="finish" class="primary"></button><button id="undo">${t.undo}</button><button id="cancel">${t.cancel}</button>
          </div>
          <div class="scene-toolbar">
            <button id="view-2d">${t.view2d}</button><button id="view-3d">${t.view3d}</button>
            <label>${t.mapLayer}<select id="map-layer"><option value="osm">${t.osmMap}</option><option value="google">${t.googleSatellite}</option></select></label>
            <span id="scene-actions" class="row" hidden>${["orbit", "move", "height", "rotate"].map((mode) =>
              `<button data-scene-mode="${mode}">${t[`scene_${mode}`]}</button>`).join("")}
              <button id="scene-fit">${t.sceneFit}</button><button id="scene-plus" aria-label="${t.zoomIn}">+</button><button id="scene-minus" aria-label="${t.zoomOut}">−</button></span>
            <div id="scene-help" hidden>${infoDetails(t.sceneModel, `${t.sceneHint}\n\n${t.sceneModelHint}`)}</div>
          </div>
          <div class="map-frame"><div id="map" aria-label="${t.map}">
            <div id="tiles"></div><div id="google-map" hidden></div><svg id="geometry" xmlns="http://www.w3.org/2000/svg"></svg>
            <div class="zoom"><button id="zoom-in" aria-label="${t.zoomIn}">+</button><button id="zoom-out" aria-label="${t.zoomOut}">−</button></div>
            <svg id="sun-compass" class="sun-compass" viewBox="0 0 100 120" role="img" aria-label="${t.sunPosition}" hidden></svg>
            <div class="credit">© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a></div>
          </div><div id="scene" hidden><svg id="scene-geometry" xmlns="http://www.w3.org/2000/svg" aria-label="${t.view3d}"></svg></div>
          <section id="inspector" aria-label="${t.select}"></section></div>
          <div class="map-help"><p id="tile-error" role="status"></p>${infoDetails(t.mapHelp, `${t.hint}\n\n${t.heights}`)}</div>
        </section>
      </main>
      <section id="analysis-view" class="analysis" role="tabpanel" hidden>
        <p id="analysis-note" class="muted"></p>
        <p id="analysis-status" role="status"></p><div id="charts"></div>
      </section>
      <section id="settings-view" class="analysis" role="tabpanel" hidden>
        <div class="row spread analysis-head"><h2>${t.inverters}</h2><button id="add-inverter" class="primary">${t.addInverter}</button></div>
        ${infoDetails(t.inverters, t.inverterHint)}<div id="inverters" class="charts"></div>
        <section id="forecast-settings" class="card" style="margin-top:16px"></section>
        ${infoDetails(t.sources, t.sourceHint)}
      </section>`;
    const on = (id, action) => { this.shadowRoot.getElementById(id).onclick = action; };
    on("save", () => this.save());
    on("add-array", () => this.addArray()); on("add-object", () => this.startDraw({ kind: "object" }));
    on("tab-map", () => this.setTab("map")); on("tab-analysis", () => this.setTab("analysis"));
    on("tab-settings", () => this.setTab("settings")); on("add-inverter", () => this.addInverter());
    on("finish", () => this.finishDrawing());
    on("undo", () => { this.points.pop(); this.drawMap(); }); on("cancel", () => this.cancelDrawing());
    on("zoom-in", () => this.changeZoom(1)); on("zoom-out", () => this.changeZoom(-1));
    on("view-2d", () => this.setMapView("2d")); on("view-3d", () => this.setMapView("3d"));
    on("scene-fit", () => this.scene3d.fit());
    on("scene-plus", () => this.scene3d.zoom(1.25)); on("scene-minus", () => this.scene3d.zoom(0.8));
    this.shadowRoot.getElementById("map-layer").onchange = (event) => this.setMapLayer(event.target.value);
    on("preview-live", () => { this.previewLive = true; this.queuePreview(0); });
    on("preview-auto", () => { this.previewHeight = null; this.queuePreview(0); });
    this.shadowRoot.getElementById("preview-date").onchange = (event) => {
      this.previewLive = false; this.previewWhen = event.target.value; this.queuePreview(0);
    };
    this.shadowRoot.getElementById("preview-height").oninput = (event) => {
      this.previewHeight = event.target.validity.valid ? Number(event.target.value) : NaN;
      this.queuePreview();
    };
    this.shadowRoot.getElementById("preview-visible").onchange = (event) => {
      this.showShadows = event.target.checked; this.drawMap();
    };
    this.shadowRoot.getElementById("draft-height").oninput = (event) => {
      if (event.target.validity.valid) this.draftHeight = Number(event.target.value);
    };
    this.shadowRoot.getElementById("sites").onchange = async (event) => {
      if ((this.dirty || this.mode === "draw") && !window.confirm(this.t.discard)) {
        event.target.value = this.site.entry_id; return;
      }
      this.useSite(this.sites.find((s) => s.entry_id === event.target.value));
      await this.loadAnalysis();
    };
    this.map = this.shadowRoot.getElementById("map");
    this.scene3d = new Scene3D(this.shadowRoot.getElementById("scene-geometry"), this.t, {
      select: (selection) => this.select(selection),
      edit: (selection, action) => this.editScene(selection, action),
    });
    for (const button of this.shadowRoot.querySelectorAll("[data-scene-mode]")) button.onclick = () => {
      this.scene3d.mode = button.dataset.sceneMode; this.updateSceneControls(); this.drawMap();
    };
    this.setMapView(this.mapView);
    this.map.onpointerdown = (event) => this.pointerDown(event);
    this.map.onpointermove = (event) => this.pointerMove(event);
    this.map.onpointerup = (event) => this.pointerEnd(event);
    this.map.onpointercancel = (event) => this.pointerEnd(event, true);
    this.map.addEventListener("wheel", (event) => {
      event.preventDefault(); this.changeZoom(event.deltaY < 0 ? 1 : -1, event);
    }, { passive: false });
    this.renderLists(); this.renderInspector(); this.renderSettings(); this.setTab(this.tab, false);
    this.updateButtons(); this.status(this.message || "", this.messageError);
    this.renderCharts(); this.observeMap();
    if (this.mapLayer === "google" && this.site) this.setMapLayer("google");
  }

  setTab(tab, refresh = true) {
    this.tab = tab;
    for (const name of ["map", "analysis", "settings"]) {
      this.shadowRoot.getElementById(`tab-${name}`).classList.toggle("active", name === tab);
      this.shadowRoot.getElementById(`tab-${name}`).setAttribute("aria-selected", String(name === tab));
      this.shadowRoot.getElementById(`${name}-view`).hidden = name !== tab;
    }
    if (tab === "map") {
      this.renderInspector();
      requestAnimationFrame(() => this.drawMap());
      if (refresh && this.site) this.queuePreview(0);
    }
    else if (tab === "analysis" && refresh && this.site) this.loadAnalysis();
  }
  setMapView(view) {
    if (view === "3d" && this.mode !== "pan") return;
    this.mapView = view;
    const root = this.shadowRoot;
    root.getElementById("map").hidden = view === "3d";
    root.getElementById("scene").hidden = view !== "3d";
    root.getElementById("scene-actions").hidden = view !== "3d";
    root.getElementById("scene-help").hidden = view !== "3d";
    for (const name of ["2d", "3d"]) root.getElementById(`view-${name}`).setAttribute("aria-pressed", String(view === name));
    this.updateSceneControls(); this.drawMap();
  }
  async setMapLayer(layer) {
    const generation = ++this.mapLayerGeneration, root = this.shadowRoot;
    this.mapLayer = layer;
    this.map.classList.toggle("google-layer", layer === "google");
    root.getElementById("tiles").hidden = layer !== "osm";
    root.getElementById("google-map").hidden = layer !== "google";
    root.querySelector(".credit").hidden = layer !== "osm";
    root.getElementById("map-layer").value = layer;
    root.getElementById("tile-error").textContent = "";
    if (layer === "osm") { this.drawMap(); return; }
    if (!this.site.google_maps_api_key?.trim()) {
      root.getElementById("google-map").hidden = true;
      root.getElementById("tile-error").textContent = this.t.googleKeyMissing; return;
    }
    root.getElementById("tile-error").textContent = this.t.loading;
    window.gm_authFailure = () => {
      if (this.isConnected && this.mapLayer === "google") root.getElementById("tile-error").textContent = this.t.googleAuthError;
    };
    try {
      const maps = await loadGoogleMaps(this.site.google_maps_api_key.trim());
      if (generation !== this.mapLayerGeneration || !this.isConnected) return;
      if (!this.googleMap) {
        const center = unproject(this.center, this.zoom);
        this.googleMap = new maps.Map(root.getElementById("google-map"), {
          center: { lat: center.latitude, lng: center.longitude }, zoom: this.zoom, mapTypeId: "satellite",
          tilt: 0, heading: 0, disableDefaultUI: true, gestureHandling: "none",
          keyboardShortcuts: false, minZoom: 3, maxZoom: 19,
        });
      }
      root.getElementById("tile-error").textContent = ""; this.drawMap();
    } catch (error) {
      if (generation === this.mapLayerGeneration) root.getElementById("tile-error").textContent = `${this.t.error}: ${error.message}`;
    }
  }
  updateSceneControls() {
    const item = this.item(), single = item && (this.selected.kind === "area" || (this.selected.kind === "array" && !item.areas.length));
    const buttons = this.shadowRoot.querySelectorAll("[data-scene-mode]");
    for (const button of buttons) {
      const mode = button.dataset.sceneMode;
      button.disabled = this.busy || (mode !== "orbit" && !item)
        || (mode === "rotate" && !single)
        || (mode === "height" && this.selected?.kind === "array" && item.areas.length > 0);
      if (button.disabled && this.scene3d.mode === mode) this.scene3d.mode = "orbit";
    }
    for (const button of buttons) button.setAttribute("aria-pressed", String(this.scene3d.mode === button.dataset.sceneMode));
    this.shadowRoot.getElementById("view-3d").disabled = this.busy || this.mode !== "pan";
  }
  editScene(selection, action) {
    if (this.busy || this.mode !== "pan") return;
    const item = selection.kind === "object" ? this.site.shading_objects[selection.index]
      : selection.kind === "area" ? this.site.pv_arrays[selection.array].areas[selection.area] : this.site.pv_arrays[selection.array];
    if (action.kind === "move") {
      const moved = action.points.map((p) => {
        const point = localPoint(p, this.scene3d.origin);
        return geographicPoint({ x: point.x + action.x, y: point.y + action.y }, this.scene3d.origin);
      });
      if (moved.some((p) => !Number.isFinite(p.latitude) || Math.abs(p.latitude) > 85)) {
        this.status(this.t.invalid, true); return;
      }
      const targets = selection.kind === "object" ? item.line : selection.kind === "area" ? item.polygon
        : item.areas.length ? item.areas.flatMap((a) => a.polygon) : [item];
      targets.forEach((p, index) => Object.assign(p, moved[index]));
    } else if (action.kind === "height") {
      if (selection.kind === "object") {
        const minimum = Math.min(...action.heights), maximum = Math.max(...action.heights);
        const delta = Math.max(0.01 - minimum, Math.min(1000 - maximum, action.delta));
        item.line.forEach((p, index) => { p.height = Math.round((action.heights[index] + delta) * 100) / 100; });
      } else item.height = Math.round(Math.max(0, Math.min(1000, action.heights[0] + action.delta)) * 100) / 100;
    } else if (action.kind === "rotate") item.azimuth = action.azimuth;
    this.changed(); this.renderInspector();
  }
  observeMap() {
    this.observer?.disconnect();
    this.observer = new ResizeObserver(() => this.drawMap());
    this.observer.observe(this.map); this.drawMap();
  }
  changed() {
    this.dirty = true;
    for (const array of this.site.pv_arrays) {
      if (array.areas.length) {
        array.peak = array.areas.reduce((sum, a) => sum + (Number(a.peak) || 0), 0);
        Object.assign(array, areaCenter(array.areas[0].polygon));
      }
    }
    this.status(this.t.dirty); this.renderLists(); this.updateButtons(); this.drawMap();
    this.updateSurfaceHeights();
    this.queuePreview();
  }
  updateButtons() {
    const root = this.shadowRoot;
    const drawing = this.mode === "draw";
    root.getElementById("save").disabled = this.busy || !this.site || !this.dirty || this.mode !== "pan";
    root.getElementById("dirty").hidden = !this.dirty;
    root.getElementById("sites").disabled = this.busy || drawing;
    root.getElementById("add-array").disabled = this.busy || !this.site || drawing || this.site.pv_arrays.length >= 64;
    root.getElementById("add-object").disabled = this.busy || !this.site || drawing || this.site.shading_objects.length >= 128;
    root.getElementById("draw-toolbar").hidden = this.mode === "pan";
    root.getElementById("draft-height-label").hidden = !drawing || this.draw?.kind !== "object";
    root.getElementById("finish").hidden = !drawing;
    root.getElementById("undo").hidden = !drawing;
    root.getElementById("finish").textContent = this.draw?.kind === "object" ? this.t.finishLine : this.t.finishArea;
    root.getElementById("mode").textContent = drawing ? this.draw?.kind === "object" ? this.t.drawLine : this.t.drawArea : this.t.placing;
    this.map.classList.toggle("drawing", this.mode !== "pan");
    root.getElementById("inspector").hidden = drawing;
    for (const field of root.querySelectorAll("#inspector input,#inspector select,#inspector button,.list button")) {
      field.disabled = this.busy || drawing || (field.dataset.key === "vertical_height" && this.item()?.tilt !== 90);
    }
    root.getElementById("summary").textContent = this.site ? `${(this.site.pv_arrays.reduce((s, a) => s + (Number(a.peak) || 0), 0) / 1000).toLocaleString(this.language)} kWp · ${this.site.pv_arrays.length} ${this.site.pv_arrays.length === 1 ? this.t.array : this.t.arrays}` : "";
    root.getElementById("analysis-note").textContent = this.dirty ? this.t.savedOnly : "";
    for (const field of root.querySelectorAll("#settings-view input,#settings-view button")) field.disabled = this.busy || !this.site;
    root.getElementById("add-inverter").disabled = this.busy || !this.site || this.site.inverters.length >= 64;
    this.updatePreviewControls();
  }

  renderLists() {
    for (const [kind, id, key] of [["array", "arrays", "pv_arrays"], ["object", "objects", "shading_objects"]]) {
      const node = this.shadowRoot.getElementById(id);
      node.innerHTML = (this.site?.[key] || []).map((item, index) => {
        const selected = kind === "array" ? this.selected?.array === index : this.selected?.kind === "object" && this.selected.index === index;
        const description = kind === "array" ? `${(Number(item.peak) / 1000).toLocaleString(this.language)} kWp · ${item.areas.length} ${item.areas.length === 1 ? this.t.area : this.t.areas}`
          : `${item.solid ? this.t.solid : this.t.vegetation} · ${item.line.length} ${this.t.points}`;
        return `<button data-index="${index}" class="${selected ? "selected" : ""}" ${this.busy || this.mode === "draw" ? "disabled" : ""}>
          <i class="dot ${kind === "object" ? "orange" : ""}"></i><span><span class="name">${escapeHTML(item.name)}</span><small>${description}</small></span></button>`;
      }).join("");
      for (const button of node.querySelectorAll("button")) button.onclick = () => this.select(
        kind === "array" ? { kind, array: Number(button.dataset.index) } : { kind, index: Number(button.dataset.index) }, true
      );
    }
  }

  select(selection, center = false) {
    if (this.busy || this.mode === "draw") return;
    this.selected = selection; this.mode = "pan";
    if (center) {
      const item = this.item();
      const point = selection.kind === "object" ? item.line[0] : selection.kind === "area" ? areaCenter(item.polygon) : item;
      this.center = project(point, this.zoom);
    }
    if (center && this.mapView === "3d") this.scene3d.focus(selection);
    this.renderLists(); this.renderInspector(); this.updateButtons(); this.updateSceneControls(); this.drawMap();
    this.queuePreview();
  }
  item() {
    const s = this.selected;
    if (!s || !this.site) return null;
    if (s.kind === "object") return this.site.shading_objects[s.index];
    const array = this.site.pv_arrays[s.array];
    return s.kind === "area" ? array.areas[s.area] : array;
  }
  field(key, value, options = {}) {
    const label = options.label || this.t[key];
    if (options.choices) return `<label class="${options.full ? "full" : ""}">${label}<select data-key="${key}">${options.choices.map(([v, title]) =>
      `<option value="${v}" ${String(value) === String(v) ? "selected" : ""}>${title}</option>`).join("")}</select></label>`;
    return `<label class="${options.full ? "full" : ""}">${label}<input data-key="${key}" required
      ${options.text ? `type="text" maxlength="100" ${options.pattern ? `pattern="${options.pattern}"` : ""}` : `type="number" min="${options.min ?? 0}" max="${options.max ?? 1000}" step="${options.step ?? "any"}"`}
      ${options.readonly ? "readonly" : ""} value="${escapeHTML(value ?? "")}"></label>`;
  }
  installationFields(item, includePeak = true) {
    return (includePeak ? this.field("peak", item.peak, { min: 0.01, max: 1e9 }) : "")
      + this.field("mounting_type", item.mounting_type, { choices: ["roof_parallel", "elevated", "ground", "facade", "balcony"].map((v) => [v, this.t[v]]), full: true })
      + this.field("azimuth", item.azimuth, { max: 360 })
      + this.field("tilt", item.tilt, { max: 90 })
      + this.field("height", item.height, { label: this.t.lowerHeight })
      + (this.selected?.kind === "area" ? `<label class="full" id="vertical-height-field" ${item.tilt === 90 ? "" : "hidden"}>${this.t.verticalHeight}
          <input data-key="vertical_height" type="number" min="0.01" max="1000" step="any" ${item.tilt === 90 ? "required" : "disabled"} value="${item.vertical_height ?? ""}"></label>` : "");
  }
  renderInspector() {
    const root = this.shadowRoot.getElementById("inspector"), item = this.item(), t = this.t;
    if (!item) {
      root.innerHTML = this.site?.pv_arrays.length ? infoDetails(t.geometryHelp, t.select)
        : `<p class="empty-selection muted" role="status">${t.setupHint}</p>`;
      return;
    }
    const selection = this.selected;
    const isObject = selection.kind === "object";
    let fields = this.field("name", item.name, { text: true, full: true }), extra = "", actions = "";
    if (!isObject) fields += this.field("inverter_id", this.site.pv_arrays[selection.array].inverter_id || "", {
      full: true, choices: [["", t.unassigned], ...this.site.inverters.map((i) => [i.id, escapeHTML(i.name)])],
    });
    if (isObject) {
      fields += this.field("solid", item.solid, { label: t.type, full: true, choices: [["true", t.solid], ["false", t.vegetation]] });
      if (item.solid) fields += this.field("reduction", (1 - item.transmission) * 100, { max: 100, full: true });
      else {
        fields += this.field("reduction_leaf_on", item.reduction_leaf_on * 100, { max: 100 });
        fields += this.field("reduction_leaf_off", item.reduction_leaf_off * 100, { max: 100 });
        fields += this.field("foliage_mode", item.foliage_mode, { choices: ["auto", "leaf_on", "leaf_off"].map((v) => [v, t[v]]), full: true });
        const dateValue = (value) => this.language === "de" ? `${value.slice(3)}.${value.slice(0, 2)}.` : value;
        const pattern = this.language === "de" ? "[0-9]{2}\\.[0-9]{2}\\." : "[0-9]{2}-[0-9]{2}";
        fields += this.field("leaf_on", dateValue(item.leaf_on), { text: true, label: t.leafOnDate, pattern });
        fields += this.field("leaf_off", dateValue(item.leaf_off), { text: true, label: t.leafOffDate, pattern });
      }
      const active = selection.vertex ?? 0;
      extra = `${infoDetails(t.geometryHelp, `${t.lineHint}${!item.solid ? `\n\n${t.seasonHint}` : ""}`)}
        <h3>${t.point} ${active + 1}</h3><label>${t.pointHeight}
          <input data-node="${active}" type="number" min="0.01" max="1000" step="any" required value="${item.line[active].height}"></label>
        <details><summary>${t.points} (${item.line.length})</summary>${item.line.map((p, index) =>
          `<div class="point-row ${index === active ? "active" : ""}"><button type="button" data-node-select="${index}">${t.point} ${index + 1}</button>
             <input aria-label="${t.point} ${index + 1} · ${t.height}" data-node="${index}" type="number" min="0.01" max="1000" step="any" required value="${p.height}"><span>m</span></div>`
        ).join("")}</details>`;
      actions = `<button id="redraw">${t.redraw}</button>`;
    } else if (selection.kind === "area") {
      fields += this.installationFields(item);
      extra = `<p id="surface-heights" class="height-result" role="status"></p>
        ${infoDetails(t.geometryHelp, `${t.slopeHint}\n\n${t.heights}`)}
        <h3>${t.strings}</h3>${infoDetails(t.sensorHelp, t.stringHint)}
        <datalist id="string-sensors">${Object.entries(this._hass.states || {})
          .filter(([id, state]) => id.startsWith("sensor.") && ["W", "kW"].includes(state.attributes.unit_of_measurement))
          .map(([id, state]) => `<option value="${escapeHTML(id)}">${escapeHTML(state.attributes.friendly_name || id)}</option>`).join("")}</datalist>
        ${(item.strings || []).map((string, index) => `<fieldset class="string-fields"><legend>${t.string} ${index + 1}</legend>
          <div class="fields"><label class="full">${t.name}<input data-string="${index}" data-string-key="name" required maxlength="100" value="${escapeHTML(string.name)}"></label>
          <label class="full">${t.stringSensor}<input data-string="${index}" data-string-key="power_entity" required pattern="sensor\\.[a-z0-9_]+" list="string-sensors" value="${escapeHTML(string.power_entity)}"></label></div>
          <button type="button" data-remove-string="${index}" class="danger">${t.remove}</button></fieldset>`).join("")}
        <button type="button" id="add-string" ${(item.strings || []).length >= 16 ? "disabled" : ""}>${t.addString}</button>`;
      actions = `<button id="redraw">${t.redraw}</button><button id="add-area">${t.addArea}</button>`;
    } else {
      fields += this.field("peak", item.peak, { min: 0.01, max: 1e9, readonly: item.areas.length > 0 });
      if (!item.areas.length) {
        fields += this.installationFields(item, false)
          + this.field("latitude", item.latitude, { min: -85, max: 85 })
          + this.field("longitude", item.longitude, { min: -180, max: 180 });
        extra = infoDetails(t.geometryHelp, t.legacy);
        actions += `<button id="place">${t.position}</button>`;
      } else extra = `${infoDetails(t.geometryHelp, t.arraySummary)}
        <div class="list">${item.areas.map((area, index) =>
          `<button type="button" data-area="${index}">${escapeHTML(area.name)} · ${area.peak} Wp · ${area.tilt}°</button>`
        ).join("")}</div><details><summary>${t.defaults}</summary><div class="fields">${this.installationFields(item, false)}</div></details>`;
      actions += `<button id="add-area" class="primary">${t.addArea}</button>`;
    }
    root.innerHTML = `<div class="inspector-header"><h2>${escapeHTML(item.name)}</h2><button id="close-inspector" aria-label="${t.close}">×</button></div>
      <div class="inspector-body"><form><div class="fields">${fields}</div>${extra}</form>
      <div class="actions">${actions}<button id="delete" class="danger">${t.remove}</button></div></div>`;
    root.querySelector("form").onsubmit = (event) => event.preventDefault();
    if (root.querySelector("#add-string")) root.querySelector("#add-string").onclick = () => {
      item.strings ||= [];
      let number = 1;
      while (item.strings.some(string => string.name === `${t.string} ${number}`)) number++;
      item.strings.push({ name: `${t.string} ${number}`, power_entity: "" });
      this.changed(); this.renderInspector();
    };
    for (const input of root.querySelectorAll("[data-string]")) input.oninput = () => {
      item.strings[Number(input.dataset.string)][input.dataset.stringKey] = input.value;
      this.changed();
    };
    for (const button of root.querySelectorAll("[data-remove-string]")) button.onclick = () => {
      item.strings.splice(Number(button.dataset.removeString), 1);
      this.changed(); this.renderInspector();
    };
    root.querySelector("#close-inspector").onclick = () => {
      this.selected = null; this.renderInspector(); this.queuePreview();
    };
    for (const input of root.querySelectorAll("[data-key]")) {
      const edit = () => {
        const key = input.dataset.key;
        if (key === "solid") item.solid = input.value === "true";
        else if (key === "inverter_id") this.site.pv_arrays[selection.array].inverter_id = input.value;
        else if (key === "reduction") item.transmission = 1 - Number(input.value) / 100;
        else if (key.startsWith("reduction_leaf")) item[key] = input.value === "" ? null : Number(input.value) / 100;
        else if (key === "leaf_on" || key === "leaf_off") {
          const parts = input.value.split(".");
          item[key] = this.language === "de" ? `${parts[1]}-${parts[0]}` : input.value;
        } else item[key] = input.type === "number" ? input.value === "" ? null : Number(input.value) : input.value;
        root.querySelector("h2").textContent = item.name;
        if (key === "tilt" && selection.kind === "area") {
          const label = root.querySelector("#vertical-height-field"), field = label.querySelector("input");
          label.hidden = item.tilt !== 90; field.required = item.tilt === 90; field.disabled = item.tilt !== 90;
        }
        this.changed();
        if (key === "solid") this.renderInspector();
      };
      if (input.tagName === "SELECT") input.onchange = edit;
      else input.oninput = edit;
    }
    for (const input of root.querySelectorAll("[data-node]")) input.oninput = () => {
      item.line[Number(input.dataset.node)].height = input.value === "" ? null : Number(input.value);
      for (const other of root.querySelectorAll(`[data-node="${input.dataset.node}"]`)) if (other !== input) other.value = input.value;
      this.changed();
    };
    for (const button of root.querySelectorAll("[data-node-select]")) button.onclick = () => {
      this.selected.vertex = Number(button.dataset.nodeSelect); this.renderInspector(); this.drawMap();
    };
    for (const button of root.querySelectorAll("[data-area]")) button.onclick = () => this.select({ kind: "area", array: selection.array, area: Number(button.dataset.area) }, true);
    if (root.querySelector("#place")) root.querySelector("#place").onclick = () => { this.mode = "place"; this.setMapView("2d"); this.updateButtons(); };
    if (root.querySelector("#add-area")) root.querySelector("#add-area").onclick = () => this.startDraw({ kind: "area", array: selection.array });
    this.updateSurfaceHeights();
    if (root.querySelector("#redraw")) root.querySelector("#redraw").onclick = () => this.startDraw(
      isObject ? { kind: "object", index: selection.index } : { kind: "area", array: selection.array, area: selection.area }
    );
    root.querySelector("#delete").onclick = () => this.deleteSelected();
  }

  addArray() {
    const wasDirty = this.dirty;
    const point = unproject(this.center, this.zoom);
    this.site.pv_arrays.push({ name: `${this.t.array} ${this.site.pv_arrays.length + 1}`,
      peak: 5000, azimuth: 180, tilt: 30, height: 0, mounting_type: "roof_parallel", inverter_id: "", areas: [], ...point });
    const index = this.site.pv_arrays.length - 1;
    this.selected = { kind: "array", array: index };
    this.startDraw({ kind: "area", array: index, newArray: true, wasDirty });
  }
  startDraw(draw) {
    if (draw.kind === "area" && draw.area === undefined && this.site.pv_arrays[draw.array].areas.length >= 32) {
      this.status(this.t.areaLimit, true); return;
    }
    this.draw = draw; this.points = []; this.mode = "draw";
    this.setMapView("2d");
    this.setTab("map", false); this.updateButtons();
    this.status(draw.kind === "object" ? this.t.drawLine : this.t.drawArea);
    this.drawMap();
    this.queuePreview();
  }
  cancelDrawing() {
    if (this.draw?.newArray && this.mode === "draw") {
      this.site.pv_arrays.splice(this.draw.array, 1);
      this.dirty = this.draw.wasDirty; this.selected = null;
    }
    this.draw = null; this.points = []; this.mode = "pan";
    this.renderLists(); this.renderInspector(); this.updateButtons();
    this.status(this.dirty ? this.t.dirty : ""); this.drawMap();
    this.queuePreview();
  }
  finishDrawing() {
    const draw = this.draw;
    if (this.points.length < (draw.kind === "object" ? 2 : 3)) {
      this.status(draw.kind === "object" ? this.t.minLine : this.t.minArea, true); return;
    }
    if (draw.kind === "object") {
      if (draw.index === undefined) {
        draw.index = this.site.shading_objects.length;
        this.site.shading_objects.push({
          name: `${this.t.object} ${draw.index + 1}`, line: [], height: this.draftHeight,
          solid: true, transmission: 0, reduction_leaf_on: 0.9, reduction_leaf_off: 0.5,
          foliage_mode: "auto", leaf_on: "04-01", leaf_off: "11-01",
        });
      }
      const item = this.site.shading_objects[draw.index];
      item.line = structuredClone(this.points);
      delete item.polygon;
      this.selected = { kind: "object", index: draw.index, vertex: 0 };
    } else {
      const array = this.site.pv_arrays[draw.array];
      if (draw.area === undefined) {
        draw.area = array.areas.length;
        array.areas.push({
          name: `${this.t.area} ${draw.area + 1}`, peak: array.areas[0]?.peak || array.peak,
          mounting_type: array.mounting_type, azimuth: array.azimuth, tilt: array.tilt,
          height: array.height, polygon: [],
          vertical_height: null,
          strings: [],
        });
      }
      array.areas[draw.area].polygon = structuredClone(this.points);
      this.selected = { kind: "area", array: draw.array, area: draw.area };
    }
    this.mode = "pan"; this.points = []; this.draw = null;
    this.changed(); this.renderInspector(); this.updateButtons();
  }
  deleteSelected() {
    const lastArray = this.selected.kind === "array" && this.site.pv_arrays.length === 1;
    if (!window.confirm(lastArray ? this.t.deleteLastArray : this.t.deleteConfirm)) return;
    const s = this.selected;
    if (s.kind === "object") this.site.shading_objects.splice(s.index, 1);
    else if (s.kind === "area") this.site.pv_arrays[s.array].areas.splice(s.area, 1);
    else this.site.pv_arrays.splice(s.array, 1);
    this.selected = null; this.changed(); this.renderInspector();
  }

  addInverter() {
    const id = globalThis.crypto?.randomUUID?.() || `inverter-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    this.site.inverters.push({ id, name: `${this.t.inverters} ${this.site.inverters.length + 1}`,
      limit_w: 5000, power_entity: "", energy_entity: "" });
    this.renderSettings(); this.renderInspector(); this.changed();
  }
  renderSettings() {
    const root = this.shadowRoot.getElementById("inverters"), t = this.t;
    if (!this.site) { root.innerHTML = ""; this.shadowRoot.getElementById("forecast-settings").innerHTML = ""; return; }
    const sensorOptions = (units) => Object.entries(this._hass.states || {})
      .filter(([id, state]) => id.startsWith("sensor.") && units.includes(state.attributes.unit_of_measurement))
      .map(([id, state]) => `<option value="${escapeHTML(id)}">${escapeHTML(state.attributes.friendly_name || id)}</option>`).join("");
    root.innerHTML = this.site.inverters.map((inverter, index) => `
      <article class="card"><div class="row spread"><h2 data-inverter-name="${index}">${escapeHTML(inverter.name)}</h2>
        <button type="button" data-inverter-delete="${index}" class="danger">${t.remove}</button></div>
        <form data-inverter="${index}"><div class="fields">
          <label class="full">${t.name}<input data-key="name" type="text" maxlength="100" required value="${escapeHTML(inverter.name)}"></label>
          <label class="full">${t.limit_w}<input data-key="limit_w" type="number" min="1" max="1000000000" step="any" required value="${inverter.limit_w}"></label>
          <label class="full">${t.power_entity}<input data-key="power_entity" aria-describedby="power-sensor-help-${index}" list="power-sensors-${index}" type="text" value="${escapeHTML(inverter.power_entity)}"><datalist id="power-sensors-${index}">${sensorOptions(["W", "kW"])}</datalist></label>
          <label class="full">${t.energy_entity}<input data-key="energy_entity" list="energy-sensors-${index}" type="text" value="${escapeHTML(inverter.energy_entity)}"><datalist id="energy-sensors-${index}">${sensorOptions(["Wh", "kWh", "MWh"])}</datalist></label>
        </div></form>${infoDetails(t.sensorHelp, `${t.powerSensorHint}\n\n${t.sensorHint}`, `power-sensor-help-${index}`)}
        <div class="stats"><div class="stat"><strong data-measured-power="${index}"></strong><span>${t.measuredPower}</span></div>
          <div class="stat"><strong data-measured-energy="${index}"></strong><span>${t.measuredEnergy}</span></div></div>
      </article>`).join("");
    for (const form of root.querySelectorAll("form")) {
      form.onsubmit = (event) => event.preventDefault();
      const index = Number(form.dataset.inverter);
      for (const input of form.querySelectorAll("input")) input.oninput = () => {
        const key = input.dataset.key;
        this.site.inverters[index][key] = key === "limit_w" ? input.value === "" ? null : Number(input.value) : input.value;
        root.querySelector(`[data-inverter-name="${index}"]`).textContent = this.site.inverters[index].name;
        this.changed(); this.updateMeasuredStates();
      };
    }
    for (const button of root.querySelectorAll("[data-inverter-delete]")) button.onclick = () => {
      if (!window.confirm(t.deleteConfirm)) return;
      const [removed] = this.site.inverters.splice(Number(button.dataset.inverterDelete), 1);
      for (const array of this.site.pv_arrays) if (array.inverter_id === removed.id) array.inverter_id = "";
      this.renderSettings(); this.renderInspector(); this.changed();
    };
    const settings = this.site.settings;
    const fields = [
      ["open_meteo_forecast_days", 1, 16, 1], ["daily_yield_sensor_days", 2, 16, 1],
      ["damping_morning", 0, 1, "any"], ["damping_evening", 0, 1, "any"], ["horizon", 0, 90, "any"],
      ["inverter_size", 1, 1e9, "any"],
    ];
    const container = this.shadowRoot.getElementById("forecast-settings");
    container.innerHTML = `<h2>${t.forecastSettings}</h2><form><div class="fields">${fields.map(([key, min, max, step]) =>
      `<label>${t[key]}<input data-setting="${key}" type="number" min="${min}" max="${max}" step="${step}"
        ${key === "inverter_size" ? "" : "required"} value="${settings[key] ?? ""}"></label>`
    ).join("")}</div>
      <h3>${t.forecastThresholds}</h3>${infoDetails(t.help, t.forecastThresholdHint)}
      <div class="fields">${["forecast_low_kwh", "forecast_high_kwh"].map(key =>
        `<label>${t[key]}<input data-setting="${key}" type="number" min="0" max="1000000" step="any" required value="${settings[key]}"></label>`
      ).join("")}</div></form>`;
    container.querySelector("form").onsubmit = (event) => event.preventDefault();
    for (const input of container.querySelectorAll("input")) input.oninput = () => {
      settings[input.dataset.setting] = input.value === "" ? null : Number(input.value);
      const high = container.querySelector('[data-setting="forecast_high_kwh"]');
      high.setCustomValidity(settings.forecast_high_kwh > settings.forecast_low_kwh ? "" : t.forecastThresholdInvalid);
      this.changed();
    };
    this.updateMeasuredStates();
  }
  updateMeasuredStates() {
    if (!this.site || !this.shadowRoot.getElementById("inverters")) return;
    for (const [index, inverter] of this.site.inverters.entries()) {
      for (const [key, target] of [["power_entity", "power"], ["energy_entity", "energy"]]) {
        const state = this._hass.states?.[inverter[key]];
        const node = this.shadowRoot.querySelector(`[data-measured-${target}="${index}"]`);
        if (!node) continue;
        const number = state && state.state !== "" ? Number(state.state) : NaN;
        node.textContent = Number.isFinite(number)
          ? `${number.toLocaleString(this.language)} ${state.attributes.unit_of_measurement || ""}` : this.t.unavailable;
      }
    }
  }

  async save() {
    for (const form of this.shadowRoot.querySelectorAll("form")) {
      if (!form.checkValidity()) {
        this.setTab(form.closest("#settings-view") ? "settings" : "map", false);
        form.reportValidity(); this.status(this.t.invalid, true); return;
      }
    }
    this.busy = true; this.status(this.t.saving); this.updateButtons();
    this.analysisGeneration++;
    try {
      const saved = await this._hass.callWS({
        type: "smart_pv_forecast/save_site", entry_id: this.site.entry_id, revision: this.site.revision,
        pv_arrays: this.site.pv_arrays, shading_objects: this.site.shading_objects,
        inverters: this.site.inverters, settings: this.site.settings,
      });
      this.site = saved;
      this.sites = this.sites.map((s) => s.entry_id === saved.entry_id ? structuredClone(saved) : s);
      this.dirty = false; this.analysis = null;
      this.renderInspector(); this.renderSettings(); this.status(this.t.saved);
      this.queuePreview(0);
    } catch (error) { this.status(`${this.t.error}: ${error.message || error}`, true); }
    finally { this.busy = false; this.renderLists(); this.updateButtons(); }
    if (!this.dirty) await this.loadAnalysis();
  }
  async loadAnalysis(attempt = 0) {
    if (!this.site) return;
    const request = ++this.analysisGeneration;
    this.analysisLoading = false;
    clearTimeout(this.analysisTimer);
    if (!this.site.pv_arrays.length) {
      this.analysis = null; this.analysisMessage = this.t.setupHint;
      this.renderCharts(); return;
    }
    if (this.site.geometry_required) {
      this.analysis = null; this.analysisMessage = this.t.geometryHint;
      this.renderCharts(); return;
    }
    const generation = this.loadGeneration, entryID = this.site.entry_id;
    this.analysisLoading = true;
    this.analysisMessage = this.analysis ? "" : this.t.loading;
    this.renderCharts();
    try {
      const data = await this._hass.callWS({ type: "smart_pv_forecast/analysis", entry_id: entryID });
      if (generation !== this.loadGeneration || request !== this.analysisGeneration || !this.isConnected) return;
      if (data.revision !== this.site.revision) throw { code: "updating", message: this.t.pending };
      this.analysisMessage = "";
      try {
        const savedSite = this.sites.find(site => site.entry_id === entryID);
        const history = await loadSiteHistory(this._hass, savedSite, data);
        if (generation !== this.loadGeneration || request !== this.analysisGeneration || !this.isConnected) return;
        data.arrays.forEach((array, index) => {
          array.strings = history.strings[index];
        });
        data.total.measurement = history.total;
        data.total.inverters = history.inverters;
        data.totalMeasurementStatus = history.totalStatus;
        data.history_until = history.until;
        data.historyMissing = history.strings.flat().some(string => !string.values.some(value => value !== null));
      } catch (error) {
        if (generation !== this.loadGeneration || request !== this.analysisGeneration || !this.isConnected) return;
        data.historyError = `${this.t.historyError}: ${error.message || error}`;
      }
      this.analysis = data;
    } catch (error) {
      if (generation !== this.loadGeneration || request !== this.analysisGeneration || !this.isConnected) return;
      this.analysisMessage = error.code === "updating" || error.code === "unavailable" ? this.t.pending : `${this.t.error}: ${error.message || error}`;
      if ((error.code === "updating" || error.code === "unavailable") && attempt < 10) {
        this.analysisTimer = setTimeout(() => this.loadAnalysis(attempt + 1), 2000);
      }
    } finally {
      if (request === this.analysisGeneration) this.analysisLoading = false;
    }
    this.renderCharts();
  }
  renderCharts() {
    this.shadowRoot.getElementById("analysis-status").textContent = this.analysisMessage || "";
    const root = this.shadowRoot.getElementById("charts");
    const settings = this.sites.find(site => site.entry_id === this.site?.entry_id)?.settings;
    updateRenderedContent(root, renderAnalysis(this.analysis, this.t, this.language, settings));
    this.unwireCharts?.();
    if (this.analysis) {
      this.unwireCharts = wireChartInteraction(root, this.analysis, this.t, this.language);
      root.querySelectorAll(".chart-tooltip:not([hidden])").forEach(tooltip => {
        const chart = tooltip.dataset.tooltip, hour = Number(tooltip.dataset.selectedHour);
        if ((chart === "total" ? this.analysis.total : this.analysis.arrays[Number(chart)])
          && Number.isInteger(hour)) showChartTooltip(root, this.analysis, this.t, this.language, chart, hour);
      });
    }
  }

  previewPlane() {
    const item = this.item();
    const installation = this.selected?.kind === "area"
      || (this.selected?.kind === "array" && !item.areas.length);
    const position = this.selected?.kind === "area" ? areaCenter(item.polygon)
      : this.selected?.kind === "object" ? item.line[0] : item || this.site;
    return {
      height: this.previewHeight ?? (installation ? item.height : 0),
      position: { latitude: position.latitude, longitude: position.longitude },
    };
  }
  updateSurfaceHeights() {
    const node = this.shadowRoot.getElementById("surface-heights");
    if (!node || this.selected?.kind !== "area") return;
    const profile = surfaceProfile(this.item());
    const invalid = !profile || (profile.upper_height !== null &&
      (!Number.isFinite(profile.upper_height) || profile.upper_height > 1000));
    node.classList.toggle("error", invalid || profile?.upper_height === null);
    const format = (v) => v.toLocaleString(this.language, { maximumFractionDigits: 2 });
    node.textContent = invalid ? this.t.invalidHeights : profile.upper_height === null ? this.t.geometryHint
      : `${this.t.upperHeight}: ${format(profile.upper_height)} m · ${this.t.heightDifference}: ${format(profile.rise)} m · ${this.t.groundSpan}: ${format(profile.span)} m`;
  }
  updatePreviewControls() {
    const root = this.shadowRoot;
    for (const field of root.querySelectorAll(".preview-controls input,.preview-controls button")) field.disabled = !this.site;
    root.getElementById("preview-live").setAttribute("aria-pressed", String(this.previewLive));
    root.getElementById("preview-auto").setAttribute("aria-pressed", String(this.previewHeight === null));
    const field = root.getElementById("preview-height");
    if (this.site && root.activeElement !== field) {
      const height = this.previewPlane().height;
      field.value = Number.isFinite(height) ? height : "";
    }
    const date = root.getElementById("preview-date");
    if (root.activeElement !== date) date.value = this.previewWhen;
    const info = root.getElementById("preview-info");
    info.classList.toggle("error", !!this.previewError);
    if (!this.preview) { info.textContent = this.previewMessage || ""; return; }
    const data = this.preview, sun = data.sun;
    const format = (v) => Number(v).toLocaleString(this.language, { maximumFractionDigits: 1 });
    info.textContent = `${this.t.previewHeight}: ${format(data.height)} m · ${data.timezone} (${data.time.slice(-6)}) · ${this.t.azimuth}: ${format(sun.azimuth)} · ${this.t.sunElevation}: ${format(sun.elevation)}°`
      + (sun.elevation <= 0 ? ` · ${this.t.sunBelow}` : "")
      + (data.truncated ? ` · ${this.t.previewTruncated}` : "");
  }
  queuePreview(delay = 300) {
    clearTimeout(this.previewTimer);
    this.previewGeneration++;
    this.preview = null; this.previewError = false; this.previewMessage = this.t.loading;
    this.updatePreviewControls(); this.drawMap();
    if (this.site && this.isConnected) this.previewTimer = setTimeout(() => this.loadPreview(), delay);
  }
  async loadPreview() {
    const root = this.shadowRoot, generation = this.previewGeneration;
    if (!this.site) return;
    if (!root.getElementById("preview-height").checkValidity()
      || (!this.previewLive && !root.getElementById("preview-date").checkValidity())) {
      this.previewError = true; this.previewMessage = this.t.invalid;
      this.updatePreviewControls(); return;
    }
    try {
      const data = await this._hass.callWS({
        type: "smart_pv_forecast/shadow_preview", entry_id: this.site.entry_id,
        shading_objects: this.site.shading_objects, ...this.previewPlane(),
        when: this.previewLive ? null : this.previewWhen,
      });
      if (generation !== this.previewGeneration || !this.isConnected) return;
      this.preview = data; this.previewMessage = "";
      if (this.previewLive) this.previewWhen = data.local_time;
    } catch (error) {
      if (generation !== this.previewGeneration || !this.isConnected) return;
      this.preview = null; this.previewError = true;
      this.previewMessage = `${this.t.previewError}: ${error.message || error}`;
    }
    this.updatePreviewControls(); this.drawMap();
  }
  shadowShapes() {
    if (!this.preview || !this.showShadows || !this.preview.shadows.length) return "";
    const paths = [...this.preview.shadows].sort((a, b) => a.reduction - b.reduction).map((shadow) => {
      const shade = Math.round(shadow.reduction * 255);
      const path = shadow.polygons.map((polygon) => {
        const points = polygon.map((p) => this.screen(p));
        return `M${points.map((p) => `${p.x},${p.y}`).join(" L")} Z`;
      }).join(" ");
      return `<path d="${path}" fill="rgb(${shade},${shade},${shade})"/>`;
    }).join("");
    // Opaque grayscale layers sorted by reduction encode the strongest shadow, not multiplied overlap.
    return `<defs><mask id="cast-shadow-mask" maskUnits="userSpaceOnUse" x="0" y="0" width="${this.map.clientWidth}" height="${this.map.clientHeight}" style="mask-type:luminance">
      <rect width="100%" height="100%" fill="black"/>${paths}</mask></defs>
      <rect class="cast-shadow" width="100%" height="100%" fill="#263849" fill-opacity=".55" mask="url(#cast-shadow-mask)" pointer-events="none"/>`;
  }
  drawSun() {
    const node = this.shadowRoot.getElementById("sun-compass");
    node.toggleAttribute("hidden", !this.preview);
    if (!this.preview) return;
    const sun = this.preview.sun, angle = sun.azimuth * Math.PI / 180;
    const x = 50 + Math.sin(angle) * 30, y = 53 - Math.cos(angle) * 30;
    node.innerHTML = `<title>${this.t.sunPosition}: ${sun.azimuth.toFixed(1)}°, ${this.t.sunElevation}: ${sun.elevation.toFixed(1)}°</title>
      <circle cx="50" cy="53" r="36" fill="#ffffffea" stroke="#9ba9b4"/>
      <text x="50" y="13" text-anchor="middle">N</text><text x="50" y="110" text-anchor="middle">${this.t.sun}</text>
      ${sun.elevation > 0 ? `<line x1="50" y1="53" x2="${x}" y2="${y}" stroke="#c47c00" stroke-width="3"/>
        <circle class="sun-marker" cx="${x}" cy="${y}" r="8" fill="#ffcc33" stroke="#b97800" stroke-width="2"/>` : `<text x="50" y="59" text-anchor="middle">—</text>`}`;
  }

  changeZoom(delta, event) {
    if (!this.center) return;
    const point = event ? this.coordinate(event) : unproject(this.center, this.zoom);
    const next = Math.max(3, Math.min(MAX_MAP_ZOOM, this.zoom + delta));
    const world = project(point, next);
    if (event) {
      const r = this.map.getBoundingClientRect();
      world.x -= event.clientX - r.left - r.width / 2;
      world.y -= event.clientY - r.top - r.height / 2;
    }
    this.zoom = next; this.center = world; this.drawMap();
  }
  screen(point) {
    const world = project(point, this.zoom), size = 256 * 2 ** this.zoom;
    return { x: ((world.x - this.center.x + size / 2) % size + size) % size - size / 2 + this.map.clientWidth / 2,
      y: world.y - this.center.y + this.map.clientHeight / 2 };
  }
  coordinate(event) {
    const r = this.map.getBoundingClientRect();
    return unproject({ x: this.center.x + event.clientX - r.left - r.width / 2,
      y: this.center.y + event.clientY - r.top - r.height / 2 }, this.zoom);
  }
  pointerDown(event) {
    if (!this.site || this.busy || event.target.closest("button,a") || event.button !== 0) return;
    event.preventDefault();
    this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    this.map.setPointerCapture(event.pointerId);
    if (this.pointers.size === 2) {
      const [a, b] = [...this.pointers.values()];
      const midpoint = { clientX: (a.x + b.x) / 2, clientY: (a.y + b.y) / 2 };
      this.pinch = { distance: Math.hypot(a.x - b.x, a.y - b.y), zoom: this.zoom, anchor: this.coordinate(midpoint) };
      this.drag = null; return;
    }
    if (this.pointers.size > 2) return;
    const target = event.target.closest("[data-kind]");
    this.drag = { pointer: event.pointerId, x: event.clientX, y: event.clientY,
      center: { ...this.center }, moved: false, edit: false };
    if (this.mode === "pan" && target) {
      const selection = target.dataset.kind === "object"
        ? { kind: "object", index: Number(target.dataset.index), ...(target.dataset.vertex !== undefined ? { vertex: Number(target.dataset.vertex) } : {}) }
        : { kind: target.dataset.kind, array: Number(target.dataset.array),
          ...(target.dataset.area !== undefined ? { area: Number(target.dataset.area) } : {}),
          ...(target.dataset.vertex !== undefined ? { vertex: Number(target.dataset.vertex) } : {}) };
      const prior = this.selected;
      const same = prior?.kind === selection.kind && prior?.array === selection.array && prior?.index === selection.index && prior?.area === selection.area;
      this.select(selection);
      this.drag.rotate = selection.kind === "area" && target.dataset.rotate === "true";
      this.drag.edit = this.drag.rotate || (same && (selection.vertex !== undefined || selection.kind === "array"));
      this.drag.selection = selection;
    }
  }
  pointerMove(event) {
    if (!this.pointers.has(event.pointerId)) return;
    this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (this.pinch && this.pointers.size >= 2) {
      const [a, b] = [...this.pointers.values()];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      this.zoom = Math.max(3, Math.min(MAX_MAP_ZOOM, this.pinch.zoom + Math.round(Math.log2(distance / Math.max(1, this.pinch.distance)))));
      const world = project(this.pinch.anchor, this.zoom), r = this.map.getBoundingClientRect();
      this.center = { x: world.x - (a.x + b.x) / 2 + r.left + r.width / 2,
        y: world.y - (a.y + b.y) / 2 + r.top + r.height / 2 };
      this.drawMap(); return;
    }
    const drag = this.drag;
    if (!drag || drag.pointer !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (Math.hypot(dx, dy) > 5) drag.moved = true;
    if (!drag.moved) return;
    if (drag.edit && this.mode === "pan") {
      const s = drag.selection;
      if (drag.rotate) {
        const area = this.site.pv_arrays[s.array].areas[s.area];
        const center = this.screen(areaCenter(area.polygon)), r = this.map.getBoundingClientRect();
        const east = event.clientX - r.left - center.x, north = center.y - (event.clientY - r.top);
        if (Math.hypot(east, north) < 8) return;
        area.azimuth = (Math.round(Math.atan2(east, north) * 180 / Math.PI) + 360) % 360;
        const field = this.shadowRoot.querySelector('#inspector [data-key="azimuth"]');
        if (field) field.value = area.azimuth;
        this.changed();
        return;
      }
      const point = this.coordinate(event);
      if (s.kind === "array") Object.assign(this.site.pv_arrays[s.array], point);
      else if (s.kind === "area") Object.assign(this.site.pv_arrays[s.array].areas[s.area].polygon[s.vertex], point);
      else Object.assign(this.site.shading_objects[s.index].line[s.vertex], point);
      this.changed();
    } else { this.center = { x: drag.center.x - dx, y: drag.center.y - dy }; this.drawMap(); }
  }
  pointerEnd(event, cancelled = false) {
    this.pointers.delete(event.pointerId);
    if (this.map.hasPointerCapture(event.pointerId)) this.map.releasePointerCapture(event.pointerId);
    if (this.pinch) { if (!this.pointers.size) this.pinch = null; this.drag = null; return; }
    const drag = this.drag;
    if (!drag || drag.pointer !== event.pointerId) return;
    this.drag = null;
    if (cancelled) { this.renderInspector(); return; }
    if (drag.edit && drag.moved) { this.renderInspector(); this.updateButtons(); return; }
    if (drag.moved) return;
    if (this.mode === "draw") {
      const limit = this.draw.kind === "object" ? 65 : 64;
      if (this.points.length >= limit) { this.status(this.t.geometryLimit, true); return; }
      if (this.draw.kind === "object" && !this.shadowRoot.getElementById("draft-height").reportValidity()) return;
      this.points.push({ ...this.coordinate(event), ...(this.draw.kind === "object" ? { height: this.draftHeight } : {}) });
      this.drawMap();
    } else if (this.mode === "place") {
      Object.assign(this.item(), this.coordinate(event)); this.mode = "pan";
      this.changed(); this.renderInspector(); this.updateButtons();
    }
  }

  drawMap() {
    if (this.scene3d) this.updateSceneControls();
    this.scene3d?.update(this.site, { selection: this.selected, preview: this.preview, showShadows: this.showShadows, busy: this.busy,
      satellite: this.mapLayer === "google" && this.mapView === "3d",
      satelliteError: (message) => { this.shadowRoot.getElementById("tile-error").textContent = message; } });
    if (!this.map || !this.center || !this.site || !this.map.clientWidth) return;
    const tileZoom = Math.min(MAX_TILE_ZOOM, this.zoom), scale = 2 ** (this.zoom - tileZoom);
    if (this.mapLayer === "google" && this.googleMap) {
      const node = this.shadowRoot.getElementById("google-map");
      const dimensions = `${this.map.clientWidth / scale}px`;
      if (node.style.width !== dimensions || node.style.height !== `${this.map.clientHeight / scale}px`) {
        node.style.width = dimensions;
        node.style.height = `${this.map.clientHeight / scale}px`;
        node.style.transform = `scale(${scale})`;
        window.google.maps.event.trigger(this.googleMap, "resize");
      }
      const point = unproject(this.center, this.zoom);
      this.googleMap.setCenter({ lat: point.latitude, lng: point.longitude });
      this.googleMap.setZoom(tileZoom);
    }
    const width = this.map.clientWidth, height = this.map.clientHeight;
    const left = this.center.x / scale - width / scale / 2, top = this.center.y / scale - height / scale / 2, count = 2 ** tileZoom;
    const needed = new Set();
    for (let x = Math.floor(left / 256); this.mapLayer === "osm" && x <= Math.floor((left + width / scale) / 256); x++) {
      for (let y = Math.floor(top / 256); y <= Math.floor((top + height / scale) / 256); y++) {
        if (y < 0 || y >= count) continue;
        const key = `${tileZoom}/${x}/${y}`; needed.add(key);
        let img = this.tiles.get(key);
        if (!img) {
          img = document.createElement("img"); img.alt = ""; img.draggable = false;
          img.referrerPolicy = "strict-origin-when-cross-origin";
          img.onerror = () => { this.shadowRoot.getElementById("tile-error").textContent = this.t.tiles; };
          img.src = `https://tile.openstreetmap.org/${tileZoom}/${((x % count) + count) % count}/${y}.png`;
          this.tiles.set(key, img); this.shadowRoot.getElementById("tiles").append(img);
        }
        img.style.left = `${(x * 256 - left) * scale}px`; img.style.top = `${(y * 256 - top) * scale}px`;
        img.style.width = `${256 * scale}px`; img.style.height = `${256 * scale}px`;
      }
    }
    for (const [key, img] of this.tiles) if (!needed.has(key)) { img.remove(); this.tiles.delete(key); }
    const shapes = [this.shadowShapes()], handles = [];
    shapes.push(`<defs><marker id="pv-direction-head" markerWidth="14" markerHeight="14" refX="11" refY="7" orient="auto" markerUnits="userSpaceOnUse">
      <path d="M1,1 L12,7 L1,13 Z" fill="#005fcc" stroke="white" stroke-width="1"/></marker></defs>`);
    this.drawSun();
    const vertices = (points, attributes, selectedVertex, color, heights = null) => points.forEach((p, vertex) => {
      handles.push(`<circle ${attributes} data-vertex="${vertex}" cx="${p.x}" cy="${p.y}" r="18" fill="transparent" style="cursor:move"/>
        <circle ${attributes} data-vertex="${vertex}" cx="${p.x}" cy="${p.y}" r="${vertex === selectedVertex ? 8 : 6}" fill="white" stroke="${color}" stroke-width="3" style="cursor:move"/>`);
      if (heights) handles.push(`<text x="${p.x + 10}" y="${p.y - 10}">${heights[vertex]} m</text>`);
    });
    this.site.pv_arrays.forEach((array, index) => {
      if (!array.areas.length) {
        const p = this.screen(array);
        shapes.push(`<circle data-kind="array" data-array="${index}" cx="${p.x}" cy="${p.y}" r="18" fill="transparent"/>
          <circle data-kind="array" data-array="${index}" cx="${p.x}" cy="${p.y}" r="8" fill="#2684ff" stroke="white" stroke-width="3"/>
          <text x="${p.x + 13}" y="${p.y + 4}">${escapeHTML(array.name)}</text>`);
      }
      array.areas.forEach((area, areaIndex) => {
        const points = area.polygon.map((p) => this.screen(p));
        const selected = this.selected?.kind === "area" && this.selected.array === index && this.selected.area === areaIndex;
        const attrs = `data-kind="area" data-array="${index}" data-area="${areaIndex}"`;
        shapes.push(`<polygon ${attrs} points="${points.map((p) => `${p.x},${p.y}`).join(" ")}" fill="${selected ? "#2684ff66" : "#2684ff33"}" stroke="#2684ff" stroke-width="${selected ? 3 : 2}"/>`);
        const center = this.screen(areaCenter(area.polygon));
        if (typeof area.azimuth === "number" && Number.isFinite(area.azimuth)) {
          const angle = area.azimuth * Math.PI / 180, dx = Math.sin(angle), dy = -Math.cos(angle);
          const length = Math.max(48, ...points.map((p) => (p.x - center.x) * dx + (p.y - center.y) * dy + 32));
          const end = { x: center.x + dx * length, y: center.y + dy * length };
          shapes.push(`<g class="pv-direction" pointer-events="none"><title>${escapeHTML(area.name)} · ${this.t.azimuth}: ${area.azimuth}°</title>
            <line x1="${center.x}" y1="${center.y}" x2="${end.x}" y2="${end.y}" stroke="white" stroke-width="6"/>
            <line data-direction-array="${index}" data-direction-area="${areaIndex}" x1="${center.x}" y1="${center.y}" x2="${end.x}" y2="${end.y}"
              stroke="#005fcc" stroke-width="3" marker-end="url(#pv-direction-head)"/>
            <circle cx="${center.x}" cy="${center.y}" r="4" fill="#005fcc" stroke="white" stroke-width="2"/></g>`);
          if (this.mode === "pan") handles.push(`<g class="pv-rotate" ${attrs} data-rotate="true">
            <title>${escapeHTML(area.name)} · ${this.t.rotateDirection}</title>
            <circle cx="${end.x}" cy="${end.y}" r="22" fill="transparent"/>
            <circle cx="${end.x}" cy="${end.y}" r="9" fill="white" stroke="#005fcc" stroke-width="3"/>
            <path d="M${end.x - 4},${end.y + 2} A4,4 0 1,1 ${end.x + 4},${end.y + 2}" fill="none" stroke="#005fcc" stroke-width="2"/>
          </g>`);
        }
        const profile = surfaceProfile(area);
        const heightText = profile?.upper_height != null && Number.isFinite(profile.upper_height)
          ? ` · ${profile.lower_height.toLocaleString(this.language, {maximumFractionDigits:2})}–${profile.upper_height.toLocaleString(this.language, {maximumFractionDigits:2})} m` : "";
        shapes.push(`<text x="${center.x}" y="${center.y}" text-anchor="middle">${escapeHTML(area.name)} · ${area.tilt}°${heightText}</text>`);
        if (selected && this.mode === "pan") vertices(points, attrs, this.selected.vertex, "#2684ff");
      });
    });
    this.site.shading_objects.forEach((obj, index) => {
      const points = obj.line.map((p) => this.screen(p));
      const selected = this.selected?.kind === "object" && this.selected.index === index;
      const attrs = `data-kind="object" data-index="${index}"`;
      const outline = points.map((p) => `${p.x},${p.y}`).join(" ");
      shapes.push(`<polyline ${attrs} points="${outline}" fill="none" stroke="transparent" stroke-width="24"/>
        <polyline ${attrs} points="${outline}" fill="none" stroke="${obj.solid ? "#ef8a21" : "#719e37"}" stroke-width="${selected ? 5 : 3}" ${obj.solid ? "" : 'stroke-dasharray="6 4"'}/>`);
      if (points[0]) shapes.push(`<text x="${points[0].x + 10}" y="${points[0].y - 16}">${escapeHTML(obj.name)}</text>`);
      if (selected && this.mode === "pan") vertices(points, attrs, this.selected.vertex, "#ef8a21", obj.line.map((p) => p.height));
    });
    if (this.points.length) {
      const points = this.points.map((p) => this.screen(p));
      const outline = points.map((p) => `${p.x},${p.y}`).join(" ");
      shapes.push(`<${this.draw.kind === "area" ? "polygon" : "polyline"} points="${outline}" fill="${this.draw.kind === "area" ? "#2684ff33" : "none"}" stroke="${this.draw.kind === "area" ? "#2684ff" : "#ef8a21"}" stroke-width="3" stroke-dasharray="6 4"/>`);
      points.forEach((p, index) => {
        shapes.push(`<circle cx="${p.x}" cy="${p.y}" r="6" fill="${this.draw.kind === "area" ? "#2684ff" : "#ef8a21"}"/>`);
        if (this.draw.kind === "object") shapes.push(`<text x="${p.x + 10}" y="${p.y - 10}">${this.points[index].height} m</text>`);
      });
    }
    this.shadowRoot.getElementById("geometry").innerHTML = shapes.concat(handles).join("");
  }
}

if (!customElements.get("smart-pv-configurator")) customElements.define("smart-pv-configurator", SmartPVConfigurator);
