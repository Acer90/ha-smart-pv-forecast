const RADIANS = Math.PI / 180;
const RADIUS = 6371000;
const longitudeDelta = (value) => ((value + 180) % 360 + 360) % 360 - 180;

export function areaCenter(polygon) {
  const first = polygon[0].longitude;
  return {
    latitude: polygon.reduce((sum, p) => sum + p.latitude, 0) / polygon.length,
    longitude: longitudeDelta(first + polygon.reduce((sum, p) =>
      sum + longitudeDelta(p.longitude - first), 0) / polygon.length),
  };
}

export function localPoint(point, origin) {
  return {
    x: RADIUS * longitudeDelta(point.longitude - origin.longitude) * RADIANS * Math.cos(origin.latitude * RADIANS),
    y: RADIUS * (point.latitude - origin.latitude) * RADIANS,
    z: point.height ?? 0,
  };
}

export function geographicPoint(point, origin) {
  return {
    latitude: origin.latitude + point.y / (RADIUS * RADIANS),
    longitude: longitudeDelta(origin.longitude + point.x / (RADIUS * RADIANS * Math.cos(origin.latitude * RADIANS))),
  };
}

function surfaceFrame(area) {
  if (!area.polygon?.length || ![area.height, area.tilt, area.azimuth].every((v) =>
    typeof v === "number" && Number.isFinite(v))) return null;
  if (area.height < 0 || area.height > 1000 || area.tilt < 0 || area.tilt > 90
    || area.azimuth < 0 || area.azimuth > 360 || (area.tilt === 90 && area.vertical_height != null
      && (!Number.isFinite(area.vertical_height) || area.vertical_height <= 0))) return null;
  const center = areaCenter(area.polygon), angle = area.azimuth * RADIANS;
  const east = Math.sin(angle), north = Math.cos(angle);
  const points = area.polygon.map((p) => localPoint(p, center));
  const projected = points.map((p) => p.x * east + p.y * north);
  const minimum = Math.min(...projected), maximum = Math.max(...projected), span = maximum - minimum;
  const rise = area.tilt === 90 ? area.vertical_height ?? null : span * Math.tan(area.tilt * RADIANS);
  return { center, points, projected, minimum, maximum, span, rise, east, north };
}

export function surfaceProfile(area) {
  const frame = surfaceFrame(area);
  if (!frame) return null;
  return { span: frame.span, rise: frame.rise, lower_height: area.height,
    upper_height: frame.rise === null ? null : area.height + frame.rise };
}

export function surfaceVertices(area, origin) {
  const frame = surfaceFrame(area);
  if (!frame || frame.rise === null || !frame.span || !Number.isFinite(frame.rise)
    || area.height + frame.rise > 1000) return null;
  return frame.points.map((p, index) => {
    const q = frame.projected[index];
    const shift = area.tilt === 90 ? (frame.minimum + frame.maximum) / 2 - q : 0;
    const point = geographicPoint({ x: p.x + frame.east * shift, y: p.y + frame.north * shift }, frame.center);
    return { ...localPoint(point, origin), z: area.height + (frame.maximum - q) / frame.span * frame.rise };
  });
}
