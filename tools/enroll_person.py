#!/usr/bin/env python3
"""
Wearables — Person Enrollment Tool
Admin-side provisioning: assigns a canonical person_id, creates the person's
isolation role and their own write-only HEC token, records the credentials so
they can be recovered later, and prints the JSON payload for the enrollment
QR (qr_enroll_viz).

WHY A SEPARATE CREDENTIALS INDEX, NOT A LOOKUP:
Lookup CSV write ACLs are file-level, not row-level — there's no way to let a
person read only their own row. An index gives that for free via the same
enforcement already used for health data: an admin-owned authorize.conf
srchFilter="person_id=..." on an INDEXED field, applied at the storage layer.
So each person's own HEC token lives in index=wearables_credentials, one
event per issuance (rotation = a new event; latest wins via _indextime, same
pattern used by the dedup saved searches), and their own role grants them
read access to just their own row — good for self-recovery later (e.g. via a
"My Enrollment" panel) without ever widening who can see anyone else's token.

TWO CREDENTIALS, TWO BLAST RADII (see instance config below):
- auth_token: a SCOPED SPLUNK AUTH TOKEN (Settings > Tokens), not a raw admin
  password. Used only for management-REST calls (create role, create HEC
  token, read/append the profile lookup). This is the more sensitive of the
  two — treat the config file holding it accordingly (never commit it).
- credentials_hec_token: a dedicated, admin-only HEC token restricted to
  WRITE-ONLY access into index=wearables_credentials. Never distributed to
  any person. Even if leaked, it can't read anything — worst case is a fake
  credentials event, not exposure of a real one.
Neither credential is the same as the per-person HEC token this script
CREATES for the enrollee — that one is scoped to index=wearables (+ the log
index) only, and never gets write access to wearables_credentials.

REST vs ACS (per-instance, set explicitly via "backend" — never auto-detected):
Splunk Cloud does not expose role creation or HEC-token creation through the
same management REST endpoints self-managed Splunk does — those specific
operations are reserved for Splunk-side Cloud admin operations, not sc_admin.
Splunk's Admin Config Service (ACS) is the customer-facing substitute, but it
is a genuinely SEPARATE API on a different hostname (admin.splunk.com, not
the stack's own :8089), with different request shapes (JSON/camelCase vs
form-encoded), and HEC token creation via ACS is ASYNCHRONOUS (POST returns
202 with just the name; the token value only appears once you poll a GET).
Verified against Splunk's own ACS API docs (2026-09-01), not guessed.
get_next_person_id()/add_profile_row() are unaffected — search-job dispatch
works identically on both backends and never needed branching.

Usage:
    # Enroll on every configured instance (person_id assigned via the
    # primary instance, then reused identically everywhere else)
    python enroll_person.py --splunk-user tvincent2 --name "Jane"

    # Test on one instance first
    python enroll_person.py --splunk-user tvincent2 --name "Jane" --instance home

    # Show what would happen without creating anything
    python enroll_person.py --splunk-user tvincent2 --name "Jane" --dry-run

Config file (default: ./enroll_config.json, override with ENROLL_CONFIG_FILE):
    See enroll_config.example.json. One block per Splunk instance; the
    "primary" instance (or the first one listed) is authoritative for
    person_id assignment.
"""

import argparse
import json
import os
import re
import sys
import time

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

CONFIG_FILE = os.environ.get("ENROLL_CONFIG_FILE", os.path.join(os.path.dirname(__file__), "enroll_config.json"))
PROFILE_LOOKUP = "wearable_person_profile"


def load_config():
    if not os.path.exists(CONFIG_FILE):
        sys.exit(f"error: config file not found: {CONFIG_FILE} (copy enroll_config.example.json and fill it in)")
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    instances = cfg["instances"]
    primary = cfg.get("primary_instance") or next(iter(instances))
    if primary not in instances:
        sys.exit(f"error: primary_instance '{primary}' is not in instances")
    return cfg, primary


def spl_escape(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def dispatch_oneshot(inst, spl):
    """Run SPL via the management REST API's oneshot search mode, return parsed result rows."""
    resp = requests.post(
        f"{inst['management_url']}/services/search/jobs",
        headers={"Authorization": f"Bearer {inst['auth_token']}"},
        data={"search": f"search {spl}", "exec_mode": "oneshot", "output_mode": "json"},
        verify=inst.get("verify_ssl", True),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def get_next_person_id(inst):
    rows = dispatch_oneshot(inst, f"| inputlookup {PROFILE_LOOKUP} | table person_id")
    max_n = 0
    for row in rows:
        m = re.match(r"^P(\d+)$", str(row.get("person_id", "")).strip())
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"P{max_n + 1:03d}"


def add_profile_row(inst, person_id, splunk_user, name):
    spl = (
        f'| makeresults '
        f'| eval person_id="{spl_escape(person_id)}", splunk_user="{spl_escape(splunk_user)}", '
        f'person_name="{spl_escape(name)}", step_goal="", distance_goal_m="", calorie_goal="" '
        f"| table person_id splunk_user person_name step_goal distance_goal_m calorie_goal "
        f"| outputlookup append=true {PROFILE_LOOKUP}"
    )
    dispatch_oneshot(inst, spl)


def create_role(inst, person_id):
    role_name = f"wearables_{person_id}"
    backend = inst.get("backend", "rest")
    if backend == "acs":
        # ACS role creation: JSON body, camelCase fields, admin.splunk.com — NOT the
        # stack's own management port. Verified against Splunk's own ACS API docs
        # (2026-09-01), not guessed: POST /adminconfig/v2/roles.
        resp = requests.post(
            f"{inst['acs_base_url']}/adminconfig/v2/roles",
            headers={"Authorization": f"Bearer {inst['auth_token']}", "Content-Type": "application/json"},
            json={
                "name": role_name,
                "importedRoles": ["user"],
                "srchIndexesAllowed": [inst["data_index"], inst["credentials_index"]],
                "srchFilter": f'person_id="{person_id}"',
            },
            timeout=30,
        )
        if resp.status_code >= 400 and "already exists" not in resp.text.lower():
            resp.raise_for_status()
        return role_name

    # Self-managed REST path: form-encoded, snake_case field, the stack's own
    # management port (8089).
    resp = requests.post(
        f"{inst['management_url']}/services/authorization/roles",
        headers={"Authorization": f"Bearer {inst['auth_token']}"},
        data={
            "name": role_name,
            "imported_roles": "user",
            "srchIndexesAllowed": [inst["data_index"], inst["credentials_index"]],
            "srchFilter": f'person_id="{person_id}"',
        },
        verify=inst.get("verify_ssl", True),
        timeout=30,
    )
    if resp.status_code >= 400 and "already exists" not in resp.text.lower():
        resp.raise_for_status()
    return role_name


def create_hec_token(inst, person_id):
    input_name = f"wearables_hec_{person_id}"
    backend = inst.get("backend", "rest")
    if backend == "acs":
        # ACS HEC creation is ASYNCHRONOUS — the POST returns 202 with only the
        # token NAME; the actual token value only appears once you poll the GET
        # endpoint for that name. Verified against Splunk's own ACS API docs
        # (2026-09-01) — NOT the same synchronous shape as the REST path below.
        resp = requests.post(
            f"{inst['acs_base_url']}/adminconfig/v2/inputs/http-event-collectors",
            headers={"Authorization": f"Bearer {inst['auth_token']}", "Content-Type": "application/json"},
            json={
                "name": input_name,
                "allowedIndexes": [inst["data_index"], inst.get("log_index", inst["data_index"])],
                "defaultIndex": inst["data_index"],
                "disabled": False,
                "useACK": False,
            },
            timeout=30,
        )
        resp.raise_for_status()  # 202 Accepted expected; anything >=400 is a real failure

        get_url = f"{inst['acs_base_url']}/adminconfig/v2/inputs/http-event-collectors/{input_name}"
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(3)
            r = requests.get(get_url, headers={"Authorization": f"Bearer {inst['auth_token']}"}, timeout=30)
            if r.status_code == 200:
                token = r.json().get("token")
                if token:
                    return token
        sys.exit(f"error: ACS HEC token '{input_name}' did not become ready within 60s — "
                  f"check {get_url} manually")

    # Self-managed REST path: synchronous — token comes back in the same response.
    resp = requests.post(
        f"{inst['management_url']}/services/data/inputs/http",
        headers={"Authorization": f"Bearer {inst['auth_token']}"},
        data={
            "name": input_name,
            "index": inst["data_index"],
            "indexes": [inst["data_index"], inst.get("log_index", inst["data_index"])],
            "sourcetype": "",
        },
        verify=inst.get("verify_ssl", True),
        timeout=30,
    )
    resp.raise_for_status()
    entry = resp.json()["entry"][0]["content"]
    token = entry.get("token")
    if not token:
        sys.exit(f"error: HEC token creation for {input_name} did not return a token value: {entry}")
    return token


def write_credentials_event(inst, person_id, splunk_user, hec_token):
    event = {
        "event": {
            "person_id": person_id,
            "splunk_user": splunk_user,
            "hec_url": inst["data_hec_url"],
            "index": inst["data_index"],
            "token": hec_token,
        },
        "fields": {"person_id": person_id},
        "index": inst["credentials_index"],
        "sourcetype": "wearables:credentials",
    }
    resp = requests.post(
        inst["credentials_hec_url"],
        headers={"Authorization": f"Splunk {inst['credentials_hec_token']}"},
        json=event,
        verify=inst.get("verify_ssl", True),
        timeout=15,
    )
    resp.raise_for_status()


def enroll_on_instance(name, inst, person_id, splunk_user, display_name, dry_run):
    print(f"--- {name} ---")
    if dry_run:
        print(f"  [dry-run] would add profile row, role wearables_{person_id}, "
              f"HEC token wearables_hec_{person_id}, credentials event")
        return None
    add_profile_row(inst, person_id, splunk_user, display_name)
    print(f"  profile row added ({person_id}, {splunk_user})")
    role_name = create_role(inst, person_id)
    print(f"  role created: {role_name}")
    hec_token = create_hec_token(inst, person_id)
    print(f"  HEC token created: wearables_hec_{person_id}")
    write_credentials_event(inst, person_id, splunk_user, hec_token)
    print(f"  credentials event written to {inst['credentials_index']}")
    return {
        "v": 1,
        "hec_url": inst["data_hec_url"],
        "index": inst["data_index"],
        "person_id": person_id,
        "token": hec_token,
    }


def main():
    ap = argparse.ArgumentParser(description="Enroll a person across one or more wearables Splunk instances.")
    ap.add_argument("--splunk-user", required=True, help="Splunk login username for this person (blank-able for a dependent with no login: pass \"\")")
    ap.add_argument("--name", default="", help="Display name (optional — self-service later fills the rest)")
    ap.add_argument("--instance", action="append", help="Limit to one instance (repeatable). Default: all configured instances.")
    ap.add_argument("--dry-run", action="store_true", help="Print what would happen, create nothing")
    args = ap.parse_args()

    cfg, primary = load_config()
    instances = cfg["instances"]
    targets = args.instance or list(instances.keys())
    for t in targets:
        if t not in instances:
            sys.exit(f"error: unknown instance '{t}' (configured: {', '.join(instances)})")
    if primary not in targets:
        targets = [primary] + targets

    person_id = get_next_person_id(instances[primary]) if not args.dry_run else "P0??"
    print(f"Assigning person_id={person_id} (via primary instance '{primary}')\n")

    payloads = {}
    for name in targets:
        payload = enroll_on_instance(name, instances[name], person_id, args.splunk_user, args.name, args.dry_run)
        if payload:
            payloads[name] = payload
        print()

    if payloads:
        print("Enrollment JSON — paste into the QR panel's Payload field:\n")
        for name, payload in payloads.items():
            print(f"# {name}")
            print(json.dumps(payload))
            print()


if __name__ == "__main__":
    main()
