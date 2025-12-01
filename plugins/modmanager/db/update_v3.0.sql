DELETE FROM mm_packages WHERE server_name NOT IN (SELECT server_name FROM servers);
ALTER TABLE mm_packages ADD CONSTRAINT mm_packages_server_name_fkey FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE;
