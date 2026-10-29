"""The livestock code lists and geography, read from Master Data.

registry-platform 1.2 keeps no copy of a country's code lists: species, breed,
vaccine, disease and the like live in the Master Data service and the platform
reads them over HTTP (openg2p_registry_core.helpers.master_data.MasterDataClient,
settings ``registry_<app>_master_data_api_url`` / ``_token_url`` /
``_client_id`` / ``_client_secret``). The platform's own client only exposes
the set of codes per list, which is what its validator needs. The livestock
rules also need a value's label (for a message a user can act on), its parent
(a breed's or vaccine's species) and the values of one list (ODK
normalisation), so this module fetches every value once and serves those from
a cache with the platform's ``master_data_cache_seconds`` lifetime.

Every lookup degrades the way the old table lookups did: a Master Data that is
not configured or not reachable yields "unknown" (``None`` / the raw id), never
an exception out of a validation.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

_logger = logging.getLogger("livestock-master-data")

_ATTRIBUTE_PAGE_SIZE = 500
_ATTRIBUTE_MAX_PAGES = 50

_values_by_id: dict[str, dict[str, Any]] = {}
_values_by_attribute: dict[str, list[dict[str, Any]]] = {}
_values_expire_at = 0.0
_values_loaded = False


def _client():
    from openg2p_registry_core.helpers.master_data import MasterDataClient

    client = MasterDataClient.get_component()
    if client is None:
        client = MasterDataClient()
    return client


def _ttl(client) -> float:
    try:
        return float(client._cache_ttl(client._settings()))
    except Exception:
        return 300.0


def is_configured() -> bool:
    try:
        client = _client()
        return bool(client.is_configured(client._settings()))
    except Exception:
        return False


def invalidate() -> None:
    global _values_by_id, _values_by_attribute, _values_expire_at, _values_loaded
    _values_by_id = {}
    _values_by_attribute = {}
    _values_expire_at = 0.0
    _values_loaded = False
    _value_attributes.clear()
    _value_attributes_expire_at.clear()


async def _load_values() -> None:
    """Fetch every attribute value once; served from the cache until the TTL."""
    global _values_by_id, _values_by_attribute, _values_expire_at, _values_loaded
    now = time.monotonic()
    if _values_loaded and now < _values_expire_at:
        return

    client = _client()
    if not client.is_configured(client._settings()):
        if not _values_loaded:
            _logger.warning("Master Data is not configured; code-list lookups return unknown")
        _values_loaded = True
        _values_expire_at = now + 30.0
        return

    by_id: dict[str, dict[str, Any]] = {}
    by_attribute: dict[str, list[dict[str, Any]]] = {}
    for page in range(1, _ATTRIBUTE_MAX_PAGES + 1):
        payload = await client._post(
            "/attributes/get_attribute_values",
            payload={"attribute_id": None},
            pagination={"current_page": page, "page_size": _ATTRIBUTE_PAGE_SIZE},
        )
        if isinstance(payload, dict):
            rows = payload.get("attribute_values") or []
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            value_id = row.get("value_id")
            attribute_id = row.get("attribute_id")
            if not value_id or not attribute_id:
                continue
            entry = {
                "value_id": value_id,
                "attribute_id": attribute_id,
                "value_code": row.get("value_code"),
                "value_display": row.get("value_display"),
                "parent_value_id": row.get("parent_value_id") or None,
                "sort_order": row.get("sort_order"),
            }
            by_id[value_id] = entry
            by_attribute.setdefault(attribute_id, []).append(entry)
        if len(rows) < _ATTRIBUTE_PAGE_SIZE:
            break

    for rows in by_attribute.values():
        rows.sort(key=lambda r: (r.get("sort_order") is None, r.get("sort_order") or 0, r["value_id"]))

    _values_by_id = by_id
    _values_by_attribute = by_attribute
    _values_loaded = True
    ttl = _ttl(client)
    _values_expire_at = now + ttl if ttl > 0 else now + 300.0


async def get_value(value_id: str | None) -> Optional[dict[str, Any]]:
    if not value_id:
        return None
    try:
        await _load_values()
    except Exception as exc:
        _logger.warning("Master Data attribute values unavailable: %s", exc)
        return _values_by_id.get(str(value_id).strip())
    return _values_by_id.get(str(value_id).strip())


async def get_values(attribute_id: str) -> list[dict[str, Any]]:
    """All values of one list, in display order (empty when unknown)."""
    try:
        await _load_values()
    except Exception as exc:
        _logger.warning("Master Data attribute values unavailable: %s", exc)
    return list(_values_by_attribute.get(attribute_id, []))


async def display(value_id: str | None) -> str:
    """The label of a value id, or the id itself when it is not a known value."""
    entry = await get_value(value_id)
    if entry and entry.get("value_display"):
        return str(entry["value_display"])
    return str(value_id)


async def parent(value_id: str | None) -> Optional[str]:
    """parent_value_id of a value, None when unknown or none."""
    entry = await get_value(value_id)
    return (entry or {}).get("parent_value_id") or None


# list code -> {value code -> attributes}, from the catalogue API.
_value_attributes: dict[str, dict[str, dict[str, Any]]] = {}
_value_attributes_expire_at: dict[str, float] = {}
_CATALOGUE_PAGE_SIZE = 500


async def list_attributes(list_code: str | None) -> dict[str, dict[str, Any]]:
    """{value code: catalogue attributes} for every value of one list, empty
    when the list is unknown or Master Data cannot be read.

    /attributes/get_attribute_values, which the platform and ``_load_values``
    read, returns a value's code, label, parent and order only. A country
    pack keeps a breed's or vaccine's species as a value attribute, which the
    Master Data catalogue API (/catalogue/get_list_values) returns. A list is
    fetched once and cached for the platform's Master Data cache lifetime.
    """
    if not list_code:
        return {}
    now = time.monotonic()
    if list_code not in _value_attributes or now >= _value_attributes_expire_at.get(list_code, 0.0):
        client = _client()
        attributes: dict[str, dict[str, Any]] = {}
        try:
            if not client.is_configured(client._settings()):
                return {}
            for page in range(1, _ATTRIBUTE_MAX_PAGES + 1):
                payload = await client._post(
                    "/catalogue/get_list_values",
                    payload={"list_code": list_code, "version": "latest", "include_retired": False},
                    pagination={"current_page": page, "page_size": _CATALOGUE_PAGE_SIZE},
                )
                rows = payload.get("values") if isinstance(payload, dict) else None
                for row in rows or []:
                    code = row.get("value_code") or row.get("value_id")
                    if code:
                        attributes[code] = row.get("attributes") or {}
                if not rows or len(rows) < _CATALOGUE_PAGE_SIZE:
                    break
        except Exception as exc:
            _logger.warning("Master Data catalogue values unavailable for %s: %s", list_code, exc)
            return {}
        _value_attributes[list_code] = attributes
        ttl = _ttl(client)
        _value_attributes_expire_at[list_code] = now + (ttl if ttl > 0 else 300.0)
    return _value_attributes.get(list_code, {})


async def value_attributes(list_code: str | None, value_id: str | None) -> dict[str, Any]:
    """The catalogue attributes of one value (e.g. a breed's or a vaccine's
    ``species``), empty when the value or the list is unknown."""
    if not value_id:
        return {}
    return dict((await list_attributes(list_code)).get(str(value_id).strip(), {}))


async def species_has_values(list_code: str, species: str | None, same_species) -> bool:
    """True when some value of ``list_code`` belongs to ``species`` (its
    ``species`` attribute names the same type, compared with
    ``same_species``). False when none does or the list cannot be read."""
    if not species:
        return False
    return any(same_species(attrs.get("species"), species) for attrs in (await list_attributes(list_code)).values())


async def species_of(value_id: str | None) -> Optional[str]:
    """The species a breed or vaccine belongs to: its ``species`` attribute in
    the catalogue, else its parent value (for lists that model the species as
    the parent). None when the value names no species."""
    entry = await get_value(value_id)
    if not entry:
        return None
    attributes = await value_attributes(entry.get("attribute_id"), value_id)
    return attributes.get("species") or entry.get("parent_value_id") or None


_geo_label_cache: dict[str, str] = {}


async def geo_label(level_value_id: str | None) -> Optional[str]:
    """The display name of a geo unit id (``ET010101101001`` -> ``Kitinbille``),
    None when Master Data does not know it."""
    if not level_value_id:
        return None
    key = str(level_value_id).strip()
    if key in _geo_label_cache:
        return _geo_label_cache[key]
    try:
        client = _client()
        index = await client._load_geo_index()
    except Exception as exc:
        _logger.warning("Master Data geo index unavailable: %s", exc)
        return None
    node = index.get(key) if index else None
    label = (node or {}).get("level_value_mnemonic") or None
    if label:
        _geo_label_cache[key] = label
    return label


async def geo_label_by_suffix(code: str) -> Optional[str]:
    """Last-resort match of a bare code against the end of every geo value id.
    Returns a name only when exactly one value ends with the code."""
    if not code:
        return None
    try:
        index = await _client()._load_geo_index()
    except Exception as exc:
        _logger.warning("Master Data geo index unavailable: %s", exc)
        return None
    suffix = str(code).strip().lower()
    matches = [n for vid, n in (index or {}).items() if str(vid).lower().endswith(suffix)]
    if len(matches) == 1:
        return matches[0].get("level_value_mnemonic") or None
    return None


async def geo_id_for(level: str, name: str | None, parent_id: str | None = None,
                     parent_name: str | None = None) -> Optional[str]:
    """The geo value id of ``name`` at ``level`` (region / zone / woreda /
    kebele), under the given parent when one is known; None unless exactly one
    value matches. A parent is matched by id or, for a Master Data whose
    values point at their parent by name, by that name."""
    if not name or not str(name).strip():
        return None
    try:
        index = await _client()._load_geo_index()
    except Exception as exc:
        _logger.warning("Master Data geo index unavailable: %s", exc)
        return None
    want = str(name).strip().casefold()
    matches = [
        vid for vid, node in (index or {}).items()
        if (node.get("level_mnemonic") or "").casefold() == level
        and (node.get("level_value_mnemonic") or "").strip().casefold() == want
    ]
    if len(matches) > 1 and (parent_id or parent_name):
        parents = {p for p in (parent_id, parent_name) if p}
        matches = [vid for vid in matches if index[vid].get("parent_level_value_id") in parents]
    return matches[0] if len(matches) == 1 else None
