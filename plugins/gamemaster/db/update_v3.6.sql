DELETE FROM campaigns_servers WHERE campaign_id NOT IN (SELECT id FROM campaigns);
DELETE FROM campaigns_servers WHERE server_name NOT IN (SELECT server_name FROM servers);
ALTER TABLE campaigns_servers ADD CONSTRAINT campaign_servers_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE;
ALTER TABLE campaigns_servers ADD CONSTRAINT campaign_servers_server_name_fkey FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE;
DELETE FROM coalitions WHERE player_ucid NOT IN (SELECT ucid FROM players);
ALTER TABLE coalitions ADD CONSTRAINT coalitions_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
DELETE FROM messages WHERE player_ucid NOT IN (SELECT ucid FROM players);
ALTER TABLE messages ADD CONSTRAINT messages_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
