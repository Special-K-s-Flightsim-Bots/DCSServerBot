DELETE FROM bg_geometry2 WHERE server_name NOT IN (SELECT server_name FROM servers);
ALTER TABLE bg_geometry2 ADD CONSTRAINT bg_geometry2_server_name_fkey FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE;
DELETE FROM bg_missions WHERE server_name NOT IN (SELECT server_name FROM servers);
ALTER TABLE bg_missions ADD CONSTRAINT bg_missions_server_name_fkey FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE;
DELETE FROM bg_task WHERE server_name NOT IN (SELECT server_name FROM servers);
ALTER TABLE bg_task ADD CONSTRAINT bg_task_server_name_fkey FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE;
DELETE FROM bg_task_user_rltn WHERE id_task NOT IN (SELECT id_task FROM bg_task);
ALTER TABLE bg_task_user_rltn ADD CONSTRAINT bg_task_user_rltn_id_task_fkey FOREIGN KEY (id_task) REFERENCES bg_task (id) ON DELETE CASCADE;
