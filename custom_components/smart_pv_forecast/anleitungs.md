# Anleitung: smart_pv_forecast

Diese Datei erklaert, wie du die Integration einrichtest und mit sinnvollen Beispielen konfigurierst.

## 1) Ziel der Integration

Die Integration erstellt eine PV-Prognose auf Basis von DWD MOSMIX-L Daten.
Standardmaessig wird Open-Meteo als Quelle genutzt.

Du kannst:
- mehrere PV-Ausrichtungen nutzen,
- Dampfungsfaktoren setzen,
- einen Inverter-Deckel setzen,
- optional adaptive Selbstoptimierung pro Ausrichtung aktivieren.


## 2) Schnellstart

Empfohlene Reihenfolge:
1. Integration mit einem einfachen Setup anlegen.
2. Pruefen, ob Grundwerte plausibel sind.
3. Danach Optionen schrittweise aktivieren.

## 3) Erstkonfiguration (Config Flow)

Felder und Beispiele:

Name:
- Beispiel: PV Garage

Latitude:
- Dezimalwert
- Beispiel: 51.234

Longitude:
- Dezimalwert
- Beispiel: 7.123

mosmix_station:
- DWD Stationscode (alphanumerisch, 1-10 Zeichen)
- Beispiel: N7244

pv_arrays:
- JSON-Liste mit peak, azimuth, tilt je Ausrichtung
- peak in Watt
- azimuth in Grad (0-360)
- tilt in Grad (0-90)

Beispiel mit zwei Ausrichtungen:

[
  {"peak": 2300, "azimuth": 95, "tilt": 35},
  {"peak": 4600, "azimuth": 270, "tilt": 30}
]

## 4) Optionen (Options Flow)

mosmix_station:
- Optional (nur relevant, wenn DWD als Quelle aktiviert ist)
- Beispiel: N7244

pv_arrays:
- Gleiches Format wie oben

damping_morning und damping_evening:
- Bereich 0.0 bis 1.0
- Beispiel: 0.1 bedeutet 10 Prozent Reduktion

inverter_size:
- Optionales AC-Limit in Watt
- Beispiel: 6000

horizon:
- Sonnenhoehe in Grad, darunter wird ignoriert
- Typisch: 0 bis 15
- Beispiel: 8

open_meteo_forecast_days:
- Anzahl Prognosetage fuer Open-Meteo.
- Bereich: 1 bis 16.
- Beispiel: 16.

Hinweis Open-Meteo:
- tilt und azimuth aus pv_arrays werden direkt in den Open-Meteo API-Requests verwendet (pro Ausrichtung).

daily_yield_sensor_days:
- Anzahl Tagesertrags-Sensoren als einzelne Variablen (heute, morgen, usw.).
- Bereich: 2 bis 16.
- Darf open_meteo_forecast_days nicht ueberschreiten.
- Beispiel: 8 erzeugt Sensoren fuer heute bis in 7 Tagen.

enable_self_optimization:
- Aktiviert lernende Korrekturfaktoren pro Ausrichtung, Stunde und Saison.

real_power_entities:
- JSON-Liste mit gemessenen PV-Leistungs-Entitaeten.
- Reihenfolge muss exakt zu pv_arrays passen.
- Beispiel:

[
  "sensor.pv_east_power",
  "sensor.pv_west_power"
]

battery_soc_entity:
- Optional.
- Wenn gesetzt, pausiert das Lernen ab battery_soc_threshold (Standard 98%).

battery_charge_power_entity + battery_charge_limit:
- Optional.
- Wenn gesetzt, pausiert das Lernen bei Ladeleistungs-Naehe zum Ladelimit.

battery_soc_threshold:
- SOC-Schwelle fuer Lernstopp.
- Standard: 98.

## 5) Typische Fehler und Tipps

1. Ungueltiges JSON in pv_arrays.
2. Zu aggressive Dampfungswerte fuehren zu zu niedrigen Prognosen.

Tipps:
1. Erst mit minimalem Setup starten.
2. Nach jeder Aenderung mindestens einen vollen Tageszyklus beobachten.
3. Werte schrittweise anpassen und jeweils einen Tag beobachten.
4. Bei aktiver Selbstoptimierung auf die neuen Sensoren fuer Faktor, 1000-W/m2-Referenz und Sample-Zaehler achten.

## 8) Komplettes Beispiel

Beispiel fuer ein typisches Setup:

- Name: PV Garage
- Latitude: 51.234
- Longitude: 7.123
- mosmix_station: N7244
- pv_arrays:

[
  {"peak": 2300, "azimuth": 95, "tilt": 35},
  {"peak": 4600, "azimuth": 270, "tilt": 30}
]

- inverter_size: 6000
- horizon: 8
- damping_morning: 0.1
- damping_evening: 0.1

Dieses Setup ist ein guter Startpunkt und kann danach schrittweise verfeinert werden.