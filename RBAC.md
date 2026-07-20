# Wearables Health — Multi-user RBAC deployment guide

Per-person data isolation for the Wearables platform. This is **deployment
config** (person IDs and roles are specific to each install), so it is **not**
shipped inside the app — apply it on your Splunk instance as described below.

## Model in one sentence
Every event carries an **indexed `person_id`** (opaque, stamped at ingest);
a role-level **`srchFilter = person_id="…"`** clamps each user to their own data
at the storage layer. The lookups only *enrich* (names/goals) — they are **not**
the access gate, so editing a lookup can never grant access to another person.

Two independent guarantees:
1. **Read isolation** — `authorize.conf` `srchFilter` on the indexed `person_id`.
2. **Write integrity** — lookups write-locked to `admin`/`sc_admin` (all paths).

---

## Prerequisites
- `index=wearables` exists; data is flowing with indexed fields `person_id` +
  `vendor` (see the fetcher / `TA-oura`).
- `TA-oura` installed (normalizes + tags the data). The `wearable_person_profile`
  enrichment lookup lives in the **wearables** app (exported global, write-locked
  to admin/sc_admin).

---

## Step 1 — Populate the person profile lookup (enrichment)
Admin edits `wearable_person_profile.csv` (via the **Splunk App for Lookup File
Editing**, or Settings → Lookups):

```
person_id,person_name,step_goal,distance_goal_m,calorie_goal
P001,Tony,10000,8000,600
P002,Alex,8000,6000,500
```
`person_id` is opaque; the readable name/goals live only here. This replaces the
old hardcoded 10K step goal.

## Step 2 — Create one role per person
> **Where this lives:** roles are *deployment* config — **not shipped in the app `.spl`**. A copy-paste template is in [`examples/authorize.conf.example`](examples/authorize.conf.example) (repo-only). Self-managed: apply to `$SPLUNK_HOME/etc/system/local/authorize.conf`. Splunk Cloud: recreate as roles via Settings → Roles / ACS (Cloud ignores app-provided authorize.conf).

**Self-managed** — `authorize.conf` in **`$SPLUNK_HOME/etc/system/local/`** (not the app):
```
[role_wearables_P001]
importRoles        = user            # capabilities only
srchIndexesAllowed = wearables
srchIndexesDefault = wearables
srchFilter         = person_id="P001"

[role_wearables_P002]
importRoles        = user
srchIndexesAllowed = wearables
srchIndexesDefault = wearables
srchFilter         = person_id="P002"

# Household / admin overview: sees everyone (no srchFilter)
[role_wearables_household]
importRoles        = user
srchIndexesAllowed = wearables
srchIndexesDefault = wearables
```
**Splunk Cloud** — create the same roles via **Settings → Roles** (or ACS):
set *Restrictions → Restrict search terms* to `person_id="P001"`, and
*Indexes → wearables*. (Cloud may ignore app-provided `authorize.conf`, so use
the UI/ACS there.)

## Step 3 — Assign users to roles
Each person-user gets **only** their `role_wearables_P00x` (plus base `user`).
Household viewers get `role_wearables_household`.

## Step 4 — Close the bypasses (critical)
- **Additive-roles gotcha:** Splunk ORs `srchFilter`s across a user's roles, so
  the person-role must be the **only** role granting `wearables`. Ensure the base
  `user` role (and any other role a person-user holds) does **not** grant
  `wearables` unfiltered — otherwise the filter is defeated.
- Person-roles must **not** have `admin_all_objects` (bypasses lookup write ACLs)
  or `run_collect`/write to `index=wearables` (event injection).
- Confirm the lookups are write-locked to `admin`/`sc_admin` (they are, via
  `wearables/metadata/default.meta`).

## Step 5 — Verify (don't trust, test)
Log in as a **restricted person-user** (e.g. mapped to `role_wearables_P001`)
and confirm ALL of:
1. `index=wearables` returns **only** that person's events (`person_id=P001`).
2. Enrichment still resolves (`person_name`, goals appear).
3. Writes are **denied**:
   - `| inputlookup wearable_person_profile | outputlookup wearable_person_profile` → denied
   - Opening the Lookup Editor and saving → denied
   - The REST lookup endpoint → denied
4. Cross-person probe: `index=wearables person_id="P002"` returns **nothing**.

Only after all four pass is per-person isolation trustworthy.

---

## Adding a device to an existing person (why person_id is the key)
Because RBAC keys on the canonical `person_id` (not a per-device id), adding a
second device for someone (e.g. a Garmin watch next to their Oura ring) is just
**one row in the identity map** (`vendor, vendor_user_id, person_id`) — the new
device's data gets the same `person_id`, so **no `authorize.conf` change and no
reindex** is needed.
