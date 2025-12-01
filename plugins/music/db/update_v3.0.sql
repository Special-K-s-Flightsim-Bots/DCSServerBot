DELETE FROM music_radios WHERE server_name NOT IN (SELECT server_name FROM servers);
ALTER TABLE music_radios ADD CONSTRAINT music_radios_server_name_fkey FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE;
