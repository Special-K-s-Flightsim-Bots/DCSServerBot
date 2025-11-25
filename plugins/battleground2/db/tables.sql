CREATE SEQUENCE IF NOT EXISTS bg_geometry_id_seq INCREMENT 1 START 20000 MINVALUE 1 MAXVALUE 9223372036854775807 CACHE 1;
CREATE SEQUENCE IF NOT EXISTS bg_task_user_rltn_id_seq INCREMENT 1 START 1 MINVALUE 1 MAXVALUE 2147483647 CACHE 1;
CREATE TABLE IF NOT EXISTS bg_geometry2 (
    id SERIAL PRIMARY KEY,
    server_name text NOT NULL,
    data json,
    "time" timestamp without time zone DEFAULT timezone('utc'::text, now()),
    FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS bg_missions (
    id SERIAL PRIMARY KEY,
    server_name text NOT NULL,
    "time" timestamp without time zone NOT NULL DEFAULT timezone('utc'::text, now()),
    data json,
    FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS bg_task (
    id SERIAL PRIMARY KEY,
    id_mission integer NOT NULL,
    server_name text NOT NULL,
    "time" timestamp without time zone NOT NULL DEFAULT timezone('utc'::text, now()),
    data json,
    FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS bg_task_user_rltn (
    id SERIAL PRIMARY KEY,
    id_task integer NOT NULL,
    discord_id bigint NOT NULL,
    "time" timestamp without time zone NOT NULL DEFAULT timezone('utc'::text, now()),
    FOREIGN KEY (id_task) REFERENCES bg_task (id) ON DELETE CASCADE
);
