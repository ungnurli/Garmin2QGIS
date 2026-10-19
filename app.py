import os
import zipfile
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox

import fitdecode
import psycopg2
from psycopg2.extras import execute_values


def semicircles_to_degrees(value):
    return value * (180 / 2**31)


def _fields_to_dict(frame):
    """Wandelt eine FIT-Datennachricht in ein Dict {Feldname: Wert} um."""
    return {field.name: field.value for field in frame.fields}


def _record_to_point(data):
    """Baut aus einer 'record'-Nachricht einen GPS-Punkt (None ohne Position)."""
    lat = data.get("position_lat")
    lon = data.get("position_long")
    if lat is None or lon is None:
        return None
    return {
        "lat": semicircles_to_degrees(lat),
        "lon": semicircles_to_degrees(lon),
        "timestamp":        data.get("timestamp"),
        "altitude":         data.get("altitude"),
        "heart_rate":       data.get("heart_rate"),
        "cadence":          data.get("cadence"),
        "speed":            data.get("speed"),           # m/s
        "distance":         data.get("distance"),        # m
        "power":            data.get("power"),           # Watt
        "temperature":      data.get("temperature"),     # °C
        "grade":            data.get("grade"),           # %
        "vertical_speed":   data.get("vertical_speed"),  # m/s
    }


def parse_fit(fit_path):
    """
    Liest eine FIT-Datei in einem Durchgang mit fitdecode.

    Gibt (session, points) zurück:
      session - Felder der ersten 'session'-Nachricht (Aktivitäts-Metadaten),
                ergänzt um 'workout_name' (aus 'workout') und ggf. 'timestamp'
      points  - alle Records mit GPS-Position als Liste von Dicts

    CRC-Prüfung und Fehlerbehandlung sind strikt (Fehler werden ausgelöst),
    damit beschädigte Dateien im Protokoll als Fehler erscheinen.
    Zeitstempel sind von fitdecode zeitzonenbehaftet (UTC).
    """
    session = {}
    points = []
    local_timestamp = None
    workout_name = None

    with fitdecode.FitReader(
        fit_path,
        check_crc=fitdecode.CrcCheck.RAISE,
        error_handling=fitdecode.ErrorHandling.RAISE,
    ) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue

            if frame.name == "record":
                point = _record_to_point(_fields_to_dict(frame))
                if point is not None:
                    points.append(point)
            elif frame.name == "session":
                if not session:  # nur erste Session
                    session = _fields_to_dict(frame)
            elif frame.name == "activity":
                if local_timestamp is None:
                    local_timestamp = frame.get_value("local_timestamp", fallback=None)
            elif frame.name == "workout":
                if workout_name is None:
                    workout_name = frame.get_value("wkt_name", fallback=None)

    # Fallback für die Zeit aus der 'activity'-Nachricht
    if "timestamp" not in session and local_timestamp is not None:
        session["timestamp"] = local_timestamp

    # Workout-Name aus der 'workout'-Nachricht (falls vorhanden)
    if workout_name is not None:
        session["workout_name"] = workout_name

    return session, points


def ensure_tables(conn):
    """Erstellt die zwei Tabellen falls sie nicht existieren."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS garmin_activities (
                id                  SERIAL PRIMARY KEY,
                source_file         TEXT,
                workout_name        TEXT,
                sport               TEXT,
                sub_sport           TEXT,
                start_time          TIMESTAMPTZ,
                total_elapsed_time  FLOAT,   -- Sekunden
                total_timer_time    FLOAT,   -- Sekunden (Pause abgezogen)
                total_distance      FLOAT,   -- Meter
                total_ascent        INTEGER, -- Meter
                total_descent       INTEGER, -- Meter
                avg_speed           FLOAT,
                max_speed           FLOAT,
                avg_heart_rate      INTEGER,
                max_heart_rate      INTEGER,
                avg_cadence         INTEGER,
                max_cadence         INTEGER,
                avg_power           INTEGER,
                max_power           INTEGER,
                total_calories      INTEGER,
                avg_temperature     FLOAT,
                max_temperature     FLOAT,
                start_position_lat  FLOAT,
                start_position_long FLOAT,
                nec_lat             FLOAT,
                nec_long            FLOAT,
                swc_lat             FLOAT,
                swc_long            FLOAT,
                imported_at         TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS garmin_trackpoints (
                id              SERIAL PRIMARY KEY,
                activity_id     INTEGER REFERENCES garmin_activities(id) ON DELETE CASCADE,
                timestamp       TIMESTAMPTZ,
                geom            geometry(Point, 4326),
                altitude        FLOAT,
                heart_rate      INTEGER,
                cadence         INTEGER,
                speed           FLOAT,
                distance        FLOAT,
                power           INTEGER,
                temperature     FLOAT,
                grade           FLOAT,
                vertical_speed  FLOAT
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_trackpoints_geom
                ON garmin_trackpoints USING GIST (geom);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_trackpoints_activity
                ON garmin_trackpoints (activity_id);
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS garmin_tracks (
                id          SERIAL PRIMARY KEY,
                activity_id INTEGER REFERENCES garmin_activities(id) ON DELETE CASCADE,
                geom        geometry(LineString, 4326)
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracks_geom
                ON garmin_tracks USING GIST (geom);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracks_activity
                ON garmin_tracks (activity_id);
        """)
    conn.commit()


def semicircles_field(session, key):
    val = session.get(key)
    if val is not None:
        return semicircles_to_degrees(val)
    return None


def insert_activity(conn, source_file, session):
    """Fügt eine Aktivität ein und gibt die neue ID zurück."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO garmin_activities (
                source_file, workout_name, sport, sub_sport,
                start_time, total_elapsed_time, total_timer_time,
                total_distance, total_ascent, total_descent,
                avg_speed, max_speed,
                avg_heart_rate, max_heart_rate,
                avg_cadence, max_cadence,
                avg_power, max_power,
                total_calories, avg_temperature, max_temperature,
                start_position_lat, start_position_long,
                nec_lat, nec_long, swc_lat, swc_long
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s
            ) RETURNING id
        """, (
            source_file,
            session.get("workout_name"),
            str(session.get("sport", "")),
            str(session.get("sub_sport", "")),
            session.get("start_time"),
            session.get("total_elapsed_time"),
            session.get("total_timer_time"),
            session.get("total_distance"),
            session.get("total_ascent"),
            session.get("total_descent"),
            session.get("avg_speed"),
            session.get("max_speed"),
            session.get("avg_heart_rate"),
            session.get("max_heart_rate"),
            session.get("avg_cadence"),
            session.get("max_cadence"),
            session.get("avg_power"),
            session.get("max_power"),
            session.get("total_calories"),
            session.get("avg_temperature"),
            session.get("max_temperature"),
            semicircles_field(session, "start_position_lat"),
            semicircles_field(session, "start_position_long"),
            semicircles_field(session, "nec_lat"),
            semicircles_field(session, "nec_long"),
            semicircles_field(session, "swc_lat"),
            semicircles_field(session, "swc_long"),
        ))
        activity_id = cur.fetchone()[0]
    conn.commit()
    return activity_id


def insert_track(conn, activity_id, points):
    """Fügt alle Punkte als eine zusammenhängende Linie ein (via temporäre Tabelle)."""
    if len(points) < 2:
        return
    with conn.cursor() as cur:
        # Punkte in temp-Tabelle schreiben
        cur.execute("CREATE TEMP TABLE IF NOT EXISTS _tmp_track_pts (ord INT, geom geometry(Point,4326)) ON COMMIT DELETE ROWS")
        execute_values(cur,
            "INSERT INTO _tmp_track_pts (ord, geom) VALUES %s",
            [(i, f"SRID=4326;POINT({round(p['lon'],8)} {round(p['lat'],8)})")
             for i, p in enumerate(points)]
        )
        # Linie aus sortierten Punkten bauen
        cur.execute("""
            INSERT INTO garmin_tracks (activity_id, geom)
            SELECT %s, ST_MakeLine(geom ORDER BY ord)
            FROM _tmp_track_pts
        """, (activity_id,))
    conn.commit()


def insert_trackpoints(conn, activity_id, points):
    if not points:
        return
    rows = [
        (
            activity_id,
            p["timestamp"],
            f"SRID=4326;POINT({p['lon']} {p['lat']})",
            p["altitude"],
            p["heart_rate"],
            p["cadence"],
            p["speed"],
            p["distance"],
            p["power"],
            p["temperature"],
            p["grade"],
            p["vertical_speed"],
        )
        for p in points
    ]
    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO garmin_trackpoints (
                activity_id, timestamp, geom,
                altitude, heart_rate, cadence,
                speed, distance, power,
                temperature, grade, vertical_speed
            ) VALUES %s
        """, rows)
    conn.commit()


def activity_exists(conn, source_file, start_time):
    """Prüft, ob die Aktivität (Dateiname + Startzeit) schon importiert wurde."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM garmin_activities
            WHERE source_file = %s
              AND start_time IS NOT DISTINCT FROM %s
            LIMIT 1
        """, (source_file, start_time))
        return cur.fetchone() is not None


def fit_to_postgres(fit_path, conn, log):
    source_file = os.path.basename(fit_path)

    session, points = parse_fit(fit_path)

    if not points:
        log(f"  ⚠ Keine GPS-Punkte: {source_file}")
        return

    if activity_exists(conn, source_file, session.get("start_time")):
        log(f"  ↷ Übersprungen (bereits importiert): {source_file}")
        return

    activity_id = insert_activity(conn, source_file, session)
    insert_trackpoints(conn, activity_id, points)
    insert_track(conn, activity_id, points)
    log(f"  ✓ {source_file} → {len(points)} Punkte + 1 Track, ID={activity_id}")


def process_zip(zip_path, conn, log):
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(tmpdir)

        for root, _, files in os.walk(tmpdir):
            for file in files:
                if file.lower().endswith(".fit"):
                    fit_path = os.path.join(root, file)
                    try:
                        fit_to_postgres(fit_path, conn, log)
                    except Exception as e:
                        log(f"  ✗ Fehler bei {file}: {e}")


def process_main_folder(main_folder, db_params, log):
    try:
        conn = psycopg2.connect(**db_params)
    except Exception as e:
        log(f"❌ DB-Verbindung fehlgeschlagen: {e}")
        return

    try:
        ensure_tables(conn)
        log("📦 Tabellen bereit.")

        for root, _, files in os.walk(main_folder):
            for file in files:
                if file.lower().endswith(".zip"):
                    zip_path = os.path.join(root, file)
                    log(f"📂 ZIP: {file}")
                    process_zip(zip_path, conn, log)

        log("✅ Fertig!")
    except Exception as e:
        log(f"❌ Fehler: {e}")
    finally:
        conn.close()


# ---------------- GUI ---------------- #

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("FIT → PostgreSQL Importer")
        self.root.resizable(False, False)

        pad = {"padx": 6, "pady": 4}

        # Quellordner
        tk.Label(root, text="Quellordner").grid(row=0, column=0, sticky="w", **pad)
        self.input_path = tk.StringVar()
        tk.Entry(root, textvariable=self.input_path, width=50).grid(row=0, column=1, **pad)
        tk.Button(root, text="…", command=self.select_input).grid(row=0, column=2, **pad)

        # DB-Verbindung
        tk.Label(root, text="── Datenbankverbindung ──").grid(
            row=1, column=0, columnspan=3, pady=(10, 2))

        fields = [
            ("Host",     "db_host",     "localhost"),
            ("Port",     "db_port",     "5432"),
            ("Datenbank","db_name",     ""),
            ("Schema",   "db_schema",   "public"),
            ("Benutzer", "db_user",     ""),
            ("Passwort", "db_password", ""),
        ]
        self.db_vars = {}
        for i, (label, key, default) in enumerate(fields, start=2):
            tk.Label(root, text=label).grid(row=i, column=0, sticky="w", **pad)
            var = tk.StringVar(value=default)
            show = "*" if key == "db_password" else ""
            tk.Entry(root, textvariable=var, width=30, show=show).grid(
                row=i, column=1, sticky="w", **pad)
            self.db_vars[key] = var

        btn_row = 2 + len(fields)
        tk.Button(root, text="▶  Import starten", command=self.start,
                  bg="#2d7d46", fg="white", font=("", 11, "bold"),
                  padx=10, pady=4).grid(row=btn_row, column=0, columnspan=3, pady=10)

        self.log_box = tk.Text(root, height=14, width=72, bg="#1e1e1e", fg="#d4d4d4",
                               font=("Courier", 10))
        self.log_box.grid(row=btn_row+1, column=0, columnspan=3, padx=6, pady=(0,8))

    def log(self, msg):
        self.log_box.insert(tk.END, msg + "\n")
        self.log_box.see(tk.END)
        self.root.update()

    def select_input(self):
        path = filedialog.askdirectory()
        if path:
            self.input_path.set(path)

    def start(self):
        if not self.input_path.get():
            messagebox.showerror("Fehler", "Bitte Quellordner wählen")
            return

        db_params = {
            "host":     self.db_vars["db_host"].get(),
            "port":     int(self.db_vars["db_port"].get() or 5432),
            "dbname":   self.db_vars["db_name"].get(),
            "user":     self.db_vars["db_user"].get(),
            "password": self.db_vars["db_password"].get(),
            "options":  f'-c search_path={self.db_vars["db_schema"].get()}',
        }

        self.log("Starte Import…")
        process_main_folder(self.input_path.get(), db_params, self.log)


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
