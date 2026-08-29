# AGENTS: smart_pv_forecast

Diese Anleitung richtet sich an zukuenftige Agents, die an der Integration smart_pv_forecast arbeiten.

## 1) Ziel und Scope

Die Integration liefert eine PV-Ertragsprognose fuer Home Assistant auf Basis mehrerer Strahlungsdatenquellen.

Abgedeckte Kernfunktionen:
- Config Flow fuer Standort, optionale DWD-Station, optionale Open-Meteo-Quelle und PV-Arrays.
- Hourly Forecast-Berechnung pro PV-Ausrichtung (multi-array).
- Optionale Damping-, Inverter- und Horizon-Anpassungen.
- Optionale adaptive Selbstoptimierung pro Ausrichtung (Saison+Stunde), mit Batterie-basierten Lernfiltern.
- Konfigurierbarer Open-Meteo Prognosehorizont (1-16 Tage).
- Konfigurierbare Anzahl von Tagesertrags-Sensoren (heute, morgen, +N Tage).
- Sensoren fuer aktuelle und kommende Energie/Leistung.

Default-Strategie:
- Neue Config-Entries starten Open-Meteo-first (Open-Meteo aktiv, DWD initial deaktiviert).
- Wenn alle Quellen deaktiviert sind, faellt der Coordinator auf Open-Meteo zurueck (nicht auf DWD).

## 2) Wichtige Dateien

Core:
- custom_components/smart_pv_forecast/manifest.json
- custom_components/smart_pv_forecast/__init__.py
- custom_components/smart_pv_forecast/const.py
- custom_components/smart_pv_forecast/coordinator.py
- custom_components/smart_pv_forecast/models.py

Config und UI:
- custom_components/smart_pv_forecast/config_flow.py
- custom_components/smart_pv_forecast/strings.json
- custom_components/smart_pv_forecast/translations/

Berechnung und Datenquellen:
- custom_components/smart_pv_forecast/dwd_mosmix.py             (DWD MOSMIX-L Parsing, KMZ-Download)
- custom_components/smart_pv_forecast/dwd_icon.py               (Estimate-Berechnung aus RadiationPayload)
- custom_components/smart_pv_forecast/open_meteo_source.py      (Open-Meteo global forecast)
- custom_components/smart_pv_forecast/forecast_solar_source.py  (forecast.solar API, NEU)

Entitaeten und Diagnose:
- custom_components/smart_pv_forecast/sensor.py
- custom_components/smart_pv_forecast/energy.py
- custom_components/smart_pv_forecast/diagnostics.py

## 3) Architektur-Kurzueberblick

Multi-Source-Laufzeitfluss:
1. Config Entry speichert latitude/longitude in data und fachliche Parameter in options.
2. coordinator.py aktualisiert stuendlich und holt Daten aus allen aktivierten Quellen.
3. Strahlungsquellen: DWD MOSMIX-L (dwd_mosmix.py) und Open-Meteo global (open_meteo_source.py) werden parallel per asyncio.gather gefetcht.
4. forecast.solar (forecast_solar_source.py): ein API-Call pro PV-Array, Ergebnisse werden summiert. Liefert Estimate direkt (kein RadiationPayload).
5. Alle aktiven Quellen werden mit _combine_estimates() zu einem Estimate kombiniert.
6. Gewichtung: Pro Quelle ein unabhaengiger Gewichtswert (0-100), normiert auf Summe der aktiven Quellen.
7. Wenn eine Quelle fehlschlaegt: restliche Quellen werden genutzt. Wenn alle fehlschlagen: UpdateFailed.
8. Wenn alle Quellen deaktiviert: Sicherheitsfallback auf Open-Meteo.
9. dwd_icon.py baut Estimate aus RadiationPayload fuer DWD. Open-Meteo nutzt open_meteo_source.py mit global_tilted_irradiance pro Array (tilt/azimuth serverseitig). forecast.solar liefert Estimate direkt.
10. sensor.py mappt Estimate-Werte auf HA-Sensor-Entitaeten. diagnostics.py liefert redigierte Daten.

Kombinationsformel (normiert):
- active = [(e, w) for (e, w) in sources if e is not None]
- total_w = sum(w for _, w in active)
- combined_val = sum(w * val_source) / total_w
- Bei total_w = 0: gleichgewichtet

Wichtig: forecast.solar liefert kein RadiationPayload, sondern ein fertiges Estimate (aus watt_hours_period). Es wird NICHT durch dwd_icon.build_estimate() verarbeitet. Damping und Inverter-Limit aus der HA-Konfiguration werden auf forecast.solar-Daten NICHT angewendet (das Modell von forecast.solar hat ein eigenes Verfahren).

Azimuth-Konvertierung fuer forecast.solar:
- HA: 0-360 (0/360=Nord, 90=Ost, 180=Sued, 270=West)
- forecast.solar: -180 bis +180 (0=Sued, -90=Ost, 90=West, ±180=Nord)
- Formel: fs_az = (ha_az % 360) - 180

API-Limits fuer forecast.solar:
- Oeffentlich (kein API-Key): 12 Anfragen/Stunde, 1 Flaeche pro Aufruf
- Mit API-Key: hoehere Limits je Kontotyp (Personal/Professional)
- Bei HTTP 429: ForecastSolarRateLimitError wird ausgeloest, Fallback auf andere Quellen
- Bei verbleibendem Limit <= 3: WARNING im Log

Wichtige Implementierungsdetails:
- Freeze-Mechanik nach 12:00 Uhr lokal: Tageswerte bleiben stabil, ausser relevante Optionen wurden geaendert.
- Optionen werden ueber Fingerprint gehasht, damit Parameteraenderungen sofort wirksam werden. Fingerprint beinhaltet jetzt auch enable_dwd, enable_open_meteo, source_weight_dwd.
- Bereits veroeffentlichte Werte von heute fuer vergangene Stunden werden bei Refresh nicht mehr ueberschrieben oder entfernt.
- Leistungsberechnung ist konservativ begrenzt: POA-Faktor wird nicht > 1.0 skaliert; statt harter Kappung wird die Tageskurve gleichmaessig per Faktor skaliert, wenn der Tages-Peak ueber PV-Peak/Inverter-Limit liegt.
- Config- und Options-Workflow enthalten Feldhilfen mit konkreten Beispielen (data_description), insbesondere fuer JSON-Felder und Entity-IDs.
- Zusaetzlich enthalten die Feldtitel selbst Inline-Beispiele, damit Eingabehinweise direkt am jeweiligen Eingabefeld sichtbar sind.
- In Translation-Texten keine geschweiften Klammern fuer Beispiele verwenden, da dies als Platzhalter interpretiert werden und MALFORMED_ARGUMENT ausloesen kann.
- JSON-Felder im Config-/Options-Flow werden als mehrzeilige Textboxen (TextSelector multiline) angezeigt und mit eingerueckten JSON-Defaults vorbelegt.
- Adaptive Modellpersistenz erfolgt ueber homeassistant.helpers.storage.Store pro ConfigEntry.
- Lernlogik nutzt pro Ausrichtung reale Leistungs-Entitaeten und aktualisiert Faktoren nur einmal pro Stunde.
- Lernfilter: Lernen pausiert bei hoher Batterie-SOC (Default >= 98%) und wenn Batterie-Ladeleistung nahe am konfigurierten Ladelimit liegt.
- Prognoseanwendung: adaptive Faktoren werden auf DWD- und Open-Meteo-Arrayberechnung angewendet; forecast.solar bleibt unveraendert.
- Lernbasis nutzt verfuegbare Strahlungsquelle (DWD oder Open-Meteo), kein DWD-Zwang.
- Open-Meteo nutzt forecast_days sowie tilt/azimuth je PV-Array direkt im API-Request (global_tilted_irradiance).
- Zusatzzustaende fuer Monitoring: Durchschnittsfaktor, 1000-W/m2-Referenz (W/kWp) und Sample-Zaehler als Sensoren.

## 4) Konfigurationsmodell

Config Entry data:
- latitude
- longitude

Optionen (Auszug):
- enable_dwd_source (optional, Standard: false) – DWD MOSMIX-L aktivieren
- enable_open_meteo_source (optional, Standard: true) – Open-Meteo aktivieren
- enable_forecast_solar_source (optional, Standard: false) – forecast.solar aktivieren
- forecast_solar_api_key (optional, Standard: leer) – API-Key fuer forecast.solar, leer = oeffentlich
- source_weight_dwd (optional, Standard: 0, Bereich 0-100) – DWD-Gewichtung
- source_weight_open_meteo (optional, Standard: 100, Bereich 0-100) – Open-Meteo-Gewichtung
- source_weight_forecast_solar (optional, Standard: 50, Bereich 0-100) – forecast.solar-Gewichtung
- open_meteo_forecast_days (optional, Standard: 7, Bereich 1-16)
- daily_yield_sensor_days (optional, Standard: 4, Bereich 2-16, <= open_meteo_forecast_days)
- mosmix_station (optional wenn DWD deaktiviert, Pflicht wenn DWD aktiv)
- pv_arrays (Pflicht, JSON-String)
- damping_morning
- damping_evening
- inverter_size
- horizon
- enable_self_optimization (optional, Standard: false)
- real_power_entities (optional JSON-String; bei aktivierter Selbstoptimierung Pflicht und 1:1 zu pv_arrays)
- battery_soc_entity (optional)
- battery_charge_power_entity (optional)
- battery_charge_limit (optional, Watt)
- battery_soc_threshold (optional, Standard: 98.0)

Formaterwartung:
- pv_arrays: Liste mit Objekten aus peak, azimuth, tilt.

Wichtige Breaking Change:
- Modul-Rename von dwd_forecast_solar zu smart_pv_forecast: bestehende Config-Entries werden orphaned.
  Nutzer muessen die Integration in HA loeschen und neu hinzufuegen.

## 5) Arbeitsregeln fuer Agents

Vor jeder Aenderung:
- Immer zuerst den Datenfluss pruefen: Config Flow -> Coordinator -> Quellen -> Combine -> Estimate -> Sensoren -> Diagnostics.
- Bestehende Migrationslogik in __init__.py nicht brechen (Entry-Version beachten).
- Bei Optionsaenderungen Validierung in config_flow.py und Verarbeitung in coordinator.py/dwd_icon.py synchron halten.
- Bei Quellen-Erweiterungen: open_meteo_source.py als Vorlage fuer neue Quellen-Klassen nutzen.

Bei Berechnungslogik:
- Einheiten strikt beibehalten (W, Wh, Grad).
- Zeitbezug und Zeitzonen konsistent halten (hour keys, lokale Tagesgrenzen).
- Adaptive Referenzgroesse ist normiert auf 1000 W/m2 und wird als W/kWp exponiert.

Bei Sensoren:
- translation_key, device_class, state_class und units konsistent halten.
- entity_registry_enabled_default nur bewusst aendern.
- Entity-IDs muessen den konfigurierten Entry-Namen als Prefix enthalten, damit mehrere Instanzen klar unterscheidbar sind.
- Dynamische Tagesertrags-Sensoren nutzen eigene Keys im Format energy_production_in_<tage>_days, damit keine Kollision mit alten (teils deaktivierten) Legacy-Keys entsteht.

## 6) Pflegepflicht (verbindlich)

Pflichtschritt fuer jede relevante Aenderung in diesem Modul:
- AGENTS.md in custom_components/smart_pv_forecast muss immer mit aktualisiert werden.
- Eine Umsetzung gilt nicht als fertig, wenn AGENTS.md nicht auf den neuen Stand gebracht wurde.

Als relevant gelten mindestens:
- Aenderungen an Datenmodell, Optionen, Migrationen oder Validierung.
- Aenderungen an Berechnung (dwd_icon.py), Koordinatorlogik oder API-Parsing.
- Aenderungen an Entitaeten, Attributen, Diagnostics oder Energy-Output.
- Neue Datenquellen oder Aenderungen an der Kombinationslogik.

## 7) Haeufige Stolperfallen

- pv_arrays werden als JSON-String gespeichert und muessen robust geparst werden.
- Nachmittags-Freeze kann Aenderungen maskieren, wenn Fingerprint nicht alle relevanten Parameter enthaelt (source_weight_dwd und source toggles muessen im Fingerprint sein).
- Historische Tageswerte duerfen fuer vergangene Stunden nicht mehr neu berechnet werden.
- Zeitzonenfehler fuehren schnell zu falschen Tagessummen (today/tomorrow).
- Aenderungen in Sensor-Keys ohne passende strings/translations-Anpassung erzeugen UI-Inkonsistenzen.
- Wenn alle Quellen deaktiviert sind, greift ein stiller Fallback auf Open-Meteo.
- mosmix_station ist nur optional wenn DWD deaktiviert; Validierung in config_flow.async_step_init sicherstellt das.
- Bei aktivierter Selbstoptimierung muessen real_power_entities in Anzahl und Reihenfolge zu pv_arrays passen.
- Lernstatus "filtered" ist erwartetes Verhalten, wenn Batterie-Limits aktiv greifen.
- daily_yield_sensor_days darf open_meteo_forecast_days nicht ueberschreiten.

## 8) Test-Checkliste

Konfiguration:
- Neuer Config Flow akzeptiert gueltige Station und gueltige pv_arrays.
- Ungueltige JSON-Eingaben werden sauber als Fehler markiert.
- Options Flow: Station kann leer sein, wenn enable_dwd_source = False.
- Options Flow: source_weight_dwd 0-100 wird korrekt validiert.

Laufzeit:
- Coordinator-Update liefert Daten ohne Exception.
- Tageswerte (today, remaining, tomorrow) sind plausibel und stabil.
- Fingerprint-Recalc greift nach Optionsaenderung sofort.
- Wenn eine Quelle fehlschlaegt, wird die andere genutzt (kein Totalausfall).
- Wenn beide Quellen fehlschlagen, wird UpdateFailed ausgeloest.

Sensoren:
- Kernsensoren liefern Werte mit korrekten Units.
- Adaptive Sensoren liefern Faktor, 1000-W/m2-Referenz und Sample-Zaehler (gesamt + je Array).
- Tagesertrags-Sensoren werden dynamisch von der Option daily_yield_sensor_days abgeleitet.

Diagnostics/Energy:
- Diagnostics redigiert sensible Daten korrekt.
- async_get_solar_forecast liefert erwartete wh_hours-Struktur.

## 9) Definition of Done

Eine Aenderung in smart_pv_forecast ist fertig, wenn:
- Config/Optionen und Berechnungslogik konsistent sind,
- Sensoren und Diagnostics korrekt und stabil liefern,
- Migrationspfad nicht gebrochen ist,
- und AGENTS.md sichtbar auf den aktuellen Stand gebracht wurde.