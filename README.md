# Wearables Health — Splunk app (vendor-neutral wearables dashboards)

Dashboards over the **Wearables data model** — one panel set that works across devices
(**Oura**, **Garmin**, and **Withings**). The hub of the multi-vendor platform; the single-vendor
**oura_health** app remains the GA fallback and is superseded by this app over time.

Quick start + all repo links are on the in-app **Overview** page. Per-vendor ingest setup:
**[TA-oura/INSTALL.md](https://github.com/narwhaldc/TA-oura/blob/main/INSTALL.md)** ·
**[TA-garmin/INSTALL.md](https://github.com/narwhaldc/TA-garmin/blob/main/INSTALL.md)** ·
**[TA-withings/INSTALL.md](https://github.com/narwhaldc/TA-withings/blob/main/INSTALL.md)**.

## Contents
- `default/data/models/Wearables.json` — the **Wearables data model**: event-based roots
  **Sleep · HeartRate · Activity · Workout · Daily · Device**, each constrained on a vendor-neutral
  tag (`wearable_sleep` / `wearable_heartrate` / …) exposing canonical fields (`total_sleep_min`,
  `avg_hr`, `steps`, `body_battery`, `person_id`, `vendor`, …). Unaccelerated (avoids the Cloud
  AppInspect disk advisory) until acceleration is wanted.
- `default/data/ui/views/` — 6 dashboards: **Today · Sleep · Heart · Activity · Wellness · Device**
  plus the **Overview** page. All queries are canonical multi-line SPL and read **only** the model.
- `default/collections.conf` + `default/transforms.conf` — **KV Store registries**
  `wearable_person_profile` (person_id→name/goals), `wearable_device_profile` (device_id→name),
  `wearable_identity_map`. Admin/sc_admin write-locked; survive app upgrades (unlike shipped CSVs).
- `default/savedsearches.conf` — index dedup maintenance (+1h after oura_health) + a nightly
  registry backup to the index.

## Requirements
- **`index=wearables`** (Settings → Indexes / ACS).
- A vendor add-on installed: **[TA-oura](https://github.com/narwhaldc/TA-oura)** and/or
  **[TA-garmin](https://github.com/narwhaldc/TA-garmin)**, and/or **[TA-withings](https://github.com/narwhaldc/TA-withings)** — they ingest + normalize into the model.
  Ingest scripts live in each add-on's `tools/` (repo-only, never shipped in the `.spl`).
- Per-person **RBAC** via role `srchFilter` on the indexed `person_id` (see `RBAC.md`).
- Custom-viz add-ons **hypnogram_viz** + **charge_ring_viz** for the hypnogram / charge panels.

## Privacy — PII / PHI
The platform keeps identifying data minimal and gates health data (PHI) by access control.
- **No strong PII in the registry.** `wearable_person_profile` (KV Store) holds only low-sensitivity
  enrichment — display name, goals, units, height, `splunk_user` (login map), and **`birth_ym`
  (birth month + year only, `YYYY-MM` — never the day)**. Age is computed at search time; month+year
  keeps it accurate all year except within the birth month and is **not** fraud-grade PII (a full DOB
  is). Never store a full DOB, SSN, address, or email-as-data.
- **The KV registry is collection-readable, not row-scoped.** `inputlookup` returns *every* person's
  row to anyone who can run it (writes are admin-only) — it is shared enrichment, which is why nothing
  sensitive lives there. True per-row isolation needs a mediated REST handler (roadmap).
- **The real access boundary is index RBAC.** All health data lives in `index=wearables`, keyed by an
  **opaque `person_id`** (a surrogate key, not a name). Per-person isolation is a role **`srchFilter`**
  on `person_id` (e.g. a caregiver limited to `person_id IN (self, dependents)`; see `RBAC.md`). This
  only protects data **where those roles are configured** — demo/shared stacks show all data to all
  users (hence the temporary Person picker); production must set the srchFilter roles.
- **Nothing sensitive ships in a package.** Every `.spl` passes an automated PII/secret scan before
  release; only synthetic (`synthetic="true"`) sample data is included.

## Scheduled maintenance
`default/savedsearches.conf` ships a two-pass **index dedup** for `index=wearables` (tag duplicates
→ delete), scheduled +1h after oura_health's (03:00 / 03:15) so both apps coexist; Pass 2's
`| delete` needs `can_delete`. Plus a nightly **registry backup** of the KV collections to the index.

## Status
Active — all 6 dashboards migrated onto the model (vendor-neutral, RBAC-ready, device picker,
Garmin-ready). Supersedes `oura_health` as the platform matures. Apache-2.0.
Source: https://github.com/narwhaldc/wearables
