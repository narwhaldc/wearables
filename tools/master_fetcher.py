#!/usr/bin/env python3
"""
Wearables — Master Fetcher (Stage 1)

Calls all five vendor fetchers' sync() in ONE process instead of five
independently-scheduled cron entries. This is the prerequisite groundwork for
a mobile port (an embedded interpreter calls sync() functions in-process, the
same way this orchestrator does) and it pays for itself today regardless:
one cron line, one combined log, less scheduling surface area.

STAGE 1 (this file): each vendor still resolves its OWN targets from its own
local *_targets.json, exactly as it would standalone. Stage 2 (not built
yet — see wearables-roadmap.md) replaces that with a live pull from
index=wearables_credentials via a persisted Splunk login, merged across
however many Splunk instances are configured. Swapping that in later should
NOT require changing this file's overall shape — only how each vendor's
`targets` argument gets built.

TEMPORARY LOCATION: this lives under wearables/tools/ for now. Once proven,
it moves (along with the five fetcher scripts themselves) into a dedicated
repo that is deliberately NOT named "TA-*" — it produces no .spl, is never
AppInspect-gated, and calling it a Technology Add-on would be misleading.

WHY EACH VENDOR'S LOCK FILE IS RE-ACQUIRED HERE, NOT JUST THIS SCRIPT'S OWN:
during the transition period, a vendor's OLD standalone cron entry may still
be active. This orchestrator bypasses each vendor's main() (which is where
that lock was previously acquired) by calling sync() directly, so it must
acquire that SAME lock file itself before calling sync() — otherwise this
orchestrator and a leftover standalone cron job could race on the same
checkpoint/dedup files with no protection between them. If the lock is held,
that vendor is skipped for this cycle (logged), not blocked on or crashed.

A SEPARATE, NEW lock (MASTER_LOCK_FILE) additionally guards against two
orchestrator runs overlapping (e.g. a manual run + a cron-triggered one).

KNOWN, CURRENTLY-DORMANT RISK (documented, not fixed): each vendor's own
load_dotenv() writes into the shared process-wide os.environ with
"first import wins" semantics (`if key not in os.environ: os.environ[key] =
val`). Today only TA-oura's .env sets the generic (non-vendor-prefixed)
SPLUNK_HEC_URL/TOKEN/INDEX/VERIFY_SSL names, and even oura's own runtime
path ignores them once a targets file exists — so nothing currently
collides. This was IMPOSSIBLE when each fetcher ran in its own OS process
(separate environments); it becomes a live risk here only if a future .env
edit reuses one of those generic names in a second fetcher.

EXTRA INSTANCES (e.g. a second/third Garmin person): list them under a vendor in
master_fetcher_config.json -> "instances": [{"name": "simon", "dir": "tools_simon"}]. Each is
a full copy of the vendor script living in ~/src/<TA-repo>/<dir>/ with ITS OWN .env/targets/
state, and is run as a SUBPROCESS (clean env, cwd = its dir) after the vendor's main run, on its
own interval, with output forwarded to this log. They are never run in-process: the shared
os.environ would leak one person's credentials into another's run. --only vendor runs the vendor
and its instances; --only vendor:name runs just that instance. A --backfill only touches an
instance if it is named explicitly (vendor:name) - never as a side effect of --only vendor.

Usage:
    python master_fetcher.py                  # run all 5 vendors
    python master_fetcher.py --only oura,garmin
    python master_fetcher.py --dry-run
"""

import argparse
import atexit
import fcntl
import json
import os
import sys
import time
from pathlib import Path

# Pristine environment, captured BEFORE any vendor module is imported (each vendor's load_dotenv()
# writes its .env into os.environ, "first import wins"). Extra per-person instances run in a
# SUBPROCESS with THIS env so they never inherit another person's key/credentials/backend.
ORIG_ENV = dict(os.environ)

HERE = Path(__file__).resolve().parent
SRC_ROOT = HERE.parent.parent  # .../wearables/tools -> .../wearables -> ~/src
MASTER_LOCK_FILE = HERE / "master_fetcher.lock"
CONFIG_FILE = HERE / "master_fetcher_config.json"
STATE_FILE = HERE / "master_fetcher_state.json"
DEFAULT_INTERVAL_MINUTES = 60  # used only if a vendor is missing from CONFIG_FILE

# Orchestration-owned last-run tracking, separate from each vendor's own (very
# differently shaped) internal checkpoint state. Deliberately NOT parsed out of
# each vendor's checkpoint file -- Oura/Garmin/Withings/Google all track "last
# run" completely differently internally, so a small uniform state file the
# orchestrator owns itself is simpler and more robust than five vendor-specific
# parsers for the same concept.


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (IOError, OSError, json.JSONDecodeError):
        return default


def _save_json(path, data):
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _due(vendor, config, state):
    interval_min = (config.get(vendor) or {}).get("interval_minutes", DEFAULT_INTERVAL_MINUTES)
    last_run = state.get(vendor)
    if last_run is None:
        return True
    return (time.time() - last_run) >= interval_min * 60

# (vendor key, TA repo dir name, module file name without .py)
VENDORS = [
    ("oura",     "TA-oura",     "oura_to_hec_with_phi"),
    ("garmin",   "TA-garmin",   "garmin_to_hec"),
    ("withings", "TA-withings", "withings_to_hec"),
    ("apple",    "TA-apple",    "apple_to_hec"),
    ("google",   "TA-google",   "google_to_hec"),
]


def _load_vendor_module(repo_dir, module_name):
    tools_dir = SRC_ROOT / repo_dir / "tools"
    if not tools_dir.is_dir():
        raise RuntimeError(f"vendor tools dir not found: {tools_dir}")
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import importlib
    return importlib.import_module(module_name), tools_dir


def _acquire_lock(lock_path):
    """Non-blocking flock. Returns an open file handle on success, None if held."""
    fp = open(lock_path, "w")
    try:
        fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        fp.close()
        return None
    fp.write(str(os.getpid()))
    fp.flush()
    return fp


def _release_lock(fp, lock_path):
    try:
        fcntl.flock(fp, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fp.close()
    except Exception:
        pass
    try:
        os.unlink(lock_path)
    except OSError:
        pass


# Per-vendor call adapter. Oura and Withings' sync() self-load targets from
# their own targets.json (target_filter=None default); Garmin, Apple, and
# Google's sync() take an already-resolved targets dict as a parameter — a
# known inconsistency from tonight's extraction (see wearables-roadmap.md),
# not yet worth unifying since Stage 2's live-pull will replace how targets
# get built for ALL five anyway. This dict is Stage 1's adapter layer; Stage
# 2 only needs to change what's INSIDE these lambdas, not this file's shape.
def _run_oura(mod, dry_run, backfill, backfill_end):
    return mod.sync(dry_run=dry_run, backfill_date=backfill, backfill_end_date=backfill_end)


def _run_garmin(mod, dry_run, backfill, backfill_end):
    targets = mod.load_targets(None)
    return mod.sync(targets, dry_run=dry_run, backfill_date=backfill, backfill_end_date=backfill_end)


def _run_withings(mod, dry_run, backfill, backfill_end):
    return mod.sync(dry_run=dry_run, backfill_date=backfill, backfill_end_date=backfill_end)


def _run_apple(mod, dry_run, backfill, backfill_end):
    # Apple's file-based model has no backfill date-range concept at all (it
    # processes whatever HAE export files exist in the watch dir) — backfill/
    # backfill_end are accepted for a uniform call signature across vendors
    # but simply don't apply here, matching the other adapters not needing them.
    if backfill or backfill_end:
        raise RuntimeError("apple has no --backfill/--backfill-end concept (file-based, not date-range-based)")
    # Ported from Apple's old standalone cron entry, which chained these with
    # `&&` — rclone pulls new HAE Drive exports into hae_inbox BEFORE the
    # fetcher runs, and the fetcher is skipped entirely if that pull fails
    # (never silently syncs against a stale/incomplete inbox). Preserved
    # exactly here rather than assumed away — this exact gap was caught only
    # by reading the FULL crontab, not just the grep'd apple_to_hec.py line.
    import subprocess
    home = os.path.expanduser("~")
    env = dict(os.environ, PATH=f"{home}/bin:{os.environ.get('PATH', '')}")
    result = subprocess.run(
        ["rclone", "move", "narwhaldcGdrive:Health Auto Export/HAE_to_splunk",
         f"{home}/hae_inbox", "--include", "*.json", "--drive-use-trash=false"],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"rclone pull failed (exit {result.returncode}): {result.stderr.strip()}")
    source, targets = mod.load_config(None)
    return mod.sync(source, targets, dry_run=dry_run)


def _run_google(mod, dry_run, backfill, backfill_end):
    targets = mod.load_targets(None)
    return mod.sync(targets, dry_run=dry_run, backfill=backfill, backfill_end=backfill_end)


RUNNERS = {
    "oura": _run_oura,
    "garmin": _run_garmin,
    "withings": _run_withings,
    "apple": _run_apple,
    "google": _run_google,
}


def _run_instances(vendor, repo_dir, module_name, config, state, args, wanted_inst, results, restrict=False):
    """Run a vendor's EXTRA instances (config -> vendor.instances) as isolated subprocesses."""
    import subprocess
    for inst in (config.get(vendor) or {}).get("instances", []):
        name, sub_dir = inst.get("name"), inst.get("dir")
        if not name or not sub_dir:
            print(f"[{vendor}] bad instance entry (needs name + dir): {inst}", file=sys.stderr)
            continue
        key, explicit = f"{vendor}:{name}", f"{vendor}:{name}" in wanted_inst
        if restrict and not explicit:
            continue                      # --only vendor:name selected OTHER instances only
        if args.backfill and not explicit:
            continue                      # never backfill a person as a side effect of --only vendor
        interval = inst.get("interval_minutes", (config.get(vendor) or {}).get("interval_minutes", DEFAULT_INTERVAL_MINUTES))
        if not args.force and not explicit and not args.backfill and state.get(key) is not None \
                and (time.time() - state[key]) < interval * 60:
            print(f"[{key}] not due yet (interval={interval}m, last run {round((time.time() - state[key]) / 60, 1)}m ago)")
            results[key] = {"skipped": "not_due"}
            continue
        inst_dir = SRC_ROOT / repo_dir / sub_dir
        script = inst_dir / f"{module_name}.py"
        if not script.is_file():
            print(f"[{key}] FAILED: {script} not found", file=sys.stderr)
            results[key] = {"error": f"{script} not found"}
            continue
        cmd = [sys.executable, str(script)]
        if args.dry_run:
            cmd.append("--dry-run")
        if args.backfill:
            cmd += ["--backfill", args.backfill] + (["--backfill-end", args.backfill_end] if args.backfill_end else [])
        vt0 = time.time()
        try:
            # ORIG_ENV (not os.environ!): see the module note - no leakage between people.
            r = subprocess.run(cmd, cwd=str(inst_dir), env=ORIG_ENV, capture_output=True, text=True,
                               timeout=inst.get("timeout_s", 4 * 3600))
            sys.stdout.write(r.stdout)
            sys.stderr.write(r.stderr)
            if r.returncode != 0:
                raise RuntimeError(f"exit {r.returncode}")
            results[key] = {"ok": True}
            print(f"[{key}] ok in {round(time.time() - vt0, 1)}s")
            if not args.dry_run:
                state[key] = time.time()
                _save_json(STATE_FILE, state)
        except Exception as e:
            print(f"[{key}] FAILED after {round(time.time() - vt0, 1)}s: {type(e).__name__}: {e}", file=sys.stderr)
            results[key] = {"error": str(e)}


def main():
    ap = argparse.ArgumentParser(description="Wearables master fetcher — runs all vendor syncs in one process.")
    ap.add_argument("--only", metavar="vendor1,vendor2", help="limit to these vendors (comma-separated)")
    ap.add_argument("--dry-run", action="store_true", help="pass through to every vendor's sync()")
    ap.add_argument("--force", action="store_true", help="ignore each vendor's interval_minutes; run all selected vendors now")
    ap.add_argument("--backfill", metavar="YYYY-MM-DD", help="pass through to the selected vendor(s)' sync() (use with --only)")
    ap.add_argument("--backfill-end", metavar="YYYY-MM-DD", help="bound --backfill to end here instead of today/now; only meaningful with --backfill")
    args = ap.parse_args()

    wanted = set(args.only.split(",")) if args.only else {v for v, _, _ in VENDORS}
    # "vendor:instance" selects one extra instance (and not the vendor's main run)
    wanted_inst = {w for w in wanted if ":" in w}
    wanted = {w.split(":")[0] for w in wanted}
    unknown = wanted - {v for v, _, _ in VENDORS}
    if unknown:
        sys.exit(f"error: unknown vendor(s) in --only: {', '.join(sorted(unknown))}")
    only_instances = {w.split(":")[0] for w in wanted_inst} - ({a for a in (args.only or "").split(",") if ":" not in a})

    master_fp = _acquire_lock(MASTER_LOCK_FILE)
    if master_fp is None:
        print("another master_fetcher run holds the lock; exiting", file=sys.stderr)
        sys.exit(0)
    atexit.register(_release_lock, master_fp, MASTER_LOCK_FILE)

    config = _load_json(CONFIG_FILE, {})
    state = _load_json(STATE_FILE, {})

    orig_cwd = os.getcwd()
    t0 = time.time()
    results = {}
    for vendor, repo_dir, module_name in VENDORS:
        if vendor not in wanted:
            continue
        if vendor in only_instances:
            _run_instances(vendor, repo_dir, module_name, config, state, args, wanted_inst, results, restrict=True)
            continue
        # A deliberate backfill is an explicit one-off action, not a regular scheduled
        # sync -- it shouldn't be silently skipped as "not due yet", same as --force.
        if not args.force and not args.backfill and not _due(vendor, config, state):
            interval_min = (config.get(vendor) or {}).get("interval_minutes", DEFAULT_INTERVAL_MINUTES)
            mins_ago = round((time.time() - state[vendor]) / 60, 1)
            print(f"[{vendor}] not due yet (interval={interval_min}m, last run {mins_ago}m ago)")
            results[vendor] = {"skipped": "not_due"}
            continue
        try:
            mod, tools_dir = _load_vendor_module(repo_dir, module_name)
        except Exception as e:
            print(f"[{vendor}] FAILED to import: {type(e).__name__}: {e}", file=sys.stderr)
            results[vendor] = {"error": str(e)}
            continue

        # Match each vendor's own cron entry (`cd .../tools && python3.11 ./script.py`) —
        # at least one fetcher (Oura) resolves its targets/lock/checkpoint/dedup paths
        # relative to CWD, not __file__, so this orchestrator must chdir before calling
        # it or it silently misses its real config AND acquires the wrong lock file.
        # Sequential execution makes this safe: only one vendor's code runs at a time.
        os.chdir(tools_dir)
        try:
            vendor_lock = getattr(mod, "LOCK_FILE", None)
            lock_fp = _acquire_lock(vendor_lock) if vendor_lock else True
            if lock_fp is None:
                print(f"[{vendor}] SKIPPED — another process (old cron entry?) holds its lock", file=sys.stderr)
                results[vendor] = {"skipped": "locked"}
                continue

            vt0 = time.time()
            try:
                result = RUNNERS[vendor](mod, args.dry_run, args.backfill, args.backfill_end)
                results[vendor] = result
                print(f"[{vendor}] ok in {round(time.time() - vt0, 1)}s: {result}")
                if not args.dry_run:
                    # Only a REAL run counts toward the interval — dry-run testing must
                    # never make the live schedule think a vendor ran when it didn't.
                    state[vendor] = time.time()
                    _save_json(STATE_FILE, state)
            except Exception as e:
                print(f"[{vendor}] FAILED after {round(time.time() - vt0, 1)}s: {type(e).__name__}: {e}", file=sys.stderr)
                results[vendor] = {"error": str(e)}
            finally:
                if lock_fp not in (None, True):
                    _release_lock(lock_fp, vendor_lock)
        finally:
            os.chdir(orig_cwd)
        _run_instances(vendor, repo_dir, module_name, config, state, args, wanted_inst, results)

    failed = [v for v, r in results.items() if isinstance(r, dict) and "error" in r]
    print(f"\nmaster_fetcher complete in {round(time.time() - t0, 1)}s — "
          f"{len(results) - len(failed)}/{len(results)} vendors ok"
          + (f", failed: {', '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
