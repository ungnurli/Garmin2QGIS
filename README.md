# Garmin2QGIS

Importiert Garmin-Aktivitäten (`.fit`-Dateien) in eine **PostgreSQL/PostGIS-Datenbank**,
damit Touren, Routen und Kennzahlen in **QGIS** dargestellt und ausgewertet werden können.

Das Programm ist eine kleine Windows-App mit grafischer Oberfläche (Tkinter). Es liest ZIP-Dateien
mit FIT-Dateien aus einem Ordner, wandelt GPS-Daten und Messwerte um und schreibt sie in drei
Tabellen: Aktivitäten, Einzelpunkte und Linien. Ergänzende SQL-Views liefern Jahres-Ansichten
für QGIS.

> Ausführliche technische Beschreibung: [docs/DOKUMENTATION.md](docs/DOKUMENTATION.md)

## Inhalt

- [Funktionen](#funktionen)
- [Voraussetzungen](#voraussetzungen)
- [Schnellstart](#schnellstart)
- [Schritt für Schritt](#schritt-für-schritt)
- [Datenquelle: FIT-Dateien aus Garmin](#datenquelle-fit-dateien-aus-garmin)
- [Datenmodell in Kürze](#datenmodell-in-kürze)
- [Darstellung in QGIS](#darstellung-in-qgis)
- [Wiederholter Import](#wiederholter-import)
- [Bekannte Einschränkungen](#bekannte-einschränkungen)
- [Projektstruktur](#projektstruktur)
- [Build der app.exe (GitHub Actions)](#build-der-appexe-github-actions)
- [Datenschutz](#datenschutz)

## Funktionen

- Import mehrerer ZIP-Archive mit `.fit`-Dateien in einem Durchgang (Unterordner werden durchsucht).
- GPS-Punkte werden als PostGIS-Geometrie (WGS84, EPSG:4326) gespeichert, zusätzlich pro Aktivität
  als zusammenhängende Linie.
- Pro Punkt werden gespeichert: Zeit, Höhe, Herzfrequenz, Kadenz, Geschwindigkeit, Distanz,
  Leistung, Temperatur, Steigung und Vertikalgeschwindigkeit (sofern die Datei sie enthält).
- Pro Aktivität werden Sportart, Startzeit, Dauer, Distanz, Auf- und Abstieg, Herzfrequenz,
  Kalorien u. a. gespeichert.
- Duplikat-Schutz: bereits importierte Aktivitäten werden übersprungen.
- Fertige SQL-Views: `garmin_tracks_full` und je ein View pro Jahr (`garmin_tracks_2025` usw.).
- Protokollfenster in der App zeigt für jede Datei, was passiert ist.

## Voraussetzungen

| Was | Details |
|---|---|
| Datenbank | PostgreSQL mit der Erweiterung **PostGIS**, vom Rechner aus erreichbar |
| DB-Zugang | Benutzer mit Recht, Tabellen und Views anzulegen (im gewünschten Schema) |
| Betriebssystem | Windows für die fertige `app.exe`; mit Python läuft `app.py` auch unter anderen Systemen |
| Python (nur ohne exe) | Python 3 mit Tkinter, dazu `fitdecode` und `psycopg2-binary` (siehe `requirements.txt`) |
| QGIS | beliebige aktuelle Version mit PostgreSQL-Anbindung |

## Schnellstart

**Variante A: fertige exe (empfohlen)**

1. `app.exe` aus diesem Repository herunterladen (Datei öffnen → Download).
2. Doppelklick. Windows SmartScreen oder Virenscanner können bei einer unsignierten exe warnen;
   in dem Fall die Datei bewusst zulassen.
3. Quellordner und Datenbankdaten eintragen, **Import starten** klicken.

**Variante B: mit Python**

```bash
pip install fitdecode psycopg2-binary
python app.py
```

## Schritt für Schritt

1. **PostGIS aktivieren** (einmal pro Datenbank):
   ```sql
   CREATE EXTENSION IF NOT EXISTS postgis;
   ```
2. **App starten** und ausfüllen:

   | Feld | Bedeutung | Vorgabe |
   |---|---|---|
   | Quellordner | Ordner mit den ZIP-Dateien (wird rekursiv durchsucht) | – |
   | Host | Datenbankserver | `localhost` |
   | Port | Port des Servers | `5432` |
   | Datenbank | Name der Datenbank | – |
   | Schema | Schema, in dem Tabellen angelegt werden | `public` |
   | Benutzer / Passwort | Zugangsdaten | – |

3. **Import starten.** Die App legt die Tabellen beim ersten Lauf automatisch an und meldet
   im Protokoll pro Datei `✓` (importiert), `↷` (übersprungen, schon vorhanden),
   `⚠` (keine GPS-Punkte) oder `✗` (Fehler).
4. **Views anlegen:** `SQL_VIEW.sql` ab dem Abschnitt "Nach Import" in der Datenbank ausführen
   (z. B. in pgAdmin oder im QGIS-DB-Manager). Nach weiteren Importen den Block mit den
   Jahres-Views erneut ausführen, damit neue Jahre einen View bekommen.
5. **In QGIS laden** (siehe unten).

## Datenquelle: FIT-Dateien aus Garmin

Die App erwartet **ZIP-Dateien, in denen `.fit`-Dateien liegen**. Lose `.fit`-Dateien im Ordner
werden aktuell nicht gelesen, sie müssen in ein ZIP gepackt werden. Einzelne Aktivitäten lassen
sich z. B. in Garmin Connect über "Original exportieren" herunterladen; das liefert ein ZIP
mit einer `.fit`-Datei (z. B. `24505428629_ACTIVITY.fit`). Auch ein Gesamtexport des Garmin-Kontos
enthält in der Regel FIT-Dateien, meist in ZIP-Archiven.

## Datenmodell in Kürze

| Objekt | Art | Inhalt |
|---|---|---|
| `garmin_activities` | Tabelle | eine Zeile pro Aktivität mit Kennzahlen und Metadaten |
| `garmin_trackpoints` | Tabelle | ein Punkt (Point, EPSG:4326) pro GPS-Messung mit Messwerten |
| `garmin_tracks` | Tabelle | eine Linie (LineString, EPSG:4326) pro Aktivität |
| `garmin_tracks_full` | View | Linien mit den Aktivitätsattributen verknüpft (für QGIS) |
| `garmin_tracks_<Jahr>` | Views | `garmin_tracks_full`, gefiltert nach Startjahr |

Alle Spalten und Beziehungen: [docs/DOKUMENTATION.md](docs/DOKUMENTATION.md#4-datenbank).

## Darstellung in QGIS

1. **Layer → Layer hinzufügen → PostGIS-Layer hinzufügen**, neue Verbindung mit denselben
   Zugangsdaten anlegen.
2. `garmin_tracks_full` oder einen Jahres-View (`garmin_tracks_2025`) hinzufügen.
   Bei Views fragt QGIS nach einer eindeutigen Spalte: `id` wählen.
3. Die Geometriespalte heißt `geom`, das Koordinatensystem ist EPSG:4326.
4. Für unterschiedliche Farben je Sportart den Layerstil auf **Kategorisiert** stellen
   und die Spalte `sport` verwenden. Über Filter (`start_time`, `sport`) lassen sich Zeiträume
   oder Sportarten gezielt anzeigen.
5. `garmin_trackpoints` eignet sich für Punktdarstellungen, z. B. nach Herzfrequenz oder
   Geschwindigkeit eingefärbt.

## Wiederholter Import

Eine Aktivität gilt als bereits importiert, wenn **Dateiname und Startzeit** schon in
`garmin_activities` stehen. Dieselben ZIPs lassen sich daher gefahrlos erneut einlesen,
es entstehen keine Duplikate. Einen harten UNIQUE-Constraint gibt es bewusst nicht, damit
bereits vorhandene Duplikate in einer bestehenden Datenbank die Tabellenerstellung nicht
verhindern.

## Bekannte Einschränkungen

An einer Beispieldatei (Multisport-Tour, Mountainbike und Wandern) wurde Folgendes festgestellt:

- **Nur die erste Session wird ausgewertet.** Multisport-Aktivitäten (mehrere Sportarten in
  einer Datei) erscheinen als eine einzige Aktivität mit den Kennzahlen der ersten Session;
  der Track enthält aber alle Punkte der Datei.
- **Geschwindigkeit und Höhe können leer bleiben.** Neuere Garmin-Geräte speichern sie unter
  `enhanced_speed` und `enhanced_altitude`. Die App liest die Felder `speed` und `altitude`,
  deshalb bleiben diese Werte bei solchen Dateien leer (NULL).
- **Aktivitätsnamen:** Der Titel aus Garmin Connect steht nicht in der FIT-Datei. Vorhanden
  ist nur der Profilname der Sportart (z. B. "Mountainbike"), der derzeit nicht gespeichert wird.
- Nur ZIP-Dateien werden eingelesen, keine losen `.fit`-Dateien.
- Zugangsdaten werden nicht gespeichert und müssen bei jedem Start neu eingegeben werden.

Details und Lösungsvorschläge: [docs/DOKUMENTATION.md](docs/DOKUMENTATION.md#10-bekannte-einschränkungen-und-ideen).

## Projektstruktur

```
.
├── app.py                         # Anwendung: GUI, FIT-Parsing, Datenbank-Import
├── app.spec                       # PyInstaller-Konfiguration (Einzeldatei, ohne Konsole)
├── app.exe                        # Fertiger Windows-Build (vom Workflow aktualisiert)
├── requirements.txt               # Python-Abhängigkeiten für den Build
├── SQL_VIEW.sql                   # PostGIS-Erweiterung und Views für QGIS
├── docs/
│   └── DOKUMENTATION.md           # Ausführliche Dokumentation
└── .github/workflows/
    └── build-exe.yml              # GitHub-Actions-Workflow für den Windows-Build
```

## Build der app.exe (GitHub Actions)

Die `app.exe` muss nicht lokal gebaut werden. Der Workflow `Build app.exe`
(`.github/workflows/build-exe.yml`) baut sie auf einem Windows-Runner mit PyInstaller:

- **Auslöser:** Push auf `main` oder `prod`, wenn `app.py`, `app.spec`, `requirements.txt` oder
  der Workflow geändert wurden; außerdem manuell über *Actions → Build app.exe → Run workflow*.
- **Ergebnis:** Die exe steht als Artifact `app-exe` am Workflow-Lauf bereit und wird als
  Commit "Build app.exe [skip ci]" ins Repository zurückgeschrieben.
- **Hinweis:** Wegen dieser Bot-Commits vor eigenen Pushes zuerst `git pull` ausführen.
  In den Repository-Einstellungen (*Settings → Actions → General → Workflow permissions*) muss
  "Read and write permissions" aktiv sein.

Lokal bauen (optional, unter Windows):

```bash
pip install -r requirements.txt
pyinstaller app.spec --noconfirm   # Ergebnis: dist/app.exe
```

## Datenschutz

FIT-Dateien und die daraus entstehenden Tabellen enthalten genaue Standorte (z. B. Start- und
Zielorte wie der Wohnort) und Gesundheitsdaten (Herzfrequenz). Datenbank, Exporte und
FIT-Dateien daher nicht öffentlich ins Repository legen oder teilen. Die App liest nur die oben
genannten Felder; persönliche Profildaten aus der FIT-Datei (Gewicht, Größe, Name) werden nicht
übernommen.
