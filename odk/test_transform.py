"""Render ls_odk_transform.j2 against real-shaped ODK submissions and check the
values the registry depends on. Needs only jinja2:

    python odk/test_transform.py

odk/sample_submissions.json holds five submissions of the livestock_registry
form as ODK Central's OData API returns them (repeat groups inlined), with
Central's __system bookkeeping removed. Every check below is a bug that was
found on the dev registry's ODK data on 2026-10-09 (raw disease choices,
offspring sex defaulting to FEMALE, lost vital-event notes, breeding location
collapsing to OTHER, lower-case farmer gender, records entering as CONFIRMED).
"""
import json
import os
import sys

import jinja2

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(
    HERE, "..", "livestock-extension", "src", "openg2p_registry_livestock_extension",
    "templates", "ls_odk_transform.j2",
)
SAMPLE = os.path.join(HERE, "sample_submissions.json")

SECTIONS = (
    "ls_farmer_identity", "ls_farmer_location", "ls_survey_personnel",
    "ls_livestock_record", "ls_livestock_location", "ls_animal_details",
    "ls_health_event_details", "ls_vaccination_details",
    "ls_vital_event_details", "ls_breeding_details",
)
LOCATIONS = {"HOME", "VETERINARY", "MARKET", "FIELD", "QUARANTINE_CENTER", "OTHER"}


def render(template, submission):
    return json.loads(template.render(expanded=submission))


def check(cond, message, failures):
    if not cond:
        failures.append(message)


def main():
    template = jinja2.Template(open(TEMPLATE, encoding="utf-8").read())
    subs = json.load(open(SAMPLE, encoding="utf-8"))["value"]
    failures = []
    for sub in subs:
        out = render(template, sub)
        name = out["ls_farmer_identity"][0]["farmer_name"]
        for s in SECTIONS:
            check(s in out and isinstance(out[s], list), f"{name}: section {s} missing", failures)
        farmer = out["ls_farmer_identity"][0]
        check(farmer["gender"] in (None, "MALE", "FEMALE", "OTHER"), f"{name}: farmer gender {farmer['gender']!r} not upper-case", failures)
        record = out["ls_livestock_record"][0]
        check(record["state"] == "DRAFT", f"{name}: livestock record state {record['state']!r}, expected DRAFT (the approval ladder starts there)", failures)
        for a in out["ls_animal_details"]:
            check("state" not in a, f"{name}: animal row carries a state ({a.get('state')!r})", failures)
            check(a["species"].startswith("LSTK_"), f"{name}: species {a['species']!r} is not a LIVESTOCK_TYPE code", failures)
            check(a["gender"] in ("MALE", "FEMALE", "MIXED"), f"{name}: animal gender {a['gender']!r}", failures)
        for h in out["ls_health_event_details"]:
            d = h["disease_type"]
            check(d is None or d.startswith("LIVESTOCK_DISEASE_"), f"{name}: health disease {d!r} not a registry code", failures)
            check(h["event_type"] == "DISEASE" or d is None, f"{name}: {h['event_type']} health event carries disease {d!r}", failures)
            check(h["location"] in LOCATIONS, f"{name}: health location {h['location']!r}", failures)
        for v in out["ls_vaccination_details"]:
            check(v["vaccine_type"].startswith("VACCINE_TYPE_"), f"{name}: vaccine {v['vaccine_type']!r} not a registry code", failures)
        for e in out["ls_vital_event_details"]:
            d = e["disease_type"]
            check(d is None or d.startswith("LIVESTOCK_DISEASE_"), f"{name}: vital disease {d!r} not a registry code", failures)
            if e["event_type"] == "BIRTH":
                check(e["offspring_gender"] in ("MALE", "FEMALE", "MIXED"), f"{name}: birth offspring_gender {e['offspring_gender']!r}", failures)
            else:
                check(e["offspring_gender"] is None, f"{name}: {e['event_type']} event has offspring_gender {e['offspring_gender']!r}", failures)
            check(e["location"] in LOCATIONS, f"{name}: vital location {e['location']!r}", failures)
        for b in out["ls_breeding_details"]:
            check(b["event_type"] in ("AI", "NATURAL"), f"{name}: breeding type {b['event_type']!r}", failures)
            check(b["location"] in LOCATIONS, f"{name}: breeding location {b['location']!r}", failures)

    # Targeted cases on top of the real data: every form choice must map.
    base = json.loads(json.dumps(subs[0]))
    animal = base["livestock"][0]
    animal["vital_events"] = [{"vital_event_type": "disease", "date": "2026-10-01",
                               "disease_details": {"disease_type": d}} for d in (
        "contagious_bovine_pleuropneumonia", "foot_and_mouth_disease", "gumboro", "bovine_tuberculosis",
        "sheep_and_goat_pox", "lumpy_skin_disease", "peste_des_petits_ruminants", "african_horse_sickness")]
    animal["vital_events"].append({"vital_event_type": "birth", "date": "2026-10-01", "vital_event_details": "twins",
                                   "birth_details": {"offspring_count": 2, "sex_of_offspring": "male"}})
    animal["breeding_events"] = [{"breeding_event_type": "natural_breeding", "breeding_date": "2026-09-01", "location": "field"}]
    animal["vaccinations"] = [{"vaccine": v, "vaccination_date": "2026-09-01"} for v in (
        "contagious_bovine_pleuropneumonia", "foot_and_mouth_disease", "lumpy_skin_disease",
        "contagious_caprine_pleuropneumonia", "peste_des_petits_ruminants", "african_horse_sickness", "sheep_and_goat_pox")]
    animal["species_id"] = "cattle"
    out = render(template, base)
    got = [e["disease_type"] for e in out["ls_vital_event_details"] if e["event_type"] == "DISEASE"]
    want = ["LIVESTOCK_DISEASE_CBPP", "LIVESTOCK_DISEASE_FMD", "LIVESTOCK_DISEASE_GUMBORO_DISEASE",
            "LIVESTOCK_DISEASE_BOVINE_TB", "LIVESTOCK_DISEASE_SHEEP_GOAT_POX", "LIVESTOCK_DISEASE_LSD",
            "LIVESTOCK_DISEASE_PPR", "LIVESTOCK_DISEASE_AFRICAN_HORSE_SICKNESS"]
    check(got == want, f"disease mapping: {got} != {want}", failures)
    birth = [e for e in out["ls_vital_event_details"] if e["event_type"] == "BIRTH"][0]
    check(birth["offspring_gender"] == "MALE", f"sex_of_offspring male -> {birth['offspring_gender']!r}", failures)
    check(birth["notes"] == "twins", f"vital_event_details -> notes {birth['notes']!r}", failures)
    check(out["ls_breeding_details"][0]["location"] == "FIELD", f"breeding location field -> {out['ls_breeding_details'][0]['location']!r}", failures)
    got_v = [v["vaccine_type"] for v in out["ls_vaccination_details"]]
    want_v = ["VACCINE_TYPE_CBPP", "VACCINE_TYPE_FMD_CATTLE", "VACCINE_TYPE_LSD", "VACCINE_TYPE_CCPP",
              "VACCINE_TYPE_PPR_GOAT", "VACCINE_TYPE_AHS", "VACCINE_TYPE_SGP_GOAT"]
    check(got_v == want_v, f"vaccine mapping: {got_v} != {want_v}", failures)
    # An unknown disease is passed through untouched, so the registry rejects it with its own message.
    animal["vital_events"] = [{"vital_event_type": "disease", "date": "2026-10-01", "disease_details": {"disease_type": "not_a_disease"}}]
    out = render(template, base)
    check(out["ls_vital_event_details"][0]["disease_type"] == "not_a_disease", "unknown disease was relabelled", failures)

    check_breed_map(failures)

    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print(f"OK: {len(subs)} sample submissions + targeted cases")


def check_breed_map(failures):
    """Every breed choice in the form must be in odk_ingest_hooks._ODK_BREED_CHOICES
    and land on a LIVESTOCK_BREED value of the same species in the code list.
    Read statically, so the test needs neither the platform nor openpyxl."""
    import ast
    import re

    root = os.path.join(os.path.dirname(__file__), "..")
    ext = os.path.join(root, "livestock-extension", "src", "openg2p_registry_livestock_extension")
    hooks = open(os.path.join(ext, "odk_ingest_hooks.py"), encoding="utf-8").read()
    node = next(
        n for n in ast.parse(hooks).body
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_ODK_BREED_CHOICES" for t in n.targets)
    )
    choices = ast.literal_eval(node.value)
    values = os.path.join(ext, "meta_data", "lookup-data", "g2p_attribute_values.sql")
    if not os.path.exists(values):
        values = os.path.join(ext, "master_data", "code_lists", "20_g2p_attribute_values.sql")
    parent = {}
    for line in open(values, encoding="utf-8"):
        m = re.match(r"\('(LIVESTOCK_BREED_[A-Z0-9_]+)','LIVESTOCK_BREED','[^']*','[^']*','([A-Z_]+)'", line)
        if m:
            parent[m.group(1)] = m.group(2)
    form = os.path.join(os.path.dirname(__file__), "breed_choices.csv")
    rows = [line.strip().split(",") for line in open(form, encoding="utf-8") if line.strip() and not line.startswith("species")]
    for species, name in rows:
        code = choices.get(species, {}).get(name)
        check(code is not None, f"breed choice {species}/{name} missing from _ODK_BREED_CHOICES", failures)
        if code:
            check(parent.get(code) == "LIVESTOCK_SPECIES_" + species.upper(),
                  f"breed choice {species}/{name} -> {code}, which is not a {species} breed in the code list", failures)


if __name__ == "__main__":
    main()
