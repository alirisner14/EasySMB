"""Update EasySMB on the server, from the dashboard, without touching the NAS.

WHY THIS IS A SEPARATE PROGRAM
    Windows will not let a running .exe overwrite itself, so the app cannot
    update in place. This runs as its own process: it waits for the app to
    exit, swaps the files, starts it again, and then checks that it actually
    came back.

THE POINT OF IT
    If the new build does not answer within the timeout, this puts the old one
    back and starts that instead. The worst case is meant to be "the update did
    not take", never "the server is unreachable and you have to go and plug a
    keyboard into it".

USAGE
    Normally the dashboard launches this for you. By hand:

        python tools\\remote_update.py --apply

        --apply        actually update (without it, nothing is changed)
        --timeout 90   seconds to wait for the new build to answer
        --no-build     skip PyInstaller even if running from an .exe

    Everything it does is appended to update_log.txt next to the app, which
    survives the restart and is how you find out what happened.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LOG_PATH = os.path.join(REPO, "update_log.txt")
TASK_NAME = "EasyNAS_WebDashboard"
PORT = 50505


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with io.open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run(cmd, cwd=None, timeout=900):
    """Run a command and return (ok, combined output)."""
    try:
        res = subprocess.run(cmd, cwd=cwd or REPO, shell=isinstance(cmd, str),
                             capture_output=True, text=True, timeout=timeout)
        return res.returncode == 0, ((res.stdout or "") + (res.stderr or "")).strip()
    except Exception as exc:
        return False, str(exc)


def health(timeout_s):
    """Poll the dashboard until it answers, or give up. -> (ok, version)."""
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/healthz" % PORT, timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data.get("ok"):
                    return True, data.get("version", "?")
        except Exception as exc:
            last = str(exc)
        time.sleep(2)
    return False, last


def dashboard_is_up():
    ok, _ = health(1)
    return ok


def stop_dashboard():
    run(["schtasks", "/end", "/tn", TASK_NAME])
    for _ in range(15):
        if not dashboard_is_up():
            return True
        time.sleep(1)
    return not dashboard_is_up()


def start_dashboard():
    return run(["schtasks", "/run", "/tn", TASK_NAME])[0]


def current_commit():
    ok, out = run(["git", "rev-parse", "HEAD"])
    return out.strip() if ok else ""


def running_from_exe():
    """Is the deployed dashboard a built .exe, or the .pyw source?"""
    ok, out = run(["schtasks", "/query", "/tn", TASK_NAME, "/fo", "list", "/v"])
    return ".exe" in out.lower() and "python" not in out.lower()


def main():
    ap = argparse.ArgumentParser(description="Update EasySMB on this server.")
    ap.add_argument("--apply", action="store_true",
                    help="actually update (without this, nothing is changed)")
    ap.add_argument("--timeout", type=int, default=90,
                    help="seconds to wait for the new build to answer")
    ap.add_argument("--no-build", action="store_true",
                    help="skip PyInstaller even when running from an .exe")
    args = ap.parse_args()

    log("=" * 70)
    log("Update requested (apply=%s)" % args.apply)

    before = current_commit()
    if not before:
        log("FAILED: this is not a git checkout, so there is nothing to update from.")
        return 2
    log("Currently at %s" % before[:12])

    ok, out = run(["git", "fetch", "--tags", "origin"])
    if not ok:
        log("FAILED: could not reach GitHub.\n%s" % out)
        return 2

    ok, behind = run(["git", "rev-list", "--count", "HEAD..origin/main"])
    behind = behind.strip() if ok else "?"
    if behind == "0":
        log("Already up to date. Nothing to do.")
        return 0
    log("%s new commit(s) available." % behind)

    if not args.apply:
        ok, out = run(["git", "log", "--oneline", "HEAD..origin/main"])
        log("Dry run. These would be applied:\n%s" % out)
        log("Run again with --apply to install them.")
        return 1

    exe_mode = (not args.no_build) and running_from_exe()
    exe_path = os.path.join(REPO, "dist", "smb_manager_GUI.exe")
    backup = exe_path + ".bak"
    log("Deployed as %s" % ("a built .exe" if exe_mode else "the .pyw source"))

    # ---- stop the running dashboard so files can be replaced -------------
    log("Stopping the dashboard...")
    if not stop_dashboard():
        log("FAILED: the dashboard is still running, so its files cannot be "
            "replaced. Nothing was changed.")
        return 2

    if exe_mode and os.path.exists(exe_path):
        try:
            if os.path.exists(backup):
                os.remove(backup)
            os.replace(exe_path, backup)
            log("Kept the current build as %s" % os.path.basename(backup))
        except Exception as exc:
            log("FAILED: could not set aside the current build: %s" % exc)
            start_dashboard()
            return 2

    def roll_back(why):
        log("ROLLING BACK: %s" % why)
        run(["git", "reset", "--hard", before])
        if exe_mode and os.path.exists(backup):
            try:
                if os.path.exists(exe_path):
                    os.remove(exe_path)
                os.replace(backup, exe_path)
                log("Put the previous build back.")
            except Exception as exc:
                log("Could not restore the previous build: %s" % exc)
        start_dashboard()
        ok2, ver2 = health(60)
        if ok2:
            log("The previous version is running again (v%s). The update did not "
                "take, but the server is reachable." % ver2)
            return 1
        log("!! The previous version did not come back either. You will have to "
            "start EasySMB on the server itself.")
        return 3

    # ---- take the new code ----------------------------------------------
    ok, out = run(["git", "reset", "--hard", "origin/main"])
    if not ok:
        return roll_back("could not check out the new version.\n%s" % out)
    log("Updated to %s" % current_commit()[:12])

    if exe_mode:
        log("Building the new .exe (this takes a minute)...")
        ok, out = run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                       os.path.join(REPO, "smb_manager_GUI.spec")])
        tail = "\n".join(out.splitlines()[-12:])
        if not ok or not os.path.exists(exe_path):
            return roll_back("the new version did not build.\n%s" % tail)
        log("Built.")

    # ---- start it and make sure it answers -------------------------------
    log("Starting the new version...")
    if not start_dashboard():
        return roll_back("the dashboard task would not start.")

    ok, ver = health(args.timeout)
    if not ok:
        return roll_back("the new version did not answer within %ds (%s)"
                         % (args.timeout, ver))

    log("SUCCESS: now running v%s and answering normally." % ver)
    if exe_mode and os.path.exists(backup):
        log("The previous build is still at %s if you want to go back."
            % os.path.basename(backup))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log("UNEXPECTED FAILURE: %s" % exc)
        log("Trying to start the dashboard so the server stays reachable.")
        start_dashboard()
        sys.exit(3)
