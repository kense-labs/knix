#!/usr/bin/env python3
"""Collect all public Knix repositories into knix as source snapshots.

For each repo in repos.json:
  * shallow-clone the default branch (depth=1, single branch, no tags) into a
    temporary directory outside the worktree,
  * copy the checked-out files into a staging snapshot, skipping every .git
    entry so no nested repository metadata lands in the monorepo,
  * atomically swap the staging snapshot into repos/<name>, moving the previous
    snapshot into the temporary trash directory instead of deleting it,
  * record the exact commit SHA and refresh time back into repos.json.

Usage:
    python3 scripts/collect-knix.py                 # refresh all snapshots
    python3 scripts/collect-knix.py --jobs 4       # limit concurrency
    python3 scripts/collect-knix.py --list         # refresh repo list via gh api
    python3 scripts/collect-knix.py --only linux,m1n1

Requires: git, network access to github.com. `--list` additionally requires
an authenticated `gh` CLI.
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "repos.json")
REPOS_DIR = os.path.join(ROOT, "repos")
ORG = os.environ.get("KNIX_ORG", "Knix")


def run(cmd, timeout=3600):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def load_manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def refresh_list(manifest):
    cmd = [
        "gh", "api", "--paginate",
        "orgs/%s/repos?per_page=100&type=public" % ORG,
        "--jq", ".[] | {name, default_branch, html_url, description, "
                "archived, fork, size, pushed_at, "
                "license: (.license.spdx_id // \"NOASSERTION\")}",
    ]
    r = run(cmd, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("gh api failed: " + r.stderr.strip())
    decoder = json.JSONDecoder()
    text, i, found = r.stdout, 0, []
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        obj, i = decoder.raw_decode(text, i)
        found.append(obj)
    old = {e["name"]: e for e in manifest["repositories"]}
    entries = []
    for o in found:
        e = old.get(o["name"], {})
        e.update({
            "name": o["name"],
            "source_url": o["html_url"],
            "clone_url": "git@github.com:%s/%s.git" % (ORG, o["name"]),
            "default_branch": o["default_branch"],
            "description": o["description"] or "",
            "archived": o["archived"],
            "fork": o["fork"],
            "license": o["license"],
            "upstream_pushed_at": o["pushed_at"],
            "upstream_size_kb": o["size"],
            "local_path": "repos/" + o["name"],
        })
        e.setdefault("snapshot_sha", None)
        e.setdefault("snapshot_at", None)
        entries.append(e)
    manifest["repositories"] = sorted(entries, key=lambda e: e["name"].lower())
    manifest["count"] = len(entries)
    return manifest


def snapshot_one(entry, workdir, jobs_slot):
    name = entry["name"]
    branch = entry["default_branch"]
    clone_dir = os.path.join(workdir, "clones", name)
    snap_dir = os.path.join(workdir, "snaps", name)
    os.makedirs(snap_dir, exist_ok=True)
    url = entry.get("clone_url") or "git@github.com:%s/%s.git" % (ORG, name)
    cmd = ["git", "clone", "--depth", "1", "--single-branch", "--no-tags",
           "--branch", branch, url, clone_dir]
    r = run(cmd)
    if r.returncode != 0:
        return {"name": name, "ok": False, "error": (r.stderr or r.stdout).strip()[-500:]}
    sha = run(["git", "-C", clone_dir, "rev-parse", "HEAD"]).stdout.strip()
    shutil.copytree(clone_dir, snap_dir, dirs_exist_ok=True, symlinks=True,
                    ignore=shutil.ignore_patterns(".git"))
    return {"name": name, "ok": True, "sha": sha, "snap_dir": snap_dir}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--list", action="store_true", help="refresh repo list from GitHub")
    ap.add_argument("--only", help="comma-separated repo names to refresh")
    args = ap.parse_args()

    manifest = load_manifest()
    if args.list:
        manifest = refresh_list(manifest)
    entries = manifest["repositories"]
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e["name"] in wanted]
        if not entries:
            print("no matching repositories", file=sys.stderr)
            return 1

    workdir = tempfile.mkdtemp(prefix="knix-snapshot-")
    trash = os.path.join(workdir, "trash")
    os.makedirs(trash, exist_ok=True)
    os.makedirs(REPOS_DIR, exist_ok=True)
    results = []
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(snapshot_one, e, workdir, i): e
                for i, e in enumerate(entries)}
        for fut in cf.as_completed(futs):
            try:
                res = fut.result()
            except Exception as exc:
                res = {"name": futs[fut]["name"], "ok": False, "error": repr(exc)}
            results.append(res)
            print("[%s] %s" % ("OK" if res["ok"] else "FAIL", res["name"]), flush=True)

    by_name = {e["name"]: e for e in manifest["repositories"]}
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for res in results:
        if not res["ok"]:
            print("  -> %s: %s" % (res["name"], res.get("error", "")), file=sys.stderr)
            continue
        name = res["name"]
        dest = os.path.join(REPOS_DIR, name)
        if os.path.exists(dest):
            os.rename(dest, os.path.join(trash, name))
        os.rename(res["snap_dir"], dest)
        by_name[name]["snapshot_sha"] = res["sha"]
        by_name[name]["snapshot_at"] = stamp

    manifest["generated_at"] = stamp
    manifest["count"] = len(manifest["repositories"])
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    failed = [r["name"] for r in results if not r["ok"]]
    print("snapshots refreshed: %d ok, %d failed; temp files in %s"
          % (len(results) - len(failed), len(failed), workdir))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
