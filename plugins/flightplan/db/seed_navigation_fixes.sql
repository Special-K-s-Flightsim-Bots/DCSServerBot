-- Seed Navigation Fixes for Flight Plan Plugin
-- Based on real-world Georgia waypoints from Furia's template and community sources

-- Caucasus Theater - Georgia VORs and NDBs
INSERT INTO flightplan_navigation_fixes (identifier, name, fix_type, latitude, longitude, map_theater, frequency, source)
VALUES
    -- VORs
    ('TSK', 'Tskhinvali VOR', 'VOR', 42.2333, 43.9667, 'Caucasus', '117.20', 'furia'),
    ('KTS', 'Kutaisi VOR', 'VOR', 42.1833, 42.4833, 'Caucasus', '113.60', 'furia'),
    ('TBS', 'Tbilisi VOR', 'VOR', 41.6694, 44.9547, 'Caucasus', '113.70', 'furia'),
    ('BTM', 'Batumi VOR', 'VOR', 41.6103, 41.5997, 'Caucasus', '116.10', 'furia'),

    -- NDBs
    ('BA', 'Batumi NDB', 'NDB', 41.6014, 41.5889, 'Caucasus', '430', 'furia'),
    ('KT', 'Kutaisi NDB', 'NDB', 42.1761, 42.4856, 'Caucasus', '477', 'furia'),
    ('TB', 'Tbilisi NDB', 'NDB', 41.6697, 44.9536, 'Caucasus', '342', 'furia'),
    ('SN', 'Senaki NDB', 'NDB', 42.2397, 42.0475, 'Caucasus', '335', 'furia'),
    ('SO', 'Sochi NDB', 'NDB', 43.4500, 39.9500, 'Caucasus', '289', 'furia'),

    -- RNAV Waypoints - Batumi Approaches
    ('ADLER', 'Adler', 'WYP', 43.4500, 39.9167, 'Caucasus', NULL, 'furia'),
    ('GUNDI', 'Gundi', 'WYP', 41.5333, 41.6500, 'Caucasus', NULL, 'furia'),
    ('NALEN', 'Nalen', 'WYP', 41.7833, 41.4667, 'Caucasus', NULL, 'furia'),
    ('RIDAR', 'Ridar', 'WYP', 41.4333, 41.9333, 'Caucasus', NULL, 'furia'),
    ('AMTIK', 'Amtik', 'WYP', 41.6833, 41.1333, 'Caucasus', NULL, 'furia'),

    -- RNAV Waypoints - Tbilisi Approaches
    ('OBIKI', 'Obiki', 'WYP', 41.8500, 44.6667, 'Caucasus', NULL, 'furia'),
    ('NATLI', 'Natli', 'WYP', 41.4167, 44.8333, 'Caucasus', NULL, 'furia'),
    ('DAKNA', 'Dakna', 'WYP', 41.5667, 45.3333, 'Caucasus', NULL, 'furia'),
    ('MILNI', 'Milni', 'WYP', 41.7500, 45.0833, 'Caucasus', NULL, 'furia'),

    -- RNAV Waypoints - Kutaisi Approaches
    ('BALTI', 'Balti', 'WYP', 42.0833, 42.1500, 'Caucasus', NULL, 'furia'),
    ('RASKO', 'Rasko', 'WYP', 42.3500, 42.4167, 'Caucasus', NULL, 'furia'),
    ('TAKVA', 'Takva', 'WYP', 42.0167, 42.7667, 'Caucasus', NULL, 'furia'),

    -- Common Route Points
    ('ERGNI', 'Ergni', 'WYP', 42.0000, 43.0000, 'Caucasus', NULL, 'furia'),
    ('GUDTA', 'Gudauta', 'WYP', 43.1000, 40.5833, 'Caucasus', NULL, 'furia'),
    ('KOBUL', 'Kobuleti', 'WYP', 41.9333, 41.8667, 'Caucasus', NULL, 'furia'),
    ('POTI', 'Poti', 'WYP', 42.1500, 41.6833, 'Caucasus', NULL, 'furia'),
    ('SUGDI', 'Sugdidi', 'WYP', 42.5000, 41.8667, 'Caucasus', NULL, 'furia'),
    ('TSKAL', 'Tskaltbo', 'WYP', 42.3333, 42.6000, 'Caucasus', NULL, 'furia'),
    ('GORI', 'Gori', 'WYP', 41.9833, 44.1167, 'Caucasus', NULL, 'furia'),
    ('MTSKT', 'Mtskheta', 'WYP', 41.8500, 44.7167, 'Caucasus', NULL, 'furia'),
    ('RUSTS', 'Rustavi', 'WYP', 41.5500, 45.0333, 'Caucasus', NULL, 'furia'),

    -- VFR Entry/Exit Points
    ('NORTH', 'North Entry', 'WYP', 42.7500, 42.5000, 'Caucasus', NULL, 'furia'),
    ('SOUTH', 'South Entry', 'WYP', 41.2500, 42.0000, 'Caucasus', NULL, 'furia'),
    ('EAST', 'East Entry', 'WYP', 41.7500, 45.5000, 'Caucasus', NULL, 'furia'),
    ('WEST', 'West Entry', 'WYP', 42.0000, 40.5000, 'Caucasus', NULL, 'furia'),

    -- Russia/Caucasus Common
    ('MINVD', 'Mineralnye Vody', 'WYP', 44.2167, 43.0833, 'Caucasus', NULL, 'airnav'),
    ('NALCK', 'Nalchik', 'WYP', 43.5167, 43.6333, 'Caucasus', NULL, 'airnav'),
    ('BESLN', 'Beslan', 'WYP', 43.2000, 44.6167, 'Caucasus', NULL, 'airnav'),
    ('VLADI', 'Vladikavkaz', 'WYP', 43.0333, 44.6667, 'Caucasus', NULL, 'airnav')

ON CONFLICT (identifier, map_theater) DO UPDATE SET
    name = EXCLUDED.name,
    fix_type = EXCLUDED.fix_type,
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude,
    frequency = EXCLUDED.frequency,
    source = EXCLUDED.source;

-- Syria Theater - Common Fixes
INSERT INTO flightplan_navigation_fixes (identifier, name, fix_type, latitude, longitude, map_theater, frequency, source)
VALUES
    -- Major Syrian Airports
    ('DAM', 'Damascus VOR', 'VOR', 33.4147, 36.5156, 'Syria', '116.00', 'openaip'),
    ('ALP', 'Aleppo VOR', 'VOR', 36.1808, 37.2244, 'Syria', '114.30', 'openaip'),
    ('LTK', 'Latakia VOR', 'VOR', 35.4011, 35.9486, 'Syria', '112.80', 'openaip'),

    -- Cyprus
    ('LCA', 'Larnaca VOR', 'VOR', 34.8750, 33.6250, 'Syria', '114.80', 'openaip'),
    ('PHA', 'Paphos VOR', 'VOR', 34.7178, 32.4856, 'Syria', '113.70', 'openaip'),
    ('NIC', 'Nicosia VOR', 'VOR', 35.1500, 33.2833, 'Syria', '116.50', 'openaip'),

    -- Lebanon
    ('BEY', 'Beirut VOR', 'VOR', 33.8208, 35.4883, 'Syria', '112.60', 'openaip'),

    -- Key Waypoints
    ('LEBOR', 'Lebanon Border', 'WYP', 34.5000, 36.0000, 'Syria', NULL, 'user'),
    ('CYPRS', 'Cyprus South', 'WYP', 34.5000, 33.0000, 'Syria', NULL, 'user'),
    ('COAST', 'Syrian Coast', 'WYP', 35.5000, 35.5000, 'Syria', NULL, 'user')

ON CONFLICT (identifier, map_theater) DO UPDATE SET
    name = EXCLUDED.name,
    fix_type = EXCLUDED.fix_type,
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude,
    frequency = EXCLUDED.frequency,
    source = EXCLUDED.source;

-- Persian Gulf Theater - Common Fixes
INSERT INTO flightplan_navigation_fixes (identifier, name, fix_type, latitude, longitude, map_theater, frequency, source)
VALUES
    -- UAE
    ('DXB', 'Dubai VOR', 'VOR', 25.2528, 55.3644, 'PersianGulf', '114.70', 'openaip'),
    ('AUH', 'Abu Dhabi VOR', 'VOR', 24.4331, 54.6511, 'PersianGulf', '112.60', 'openaip'),
    ('SHJ', 'Sharjah VOR', 'VOR', 25.3286, 55.5172, 'PersianGulf', '116.20', 'openaip'),
    ('FJR', 'Fujairah VOR', 'VOR', 25.1122, 56.3241, 'PersianGulf', '113.50', 'openaip'),

    -- Iran
    ('BND', 'Bandar Abbas VOR', 'VOR', 27.2183, 56.3778, 'PersianGulf', '115.50', 'openaip'),
    ('KIS', 'Kish VOR', 'VOR', 26.5269, 53.9803, 'PersianGulf', '112.00', 'openaip'),

    -- Oman
    ('MCT', 'Muscat VOR', 'VOR', 23.5933, 58.2844, 'PersianGulf', '113.10', 'openaip'),

    -- Key Waypoints
    ('STRIT', 'Strait of Hormuz', 'WYP', 26.5000, 56.5000, 'PersianGulf', NULL, 'user'),
    ('GULF', 'Gulf Center', 'WYP', 26.0000, 52.5000, 'PersianGulf', NULL, 'user')

ON CONFLICT (identifier, map_theater) DO UPDATE SET
    name = EXCLUDED.name,
    fix_type = EXCLUDED.fix_type,
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude,
    frequency = EXCLUDED.frequency,
    source = EXCLUDED.source;

-- Afghanistan Theater - Common Fixes
-- Based on real-world Afghan airways and navaids
INSERT INTO flightplan_navigation_fixes (identifier, name, fix_type, latitude, longitude, map_theater, frequency, source)
VALUES
    -- Major Afghan VORs/NDBs
    ('KDH', 'Kandahar VOR', 'VOR', 31.5058, 65.8478, 'Afghanistan', '116.00', 'airnav'),
    ('KBL', 'Kabul VOR', 'VOR', 34.5658, 69.2125, 'Afghanistan', '112.10', 'airnav'),
    ('HRT', 'Herat VOR', 'VOR', 34.2100, 62.2283, 'Afghanistan', '114.50', 'airnav'),
    ('MZR', 'Mazar-i-Sharif VOR', 'VOR', 36.7069, 67.2097, 'Afghanistan', '113.30', 'airnav'),
    ('JBD', 'Jalalabad NDB', 'NDB', 34.3997, 70.4986, 'Afghanistan', '355', 'airnav'),
    ('BGM', 'Bagram NDB', 'NDB', 34.9461, 69.2650, 'Afghanistan', '290', 'airnav'),
    ('SGA', 'Shindand NDB', 'NDB', 33.3913, 62.2610, 'Afghanistan', '386', 'airnav'),

    -- Pakistan Border Region
    ('ISB', 'Islamabad VOR', 'VOR', 33.6167, 73.0992, 'Afghanistan', '115.10', 'airnav'),
    ('LHE', 'Lahore VOR', 'VOR', 31.5216, 74.4036, 'Afghanistan', '113.80', 'airnav'),
    ('MUX', 'Multan VOR', 'VOR', 30.2033, 71.4192, 'Afghanistan', '114.10', 'airnav'),
    ('PSN', 'Peshawar NDB', 'NDB', 33.9939, 71.5147, 'Afghanistan', '365', 'airnav'),

    -- Airway G202 Waypoints (Kandahar to Kabul route)
    ('NABID', 'Nabid', 'WYP', 32.0000, 66.5000, 'Afghanistan', NULL, 'airnav'),
    ('DOLAN', 'Dolan', 'WYP', 32.5000, 67.0000, 'Afghanistan', NULL, 'airnav'),
    ('GURMA', 'Gurma', 'WYP', 33.0000, 67.5000, 'Afghanistan', NULL, 'airnav'),
    ('TAKAM', 'Takam', 'WYP', 33.5000, 68.0000, 'Afghanistan', NULL, 'airnav'),
    ('LURAN', 'Luran', 'WYP', 34.0000, 68.5000, 'Afghanistan', NULL, 'airnav'),

    -- Airway A466 Waypoints
    ('PASAB', 'Pasab', 'WYP', 31.6667, 65.2500, 'Afghanistan', NULL, 'airnav'),
    ('TUGAB', 'Tugab', 'WYP', 32.1667, 64.5000, 'Afghanistan', NULL, 'airnav'),
    ('DALBA', 'Dalba', 'WYP', 32.5000, 64.0000, 'Afghanistan', NULL, 'airnav'),

    -- Airway B466 Waypoints (to Bagram)
    ('OQBAN', 'Oqban', 'WYP', 34.7500, 69.3000, 'Afghanistan', NULL, 'airnav'),
    ('SOKMA', 'Sokma', 'WYP', 35.0000, 68.8000, 'Afghanistan', NULL, 'airnav'),

    -- Camp Bastion / Helmand Region
    ('BASTN', 'Bastion', 'WYP', 31.8631, 64.2244, 'Afghanistan', NULL, 'user'),
    ('GERSH', 'Gereshk', 'WYP', 31.8167, 64.5667, 'Afghanistan', NULL, 'user'),
    ('LASHR', 'Lashkar Gah', 'WYP', 31.5939, 64.3700, 'Afghanistan', NULL, 'user'),

    -- Approach Fixes
    ('OAKN', 'Kandahar Intl', 'WYP', 31.5058, 65.8478, 'Afghanistan', NULL, 'user'),
    ('OAZI', 'Camp Bastion', 'WYP', 31.8631, 64.2244, 'Afghanistan', NULL, 'user'),
    ('OAIX', 'Bagram AB', 'WYP', 34.9461, 69.2650, 'Afghanistan', NULL, 'user'),
    ('OAKB', 'Kabul Intl', 'WYP', 34.5658, 69.2125, 'Afghanistan', NULL, 'user'),
    ('OASD', 'Shindand AB', 'WYP', 33.3913, 62.2610, 'Afghanistan', NULL, 'user'),
    ('OAHR', 'Herat Intl', 'WYP', 34.2100, 62.2283, 'Afghanistan', NULL, 'user'),
    ('OAMS', 'Mazar-i-Sharif', 'WYP', 36.7069, 67.2097, 'Afghanistan', NULL, 'user'),

    -- Entry/Exit Points
    ('SARAN', 'Saran Pass', 'WYP', 35.3167, 69.0167, 'Afghanistan', NULL, 'user'),
    ('KHYBR', 'Khyber Pass', 'WYP', 34.0831, 71.0942, 'Afghanistan', NULL, 'user'),
    ('SPINB', 'Spin Boldak', 'WYP', 31.0014, 66.3972, 'Afghanistan', NULL, 'user')

ON CONFLICT (identifier, map_theater) DO UPDATE SET
    name = EXCLUDED.name,
    fix_type = EXCLUDED.fix_type,
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude,
    frequency = EXCLUDED.frequency,
    source = EXCLUDED.source;
