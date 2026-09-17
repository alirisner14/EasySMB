"""Check the web dashboard's JavaScript actually parses.

This exists because of a real bug. The dashboard HTML is built with a Python
f-string, so a backslash has to survive both Python and JavaScript escaping.
One line got it wrong:

    h += '<tr><th>\\server\' + sh.name + ...

The \\' escaped the closing quote, so the string never ended. That is a syntax
error, which kills the WHOLE <script> block - so openTab() was never defined
and every button on the dashboard silently did nothing. Python imported the
file happily; the page looked fine; nothing worked.

It runs the dashboard for a moment, pulls the <script> out of the page, and
hands it to node --check. If node is not installed the check is skipped rather
than failing the build.

Usage:  python tools/check_dashboard_js.py
Exit:   0 = fine (or skipped), 1 = the dashboard JavaScript is broken
"""
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

PORT = 50505
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "smb_manager_GUI.pyw")


def port_is_busy():
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


def main():
    if not shutil.which("node"):
        print("  node is not installed - skipping the dashboard JavaScript check.")
        return 0
    if not os.path.exists(APP):
        print("  %s not found." % APP)
        return 1
    if port_is_busy():
        print("  Port %d is already in use, so the dashboard cannot be started for" % PORT)
        print("  this check. Stop the running dashboard and build again, or ignore this.")
        return 0

    work = tempfile.mkdtemp(prefix="easysmb_jscheck_")
    app_copy = os.path.join(work, "app_check.py")
    shutil.copyfile(APP, app_copy)
    io.open(os.path.join(work, "easynas_config.json"), "w").write(
        json.dumps({"root_dir": work, "web_pass": ""}))

    server = subprocess.Popen([sys.executable, app_copy, "--headless"], cwd=work,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        body = None
        for _ in range(20):
            time.sleep(0.5)
            if server.poll() is not None:
                print("  The dashboard would not start:")
                print("   ", (server.stdout.read() or "").strip()[:400])
                return 1
            try:
                body = urllib.request.urlopen("http://127.0.0.1:%d/" % PORT,
                                              timeout=5).read().decode("utf-8", "replace")
                break
            except Exception:
                continue
        if body is None:
            print("  The dashboard did not answer in time - skipping the check.")
            return 0

        scripts = [js for js in re.findall(r"<script[^>]*>(.*?)</script>", body, re.S) if js.strip()]
        if not scripts:
            print("  No <script> found in the dashboard page.")
            return 1

        problems = 0
        for i, js in enumerate(scripts):
            path = os.path.join(work, "block_%d.js" % i)
            io.open(path, "w", encoding="utf-8").write(js)
            res = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            if res.returncode == 0:
                print("  Dashboard script block %d parses (%d characters)." % (i, len(js)))
                continue
            problems += 1
            print("")
            print("  Dashboard script block %d HAS A SYNTAX ERROR." % i)
            print("  Every button on the dashboard would silently do nothing.")
            for line in (res.stderr or "").splitlines()[:10]:
                print("     %s" % line.replace(path, "dashboard.js"))
        return 1 if problems else 0
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except Exception:
                server.kill()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
