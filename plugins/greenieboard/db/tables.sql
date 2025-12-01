CREATE TABLE IF NOT EXISTS traps (
    id SERIAL PRIMARY KEY,
    mission_id INTEGER NOT NULL,
    player_ucid TEXT NOT NULL,
    unit_type TEXT NOT NULL,
    grade TEXT NOT NULL,
    comment TEXT NOT NULL,
    place TEXT NOT NULL,
    trapcase INTEGER NOT NULL,
    wire INTEGER,
    night BOOLEAN NOT NULL,
    points DECIMAL,
    trapsheet BYTEA,
    time TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    FOREIGN KEY (mission_id) REFERENCES missions (id) ON DELETE CASCADE,
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_traps_ucid ON traps(player_ucid);
INSERT INTO traps (mission_id, player_ucid, unit_type, grade, comment, place, trapcase, wire, night, points, time)
SELECT mission_id, init_id, init_type, grade, comment, place, 1, wire, FALSE,
       CASE WHEN grade = '_OK_' THEN 5
            WHEN grade = 'OK' THEN 4
            WHEN grade = '(OK)' THEN 3
            WHEN grade = 'B' THEN 2.5
            WHEN grade IN('--', 'OWO', 'WOP') THEN 2
            WHEN grade IN ('WO', 'LIG') THEN 1
            WHEN grade = 'C' THEN 0 END AS points,
       time FROM (
            SELECT mission_id, init_id, init_type,
               REPLACE(SUBSTRING(comment, 'LSO: GRADE:([_\(\)-BCKOW]{1,4})'), '---', '--') AS grade,
               REGEXP_REPLACE(TRIM(REGEXP_REPLACE(comment, 'LSO: GRADE:.*:', '')), 'WIRE# [1234]', '') as comment,
               place, substring(comment FROM NULLIF(position('WIRE' IN comment), 0) + 6 FOR 1)::INTEGER as wire, time
            FROM missionstats WHERE event LIKE '%QUALITY%' AND init_type IS NOT NULL
       ) AS landings;
