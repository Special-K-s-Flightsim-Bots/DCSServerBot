CREATE TABLE IF NOT EXISTS mm_packages (
    server_name TEXT NOT NULL,
    package_name TEXT NOT NULL,
    version TEXT NOT NULL,
    folder TEXT NOT NULL,
    time TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY(server_name, package_name)
);
