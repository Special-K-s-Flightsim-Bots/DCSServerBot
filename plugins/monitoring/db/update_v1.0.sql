ALTER TABLE serverstats ADD COLUMN IF NOT EXISTS agent_host TEXT;
UPDATE serverstats s SET agent_host = (SELECT agent_host FROM servers WHERE server_name = s.server_name)
