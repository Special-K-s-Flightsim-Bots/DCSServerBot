UPDATE plugins SET version = 'v1.2' WHERE plugin = 'userstats';
UPDATE statistics SET kills_planes = kills_planes + pvp, deaths_planes = deaths_planes + deaths_pvp;
