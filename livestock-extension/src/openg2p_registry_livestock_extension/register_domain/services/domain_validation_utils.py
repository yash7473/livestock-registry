import re
from contextvars import ContextVar
from datetime import date, datetime

from openg2p_registry_core.errors import G2PRegistryErrorCodes, G2PRegistryException


def validation_error(message: str) -> None:
    raise G2PRegistryException(
        code=G2PRegistryErrorCodes.REQUEST_VALIDATION_ERROR.value[1],
        message=message,
    )


def parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_bool(value) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return bool(value)


def is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def require_field(record: dict, field: str, label: str | None = None) -> None:
    """Reject the save outright if field is missing/blank. Enforced here
    server-side rather than only via "widget-required" in the form's JSON,
    since a required-but-empty field is something the backend can always
    catch reliably — unlike display quirks in the table-cell widgets, which
    this project found are not something we can trust the frontend to get
    right without source access to fix them.
    """
    if is_blank(record.get(field)):
        validation_error(f"{label or field} is required")


async def refresh_total_animals(session, livestock_internal_record_id) -> None:
    """Set Total Livestock on a register Livestock record to the number of its
    animals in the Animal register (a flock or hive counts as one row, as the
    ODK ingest counts it). Called wherever an animal reaches the register:
    a first approval, an Animal change request, a Birth's offspring and the
    animal bulk import. Only ODK records used to get the number at all."""
    if not livestock_internal_record_id:
        return
    import importlib

    from sqlalchemy import func, select

    models = importlib.import_module("openg2p_registry_extensions.register_domain.models")
    count = (
        await session.execute(
            select(func.count())
            .select_from(models.G2PRegisterAnimal)
            .where(models.G2PRegisterAnimal.link_internal_record_id == livestock_internal_record_id)
        )
    ).scalar() or 0
    livestock = (
        await session.execute(
            select(models.G2PRegisterLivestock).where(
                models.G2PRegisterLivestock.internal_record_id == livestock_internal_record_id
            )
        )
    ).scalar_one_or_none()
    if livestock is not None and livestock.total_animals != count:
        livestock.total_animals = count
        session.add(livestock)


def _animal_models():
    """Import the animal models the same way the platform itself resolves
    the domain extension: through the "openg2p_registry_extensions" alias
    that main.py points at this package's real module in sys.modules
    (Option C), not through this package's own real dotted name.

    Importing via "..models" instead loads a second, independent copy of
    every model under a different sys.modules key — same source file, but a
    distinct module object — and SQLAlchemy then refuses the second
    declarative Table registration ("... is already defined for this
    MetaData instance"). Importing here, not at module load: only needed by
    the two DB-touching functions below, and doing it lazily avoids forcing
    load order relative to app startup.
    """
    import importlib

    models = importlib.import_module("openg2p_registry_extensions.register_domain.models")
    return models.G2PRegisterAnimal, models.G2PIntakeFormAnimal


_NATIONAL_EAR_TAG = re.compile(r"^ET\d{10}$", re.IGNORECASE)


def normalize_ear_tag_text(value) -> str | None:
    """The identifier an event names its animal by, normalised the way the
    Animal section stores it: a national ear tag (ET + 10 digits, any case,
    spaces ignored) becomes upper-case; anything else -- a flock, batch or
    hive secondary identifier, which is free text -- is only trimmed. Lookups
    match exactly on the stored value, so an event typed or sent as
    'et1000000001' would otherwise never find animal 'ET1000000001'."""
    if is_blank(value):
        return None
    compact = re.sub(r"\s+", "", str(value))
    if _NATIONAL_EAR_TAG.match(compact):
        return compact.upper()
    return str(value).strip()


def normalize_event_ear_tag(record: dict) -> str | None:
    """normalize_ear_tag_text applied to the record's `ear_tag_id`, written
    back so the stored row carries the normalised value."""
    value = normalize_ear_tag_text(record.get("ear_tag_id"))
    if value is not None:
        record["ear_tag_id"] = value
    return value


def animal_identified_by(model, identifier: str):
    """SQL condition: this Animal row is the one `identifier` names.

    An animal carries one of two identifiers, decided by its species'
    "Requires Ear Tag" config (get_species_config): an ear tag (ET + 10
    digits) or, for a species that has no ear to tag -- poultry, beehives --
    a free-text secondary_identifier (leg band, wing tag, hive number).
    Event rows name their animal in `ear_tag_id` either way, so every lookup
    from an event must accept both, or a flock could never have a health
    event, vaccination, vital event or breeding record logged against it.
    The two formats do not overlap in practice, and an exact match on either
    column is what the Animal section itself enforces uniqueness on.
    """
    from sqlalchemy import or_

    return or_(model.ear_tag_id == identifier, model.secondary_identifier == identifier)


# The ingest worker's database session while it saves an ingested submission
# (ODK, File Import); None everywhere else. Set by odk_ingest_hooks.
ingest_session: ContextVar = ContextVar("livestock_ingest_session", default=None)


async def ear_tag_exists(ear_tag_id: str, application_reference: str | None = None) -> bool:
    """True if `ear_tag_id` names a real animal — already approved into the
    register, or drafted under an in-progress intake submission. The value
    may be an ear tag or a secondary identifier (see animal_identified_by).

    With `application_reference` (the intake submission's reference, present on
    every row the platform has already saved) the check is SCOPED to that
    submission: the animal must be drafted in the same submission, or already
    approved from it. That is the Old System's rule (an event's animal is
    picked from the holding's own line_ids) and what keeps a typed ear tag
    from attaching an event to another farmer's animal. Without a reference
    (first save of a brand-new row) only global existence can be checked —
    the platform hands validate_domain_attributes this section's rows alone,
    with no submission context.
    """
    if is_blank(ear_tag_id):
        return False

    import importlib

    from openg2p_fastapi_common.context import dbengine
    from sqlalchemy import exists, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    G2PRegisterAnimal, G2PIntakeFormAnimal = _animal_models()

    register_cond = animal_identified_by(G2PRegisterAnimal, ear_tag_id)
    intake_cond = animal_identified_by(G2PIntakeFormAnimal, ear_tag_id)
    if application_reference:
        # Intake rows carry the submission's reference directly. Register rows
        # do not, but they hang off the holding (link_internal_record_id), and
        # the holding keeps its internal_record_id from intake to register —
        # so "approved from this submission" = linked to this submission's
        # Livestock record.
        G2PIntakeFormLivestock = importlib.import_module(
            "openg2p_registry_extensions.register_domain.models"
        ).G2PIntakeFormLivestock
        holdings_of_submission = select(G2PIntakeFormLivestock.internal_record_id).where(
            G2PIntakeFormLivestock.application_reference == application_reference
        )
        register_cond = register_cond & G2PRegisterAnimal.link_internal_record_id.in_(holdings_of_submission)
        intake_cond = intake_cond & (G2PIntakeFormAnimal.application_reference == application_reference)

    async def _found(session) -> bool:
        if (await session.execute(select(exists().where(register_cond)))).scalar():
            return True
        return bool((await session.execute(select(exists().where(intake_cond)))).scalar())

    # An ODK or File Import submission is saved by the ingest worker in one
    # transaction, animals first and events after; a separate connection
    # cannot see those uncommitted animals, so the event would be rejected.
    # The worker's own session (set around the save by the ingest hooks) is
    # used instead when there is one.
    worker_session = ingest_session.get()
    if worker_session is not None:
        return await _found(worker_session)

    session_maker = async_sessionmaker(dbengine.get(), expire_on_commit=False)
    async with session_maker() as session:
        return await _found(session)

async def ear_tag_used_by_other_animal(
    ear_tag_id: str,
    species,
    breed,
    exclude_internal_record_ids: set[str] | None = None,
    exclude_submission_ids: set[str] | None = None,
    search: tuple[str, ...] = ("register", "intake"),
) -> bool:
    """True if `ear_tag_id`, with this same species and breed, already
    belongs to a DIFFERENT animal — either already approved into the
    register, or drafted under some other in-progress intake submission.
    Mirrors the Old System's duplicate check ("same tag + same species +
    same breed already used"), which Gen2's Livestock Details section was
    missing entirely — see `_validate_no_duplicate_ear_tags` in
    G2PRegisterDomainServiceAnimal, the only caller.

    Scoped to the record being edited on BOTH tables:
    `exclude_internal_record_ids` should be every internal_record_id already
    present in the current save's own row list. This matters just as much
    for g2p_register_animals as for g2p_intake_form_animals — editing an
    already-approved animal's *other* fields (e.g. health_status) resubmits
    its unchanged ear_tag/species/breed, and without excluding its own
    internal_record_id that combination is always found "already registered"
    against itself, permanently blocking every edit to an approved animal
    that doesn't touch its ear tag. (Found via G2R-136 audit-log testing:
    editing Health Status on an already-approved animal raised
    "ear_tag_id ... is already registered to a different animal" even though
    nothing about the tag, species or breed had changed.)

    `exclude_submission_ids` / `search`: see exists_in_tables and
    exclude_own_rows below — the intake table is only searched from
    post_intake_upsert, where the submission being saved is known.
    """
    if is_blank(ear_tag_id):
        return False

    from sqlalchemy import and_

    G2PRegisterAnimal, G2PIntakeFormAnimal = _animal_models()

    def _same_animal_key(model):
        conditions = [
            model.ear_tag_id == ear_tag_id,
            model.species == species,
            model.breed == breed,
        ]
        exclude_own_rows(model, conditions, exclude_internal_record_ids, exclude_submission_ids)
        return and_(*conditions)

    return await exists_in_tables(G2PRegisterAnimal, G2PIntakeFormAnimal, _same_animal_key, search)

async def secondary_identifier_used_by_other_animal(
    secondary_identifier: str,
    species,
    breed,
    exclude_internal_record_ids: set[str] | None = None,
    exclude_submission_ids: set[str] | None = None,
    search: tuple[str, ...] = ("register", "intake"),
) -> bool:
    """Same check as ear_tag_used_by_other_animal, for `secondary_identifier`
    — the leg band/wing tag/hive number an _EAR_TAG_EXEMPT_SPECIES animal
    (poultry, beehive; see G2PRegisterDomainServiceAnimal) identifies by
    instead of an ear tag. Kept as a separate function rather than a
    parameterized field name so each stays a straightforward, obviously
    correct mirror of the other — see that function's docstring for why the
    exclude_internal_record_ids scoping (and its ear-tag-only caveat) matter.
    """
    if is_blank(secondary_identifier):
        return False

    from sqlalchemy import and_

    G2PRegisterAnimal, G2PIntakeFormAnimal = _animal_models()

    def _same_animal_key(model):
        conditions = [
            model.secondary_identifier == secondary_identifier,
            model.species == species,
            model.breed == breed,
        ]
        exclude_own_rows(model, conditions, exclude_internal_record_ids, exclude_submission_ids)
        return and_(*conditions)

    return await exists_in_tables(G2PRegisterAnimal, G2PIntakeFormAnimal, _same_animal_key, search)

def format_age(birth_date: date | None) -> str | None:
    """The Age display string for a date of birth ("2 years, 8 months"), the
    same way `g2p.livestock.registry.line._compute_age` did in the Odoo
    module. One implementation for the Animal row and for the event rows that
    copy the animal's age, so the two can never drift apart."""
    if birth_date is None:
        return None
    today = date.today()
    years = today.year - birth_date.year
    months = today.month - birth_date.month
    if today.day < birth_date.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    if years < 0:
        return None
    return f"{years} years, {months} months"


async def get_animal_species(ear_tag_id: str) -> str | None:
    """The species already recorded against ear_tag_id under Livestock
    Details, or None if the ear tag isn't known anywhere."""
    species, _ = await get_animal_species_and_birth_date(ear_tag_id)
    return species


async def get_animal_species_and_birth_date(ear_tag_id: str) -> tuple[str | None, date | None]:
    """The (species, date_of_birth) already recorded against ear_tag_id under
    Livestock Details, or (None, None) if the ear tag isn't known anywhere.
    Checks the approved register first (authoritative), then falls back to
    any in-progress intake draft.

    Same scoping caveat as ear_tag_exists: this is a global lookup by ear
    tag, not scoped to the farmer/record currently being edited.
    """
    if is_blank(ear_tag_id):
        return None, None

    from openg2p_fastapi_common.context import dbengine
    from sqlalchemy import and_, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    G2PRegisterAnimal, G2PIntakeFormAnimal = _animal_models()

    def _has_species(model):
        # Excluded in the WHERE clause, not just checked after fetching: the
        # same ear tag can legitimately appear on more than one row (repeat
        # test submissions, a farmer's animal re-entered in a later intake),
        # and without this an unordered .limit(1) can just as easily land on
        # a row where species was never filled in, making the result
        # nondeterministic — same ear tag, different answer between calls.
        return and_(animal_identified_by(model, ear_tag_id), model.species.is_not(None), model.species != "")

    session_maker = async_sessionmaker(dbengine.get(), expire_on_commit=False)
    async with session_maker() as session:
        for model in (G2PRegisterAnimal, G2PIntakeFormAnimal):
            row = (
                await session.execute(
                    select(model.species, model.date_of_birth)
                    .where(_has_species(model))
                    .order_by(model.created_at.desc())
                    .limit(1)
                )
            ).first()
            if row and row[0]:
                return row[0], parse_date(row[1])
        return None, None


async def humanize_attribute_value(value_id: str | None) -> str:
    """The human-readable label for a code-list value id (e.g.
    "LSTK_SHEEP" -> "SHEEP", a former "LIVESTOCK_SPECIES_SHEEP" likewise), for building a validation message
    a user can actually act on. Read from Master Data; falls back to the raw
    id if it isn't a known value (or is blank) — validation error text should
    never go silent just because a lookup came up empty.
    """
    if is_blank(value_id):
        return str(value_id)
    from ... import master_data_lists
    from .species_codes import canonical

    value = str(value_id).strip()
    label = await master_data_lists.display(value)
    if label == value:
        # A former species code is not in Master Data; label its type instead.
        label = await master_data_lists.display(canonical(value))
    return label


async def get_species_config(species_value_id: str | None) -> tuple[bool, bool]:
    """(requires_ear_tag, is_flock_species) for a species value (LIVESTOCK_TYPE,
    or a former registry code) — e.g. Chicken vs. Cattle/Sheep/... — configurable per species from
    Configuration > Attributes > Species (an Edit Attribute Value's "Requires
    Ear Tag" / "Flock / Group Species" checkboxes), not a hardcoded species
    list in this codebase. Backed by G2PLivestockSpeciesConfig, a
    per-value side table (see core-patches/apply_patches.py Fix 4) the same
    way G2PAttributeValueSchedule already backs per-value vaccine scheduling
    — most species (and every non-species attribute value) never get a row
    there, which is why every field on it is nullable.

    Defaults to (True, False) — ear-tag-required, not a flock — whenever no
    row exists (species left blank, a species nobody has configured yet, or
    a brand-new species just added in Configuration). That default matches
    every species' actual behavior before this table existed, so an
    unconfigured species behaves exactly as before rather than silently
    losing its ear-tag requirement.
    """
    requires_ear_tag, is_flock_species = True, False
    if is_blank(species_value_id):
        return requires_ear_tag, is_flock_species

    from openg2p_fastapi_common.context import dbengine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ..models.species_config import G2PLivestockSpeciesConfig

    from .species_codes import lookup_keys

    session_maker = async_sessionmaker(dbengine.get(), expire_on_commit=False)
    async with session_maker() as session:
        # Config rows are keyed by species value id: the Master Data code
        # first, then a former registry code an older row may be keyed by.
        config = None
        for key in lookup_keys(species_value_id):
            config = await session.get(G2PLivestockSpeciesConfig, key)
            if config is not None:
                break
        if config is None:
            return requires_ear_tag, is_flock_species
        if config.requires_ear_tag is not None:
            requires_ear_tag = config.requires_ear_tag
        if config.is_flock_species is not None:
            is_flock_species = config.is_flock_species
    return requires_ear_tag, is_flock_species


async def validate_species_matches(record: dict) -> None:
    """If both ear_tag_id and species are filled in on this record, species
    must match what's already recorded for that ear tag under Livestock
    Details. Either one left blank is skipped, not rejected — this only
    catches a genuine mismatch, not an incomplete row (other validators
    handle required-field checks).
    """
    ear_tag_id = record.get("ear_tag_id")
    species = record.get("species")
    if is_blank(ear_tag_id) or is_blank(species):
        return

    animal_species = await get_animal_species(str(ear_tag_id).strip())
    if animal_species is None:
        # Nothing recorded to compare against (e.g. species was never filled
        # in under Livestock Details for this animal) — ear_tag_exists
        # already rejects an ear tag that isn't real at all, so this is not
        # this check's job to also flag.
        return

    from .species_codes import same_species

    if not same_species(species, animal_species):
        entered_label = await humanize_attribute_value(species)
        actual_label = await humanize_attribute_value(animal_species)
        validation_error(
            f"species '{entered_label}' does not match animal '{ear_tag_id}', "
            f"which is recorded as '{actual_label}' under Livestock Details. "
            "Select the matching species, or check you entered the correct ear tag / identifier."
        )


# ─── Duplicate event checks (G2R-134) ────────────────────────────────────────
#
# The Old System refused to record the same event twice for one animal
# (`_check_duplicate_health_event` and friends in the Odoo module); Gen2 only
# had the ear-tag duplicate check above for the Animal section. The two
# helpers below give an event section the same two-layer check the Animal
# section already has: first within the rows of the current save, then
# against everything already in the register or drafted in any intake
# submission. What counts as "the same event" is decided by each section's
# own domain service, which passes the fields that must match. Used by the
# Health Event and Vaccination services here; the Vital Event (mortality /
# disease) and Breeding (21-day cycle) checks live in their own services
# with their own helpers.


def _event_models(register_mnemonic: str):
    """The (register, intake) model pair for an event section, e.g.
    "HealthEvent" -> (G2PRegisterHealthEvent, G2PIntakeFormHealthEvent).
    Imported through the "openg2p_registry_extensions" alias for the same
    reason _animal_models does: importing via "..models" would register a
    second copy of every table with SQLAlchemy.
    """
    import importlib

    models = importlib.import_module("openg2p_registry_extensions.register_domain.models")
    return (
        getattr(models, f"G2PRegister{register_mnemonic}"),
        getattr(models, f"G2PIntakeForm{register_mnemonic}"),
    )


def tables_for(record: dict) -> tuple[str, ...]:
    """Which tables a duplicate search may look in for this row, when the
    caller has not said (see exists_in_tables).

    A row that carries an internal_record_id names a record that already
    exists: a change request editing an approved record, or an intake row the
    platform will update in place. Excluding that id is enough to keep the
    search off the row itself, so both tables can be searched.

    A row without one is a dialog row of an intake save: every "Next" resends
    it with edit_action ADD, the platform inserts it under a fresh id and
    deletes the previous copy afterwards, so the submission's own earlier copy
    is sitting in the intake table with an id nothing in the payload can
    match. Only the register is searched here; post_intake_upsert then runs
    the intake half with the submission itself excluded.
    """
    return ("register", "intake") if record.get("internal_record_id") else ("register",)


def exclude_own_rows(model, conditions: list, exclude_internal_record_ids, exclude_submission_ids) -> None:
    """Keep a duplicate search from finding the record being saved.

    `exclude_internal_record_ids`: every internal_record_id the current save's
    own rows carry — an edit of an already-approved record (change request)
    resubmits its unchanged key fields with its id, and without this it is
    always found "already recorded" against itself.

    `exclude_submission_ids`: the intake submission being saved. Dialog rows
    (Animal, Health, Vaccination, Vital, Breeding) never carry an id back to
    the server: every "Next" resends them with edit_action ADD, the platform
    inserts them under fresh ids and deletes the previous copies afterwards
    (_upsert_intake_rows / _delete_missing_intake_rows). So the previous copy
    of the very same row sits in the intake table, under the same
    submission_id, at the moment the search runs — Previous then Next with
    no edits, or reopening a draft, must not trip over it. Intake rows carry
    submission_id; register rows don't, and never need this.
    """
    if exclude_internal_record_ids:
        conditions.append(model.internal_record_id.not_in(exclude_internal_record_ids))
    if exclude_submission_ids and hasattr(model, "submission_id"):
        conditions.append(model.submission_id.not_in(exclude_submission_ids))


async def exists_in_tables(register_model, intake_model, condition_of, search=("register", "intake")) -> bool:
    """True if `condition_of(model)` matches a row in any of the tables named
    by `search` ("register" and/or "intake"), checked in that order.

    Which tables to search depends on where the caller runs:
    - validate_domain_attributes gets the request rows with NO submission
      context, so it leaves the choice to tables_for(record) above: the
      register always, and the intake table only for a row whose own
      internal_record_id can be excluded from the search.
    - post_intake_upsert gets the freshly upserted ORM rows, which carry the
      submission_id, so the intake half runs there for every row.
    """
    from openg2p_fastapi_common.context import dbengine
    from sqlalchemy import exists, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    models = {"register": register_model, "intake": intake_model}
    session_maker = async_sessionmaker(dbengine.get(), expire_on_commit=False)
    async with session_maker() as session:
        for table in search:
            found = (await session.execute(select(exists().where(condition_of(models[table]))))).scalar()
            if found:
                return True
    return False


def intake_rows_as_records(rows: list) -> list[dict]:
    """The column values of upserted intake ORM rows as plain dicts, so the
    per-section duplicate check written for the request payload can run on
    them from post_intake_upsert."""
    from sqlalchemy import inspect

    return [
        {attr.key: getattr(row, attr.key, None) for attr in inspect(row).mapper.column_attrs}
        for row in rows
    ]


def submission_ids_of(rows: list) -> set[str]:
    return {str(row.submission_id) for row in rows if getattr(row, "submission_id", None)}


def first_repeated_key(records: list[dict], key_of) -> tuple | None:
    """The first key that appears on more than one row of this save, or
    None. `key_of(record)` returns the tuple that identifies an event for
    duplicate purposes, or None to leave that row out (e.g. ear tag or date
    not filled in yet — required-field checks own those).
    """
    seen: set[tuple] = set()
    for record in records:
        key = key_of(record)
        if key is None:
            continue
        if key in seen:
            return key
        seen.add(key)
    return None


async def event_already_recorded(
    register_mnemonic: str,
    match: dict,
    exclude_internal_record_ids: set[str] | None = None,
    within_days: tuple | None = None,
    exclude_submission_ids: set[str] | None = None,
    search: tuple[str, ...] = ("register", "intake"),
) -> bool:
    """True if an event with these same field values already exists for
    this section — approved into the register, or drafted under any intake
    submission.

    `match` maps column name -> value that must be equal (a None value
    matches IS NULL, so "no disease recorded" is a value in its own right,
    not a wildcard). `within_days=(column, date, days)` adds a date-window
    condition instead of an exact date, for rules like the Old System's
    "no second breeding event of the same type within 21 days".

    `exclude_internal_record_ids` must be every internal_record_id already
    present in the current save's own rows, for the same reason as in
    ear_tag_used_by_other_animal: editing an already-approved event
    resubmits its unchanged key fields, and without excluding its own row it
    would always be found "already recorded" against itself.

    `exclude_submission_ids` / `search`: see exists_in_tables and
    exclude_own_rows — the intake table is only searched from
    post_intake_upsert, where the submission being saved is known.
    """
    from datetime import timedelta

    from sqlalchemy import and_

    register_model, intake_model = _event_models(register_mnemonic)

    def _same_event(model):
        conditions = []
        for column, value in match.items():
            attribute = getattr(model, column)
            conditions.append(attribute.is_(None) if value is None else attribute == value)
        if within_days:
            column, on, days = within_days
            attribute = getattr(model, column)
            conditions.append(attribute.between(on - timedelta(days=days), on + timedelta(days=days)))
        exclude_own_rows(model, conditions, exclude_internal_record_ids, exclude_submission_ids)
        return and_(*conditions)

    return await exists_in_tables(register_model, intake_model, _same_event, search)


def resolve_today_default(record: dict, field: str) -> None:
    """Replace the literal string "today" in a date field with today's date.

    The intake form gives some date widgets `"widget-data-default": "today"`
    (Animal registration_date, Vaccination vaccination_date). The platform's
    staff-ui resolves that token for what it displays, but if the user leaves
    the picker untouched the submitted value is the literal "today", which
    the database rejects ("invalid input for query argument ... 'today'") and
    the whole section save fails with UNEXPECTED_ERROR. Resolving it here on
    save keeps the default meaningful for the UI and for API clients alike.
    """
    value = record.get(field)
    if isinstance(value, str) and value.strip().lower() == "today":
        record[field] = date.today().isoformat()


def live_rows(records: list[dict]) -> list[dict]:
    """The rows of a section save that stay in the record.

    A dialog row the user removed on screen is still sent with the save, with
    edit_action DELETE, so the platform can drop it. It is not part of the
    record any more and must not be validated: checked with the rest, a
    removed animal and its re-added replacement were reported as "more than
    one animal" with the same ear tag, and a removed health or vaccination row
    as an event "already recorded" (G2R-229 points 9 and 27, G2R-232 point 3).
    """
    return [r for r in records if str(r.get("edit_action") or "").strip().upper() != "DELETE"]


async def attribute_value_known(value_id, attribute_id: str) -> bool:
    """True when value_id is a value of the code list attribute_id in Master
    Data, or when that list cannot be read or is empty: an unreadable list
    must not block every save, it only means this check cannot be made. (The
    platform's own coded-value validation, registry_*_validate_attribute_values,
    rejects unknown values too; this check names the field in the message.)"""
    if is_blank(value_id):
        return True
    try:
        from ... import master_data_lists

        values = await master_data_lists.get_values(attribute_id)
        if not values:
            return True
        wanted = str(value_id).strip()
        return any(v.get("value_id") == wanted for v in values)
    except Exception:
        return True


async def validate_known_code(record: dict, field: str, attribute_id: str, label: str) -> None:
    """The value in `field` must be a value of the Master Data code list
    `attribute_id`. The platform's coded-value validation is switched off
    (it reloads every attribute value of the shared Master Data on each save),
    so the lists that ODK, File Import or API rows can carry free text into
    are checked here, one list at a time. Blank is left to the required-field
    checks."""
    value = record.get(field)
    if is_blank(value):
        return
    if not await attribute_value_known(value, attribute_id):
        validation_error(f"{label} '{value}' is not in the {label.lower()} list.")


async def attribute_value_parent(value_id) -> str | None:
    """The species a breed or vaccine belongs to (e.g. BREED_CATTLE_BORAN ->
    LSTK_CATTLE, VACCINE_TYPE_ANTHRAX_CATTLE -> LSTK_CATTLE): the value's
    ``species`` attribute in the Master Data catalogue, or its parent value.
    None when the value is unknown or names no species."""
    if is_blank(value_id):
        return None
    from ... import master_data_lists

    return await master_data_lists.species_of(str(value_id).strip())


async def validate_belongs_to_species(value_id, species, kind: str) -> None:
    """Reject a breed/vaccine whose catalogue parent is a DIFFERENT species
    than the one on the record. The dialogs show the full breed and vaccine
    lists on the official staff-ui (no species filter inside a pop-up), so
    this is what keeps a goat breed off a cattle record. A value without a
    parent in the catalogue is left alone."""
    if is_blank(value_id) or is_blank(species):
        return
    from .species_codes import same_species

    parent = await attribute_value_parent(value_id)
    if parent is None or same_species(parent, species):
        return
    value_label = await humanize_attribute_value(value_id)
    parent_label = await humanize_attribute_value(parent)
    species_label = await humanize_attribute_value(species)
    validation_error(
        f"{kind} '{value_label}' belongs to species '{parent_label}', not '{species_label}'."
    )


def first_application_reference(records: list[dict]) -> str | None:
    """The submission reference shared by this save's already-saved rows, if
    any. Rows the platform has stored once carry `application_reference`
    (one per submission); brand-new rows don't. Used to scope ear-tag checks
    to the submission being edited."""
    for record in records:
        value = record.get("application_reference")
        if not is_blank(value):
            return str(value).strip()
    return None


async def fill_species_and_age_from_animal(record: dict) -> None:
    """Copy the animal's species (when the row has none) and its current age
    onto an event row that names an ear tag.

    The event dialogs show Species and Age as read-only fields that the
    original ear-tag dropdown filled in from the selected Animal row; with
    the ear tag typed in as text on the official staff-ui nothing fills them,
    so species would fail the required-field check and age would stay blank
    in the section table and the register. Both values are derived from the
    animal, so filling them here is authoritative; validate_species_matches
    still rejects an explicit species mismatch. Age is always recomputed
    (it is a display value as of today, not independent input).
    """
    if is_blank(record.get("ear_tag_id")):
        return
    animal_species, birth_date = await get_animal_species_and_birth_date(str(record["ear_tag_id"]).strip())
    if animal_species and is_blank(record.get("species")):
        record["species"] = animal_species
    if birth_date is not None:
        record["age"] = format_age(birth_date)


async def get_animal_gender(ear_tag_id: str) -> str | None:
    """The gender already recorded against ear_tag_id under Livestock
    Details, or None if the ear tag isn't known anywhere or has no gender on
    file yet. Same lookup shape as get_animal_species_and_birth_date (register
    first, then any in-progress intake draft; same "same ear tag, different
    answer between calls" ordering guard) -- used by Breeding to reject
    logging a breeding event against a male animal (see
    G2PRegisterDomainServiceBreeding._validate_female_only).
    """
    if is_blank(ear_tag_id):
        return None

    from openg2p_fastapi_common.context import dbengine
    from sqlalchemy import and_, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    G2PRegisterAnimal, G2PIntakeFormAnimal = _animal_models()

    def _has_gender(model):
        return and_(animal_identified_by(model, ear_tag_id), model.gender.is_not(None), model.gender != "")

    session_maker = async_sessionmaker(dbengine.get(), expire_on_commit=False)
    async with session_maker() as session:
        for model in (G2PRegisterAnimal, G2PIntakeFormAnimal):
            row = (
                await session.execute(
                    select(model.gender)
                    .where(_has_gender(model))
                    .order_by(model.created_at.desc())
                    .limit(1)
                )
            ).first()
            if row and row[0]:
                return row[0]
        return None


async def ensure_ear_tags_belong_to_submission(rows: list) -> None:
    """post_intake_upsert companion to ear_tag_exists(): the platform hands
    validate_domain_attributes a brand-new row with NO submission context, so
    that check can only be global there — a real ear tag from a *different*
    farmer's animals passes it. Right after the upsert the same rows are ORM
    objects carrying the submission's application_reference, so here the check
    can finally be scoped: every event row's animal must be drafted in, or
    already approved from, this very submission. Raising here returns 400 and
    rolls the section save back (the platform commits later)."""
    for row in rows:
        ear_tag_id = getattr(row, "ear_tag_id", None)
        reference = getattr(row, "application_reference", None)
        if is_blank(ear_tag_id) or is_blank(reference):
            continue
        if not await ear_tag_exists(str(ear_tag_id).strip(), application_reference=str(reference)):
            validation_error(
                f"'{str(ear_tag_id).strip()}' is not an animal of this submission. "
                "Add the animal under Livestock Details first, or check the ear tag / identifier."
            )


def compose_farmer_name(first=None, middle=None, last=None) -> str | None:
    """The farmer's display name from the parts the form collects (First /
    Middle / Last Name), or None when all three are blank. farmer_name is the
    Farmer register's display name, a dedup field, and what the Livestock
    record mirrors, yet nothing composed it once the intake form stopped
    carrying a farmer_name field of its own."""
    parts = [str(p).strip() for p in (first, middle, last) if not is_blank(p)]
    return " ".join(parts) or None


async def sync_farmer_identity_to_livestock(session, application_reference, *, farmer=None, livestock_rows=None) -> int:
    """Mirror the farmer's identity (farmer_id, fayda_fan_id, farmer_name) from
    the submission's Farmer intake row onto its Livestock intake row(s),
    overwriting whatever they hold. Returns how many livestock rows changed.

    Overwrite, not fill-blank-only: on a reopened draft the operator may
    correct the Farmer ID / name in the Farmer section, and the Livestock row
    must follow or the engine scores the wrong farmer. That is safe because
    no section of the intake form edits these three fields on the Livestock
    row any more (the old "Farmer Details" section survives only on the
    register's view tab), and it matches the approval-time copy in
    G2PRegisterDomainServiceLivestock._sync_farmer_identity, which also
    overwrites unconditionally.

    Why: the deduplication engine scores only the main (Livestock) register —
    the Farmer section embedded in the Livestock intake form is never scored —
    and the Livestock dedup schema is farmer_id / fayda_fan_id / farmer_name /
    woreda. Until 2026-09-14 the intake form's "Farmer Details" section wrote
    those three fields onto the Livestock row; it was removed as redundant for
    the operator, after which the Livestock row carried only woreda at intake
    (score ~10 of a 55 threshold) and no duplicate was ever flagged until the
    identity was copied at approval. Doing that copy at intake restores the
    engine's input without bringing the section back.

    Called from post_intake_upsert of BOTH domain services, so the copy happens
    whichever section is saved first: the Farmer section (farmer row given,
    livestock rows looked up) or a Livestock-register section (livestock rows
    given, farmer row looked up). Runs inside the section-save transaction.
    """
    import importlib

    from sqlalchemy import select

    if is_blank(application_reference):
        return 0
    models = importlib.import_module("openg2p_registry_extensions.register_domain.models")
    Farmer, Livestock = models.G2PIntakeFormFarmer, models.G2PIntakeFormLivestock
    if farmer is None:
        farmer = (
            await session.execute(select(Farmer).where(Farmer.application_reference == application_reference))
        ).scalars().first()
    if farmer is None:
        return 0
    if livestock_rows is None:
        livestock_rows = (
            await session.execute(select(Livestock).where(Livestock.application_reference == application_reference))
        ).scalars().all()
    full_name = farmer.farmer_name
    if is_blank(full_name):
        full_name = compose_farmer_name(farmer.first_name, farmer.middle_name, farmer.last_name)
    changed = 0
    for row in livestock_rows:
        touched = False
        for field, value in (("farmer_id", farmer.farmer_id), ("fayda_fan_id", farmer.fayda_fan_id), ("farmer_name", full_name)):
            new = None if is_blank(value) else str(value).strip()
            current = getattr(row, field, None)
            current = None if is_blank(current) else str(current).strip()
            if current != new:
                setattr(row, field, new)
                touched = True
        changed += int(touched)
    if changed:
        await session.flush()
    return changed


_LIVESTOCK_FORM_REGISTER_ID = "997676d3-7008-59f9-b23e-613ad79bbb08"


async def ensure_livestock_intake_row(session, farmer) -> bool:
    """Give a Livestock intake submission its Livestock row as soon as the
    Farmer section is saved. Return True when one was created.

    Only the Survey Personnel, Location and Livestock record sections write
    the submission's Livestock (parent) row, and all three are optional. An
    operator who skipped Survey Personnel and Location had no parent row, so
    the first animal failed with "No parent records exist in this submission;
    cannot link child row for register 'Animal'". The platform keeps one
    Livestock row per submission and the form reads it back by submission,
    so a later Survey Personnel or Location save updates this row instead of
    adding a second one.
    """
    import importlib
    import uuid
    from datetime import datetime

    from sqlalchemy import select

    submission_id = getattr(farmer, "submission_id", None)
    if not submission_id:
        return False
    models = importlib.import_module("openg2p_registry_extensions.register_domain.models")
    core_models = importlib.import_module("openg2p_registry_core.models")
    Livestock = models.G2PIntakeFormLivestock
    Submission = core_models.G2PIntakeFormSubmission
    register_id = (
        await session.execute(select(Submission.register_id).where(Submission.submission_id == submission_id))
    ).scalar()
    if str(register_id or "") != _LIVESTOCK_FORM_REGISTER_ID:
        return False  # the Farmer register's own form has no Livestock row
    exists = (
        await session.execute(select(Livestock.internal_record_id).where(Livestock.submission_id == submission_id))
    ).first()
    if exists:
        return False
    actor = getattr(farmer, "created_by", None) or "system"
    now = datetime.now()
    session.add(Livestock(
        submission_id=submission_id,
        application_reference=getattr(farmer, "application_reference", None),
        internal_record_id=str(uuid.uuid4()),
        created_by=actor,
        created_at=now,
        last_approved_at=now,
        last_approved_by=actor,
        record_status="ACTIVE",
    ))
    await session.flush()
    return True
