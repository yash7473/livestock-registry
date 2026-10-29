-- Patch for already-seeded environments only (the seed only INSERTs, and a
-- multi-row INSERT that hits one existing row fails as a whole).
--
-- Adds the Ear Tag Replacement (Retagging) register -- SRS LR-03 / LR-14 --
-- as a child table of the Livestock record, shown on the register's
-- "Livestock" tab under Livestock Details. Rows are added through a change
-- request (Edit Details) and applied on approval by
-- G2PRegisterDomainServiceRetagging.post_approve. Same rows as the canonical
-- lines in g2p_register_definitions.sql, g2p_register_schemas.sql,
-- g2p_register_sections.sql and g2p_register_ui_tab_sections.sql; keep them in
-- sync. Safe to re-run.

-- Tables. The extension's startup migrate_database (app.py) does not
-- reliably create new tables on this stack, so they are created here too,
-- matching the columns the models in register_domain/models/retagging.py
-- declare (same base columns as every other event line, e.g. breedings).
CREATE TABLE IF NOT EXISTS g2p_register_retaggings (
    internal_record_id varchar NOT NULL,
    functional_record_id varchar,
    link_internal_record_id varchar,
    link_foundational_id varchar,
    record_name varchar,
    record_image_document_id text,
    created_by varchar NOT NULL,
    created_at timestamp NOT NULL,
    last_approved_at timestamp NOT NULL,
    last_approved_by varchar NOT NULL,
    search_text text,
    record_status varchar NOT NULL,
    record_status_reason varchar,
    ear_tag_id varchar,
    species varchar,
    new_ear_tag_id varchar,
    reason varchar,
    retag_date date,
    approving_officer varchar,
    justification text,
    applied_on date,
    PRIMARY KEY (internal_record_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_g2p_register_retaggings_functional_record_id ON g2p_register_retaggings (functional_record_id);
CREATE INDEX IF NOT EXISTS ix_g2p_register_retaggings_link_internal_record_id ON g2p_register_retaggings (link_internal_record_id);
CREATE INDEX IF NOT EXISTS ix_g2p_register_retaggings_link_foundational_id ON g2p_register_retaggings (link_foundational_id);

CREATE TABLE IF NOT EXISTS g2p_register_history_retaggings (
    history_record_id varchar NOT NULL,
    internal_record_id varchar NOT NULL,
    tab_id varchar NOT NULL,
    section_id varchar NOT NULL,
    change_request_id varchar,
    submission_id varchar,
    change_request_source varchar NOT NULL,
    is_primary_section boolean NOT NULL,
    functional_record_id varchar,
    link_internal_record_id varchar,
    link_foundational_id varchar,
    record_name varchar,
    record_image_document_id text,
    record_status varchar NOT NULL,
    record_status_reason varchar,
    created_by varchar NOT NULL,
    created_at timestamp NOT NULL,
    approved_by varchar NOT NULL,
    approved_at timestamp NOT NULL,
    ear_tag_id varchar,
    species varchar,
    new_ear_tag_id varchar,
    reason varchar,
    retag_date date,
    approving_officer varchar,
    justification text,
    applied_on date,
    PRIMARY KEY (history_record_id)
);
CREATE INDEX IF NOT EXISTS ix_g2p_register_history_retaggings_internal_record_id ON g2p_register_history_retaggings (internal_record_id);
CREATE INDEX IF NOT EXISTS ix_g2p_register_history_retaggings_link_internal_record_id ON g2p_register_history_retaggings (link_internal_record_id);
CREATE INDEX IF NOT EXISTS ix_g2p_register_history_retaggings_section_id ON g2p_register_history_retaggings (section_id);
CREATE INDEX IF NOT EXISTS ix_g2p_register_history_retaggings_tab_id ON g2p_register_history_retaggings (tab_id);

CREATE TABLE IF NOT EXISTS g2p_intake_form_retaggings (
    submission_id uuid NOT NULL,
    application_reference varchar,
    internal_record_id varchar NOT NULL,
    functional_record_id varchar,
    link_internal_record_id varchar,
    link_foundational_id varchar,
    record_name varchar,
    record_image_document_id text,
    created_by varchar NOT NULL,
    created_at timestamp NOT NULL,
    last_approved_at timestamp NOT NULL,
    last_approved_by varchar NOT NULL,
    search_text text,
    record_status varchar NOT NULL,
    record_status_reason varchar,
    ear_tag_id varchar,
    species varchar,
    new_ear_tag_id varchar,
    reason varchar,
    retag_date date,
    approving_officer varchar,
    justification text,
    applied_on date,
    PRIMARY KEY (submission_id, internal_record_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_g2p_intake_form_retaggings_functional_record_id ON g2p_intake_form_retaggings (functional_record_id);
CREATE INDEX IF NOT EXISTS ix_g2p_intake_form_retaggings_link_internal_record_id ON g2p_intake_form_retaggings (link_internal_record_id);
CREATE INDEX IF NOT EXISTS ix_g2p_intake_form_retaggings_application_reference ON g2p_intake_form_retaggings (application_reference);

INSERT INTO "public"."g2p_register_definitions" ("register_id","register_mnemonic","register_subject","register_description","master_register_id","register_rank","functional_id_generation_required","register_purpose","program_id","program_mnemonic","register_icon","has_image","dedup_is_enabled","dedup_threshold_score","completion_score_required","outgest_applicable","requires_registrant_authentication","registrant_authentication_validity_days","registrant_re_auth_warning_days_before") VALUES
('b853f1db-6dd6-5b16-a0d8-25a8524142e6','Retagging','Ear Tag Replacements','Ear Tag Replacement Register','997676d3-7008-59f9-b23e-613ad79bbb08',55,'FALSE','TABLE',NULL,NULL,NULL,'FALSE','FALSE',0,'FALSE','FALSE','FALSE',730,30)
ON CONFLICT DO NOTHING;

INSERT INTO "public"."g2p_register_schemas" ("register_id","deduplicate_schema","search_result_schema","filter_schema") VALUES
('b853f1db-6dd6-5b16-a0d8-25a8524142e6','[]','[{"field_name": "ear_tag_id", "display_label": "Old Ear Tag", "order": 1}, {"field_name": "new_ear_tag_id", "display_label": "New Ear Tag", "order": 2}, {"field_name": "reason", "display_label": "Reason", "order": 3}, {"field_name": "retag_date", "display_label": "Date of Retagging", "order": 4}]','[{"field_name": "reason", "display_label": "Reason", "filter_type": "select", "order": 1, "allowed_operators": ["eq"]}]')
ON CONFLICT DO NOTHING;

INSERT INTO "public"."g2p_register_sections" ("register_id","section_id","section_register_id","is_core_section","section_mnemonic","section_description","documents_required","no_of_verifications_required","is_list","section_weightage","section_ui_schema","cr_auto_approve_for_bene_portal","cr_auto_approve_for_agent_portal","cr_auto_approve_for_staff_portal","cr_auto_approve_for_partner") VALUES
('997676d3-7008-59f9-b23e-613ad79bbb08','livestock_retagging_details_section_01','b853f1db-6dd6-5b16-a0d8-25a8524142e6','FALSE','ls_retagging_details','Ear Tag Replacement','FALSE',0,'TRUE',10,'{"panels": [{"panels": [{"widgets": [{"widget": "dialog-table", "widget-id": "ls_retagging_details_table", "widget-type": "dialog-table", "widget-label": "Ear Tag Replacement", "widget-readonly": false, "widget-data-path": "b853f1db-6dd6-5b16-a0d8-25a8524142e6.records", "widget-data-columns": [{"widget": "text", "column-key": "ear_tag_id", "widget-type": "input", "widget-label": "Old Ear Tag", "widget-readonly": false, "widget-required": true, "widget-data-path": "ear_tag_id", "widget-placeholder": "ET + 10 digits, e.g. ET0000000123"}, {"widget": "select", "column-key": "species", "widget-type": "input", "widget-label": "Species", "widget-readonly": true, "widget-data-path": "species", "widget-data-source": {"type": "api", "method": "POST", "params": {"page_size": 500, "attribute_id": "LIVESTOCK_TYPE"}, "service": "attributes", "endpoint": "values", "labelKey": "value_display", "valueKey": "value_id"}}, {"widget": "text", "column-key": "new_ear_tag_id", "widget-type": "input", "widget-label": "New Ear Tag", "widget-readonly": false, "widget-required": true, "widget-data-path": "new_ear_tag_id", "widget-placeholder": "ET + 10 digits, e.g. ET0000000123"}, {"widget": "select", "column-key": "reason", "widget-type": "input", "widget-label": "Reason for Replacement", "widget-readonly": false, "widget-required": true, "widget-data-path": "reason", "widget-data-source": {"type": "static", "options": [{"label": "Lost", "value": "LOST"}, {"label": "Damaged", "value": "DAMAGED"}, {"label": "Upgrade", "value": "UPGRADE"}]}}, {"widget": "date", "column-key": "retag_date", "widget-type": "input", "widget-label": "Date of Retagging", "widget-readonly": false, "widget-required": true, "widget-data-path": "retag_date", "widget-data-format": {"dateFormat": "DD/MM/YYYY", "inputMethod": "picker", "dateConstraint": "any"}, "widget-data-placeholder": "dd_mm_yyyy"}, {"widget": "text", "column-key": "approving_officer", "widget-type": "input", "widget-label": "Approving Officer", "widget-readonly": false, "widget-required": true, "widget-data-path": "approving_officer"}, {"widget": "text", "column-key": "justification", "widget-type": "input", "widget-label": "Justification Notes", "widget-readonly": false, "widget-required": true, "widget-data-path": "justification"}, {"widget": "date", "column-key": "applied_on", "widget-type": "input", "widget-label": "Applied On", "widget-readonly": true, "widget-data-path": "applied_on", "widget-data-format": {"dateFormat": "DD/MM/YYYY", "inputMethod": "picker"}}], "widget-data-operations": {"add": true, "edit": false, "remove": false}, "widget-data-dialog-title-add": "Add Ear Tag Replacement", "widget-data-dialog-title-edit": "Ear Tag Replacement"}], "panel-id": "vertical_panel_ls_retagging_details_1", "panel-orientation": "vertical"}], "panel-id": "horizontal_panel_ls_retagging_details", "panel-orientation": "horizontal"}], "section-id": "livestock_retagging_details_section_01", "section-title": "Ear Tag Replacement", "section-editable": true, "section-required-for-submit": false}','FALSE','FALSE','FALSE','FALSE')
ON CONFLICT DO NOTHING;

INSERT INTO "public"."g2p_register_ui_tab_sections" ("tab_section_id","register_id","tab_id","section_id","section_order") VALUES
('9c220135-0705-50e5-9acd-d0941de979ee','997676d3-7008-59f9-b23e-613ad79bbb08','livestock_animal_tab','livestock_retagging_details_section_01',20)
ON CONFLICT DO NOTHING;

-- Section heading label (keyed by section_mnemonic, like ls_breeding_details).
UPDATE "public"."registry_languages"
SET core_translation = (core_translation::jsonb || '{"ls_retagging_details": "Ear Tag Replacement"}'::jsonb)::json
WHERE core_translation::jsonb ? 'ls_breeding_details'
  AND NOT core_translation::jsonb ? 'ls_retagging_details';
