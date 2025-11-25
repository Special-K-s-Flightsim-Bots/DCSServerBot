DELETE FROM serverstats WHERE server_name NOT IN (SELECT server_name FROM servers);
ALTER TABLE serverstats ADD CONSTRAINT serverstats_server_name_fkey FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE;
