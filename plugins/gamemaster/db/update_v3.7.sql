-- "Which campaigns belong to these servers" reads campaigns_servers by server_name; the primary key (campaign_id, server_name) only serves that order.
CREATE INDEX IF NOT EXISTS idx_campaigns_servers_server ON campaigns_servers (server_name);
