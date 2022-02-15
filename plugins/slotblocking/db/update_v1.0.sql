UPDATE plugins SET version = 'v1.1' WHERE plugin = 'slotblocking';
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_server_name ON campaigns (server_name);
