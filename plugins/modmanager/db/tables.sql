CREATE TABLE IF NOT EXISTS mm_packages (
    node TEXT,
    server_name TEXT,
    package_name TEXT NOT NULL,
    version TEXT NOT NULL,
    folder TEXT NOT NULL,
    time TIMESTAMP DEFAULT NOW(),
    UNIQUE (node, package_name),
    UNIQUE (server_name, package_name),
    FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE
);
