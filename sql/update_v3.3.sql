UPDATE nodes SET last_seen=last_seen at time zone current_setting('TIMEZONE') at time zone 'utc';
UPDATE version SET version='v3.4';
