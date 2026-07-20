# wearables — Splunk app (vendor-neutral wearables dashboards)

Dashboards over the **Wearables data model** — one panel set that works across
devices. Companion to per-vendor add-ons (`TA-oura`, and later `TA-garmin`, …).
The single-vendor **oura_health** app remains the GA fallback.

## Contents
- `default/data/models/Wearables.json` — the **Wearables data model**: event-based
  `Sleep`, `HeartRate`, `Activity` datasets, each constrained on a vendor-neutral
  tag (`wearable_sleep` / `wearable_heartrate` / `wearable_activity`) and exposing
  canonical fields (`total_sleep_min`, `avg_hr`, `steps`, `person_id`, `vendor`, …).
- (no `datamodels.conf`) — the model defaults to **unaccelerated**. Shipping
  `datamodels.conf` triggers a Cloud AppInspect disk-usage advisory, so it's
  omitted until acceleration is actually wanted (then add it and accept the
  advisory, or accelerate via the Cloud console).
- `default/data/ui/views/wearables_overview.xml` — overview page (dashboards migrate here).

## Requirements
- **`index=wearables`** (create via Settings → Indexes or the Cloud console/ACS).
- **`TA-oura`** (or another vendor add-on) installed — it provides the canonical
  fields and dataset tags the model constrains on.
- Per-person **RBAC** via role `srchFilter` on the indexed `person_id`, and an
  admin-managed `person_id → name/goals` enrichment lookup (in the add-on).

## Status
0.1.0 — scaffold: data model defined + overview page. Dashboard migration from
`oura_health` (Today / Sleep / Heart / Activity / Wellness / Device) is in
progress. Apache-2.0.
