"""Species codes: the Master Data livestock types, and the codes saved before them.

The species field is bound to the Master Data list LIVESTOCK_TYPE, the Gen1
livestock types: cattle, camel, sheep, goat, chicken, donkey, horse, mule
(LSTK_CATTLE ... LSTK_MULE). Breeds (LIVESTOCK_BREED_NAME) and vaccines
(VACCINE_TYPE) name their species with the same codes in their ``species``
attribute.

Records saved before this change hold the registry's former species codes
(LIVESTOCK_SPECIES_CATTLE, ..., LIVESTOCK_SPECIES_POULTRY). Those rows are not
rewritten; every comparison between two species goes through ``canonical``
so an old row and a new one name the same animal type. New saves are checked
against LIVESTOCK_TYPE by the platform's coded-value validation, so only the
Master Data codes are written from now on.
"""

from __future__ import annotations

MASTER_DATA_LIST = "LIVESTOCK_TYPE"

_FORMER_PREFIX = "LIVESTOCK_SPECIES_"

# Former registry code (without its prefix) -> LIVESTOCK_TYPE code. Poultry
# was the registry's name for Gen1's chicken. Pig and beehive have no Gen1 or
# Master Data type and are kept as they are.
_FORMER_TO_TYPE = {
    "CATTLE": "LSTK_CATTLE",
    "CAMEL": "LSTK_CAMEL",
    "SHEEP": "LSTK_SHEEP",
    "GOAT": "LSTK_GOAT",
    "POULTRY": "LSTK_CHICKEN",
    "DONKEY": "LSTK_DONKEY",
    "HORSE": "LSTK_HORSE",
    "MULE": "LSTK_MULE",
}


def canonical(value) -> str | None:
    """The LIVESTOCK_TYPE code for a species value, old or new; None when blank.

    A value that is neither a former registry code nor a known type is
    returned trimmed and unchanged, so an unknown species still compares
    equal only to itself.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    upper = text.upper()
    if upper.startswith(_FORMER_PREFIX):
        return _FORMER_TO_TYPE.get(upper[len(_FORMER_PREFIX):], text)
    return text


def same_species(first, second) -> bool:
    """True when both values name the same livestock type (old or new codes)."""
    a, b = canonical(first), canonical(second)
    return a is not None and a == b


def lookup_keys(value) -> list[str]:
    """Keys to try, in order, for a table keyed by species value id: the
    Master Data code first, then the value as saved (a former code)."""
    keys: list[str] = []
    for key in (canonical(value), None if value is None else str(value).strip()):
        if key and key not in keys:
            keys.append(key)
    return keys
