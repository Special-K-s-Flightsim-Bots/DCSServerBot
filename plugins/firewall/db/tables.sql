CREATE TABLE IF NOT EXISTS port_traffic (
    id SERIAL PRIMARY KEY,
    node TEXT NOT NULL,
    server_name TEXT NOT NULL,
    port INTEGER NOT NULL,
    protocol TEXT NOT NULL,
    bytes_in BIGINT NOT NULL DEFAULT 0,
    bytes_out BIGINT NOT NULL DEFAULT 0,
    packets_in BIGINT NOT NULL DEFAULT 0,
    packets_out BIGINT NOT NULL DEFAULT 0,
    unique_ips INTEGER NOT NULL DEFAULT 0,
    non_player_udp_ips INTEGER NOT NULL DEFAULT 0,
    connections INTEGER NOT NULL DEFAULT 0,
    players INTEGER NOT NULL DEFAULT 0,
    under_attack BOOLEAN NOT NULL DEFAULT FALSE,
    time TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);
CREATE INDEX IF NOT EXISTS idx_port_traffic_server_port ON port_traffic(server_name, port);
CREATE INDEX IF NOT EXISTS idx_port_traffic_time ON port_traffic(time);
CREATE INDEX IF NOT EXISTS idx_port_traffic_under_attack ON port_traffic(under_attack);
