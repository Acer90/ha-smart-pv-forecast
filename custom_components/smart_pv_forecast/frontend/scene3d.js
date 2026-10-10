import { areaCenter, localPoint, geographicPoint, surfaceProfile, surfaceVertices } from "./geometry.js";
import { escapeHTML } from "./i18n.js";

const radians = Math.PI / 180;
const sameSelection = (a, b) => a?.kind === b?.kind && a?.array === b?.array && a?.area === b?.area && a?.index === b?.index;
const attributes = (s) => `data-scene-kind="${s.kind}" ${s.index !== undefined ? `data-index="${s.index}"` : `data-array="${s.array}" ${s.area !== undefined ? `data-area="${s.area}"` : ""}`}`;

export function project3D(point, camera, width, height) {
  const x = point.x - camera.target.x, y = point.y - camera.target.y, z = point.z - camera.target.z;
  const c = Math.cos(camera.yaw), s = Math.sin(camera.yaw);
  const depth = x * s + y * c;
  return {
    x: width / 2 + (x * c - y * s) * camera.scale,
    y: height / 2 - (z * Math.cos(camera.pitch) + depth * Math.sin(camera.pitch)) * camera.scale,
    depth: z * Math.sin(camera.pitch) - depth * Math.cos(camera.pitch),
  };
}

export function planePoint(screen, z, camera, width, height) {
  const u = (screen.x - width / 2) / camera.scale;
  const v = (height / 2 - screen.y) / camera.scale;
  const depth = (v - (z - camera.target.z) * Math.cos(camera.pitch)) / Math.sin(camera.pitch);
  const c = Math.cos(camera.yaw), s = Math.sin(camera.yaw);
  return { x: camera.target.x + u * c + depth * s, y: camera.target.y - u * s + depth * c, z };
}

export function satelliteFrame(points, origin) {
  const coordinates = points.length ? points : [{ x: -20, y: -20 }, { x: 20, y: 20 }];
  const minX = Math.min(...coordinates.map(p => p.x)), maxX = Math.max(...coordinates.map(p => p.x));
  const minY = Math.min(...coordinates.map(p => p.y)), maxY = Math.max(...coordinates.map(p => p.y));
  const center = geographicPoint({ x: (minX + maxX) / 2, y: (minY + maxY) / 2 }, origin);
  const extent = Math.max(40, maxX - minX, maxY - minY) * 1.5;
  const metersPerPixel = 2 * Math.PI * 6371000 * Math.cos(center.latitude * radians) / 256;
  const zoom = Math.max(0, Math.min(20, Math.floor(Math.log2(640 * metersPerPixel / extent))));
  const size = 256 * 2 ** zoom;
  const sin = Math.sin(center.latitude * radians);
  const world = { x: (center.longitude + 180) / 360 * size, y: (.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * size };
  const corner = (dx, dy) => localPoint({
    longitude: (world.x + dx) / size * 360 - 180,
    latitude: Math.atan(Math.sinh(Math.PI * (1 - 2 * (world.y + dy) / size))) / radians,
  }, origin);
  return { center, zoom, corners: [corner(-320, -320), corner(320, -320), corner(-320, 320)] };
}

export class Scene3D {
  constructor(svg, text, callbacks) {
    this.svg = svg; this.text = text; this.callbacks = callbacks;
    this.mode = "orbit"; this.pointers = new Map();
    this.camera = { yaw: -35 * radians, pitch: 40 * radians, scale: 3, target: { x: 0, y: 0, z: 0 } };
    this.fitPending = true;
    svg.onpointerdown = (event) => this.pointerDown(event);
    svg.onpointermove = (event) => this.pointerMove(event);
    svg.onpointerup = svg.onpointercancel = (event) => this.pointerEnd(event);
    svg.onlostpointercapture = (event) => this.pointerEnd(event);
    svg.addEventListener("wheel", (event) => {
      event.preventDefault(); this.zoom(event.deltaY < 0 ? 1.15 : 1 / 1.15);
    }, { passive: false });
    this.observer = new ResizeObserver(() => this.draw());
    this.observer.observe(svg);
  }
  dispose() {
    this.observer.disconnect(); this.pointers.clear(); this.drag = null;
    clearTimeout(this.satelliteTimer);
    this.satelliteGeneration = (this.satelliteGeneration || 0) + 1;
  }
  update(site, state) {
    this.site = site; Object.assign(this, state);
    if (this.drag?.selection && (!sameSelection(this.drag.selection, this.selection)
      || this.item(this.drag.selection) !== this.drag.item)) this.drag = null;
    if (site && !this.origin) {
      this.origin = site.pv_arrays[0]?.areas[0]?.polygon.length
        ? areaCenter(site.pv_arrays[0].areas[0].polygon) : { latitude: site.latitude, longitude: site.longitude };
    }
    this.draw();
  }
  item(selection = this.selection) {
    if (!selection || !this.site) return null;
    if (selection.kind === "object") return this.site.shading_objects[selection.index];
    const array = this.site.pv_arrays[selection.array];
    return selection.kind === "area" ? array?.areas[selection.area] : array;
  }
  points(selection) {
    const item = this.item(selection);
    return selection.kind === "object" ? item.line : selection.kind === "area" ? item.polygon
      : item.areas.length ? item.areas.flatMap((area) => area.polygon) : [item];
  }
  anchor(selection = this.selection) {
    const item = this.item(selection);
    if (!item) return null;
    const points = this.points(selection), point = localPoint(areaCenter(points), this.origin);
    const profile = selection.kind === "area" ? surfaceProfile(item) : null;
    return { ...point, z: selection.kind === "object" ? Math.max(...item.line.map((p) => p.height))
      : (Number(item.height) || 0) + (profile?.rise != null && Number.isFinite(profile.rise) ? profile.rise / 2 : 0) };
  }
  zoom(factor) {
    this.camera.scale = Math.max(0.001, Math.min(100, this.camera.scale * factor)); this.draw();
  }
  fit() { this.fitPending = true; this.satelliteKey = null; this.draw(); }
  focus(selection) {
    const anchor = this.anchor(selection);
    if (anchor) {
      this.camera.target = { ...anchor, z: anchor.z / 2 };
      const points = this.points(selection).map((p) => localPoint(p, this.origin));
      const radius = Math.max(5, anchor.z, ...points.map((p) => Math.hypot(p.x - anchor.x, p.y - anchor.y)));
      this.camera.scale = Math.max(.001, Math.min(100, this.svg.clientWidth * .25 / radius, this.svg.clientHeight * .25 / radius));
      this.draw();
    }
  }
  meshes() {
    const result = [];
    this.site.pv_arrays.forEach((array, index) => {
      if (!array.areas.length) result.push({ vertices: [{ ...localPoint(array, this.origin), z: array.height }],
        selection: { kind: "array", array: index }, label: array.name, color: "#2684ff", point: true });
      array.areas.forEach((area, areaIndex) => {
        const vertices = surfaceVertices(area, this.origin);
        result.push({ vertices: vertices || area.polygon.map((p) => ({ ...localPoint(p, this.origin), z: area.height })),
          selection: { kind: "area", array: index, area: areaIndex }, color: "#2684ff",
          label: vertices ? area.name : `${area.name} · ${this.text.invalidHeights}`, ghost: !vertices });
      });
    });
    this.site.shading_objects.forEach((object, index) => {
      const points = object.line.map((p) => localPoint(p, this.origin));
      for (let i = 1; i < points.length; i++) result.push({
        vertices: [{ ...points[i - 1], z: 0 }, { ...points[i], z: 0 }, points[i], points[i - 1]],
        selection: { kind: "object", index }, color: object.solid ? "#ef8a21" : "#719e37",
        label: i === 1 ? object.name : "", vegetation: !object.solid,
      });
    });
    return result;
  }
  satelliteLayer(points, project, width, height) {
    if (!this.satellite || !this.site.google_maps_api_key) return "";
    if (this.satelliteKey !== this.site.google_maps_api_key) {
      this.satelliteKey = this.site.google_maps_api_key;
      this.satelliteFrame = satelliteFrame(points, this.origin);
      const frame = this.satelliteFrame, generation = this.satelliteGeneration = (this.satelliteGeneration || 0) + 1;
      clearTimeout(this.satelliteTimer);
      this.satelliteLoaded = false;
      const params = new URLSearchParams({ center: `${frame.center.latitude},${frame.center.longitude}`,
        zoom: String(frame.zoom), size: "640x640", scale: "1", maptype: "satellite", key: this.satelliteKey });
      const image = new Image();
      image.referrerPolicy = "strict-origin-when-cross-origin";
      image.onload = () => {
        if (generation !== this.satelliteGeneration) return;
        clearTimeout(this.satelliteTimer);
        this.satelliteLoaded = true; this.draw();
      };
      const fail = () => {
        if (generation !== this.satelliteGeneration) return;
        clearTimeout(this.satelliteTimer);
        this.satelliteGeneration++;
        if (this.satellite) this.satelliteError(this.text.satellite3dError);
      };
      image.onerror = fail;
      this.satelliteTimer = setTimeout(fail, 20000);
      this.satelliteURL = `https://maps.googleapis.com/maps/api/staticmap?${params}`;
      image.src = this.satelliteURL;
    }
    if (!this.satelliteLoaded) return "";
    const [a, b, c] = this.satelliteFrame.corners.map(project);
    const matrix = [(b.x - a.x) / 640, (b.y - a.y) / 640, (c.x - a.x) / 640, (c.y - a.y) / 640, a.x, a.y];
    const image = `<image href="${escapeHTML(this.satelliteURL)}" width="640" height="640" referrerpolicy="strict-origin-when-cross-origin"/>`;
    const attributionWidth = Math.min(width, 640), attributionHeight = attributionWidth * 40 / 640;
    this.satelliteAttribution = `<svg x="0" y="${height - attributionHeight}" width="${attributionWidth}" height="${attributionHeight}"
      viewBox="0 600 640 40" pointer-events="none">${image}</svg>`;
    return `<g class="satellite-ground" pointer-events="none" transform="matrix(${matrix.join(" ")})">${image}</g>`;
  }
  draw() {
    const width = this.svg.clientWidth, height = this.svg.clientHeight;
    if (!this.site || !width || !height || !this.origin) return;
    const meshes = this.meshes(), points = meshes.flatMap((m) => m.vertices);
    if (this.fitPending) {
      const coordinates = points.length ? points : [{ x: -10, y: -10, z: 0 }, { x: 10, y: 10, z: 10 }];
      const minimum = (key) => Math.min(...coordinates.map((p) => p[key]));
      const maximum = (key) => Math.max(...coordinates.map((p) => p[key]));
      this.camera.target = { x: (minimum("x") + maximum("x")) / 2, y: (minimum("y") + maximum("y")) / 2, z: maximum("z") / 2 };
      this.camera.scale = 1;
      const projected = coordinates.concat(coordinates.map((p) => ({ ...p, z: 0 }))).map((p) => project3D(p, this.camera, width, height));
      this.camera.scale = Math.max(0.001, Math.min(100, width * 0.36 / Math.max(10, ...projected.map((p) => Math.abs(p.x - width / 2))),
        height * 0.32 / Math.max(10, ...projected.map((p) => Math.abs(p.y - height / 2)))));
      this.fitPending = false;
    }
    const project = (p) => project3D(p, this.camera, width, height);
    const path = (vertices) => vertices.map((p, i) => { const s = project(p); return `${i ? "L" : "M"}${s.x},${s.y}`; }).join(" ") + " Z";
    const radius = Math.max(20, width / this.camera.scale / 2);
    const step = 10 ** Math.floor(Math.log10(radius / 3));
    const target = this.camera.target, floor = [];
    this.satelliteAttribution = "";
    const satellite = this.satelliteLayer(points, project, width, height);
    for (let i = -6; i <= 6; i++) {
      for (const alongX of [true, false]) {
        const a = project({ x: target.x + (alongX ? -6 * step : i * step), y: target.y + (alongX ? i * step : -6 * step), z: 0 });
        const b = project({ x: target.x + (alongX ? 6 * step : i * step), y: target.y + (alongX ? i * step : 6 * step), z: 0 });
        floor.push(`<path d="M${a.x},${a.y} L${b.x},${b.y}" fill="none" stroke="#aabecb" stroke-width="1"/>`);
      }
    }
    const north = project({ x: target.x, y: target.y + 3 * step, z: 0 });
    floor.push(`<text x="${north.x}" y="${north.y}">N · ${step} m</text>`);
    const shadows = this.preview && this.showShadows ? [...this.preview.shadows].sort((a, b) => a.reduction - b.reduction) : [];
    const shadowMask = shadows.map((shadow) => `<path d="${shadow.polygons.map((polygon) =>
      path(polygon.map((p) => ({ ...localPoint(p, this.origin), z: this.preview.height })))).join(" ")}" fill="rgb(${[1, 2, 3].map(() => Math.round(shadow.reduction * 255)).join(",")})"/>`).join("");
    const shadowLayer = shadows.length ? `<defs><mask id="scene-shadow-mask" maskUnits="userSpaceOnUse" x="0" y="0" width="${width}" height="${height}" style="mask-type:luminance">
      <rect width="100%" height="100%" fill="black"/>${shadowMask}</mask></defs>
      <rect width="100%" height="100%" fill="#263849" opacity=".55" mask="url(#scene-shadow-mask)" pointer-events="none"/>` : "";
    meshes.forEach((mesh) => { mesh.depth = mesh.vertices.reduce((sum, p) => sum + project(p).depth, 0) / mesh.vertices.length; });
    meshes.sort((a, b) => a.depth - b.depth);
    const faces = meshes.map((mesh) => {
      const selected = sameSelection(mesh.selection, this.selection) || (this.selection?.kind === "array" && mesh.selection.array === this.selection.array);
      const attrs = attributes(mesh.selection), center = mesh.vertices.reduce((sum, p) => ({
        x: sum.x + p.x / mesh.vertices.length, y: sum.y + p.y / mesh.vertices.length, z: sum.z + p.z / mesh.vertices.length,
      }), { x: 0, y: 0, z: 0 }), label = project(center);
      return `<g ${attrs}><title>${escapeHTML(mesh.label)}</title>${mesh.point
        ? `<circle cx="${label.x}" cy="${label.y}" r="12" fill="${mesh.color}" stroke="white" stroke-width="3"/>`
        : `<path d="${path(mesh.vertices)}" fill="none" stroke="transparent" stroke-width="24"/>
          <path d="${path(mesh.vertices)}" fill="${mesh.ghost ? "none" : mesh.color}" fill-opacity="${mesh.vegetation ? ".4" : ".75"}"
          stroke="${selected ? "#003e91" : mesh.color}" stroke-width="${selected ? 3 : 1.5}" ${mesh.ghost ? 'stroke-dasharray="6 4"' : ""}/>`}
        ${mesh.label ? `<text x="${label.x}" y="${label.y - 8}" text-anchor="middle">${escapeHTML(mesh.label)}</text>` : ""}</g>`;
    }).join("");
    const handles = [];
    const anchor = this.anchor();
    if (anchor) {
      const attrs = attributes(this.selection), item = this.item(), a = project(anchor);
      if (this.mode === "height") {
        const b = project({ ...anchor, z: anchor.z + Math.max(5, 50 / this.camera.scale) });
        handles.push(`<g ${attrs}><path d="M${a.x},${a.y} L${b.x},${b.y}" stroke="#8e44ad" stroke-width="4"/>
          <circle cx="${b.x}" cy="${b.y}" r="22" fill="transparent"/><circle data-scene-handle="height" cx="${b.x}" cy="${b.y}" r="10" fill="#8e44ad" stroke="white" stroke-width="3"/></g>`);
      } else if (this.mode === "rotate" && Number.isFinite(item.azimuth)) {
        const r = Math.max(8, 65 / this.camera.scale), angle = item.azimuth * radians;
        const circle = Array.from({ length: 49 }, (_, i) => ({ x: anchor.x + Math.sin(i * Math.PI / 24) * r, y: anchor.y + Math.cos(i * Math.PI / 24) * r, z: anchor.z }));
        const b = project({ x: anchor.x + Math.sin(angle) * r, y: anchor.y + Math.cos(angle) * r, z: anchor.z });
        handles.push(`<path d="${path(circle)}" fill="none" stroke="#005fcc" stroke-width="2" stroke-dasharray="5 4" pointer-events="none"/>
          <g ${attrs}><path d="M${a.x},${a.y} L${b.x},${b.y}" stroke="#005fcc" stroke-width="4"/>
          <circle cx="${b.x}" cy="${b.y}" r="22" fill="transparent"/><circle data-scene-handle="rotate" cx="${b.x}" cy="${b.y}" r="10" fill="white" stroke="#005fcc" stroke-width="3"/></g>`);
      } else if (this.mode === "move") handles.push(`<g ${attrs}><circle cx="${a.x}" cy="${a.y}" r="22" fill="transparent"/>
        <circle data-scene-handle="move" cx="${a.x}" cy="${a.y}" r="10" fill="#00897b" stroke="white" stroke-width="3"/></g>`);
    }
    if (this.preview?.sun.elevation > 0) {
      const az = this.preview.sun.azimuth * radians, elevation = this.preview.sun.elevation * radians;
      const p = project({ x: target.x + Math.sin(az) * Math.cos(elevation) * radius * .7,
        y: target.y + Math.cos(az) * Math.cos(elevation) * radius * .7, z: Math.sin(elevation) * radius * .7 });
      handles.push(`<g pointer-events="none"><circle cx="${p.x}" cy="${p.y}" r="12" fill="#ffc843" stroke="#b77a00" stroke-width="2"/>
        <text x="${p.x}" y="${p.y - 20}" text-anchor="middle">${this.text.sun}</text></g>`);
    }
    this.svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    this.svg.innerHTML = satellite + floor.join("") + shadowLayer + faces + handles.join("") + this.satelliteAttribution;
  }
  cursor(event) {
    const rect = this.svg.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  pointerDown(event) {
    if (!this.site || this.busy || event.button !== 0) return;
    event.preventDefault();
    const cursor = this.cursor(event);
    this.pointers.set(event.pointerId, cursor); this.svg.setPointerCapture(event.pointerId);
    if (this.pointers.size === 2) {
      const [a, b] = [...this.pointers.values()];
      this.pinch = { distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), scale: this.camera.scale,
        midpoint: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, target: { ...this.camera.target } };
      this.drag = null; return;
    }
    if (this.pointers.size > 2) return;
    const hit = event.target.closest("[data-scene-kind]");
    let selection = null;
    if (hit) {
      selection = hit.dataset.sceneKind === "object" ? { kind: "object", index: Number(hit.dataset.index) }
        : { kind: hit.dataset.sceneKind, array: Number(hit.dataset.array), ...(hit.dataset.area !== undefined ? { area: Number(hit.dataset.area) } : {}) };
      this.callbacks.select(selection);
    }
    const anchor = selection ? this.anchor(selection) : null;
    const start = anchor ? planePoint(cursor, anchor.z, this.camera, this.svg.clientWidth, this.svg.clientHeight) : null;
    const item = selection ? this.item(selection) : null;
    this.drag = { pointer: event.pointerId, cursor, mode: selection ? this.mode : "orbit", selection, item, anchor, start,
      camera: structuredClone(this.camera), points: selection ? structuredClone(this.points(selection)) : [],
      heights: item ? selection.kind === "object" ? item.line.map((p) => p.height) : [item.height] : [],
      azimuth: item?.azimuth, moved: false };
  }
  pointerMove(event) {
    if (!this.pointers.has(event.pointerId)) return;
    const cursor = this.cursor(event); this.pointers.set(event.pointerId, cursor);
    if (this.pinch && this.pointers.size >= 2) {
      const [a, b] = [...this.pointers.values()], pinch = this.pinch;
      this.camera.scale = Math.max(.001, Math.min(100, pinch.scale * Math.hypot(a.x - b.x, a.y - b.y) / pinch.distance));
      const original = planePoint(pinch.midpoint, 0, { ...this.camera, target: pinch.target, scale: pinch.scale }, this.svg.clientWidth, this.svg.clientHeight);
      const current = planePoint({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, 0, { ...this.camera, target: pinch.target }, this.svg.clientWidth, this.svg.clientHeight);
      this.camera.target = { ...pinch.target, x: pinch.target.x + original.x - current.x, y: pinch.target.y + original.y - current.y };
      this.draw(); return;
    }
    const drag = this.drag;
    if (!drag || drag.pointer !== event.pointerId) return;
    const dx = cursor.x - drag.cursor.x, dy = cursor.y - drag.cursor.y;
    if (Math.hypot(dx, dy) < 5 && !drag.moved) return;
    drag.moved = true;
    if (drag.mode === "orbit") {
      this.camera.yaw = drag.camera.yaw + dx * .008;
      this.camera.pitch = Math.max(20 * radians, Math.min(85 * radians, drag.camera.pitch + dy * .006));
      this.draw(); return;
    }
    if (this.busy) return;
    if (drag.mode === "move") {
      const point = planePoint(cursor, drag.anchor.z, drag.camera, this.svg.clientWidth, this.svg.clientHeight);
      this.callbacks.edit(drag.selection, { kind: "move", points: drag.points, x: point.x - drag.start.x, y: point.y - drag.start.y });
    } else if (drag.mode === "height") {
      this.callbacks.edit(drag.selection, { kind: "height", heights: drag.heights, delta: -dy / (drag.camera.scale * Math.cos(drag.camera.pitch)) });
    } else if (drag.mode === "rotate") {
      const point = planePoint(cursor, drag.anchor.z, drag.camera, this.svg.clientWidth, this.svg.clientHeight);
      if (Math.hypot(point.x - drag.anchor.x, point.y - drag.anchor.y) < 1 / drag.camera.scale) return;
      const angle = Math.atan2(point.x - drag.anchor.x, point.y - drag.anchor.y);
      const startAngle = Math.atan2(drag.start.x - drag.anchor.x, drag.start.y - drag.anchor.y);
      this.callbacks.edit(drag.selection, { kind: "rotate", azimuth: ((Math.round(drag.azimuth + (angle - startAngle) / radians) % 360) + 360) % 360 });
    }
  }
  pointerEnd(event) {
    this.pointers.delete(event.pointerId);
    if (this.svg.hasPointerCapture(event.pointerId)) this.svg.releasePointerCapture(event.pointerId);
    if (this.pinch) { if (!this.pointers.size) this.pinch = null; this.drag = null; return; }
    if (this.drag?.pointer === event.pointerId) this.drag = null;
  }
}
