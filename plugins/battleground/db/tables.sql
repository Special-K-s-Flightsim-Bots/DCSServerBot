CREATE TABLE IF NOT EXISTS bg_geometry(
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT,
    posmgrs TEXT,
    screenshot TEXT[] DEFAULT '{}'::TEXT[],
    side TEXT NOT NULL,
    server TEXT NOT NULL,
    "position" NUMERIC[] DEFAULT '{}'::NUMERIC[],
    points NUMERIC[] DEFAULT '{}'::NUMERIC[],
    center NUMERIC[] DEFAULT '{}'::NUMERIC[],
    radius NUMERIC DEFAULT 0,
    discordname TEXT NOT NULL,
    avatar TEXT NOT NULL,
    FOREIGN KEY (server) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "bg_geometry$server_side" ON bg_geometry USING btree (server ASC, side ASC);
CREATE SEQUENCE IF NOT EXISTS bg_geometry_id_seq INCREMENT 1 START 20000 MINVALUE 1 OWNED BY bg_geometry."id";
