-- Patch for already-seeded environments (and fresh ones: the seed runs every
-- patch file on every deploy, in name order — after
-- patch_attribute_value_species_configs_table.sql, which creates the table).
--
-- Poultry (chicken) and Beehive have no ear to attach a tag to, so they are registered
-- with a Secondary Identifier (leg band, wing tag, hive number, ...) and a
-- Quantity (head count) instead of an Ear Tag, and their Sex can be Mixed.
-- That is what the species' "Requires Ear Tag" (off) / "Flock / Group Species"
-- (on) config means (see domain_validation_utils.get_species_config). Without a
-- row a species defaults to "requires an ear tag, not a flock", which is why
-- neither species could be registered without one.
--
-- The species ids are Master Data value ids: LIVESTOCK_TYPE codes (LSTK_CHICKEN
-- is the Gen1 livestock type the registry called Poultry). The two former
-- registry codes stay for records saved before the species field moved to
-- LIVESTOCK_TYPE (get_species_config tries the type code first). The
-- registry keeps no copy of the list, so the rows are written directly.
-- ON CONFLICT DO NOTHING: a species an administrator has already configured
-- is left exactly as they set it.
INSERT INTO "public"."g2p_attribute_value_species_configs" ("value_id", "requires_ear_tag", "is_flock_species")
VALUES
  ('LSTK_CHICKEN', FALSE, TRUE),
  ('LIVESTOCK_SPECIES_POULTRY', FALSE, TRUE),
  ('LIVESTOCK_SPECIES_BEEHIVE', FALSE, TRUE)
ON CONFLICT ("value_id") DO NOTHING;
