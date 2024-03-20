ALTER TABLE bans_hist DROP CONSTRAINT bans_hist_pkey;
ALTER TABLE bans_hist ADD PRIMARY KEY (ucid, banned_at);