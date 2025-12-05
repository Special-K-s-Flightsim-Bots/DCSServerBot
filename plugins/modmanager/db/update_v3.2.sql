CREATE TABLE IF NOT EXISTS mm_packages_new (
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
INSERT INTO mm_packages_new (server_name, package_name, version, folder, time)
    SELECT server_name, package_name, version, folder, time
    FROM mm_packages
    WHERE folder = 'SavedGames';
INSERT INTO mm_packages_new (node, package_name, version, folder, time)
    SELECT server_name, package_name, version, folder, time
    FROM mm_packages
    WHERE folder = 'RootFolder';
DROP TABLE mm_packages;
ALTER TABLE mm_packages_new RENAME TO mm_packages;
