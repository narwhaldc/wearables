# Wearables Health → Splunk — Installation Guide

End-to-end setup for the **Wearables** platform: vendor-neutral dashboards on the Wearables data
model, fed by per-vendor add-ons. This is the **platform** guide; each vendor's ingest detail lives
in that add-on's own INSTALL:
- Oura ingest → **[TA-oura/INSTALL.md](https://github.com/narwhaldc/TA-oura/blob/main/INSTALL.md)**
- Garmin ingest → **[TA-garmin/INSTALL.md](https://github.com/narwhaldc/TA-garmin/blob/main/INSTALL.md)**
- Withings ingest → **[TA-withings/INSTALL.md](https://github.com/narwhaldc/TA-withings/blob/main/INSTALL.md)**

**App version:** wearables 0.1.73 · Apache-2.0 · Source: https://github.com/narwhaldc/wearables

---

## Table of Contents
1. [Architecture](#architecture)
2. [Components](#components)
3. [Prerequisites](#prerequisites)
4. [1. Create index=wearables + HEC](#1-create-indexwearables--hec)
5. [2. Install the apps](#2-install-the-apps)
6. [3. Set up vendor ingest](#3-set-up-vendor-ingest)
7. [4. Populate the KV registries](#4-populate-the-kv-registries)
8. [5. Multi-user RBAC](#5-multi-user-rbac)
9. [6. First run, backfill, verify](#6-first-run-backfill-verify)
10. [Scheduled maintenance](#scheduled-maintenance)
11. [Troubleshooting](#troubleshooting)

---

## Architecture
```
Oura Ring ─▶ TA-oura/tools/oura_to_hec_with_phi.py  (pull, OAuth2)
Garmin    ─▶ TA-garmin/tools/garmin_to_hec.py       (pull, python-garminconnect)
Withings  ─▶ TA-withings/tools/withings_to_hec.py   (pull, OAuth2)
                         │  HEC: index=wearables, sourcetype=<vendor>:<type>,
                         │       indexed fields vendor + person_id
                         ▼
        TA-oura / TA-garmin / TA-withings  ── search-time normalization ──▶ canonical fields + tags
                         ▼
        wearables  ── data model (Sleep/HeartRate/Activity/Workout/Daily/Device) + 6 dashboards
                         ▼
        Per-person isolation via role srchFilter on the indexed person_id (RBAC)
```
Ingest runs on any host that can reach the vendor API + your Splunk HEC port; Splunk need not be
internet-facing. Ingest scripts are **repo-only** (never shipped in a `.spl` — they hold creds).

## Components
| Component | Repo | Ships in `.spl`? |
|---|---|---|
| **wearables** app (model + dashboards + KV registries) | [narwhaldc/wearables](https://github.com/narwhaldc/wearables) | yes |
| **TA-oura** (Oura normalize + `tools/` fetcher) | [narwhaldc/TA-oura](https://github.com/narwhaldc/TA-oura) | app yes, `tools/` no |
| **TA-garmin** (Garmin normalize + `tools/` poller) | [narwhaldc/TA-garmin](https://github.com/narwhaldc/TA-garmin) | app yes, `tools/` no |
| **TA-withings** (Withings normalize + `tools/` fetcher) | [narwhaldc/TA-withings](https://github.com/narwhaldc/TA-withings) | app yes, `tools/` no |
| **hypnogram_viz** (custom viz) | [narwhaldc/hypnogram_viz](https://github.com/narwhaldc/hypnogram_viz) | yes |
| **charge_ring_viz** (custom viz) | [narwhaldc/charge_ring_viz](https://github.com/narwhaldc/charge_ring_viz) | yes |

## Prerequisites
- Splunk Enterprise 10.x or Splunk Cloud, with HTTP Event Collector enabled.
- Admin (install apps, create index, populate KV registries, set roles).
- A host with **Python 3.9+** (Oura fetcher) / **3.10+** (Garmin poller — `garminconnect` needs it).
- Vendor credentials: an Oura developer app (OAuth2) and/or a Garmin account (see the TA INSTALLs).

---

## 1. Create index=wearables + HEC
- **Index:** Settings → Indexes → New Index → `wearables` (Events). On Cloud, use the console/ACS.
  **The recommended index name is `wearables`.**
  > **Using a different index?** The dashboards read their index through the **`widx` macro**
  > (Settings → Advanced Search → Search macros → `widx`, in this app), defined as
  > `index=wearables`. Point the whole app at another index by editing that **one macro line** —
  > no dashboard edits needed. The `widx` index **must match the ingest target index** you set in
  > the TA (see the TA INSTALLs' targets config), and the `oura_health` app has its own `widx`
  > (a bridge `(index=oura OR index=wearables)`) to update to match too. The dedup-maintenance /
  > registry-backup saved searches use the literal index name — update those if you rename.
- **HEC:** Settings → Data Inputs → HTTP Event Collector → enable + note the port (default 8088);
  create a token with access to `index=wearables`. The `hec_url` is
  `<http|https>://<host>:8088/services/collector/event` (scheme must match HEC's SSL setting; on
  Cloud use the dedicated `http-inputs-<stack>.splunkcloud.com:443` host).

## 2. Install the apps
Install (Apps → Install app from file) and **restart Splunk** so custom-viz JS + the data model load:
1. **TA-oura**, **TA-garmin**, and/or **TA-withings** (normalization the model constrains on).
2. **wearables** (data model + dashboards + KV registries).
3. **hypnogram_viz** + **charge_ring_viz** (hypnogram + charge panels).

## 3. Set up vendor ingest
Copy the vendor's script from its add-on `tools/` to your ingest host and follow that add-on's INSTALL:
- **Oura:** `oura_to_hec_with_phi.py` — OAuth2 `--auth`, `oura_targets.json`. See **TA-oura/INSTALL.md**.
- **Garmin:** `garmin_to_hec.py` — one-time `garmin_probe.py` login, `garmin_targets.json`. See **TA-garmin/INSTALL.md**.
- **Withings:** `withings_to_hec.py` — OAuth2 `--auth`, `withings_targets.json`. See **TA-withings/INSTALL.md**.

Both point HEC at `index=wearables` and stamp indexed `vendor` + `person_id`. Targets files hold
tokens → **gitignored, never commit** (both add-ons enforce this).

## 4. Populate the KV registries
Admin-managed enrichment (KV Store in this app; write-locked to admin/sc_admin; survive upgrades).
The easiest way is the in-app UI — **Admin → People &amp; Defaults** — which adds/edits a person's
row (name, default units, goals, height) and maps their **Splunk login → person_id** (identity map),
without touching the lookup editor. Equivalent raw SPL:
```
| makeresults | eval person_id="P001", person_name="Tony", step_goal=10000
| table person_id person_name step_goal | outputlookup wearable_person_profile
```
`wearable_device_profile` (device_id→friendly name) can be derived from data after first ingest, or
edited by admins. `wearable_identity_map` (vendor,vendor_user_id→person_id) is optional (the
fetchers stamp `person_id` directly). These are **enrichment only** — never the access gate.

## 5. Multi-user RBAC
Per-person isolation = a role-level **`srchFilter = person_id="P00x"`** on the indexed `person_id`.
Full recipe (roles, users, closing the bypasses, verification) is in the app's **`RBAC.md`**
(deployment config — not shipped in the `.spl`; `authorize.conf` lives in the deployment, not the app).

## 6. First run, backfill, verify
Per vendor (see the TA INSTALLs for full flags): `--dry-run`, `--backfill <date>`, then incremental,
and `--status`. Then confirm:
```
index=wearables | stats count by vendor, sourcetype
index=wearables tag=wearable_sleep | table _time vendor total_sleep_min sleep_score
```
Open the dashboards: **Today · Sleep · Heart · Activity · Wellness · Device** (temp "Person" picker
lets an admin switch persons until RBAC is enforced).

> **Test without a device:** `garmin_to_hec.py --generate-sample-data` sends synthetic `synthetic="true"`
> events through the whole pipeline. Clean up (All time): `index=wearables synthetic="true" | delete`.

## Scheduled maintenance
`default/savedsearches.conf` ships:
- **Index dedup** (two-pass tag→delete for `index=wearables`), +1h after oura_health's (03:00 / 03:15)
  so both apps coexist; Pass 2's `| delete` needs `can_delete`. Overlap re-fetch dupes are cleaned here.
- **Registry backup** — nightly dump of the KV registries to the index (survives a full app removal).

## Troubleshooting
- **Dashboards empty** → confirm the vendor add-on is installed (props/tags fire) and data is in
  `index=wearables` (`| stats count by sourcetype`); check the Person picker isn't scoped to a person
  with no data.
- **Capability panels blank (hypnogram / contributors)** → those need Oura-specific fields; for a
  multi-vendor person they show the latest record that has the data (a Garmin-only night won't populate them).
- **Nothing ingested** → see the vendor's INSTALL (auth/targets); `--dry-run` to inspect payloads.
- **Custom viz not rendering** → install `hypnogram_viz` + `charge_ring_viz` and do a full Splunk restart.
