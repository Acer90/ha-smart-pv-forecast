# Smart PV Forecast: Solarprognose

Die Integration kombiniert PV-Prognosen aus Open-Meteo, DWD MOSMIX-L und optional
forecast.solar. Ab Version **0.5.0** wird die gesamte Anlage in der **Solarprognose**
konfiguriert. Adaptive Selbstoptimierung und die zugehörigen Lernsensoren sind entfernt.

## Erste Einrichtung

1. Die Integration in Home Assistant hinzufügen, Namen und Datenquellen einstellen.
   Open-Meteo ist standardmäßig aktiv. DWD-Station, forecast.solar-API-Schlüssel
   und Quellengewichte lassen sich dort ebenfalls festlegen. Optional hier den
   Google Maps API-Schlüssel für Satellitenbilder in 2D und auf dem 3D-Boden setzen.
2. Als Administrator **Solarprognose** in der Seitenleiste öffnen: `/smart-pv`.
   Die Startansicht ist **Heute · 0–24 Uhr**, danach folgen die Reiter
   **Karte & Planung** und **Wechselrichter & Einstellungen**.
   Die Reiter liegen direkt im kompakten Header. **Standort** wird nur bei
   mehreren konfigurierten Standorten eingeblendet. Erklärungen stehen in
   standardmäßig geschlossenen **Hinweisen**; Messwerte, Warnungen und Fehler
   bleiben sichtbar. Die Aufklapper lassen sich auch per Touch oder Tastatur öffnen.
   Bei mehreren Integrationsstandorten oben den gewünschten Standort auswählen.
3. Unter **Wechselrichter & Einstellungen** Wechselrichter und ihre Sensoren anlegen.
4. Unter **Karte & Planung** Arrays hinzufügen und ihre PV-Flächen zeichnen.
5. Schattenlinien einzeichnen und anschließend **Speichern** wählen.

Neue Installationen enthalten bewusst keine erfundene PV-Anlage. Bis zur ersten
gespeicherten Anlage sind Prognosesensoren nicht verfügbar. Der anfängliche
Kartenausschnitt orientiert sich am Home-Assistant-Standort.
Die normalen Integrationsdialoge verwalten **Prognose- und Kartenquellen**; Anlagen, Wechselrichter,
Sensorzuordnung und Prognoseparameter werden ausschließlich in der Solarprognose bearbeitet.

## Arrays mit mehreren PV-Flächen

Die 2D-Karte lässt sich mit **+**, Mausrad oder zwei Fingern bis Zoomstufe 22
vergrößern: achtfache Darstellung gegenüber der bisherigen Grenze bei 19.
Über Stufe 19 werden vorhandene Karten-/Satellitenbilder digital vergrößert.
Das erleichtert das genaue Platzieren von Eckpunkten, erzeugt aber keine neuen
Bilddetails. Positionen und Abmessungen der PV-Flächen bleiben beim Zoomen unverändert.

Ein **Array** gruppiert zusammengehörige PV-Flächen, etwa „Hausdach“ mit Ost- und
Westseite. **Array hinzufügen** wählen, benennen und gegebenenfalls einem
Wechselrichter zuordnen. Anschließend **PV-Fläche zeichnen** wählen, mindestens drei
Eckpunkte auf der Karte antippen und **Fläche abschließen** drücken.
Ein Array kann mehrere Flächen mit unterschiedlichen Eigenschaften enthalten.

Jede einzelne Fläche hat ihre eigenen Einstellungen im Eigenschaftenfenster
direkt über der Karte:

- **Name**, etwa „Ostdach“ oder „Garage“.
- **Leistung in Wp**: 5000 Wp entsprechen 5 kWp; Leistung jeder Fläche separat eingeben.
  Die Arrayleistung ergibt sich aus der Summe der Flächenleistungen.
- **Montageart**: dachparallel, aufgeständert, freistehend, Fassade oder Balkon.
- **Neigung zur Horizontalen**: 0° ist flach, 90° ist senkrecht.
- **Himmelsrichtung**: 0°/360° Nord, 90° Ost, 180° Süd, 270° West.
- **Unterkantenhöhe in Metern** über der gemeinsamen Bodenreferenz:
  immer der niedrigste Punkt dieser Fläche, nicht die mittlere Höhe.
- **Position und Umriss** durch die gezeichnete Fläche.

Eine Fläche antippen, um sie zu bearbeiten. Ausgewählte Eckpunkte lassen sich
ziehen; **Geometrie neu zeichnen** ersetzt den Umriss. **Abbrechen** lässt die
bisherige Geometrie bestehen. Sich kreuzende Kanten, doppelte Eckpunkte und
entartete Flächen werden beim Speichern abgewiesen.

### 3D-Ansicht und Bearbeitung

Mit **3D-Ansicht** zur räumlichen Ansicht wechseln. **Ansicht drehen** und
Ziehen drehen die Kamera; Mausrad, **+ / −** oder zwei Finger zoomen.
Mit zwei Fingern lässt sich die Kamera außerdem verschieben.
**Alles anzeigen** passt den Ausschnitt an alle eingezeichneten Elemente an.

Eine PV-Fläche oder Hinderniswand antippen, anschließend den Modus wählen:

- **Verschieben:** Das Element oder den grünen Griff ziehen. PV-Umrisse und
  Hinderniskonturen werden zusammen verschoben; Höhen bleiben unverändert.
  Ein im Array-Menü ausgewähltes Array kann mit allen seinen Flächen verschoben werden.
- **Höhe:** Nach oben oder unten ziehen, auch am violetten Griff.
  Bei PV-Flächen verändert das die Unterkantenhöhe. Bei Hindernissen werden
  die Konturpunkthöhen gemeinsam erhöht oder gesenkt, innerhalb der erlaubten Grenzen.
- **Ausrichtung:** Den blauen Griff auf seinem Kreis um die PV-Fläche drehen.
  Die Himmelsrichtung verändert sich in ganzen Grad; Umriss und Position bleiben erhalten.
  Höhe und Ausrichtung werden pro PV-Fläche bearbeitet, nicht gemeinsam für ein mehrflächiges Array.

Das Eigenschaftenfenster, die 2D-Karte und die 3D-Ansicht verwenden denselben
Entwurf. Änderungen mit **Speichern** übernehmen. Neue Flächen und Konturen
werden weiterhin in 2D gezeichnet; die Ansicht wechselt dafür automatisch.

Die PV-Flächen zeigen ihre tatsächliche modellierte Neigung und Höhen;
senkrechte Flächen verwenden ihre eingegebene senkrechte Ausdehnung.
Unvollständige Höhen werden nur als beschrifteter Umriss dargestellt.
Hindernislinien erscheinen als senkrechte Konturwände bis zur Bodenreferenz,
nicht als erfundene Gebäudevolumen. Es werden keine Geländehöhen oder
externen 3D-Gebäudedaten geladen.
Sonne und Schatten verwenden dieselbe Uhrzeit wie die Kartenvorschau.
Die Schatten bleiben eine horizontale Vergleichsebene auf der eingestellten
Höhe, keine Schattenberechnung auf geneigten 3D-Dächern.

### Richtungspfeil und berechnete Oberkante

Jede PV-Fläche hat einen Pfeil vom Mittelpunkt in ihre eingestellte
Himmelsrichtung. Der Pfeil reicht über den Umriss hinaus; bei einer
Richtungsänderung dreht er sich sofort. Die Karte bleibt nordorientiert.
Den runden Griff an der Pfeilspitze mit der Maus oder einem Finger um den
Flächenmittelpunkt ziehen, um die Ausrichtung direkt einzustellen. Der Winkel
wird auf ganze Grad gesetzt und im Zahlenfeld sofort mitgeführt. Umriss und
Position bleiben unverändert; die Oberkantenhöhe wird neu berechnet.
Die Änderung anschließend mit **Speichern** übernehmen.
Beim Verschieben wird keine native Browser-Text-/Bildauswahl auf der Karte ausgelöst.

Die gezeichnete Fläche ist der **horizontale Grundriss**. Die Unterkante zeigt in
Pfeilrichtung, die gegenüberliegende Seite liegt höher. Die Projektion des gesamten
Umrisses auf diese Richtung ergibt die maßgebliche Grundrisstiefe:

```text
Höhenunterschied = Grundrisstiefe × tan(Neigung)
Oberkantenhöhe = Unterkantenhöhe + Höhenunterschied
```

Beispiel: 4 m Tiefe in Neigungsrichtung, 30° Neigung und 6 m Unterkantenhöhe
ergeben rund **8,31 m Oberkantenhöhe**. Nicht irgendeine Kantenlänge oder die
Quadratmeterzahl verwenden: Bei gedrehter Ausrichtung kann sich die maßgebliche
Tiefe ändern.

**Berechnete Oberkante**, **Höhenunterschied** und **Grundrisstiefe** erscheinen
im Eigenschaftenfenster. Die Karte zeigt zusätzlich den Höhenbereich jeder Fläche.
Alle Werte reagieren auf Winkel-, Richtungs-, Höhen- und Geometrieänderungen.
Die Verschattungsprognose verwendet an ihren räumlichen Abtastpunkten tatsächlich
unterschiedliche Höhen entlang der geneigten Fläche, nicht überall die Unterkante.

Bei **90°** lässt sich die Höhe nicht aus einem horizontalen Grundriss ableiten.
Dafür **Senkrechte Ausdehnung bei 90° (m)** eintragen; die Oberkante ergibt sich
aus Unterkante plus dieser Ausdehnung. Der gezeichnete Umriss dient hier als
Positions-/Breitenhilfe. Für die Berechnung werden die Punkte auf eine senkrechte
Ebene durch den Flächenmittelpunkt abgebildet und über die senkrechte Ausdehnung verteilt.
Die senkrechte Ebene steht quer zur Pfeilrichtung.

Fehlt bei einer bestehenden senkrechten Fläche diese Ausdehnung, bleibt sie in der
Solarprognose bearbeitbar. Die Prognosesensoren bleiben bis zur Ergänzung nicht
verfügbar; es wird keine Höhe geraten. Auch eine unrealistische berechnete
Oberkante über 1000 m muss korrigiert werden.

Montageart ist eine Beschreibung, keine automatische Winkel- oder Höhenvorgabe.
Die tatsächliche Neigung und Himmelsrichtung jeder Fläche werden an die
Prognosequellen übergeben. Tracker oder gegenseitige Verschattung von Modulreihen
werden nicht automatisch aus „aufgeständert“ abgeleitet.

## Schattenlinien und individuelle Höhen

1. **Schattenlinie zeichnen** wählen.
2. Die höchste sichtbare Kontur des Hindernisses als Linie auf der Karte markieren.
   Im Zeichnen-Werkzeug die **Höhe dieses Konturpunkts** für den nächsten Punkt einstellen.
3. Mindestens zwei Punkte setzen und **Linie abschließen** wählen.
4. Im Eigenschaftenfenster Name, Objektart und Reduktion einstellen.
   Jeder Konturpunkt hat eine einzeln bearbeitbare Höhe.

Die Linie darf offen bleiben, etwa für eine Baumreihe oder die Dachkante eines Hauses.
Zwischen ihren Punkten wird die Höhe linear interpoliert. Das Modell behandelt
diese Kontur als senkrechtes Hindernis bis zur Bodenreferenz, nicht als vollständiges
dreidimensionales Gebäude oder Baumvolumen. Für Schatten aus mehreren Richtungen
entsprechende weitere Kanten einzeichnen oder die Kontur schließen.

Ausgewählte Konturpunkte können verschoben werden; ihre Höhen bleiben erhalten.
Über die Punktliste lassen sich Positionen und Höhen auch numerisch bearbeiten.

**Alle Höhen müssen denselben Bezug verwenden.** Beispiel: Module auf 6 m,
Nachbarhaus auf 12 m – für die Schattenlänge zählen 6 m Höhendifferenz.
Unterschiedliches Geländeniveau in die Höhen einrechnen.

### Massive Objekte und Vegetation

- **Massiv · Haus / Mauer**: konstante **Reduktion im Schatten**.
  100 % bedeutet vollständige Abschattung, 0 % keine Reduktion.
  Neue massive Objekte starten mit 100 %.
- **Durchlässig · Baum / Vegetation**: getrennte Reduktion **mit Blättern**
  und **ohne Blätter**, beispielsweise 90 % beziehungsweise 50 %.

Für Vegetation gibt es drei Betriebsarten:

- **Automatisch** anhand der jährlich wiederkehrenden Blattzeit.
- **Mit Blättern** als manuelle Übersteuerung.
- **Ohne Blätter** als manuelle Übersteuerung.

Beginn und Ende werden als `TT.MM.` eingegeben, zum Beispiel `01.04.` und `01.11.`.
Der Beginn zählt zur Blattzeit, der Endtag bereits zur Zeit ohne Blätter.
Auch Intervalle über den Jahreswechsel werden unterstützt.
Die Prozentwerte sind einstellbare Näherungen für durchgelassenes/diffuses Licht;
es findet keine automatische Optimierung anhand von Messwerten statt.

## Wechselrichter und Sensoren

### Optionale Google-Satellitenbilder

OpenStreetMap liefert die normale Straßenkarte, keine Satellitenaufnahmen.
Unter **Einstellungen → Geräte & Dienste → Smart PV Forecast → Konfigurieren**
im normalen Home-Assistant-Integrationsdialog den optionalen Google Maps
Browser-API-Schlüssel hinterlegen. Bereits in der Solarprognose gespeicherte Schlüssel
bleiben erhalten und sind dort im Integrationsdialog bearbeitbar.
Die frühere Infokarte **Satellitenkarte (optional)** entfällt in der Solarprognose;
die API-Konfiguration und Kartenfunktion bleiben unverändert. Im Google-Cloud-Projekt
die **Maps JavaScript API** (2D), **Maps Static API** (3D) und Abrechnung aktivieren; je nach Nutzung können
Kosten entstehen. Aktuelle Konditionen:
[Google Maps JavaScript API](https://developers.google.com/maps/documentation/javascript/usage-and-billing).

Den Schlüssel auf **Maps JavaScript API und Maps Static API** sowie über **Website-/HTTP-Referrer-
Beschränkungen** auf die tatsächlich verwendeten Home-Assistant-Adressen
beschränken, beispielsweise `https://ha.example.de/*`.
Interne und externe Adressen müssen gegebenenfalls separat erlaubt werden.
Browser-Schlüssel sind trotz verdecktem Eingabefeld technisch im Browser sichtbar.
Keine unbeschränkten Server-Schlüssel verwenden.

Im Integrationsdialog speichern. Mit hinterlegtem Schlüssel wird
**Google · Satellit standardmäßig** verwendet, ohne Schlüssel **OpenStreetMap**.
Nach Änderung eines bereits geladenen Schlüssels die Seite vollständig neu laden.
Mit Schlüssel wird Google beim Öffnen geladen; dabei werden unter anderem
IP-Adresse und Kartenausschnitt an Google übermittelt.
Bei Lade- oder Zugriffsfehlern erscheint eine Meldung; die Kartenquelle kann
ausdrücklich wieder auf **OpenStreetMap** gestellt werden.
Ohne Schlüssel bleibt OpenStreetMap nutzbar. Die Google-Karte nutzt die
offizielle JavaScript API einschließlich ihrer Quellenangaben, keine abgegriffenen
Kachel-URLs. In 3D liegt ein Satellitenbild aus der Maps Static API auf der
Bodenebene unter den bearbeitbaren Flächen und Hindernissen. Das sind keine
Google-3D-Gebäude oder Geländehöhen. Die Quellenangaben werden zusätzlich
unverzerrt am unteren Rand angezeigt.
Das 3D-Bild zeigt einen festen Ausschnitt um die modellierten Elemente; Drehen,
Zoomen und Bearbeiten laden nicht bei jeder Bewegung ein neues Bild.
**Alles anzeigen** passt den Ausschnitt erneut an und lädt ein neues Bild,
was einen weiteren kostenpflichtigen API-Aufruf auslösen kann.
Bei einem Fehler der Static API erscheint eine Meldung; die Geometrie bleibt
bedienbar. **OpenStreetMap** schaltet in 3D auf die schematische Ansicht zurück.

Unter **Wechselrichter & Einstellungen** beliebig benannte Wechselrichter anlegen:

- **Leistungsgrenze in W** entsprechend der AC-Nennleistung des Wechselrichters.
- **Aktuelle PV-Erzeugungsleistung (W / kW):** Die Home-Assistant-Entitäts-ID
  der momentanen Solarleistung dieses Wechselrichters auswählen, möglichst
  seine aus Solarenergie erzeugte AC-Leistung. **Nicht „Load“/Hausverbrauch,
  Netzbezug oder Netzeinspeisung** eintragen: Das sind andere Messgrößen.
  Bei Hybridwechselrichtern keinen kombinierten Ausgangswert verwenden, der
  auch Batterieentladung enthält. Falls nur ein separater PV-/DC-Leistungssensor
  vorhanden ist, kann dieser zur Anzeige genutzt werden; wegen Umwandlungsverlusten
  ist er aber nicht direkt mit der AC-Prognose vergleichbar.
  Ohne passenden Sensor das optionale Feld leer lassen.
- **Gesamtertragssensor** mit Einheit Wh, kWh oder MWh.

Die Sensorfelder bieten passende vorhandene Home-Assistant-Sensoren zur Auswahl.
Sensorzuordnungen sind optional. Im Arrayfenster wird bestimmt, welcher
Wechselrichter das Array versorgt; mehrere Arrays dürfen denselben Wechselrichter
verwenden. Bei Bedarf weitere Wechselrichter anlegen.

Aktuelle Sensorwerte werden angezeigt, aber **nicht zum Lernen oder zur Korrektur
der Prognose verwendet**. Nicht verfügbare Werte bleiben sichtbar nicht verfügbar
und werden nicht als 0 ausgegeben.

Wechselrichterlimits verwenden eine konservative Skalierung der Tagesprognose vor
der lokalen Objektverschattung. Das ist kein exaktes, stundenweises Clippingmodell
und keine Simulation der Leistungsverteilung auf Eingänge/MPPTs.

## Heute: Diagramme von 00 bis 24 Uhr

### Animierte Ertragsprognose für die nächsten Tage

Ganz oben stehen Tagesbalken ab heute entsprechend **Prognosetage (1–16)** für die **gesamte Anlage mit
berechneter Verschattung**, inklusive Ertrag in kWh. Die Werte stammen aus den
bereits abgerufenen Prognosen; die Anzeige verursacht keine weiteren Wetterabrufe.
Alle Balken verwenden dieselbe Skala. Die Farben stellen Ertragsschwellen dar,
keine Wahrscheinlichkeiten: zunächst Rot, danach Orange, darüber Grün.

Unter **Wechselrichter & Einstellungen → Farbschwellen der Mehrtagesprognose**
lassen sich beide Übergänge in **kWh** festlegen und speichern.
Standard: Rot bis 15 kWh, Orange bis 25 kWh, darüber Grün.
Die zweite Schwelle muss größer als die erste sein.

Fehlende Tage zeigen **Keine Daten**, nicht 0 kWh. Teilprognosen tragen **≥**
und **Teilprognose**: Es werden ausschließlich Stunden summiert, für die alle
Arrays Daten haben. Die vollständige Tagesabdeckung steht am Balken als
Hinweis und ist für Hilfstechnologien zugänglich. Die HA-Zeitzone bestimmt den
Kalendertag; bei Zeitumstellungen werden 23 bzw. 25 reale Stunden berücksichtigt.
Die Balken wachsen beim Anzeigen animiert; bei aktivierter Systemoption für
reduzierte Bewegung entfällt die Animation.

### String-Leistungsmessungen je PV-Fläche

Eine gezeichnete Fläche auf der Karte auswählen und unter **PV-Strings · Messwerte**
mit **String hinzufügen** die einzelnen Strings benennen. Für jeden String den
Home-Assistant-Sensor seiner **aktuellen PV-Leistung in W oder kW** auswählen.
Nicht die Nennleistung in Wp, Energie in kWh oder „Load“/Hausverbrauch eintragen.
Bis zu 16 Strings je Fläche und insgesamt 256 Zuordnungen sind möglich.
Die Zuordnung ist optional; ohne Stringsensor bleibt die bisherige Prognose unverändert.

Nach dem Speichern erscheinen im Diagramm des jeweiligen Arrays zusätzliche
Messkurven mit **Flächenname / Stringname**, auch in Stundentabelle und Tooltip.
Jede Kurve zeigt nur diesen String, nicht die gesamte Array-Leistung.
Es wird keine eigene Stringprognose erfunden. DC-Stringleistung ist wegen
Umwandlungsverlusten nicht direkt mit der AC-Arrayprognose vergleichbar.

Home Assistants **History/Recorder** muss diese Sensoren aufzeichnen.
Der Stundenmittelwert wird nach der Dauer jedes Sensorzustands gewichtet,
nicht nach der Anzahl von Zustandsänderungen. W/kW werden zu W vereinheitlicht.
Ein Zustand gilt bis zur nächsten aufgezeichneten Änderung.
Fehlende Anfangswerte, unbekannte Zustände, falsche Einheiten und zukünftige
Stunden werden nicht als 0 behandelt. Stunden mit Datenlücken zeigen den
zeitgewichteten Durchschnitt der vorhandenen Messminuten.
Ohne gültige Messminuten bleibt die Stunde leer. Tabellen und Tooltips zeigen
nur Leistungswerte, ohne Messminuten oder Zusatzkennzeichnung.
Die Stundenwerttabellen passen sich der verfügbaren Breite an; lange Überschriften
und Werte umbrechen ohne horizontales Scrollen. Die laufende Stunde wird
nur bis zum Abruf gemittelt. Beim Öffnen des Studios oder Auswählen von
**Heute · 0–24 Uhr** werden die Diagramme und Messwerte neu geladen.
Die sichtbare Heute-Ansicht aktualisiert sich außerdem jede Minute und beim
Zurückkehren aus einem ausgeblendeten Browser-Tab, sofern keine ungespeicherten
Änderungen vorliegen. **Messwerte abgerufen bis** zeigt die Abrufgrenze in der
Home-Assistant-Ortszeit; vollständig fehlende Stunden bleiben leer. Die laufende
Stunde wird bis zu dieser Grenze angezeigt, nicht erst nach Stundenende.
Die zusätzlichen Buttons für Neuladen, Diagrammaktualisierung und das
Home-Assistant-Hamburgermenü entfallen; **Speichern** bleibt im Header.
Eine doppelte Stunde bei Zeitumstellung wird zeitgewichtet zusammengefasst;
die übersprungene Stunde bleibt leer.
Fehler beim Historienabruf werden sichtbar gemeldet; Prognosekurven bleiben nutzbar.
Messwerte dienen ausschließlich der Kontrolle, niemals zur automatischen Optimierung.

### Solarprognose-Karten im Dashboard

Die Ertragsprognose und Leistungsdiagramme lassen sich als **einzelne Karten**
in beliebigen Home-Assistant-Dashboards verwenden. Die Solarprognose bleibt der
Ort für Anlagen-, String- und Sensor-Konfiguration; die Karten sind reine Anzeigen.

**Einmalig einrichten:**

1. Nach Installation dieser Version **Home Assistant neu starten**, damit die
   zusätzlichen lesenden WebSocket-Befehle und die Dashboard-Ressource
   **automatisch registriert** werden.
2. Den Browser vollständig neu laden.
3. Dashboard bearbeiten → **Karte hinzufügen** und nach **Solarprognose** suchen.

Ein manuelles Hinzufügen unter **Ressourcen** ist nicht nötig.
Im Storage-Modus wird `/smart_pv_forecast_static/dashboard.js?v=1` als
**JavaScript-Modul** gespeichert. Vorhandene Einträge derselben Moduldatei
werden wiederverwendet und auf die aktuelle URL/den Modultyp gebracht,
statt weitere Einträge anzulegen. Andere Ressourcen bleiben unverändert.
Bei YAML-verwalteten Ressourcen wird das Modul automatisch über das
HA-Frontend geladen, ohne YAML-Dateien zu verändern. Ein dort bereits
konfiguriertes Solarprognose-Modul wird nicht zusätzlich geladen.

Es gibt zwei Karten mit **visuellem Editor**:

- **Solarprognose · Ertragsprognose**: Standort auswählen; zeigt die nächsten
  die unter **Prognosetage (1–16)** eingestellte Tagesanzahl mit den in der
  Solarprognose eingestellten Farbschwellen.
  Im visuellen Karteneditor lässt sich **Kompaktmodus** einschalten:
  Alle eingestellten Tage und die farbigen Balken bleiben erhalten, mit kleineren
  Abständen, 15 px hohen Balken und normal gewichteter Dashboard-Schrift
  (standardmäßig 14 px, über die Home-Assistant-Themes anpassbar), ohne Überschrift,
  Hinweise und Farblegende.
  Tageskürzel und kWh-Werte bleiben sichtbar, Datum und „Heute“ entfallen;
  Teilprognosen und fehlende Daten
  bleiben gekennzeichnet. Das Datum steht weiterhin im zugänglichen Balkentext.
  Die Anzeige „24/24 Stunden mit Daten“ entfällt überall, auch in Balken-Tooltips.
  Standardmäßig bleibt die normale Ansicht aktiv.
  Alternativ in YAML `compact: true` ergänzen.
- **Solarprognose · Leistungsdiagramm**: Standort und **Gesamtanlage** oder ein
  einzelnes **Array** auswählen. Jede Karte enthält genau ein Diagramm mit
  Prognose, Messkurven, Tooltip und aufklappbarer Stundentabelle.
  Array-Karten zeigen die dort definierten String-Sensoren, die Gesamtkarte
  die Summe der Wechselrichter-PV-Leistungssensoren.

Bei genau einem Standort ist dessen automatische Auswahl möglich; bei
mehreren Standorten muss einer ausgewählt werden. Zum Anzeigen ist kein
Administratorzugriff auf die Solarprognose erforderlich. Die Karten verwenden
authentifizierte, rein lesende Befehle ohne API-Schlüssel oder Schattengeometrie.
Messwerte benötigen weiterhin die HA-History/Recorder-Aufzeichnung und die
entsprechenden Benutzerrechte.

Sichtbare Karten aktualisieren sich jede Minute und beim Zurückkehren zum
Browser-Tab. Dabei wird nur im Hintergrund geprüft: Unveränderte Diagramme
und Ertragsbalken werden nicht neu gezeichnet. Das gilt auch für die Heute-Ansicht
in der Solarprognose. Nur tatsächlich geänderte Texte, Messpunkte und Prognosewerte
werden angepasst; geöffnete Tabellen/Hinweise und die ausgewählte Stunde bleiben
erhalten. Eine neue Abrufzeit allein ersetzt weder Diagramme noch Ertragsbalken.
Mehrere Karten teilen sich die Abfragen. Abruffehler werden
angezeigt; bei einem History-Fehler bleiben Prognosekurven sichtbar.
Fehlende Messzeiträume werden nicht durch Nullwerte oder Schätzungen ersetzt.

Optional ist auch YAML möglich:

```yaml
type: custom:smart-pv-yield-card
entry_id: DEINE_CONFIG_ENTRY_ID
```

```yaml
type: custom:smart-pv-power-card
entry_id: DEINE_CONFIG_ENTRY_ID
array: total
```

Für ein einzelnes Array statt `total` dessen **nullbasierten Index** einsetzen,
beispielsweise `array: 0` für das erste Array. Nach Löschen oder Neuordnen von
Arrays die Auswahl im Karteneditor prüfen. Die `entry_id` wird vom visuellen
Editor gesetzt und ist in der YAML-Ansicht der Karte ablesbar.
Auch in YAML-verwalteten Dashboards ist kein zusätzlicher manueller
Ressourceneintrag erforderlich.

### Prognosevergleich

**Home-Assistant-Energie-Dashboard:** Prognosestunden werden chronologisch
nach ihrem tatsächlichen Zeitpunkt ausgegeben, auch bei unterschiedlichen
UTC-Offsets. Fehlende Strahlungswerte (Open-Meteo `null`, fehlende Stunden,
DWD `-`) bleiben Datenlücken und werden nicht in 0 umgewandelt.
Array- und Gesamtwerte werden nur an Zeitpunkten mit Daten aller zugehörigen
Flächen gebildet. Echte Nullprognosen, beispielsweise nachts oder bei
vollständiger Verschattung, bleiben erhalten.
Änderungen der vorhandenen Stunden aktualisieren auch den gespeicherten
Energie-Snapshot, selbst wenn die betroffenen Werte 0 sind.
Nach einem Update Home Assistant neu starten, damit die Quellen neu
eingelesen und korrigierte Prognosen bereitgestellt werden.

Unter **Heute · 0–24 Uhr** erscheinen:

1. Ein Leistungsdiagramm für die **Gesamtanlage** in W:
   **Ohne Objektverschattung (100 %)** gegenüber **Mit berechneter Verschattung**,
   zusätzlich die **gemessene Gesamt-PV-Leistung** aus der HA-Historie.
2. Für **jedes Array** ein Leistungsvergleich in W:
   **Ohne Objektverschattung (100 %)** gegenüber **Mit berechneter Verschattung**.
   Dazu Tagesenergie in kWh und die Tagesreduktion.

Das frühere eigenständige Prozentdiagramm der Verschattungsreduktion entfällt.
Prozentwerte bleiben als Kennzahl, in der Stundentabelle und im Tooltip erhalten.
Gesamtprognosen summieren Arrays nur an gemeinsam vorhandenen realen Zeitpunkten;
fehlende Arraydaten werden nicht durch 0 ersetzt.

Für die Gesamtmessung benötigt **jeder konfigurierte Wechselrichter** einen
eigenen PV-Leistungssensor in W/kW. Dieselbe Entität an mehreren Wechselrichtern
wird nicht doppelt gezählt; stattdessen erscheint ein Zuordnungshinweis.
Die Sensorzustände werden vor der zeitgewichteten Stundenmittelbildung summiert.
Die gemessene Leistung erscheint als **gestapelte Stundenbalken**, mit einer
eigenen Farbe je Wechselrichter, in der Solarprognose und in der Gesamtanlagen-Karte.
Die Balkenhöhe ist die gemessene Gesamt-PV-Leistung in W; die Prognosen bleiben
Linien. Tooltip und Stundentabelle zeigen jeden Wechselrichter sowie die Summe.
Bei Datenlücken werden alle Wechselrichteranteile und die Summe ausschließlich
über dieselben Zeitabschnitte mit gültigen Werten aller Wechselrichter gemittelt.
So entsprechen die gestapelten Anteile genau der Summe. Die Stunde wird als
ohne Zusatzkennzeichnung angezeigt; ohne gemeinsame Messzeit bleibt der Balken leer.
Jeder Abruf fragt den gesamten heutigen Zeitraum erneut ab, nicht nur einmal
zur vollen Stunde. Bereits aufgezeichnete Daten werden nach Wiederverbindung
erneut geladen; vom Recorder während einer Abschaltung nicht aufgezeichnete
Messwerte können nicht nachträglich rekonstruiert werden.
Eine bloße Summe einzelner Strings wird nicht als
vollständige Anlagenmessung ausgegeben. Messwerte bleiben reine Anzeige und
setzen eine Sensoraufzeichnung im History/Recorder voraus.

Die **einzelnen Array-Diagramme** zeigen zusätzlich für jeden dort definierten
**PV-String** dessen eigene historische Leistung für heute.
Unter **Karte & Planung** die PV-Fläche auswählen, unter **PV-Strings · Messwerte**
die jeweiligen Leistungssensoren hinterlegen und speichern.
Jeder String erscheint separat als **Gemessen · PV-String: Fläche / Stringname**
in Legende, Tooltip und Stundentabelle. Die Wechselrichterleistung erscheint
**nur im Diagramm der Gesamtanlage**, nicht bei den einzelnen Arrays.
Dieselbe Historienabfrage wird für Gesamtanlage und Strings gemeinsam genutzt.

Die Prognosekurven zeigen weiterhin das gesamte Array, keine rechnerisch
aufgeteilte Stringprognose. Sie bleiben der heutige Modellvergleich, keine
nachträglich archivierte damalige Prognose.

Stunden antippen oder mit Maus/Tastatur auswählen, um genaue Werte zu sehen.
Die aufklappbare Stundentabelle enthält die Zahlen zusätzlich.

Beide Kurven verwenden dieselben Wetterdaten, Quellengewichte und Begrenzungen;
der Unterschied ist die konfigurierte lokale Objektverschattung. „100 %“ bedeutet
also **unverschattete Modellprognose**, nicht dauerhaft die installierte Wp-Leistung.
Die lokale Zeitzone von Home Assistant bestimmt den Tag.

Die beiden Prognosekurven sind ein **Modellvergleich**, keine Aufzeichnung gemessener Erträge.
Auch bereits vergangene Stunden werden mit der aktuell gespeicherten Geometrie
berechnet. Die bestehende Erhaltung vergangener Prognosestunden in den Sensoren
ist davon unabhängig. Fehlende Quelldaten bleiben Lücken; bei nächtlicher
Basisleistung 0 W wird 0 % Reduktion angezeigt.
Bei Zeitumstellungen bleibt eine fehlende Stunde leer; in einer doppelt vorkommenden
Stunde wird Energie summiert und Leistung gemittelt.

Nach einer Speicherung wird die Prognose neu berechnet. Bis die passende Revision
verfügbar ist, erscheint ein Aktualisierungshinweis statt eines veralteten Diagramms.

## Geworfene Schatten und Sonnenposition auf der Karte

Über der Karte gibt es eine **Sonnen- und Schattenvorschau**:

- **Jetzt (live)** ist standardmäßig aktiv. Die tatsächliche aktuelle Zeit wird
  verwendet; die Vorschau aktualisiert sich einmal pro Minute.
- **Datum und Uhrzeit (HA-Ortszeit)** erlauben das Prüfen anderer Tageszeiten und
  Jahreszeiten zwischen 2000 und 2100. Das Bearbeiten schaltet den Live-Modus aus.
  **Jetzt (live)** führt wieder zur aktuellen Zeit zurück.
- Der kleine **Sonnenkompass** zeigt die Richtung zur Sonne auf der nordorientierten
  Karte. Himmelsrichtung und Sonnenhöhe stehen zusätzlich als Zahlen über der Karte.
- **Höhe automatisch** verwendet die Unterkantenhöhe der ausgewählten PV-Fläche;
  ohne Flächenauswahl den Boden auf 0 m. Ein altes PV-Punktarray verwendet bei
  Auswahl seine Montagehöhe.
- **Schattenebene (m)** erlaubt eine frei eingestellte Referenzhöhe. Bei mehreren
  unterschiedlich hohen PV-Flächen jeweils die zu prüfende Fläche auswählen.
- **Geworfene Schatten anzeigen** blendet die Schattenflächen ein oder aus.

Die gesamte Karte zeigt gleichzeitig Schatten auf **einer gemeinsamen horizontalen
Ebene**, nicht die Schatten jeder Dachfläche auf ihrer jeweils eigenen Höhe.
Sie ist keine Projektion auf das geneigte Dach. Zum Vergleich die berechnete
Oberkantenhöhe manuell als Schattenebene auswählen. Die eigentliche Prognose
berücksichtigt dagegen die unterschiedliche Höhe der Abtastpunkte.
Die Schatten werden aus den Konturpunkten und individuellen Höhen in die Richtung
von der Sonne weg projiziert. Eine Kontur unterhalb der gewählten Ebene wirft dort
keinen Schatten. Die Stärke der Abdunklung berücksichtigt die eingestellte
Reduktion und die Blattzeit am gewählten Datum. Überlagerungen verwenden die
stärkste Reduktion, ohne Schatten mehrfach zu multiplizieren.

Die Vorschau berücksichtigt **auch ungespeicherte Eingaben** an vorhandenen
Geometrien. Damit lassen sich Höhen, Vegetation und Umrisse prüfen, bevor die
Prognosekonfiguration gespeichert wird. Ein laufender neuer Linien-/Flächenentwurf
geht erst nach **Linie/Fläche abschließen** ein.
Zeit- und Ebenenauswahl sind nur Vorschauparameter; sie verändern keine
Prognoseeinstellungen und lösen keine Wetter-/Quellenabrufe aus.

Sonnenstände werden lokal berechnet, in der Home-Assistant-Zeitzone und unabhängig
von der Browserzeitzone. Nach Sonnenuntergang erscheinen keine geworfenen Schatten.
Sehr lange Schatten nahe dem Horizont werden für die Kartenvorschau auf 10 km
begrenzt; ein sichtbarer Hinweis kennzeichnet dies. Die Prognose selbst wird dadurch
nicht begrenzt. Ungültige Zeiten in der Lücke einer Zeitumstellung werden abgewiesen;
eine doppelte Ortszeit verwendet bei manueller Auswahl die erste Stunde.

Die Karte zeigt eine geometrische Momentaufnahme, keinen Wetter-/Wolkenverlauf und
keine Modul-/Stringsimulation. Die Tagesdiagramme verwenden dagegen gespeicherte
Konfiguration, Wetterprognosen und mehrere Zeitpunkte pro Stunde.

## Weitere Prognoseparameter und Speicherung

Ebenfalls in der Solarprognose: Morgen-/Abenddämpfung, Horizont, Open-Meteo-Prognosetage,
Anzahl der Tagesertragssensoren und optional eine zusätzliche globale Leistungsgrenze.
Bei aktivem Open-Meteo darf die Zahl der Tagesertragssensoren die Prognosetage
nicht überschreiten.

**Speichern** persistiert gemeinsam Flächen, Hindernisse, Wechselrichter und
Prognoseparameter. Quellenoptionen und API-Schlüssel bleiben dabei erhalten.
Die Integration lädt den Standort anschließend neu. **Solarprognose bleibt dabei
im Seitenmenü und die aktuelle Seite mit ihrem Reiter und ihrer Kartenansicht
bleibt geöffnet**. Das gemeinsame Panel wird erst beim Löschen des letzten
konfigurierten Integrationsstandorts entfernt, nicht bei einem Reload.
Bei Speicherfehlern bleiben die ungespeicherten Angaben im UI bestehen.
Parallele Änderungen werden anhand der Revision erkannt und nicht überschrieben;
dann neu laden und die Änderungen erneut übernehmen.
Standortwechsel und Neuladen fragen vor dem Verwerfen ungespeicherter Änderungen nach.
Wechselrichter und Hindernisse dürfen auch schon ohne PV-Array gespeichert werden;
die Prognose bleibt dann nicht verfügbar. Das Löschen des letzten Arrays fragt
ausdrücklich nach und führt ebenfalls zurück in diesen Einrichtungszustand.

## Handy, Touch und Modellgrenzen

- Karte mit einem Finger verschieben, mit zwei Fingern zoomen; Punkte durch Antippen
  setzen. Am PC stehen Maus, Mausrad und Zoomtasten zur Verfügung.
- Auf dem Handy erscheint das Eigenschaftenfenster als scrollbare Karte am unteren
  Kartenrand. Zeichnen blendet es aus, damit die Karte frei bleibt.
- Flächen werden räumlich gewichtet abgetastet, Sonnenstände sechsmal je Stunde.
  Teilverschattung ist damit eine Näherung, kein vollständig aufgelöstes Modulmodell.
- Überlappende Hindernisse verwenden die stärkste Abschattung, nicht das Produkt.
  Lokale Objektverschattung wirkt auf DWD, Open-Meteo und forecast.solar.
- Elektrische Stringeffekte, Bypassdioden, MPP-Tracking und automatische
  Selbstverschattung werden nicht simuliert. Eine gezeichnete Fläche wird als
  zusammenhängende geneigte Ebene behandelt; nicht ebene Dächer in getrennte Flächen aufteilen.
- Maximal 64 Arrays, 32 Flächen pro Array und insgesamt 128 Berechnungsflächen;
  64 Eckpunkte je Fläche, 128 Hindernisse und 65 Konturpunkte je Hindernis.
  Viele Flächen erhöhen die Quellenabrufe; insbesondere forecast.solar-API-Limits beachten.
- Polygon-Ausdehnung maximal 10 km; Kartenbreitengrade −85° bis +85°.

OpenStreetMap liefert Kartenkacheln direkt an den Browser. Dabei werden angezeigte
Kartenausschnitte und die Browser-IP an den Kartenanbieter übermittelt.
Es gibt keine zusätzlichen JavaScript-Importe von einem CDN. Bei Kartenladefehlern
bleiben Zahlenfelder und gespeicherte Geometrie bearbeitbar.

## Bestehende Installationen aktualisieren

Nach dem Austausch der Integrationsdateien **Home Assistant neu starten**.
Config-Entries werden auf Version **7** migriert. Bestehende Anlagen und
Quelleneinstellungen bleiben erhalten; alte PV-Punkte bleiben bearbeitbar und
können anschließend durch gezeichnete Flächen ersetzt werden.
Alte Hindernispolygone werden in geschlossene Höhenlinien umgewandelt, einschließlich
ihrer bisherigen Reststrahlung.
Vorhandene Flächenhöhen werden jetzt als Unterkantenhöhen verstanden; dadurch kann
sich ihre berechnete Verschattung ändern. Die Angaben in der Solarprognose prüfen.
Bereits gezeichnete 90°-Flächen benötigen die ergänzende senkrechte Ausdehnung.

Adaptive Lernoptionen, Adaptive-Sensoren und der standortbezogene Lernspeicher
werden entfernt. Kernsensoren behalten ihre IDs. Energie-Snapshots tragen einen
Konfigurationsfingerprint, damit veraltete Werte nicht weiterverwendet werden.
Die Integration muss nicht gelöscht oder neu angelegt werden.
