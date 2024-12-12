ALTER TABLE IF EXISTS ovgme_packages RENAME TO mm_packages;
UPDATE version SET version='v3.12';
