"""Repair folder access after a bad share publish.

WHY THIS EXISTS
    Versions up to 2.3.0 published shares by running

        icacls <folder> /inheritance:r /grant:r "Administrators":(OI)(CI)F

    on every top-level folder. `/inheritance:r` DELETES inherited permissions.
    Anyone whose access came from the parent folder rather than from an explicit
    rule on the folder itself lost it - and because shares use access-based
    enumeration, those folders then vanish from phones and laptops entirely.

    Typical symptom: you can still see one or two folders (the ones with
    explicit rules) and everything else is missing or empty.

WHAT THIS DOES
    Re-enables inheritance (`/inheritance:e`) on every managed folder, so the
    access defined on the parent flows back down. It then re-reads every folder
    and shows you who can get in.

    It does not delete anything, and it does not touch file contents.

USAGE
    Run it on the NAS, in an Administrator command prompt:

        python tools\\repair_access.py              show what is wrong, change nothing
        python tools\\repair_access.py --repair     actually re-enable inheritance

    It reads easynas_config.json next to the program for your folder layout, or
    pass folders yourself:

        python tools\\repair_access.py --root D:\\FamilyNAS --repair
"""
import argparse
import ctypes
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "smb_manager_GUI.pyw")


def load_app():
    """Borrow the real permission parser so this agrees with the app."""
    src = io.open(APP, encoding="utf-8").read()
    start = src.index("# NTFS PERMISSION MODEL")
    end = src.index("# STEP TRACKING")
    ns = {"re": re, "os": os, "subprocess": subprocess, "io": io, "json": json,
          "sys": sys, "ctypes": ctypes}
    exec(compile(src[start:end], APP, "exec"), ns)
    return ns


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def find_config():
    for base in (os.path.dirname(HERE), HERE, os.getcwd()):
        p = os.path.join(base, "easynas_config.json")
        if os.path.exists(p):
            return p
    return ""


def local_users():
    res = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-LocalUser | Where-Object { $_.Enabled -eq $True } | "
         "Select-Object -ExpandProperty Name"],
        capture_output=True, text=True)
    skip = {"administrator", "guest", "defaultaccount", "wdagutilityaccount"}
    return [u.strip() for u in res.stdout.splitlines()
            if u.strip() and u.strip().lower() not in skip
            and not u.strip().lower().startswith("defaultuser")]


def walk_folders(root, depth=2):
    out = [os.path.normpath(root)]

    def walk(parent, level):
        if level > depth:
            return
        try:
            for item in sorted(os.listdir(parent)):
                full = os.path.join(parent, item)
                if os.path.isdir(full):
                    out.append(full)
                    walk(full, level + 1)
        except Exception:
            pass

    walk(root, 1)
    return out


def main():
    ap = argparse.ArgumentParser(description="Repair folder access after a bad share publish.")
    ap.add_argument("--repair", action="store_true",
                    help="actually re-enable inheritance (without this, nothing is changed)")
    ap.add_argument("--root", action="append", default=[],
                    help="a folder to repair; may be given more than once")
    args = ap.parse_args()

    app = load_app()

    roots = [os.path.normpath(r) for r in args.root]
    if not roots:
        cfg_path = find_config()
        if not cfg_path:
            print("Could not find easynas_config.json. Pass --root yourself, e.g.")
            print(r"    python tools\repair_access.py --root D:\FamilyNAS --repair")
            return 2
        cfg = json.loads(io.open(cfg_path, encoding="utf-8").read())
        print("Using %s" % cfg_path)
        if cfg.get("root_dir"):
            roots.append(os.path.normpath(cfg["root_dir"]))
        for e in (cfg.get("extra_locations") or []):
            if isinstance(e, dict) and e.get("path"):
                roots.append(os.path.normpath(e["path"]))
        if cfg.get("archive_dir"):
            roots.append(os.path.normpath(cfg["archive_dir"]))

    roots = [r for r in roots if os.path.isdir(r)]
    if not roots:
        print("None of those folders exist.")
        return 2

    users = local_users()
    print("Accounts: %s" % (", ".join(users) or "(none found)"))
    print("Folders under: %s" % ", ".join(roots))
    if not is_admin():
        print("\n!! Not running as Administrator. Reading will work; repairing will not.")
        print("   Right-click Command Prompt and choose 'Run as administrator'.\n")

    folders = []
    for r in roots:
        folders.extend(walk_folders(r))
    seen, ordered = set(), []
    for f in folders:
        k = f.lower()
        if k not in seen:
            seen.add(k)
            ordered.append(f)

    print("\nBEFORE")
    print("-" * 78)
    locked = []
    for f in ordered:
        who = []
        for u in users:
            info = app["get_user_permission"](f, u)
            if info["level"] != "REMOVE":
                who.append("%s=%s" % (u, info["level"]))
        print("  %-52s %s" % (f[-52:], ", ".join(who) or "NOBODY"))
        if not who:
            locked.append(f)

    if not locked:
        print("\nEveryone still has access somewhere on every folder. Nothing to repair.")
        return 0

    print("\n%d folder(s) nobody can open." % len(locked))
    if not args.repair:
        print("\nThis was a dry run. Nothing was changed.")
        print("Run it again with --repair to re-enable inheritance on these folders:")
        print("    python tools\\repair_access.py --repair")
        return 1

    print("\nREPAIRING - re-enabling inheritance")
    print("-" * 78)
    failed = 0
    for f in ordered:
        res = subprocess.run('icacls "%s" /inheritance:e' % f, shell=True,
                             capture_output=True, text=True)
        if res.returncode != 0:
            failed += 1
            print("  FAILED %s" % f)
            print("         %s" % (res.stdout + res.stderr).strip()[:200])

    print("\nAFTER")
    print("-" * 78)
    still = []
    for f in ordered:
        who = []
        for u in users:
            info = app["get_user_permission"](f, u)
            if info["level"] != "REMOVE":
                who.append("%s=%s" % (u, info["level"]))
        print("  %-52s %s" % (f[-52:], ", ".join(who) or "NOBODY"))
        if not who:
            still.append(f)

    print()
    if still:
        print("%d folder(s) still have nobody on them:" % len(still))
        for f in still:
            print("   %s" % f)
        print("\nThat means the parent folder has no rules to inherit either. Open the app")
        print("and set who can open each of these on the 'People & Folders' tab.")
        return 1

    print("Every folder has somebody on it again.")
    print("Open a folder from a phone or laptop to confirm, then set the exact")
    print("per-person access you want on the 'People & Folders' tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
