const MINUTE = 60000;

export function dayTimeline(date, timezone, now = Date.now()) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(now)) throw Error("Invalid history date");
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", hourCycle: "h23",
  });
  const nominal = Date.parse(`${date}T00:00:00Z`);
  const intervals = [];
  // Scan real minutes so skipped/repeated local hours and fractional UTC offsets stay correct.
  for (let time = nominal - 86400000; time < nominal + 172800000; time += MINUTE) {
    const parts = Object.fromEntries(formatter.formatToParts(time).map(p => [p.type, p.value]));
    if (`${parts.year}-${parts.month}-${parts.day}` !== date) continue;
    const hour = Number(parts.hour);
    const previous = intervals.at(-1);
    if (previous && previous.hour === hour && previous.end === time) previous.end += MINUTE;
    else intervals.push({ start: time, end: time + MINUTE, hour });
  }
  if (!intervals.length) throw Error("Local history day does not exist");
  const start = intervals[0].start, end = intervals.at(-1).end;
  return { start, end, until: Math.max(start, Math.min(now, end)), intervals };
}

function powerEvents(states) {
  if (!Array.isArray(states)) throw Error("Invalid sensor history response");
  return states.map(state => {
    const timestamp = state.lu ?? state.last_updated ?? state.last_changed;
    const time = typeof timestamp === "number" ? timestamp * 1000 : Date.parse(timestamp);
    if (!Number.isFinite(time)) throw Error("Invalid sensor history timestamp");
    const raw = state.s ?? state.state;
    const unit = (state.a ?? state.attributes)?.unit_of_measurement;
    const value = typeof raw === "string" && raw.trim() !== "" ? Number(raw) : NaN;
    return { time, value: Number.isFinite(value) && value >= 0 && ["W", "kW"].includes(unit)
      ? value * (unit === "kW" ? 1000 : 1) : null };
  }).sort((a, b) => a.time - b.time);
}

export function hourlyPower(states, timeline) {
  return hourlyEvents(powerEvents(states), timeline);
}

function hourlyEvents(events, timeline) {
  const energy = Array(24).fill(0), known = Array(24).fill(0), elapsed = Array(24).fill(0);
  for (const interval of timeline.intervals) {
    const end = Math.min(interval.end, timeline.until);
    if (end <= interval.start) continue;
    elapsed[interval.hour] += end - interval.start;
    for (let i = 0; i < events.length; i++) {
      const event = events[i];
      const left = Math.max(interval.start, event.time);
      const right = Math.min(end, events[i + 1]?.time ?? timeline.until);
      if (event.value === null || right <= left) continue;
      known[interval.hour] += right - left;
      energy[interval.hour] += event.value * (right - left);
    }
  }
  return {
    values: energy.map((sum, hour) => known[hour] > 0
      ? sum / known[hour] : null),
    minutes: known.map(ms => ms / MINUTE),
    elapsedMinutes: elapsed.map(ms => ms / MINUTE),
  };
}

export function totalHourlyPower(histories, timeline) {
  return simultaneousHourlyPower(histories, timeline).total;
}

function simultaneousHourlyPower(histories, timeline) {
  const current = Array(histories.length).fill(null);
  const events = histories.flatMap((states, index) => powerEvents(states).map(event => ({ ...event, index })))
    .sort((a, b) => a.time - b.time);
  const combined = events.map(event => {
    current[event.index] = event.value;
    return { time: event.time, values: [...current],
      complete: current.every(value => value !== null) };
  });
  return {
    total: hourlyEvents(combined.map(event => ({ time: event.time,
      value: event.complete ? event.values.reduce((sum, value) => sum + value, 0) : null })), timeline),
    inverters: histories.map((_, index) => hourlyEvents(combined.map(event => ({
      time: event.time, value: event.complete ? event.values[index] : null,
    })), timeline)),
  };
}

function stringAssignments(arrays) {
  return arrays.map(array => array.areas.flatMap(area => (area.strings || []).map(string => ({
    label: `${area.name} / ${string.name}`, entity: string.power_entity,
  }))));
}

async function assignmentHistory(hass, assignments, analysis, now) {
  const entities = [...new Set(assignments.flat().map(string => string.entity))];
  const timeline = dayTimeline(analysis.date, analysis.timezone, now);
  let history = {};
  if (entities.length && timeline.until > timeline.start) history = await hass.callWS({
    type: "history/history_during_period",
    start_time: new Date(timeline.start).toISOString(),
    end_time: new Date(timeline.until).toISOString(),
    entity_ids: entities, include_start_time_state: true,
    significant_changes_only: false, minimal_response: false, no_attributes: false,
  });
  if (!history || typeof history !== "object" || Array.isArray(history)) throw Error("Invalid history response");
  const averages = new Map(entities.map(entity => [entity, hourlyPower(history[entity] || [], timeline)]));
  return { timeline, history, assignments: assignments.map(strings => strings.map(string => ({
    ...string, ...averages.get(string.entity),
  }))) };
}

export async function loadStringHistory(hass, arrays, analysis, now = Date.now()) {
  const assignments = stringAssignments(arrays);
  if (!assignments.flat().length) return assignments;
  return (await assignmentHistory(hass, assignments, analysis, now)).assignments;
}

export async function loadSiteHistory(hass, site, analysis, now = Date.now()) {
  const inverters = site.inverters || [];
  const assignments = stringAssignments(site.pv_arrays);
  const inverterEntities = inverters.filter(inverter => inverter.power_entity)
    .map(inverter => ({ label: inverter.name, entity: inverter.power_entity }));
  const result = await assignmentHistory(hass, [...assignments, inverterEntities], analysis, now);
  const totalStatus = !inverters.length ? "unconfigured"
    : inverterEntities.length !== inverters.length ? "incomplete"
    : new Set(inverterEntities.map(inverter => inverter.entity)).size !== inverters.length ? "duplicate" : null;
  const simultaneous = totalStatus ? null : simultaneousHourlyPower(inverterEntities.map(inverter =>
    result.history[inverter.entity] || []), result.timeline);
  return {
    strings: result.assignments.slice(0, assignments.length),
    until: new Date(result.timeline.until).toISOString(),
    inverters: totalStatus ? [] : result.assignments.at(-1).map((measurement, index) => ({
      ...measurement, ...simultaneous.inverters[index], inverter: true,
    })),
    total: simultaneous?.total ?? null,
    totalStatus,
  };
}
