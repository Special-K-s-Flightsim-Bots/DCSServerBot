DELETE FROM bg_geometry WHERE server NOT IN (select server_name FROM servers);
ALTER TABLE bg_geometry ADD CONSTRAINT bg_geometry_server_name_fkey FOREIGN KEY (server) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE;
