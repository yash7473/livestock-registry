"""Species and breed come from the Master Data lists LIVESTOCK_TYPE and
LIVESTOCK_BREED_NAME; records saved with the former registry codes still
compare as the same livestock type."""
import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path

EXT = Path(__file__).parents[1] / "src" / "openg2p_registry_livestock_extension"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = sys.modules[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


species = _load("species_codes", EXT / "register_domain" / "services" / "species_codes.py")
lists = _load("livestock_master_data_lists", EXT / "master_data_lists.py")


def test_former_codes_map_to_livestock_types():
    assert species.canonical("LIVESTOCK_SPECIES_CATTLE") == "LSTK_CATTLE"
    assert species.canonical("LIVESTOCK_SPECIES_POULTRY") == "LSTK_CHICKEN"
    assert species.canonical(" LSTK_GOAT ") == "LSTK_GOAT"
    assert species.canonical("LIVESTOCK_SPECIES_BEEHIVE") == "LIVESTOCK_SPECIES_BEEHIVE"
    assert species.canonical("") is None and species.canonical(None) is None


def test_old_and_new_codes_name_the_same_animal():
    assert species.same_species("LIVESTOCK_SPECIES_SHEEP", "LSTK_SHEEP")
    assert not species.same_species("LSTK_SHEEP", "LSTK_GOAT")
    assert not species.same_species(None, None)


def test_species_config_tries_the_type_code_first():
    assert species.lookup_keys("LIVESTOCK_SPECIES_POULTRY") == ["LSTK_CHICKEN", "LIVESTOCK_SPECIES_POULTRY"]
    assert species.lookup_keys("LSTK_CATTLE") == ["LSTK_CATTLE"]
    assert species.lookup_keys(None) == []


class _FakeClient:
    """The two Master Data endpoints the helper reads."""

    calls = []

    def is_configured(self, _settings):
        return True

    def _settings(self):
        return None

    def _cache_ttl(self, _settings):
        return 300

    async def _post(self, path, payload=None, pagination=None):
        self.calls.append(path)
        if path == "/attributes/get_attribute_values":
            return {"attribute_values": [
                {"attribute_id": "LIVESTOCK_BREED_NAME", "value_id": "BREED_CATTLE_BORAN",
                 "value_code": "BREED_CATTLE_BORAN", "value_display": "Boran", "parent_value_id": None},
                {"attribute_id": "LEGACY", "value_id": "CHILD", "value_display": "Child",
                 "parent_value_id": "PARENT"},
            ]}
        if path == "/catalogue/get_list_values":
            self.calls.append(payload["list_code"])
            if payload["list_code"] != "LIVESTOCK_BREED_NAME":
                return {"values": []}
            return {"values": [{"value_code": "BREED_CATTLE_BORAN",
                                "attributes": {"species": "LSTK_CATTLE", "breed_type": "Indigenous"}}]}
        raise AssertionError(path)


def test_breed_species_comes_from_the_catalogue_attribute(monkeypatch):
    lists.invalidate()
    monkeypatch.setattr(lists, "_client", lambda: _FakeClient())
    assert asyncio.run(lists.species_of("BREED_CATTLE_BORAN")) == "LSTK_CATTLE"
    assert asyncio.run(lists.species_of("CHILD")) == "PARENT"
    assert asyncio.run(lists.species_of("UNKNOWN")) is None
    asyncio.run(lists.species_of("BREED_CATTLE_BORAN"))
    assert _FakeClient.calls.count("LIVESTOCK_BREED_NAME") == 1  # cached per list


def _bindings():
    found = {}
    for path in sorted((EXT / "meta_data" / "register-metadata").glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'"widget-data-path": "(species|breed)", "widget-data-source": \{[^}]*?"params": \{"attribute_id": "([A-Z_]+)"', text):
            found.setdefault(match.group(1), set()).add(match.group(2))
    return found


def test_sections_bind_species_and_breed_to_the_master_data_lists():
    # The upgrade patch names the former bindings on purpose (it replaces them).
    text = "\n".join(p.read_text(encoding="utf-8") for p in (EXT / "meta_data" / "register-metadata").glob("*.sql")
                     if p.name != "patch_ls_species_breed_master_data_lists.sql")
    assert '"attribute_id": "LIVESTOCK_SPECIES"' not in text
    assert '"attribute_id": "LIVESTOCK_BREED"' not in text
    bindings = _bindings()
    assert bindings.get("species") == {"LIVESTOCK_TYPE"}
    assert bindings.get("breed") == {"LIVESTOCK_BREED_NAME"}


def test_vaccine_schedule_lookup_reads_new_species_codes():
    source = (EXT / "register_domain" / "services" / "g2p_register_domain_service_vaccination.py").read_text(encoding="utf-8")
    assert '"LSTK_"' in source and '"POULTRY" if text == "CHICKEN"' in source
    json.dumps(source[:1])  # the file is text, not a template


def test_breed_is_required_only_for_species_with_breeds(monkeypatch):
    lists.invalidate()
    monkeypatch.setattr(lists, "_client", lambda: _FakeClient())
    has = lambda s: asyncio.run(lists.species_has_values("LIVESTOCK_BREED_NAME", s, species.same_species))
    assert has("LSTK_CATTLE")
    assert has("LIVESTOCK_SPECIES_CATTLE")  # a former code names the same type
    assert not has("LSTK_CHICKEN")
    assert not has(None)


def test_form_leaves_breed_optional():
    text = (EXT / "meta_data" / "register-metadata" / "patch_ls_animal_details_sync_ui_schema.sql").read_text(encoding="utf-8")
    breed = re.search(r'"column-key": "breed".*?"widget-required": (true|false)', text)
    assert breed and breed.group(1) == "false"
