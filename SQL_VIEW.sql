
--------------------------------Nach Drop
CREATE EXTENSION IF NOT EXISTS postgis;


-------------------------------Nach Import
CREATE OR REPLACE VIEW garmin_tracks_full AS
SELECT
    t.id,
    t.activity_id,
    t.geom,
    a.sport,
    a.sub_sport,
    a.workout_name,
    a.start_time,
    a.total_distance,
    a.total_elapsed_time,
    a.total_timer_time,
    a.total_ascent,
    a.total_descent,
    a.avg_speed,
    a.max_speed,
    a.avg_heart_rate,
    a.max_heart_rate,
    a.total_calories,
    a.avg_temperature,
    a.source_file
FROM garmin_tracks t
JOIN garmin_activities a ON t.activity_id = a.id;


-----------------------------------Nach 1. View
-- Jahres-Views: je ein View pro Jahr, das in den Daten vorkommt.
-- Kein hartkodierter Bereich mehr; nach jedem Import erneut ausführen,
-- damit neue Jahre automatisch einen View bekommen.
DO '
DECLARE
    y INT;
BEGIN
    FOR y IN
        SELECT DISTINCT extract(year FROM start_time)::int
        FROM garmin_activities
        WHERE start_time IS NOT NULL
        ORDER BY 1
    LOOP
        EXECUTE ''CREATE OR REPLACE VIEW garmin_tracks_'' || y || '' AS SELECT * FROM garmin_tracks_full WHERE extract(year FROM start_time) = '' || y;
    END LOOP;
END;
'
