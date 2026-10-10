# AGENTS: smart_pv_forecast

## Ziel und Zuständigkeiten

PV-Ertragsprognose für Home Assistant aus Open-Meteo, DWD MOSMIX-L und
optional forecast.solar. Stand: Version **0.5.0**, Config-Entry-Version **7**.
Keine adaptive Selbstoptimierung, Lernfilter oder Adaptive-Sensoren.

- Native Integrationsdialoge: ausschließlich Name bei Einrichtung und Quellen.
- **Solarprognose**, `/smart-pv`, Administratoren: gesamte Anlagenkonfiguration,
  PV-Flächen, Schattenlinien, Wechselrichter, Sensorzuordnung und Prognoseparameter.
- Deutsche Benutzertexte mit ä/ö/ü/ß; englische Übersetzung synchron halten.
- Desktop, Handy und Touch unterstützen, nicht nur einen schmalen Viewport.
- Bedienung und Modellgrenzen stehen in [anleitungs.md](anleitungs.md).
  Das separate [symcon/](symcon/) bleibt außerhalb dieses Arbeitsumfangs.

## Architektur

- [config_flow.py](config_flow.py): quellenbasierte Einrichtung/Optionen.
  Neue Entries haben keine erfundene PV-Anlage; bestehende Studiooptionen erhalten.
- [configurator.py](configurator.py): Panel, statische Dateien, Admin-WebSockets,
  vollständige serverseitige Validierung, revisionierte Speicherung unter Lock.
- [site.py](site.py): gemeinsame Validatoren, Flächenaufteilung, Sonnenstrahl/
  Schattenlinien-Schnitt, Höheninterpolation, Vegetationskalender.
- [analysis.py](analysis.py): Quellengewichtung, Wechselrichterbegrenzung,
  Rückgruppierung einzelner Flächen und Tagesvergleich.
- [coordinator.py](coordinator.py): Quellen abrufen/cachen, unverdeckte Basis bilden,
  begrenzen, lokale Verschattung anwenden, zu Arrays gruppieren und veröffentlichen.
- [dwd_icon.py](dwd_icon.py), [open_meteo_source.py](open_meteo_source.py),
  [forecast_solar_source.py](forecast_solar_source.py): Prognosen je einzelner Fläche.
  Rohe forecast.solar-Flächenerträge für den Vergleich ohne zusätzliche Abrufe erhalten.
- [frontend/configurator.js](frontend/configurator.js), [frontend/i18n.js](frontend/i18n.js),
  [frontend/styles.css](frontend/styles.css), [frontend/charts.js](frontend/charts.js):
  Web Component ohne CDN-JavaScript; Karte, Eigenschaften, Touch und Diagramme.
- [__init__.py](__init__.py): Setup und Migrationen;
  [sensor.py](sensor.py), [energy.py](energy.py), [diagnostics.py](diagnostics.py):
  Entitäten, Energy Dashboard und redigierte Diagnose.

## Daten und Berechnungsregeln

- `data.latitude/longitude` bleiben Standortfallback. `options.pv_arrays` enthält
  logische Arrays mit `name`, optionalem `inverter_id` und `areas`.
  Jede Fläche hat `polygon`, `name`, `peak` in Wp, `mounting_type`, `tilt`,
  `azimuth`, `height` als Unterkante und optional `vertical_height`.
  Arrayleistung ist die Summe seiner Flächen.
- Legacy-Punktarrays und JSON-Strings weiterhin normalisieren. Keine ungültigen
  Eingaben still verwerfen. Gemeinsame Validatoren in [site.py](site.py) verwenden.
- Montageart ist Metadatum, kein automatischer Winkel/Ertragsfaktor.
  Neigung zur Horizontalen: 0–90°. HA-Azimuth: Nord 0°, Ost 90°, Süd 180°, West 270°.
  Quellenkonvention bei forecast.solar/Open-Meteo entsprechend umrechnen.
- `shading_objects.line`: Konturpunkte mit Breitengrad, Längengrad und eigener Höhe.
  Linie bildet eine senkrechte Kontur bis zur gemeinsamen Bodenreferenz;
  dazwischen Höhen linear interpolieren. Keine volumetrische Baumsimulation.
- Massive Objekte: `solid=true`, `transmission` 0–1. Vegetation: `solid=false`,
  `reduction_leaf_on/off` 0–1, `leaf_on/off` als `MM-DD`,
  `foliage_mode` aus `auto`, `leaf_on`, `leaf_off`.
  Blattbeginn inklusive, Blattende exklusive; jahresübergreifende Intervalle beachten.
- Flächen triangulieren und räumlich gewichtet abtasten; sechs Zeitpunkte je Stunde.
  Grundriss auf Azimut projizieren: untere Kante in Azimutrichtung, gegenüberliegend
  steigt die Fläche. Oberkante = Unterkante + projizierte Tiefe × tan(Neigung).
  Abtastpunkthöhen entlang dieses Gefälles interpolieren, nicht konstant setzen.
  Bei 90° `vertical_height` erforderlich; Abtastpunkte auf senkrechte Ebene durch
  den Mittelpunkt abbilden und Höhen aus der Grundriss-Projektion verteilen.
  Überlappende Schatten verwenden die niedrigste Transmission, nicht deren Produkt.
  Kein String-, Bypassdioden-, Tracker- oder automatisches Reihenverschattungsmodell.
- Maximal 64 Arrays, 32 Flächen je Array, insgesamt 128 Berechnungsflächen;
  64 Polygonpunkte, 128 Hindernisse und 65 Linienpunkte. Keine degenerierten/
  selbstkreuzenden Polygone, doppelten Punkte oder nicht endlichen Zahlen akzeptieren.
- `inverters`: eindeutige `id`, `name`, `limit_w`, `power_entity`, `energy_entity`.
  Referenzen und vorhandene HA-Sensoren samt Einheiten serverseitig prüfen:
  Leistung W/kW; Energie Wh/kWh/MWh. Sensorwerte nur anzeigen, niemals lernen.
- Begrenzung vor Objektverschattung anwenden, damit verschatteter Ertrag niemals
  die identische unverdeckte Basis übersteigt. Wechselrichterlimits sind konservative
  Tagesskalierung, kein physikalisches Clipping-/Umverteilungsmodell.
- Schattenfaktoren instanzlokal cachen; keine alten Geometrien nach Reload behalten.
  W, Wh, Wp und Zeitzonen konsistent halten.
- Keine Arrays: `setup_required`, keine Quellenabrufe, Sensoren nicht verfügbar.
  Dasselbe gilt bei fehlender 90°-Ausdehnung oder abgeleiteter Oberkante >1000 m;
  Migration und Lese-API müssen diese Daten zur Korrektur zugänglich lassen.
  Speichern lehnt unvollständige Flächen ab, erlaubt aber leere Arrays mit
  bereits konfigurierten Wechselrichtern/Hindernissen. Letztes Array nur mit
  expliziter Warnung im UI löschen. Keine geratenen Höhen ergänzen.
  Ungültige Arrays sind dagegen ein Fehler, kein leeres erfolgreiches Ergebnis.

## Persistenz, Diagramme und Sicherheit

- Migrationen 1–7 erhalten: 3→4 entfernt Lernoptionen/Adaptive-Store/-Entitäten;
  4→5 ergänzt Flächen und konvertiert alte Polygone in geschlossene Höhenlinien;
  5→6 ergänzt Wechselrichter; 6→7 ergänzt Flächenmetadaten ohne geratene
  senkrechte Abmessungen. Kernsensor-Unique-IDs stabil halten.
- Energy-Snapshots enthalten Konfigurationsfingerprint und `wh_hours`.
  Fremde/veraltete Snapshots verwerfen; Energy Dashboard erhält reine `wh_hours`.
  Update-Listener invalidiert Snapshot und lädt Entry neu; kein zusätzlicher
  `OptionsFlowWithReload`, sonst doppelte Reloads.
- Adminpflichtige Kommandos: `smart_pv_forecast/sites`, `save_site`, `analysis`.
  Keine Prognose-API-Schlüssel im Frontend. Der optionale Google-Maps-Browser-
  Schlüssel wird im Integrationsdialog gesetzt, nur an den Admin übermittelt und
  in Diagnostics redigiert. Mit Schlüssel standardmäßig Google nutzen:
  Maps JavaScript API für 2D, Maps Static API für das 3D-Bodenbild. Browser- und
  API-Beschränkungen sowie Kosten/Datentransfer erläutern. Keine Schlüssel über
  Solarprognose-Saves ändern; Saves erhalten Quellenoptionen und prüfen Revision.
- `smart_pv_forecast/shadow_preview`: Admin-Vorschau ungespeicherter Konturen,
  keine Persistenz/Quellenabrufe. Validierung und Projektion in [site.py](site.py),
  Berechnung im Executor; Sonnenstand ohne Refraktion wie beim Prognosemodell.
  Live-Uhr minütlich, manuelle Zeit in HA-Zeitzone; nicht existierende Ortszeiten
  ablehnen. Aktive PV-Flächenhöhe oder Boden, manuell übersteuerbar.
  Gesamte Karte verwendet eine Ebene. Horizontnahe Vorschau auf 10 km schneiden
  und kennzeichnen, nicht die Prognose kürzen. SVG-Maske übernimmt stärkste
  Reduktion; überlagerte Transparenz nicht multiplizieren. Veraltete Antworten
  per Generation verwerfen, fehlerhafte Vorschau löschen und Fehler anzeigen.
  Die Kartenvorschau bleibt eine horizontale Vergleichsebene; automatische
  Höhe ist die Unterkante. Das deutlich von geneigten Prognoseflächen unterscheiden.
- Jede PV-Fläche erhält einen nicht interaktiven Azimutpfeil ab ihrem Mittelpunkt,
  mindestens bis über ihre projizierte Kante. Berechnete Oberkante live anzeigen.
  Frontend- und Backend-Höhenformeln gegeneinander testen. Native Auswahl/Drag
  auf der Karte verhindern, Bedienelemente und Textfelder aber bedienbar lassen.
- Tagesvergleich: aktuelle gespeicherte Geometrie für lokale 00–24 Uhr, kein
  Messverlauf und keine historische Aufzeichnung. Alte Sensorschätzungen für
  vergangene Stunden unabhängig davon erhalten. Revisionen nicht vermischen.
- Fehlende Stunden als `null`, nachts bei Basis 0 W Reduktion 0 %.
  Zeitumstellung: fehlende Stunde leer; doppelte Stunde Energie summieren/
  Leistung mitteln. HA-Zeitzone verwenden, nicht die Browserzeitzone.
- Frontend behält Entwürfe bei Speicherfehlern und zeigt Fehler sichtbar;
  Nutzernamen escapen/`textContent`, nicht ungeprüft in HTML einsetzen.
- Ein gemeinsames Panel für mehrere Entries; beim Unload anderer Entries erhalten.
  Diagnostics redigiert Koordinaten, gesamte Geometrie und API-Schlüssel.
- OSM-Kacheln sind die einzige externe Frontend-Verbindung. Attribution,
  Datenschutzhinweis und sichtbare Kartenladefehler erhalten.

## Verifikation und Pflege

Tests in [tests/](tests/); Abhängigkeiten in [tests/requirements.txt](tests/requirements.txt):

```text
python -m unittest discover -s custom_components\smart_pv_forecast\tests -p "test_*.py" -v
```

HA-API-Doubles ersetzen keinen Live-HA-Smoke-Test.
[tests/frontend.html](tests/frontend.html) lokal über HTTP aus dem
Integrationsverzeichnis bereitstellen. Prüfen: unabhängige Flächenwerte, Linienhöhen,
Vegetation, Wechselrichter/Sensoren, Zeichnen/Abbrechen, Ziehen, echtes Touch/Pinch,
Speicherfehler, Reload-Persistenz und 00–24-Diagramme. Schattenprojektion gegen
Prognose-Strahltests prüfen, Tag/Nacht, Ebenenhöhe, Uhrzeit, Fehler und SVG-Überlagerung
testen. Browser-Sonnendaten sind gemockt; Astral separat testen.
Keine echten Standorte in Tests.

Bei relevanten Änderungen diese Datei und [anleitungs.md](anleitungs.md) aktualisieren.
Alle Konfigurations-, Quellen-, Sensor-, Diagnose- und Frontendoberflächen konsistent halten.
