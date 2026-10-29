"""Default vaccine intervals, present on every environment.

next_due_date is vaccination_date + the interval_days of the approved Vaccine
Schedule row for that vaccine and species
(G2PRegisterDomainServiceVaccination._populate_next_due_date, and the dialog's
preview through /livestock/form-config). These rows used to come only from
docker/db-seed/sample-data, which only the local stack loads, so on dev the
table was empty and every Next Due Date stayed blank. The portal has no screen
for the table, so the defaults are written here, at the end of the
extension's migration, right after the table is created.

A default is added only when no row exists for its vaccine and species, active
or not: an interval changed or switched off on an environment is kept as is.
The Vaccine Schedule form saves attribute value ids (VACCINE_TYPE_ANTHRAX_CATTLE,
species LSTK_CATTLE, or a former LIVESTOCK_SPECIES_CATTLE) while these defaults
use the bare codes, so the existence check strips the prefixes before
comparing (and reads chicken as poultry), as the lookup does.
"""

import logging

from sqlalchemy import text

_logger = logging.getLogger(__name__)

# (vaccine_name, species, interval_days, notes). vaccine_name and species are
# the bare codes the schedule lookup compares (VACCINE_TYPE_ / LIVESTOCK_SPECIES_ /
# LSTK_ prefixes are stripped on both sides).
DEFAULT_SCHEDULES = [
    ("ANTHRAX_CATTLE", "CATTLE", 365, "Annual, ahead of the main rains."),
    ("ANTHRAX_CAMEL", "CAMEL", 365, None),
    ("BLACKLEG", "CATTLE", 365, None),
    ("LSD", "CATTLE", 365, None),
    ("PPR_GOAT", "GOAT", 365, None),
    ("PPR_SHEEP", "SHEEP", 365, None),
    ("CCPP", "GOAT", 365, None),
    ("NEWCASTLE", "POULTRY", 90, "Quarterly for backyard layer flocks."),
]

_INSERT = text(
    """
    INSERT INTO g2p_register_vaccine_schedules (
        internal_record_id, record_name, search_text, created_by, created_at,
        last_approved_at, last_approved_by, record_status,
        vaccine_name, species, interval_days, is_active, notes
    )
    SELECT CAST(:rid AS VARCHAR), CAST(:name AS VARCHAR), CAST(:name AS VARCHAR),
           'system', now(), now(), 'system', 'ACTIVE',
           CAST(:vaccine AS VARCHAR), CAST(:species AS VARCHAR), CAST(:days AS INTEGER),
           TRUE, CAST(:notes AS VARCHAR)
    WHERE NOT EXISTS (
        SELECT 1 FROM g2p_register_vaccine_schedules
        WHERE regexp_replace(upper(vaccine_name), '^(VACCINE_TYPE_|LIVESTOCK_VACCINE_)', '') = CAST(:vaccine AS VARCHAR)
          AND regexp_replace(regexp_replace(upper(species), '^(LIVESTOCK_SPECIES_|LSTK_)', ''), '^CHICKEN$', 'POULTRY') = CAST(:species AS VARCHAR)
    )
    """
)


async def add_default_vaccine_schedules(engine) -> list[str]:
    """Insert the missing default schedules; return the ones added. A failure
    is logged, never raised: it must not stop the service from starting."""
    try:
        return await _add_defaults(engine)
    except Exception:
        _logger.exception("Vaccine schedules: could not add the defaults")
        return []


async def _add_defaults(engine) -> list[str]:
    added: list[str] = []
    async with engine.begin() as conn:
        for vaccine, species, days, notes in DEFAULT_SCHEDULES:
            result = await conn.execute(_INSERT, {
                "rid": f"vsched-default-{vaccine.lower()}-{species.lower()}",
                "name": f"{vaccine} {species}",
                "vaccine": vaccine, "species": species, "days": days, "notes": notes,
            })
            if result.rowcount:
                added.append(f"{vaccine}/{species}")
    if added:
        _logger.warning("Vaccine schedules: added defaults %s", ", ".join(added))
    return added
