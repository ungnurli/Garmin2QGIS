# Garmin2QGIS – Dokumentation

Diese Dokumentation beschreibt Aufbau, Funktionsweise und Betrieb des Projekts im Detail.
Eine kurze Einführung steht in der [README](../README.md).

## Inhaltsverzeichnis

1. [Überblick und Architektur](#1-überblick-und-architektur)
2. [Importablauf im Detail](#2-importablauf-im-detail)
3. [Zuordnung FIT-Felder → Datenbank](#3-zuordnung-fit-felder--datenbank)
4. [Datenbank](#4-datenbank)
5. [SQL-Views](#5-sql-views)
6. [Arbeiten mit QGIS](#6-arbeiten-mit-qgis)
7. [Build und Continuous Integration](#7-build-und-continuous-integration)
8. [Betrieb: Neuimport, Zurücksetzen, Sicherung](#8-betrieb-neuimport-zurücksetzen-sicherung)
9. [Fehlersuche](#9-fehlersuche)
10. [Bekannte Einschränkungen und Ideen](#10-bekannte-einschränkungen-und-ideen)
11. [Entwicklung und Branch-Workflow](#11-entwicklung-und-branch-workflow)
12. [Glossar](#12-glossar)

---

## 1. Überblick und Architektur

Das Projekt besteht aus einer einzigen Python-Anwendung (`app.py`), die FIT-Dateien aus ZIP-Archiven
liest und in PostgreSQL/PostGIS schreibt. QGIS greift anschließend direkt auf die Datenbank zu.

```
 Garmin-Gerät / Garmin Connect
          │  Export (ZIP mit .fit)
          ▼
 ┌───────────────────────┐     fitdecode       ┌─────────────────────┐
 │ Quellordner mit .zip  │ ──────────────────► │ app.py / app.exe    │
 └───────────────────────┘   entpacken, lesen  │ (Tkinter-GUI)       │
                                                └──────────┬──────────┘
                                                           │ psycopg2
                                                           ▼
                                       ┌───────────────────────────────────┐
                                       │ PostgreSQL + PostGIS              │
                                       │  garmin_activities                │
                                       │  garmin_trackpoints (Points)      │
                                       │  garmin_tracks      (LineStrings) │
                                       │  Views: garmin_tracks_full,       │
                                       │         garmin_tracks_<Jahr>      │
                                       └──────────────────┬────────────────┘
                                                          │ PostGIS-Verbindung
                                                          ▼
                                                        QGIS
```

**Verwendete Bibliotheken**

| Bibliothek | Zweck |
|---|---|
| `fitdecode` | Liest das binäre Garmin-FIT-Format (reines Python, ersetzt das frühere `fitparse`) |
| `psycopg2` | Verbindung zu PostgreSQL, Massen-Insert mit `execute_values` |
| `tkinter` | Grafische Oberfläche (Teil der Python-Standardbibliothek) |
| `zipfile`, `tempfile`, `os` | Entpacken und Durchsuchen der Quellordner |
| PyInstaller | Verpackt die App zu einer einzelnen `app.exe` |

---

## 2. Importablauf im Detail

Der Import startet mit **Import starten** in der Oberfläche (`App.start`) und läuft in dieser Reihenfolge:

1. **Eingaben prüfen.** Ohne Quellordner erscheint eine Fehlermeldung. Die Datenbankparameter werden
   zusammengesetzt; das gewählte Schema wird über `-c search_path=<Schema>` gesetzt, sodass alle
   Tabellen und Views in diesem Schema landen.
2. **Verbindung aufbauen** (`process_main_folder`). Schlägt sie fehl, wird das protokolliert und der Import beendet.
3. **Tabellen anlegen** (`ensure_tables`): Dreimal `CREATE TABLE IF NOT EXISTS` plus Indizes.
   Bestehende Daten bleiben unverändert.
4. **Ordner durchsuchen.** Rekursiv werden alle Dateien mit Endung `.zip` (Groß-/Kleinschreibung egal) gesucht.
5. **Pro ZIP** (`process_zip`): Das Archiv wird in ein temporäres Verzeichnis entpackt, darin werden alle
   `.fit`-Dateien gefunden. Das Verzeichnis wird danach automatisch gelöscht.
6. **Pro FIT-Datei** (`fit_to_postgres`):
   1. Session-Daten lesen (`extract_session_info`).
   2. Alle GPS-Punkte lesen (`extract_trackpoints`). Ohne GPS-Punkte wird die Datei mit `⚠` übersprungen
      (z. B. Indoor-Aktivitäten).
   3. **Duplikatprüfung** (`activity_exists`): gleicher Dateiname und gleiche Startzeit → Übersprungen mit `↷`.
   4. Aktivität einfügen (`insert_activity`), Punkte einfügen (`insert_trackpoints`),
      Linie erzeugen (`insert_track`).
   5. Erfolg wird mit `✓` und der vergebenen Aktivitäts-ID protokolliert.
7. **Fehlerbehandlung:** Fehler bei einer einzelnen FIT-Datei werden protokolliert (`✗`), der Import läuft mit
   der nächsten Datei weiter. Am Ende steht "Fertig!".

### Die wichtigsten Funktionen

| Funktion | Aufgabe |
|---|---|
| `semicircles_to_degrees(value)` | Rechnet Garmin-Koordinaten (Semicircles, 2³¹ entspricht 180°) in Grad um: `wert × 180 / 2³¹` |
| `extract_session_info(fit_file)` | Liest die erste `session`-Nachricht, ergänzt Zeit aus `activity` und den Namen aus `workout` |
| `extract_trackpoints(fit_file)` | Liest alle `record`-Nachrichten mit Breiten- und Längengrad |
| `ensure_tables(conn)` | Legt Tabellen und Indizes an |
| `activity_exists(...)` | Duplikatprüfung über Dateiname und Startzeit (`IS NOT DISTINCT FROM`, behandelt auch fehlende Startzeit) |
| `insert_activity(...)` | Fügt die Aktivität ein, gibt die neue ID zurück, wandelt Koordinaten der Session in Grad um |
| `insert_trackpoints(...)` | Massen-Insert aller Punkte mit `execute_values` |
| `insert_track(...)` | Baut aus den Punkten eine Linie (siehe unten) |

### Wie die Linie entsteht

`insert_track` schreibt die Punkte (Reihenfolge `ord` und Geometrie) in eine temporäre Tabelle
`_tmp_track_pts` und erzeugt daraus mit `ST_MakeLine(geom ORDER BY ord)` eine Linie, die in
`garmin_tracks` eingefügt wird. Voraussetzung sind mindestens zwei Punkte; bei weniger
entsteht keine Linie. Koordinaten der Linie werden auf 8 Nachkommastellen gerundet (ca. 1 mm),
die Einzelpunkte werden ungerundet gespeichert.

### Transaktionen

Jeder Teilschritt (`insert_activity`, `insert_trackpoints`, `insert_track`) führt ein eigenes `commit` aus.
Das bedeutet: Bricht ein Schritt ab, können Teilergebnisse stehen bleiben (z. B. eine Aktivität
ohne Punkte). Mehr dazu in [Abschnitt 10](#10-bekannte-einschränkungen-und-ideen).

---

## 3. Zuordnung FIT-Felder → Datenbank

### Session (Aktivitäts-Kennzahlen) → `garmin_activities`

| Datenbankspalte | FIT-Feld (Nachricht `session`) | Einheit / Hinweis |
|---|---|---|
| `source_file` | Dateiname der `.fit`-Datei | ohne Pfad |
| `workout_name` | `wkt_name` aus Nachricht `workout` | nur bei geplanten Workouts vorhanden |
| `sport` | `sport` | z. B. `cycling`, `hiking`, `running` |
| `sub_sport` | `sub_sport` | z. B. `mountain`, `generic` |
| `start_time` | `start_time` | Zeitstempel (von Garmin in UTC, siehe Hinweis zu Zeitzonen in Abschnitt 10) |
| `total_elapsed_time` | `total_elapsed_time` | Sekunden, inklusive Pausen |
| `total_timer_time` | `total_timer_time` | Sekunden, ohne Pausen |
| `total_distance` | `total_distance` | Meter |
| `total_ascent` / `total_descent` | `total_ascent` / `total_descent` | Meter |
| `avg_speed` / `max_speed` | `avg_speed` / `max_speed` | m/s; siehe Einschränkung zu `enhanced_*` |
| `avg_heart_rate` / `max_heart_rate` | gleichnamig | Schläge/min |
| `avg_cadence` / `max_cadence` | gleichnamig | U/min bzw. Schritte/min |
| `avg_power` / `max_power` | gleichnamig | Watt |
| `total_calories` | `total_calories` | kcal |
| `avg_temperature` / `max_temperature` | gleichnamig | °C |
| `start_position_lat` / `start_position_long` | gleichnamig | Semicircles → Grad |
| `nec_lat` / `nec_long` | gleichnamig | Nordöstliche Ecke der Aktivität (Grad) |
| `swc_lat` / `swc_long` | gleichnamig | Südwestliche Ecke der Aktivität (Grad) |
| `imported_at` | – | Importzeitpunkt, automatisch |

`sport` und `sub_sport` werden als Text gespeichert; fehlen sie, steht ein leerer String in der Spalte.

### Record (Einzelmessungen) → `garmin_trackpoints`

| Datenbankspalte | FIT-Feld (Nachricht `record`) | Einheit |
|---|---|---|
| `timestamp` | `timestamp` | UTC (siehe Zeitzonen-Hinweis in Abschnitt 10) |
| `geom` | `position_lat`, `position_long` | Punkt in EPSG:4326 (Länge, Breite) |
| `altitude` | `altitude` | Meter |
| `heart_rate` | `heart_rate` | Schläge/min |
| `cadence` | `cadence` | U/min |
| `speed` | `speed` | m/s |
| `distance` | `distance` | Meter ab Start |
| `power` | `power` | Watt |
| `temperature` | `temperature` | °C |
| `grade` | `grade` | Prozent |
| `vertical_speed` | `vertical_speed` | m/s |

Nur Records mit Breiten- **und** Längengrad werden übernommen. Felder, die das Gerät nicht aufgezeichnet
hat (etwa Leistung ohne Powermeter), bleiben NULL.

---

## 4. Datenbank

### 4.1 Tabelle `garmin_activities`

Eine Zeile pro importierter Aktivität (`id` ist der Primärschlüssel).

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | SERIAL, Primärschlüssel | Interne Aktivitäts-ID |
| `source_file` | TEXT | Name der FIT-Datei |
| `workout_name` | TEXT | Name eines Workouts, falls vorhanden |
| `sport` | TEXT | Sportart |
| `sub_sport` | TEXT | Unterart |
| `start_time` | TIMESTAMPTZ | Startzeit |
| `total_elapsed_time` | FLOAT | Gesamtdauer in Sekunden |
| `total_timer_time` | FLOAT | Dauer ohne Pausen in Sekunden |
| `total_distance` | FLOAT | Distanz in Metern |
| `total_ascent` | INTEGER | Aufstieg in Metern |
| `total_descent` | INTEGER | Abstieg in Metern |
| `avg_speed`, `max_speed` | FLOAT | Geschwindigkeit in m/s |
| `avg_heart_rate`, `max_heart_rate` | INTEGER | Herzfrequenz |
| `avg_cadence`, `max_cadence` | INTEGER | Kadenz |
| `avg_power`, `max_power` | INTEGER | Leistung in Watt |
| `total_calories` | INTEGER | Kalorien |
| `avg_temperature`, `max_temperature` | FLOAT | Temperatur in °C |
| `start_position_lat`, `start_position_long` | FLOAT | Startposition in Grad |
| `nec_lat`, `nec_long`, `swc_lat`, `swc_long` | FLOAT | Eckpunkte des Begrenzungsrechtecks in Grad |
| `imported_at` | TIMESTAMPTZ | Standard `NOW()` |

### 4.2 Tabelle `garmin_trackpoints`

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | SERIAL, Primärschlüssel | Punkt-ID |
| `activity_id` | INTEGER | Fremdschlüssel auf `garmin_activities(id)`, `ON DELETE CASCADE` |
| `timestamp` | TIMESTAMPTZ | Messzeitpunkt |
| `geom` | `geometry(Point, 4326)` | Position |
| `altitude`, `speed`, `distance`, `temperature`, `grade`, `vertical_speed` | FLOAT | Messwerte |
| `heart_rate`, `cadence`, `power` | INTEGER | Messwerte |

Indizes: räumlicher GIST-Index `idx_trackpoints_geom` auf `geom` und `idx_trackpoints_activity` auf `activity_id`.

### 4.3 Tabelle `garmin_tracks`

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | SERIAL, Primärschlüssel | Track-ID |
| `activity_id` | INTEGER | Fremdschlüssel auf `garmin_activities(id)`, `ON DELETE CASCADE` |
| `geom` | `geometry(LineString, 4326)` | Zusammenhängende Linie der Aktivität |

Indizes: `idx_tracks_geom` (GIST) und `idx_tracks_activity`.

### 4.4 Beziehungen

```
garmin_activities (1) ──< garmin_trackpoints (n)
garmin_activities (1) ──< garmin_tracks      (1 je Aktivität)
```

Wird eine Aktivität gelöscht (`DELETE FROM garmin_activities WHERE id = …`), verschwinden durch
`ON DELETE CASCADE` automatisch ihre Punkte und ihre Linie.

---

## 5. SQL-Views

Die Datei `SQL_VIEW.sql` hat drei Abschnitte. Sie wird **nicht** von der App ausgeführt, sondern von Hand in der Datenbank.

| Abschnitt | Wann ausführen | Inhalt |
|---|---|---|
| "Nach Drop" | Einmal pro neuer Datenbank bzw. nach einem Löschen | `CREATE EXTENSION IF NOT EXISTS postgis;` |
| "Nach Import" | Nach dem ersten Import | View `garmin_tracks_full` |
| "Nach 1. View" | Nach jedem Import | Jahres-Views `garmin_tracks_<Jahr>` |

**`garmin_tracks_full`** verbindet jede Linie (`garmin_tracks`) per JOIN mit ihren Aktivitätsdaten
(`garmin_activities`). Es enthält `id`, `activity_id`, `geom` sowie Sportart, Startzeit, Distanz,
Auf-/Abstieg, Geschwindigkeit, Herzfrequenz, Kalorien, Temperatur und Quelldatei.

**Jahres-Views** (`garmin_tracks_2024`, `garmin_tracks_2025`, …) filtern `garmin_tracks_full` nach dem
Jahr von `start_time`. Der Block im Skript ermittelt die Jahre selbst aus den vorhandenen Daten
(`SELECT DISTINCT extract(year FROM start_time)`), es ist also keine Jahresgrenze mehr fest eingetragen.
Neue Jahre bekommen ihren View, sobald der Block nach einem Import erneut läuft; bereits
vorhandene Views werden dabei ersetzt (`CREATE OR REPLACE VIEW`).

Hinweis: Aktivitäten ohne `start_time` tauchen in keinem Jahres-View auf, sehr wohl aber in `garmin_tracks_full`.

---

## 6. Arbeiten mit QGIS

### Verbindung anlegen

1. *Layer → Layer hinzufügen → PostGIS-Layer hinzufügen* (oder Datenquellenverwaltung).
2. *Neu*: Name, Host, Port, Datenbank, Benutzer und Passwort eintragen. Verbindung testen.
3. Verbinden, Schema öffnen und die gewünschten Views/Tabellen auswählen.

### Welche Ebene wofür

| Ebene | Geeignet für |
|---|---|
| `garmin_tracks_full` | Alle Touren als Linien mit Attributen (Standardansicht) |
| `garmin_tracks_<Jahr>` | Ein einzelnes Jahr |
| `garmin_trackpoints` | Punktdarstellung, z. B. nach Herzfrequenz, Höhe oder Geschwindigkeit eingefärbt |
| `garmin_tracks` | Linien ohne Attribute (selten nötig) |

### Tipps

- **Eindeutige Spalte bei Views:** QGIS fragt beim Laden eines Views nach dem Primärschlüssel; `id` wählen.
- **Kategorisierter Stil:** Spalte `sport` verwenden, um Sportarten unterschiedlich einzufärben.
- **Filter:** Mit dem Abfragegenerator (Layer-Eigenschaften → Quelle) lassen sich Zeiträume oder Sportarten
  filtern, z. B. `"start_time" >= '2026-01-01'` oder `"sport" = 'cycling'`.
- **Beschriftung:** Über `start_time`, `total_distance` oder `source_file` lassen sich Touren beschriften.
- **Maßeinheiten:** `total_distance` ist in Metern, `avg_speed` in m/s (Umrechnung in km/h: Wert × 3,6).
- **Aktualisieren:** Nach einem neuen Import den Layer in QGIS neu laden (F5).

---

## 7. Build und Continuous Integration

### Lokal bauen (Windows)

```bash
pip install -r requirements.txt
pyinstaller app.spec --noconfirm
```

Die Ergebnisdatei liegt in `dist/app.exe`. `app.spec` erzeugt eine **Einzeldatei** ohne Konsolenfenster
(`console=False`). UPX-Komprimierung ist in der Spec aktiviert, wirkt aber nur, wenn UPX auf dem Build-Rechner installiert ist.

### GitHub-Actions-Workflow `Build app.exe`

Datei: `.github/workflows/build-exe.yml`

| Eigenschaft | Wert |
|---|---|
| Runner | `windows-latest` mit Python 3.12 |
| Auslöser | Push auf `main` oder `prod`, wenn `app.py`, `app.spec`, `requirements.txt` oder der Workflow geändert wurde; manueller Start (`workflow_dispatch`) |
| Schritte | Checkout → Python einrichten → Abhängigkeiten installieren → PyInstaller → Artifact hochladen → exe zurückcommitten |
| Ergebnis 1 | Artifact `app-exe` am Workflow-Lauf (Download als ZIP) |
| Ergebnis 2 | `app.exe` im Hauptordner des Repositories, Commit "Build app.exe [skip ci]" |
| Berechtigung | `contents: write` (damit der Bot pushen darf) |

**Wichtig**

- `[skip ci]` im Commit verhindert, dass der Bot-Commit den Workflow erneut startet.
- Nach einem Build liegt ein zusätzlicher Commit auf dem Branch. Vor dem eigenen Push zuerst `git pull`.
- Ist für `main` ein Branch-Schutz aktiv, darf der Bot dort eventuell nicht pushen.
- Die manuelle Schaltfläche *Run workflow* erscheint erst, wenn die Workflow-Datei auf dem Standard-Branch liegt.
- `build/` und `dist/` gehören nicht ins Repository (`.gitignore`), sie sind reine Build-Zwischenstände.

### Typische Probleme beim Build

| Problem | Lösung |
|---|---|
| Commit-Schritt scheitert mit 403 | *Settings → Actions → General → Workflow permissions* auf "Read and write" |
| exe fehlt nach dem Lauf | Prüfen, ob die geänderten Dateien zum Pfadfilter passen; sonst manuell starten |
| Windows warnt vor der exe | Normal bei unsignierter, frisch gebauter exe; bewusst zulassen |

---

## 8. Betrieb: Neuimport, Zurücksetzen, Sicherung

### Sicherung

Vor jeder größeren Änderung die Datenbank sichern, z. B.:

```bash
pg_dump -Fc -h localhost -U benutzer -d datenbank -t garmin_activities -t garmin_trackpoints -t garmin_tracks -f garmin_backup.dump
```

### Einzelne Aktivität löschen

```sql
DELETE FROM garmin_activities WHERE id = 123;   -- Punkte und Linie werden mitgelöscht
```

### Alles zurücksetzen und neu importieren

```sql
DROP TABLE IF EXISTS garmin_tracks, garmin_trackpoints, garmin_activities CASCADE;
```

`CASCADE` entfernt auch die abhängigen Views. Danach App starten (legt die Tabellen neu an), importieren
und die Views aus `SQL_VIEW.sql` neu anlegen.

### Bestehende Duplikate finden

```sql
SELECT source_file, start_time, count(*)
FROM garmin_activities
GROUP BY source_file, start_time
HAVING count(*) > 1;
```

Doppelte Zeilen lassen sich anschließend über die kleinere `id` löschen (vorher sichern).

---

## 9. Fehlersuche

| Meldung / Symptom | Wahrscheinliche Ursache | Abhilfe |
|---|---|---|
| `❌ DB-Verbindung fehlgeschlagen` | Host, Port, Datenbank oder Zugangsdaten falsch; Server nicht erreichbar | Daten prüfen, Firewall und `pg_hba.conf` kontrollieren, mit pgAdmin gegenprüfen |
| `type "geometry" does not exist` | PostGIS fehlt oder ist in einem anderen Schema | `CREATE EXTENSION postgis;` ausführen; Schema in der App prüfen |
| `permission denied` beim Anlegen | Benutzer darf im Schema keine Tabellen erstellen | Rechte vergeben (`GRANT CREATE ON SCHEMA …`) |
| `⚠ Keine GPS-Punkte` | Aktivität ohne GPS (z. B. Indoor-Training) | Erwartetes Verhalten, die Datei wird nicht importiert |
| `↷ Übersprungen (bereits importiert)` | Gleicher Dateiname und gleiche Startzeit liegen schon vor | Erwartetes Verhalten |
| `✗ Fehler bei <Datei>` | Beschädigte oder unerwartete FIT-Datei | Meldung im Protokoll lesen; Datei in Garmin Connect neu exportieren |
| Folgefehler nach einem `✗` (`current transaction is aborted`) | Nach einem Datenbankfehler wird die Transaktion nicht zurückgesetzt (siehe Abschnitt 10) | App neu starten und den Import wiederholen; bereits importierte Dateien werden übersprungen |
| In der App passiert scheinbar nichts | Die Oberfläche wird während des Imports nur nach jeder Protokollzeile aktualisiert | Bei großen Archiven etwas warten |
| Leere Geschwindigkeits- oder Höhenspalten | Gerät speichert `enhanced_speed` / `enhanced_altitude` | Siehe Abschnitt 10 |
| QGIS zeigt nichts | Falsche Ebene, Filter aktiv oder Layer nicht neu geladen | Ebenenquelle und Filter prüfen, Karte auf Ebene zoomen, F5 |
| QGIS fragt nach Primärschlüssel | Ebene ist ein View | Spalte `id` wählen |

Python-Aufruf mit sichtbarer Konsole zur Fehlersuche: `python app.py` (die exe zeigt keine Konsole an).

---

## 10. Bekannte Einschränkungen und Ideen

Die Punkte 1 und 2 wurden an einer Beispieldatei (Multisport-Tour am 26.09.2026 mit Mountainbike, Wandern
und Bergsteigen) beobachtet.

### 10.1 Multisport-Aktivitäten (nur erste Session)

*Beobachtung:* Die Beispieldatei enthält 9 Sessions: Mountainbike, Übergang, Wandern, Übergang, Bergsteigen, Übergang,
Wandern, Übergang, Mountainbike. `extract_session_info` liest nur die erste. Die Datenbank erhält eine
Aktivität "cycling / mountain" mit den Kennzahlen der ersten Etappe (7,7 km), der Track enthält aber alle
11.042 Punkte der gesamten Tour. Die Wanderetappen und die zweite Radetappe fehlen in den Kennzahlen.

*Idee:* Jede Session als eigene Aktivität mit eigenem Track speichern. Die Punkte werden anhand der
Start- und Endzeiten der Sessions zugeordnet. Übergangs-Sessions (`transition`, nur Meter lang) könnte man
weglassen oder mitspeichern. Das erfordert Änderungen am Datenmodell (z. B. eine Spalte für die
Session-Nummer) und einen Neuimport bzw. `ALTER TABLE`.

### 10.2 `enhanced_speed` und `enhanced_altitude`

*Beobachtung:* Die Datei speichert die Werte als `enhanced_speed`, `enhanced_altitude`,
`enhanced_avg_speed` und `enhanced_max_speed`. Die App liest `speed`, `altitude`, `avg_speed` und `max_speed`.
Deshalb sind diese Spalten bei solchen Dateien leer, Distanz, Herzfrequenz und Kadenz sind
davon nicht betroffen.

*Idee:* Beim Lesen zuerst die `enhanced_*`-Felder nehmen und auf die klassischen Felder zurückfallen.

### 10.3 Aktivitätsname

Der in Garmin Connect vergebene Titel steht nicht in der FIT-Datei. Vorhanden ist der Name des
Aktivitätsprofils (z. B. "Mountainbike", "Wandern", "Bergsteigen") in der Nachricht `sport` (Feld `name`) und
in der Session (von fitparse als `unknown_110` angezeigt). Die Zahl im Dateinamen (z. B. `24505428629`)
ist vermutlich die Garmin-Connect-Aktivitäts-ID.

*Idee:* Spalten `sport_name` und `garmin_activity_id` ergänzen. Die ID eignet sich auch als verlässlicherer
Schlüssel für den Duplikat-Schutz als Dateiname plus Startzeit.

### 10.4 Weitere Punkte

| Thema | Beschreibung |
|---|---|
| Transaktionen | `insert_activity`, `insert_trackpoints` und `insert_track` committen einzeln. Bei einem Fehler mitten im Ablauf kann eine Aktivität ohne Punkte oder Linie übrig bleiben, und nach einem Datenbankfehler wird kein `rollback` ausgeführt. Besser: alle drei Schritte in einer Transaktion mit `rollback` bei Fehlern. |
| `start_time`-Fallback | Der Fallback in `extract_session_info` setzt bei fehlender `start_time` nur das Feld `timestamp` (aus `activity.local_timestamp`), nicht `start_time`. Die Aktivität hätte dann keine Startzeit und würde in keinem Jahres-View erscheinen. |
| Nur ZIP | Lose `.fit`-Dateien werden nicht gelesen. |
| Zeitzonen | Garmin speichert Zeitstempel in UTC, fitparse liefert sie ohne Zeitzonenangabe. Beim Schreiben in die `TIMESTAMPTZ`-Spalten deutet PostgreSQL sie in der Zeitzone der Datenbank-Sitzung. Ist diese nicht UTC, sind die gespeicherten Zeiten verschoben (nicht geprüft). Dasselbe gilt für die Jahresgrenze der Jahres-Views, die Lokalzeit kann um Stunden abweichen. |
| Weitere Daten | Die Dateien enthalten mehr, was bisher nicht gelesen wird: Runden (Laps), Trainingseffekt, Herzfrequenzvariabilität, Geräte- und Sensorinformationen. |
| Persönliche Daten | Das Benutzerprofil in der FIT-Datei (Gewicht, Größe, Ruhepuls, Name) wird bewusst nicht gespeichert. |
| Oberfläche | Der Import läuft im Hauptthread der GUI; bei sehr großen Datenmengen wirkt das Fenster träge. |

---

## 11. Entwicklung und Branch-Workflow

### Lokale Entwicklung

```bash
git clone https://github.com/ungnurli/Garmin2QGIS.git
cd Garmin2QGIS
python -m venv .venv
.venv\Scripts\activate          # Windows; unter Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Tkinter gehört zur Standard-Python-Installation (unter manchen Linux-Distributionen separat: `python3-tk`).

### Branches

| Branch | Zweck |
|---|---|
| `main` | Stabiler Stand, von dem die Nutzung ausgeht |
| `prod` | Arbeitsbranch für Änderungen; wird per Pull Request oder Fast-Forward nach `main` übernommen |

Empfehlung: Änderungen auf einem eigenen Branch entwickeln, den Workflow die exe bauen lassen,
die exe testen und erst dann nach `main` übernehmen.

Vor dem Pushen immer zuerst:

```bash
git pull
```

weil der Workflow Bot-Commits ("Build app.exe [skip ci]") auf den Branch schreibt.

### Namensänderung des Repositorys

Das Repository hieß früher `Garmin2QGIS_2`. Alte Adressen werden von GitHub weitergeleitet. Wer noch
mit der alten Adresse arbeitet, setzt die neue so:

```bash
git remote set-url origin https://github.com/ungnurli/Garmin2QGIS.git
```

---

## 12. Glossar

| Begriff | Bedeutung |
|---|---|
| FIT | "Flexible and Interoperable Data Transfer", das binäre Dateiformat von Garmin für Aktivitäten |
| Session | Abschnitt einer Aktivität in einer FIT-Datei mit eigenen Kennzahlen (bei Multisport mehrere) |
| Record | Einzelne Messung (Zeit, Position, Herzfrequenz usw.) in einer FIT-Datei |
| Semicircles | Garmin-Koordinatenformat; 2³¹ Semicircles entsprechen 180° |
| PostGIS | Räumliche Erweiterung für PostgreSQL (Geometrien, räumliche Indizes und Abfragen) |
| EPSG:4326 | WGS84, geografische Koordinaten (Länge/Breite in Grad) |
| GIST | Räumlicher Index-Typ in PostgreSQL |
| PyInstaller | Werkzeug, das ein Python-Programm zu einer eigenständigen exe verpackt |
| Artifact | Von GitHub Actions bereitgestellte Ergebnisdatei eines Workflow-Laufs |
