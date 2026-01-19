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
-- Based on real-world Afghan airways and navaids from OpenNav database
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

    -- Kandahar/Helmand Region RNAV Waypoints (from OpenNav)
    ('AKOGE', 'Akoge', 'WYP', 31.520442, 65.872261, 'Afghanistan', NULL, 'opennav'),
    ('ASLUM', 'Aslum', 'WYP', 31.016667, 66.616667, 'Afghanistan', NULL, 'opennav'),
    ('BAGNI', 'Bagni', 'WYP', 32.625000, 64.441667, 'Afghanistan', NULL, 'opennav'),
    ('BENUL', 'Benul', 'WYP', 31.564589, 65.943292, 'Afghanistan', NULL, 'opennav'),
    ('CANUP', 'Canup', 'WYP', 31.725094, 66.217125, 'Afghanistan', NULL, 'opennav'),
    ('CEGAT', 'Cegat', 'WYP', 31.452764, 65.750556, 'Afghanistan', NULL, 'opennav'),
    ('CEKAS', 'Cekas', 'WYP', 31.571181, 65.957819, 'Afghanistan', NULL, 'opennav'),
    ('CODIX', 'Codix', 'WYP', 31.440472, 65.737697, 'Afghanistan', NULL, 'opennav'),
    ('DANOD', 'Danod', 'WYP', 32.407936, 62.010908, 'Afghanistan', NULL, 'opennav'),
    ('DARUS', 'Darus', 'WYP', 32.295556, 66.126944, 'Afghanistan', NULL, 'opennav'),
    ('DILAM', 'Dilam', 'WYP', 32.175000, 63.400000, 'Afghanistan', NULL, 'opennav'),
    ('DIRT', 'Dirt', 'WYP', 31.090333, 65.170833, 'Afghanistan', NULL, 'opennav'),
    ('DOLAN', 'Dolan', 'WYP', 31.841667, 64.650000, 'Afghanistan', NULL, 'opennav'),
    ('DOVKE', 'Dovke', 'WYP', 31.627956, 66.054303, 'Afghanistan', NULL, 'opennav'),
    ('DUPUG', 'Dupug', 'WYP', 31.295931, 65.473381, 'Afghanistan', NULL, 'opennav'),
    ('ELEKO', 'Eleko', 'WYP', 30.334733, 66.145833, 'Afghanistan', NULL, 'opennav'),
    ('EMAVO', 'Emavo', 'WYP', 31.492069, 65.824078, 'Afghanistan', NULL, 'opennav'),
    ('EMEKU', 'Emeku', 'WYP', 31.347661, 65.992350, 'Afghanistan', NULL, 'opennav'),
    ('EMERO', 'Emero', 'WYP', 30.239967, 65.105139, 'Afghanistan', NULL, 'opennav'),
    ('FALOD', 'Falod', 'WYP', 31.383203, 65.641894, 'Afghanistan', NULL, 'opennav'),
    ('FARAH', 'Farah', 'WYP', 32.366667, 62.158333, 'Afghanistan', NULL, 'opennav'),
    ('HAKER', 'Haker', 'WYP', 31.731006, 66.212233, 'Afghanistan', NULL, 'opennav'),
    ('HHOOT', 'Hhoot', 'WYP', 31.561503, 65.941444, 'Afghanistan', NULL, 'opennav'),
    ('HIWY2', 'Hiwy2', 'WYP', 31.900000, 65.485278, 'Afghanistan', NULL, 'opennav'),
    ('IMENO', 'Imeno', 'WYP', 31.623389, 66.038103, 'Afghanistan', NULL, 'opennav'),
    ('JATON', 'Jaton', 'WYP', 31.396400, 65.652625, 'Afghanistan', NULL, 'opennav'),
    ('KIRAT', 'Kirat', 'WYP', 30.665000, 64.910278, 'Afghanistan', NULL, 'opennav'),
    ('KUNAN', 'Kunan', 'WYP', 32.059444, 66.607500, 'Afghanistan', NULL, 'opennav'),
    ('LABUS', 'Labus', 'WYP', 33.386667, 62.250000, 'Afghanistan', NULL, 'opennav'),
    ('LOVIT', 'Lovit', 'WYP', 31.151111, 65.007222, 'Afghanistan', NULL, 'opennav'),
    ('MAXIN', 'Maxin', 'WYP', 32.775000, 67.450000, 'Afghanistan', NULL, 'opennav'),
    ('NABID', 'Nabid', 'WYP', 31.747778, 64.974167, 'Afghanistan', NULL, 'opennav'),
    ('NABKA', 'Nabka', 'WYP', 31.483267, 62.851922, 'Afghanistan', NULL, 'opennav'),
    ('OYLER', 'Oyler', 'WYP', 31.618550, 66.037703, 'Afghanistan', NULL, 'opennav'),
    ('PAROD', 'Parod', 'WYP', 31.483333, 65.900000, 'Afghanistan', NULL, 'opennav'),
    ('PRKWY', 'Prkwy', 'WYP', 31.524667, 66.448500, 'Afghanistan', NULL, 'opennav'),
    ('R1264', 'R1264', 'WYP', 31.497642, 65.833969, 'Afghanistan', NULL, 'opennav'),
    ('R1266', 'R1266', 'WYP', 31.514108, 65.861631, 'Afghanistan', NULL, 'opennav'),
    ('RIKAD', 'Rikad', 'WYP', 33.461667, 66.458333, 'Afghanistan', NULL, 'opennav'),
    ('SIGSI', 'Sigsi', 'WYP', 31.091667, 61.883333, 'Afghanistan', NULL, 'opennav'),
    ('SODAS', 'Sodas', 'WYP', 30.660556, 66.067222, 'Afghanistan', NULL, 'opennav'),
    ('TOTSI', 'Totsi', 'WYP', 32.038889, 65.170278, 'Afghanistan', NULL, 'opennav'),
    ('ULOSA', 'Ulosa', 'WYP', 30.752500, 65.429722, 'Afghanistan', NULL, 'opennav'),
    ('VACUK', 'Vacuk', 'WYP', 30.712356, 63.855283, 'Afghanistan', NULL, 'opennav'),
    ('VUSIP', 'Vusip', 'WYP', 31.432222, 66.872222, 'Afghanistan', NULL, 'opennav'),

    -- A453 Airway (Kandahar to Kabul)
    ('BURTA', 'Burta', 'WYP', 32.625000, 64.441667, 'Afghanistan', NULL, 'skyvector'),
    ('PATOX', 'Patox', 'WYP', 33.548333, 68.420000, 'Afghanistan', NULL, 'opennav'),
    ('NOLEX', 'Nolex', 'WYP', 33.867833, 68.660167, 'Afghanistan', NULL, 'skyvector'),

    -- Camp Bastion / Helmand Region
    ('BASTN', 'Bastion', 'WYP', 31.8631, 64.2244, 'Afghanistan', NULL, 'user'),
    ('GERSH', 'Gereshk', 'WYP', 31.8167, 64.5667, 'Afghanistan', NULL, 'user'),
    ('LASHR', 'Lashkar Gah', 'WYP', 31.5939, 64.3700, 'Afghanistan', NULL, 'user'),

    -- Approach Fixes / Airport Identifiers
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
