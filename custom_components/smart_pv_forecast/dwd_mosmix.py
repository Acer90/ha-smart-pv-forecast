"""DWD MOSMIX-L client (station-based) for solar forecast.

This downloads the MOSMIX-L KMZ for a single station and extracts radiation
parameters used in the legacy Symcon module:
- Rad1h (base radiation)
- RRad1 (radiation intensity in %)

The resulting hourly radiation is converted into a single value `rad_total`
compatible with the PV estimate builder.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from xml.etree import ElementTree as ET

from aiohttp import ClientSession


_MOSMIX_URL: Final[
    str
] = "https://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_L/single_stations/{station}/kml/MOSMIX_L_LATEST_{station}.kmz"


@dataclass(frozen=True, slots=True)
class MosmixRadiationPayload:
    timezone: str
    hourly_time: list[str]
    shortwave_radiation: list[float]


def _parse_iso_datetime(value: str) -> datetime:
    # MOSMIX uses ISO8601 with Z
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _split_fixed_width(value: str, width: int) -> list[str]:
    return [value[i : i + width] for i in range(0, len(value), width)]


def _get_text(parent: ET.Element | None, path: str) -> str | None:
    if parent is None:
        return None
    elem = parent.find(path)
    if elem is None or elem.text is None:
        return None
    return elem.text


def _strip_namespaces(xml_bytes: bytes) -> bytes:
    # Similar approach as the Symcon module: remove kml:/dwd: prefixes.
    text = xml_bytes.decode("utf-8", errors="replace")
    text = text.replace("<kml:", "<").replace("</kml:", "</")
    text = text.replace("<dwd:", "<").replace("</dwd:", "</")
    text = text.replace(" dwd:", " ")
    return text.encode("utf-8")


class DwdMosmixClient:
    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def fetch_radiation(self, *, station: str) -> MosmixRadiationPayload:
        station = station.strip().upper()
        if not station:
            raise ValueError("MOSMIX station must be set")

        url = _MOSMIX_URL.format(station=station)
        async with self._session.get(url) as resp:
            resp.raise_for_status()
            kmz_bytes = await resp.read()

        # Extract the single KML file from KMZ
        with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as zf:
            kml_name = next((n for n in zf.namelist() if n.lower().endswith(".kml")), None)
            if kml_name is None:
                raise ValueError("MOSMIX KMZ did not contain a .kml")
            kml_bytes = zf.read(kml_name)

        kml_bytes = _strip_namespaces(kml_bytes)
        root = ET.fromstring(kml_bytes)

        # Time steps
        time_steps_parent = root.find(
            "./Document/ExtendedData/ProductDefinition/ForecastTimeSteps"
        )
        if time_steps_parent is None:
            raise ValueError("MOSMIX KML missing ForecastTimeSteps")

        time_steps: list[datetime] = []
        for ts_elem in time_steps_parent.findall("./TimeStep"):
            if ts_elem.text:
                time_steps.append(_parse_iso_datetime(ts_elem.text))

        # Forecast values in a fixed width encoded string
        extended = root.find("./Document/Placemark/ExtendedData")
        if extended is None:
            raise ValueError("MOSMIX KML missing Placemark/ExtendedData")

        def find_forecast_value(element_name: str) -> list[str] | None:
            for forecast in extended.findall("./Forecast"):
                if forecast.get("elementName") != element_name:
                    continue
                val_text = _get_text(forecast, "./value")
                if not val_text:
                    return None
                return _split_fixed_width(val_text, 11)
            return None

        rad = find_forecast_value("Rad1h")
        rrad = find_forecast_value("RRad1")
        if rad is None or rrad is None:
            raise ValueError("MOSMIX KML missing Rad1h/RRad1")

        count = min(len(time_steps), len(rad), len(rrad))
        hourly_time: list[str] = []
        shortwave_radiation: list[float] = []

        for i in range(count):
            rad_s = rad[i].strip()
            rrad_s = rrad[i].strip()

            if rad_s == "-" or rad_s == "":
                rad_v = 0.0
            else:
                rad_v = float(rad_s)

            if rrad_s == "-" or rrad_s == "":
                rrad_v = 0.0
            else:
                rrad_v = float(rrad_s)

            # Symcon: rad_total = round(rad * rrad/100)
            rad_total = (rad_v * rrad_v) / 100.0

            hourly_time.append(time_steps[i].isoformat())
            shortwave_radiation.append(float(rad_total))

        # Most MOSMIX times are UTC; use a stable timezone label for consumers.
        return MosmixRadiationPayload(
            timezone="Europe/Berlin",
            hourly_time=hourly_time,
            shortwave_radiation=shortwave_radiation,
        )
