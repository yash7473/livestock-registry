-- Patch for already-seeded environments (fresh ones get the same bindings from
-- g2p_register_sections.sql; this then changes nothing).
--
-- The species fields of every livestock section read the Master Data list
-- LIVESTOCK_TYPE (the Gen1 livestock types: cattle, camel, sheep, goat,
-- chicken, donkey, horse, mule) and the breed field reads LIVESTOCK_BREED_NAME
-- (the MoA national breed list, whose species attribute is a LIVESTOCK_TYPE
-- code). The platform validates a coded field against the list named by its
-- widget's attribute_id, so this binding is what decides which codes a save
-- accepts.
--
-- The seed only INSERTs sections and never touches an existing row. The
-- sections that have a *_sync_ui_schema patch re-apply their whole schema;
-- this updates the remaining ones (Retagging, Vaccine Schedule) and any other
-- livestock section still bound to the former lists. Only the two exact
-- bindings are replaced, so the file is idempotent and leaves every other
-- part of a schema as it is.
UPDATE "public"."g2p_register_sections"
SET section_ui_schema = replace(
      replace(section_ui_schema::text,
              '"attribute_id": "LIVESTOCK_SPECIES"', '"attribute_id": "LIVESTOCK_TYPE"'),
      '"attribute_id": "LIVESTOCK_BREED"', '"attribute_id": "LIVESTOCK_BREED_NAME"')::jsonb
WHERE section_mnemonic LIKE 'ls\_%'
  AND (section_ui_schema::text LIKE '%"attribute_id": "LIVESTOCK_SPECIES"%'
       OR section_ui_schema::text LIKE '%"attribute_id": "LIVESTOCK_BREED"%');
