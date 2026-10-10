"""Validated site geometry and deterministic, hourly object shading."""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

EARTH_RADIUS = 6371000.0
MOUNTING_TYPES = ("roof_parallel", "elevated", "ground", "facade", "balcony")


def _number(value: Any, low: float, high: float, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label}: expected a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"{label}: expected a number") from err
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{label}: must be between {low} and {high}")
    return result


def _list(value: Any, label: str, maximum: int) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as err:
            raise ValueError(f"{label}: invalid JSON") from err
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label}: expected a list with at most {maximum} items")
    return value


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError("Name must contain 1-100 characters")
    return value.strip()


def normalize_strings(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    names: set[str] = set()
    entities: set[str] = set()
    for item in _list(value, "PV strings", 16):
        if not isinstance(item, dict):
            raise ValueError("PV string must be an object")
        name = _name(item.get("name"))
        entity = item.get("power_entity")
        if not isinstance(entity, str) or re.fullmatch(r"sensor\.[a-z0-9_]+", entity) is None:
            raise ValueError("PV string needs a power sensor entity ID")
        if name.casefold() in names or entity in entities:
            raise ValueError("PV string names and sensors must be unique within each area")
        names.add(name.casefold())
        entities.add(entity)
        result.append({"name": name, "power_entity": entity})
    return result


def normalize_arrays(value: Any) -> list[dict[str, Any]]:
    """Keep legacy arrays valid, without silently dropping malformed arrays."""
    items = _list(value, "PV arrays", 64)
    if not items:
        raise ValueError("At least one PV array is required")
    arrays: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("PV array must be an object")
        mounting_type = item.get("mounting_type", "roof_parallel")
        if mounting_type not in MOUNTING_TYPES:
            raise ValueError("Invalid PV mounting type")
        array: dict[str, Any] = {
            "name": _name(item.get("name", f"PV {index + 1}")),
            "mounting_type": mounting_type,
            "peak": _number(item.get("peak"), 0.01, 1e9, "Peak power (Wp)"),
            "azimuth": _number(item.get("azimuth"), 0, 360, "Azimuth"),
            "tilt": _number(item.get("tilt"), 0, 90, "Tilt"),
            "height": _number(item.get("height", 0), 0, 1000, "Panel height (m)"),
            "inverter_id": item.get("inverter_id", ""),
        }
        if not isinstance(array["inverter_id"], str):
            raise ValueError("Invalid inverter assignment")
        if "latitude" in item or "longitude" in item:
            array["latitude"] = _number(item.get("latitude"), -85, 85, "Latitude")
            array["longitude"] = _number(item.get("longitude"), -180, 180, "Longitude")
        areas: list[dict[str, Any]] = []
        for area_index, area in enumerate(_list(item.get("areas", []), "PV areas", 32)):
            if not isinstance(area, dict):
                raise ValueError("PV area must be an object")
            mounting = area.get("mounting_type", array["mounting_type"])
            if mounting not in MOUNTING_TYPES:
                raise ValueError("Invalid PV area mounting type")
            areas.append({
                "name": _name(area.get("name", f"Area {area_index + 1}")),
                "polygon": normalize_polygon(area.get("polygon")),
                "peak": _number(area.get("peak", array["peak"]), 0.01, 1e9, "Area peak (Wp)"),
                "azimuth": _number(area.get("azimuth", array["azimuth"]), 0, 360, "Area azimuth"),
                "tilt": _number(area.get("tilt", array["tilt"]), 0, 90, "Area tilt"),
                "height": _number(area.get("height", array["height"]), 0, 1000, "Area height (m)"),
                "mounting_type": mounting,
                "vertical_height": _number(area["vertical_height"], 0.01, 1000, "Vertical extent (m)")
                if area.get("vertical_height") is not None else None,
                "strings": normalize_strings(area.get("strings", [])),
            })
        array["areas"] = areas
        if areas:
            array["peak"] = sum(a["peak"] for a in areas)
            center = polygon_center(areas[0]["polygon"])
            array.update(center)
        arrays.append(array)
    if sum(max(1, len(a["areas"])) for a in arrays) > 128:
        raise ValueError("At most 128 PV surfaces per site")
    if sum(len(area["strings"]) for array in arrays for area in array["areas"]) > 256:
        raise ValueError("At most 256 PV string assignments per site")
    return arrays


def normalize_inverters(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, item in enumerate(_list(value, "Inverters", 64)):
        if not isinstance(item, dict):
            raise ValueError("Inverter must be an object")
        identifier = item.get("id")
        if not isinstance(identifier, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier) is None or identifier in ids:
            raise ValueError("Inverter IDs must be unique and contain letters, digits, underscores or hyphens")
        ids.add(identifier)
        inverter = {
            "id": identifier, "name": _name(item.get("name", f"Inverter {index + 1}")),
            "limit_w": _number(item.get("limit_w"), 1, 1e9, "Inverter AC limit (W)"),
        }
        for key in ("power_entity", "energy_entity"):
            entity = item.get(key, "")
            if not isinstance(entity, str) or (entity and re.fullmatch(r"sensor\.[a-z0-9_]+", entity) is None):
                raise ValueError("Inverter sensor must be a sensor entity ID")
            inverter[key] = entity
        result.append(inverter)
    return result


def validate_inverter_assignments(arrays: list[dict[str, Any]], inverters: list[dict[str, Any]]) -> None:
    ids = {i["id"] for i in inverters}
    if any(a["inverter_id"] and a["inverter_id"] not in ids for a in arrays):
        raise ValueError("PV array references an unknown inverter")


def normalize_settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Forecast settings must be an object")
    defaults = {
        "damping_morning": 0.0, "damping_evening": 0.0,
        "horizon": 0, "open_meteo_forecast_days": 7, "daily_yield_sensor_days": 4,
        "inverter_size": None,
        "forecast_low_kwh": 15.0, "forecast_high_kwh": 25.0,
    }
    if set(value) - set(defaults):
        raise ValueError("Unknown forecast setting")
    values = {**defaults, **value}
    result: dict[str, Any] = {}
    for key, high in (("damping_morning", 1), ("damping_evening", 1), ("horizon", 90)):
        result[key] = _number(values[key], 0, high, key)
    for key, low in (("open_meteo_forecast_days", 1), ("daily_yield_sensor_days", 2)):
        number = _number(values[key], low, 16, key)
        if not number.is_integer():
            raise ValueError("Forecast days must be integers")
        result[key] = int(number)
    result["inverter_size"] = None if values["inverter_size"] is None else _number(values["inverter_size"], 1, 1e9, "Site AC limit")
    for key in ("forecast_low_kwh", "forecast_high_kwh"):
        result[key] = _number(values[key], 0, 1e6, key)
    if result["forecast_high_kwh"] <= result["forecast_low_kwh"]:
        raise ValueError("Forecast orange/green threshold must exceed the red/orange threshold")
    return result


def _local_point(latitude: float, longitude: float, point: dict[str, float]) -> tuple[float, float]:
    delta_lon = (point["longitude"] - longitude + 180) % 360 - 180
    return (
        EARTH_RADIUS * math.radians(delta_lon) * math.cos(math.radians(latitude)),
        EARTH_RADIUS * math.radians(point["latitude"] - latitude),
    )


def _cross(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _subtract(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return a[0] - b[0], a[1] - b[1]


def _segments_intersect(a: tuple[float, float], b: tuple[float, float],
                        c: tuple[float, float], d: tuple[float, float]) -> bool:
    ab, cd = _subtract(b, a), _subtract(d, c)
    denominator = _cross(ab, cd)
    if abs(denominator) < 1e-9:
        if abs(_cross(_subtract(c, a), ab)) > 1e-9:
            return False
        return (
            max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0]))
            and max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1]))
        )
    offset = _subtract(c, a)
    return 0 <= _cross(offset, cd) / denominator <= 1 and 0 <= _cross(offset, ab) / denominator <= 1


def normalize_polygon(value: Any) -> list[dict[str, float]]:
    polygon = [_point(p) for p in _list(value, "Polygon", 64)]
    if len(polygon) > 1 and polygon[0] == polygon[-1]:
        polygon.pop()
    if len(polygon) < 3:
        raise ValueError("Polygon needs at least three different points")
    local = [_local_point(polygon[0]["latitude"], polygon[0]["longitude"], p) for p in polygon]
    if len(set(local)) != len(local):
        raise ValueError("Polygon contains duplicate points")
    if max(math.hypot(*p) for p in local) > 10000:
        raise ValueError("Geometry must be local (maximum extent: 10 km)")
    area = abs(sum(_cross(p, local[(i + 1) % len(local)]) for i, p in enumerate(local))) / 2
    if area < 0.01:
        raise ValueError("Polygon area must be at least 0.01 square metres")
    for i in range(len(local)):
        for j in range(i + 1, len(local)):
            if j == i + 1 or (i == 0 and j == len(local) - 1):
                continue
            if _segments_intersect(local[i], local[(i + 1) % len(local)],
                                   local[j], local[(j + 1) % len(local)]):
                raise ValueError("Polygon edges must not cross or touch")
    return polygon


def _point(point: Any) -> dict[str, float]:
    if not isinstance(point, dict):
        raise ValueError("Point must contain latitude and longitude")
    return {
        "latitude": _number(point.get("latitude"), -85, 85, "Latitude"),
        "longitude": _number(point.get("longitude"), -180, 180, "Longitude"),
    }


def polygon_center(polygon: list[dict[str, float]]) -> dict[str, float]:
    lon = polygon[0]["longitude"]
    return {
        "latitude": sum(p["latitude"] for p in polygon) / len(polygon),
        "longitude": (lon + sum((p["longitude"] - lon + 180) % 360 - 180 for p in polygon) / len(polygon) + 180) % 360 - 180,
    }


def flatten_arrays(arrays: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[int]]:
    """Weather calls and shading use each surface's individual installation."""
    surfaces: list[dict[str, Any]] = []
    parents: list[int] = []
    for index, array in enumerate(arrays):
        for area in array["areas"] or [None]:
            surface = dict(array)
            if area is not None:
                surface.update({k: v for k, v in area.items() if k != "polygon"})
                surface.update(polygon_center(area["polygon"]))
                surface["areas"] = [area]
            surfaces.append(surface)
            parents.append(index)
    return surfaces, parents


def _annual_date(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 5:
        raise ValueError("Season dates must use MM-DD")
    try:
        parsed = datetime.strptime(f"2000-{value}", "%Y-%m-%d")
    except ValueError as err:
        raise ValueError("Invalid season date (MM-DD)") from err
    if parsed.strftime("%m-%d") != value:
        raise ValueError("Season dates must use MM-DD")
    return value


def normalize_objects(value: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for index, item in enumerate(_list(value, "Shading objects", 128)):
        if not isinstance(item, dict):
            raise ValueError("Shading object must be an object")
        legacy = normalize_polygon(item["polygon"]) if "polygon" in item and "line" not in item else None
        height = _number(item.get("height", 5), 0.01, 1000, "Object height (m)")
        points = item.get("line")
        if points is None and legacy is not None:
            points = [{**p, "height": height} for p in legacy + [legacy[0]]]
        line: list[dict[str, float]] = []
        for point in _list(points, "Shading line", 65):
            location = _point(point)
            location["height"] = _number(point.get("height", height), 0.01, 1000, "Point height (m)")
            line.append(location)
        if len(line) < 2:
            raise ValueError("A shading line needs at least two points")
        local = [_local_point(line[0]["latitude"], line[0]["longitude"], p) for p in line]
        if max(math.hypot(*p) for p in local) > 10000:
            raise ValueError("Geometry must be local (maximum extent: 10 km)")
        if any(math.dist(a, b) < 0.01 for a, b in zip(local, local[1:])):
            raise ValueError("Adjacent line points must be at least 1 cm apart")
        solid = item.get("solid", True)
        if not isinstance(solid, bool):
            raise ValueError("Solid must be true or false")
        mode = item.get("foliage_mode", "auto")
        if mode not in ("auto", "leaf_on", "leaf_off"):
            raise ValueError("Invalid foliage mode")
        on, off = _annual_date(item.get("leaf_on", "04-01")), _annual_date(item.get("leaf_off", "11-01"))
        if on == off:
            raise ValueError("Leaf-on and leaf-off dates must differ")
        transmission = _number(item.get("transmission", 0.2 if legacy else 0), 0, 1, "Remaining radiation")
        obj = {
            "name": _name(item.get("name", f"Object {index + 1}")),
            "height": height, "line": line, "solid": solid,
            "transmission": transmission,
            "reduction_leaf_on": _number(item.get("reduction_leaf_on", 0.9), 0, 1, "Leaf-on reduction"),
            "reduction_leaf_off": _number(item.get("reduction_leaf_off", 0.5), 0, 1, "Leaf-off reduction"),
            "leaf_on": on, "leaf_off": off, "foliage_mode": mode,
        }
        if legacy is not None:
            obj["polygon"] = legacy
        objects.append(obj)
    return objects


def object_transmission(obj: dict[str, Any], when: date) -> float:
    if obj["solid"]:
        return obj["transmission"]
    mode = obj["foliage_mode"]
    day = when.strftime("%m-%d")
    on, off = obj["leaf_on"], obj["leaf_off"]
    leaves = (on <= day < off if on < off else day >= on or day < off) if mode == "auto" else mode == "leaf_on"
    return 1 - obj["reduction_leaf_on" if leaves else "reduction_leaf_off"]


def preview_time(value: str | None, timezone_name: str, now: datetime) -> datetime:
    """Interpret the picker in HA's timezone, rejecting nonexistent clock hours."""
    tz = ZoneInfo(timezone_name)
    if value is None:
        return now.astimezone(tz)
    try:
        when = datetime.fromisoformat(value)
    except ValueError as err:
        raise ValueError("Select a valid preview date and time") from err
    if not 2000 <= when.year <= 2100:
        raise ValueError("Preview year must be between 2000 and 2100")
    if when.tzinfo is None:
        local = when.replace(tzinfo=tz)
        if local.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != when:
            raise ValueError("Selected local time does not exist because of the clock change")
        return local
    return when.astimezone(tz)


def project_shadows(objects: list[dict[str, Any]], latitude: float, longitude: float,
                    height: float, azimuth: float, elevation: float,
                    when: date) -> tuple[list[dict[str, Any]], bool]:
    """Project validated vertical contours onto one horizontal reference plane."""
    shadows: list[dict[str, Any]] = []
    truncated = False
    if elevation <= 0:
        return shadows, truncated
    direction = (math.sin(math.radians(azimuth)), math.cos(math.radians(azimuth)))
    slope = math.tan(math.radians(elevation))

    def geographic(point: tuple[float, float]) -> dict[str, float]:
        return {
            "latitude": latitude + math.degrees(point[1] / EARTH_RADIUS),
            "longitude": (longitude + math.degrees(point[0] / EARTH_RADIUS /
                                                  math.cos(math.radians(latitude))) + 180) % 360 - 180,
        }

    for index, obj in enumerate(objects):
        reduction = 1 - object_transmission(obj, when)
        if reduction <= 0:
            continue
        polygons: list[list[dict[str, float]]] = []
        line = [(*_local_point(latitude, longitude, p), p["height"]) for p in obj["line"]]
        for start, end in zip(line, line[1:]):
            if max(start[2], end[2]) <= height:
                continue
            if min(start[2], end[2]) < height:
                fraction = (height - start[2]) / (end[2] - start[2])
                intersection = (start[0] + (end[0] - start[0]) * fraction,
                                start[1] + (end[1] - start[1]) * fraction, height)
                if start[2] < height:
                    start = intersection
                else:
                    end = intersection
            projected: list[tuple[float, float]] = []
            for point in (start, end):
                length = (point[2] - height) / slope
                projected.append((point[0] - direction[0] * length,
                                  point[1] - direction[1] * length))
            polygon = [(start[0], start[1]), (end[0], end[1]), projected[1], projected[0]]
            def along(point: tuple[float, float]) -> float:
                return -point[0] * direction[0] - point[1] * direction[1]
            limit = max(along(polygon[0]), along(polygon[1])) + 10000
            if any(along(p) > limit for p in polygon):
                truncated = True
                clipped: list[tuple[float, float]] = []
                for previous, current in zip(polygon[-1:] + polygon[:-1], polygon):
                    previous_inside, current_inside = along(previous) <= limit, along(current) <= limit
                    if previous_inside != current_inside:
                        fraction = (limit - along(previous)) / (along(current) - along(previous))
                        clipped.append((previous[0] + (current[0] - previous[0]) * fraction,
                                        previous[1] + (current[1] - previous[1]) * fraction))
                    if current_inside:
                        clipped.append(current)
                polygon = clipped
            signed_area = sum(_cross(p, polygon[(i + 1) % len(polygon)])
                              for i, p in enumerate(polygon))
            if abs(signed_area) < 1e-8:
                continue
            if signed_area < 0:
                polygon.reverse()
            polygons.append([geographic(p) for p in polygon])
        if polygons:
            shadows.append({"index": index, "name": obj["name"], "solid": obj["solid"],
                            "reduction": reduction, "polygons": polygons})
    return shadows, truncated


def shadow_preview(objects: Any, position: Any, height: Any, when: datetime) -> dict[str, Any]:
    """Use the same sun, heights and seasonal transmission as object shading."""
    from astral import Observer
    from astral.sun import azimuth, elevation

    location = _point(position)
    plane = _number(height, 0, 1000, "Shadow plane height (m)")
    normalized = normalize_objects(objects)
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("Preview time must include a timezone")
    observer = Observer(**location)
    sun_azimuth = float(azimuth(observer, when))
    sun_elevation = float(elevation(observer, when, with_refraction=False))
    shadows, truncated = project_shadows(normalized, **location, height=plane,
                                         azimuth=sun_azimuth, elevation=sun_elevation,
                                         when=when.date())
    return {"time": when.isoformat(), "local_time": when.strftime("%Y-%m-%dT%H:%M"),
            "timezone": str(when.tzinfo), "height": plane, "position": location,
            "sun": {"azimuth": sun_azimuth, "elevation": sun_elevation},
            "shadows": shadows, "truncated": truncated}


def _contains_origin(polygon: list[tuple[float, float]]) -> bool:
    inside = False
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        if abs(_cross(a, b)) < 1e-8 and min(a[0], b[0]) <= 0 <= max(a[0], b[0]) and min(a[1], b[1]) <= 0 <= max(a[1], b[1]):
            return True
        if (a[1] > 0) != (b[1] > 0):
            if a[0] + (b[0] - a[0]) * (-a[1]) / (b[1] - a[1]) > 0:
                inside = not inside
    return inside


def ray_hits(polygon: list[tuple[float, float]], azimuth: float, distance: float) -> bool:
    """Whether the ray towards the sun enters a footprint before rising above it."""
    if _contains_origin(polygon):
        return True
    direction = (math.sin(math.radians(azimuth)), math.cos(math.radians(azimuth)))
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        edge = _subtract(b, a)
        denominator = _cross(direction, edge)
        if abs(denominator) < 1e-9:
            if abs(_cross(a, direction)) < 1e-8:
                start = a[0] * direction[0] + a[1] * direction[1]
                end = b[0] * direction[0] + b[1] * direction[1]
                if max(start, end) >= 0 and min(start, end) <= distance:
                    return True
            continue
        along = _cross(a, edge) / denominator
        across = _cross(a, direction) / denominator
        if 0 <= along <= distance and 0 <= across <= 1:
            return True
    return False


def _triangles(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Ear clipping for validated simple, possibly concave PV footprints."""
    remaining = list(points)
    if sum(_cross(p, remaining[(i + 1) % len(remaining)]) for i, p in enumerate(remaining)) < 0:
        remaining.reverse()
    result: list[list[tuple[float, float]]] = []
    while len(remaining) > 3:
        found = False
        for i, b in enumerate(remaining):
            a, c = remaining[i - 1], remaining[(i + 1) % len(remaining)]
            turn = _cross(_subtract(b, a), _subtract(c, b))
            if abs(turn) < 1e-9:
                remaining.pop(i)
                found = True
                break
            if turn < 0:
                continue
            others = [p for j, p in enumerate(remaining) if j not in ((i - 1) % len(remaining), i, (i + 1) % len(remaining))]
            if any(all(_cross(_subtract(v, u), _subtract(p, u)) >= -1e-9
                       for u, v in ((a, b), (b, c), (c, a))) for p in others):
                continue
            result.append([a, b, c])
            remaining.pop(i)
            found = True
            break
        if not found:
            raise ValueError("PV polygon could not be triangulated")
    result.append(remaining)
    return result


def surface_samples(array: dict[str, Any], latitude: float, longitude: float) -> list[tuple[float, float, float, float]]:
    if not array["areas"]:
        return [(0, 0, array["height"], 1)]
    samples: list[tuple[float, float, float, float]] = []
    for area in array["areas"]:
        profile = _surface_frame(area, latitude, longitude)
        if profile["rise"] is None:
            raise ValueError("Vertical PV area needs its vertical extent")
        triangles = _triangles([_local_point(latitude, longitude, p) for p in area["polygon"]])
        sizes = [abs(_cross(_subtract(t[1], t[0]), _subtract(t[2], t[0]))) / 2 for t in triangles]
        total = sum(sizes)
        for triangle, size in zip(triangles, sizes):
            for weights in ((0.6, 0.2, 0.2), (0.2, 0.6, 0.2), (0.2, 0.2, 0.6)):
                x = sum(p[0] * w for p, w in zip(triangle, weights))
                y = sum(p[1] * w for p, w in zip(triangle, weights))
                q = x * profile["east"] + y * profile["north"]
                height = area["height"] + (profile["qmax"] - q) / profile["span"] * profile["rise"]
                if area["tilt"] == 90:
                    shift = (profile["qmin"] + profile["qmax"]) / 2 - q
                    x += shift * profile["east"]
                    y += shift * profile["north"]
                samples.append((x, y, height, size / total / 3 * area["peak"] / array["peak"]))
    return samples


def _surface_frame(area: dict[str, Any], latitude: float, longitude: float) -> dict[str, Any]:
    direction = math.radians(area["azimuth"])
    east, north = math.sin(direction), math.cos(direction)
    projected = [x * east + y * north for x, y in
                 (_local_point(latitude, longitude, p) for p in area["polygon"])]
    qmin, qmax = min(projected), max(projected)
    span = qmax - qmin
    rise = area.get("vertical_height") if area["tilt"] == 90 else span * math.tan(math.radians(area["tilt"]))
    return {"east": east, "north": north, "qmin": qmin, "qmax": qmax, "span": span,
            "rise": rise, "lower_height": area["height"],
            "upper_height": area["height"] + rise if rise is not None else None}


def surface_profile(area: dict[str, Any]) -> dict[str, Any]:
    """The edge facing the azimuth is lowest; the opposite edge rises."""
    center = polygon_center(area["polygon"])
    return _surface_frame(area, center["latitude"], center["longitude"])


def geometry_required(arrays: list[dict[str, Any]]) -> bool:
    return any((upper := surface_profile(area)["upper_height"]) is None
               or not math.isfinite(upper) or upper > 1000
               for array in arrays for area in array["areas"])


def line_blocks(line: list[tuple[float, float, float]], azimuth: float, elevation: float,
                sample: tuple[float, float, float, float]) -> bool:
    sx, sy, panel_height, _ = sample
    direction = (math.sin(math.radians(azimuth)), math.cos(math.radians(azimuth)))
    slope = math.tan(math.radians(elevation))
    for start, end in zip(line, line[1:]):
        a, b = (start[0] - sx, start[1] - sy), (end[0] - sx, end[1] - sy)
        edge = _subtract(b, a)
        denominator = _cross(direction, edge)
        if abs(denominator) < 1e-9:
            if abs(_cross(a, direction)) > 1e-8:
                continue
            da = a[0] * direction[0] + a[1] * direction[1]
            db = b[0] * direction[0] + b[1] * direction[1]
            if max(da, db) < 0:
                continue
            fractions = [0.0, 1.0]
            if min(da, db) <= 0 <= max(da, db) and da != db:
                fractions.append(-da / (db - da))
            if any((d := da + (db - da) * f) >= 0
                   and start[2] + (end[2] - start[2]) * f > panel_height
                   and panel_height + slope * d <= start[2] + (end[2] - start[2]) * f + 1e-8
                   for f in fractions):
                return True
            continue
        distance, fraction = _cross(a, edge) / denominator, _cross(a, direction) / denominator
        if distance < 0 or not 0 <= fraction <= 1:
            continue
        top = start[2] + fraction * (end[2] - start[2])
        if top > panel_height and panel_height + slope * distance <= top + 1e-8:
            return True
    return False


class ObjectShading:
    """Area-weighted spatial samples and six time samples per hour."""

    def __init__(self, arrays: list[dict[str, Any]], objects: list[dict[str, Any]],
                 latitude: float, longitude: float) -> None:
        self.arrays = arrays
        self.locations = [(a.get("latitude", latitude), a.get("longitude", longitude)) for a in arrays]
        self.samples = [surface_samples(a, lat, lon) for a, (lat, lon) in zip(arrays, self.locations)]
        self.geometry = [
            [(o, [(*_local_point(lat, lon, p), p["height"]) for p in o["line"]]) for o in objects]
            for lat, lon in self.locations
        ]
        self._cached_factor = lru_cache(maxsize=32768)(self._factor)

    def at_sun(self, index: int, azimuth: float, elevation: float, when: date | None = None) -> float:
        if elevation <= 0:
            return 0.0
        result = 0.0
        for sample in self.samples[index]:
            factor = 1.0
            for obj, line in self.geometry[index]:
                hit = line_blocks(line, azimuth, elevation, sample)
                if not hit and "polygon" in obj and obj["height"] > sample[2]:
                    polygon = [(p[0] - sample[0], p[1] - sample[1]) for p in line[:-1]]
                    hit = _contains_origin(polygon)
                if hit:
                    factor = min(factor, object_transmission(obj, when or date.today()))
            result += factor * sample[3]
        return max(0.0, min(1.0, result))

    def factor(self, index: int, hour: datetime) -> float:
        return self._cached_factor(index, hour)

    def _factor(self, index: int, hour: datetime) -> float:
        if not self.geometry[index]:
            return 1.0
        from astral import Observer
        from astral.sun import azimuth, elevation

        lat, lon = self.locations[index]
        observer = Observer(latitude=lat, longitude=lon)
        total = 0.0
        for minute in range(5, 60, 10):
            when = hour + timedelta(minutes=minute)
            total += self.at_sun(index, float(azimuth(observer, when)),
                                 float(elevation(observer, when, with_refraction=False)), when.date())
        return total / 6
