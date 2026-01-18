-- Seed data for logbook awards and qualifications
-- Ported from the legacy JSW logbook system

-- =====================
-- AWARDS
-- =====================

-- Hours-based awards (auto-grantable)
INSERT INTO logbook_awards (name, description, auto_grant, requirements) VALUES
('50_HOURS', 'Flown 50 hours in total across all types.', true, '{"total_hours": 50}'),
('100_HOURS', 'Flown 100 hours in total across all types.', true, '{"total_hours": 100}'),
('250_HOURS', 'Flown 250 hours in total across all types.', true, '{"total_hours": 250}'),
('500_HOURS', 'Flown 500 hours in total across all types.', true, '{"total_hours": 500}'),
('50_TYPE_HOURS', 'Flown 50 hours in one aircraft type.', true, '{"type_hours": 50}'),
('100_TYPE_HOURS', 'Flown 100 hours in one aircraft type.', true, '{"type_hours": 100}'),
('250_TYPE_HOURS', 'Flown 250 hours in one aircraft type.', true, '{"type_hours": 250}'),
('500_TYPE_HOURS', 'Flown 500 hours in one aircraft type.', true, '{"type_hours": 500}')
ON CONFLICT (name) DO NOTHING;

-- Type conversion awards (manually granted)
INSERT INTO logbook_awards (name, description, auto_grant) VALUES
('HARRIER_TYPE_CONVERSION', 'Completed the necessary training to operate the Harrier aircraft safely.', false),
('TOMCAT_TYPE_CONVERSION', 'Completed the necessary training to operate the Tomcat aircraft safely.', false),
('VIPER_TYPE_CONVERSION', 'Completed the necessary training to operate the Viper aircraft safely.', false),
('APACHE_TYPE_CONVERSION', 'Completed the necessary training to operate the Apache aircraft safely.', false),
('EAGLE_TYPE_CONVERSION', 'Completed the necessary training to operate the Eagle aircraft safely.', false)
ON CONFLICT (name) DO NOTHING;

-- Combat and skill awards (manually granted)
INSERT INTO logbook_awards (name, description, auto_grant) VALUES
('FIGHTER_ACE', 'A pilot who has achieved five or more confirmed air kills on operations.', false),
('COMBAT_READY', 'Has completed the necessary training to be deemed fully combat ready.', false),
('COMPLETED_OCU', 'Has finished the complete OCU syllabus.', false),
('GREY_HEART', 'Brought an aircraft home that has been badly damaged due to enemy action.', false),
('WAR_HAWK', 'Has completed every mission in an operational tour.', false),
('ENGINEERS_FRIEND', 'Has completed one operational tour without loss of an aircraft.', false),
('AGRESSOR', 'Has completed one operational tour as an aggressor pilot.', false),
('DOUBLE_ENDER', 'Has qualified on all seats of a particular aircraft type.', false),
('SAR_PILOT', 'Has rescued other JSW pilots on operational sorties.', false),
('TALK_TO_ME_GOOSE', 'Has completed at least one operation primarily in the second seat of an aircraft type.', false)
ON CONFLICT (name) DO NOTHING;

-- AAR awards (manually granted)
INSERT INTO logbook_awards (name, description, auto_grant) VALUES
('PLUGGED_IN_DAY', 'First AAR refuel connection on first attempt during the day.', false),
('PLUGGED_IN_NIGHT', 'First AAR refuel connection on first attempt at night.', false)
ON CONFLICT (name) DO NOTHING;

-- Special role awards (manually granted)
INSERT INTO logbook_awards (name, description, auto_grant) VALUES
('CONTROLLER', 'Has consistently provided air traffic control services to JSW pilots.', false),
('SANDY_QUALIFIED', 'Provided AFAC or JTAC services in support of troops in contact on operations.', false),
('LOGISTICS_ENABLER', 'Provided significant assistance and support in the logistics field to enable JSW''s primary missions.', false),
('MISSION_MAKER', 'Provided significant support and assistance to the mission making team in the execution of JSW''s campaigns.', false),
('DIPLOMAT', 'Represented JSW''s best interests in developing relationships with third parties.', false),
('AVIATION_AMBASSADOR', 'Brings real-world aviation experience to the JSW community to improve the experience for all members.', false),
('QUALIFIED_FLIGHT_INSTRUCTOR', 'Has provided significant training to JSW members on basic flight instruction.', false),
('QUALIFIED_WARFARE_INSTRUCTOR', 'Has provided significant training to JSW members on weapon employment and war fighting techniques.', false),
('FINANCIAL_CONTRIBUTOR', 'Has provided financial support to the running of the JSW group.', false),
('MEDIA_PERSONALITY', 'Has used their social media presence to promote JSW activities.', false)
ON CONFLICT (name) DO NOTHING;

-- Wings grading awards (manually granted)
INSERT INTO logbook_awards (name, description, auto_grant) VALUES
('DABBING_UNICORN', 'Received a dabbing unicorn (4.0) from Wings.', false),
('ACTUAL_UNICORN', 'Received an actual unicorn (5.0) from Wings.', false)
ON CONFLICT (name) DO NOTHING;

-- Operation campaign awards (manually granted)
INSERT INTO logbook_awards (name, description, auto_grant) VALUES
('OP_TALON_1', 'Completed more than half of the missions in support of Operation TALON 1.', false),
('OP_PULLMAN', 'Completed more than half of the missions in support of Operation PULLMAN.', false),
('OP_THRESHER_1', 'Completed more than half of the missions in support of Operation THRESHER 1.', false),
('OP_SHADER_1', 'Completed more than half of the missions in support of Operation SHADER 1.', false),
('OP_PURPLE_WARRIOR_1', 'Completed more than half of the missions in support of Operation PURPLE WARRIOR 1.', false)
ON CONFLICT (name) DO NOTHING;

-- =====================
-- QUALIFICATIONS
-- =====================

-- Carrier qualifications (90-day validity = 7776000 seconds)
INSERT INTO logbook_qualifications (name, description, valid_days) VALUES
('CQ_STOVL_DAY', 'Completed three takeoffs and landings to the deck during the day in accordance with NAVAIR 00-80T-111.', 90),
('CQ_STOVL_NIGHT', 'Completed three takeoffs and landings to the deck during the night in accordance with NAVAIR 00-80T-111.', 90),
('CQ_CV_DAY', 'Completed three takeoffs and landings to the deck during the day in accordance with NATOPS.', 90),
('CQ_CV_NIGHT', 'Completed three takeoffs and landings to the deck during the night in accordance with NATOPS.', 90)
ON CONFLICT (name) DO NOTHING;

-- Wings qualifications for 801 NAS (365-day validity = 31536000 seconds)
INSERT INTO logbook_qualifications (name, description, valid_days) VALUES
('WINGS_1_801NAS', '1.1 Pass introduction check-ride. 1.2 Basic aircraft handling familiarity. 1.3 Remain in formation during cross-country sorties while managing own aircraft. 1.4 Operate the aircraft from land in day VMC conditions.', 365),
('WINGS_2_801NAS', '2.1 Operate the aircraft safely to and from the deck using CASE 1 procedures in the day. 2.2 Fly safely as a second aircraft in arrow formation.', 365),
('WINGS_3_801NAS', '3.1 Operate the aircraft safely to and from the deck using CASE 1 procedures at night. 3.2 Operate the aircraft safely to the deck using CASE 3 procedures during the day. 3.3 Fly in tactical formation on a pre-briefed route during the day. 3.4 Unguided AG weaponeering.', 365),
('WINGS_4_801NAS', '4.1 Deliver ordinance on pre-briefed and FAC directed targets at an assigned time. 4.2 Guided AG weaponeering. 4.3 Offensive and defensive BFM. 4.4 Basic flight planning.', 365),
('WINGS_5_801NAS', '5.1 Plan, brief, and debrief complex missions. 5.2 Operate the aircraft safely to the deck using CASE 3 procedures at night. 5.3 Lead a flight of two aircraft on operations.', 365)
ON CONFLICT (name) DO NOTHING;

-- AAR qualifications (90-day validity)
INSERT INTO logbook_qualifications (name, description, valid_days) VALUES
('AAR_DAY_S3', 'Demonstrated proficiency in daytime AAR on the S-3 tanker.', 90),
('AAR_NIGHT_S3', 'Demonstrated proficiency in nighttime AAR on the S-3 tanker.', 90),
('AAR_DAY_KC135', 'Demonstrated proficiency in daytime AAR on the KC135 or KC135(MPRS) tanker.', 90),
('AAR_NIGHT_KC135', 'Demonstrated proficiency in nighttime AAR on the KC135 or KC135(MPRS) tanker.', 90),
('AAR_DAY_KC130', 'Demonstrated proficiency in daytime AAR on the KC130 tanker.', 90),
('AAR_NIGHT_KC130', 'Demonstrated proficiency in nighttime AAR on the KC130 tanker.', 90)
ON CONFLICT (name) DO NOTHING;
