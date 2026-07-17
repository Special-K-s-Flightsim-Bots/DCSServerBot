CREATE TABLE IF NOT EXISTS charity_donations (
    campaign_id TEXT NOT NULL,
    donation_id TEXT NOT NULL,
    amount NUMERIC(10, 2),
    name TEXT,
    message TEXT,
    created_at TIMESTAMP,
    PRIMARY KEY (campaign_id, donation_id)
);
