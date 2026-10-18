# Garmin2QGIS

Importiert Garmin-Aktivitäten (FIT-Dateien) in eine PostgreSQL/PostGIS-Datenbank,
damit sie in QGIS dargestellt und ausgewertet werden können.
Verwendet [fitparse](https://github.com/dtcooper/python-fitparse) und psycopg2.

## Ablauf

1. **Datenbank vorbereiten** (einmalig, siehe `SQL_VIEW.sql`, Abschnitt "Nach Drop"):
   `CREATE EXTENSION IF NOT EXISTS postgis;`
2. **Import**: `app.py` starten (oder `app.exe`), Quellordner mit den ZIP-Dateien
   (Garmin-Export, enthalten `.fit`-Dateien) und DB-Zugangsdaten eintragen,
   "Import starten" klicken. Die Tabellen werden automatisch angelegt.
3. **Views erstellen**: `SQL_VIEW.sql` ab "Nach Import" ausführen
   (`garmin_tracks_full` und je ein View pro Jahr, z. B. `garmin_tracks_2025`).
   Nach weiteren Importen den Jahres-View-Block erneut ausführen, damit neue Jahre
   einen View bekommen.
4. **QGIS**: Views bzw. Tabellen über die PostGIS-Verbindung laden.

## Datenmodell

| Tabelle | Inhalt |
|---|---|
| `garmin_activities` | Metadaten pro Aktivität (Sportart, Start, Distanz, Auf-/Abstieg, Geschwindigkeit, Herzfrequenz, Kadenz, Leistung, Kalorien, Temperatur, Quelldatei) |
| `garmin_trackpoints` | Einzelne GPS-Punkte (Point, EPSG:4326) mit Höhe, Herzfrequenz, Kadenz, Geschwindigkeit, Distanz, Leistung, Temperatur, Steigung, Vertikalgeschwindigkeit |
| `garmin_tracks` | Eine Linie (LineString, EPSG:4326) pro Aktivität |

Views: `garmin_tracks_full` (Tracks + Aktivitätsattribute) und `garmin_tracks_<Jahr>`.

## Wiederholter Import

Bereits importierte Aktivitäten (gleicher Dateiname und gleiche Startzeit) werden
übersprungen, ein erneuter Import derselben ZIP-Dateien erzeugt also keine Duplikate.

## Build

`app.exe` wird mit PyInstaller aus `app.spec` gebaut (unter Windows).
