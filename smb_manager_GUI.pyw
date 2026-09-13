import base64
import ctypes
import datetime
import time
import traceback
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import tkinter as tk
import urllib.parse
import webbrowser
import http.server
from tkinter import filedialog, messagebox, ttk


APP_VERSION = "2.0.0"

# =========================================================================
# SYSTEM & ADMIN HELPER FUNCTIONS
# =========================================================================
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def elevation_command():
    """What to hand ShellExecuteW to relaunch this program elevated.

    Returns (executable, parameters, working_directory).
    """
    if getattr(sys, "frozen", False):
        # A PyInstaller build relaunches itself; argv[0] IS the exe, so passing
        # it again would hand the program its own path as argument 1.
        executable = sys.executable
        args = list(sys.argv[1:])
        workdir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        executable = sys.executable
        # Swap the console launcher for the windowed one so no black box flashes
        # up. Match on the file name, not anywhere in the path.
        if os.path.basename(executable).lower() == "python.exe":
            windowed = os.path.join(os.path.dirname(executable), "pythonw.exe")
            if os.path.exists(windowed):
                executable = windowed
        # An elevated process does NOT inherit the current directory, so a
        # relative script path would simply not be found.
        script = os.path.abspath(sys.argv[0])
        args = [script] + list(sys.argv[1:])
        workdir = os.path.dirname(script)

    return executable, " ".join('"%s"' % a for a in args), workdir


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong), ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p), ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p), ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong), ("hIcon", ctypes.c_void_p), ("hProcess", ctypes.c_void_p),
    ]


def shell_execute_elevated(executable, params, workdir):
    """Launch elevated via ShellExecuteEx. -> (started, windows_error_code)

    Plain ShellExecuteW cannot tell you that the UAC prompt was declined - it
    just returns 5, the same as a genuine access denial. ShellExecuteEx sets
    the thread error to ERROR_CANCELLED instead, which is worth distinguishing.
    """
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040             # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = params
    info.lpDirectory = workdir
    info.nShow = 1                      # SW_SHOWNORMAL
    ctypes.set_last_error(0)
    ok = shell32.ShellExecuteExW(ctypes.byref(info))
    return bool(ok), ctypes.get_last_error()


def run_as_admin():
    """Ask Windows for an elevated copy of this program.

    Returns (started, reason). The caller decides what to do when it did not
    start - the old code exited silently, so declining the UAC prompt looked
    exactly like the app crashing.
    """
    executable, params, workdir = elevation_command()
    try:
        started, err = shell_execute_elevated(executable, params, workdir)
    except Exception as e:
        return False, "Windows could not start an elevated copy:\n%s" % e

    if started:
        return True, ""
    if err == 1223:                     # ERROR_CANCELLED
        return False, ("You chose No on the Windows administrator prompt.\n\n"
                       "EasySMB needs administrator rights to change folder permissions, "
                       "publish network shares or create user accounts.")
    if err == 5:                        # ERROR_ACCESS_DENIED
        return False, ("Windows refused the request (access denied). The account you are "
                       "signed in with may not be allowed to elevate.")
    return False, ("Windows would not start an elevated copy.\n\n"
                   "Error %d while launching:\n%s %s" % (err, executable, params))


CONFIG_DEFAULTS = {
    "root_dir": "C:\\",
    "web_pass": "",
    # Accounts that exist on this PC but are not NAS users - the machine's own
    # sign-in account, for example. Managed from the Users tab.
    "hidden_users": [],
    # "folders" = publish each top-level folder, so tapping the server goes
    # straight to Users / Family_Shared / Resources with no wrapper folder.
    "share_mode": "folders",
}


def config_path():
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.join(base_dir, "easynas_config.json")


def load_config():
    """Loads settings shared by the desktop app and the headless web server."""
    cfg = dict(CONFIG_DEFAULTS)
    try:
        with open(config_path(), "r") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            cfg.update(stored)
    except Exception:
        pass
    if not isinstance(cfg.get("hidden_users"), list):
        cfg["hidden_users"] = []
    return cfg


def save_config(updates):
    """Merge changes into the config file. -> (ok, error)"""
    cfg = load_config()
    cfg.update(updates)
    try:
        with open(config_path(), "w") as f:
            json.dump(cfg, f, indent=2)
        return True, ""
    except Exception as e:
        return False, str(e)


# =========================================================================
# NTFS PERMISSION MODEL
# Shared by the desktop GUI and the headless web dashboard so that both
# surfaces read and report permissions in exactly the same way.
# =========================================================================

# Ordered from least to most access. "OTHER" means the folder carries an ACE
# this tool did not create and cannot describe with one of its own levels.
PERM_RANK = {"REMOVE": 0, "OTHER": 1, "RX": 2, "C": 3, "M": 4, "F": 5}
PERM_ORDER = ["REMOVE", "RX", "C", "M", "F"]
PERM_LABELS = {
    "REMOVE": "No Access",
    "RX": "Read Only",
    "C": "Read + Add Files",
    "M": "Read / Write / Delete",
    "F": "Full Control",
    "OTHER": "Custom (set outside this app)",
}
# What we hand to icacls /grant:r for each level.
PERM_ICACLS = {"RX": "RX", "C": "(RX,W)", "M": "M", "F": "F"}

# Inheritance / propagation flags, not access rights.
_ACL_FLAGS = {"I", "OI", "CI", "IO", "NP"}

# Split "principal:(OI)(CI)(F)" at the colon that introduces the mask. A Windows
# path contains a colon ("C:\...") but never a colon immediately before "(",
# so this stays correct even on the first line where icacls prefixes the path.
_ACE_RE = re.compile(r"^(?P<principal>.*?):(?=\()(?P<mask>\(.*)$")
_ICACLS_TRAILER_RE = re.compile(r"^\s*(Successfully processed|Failed processing)", re.I)


def _mask_tokens(mask):
    """'(I)(OI)(CI)(RX,W)' -> ['I', 'OI', 'CI', 'RX', 'W']"""
    return [t.strip() for grp in re.findall(r"\(([^()]*)\)", mask) for t in grp.split(",") if t.strip()]


def classify_mask(tokens):
    """Map an icacls right-mask onto one of our permission levels."""
    t = {x.upper() for x in tokens} - _ACL_FLAGS
    if not t or t == {"N"}:
        return "REMOVE"
    if "F" in t or "GA" in t:
        return "F"
    if "M" in t:
        return "M"
    # Expanded / specific-rights form, e.g. (Rc,S,RA,REA,RD,X,DE,...)
    read = bool(t & {"R", "RX", "RD", "REA", "RA", "X", "GR", "GE"})
    write = bool(t & {"W", "WD", "AD", "WEA", "WA", "GW"})
    delete = bool(t & {"D", "DE", "DC"})
    if read and write and delete:
        return "M"
    if read and write:
        return "C"
    if read:
        return "RX"
    return "OTHER"


def _principal_matches(principal, user):
    """'DESKTOP-A1\\alice' matches 'alice'. Exact account match only - no
    substring test, so a user named 'media' no longer matches a Media folder."""
    p = principal.strip().lower()
    u = user.strip().lower()
    if not p or not u:
        return False
    return p == u or p.split("\\")[-1] == u.split("\\")[-1]


def parse_icacls_output(stdout, path):
    """Turn raw `icacls <path>` text into a list of {'principal', 'mask'} ACEs."""
    entries, pending, first = [], None, True
    for raw in stdout.splitlines():
        if not raw.strip() or _ICACLS_TRAILER_RE.match(raw):
            continue
        work = raw.strip()
        if first:
            # icacls prints the folder path in front of the very first ACE.
            for cand in (path, os.path.normpath(path), path.rstrip("\\/")):
                if cand and work.lower().startswith(cand.lower()):
                    work = work[len(cand):].strip()
                    break
            first = False
        m = _ACE_RE.match(work)
        if m:
            if pending:
                entries.append(pending)
            pending = {"principal": m.group("principal").strip(), "mask": m.group("mask").strip()}
        elif pending:
            # icacls wraps very long expanded masks onto continuation lines.
            pending["mask"] += work
    if pending:
        entries.append(pending)
    return entries


def read_folder_acl(path):
    """Run icacls against a folder. Returns (aces, error_string)."""
    try:
        res = subprocess.run(f'icacls "{path}"', shell=True, capture_output=True, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        return [], str(e)
    if res.returncode != 0:
        return [], (res.stderr.strip() or res.stdout.strip() or "icacls returned %s" % res.returncode)
    return parse_icacls_output(res.stdout, path), ""


def get_user_permission(path, user):
    """The single source of truth for 'what can this user do in this folder?'

    Returns a dict:
      level      - one of PERM_LABELS' keys
      label      - human readable form of level
      inherited  - True if the winning ACE came from the parent folder
      deny       - True if an explicit DENY ACE exists for this user
      raw        - the matching ACE lines, for the details popup
      error      - non-empty if the folder could not be read at all
    """
    info = {"level": "REMOVE", "label": PERM_LABELS["REMOVE"], "inherited": False,
            "deny": False, "raw": "", "error": ""}
    if not path or not user:
        info["error"] = "Missing folder or user."
        return info

    aces, err = read_folder_acl(path)
    if err:
        info["error"] = err
        return info

    best_rank, matched = -1, []
    for ace in aces:
        if not _principal_matches(ace["principal"], user):
            continue
        matched.append("%s:%s" % (ace["principal"], ace["mask"]))
        tokens = _mask_tokens(ace["mask"])
        upper = {t.upper() for t in tokens}
        if "DENY" in upper:
            info["deny"] = True
            continue
        level = classify_mask(tokens)
        rank = PERM_RANK.get(level, 1)
        if rank > best_rank:
            best_rank, info["level"] = rank, level
            info["inherited"] = "I" in upper

    info["label"] = PERM_LABELS.get(info["level"], info["level"])
    info["raw"] = "\n".join(matched)
    return info


def describe_permission(info):
    """Short badge text for a permission dict, e.g. 'Read Only (inherited)'."""
    if info.get("error"):
        return "Unreadable"
    text = info.get("label", "No Access")
    if info.get("deny"):
        text = "DENY rule present - " + text
    elif info.get("inherited") and info.get("level") != "REMOVE":
        text += " (inherited)"
    return text


def apply_user_permission(path, user, level):
    """Write one permission level and then read it back to confirm.

    Returns (ok, verified_info, log_text). ok is True only when the folder
    actually reports the level we asked for afterwards.
    """
    if level == "REMOVE":
        cmd = 'icacls "%s" /remove:g "%s"' % (path, user)
    else:
        cmd = 'icacls "%s" /grant:r "%s":(OI)(CI)%s' % (path, user, PERM_ICACLS.get(level, "RX"))

    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        log = (res.stdout or "") + (res.stderr or "")
        run_failed = res.returncode != 0
    except Exception as e:
        log, run_failed = str(e), True

    after = get_user_permission(path, user)
    ok = (not run_failed) and (not after.get("error")) and after.get("level") == level
    if ok and level != "REMOVE" and after.get("deny"):
        ok = False
        log += "\nA DENY rule on this folder still overrides the access you granted."
    if not ok and after.get("inherited") and after.get("level") != "REMOVE":
        log += ("\nThis folder still inherits '%s' from its parent. Break inheritance on it "
                "(the 'Save All Changes' button does this) before this setting can take effect."
                % PERM_LABELS.get(after.get("level"), after.get("level")))
    return ok, after, (cmd + "\n" + log.strip()).strip()


# =========================================================================
# STEP TRACKING
# Every action in this app runs through an ActionLog. Each step announces
# itself before it runs, then records success or failure together with the
# real console output, so nothing ever succeeds or fails silently.
# =========================================================================
STEP_RUNNING, STEP_OK, STEP_FAIL, STEP_SKIP = "running", "ok", "fail", "skip"
STEP_MARK = {STEP_RUNNING: "  ...  ", STEP_OK: "   OK  ", STEP_FAIL: " FAILED", STEP_SKIP: " SKIP  "}


def run_console(cmd, shell=True, timeout=None):
    """Run one command and always come back with its output. -> (ok, output)"""
    try:
        res = subprocess.run(cmd, shell=shell, capture_output=True, text=True,
                             timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        parts = [(res.stdout or "").strip(), (res.stderr or "").strip()]
        out = "\n".join(p for p in parts if p)
        return res.returncode == 0, (out or "(the command produced no output)")
    except subprocess.TimeoutExpired:
        return False, "The command did not finish within %s seconds and was stopped." % timeout
    except Exception as e:
        return False, "Could not start the command: %s" % e


def run_powershell(script, timeout=None):
    return run_console(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                       shell=False, timeout=timeout)


def powershell_json(script, timeout=None):
    """Run PowerShell and parse its JSON. -> (data_or_None, error_string)"""
    ok, out = run_powershell(script, timeout=timeout)
    if not ok:
        return None, out
    text = out.strip()
    if not text or text == "(the command produced no output)":
        return [], ""
    try:
        data = json.loads(text)
    except Exception as e:
        return None, "Could not read PowerShell's reply (%s):\n%s" % (e, out)
    return (data if isinstance(data, list) else [data]), ""


class ActionLog:
    """One multi-step operation, tracked step by step."""

    def __init__(self, title, notify=None):
        self.title = title
        self.steps = []
        self.notify = notify          # called as notify(log, step) on every change
        self.started = datetime.datetime.now()
        self.finished = None

    # -- reporting a step -------------------------------------------------
    def _emit(self, step):
        if self.notify:
            try:
                self.notify(self, step)
            except Exception:
                pass

    def begin(self, label):
        step = {"n": len(self.steps) + 1, "label": label, "status": STEP_RUNNING,
                "detail": "", "output": ""}
        self.steps.append(step)
        self._emit(step)
        return step

    def _end(self, step, status, detail="", output=""):
        step["status"], step["detail"], step["output"] = status, detail, output
        self._emit(step)
        return status == STEP_OK

    def ok(self, step, detail="", output=""):
        return self._end(step, STEP_OK, detail, output)

    def fail(self, step, detail="", output=""):
        return self._end(step, STEP_FAIL, detail, output)

    def skip(self, step, detail="", output=""):
        return self._end(step, STEP_SKIP, detail, output)

    def note(self, label, detail=""):
        """Record something that already happened, with no work to do."""
        return self._end(self.begin(label), STEP_SKIP, detail)

    # -- running work as a step -------------------------------------------
    def run(self, label, cmd, shell=True, detail="", verify=None):
        """Run a console command as one tracked step.

        verify() -> (ok, detail) runs after a command that reported success,
        because Windows tools routinely exit 0 without doing anything.
        """
        step = self.begin(label)
        ok, out = run_console(cmd, shell=shell)
        if not ok:
            return self.fail(step, "The command reported an error.", out)
        if verify is not None:
            try:
                vok, vdetail = verify()
            except Exception as e:
                return self.fail(step, "Could not confirm the result: %s" % e, out)
            if not vok:
                return self.fail(step, vdetail or "The command exited cleanly but the change is not there.", out)
            return self.ok(step, vdetail or detail, out)
        return self.ok(step, detail, out)

    def do(self, label, fn):
        """Run a python callable as one tracked step. fn() -> (ok, detail, output)"""
        step = self.begin(label)
        try:
            ok, detail, out = fn()
        except Exception:
            return self.fail(step, "This step hit an unexpected error.", traceback.format_exc())
        return self._end(step, STEP_OK if ok else STEP_FAIL, detail, out)

    def skip_rest(self, reason):
        """Mark that we stopped early, so the report says so explicitly."""
        self.note("Remaining steps not attempted", reason)

    # -- results ----------------------------------------------------------
    @property
    def counts(self):
        c = {STEP_OK: 0, STEP_FAIL: 0, STEP_SKIP: 0, STEP_RUNNING: 0}
        for s in self.steps:
            c[s["status"]] = c.get(s["status"], 0) + 1
        return c

    @property
    def failed(self):
        return any(s["status"] == STEP_FAIL for s in self.steps)

    @property
    def first_failure(self):
        for s in self.steps:
            if s["status"] == STEP_FAIL:
                return s
        return None

    def headline(self):
        c = self.counts
        if self.failed:
            bad = self.first_failure
            return "%s - FAILED at step %d of %d: %s" % (self.title, bad["n"], len(self.steps), bad["label"])
        if c[STEP_OK]:
            extra = " (%d skipped)" % c[STEP_SKIP] if c[STEP_SKIP] else ""
            return "%s - all %d step%s succeeded%s." % (self.title, c[STEP_OK],
                                                        "" if c[STEP_OK] == 1 else "s", extra)
        return "%s - nothing to do." % self.title

    def report(self):
        total = len(self.steps)
        lines = [self.title, "=" * max(len(self.title), 70),
                 "Started %s" % self.started.strftime("%Y-%m-%d %H:%M:%S"), ""]
        for s in self.steps:
            lines.append("[%d/%d] %s  %s" % (s["n"], total, STEP_MARK.get(s["status"], "  ?  "), s["label"]))
            if s["detail"]:
                for l in s["detail"].splitlines():
                    lines.append("               %s" % l)
            if s["output"] and s["status"] == STEP_FAIL:
                lines.append("               ----- console output -----")
                for l in s["output"].splitlines():
                    lines.append("               %s" % l)
                lines.append("               --------------------------")
            elif s["output"] and s["output"] != "(the command produced no output)":
                for l in s["output"].splitlines()[:4]:
                    lines.append("               > %s" % l)
            lines.append("")

        c = self.counts
        secs = ((self.finished or datetime.datetime.now()) - self.started).total_seconds()
        lines.append("-" * 70)
        lines.append("%d succeeded, %d failed, %d skipped   -   finished in %.1fs"
                     % (c[STEP_OK], c[STEP_FAIL], c[STEP_SKIP], secs))
        bad = self.first_failure
        if bad:
            lines.append("")
            lines.append("The first thing that went wrong was step %d: %s" % (bad["n"], bad["label"]))
            if bad["detail"]:
                lines.append("  %s" % bad["detail"].splitlines()[0])
            lines.append("Everything above step %d did succeed." % bad["n"])
        return "\n".join(lines)


# =========================================================================
# LOCAL ACCOUNTS
# Built-in Windows accounts are identified by their well-known SID suffix
# rather than by name, so renamed or non-English accounts are still caught.
# Anything else the admin does not want listed goes in hidden_users.
# =========================================================================
BUILTIN_USER_RIDS = {"500", "501", "502", "503", "504"}   # Administrator, Guest, krbtgt, DefaultAccount, WDAG


def list_local_users(hidden=None):
    """Enabled local accounts worth managing. -> (users, hidden_found, error)"""
    hidden_lower = {h.strip().lower() for h in (hidden or []) if h and h.strip()}
    data, err = powershell_json(
        "Get-LocalUser | Where-Object { $_.Enabled -eq $True } | "
        "Select-Object Name,@{N='Sid';E={$_.SID.Value}} | ConvertTo-Json -Compress")
    if err:
        return [], [], err

    users, skipped = [], []
    for item in data:
        name = (item.get("Name") or "").strip()
        sid = (item.get("Sid") or "")
        if not name:
            continue
        rid = sid.rsplit("-", 1)[-1] if sid else ""
        if rid in BUILTIN_USER_RIDS or name.lower().startswith("defaultuser"):
            continue
        if name.lower() in hidden_lower:
            skipped.append(name)
            continue
        users.append(name)
    return sorted(users, key=lambda s: s.lower()), sorted(skipped, key=lambda s: s.lower()), ""


def list_all_local_users():
    """Every enabled non-built-in account, including ones the admin hid."""
    users, hidden, err = list_local_users(hidden=None)
    return users, err


# =========================================================================
# SMB SHARE LAYOUT
#
# What a phone shows when you tap \\<server-ip> is the SHARE list, not the
# folder tree. Sharing the parent folder (\\ip\FamilyNAS) forces everyone
# through one extra tap, and leaving an older per-folder share behind makes
# the same folder appear twice. Both are layout problems, not permissions.
# =========================================================================
SHARE_MODE_FOLDERS = "folders"   # \\ip\Users, \\ip\Family_Shared, \\ip\Resources
SHARE_MODE_MASTER = "master"     # \\ip\FamilyNAS -> then tap again


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _norm(path):
    return os.path.normpath(str(path)).rstrip("\\/").lower()


def list_smb_shares():
    """Every SMB share on this machine. -> (shares, error)"""
    data, err = powershell_json(
        "Get-SmbShare | Select-Object Name,Path,Special,FolderEnumerationMode | ConvertTo-Json -Compress")
    if err:
        return [], err
    shares = []
    for d in data:
        mode = d.get("FolderEnumerationMode")
        shares.append({
            "name": (d.get("Name") or "").strip(),
            "path": (d.get("Path") or "").strip(),
            "special": bool(d.get("Special")),
            "abe": mode in (1, "1", "AccessBased"),
        })
    return shares, ""


def top_level_folders(root_dir):
    """The folders that should appear when a user taps the server."""
    out = []
    try:
        for item in sorted(os.listdir(root_dir), key=lambda s: s.lower()):
            full = os.path.join(root_dir, item)
            if os.path.isdir(full):
                out.append(full)
    except Exception:
        pass
    return out


def list_managed_folders(root_dir, depth=2):
    """Every folder we manage permissions for: the root, each top-level folder,
    and one level below those - so Users\\Ali and Resources\\Manuals are both
    covered without hard-coding a folder called "Users"."""
    root_dir = os.path.normpath(root_dir)
    found = [root_dir]

    def walk(parent, level):
        if level > depth:
            return
        try:
            children = sorted(os.listdir(parent), key=lambda s: s.lower())
        except Exception:
            return
        for item in children:
            full = os.path.join(parent, item)
            if os.path.isdir(full):
                found.append(full)
                walk(full, level + 1)

    walk(root_dir, 1)
    return sorted({f.lower(): f for f in found}.values(), key=lambda s: s.lower())


def plan_shares(root_dir, mode):
    """What the share list should look like. -> [(share_name, path)]"""
    root_dir = os.path.normpath(root_dir)
    if mode == SHARE_MODE_MASTER:
        name = os.path.basename(root_dir.rstrip("\\/"))
        if not name or (len(name) == 2 and name[1] == ":"):
            name = "RootNAS"
        return [(name, root_dir)]
    return [(os.path.basename(f), f) for f in top_level_folders(root_dir)]


def is_under_root(path, root_dir):
    """True if this path is the NAS root or lives inside it."""
    p, r = _norm(path), _norm(root_dir)
    return bool(p) and (p == r or p.startswith(r + os.sep.lower()))


def shares_to_remove(shares, root_dir, planned):
    """Shares under our root that are not part of the plan.

    Admin shares (C$, IPC$, ADMIN$) are never touched.
    """
    root = _norm(root_dir)
    keep = {(n.lower(), _norm(p)) for n, p in planned}
    stale = []
    for sh in shares:
        if sh["special"] or not sh["path"]:
            continue
        p = _norm(sh["path"])
        # The old cleanup used -like 'root\*', which never matched a share
        # pointing at the root folder itself. That is why duplicates survived.
        if not is_under_root(sh["path"], root_dir):
            continue
        if (sh["name"].lower(), p) in keep:
            continue
        stale.append(sh)
    return stale


def diagnose_network_view(root_dir, shares=None):
    """Explain, in plain terms, what a phone will show when it taps the server."""
    if shares is None:
        shares, err = list_smb_shares()
        if err:
            return "Could not read the share list:\n%s" % err
    root = _norm(root_dir)
    mine = [s for s in shares if not s["special"]]

    lines = ["WHAT THE NETWORK CURRENTLY SEES",
             "=" * 70,
             "",
             "Tapping \\\\<this server> shows this list of shares:", ""]
    if not mine:
        lines.append("   (no shares published yet)")
    for s in sorted(mine, key=lambda x: x["name"].lower()):
        lines.append("   \\\\server\\%-20s ->  %s%s"
                     % (s["name"], s["path"], "" if s["abe"] else "   [hidden-folders OFF]"))
    lines.append("")

    problems = []

    root_shares = [s for s in mine if _norm(s["path"]) == root]
    for s in root_shares:
        problems.append(
            "'%s' shares the whole parent folder (%s).\n"
            "   Everyone has to tap '%s' first before reaching Users / Family_Shared /\n"
            "   Resources. That is the extra level you are seeing on mobile."
            % (s["name"], s["path"], s["name"]))

    by_path = {}
    for s in mine:
        by_path.setdefault(_norm(s["path"]), []).append(s["name"])
    for p, names in by_path.items():
        if len(names) > 1:
            problems.append("The same folder is shared %d times, as %s.\n"
                            "   It will appear more than once on every client."
                            % (len(names), " and ".join("'%s'" % n for n in sorted(names))))

    for outer in mine:
        for inner in mine:
            if outer is inner:
                continue
            op, ip = _norm(outer["path"]), _norm(inner["path"])
            if ip.startswith(op + os.sep.lower()):
                problems.append(
                    "'%s' sits inside '%s'.\n"
                    "   That folder is reachable two ways - directly as '%s', and by opening\n"
                    "   '%s' and tapping through. That is why the same folders show up twice."
                    % (inner["name"], outer["name"], inner["name"], outer["name"]))

    no_abe = [s["name"] for s in mine if not s["abe"]]
    if no_abe:
        problems.append("Access-based enumeration is off for %s.\n"
                        "   Users will see folders they cannot open, instead of them being hidden."
                        % ", ".join("'%s'" % n for n in sorted(no_abe)))

    lines.append("-" * 70)
    if problems:
        lines.append("PROBLEMS FOUND (%d):" % len(problems))
        lines.append("")
        for i, p in enumerate(problems, 1):
            lines.append("%d. %s" % (i, p))
            lines.append("")
        lines.append("The 'Update The Folder List' button fixes all of the above.")
    else:
        lines.append("No layout problems found. Tapping the server goes straight to your")
        lines.append("top-level folders, each one published exactly once.")
    return "\n".join(lines)


def share_exists(name):
    ok, _out = run_powershell("if (Get-SmbShare -Name %s -ErrorAction SilentlyContinue) "
                              "{ exit 0 } else { exit 1 }" % _ps_quote(name))
    return ok


# =========================================================================
# TAILSCALE SERVE
#
# The dashboard already binds 0.0.0.0, so it is reachable at
# http://<tailscale-ip>:50505 without any of this. What `tailscale serve`
# adds is a real HTTPS certificate and a proper hostname, so the dashboard
# password stops travelling as plain base64 over the wire.
#
# `serve` is tailnet-only. `funnel` is the one that publishes to the whole
# internet, and this app deliberately never calls it.
# =========================================================================
DASHBOARD_PORT = 50505
TAILSCALE_PATHS = [
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
]

# Tailscale changed this command's shape over the years; try newest first.
SERVE_VARIANTS = [
    ["serve", "--bg", "{port}"],
    ["serve", "--bg", "http://localhost:{port}"],
    ["serve", "https:443", "/", "http://localhost:{port}"],
    ["serve", "https", "/", "proxy", "{port}"],
]
SERVE_OFF_VARIANTS = [
    ["serve", "--bg", "off"],
    ["serve", "reset"],
]


def tailscale_exe():
    """Locate tailscale.exe, or return '' if it is not installed."""
    for p in TAILSCALE_PATHS:
        if os.path.exists(p):
            return p
    ok, out = run_console(["where", "tailscale"], shell=False)
    if ok and out.strip():
        first = out.strip().splitlines()[0].strip()
        if os.path.exists(first):
            return first
    return ""


def tailscale_self(exe):
    """This machine's tailnet identity. -> (dns_name, online, error)"""
    ok, out = run_console([exe, "status", "--json"], shell=False, timeout=30)
    if not ok:
        return "", False, out
    try:
        data = json.loads(out)
    except Exception as e:
        return "", False, "Could not read Tailscale's status reply (%s):\n%s" % (e, out[:400])
    me = data.get("Self") or {}
    return (me.get("DNSName") or "").rstrip("."), bool(me.get("Online")), ""


def tailscale_serving_port(exe, port=DASHBOARD_PORT):
    """Is this port actually being served right now? -> (serving, raw_status)"""
    ok, out = run_console([exe, "serve", "status"], shell=False, timeout=30)
    if not ok:
        return False, out
    serving = ("localhost:%d" % port) in out or ("127.0.0.1:%d" % port) in out
    return serving, out


# =========================================================================
# HEADLESS WEB DASHBOARD SERVER (RUNS WHEN --headless IS PASSED)
# =========================================================================
class WebDashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Mutes access logs to prevent background server crash
        pass

    def check_auth(self):
        config = load_config()
        expected_pass = config.get("web_pass", "")
        if not expected_pass:
            return True  
            
        auth_header = self.headers.get("Authorization")
        if auth_header and auth_header.startswith("Basic "):
            try:
                encoded = auth_header.split(" ")[1]
                decoded = base64.b64decode(encoded).decode("utf-8")
                username, password = decoded.split(":", 1)
                if username == "admin" and password == expected_pass:
                    return True
            except:
                pass
        return False

    def require_auth(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="EasyNAS Secure Dashboard"')
        self.end_headers()
        self.wfile.write(b"Unauthorized. Please provide the admin password.")

    def get_users(self):
        users, _hidden, _err = list_local_users(load_config().get("hidden_users"))
        return users

    def get_folders(self):
        root_dir = load_config().get("root_dir", "")
        if not root_dir or not os.path.exists(root_dir):
            return []
        return list_managed_folders(root_dir)

    def do_GET(self):
        if not self.check_auth():
            self.require_auth()
            return
            
        parsed = urllib.parse.urlparse(self.path)
        
        # LIVE PERMISSION CHECKER API - one user + folder
        if parsed.path == "/api/check_perm":
            qs = urllib.parse.parse_qs(parsed.query)
            user = qs.get("user", [""])[0]
            folder = qs.get("folder", [""])[0]
            info = get_user_permission(folder, user) if (user and folder) else {}
            payload = {
                "level": info.get("level", "REMOVE"),
                "label": info.get("label", PERM_LABELS["REMOVE"]),
                "describe": describe_permission(info) if info else PERM_LABELS["REMOVE"],
                "inherited": bool(info.get("inherited")),
                "deny": bool(info.get("deny")),
                "raw": info.get("raw", ""),
                "error": info.get("error", ""),
            }
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        # WHAT THE NETWORK SEES - the share layout, explained
        if parsed.path == "/api/network_view":
            root_dir = load_config().get("root_dir", "")
            shares, err = list_smb_shares()
            payload = {
                "error": err,
                "shares": [sh for sh in shares if not sh["special"]],
                "report": diagnose_network_view(root_dir, shares) if not err else err,
                "root": root_dir,
            }
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        # LIVE PERMISSION MATRIX API - every user against every folder
        if parsed.path == "/api/matrix":
            m_users = self.get_users()
            m_folders = self.get_folders()
            grid = {}
            for f in m_folders:
                aces, err = read_folder_acl(f)
                row = {}
                for u in m_users:
                    if err:
                        row[u] = {"level": "ERROR", "text": "unreadable", "deny": False, "inherited": False}
                        continue
                    best_rank, level, inherited, deny = -1, "REMOVE", False, False
                    for ace in aces:
                        if not _principal_matches(ace["principal"], u):
                            continue
                        tokens = _mask_tokens(ace["mask"])
                        upper = {t.upper() for t in tokens}
                        if "DENY" in upper:
                            deny = True
                            continue
                        lvl = classify_mask(tokens)
                        rank = PERM_RANK.get(lvl, 1)
                        if rank > best_rank:
                            best_rank, level, inherited = rank, lvl, "I" in upper
                    row[u] = {
                        "level": level,
                        "text": describe_permission({"level": level, "label": PERM_LABELS.get(level, level),
                                                     "inherited": inherited, "deny": deny}),
                        "deny": deny,
                        "inherited": inherited,
                    }
                grid[f] = row
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"users": m_users, "folders": m_folders, "grid": grid}).encode("utf-8"))
            return

        users = self.get_users()
        folders = self.get_folders()
        config = load_config()
        root_dir = config.get("root_dir", "Not Configured")
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <title>EasyNAS Dashboard</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          :root {{
            --bg-dark: #161719;
            --glass-card: rgba(33, 35, 39, 0.7);
            --glass-border: rgba(59, 62, 69, 0.5);
            --accent: #4d79ff;
            --accent-hover: #3b5bdb;
            --success: #34d399;
            --danger: #f87171;
            --text-main: #e5e7eb;
            --text-muted: #9ca3af;
            --well-bg: #101113;
          }}
          body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg-dark); color: var(--text-main); margin: 0; padding: 20px; }}
          h2, h3 {{ color: #ffffff; margin-top: 0; }}
          
          .header {{ text-align: center; margin-bottom: 30px; }}
          .header h2 {{ font-size: 24px; margin-bottom: 5px; }}
          .header p {{ color: var(--text-muted); font-size: 14px; margin: 0; }}
          
          .tabs {{ display: flex; justify-content: center; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }}
          .tab-btn {{ background: var(--glass-card); border: 1px solid var(--glass-border); color: var(--text-muted); padding: 12px 24px; border-radius: 8px; cursor: pointer; font-weight: bold; transition: all 0.2s; backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }}
          .tab-btn:hover {{ background: rgba(50, 53, 59, 0.8); color: #fff; }}
          .tab-btn.active {{ background: var(--well-bg); color: #fff; border-bottom: 2px solid var(--accent); }}
          
          .tab-content {{ display: none; max-width: 800px; margin: 0 auto; }}
          .tab-content.active {{ display: block; }}
          
          .card {{ background: var(--glass-card); border: 1px solid var(--glass-border); padding: 25px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 8px 32px rgba(0,0,0,0.3); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); }}
          
          .form-group {{ margin-bottom: 15px; }}
          label {{ display: block; margin-bottom: 8px; font-size: 13px; font-weight: bold; color: var(--text-muted); }}
          input, select {{ width: 100%; padding: 12px; background: var(--well-bg); color: white; border: 1px solid var(--glass-border); border-radius: 6px; font-size: 14px; box-sizing: border-box; }}
          input:focus, select:focus {{ outline: none; border-color: var(--accent); }}
          
          button.action-btn {{ background: var(--accent); color: white; border: none; padding: 12px 20px; border-radius: 8px; cursor: pointer; font-weight: bold; width: 100%; font-size: 14px; transition: background 0.2s; }}
          button.action-btn:hover {{ background: var(--accent-hover); }}
          button.action-btn:disabled {{ background: var(--glass-border); color: var(--text-muted); cursor: not-allowed; }}
          button.success-btn {{ background: var(--success); color: #061a15; }}
          button.success-btn:hover {{ background: #10b981; }}
          
          .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
          @media (max-width: 600px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
          .muted {{ color: var(--text-muted); }}
          .mono {{ font-family: Consolas, ui-monospace, monospace; font-size: 11px; white-space: pre-wrap; }}
          .link-btn {{ background: none; border: none; color: var(--accent); cursor: pointer; font-size: 13px; padding: 0 4px; }}
          .link-btn:hover {{ text-decoration: underline; }}

          .matrix-wrap {{ overflow-x: auto; }}
          table.matrix {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
          table.matrix th, table.matrix td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--glass-border); white-space: nowrap; }}
          table.matrix thead th {{ color: var(--text-muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }}
          table.matrix tbody th {{ font-weight: 600; color: var(--text-main); }}

          .pill {{ display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; border: 1px solid transparent; }}
          .pill i {{ font-weight: 400; opacity: .75; }}
          .lvl-F {{ background: rgba(52,211,153,.16); color: #6ee7b7; border-color: rgba(52,211,153,.35); }}
          .lvl-M {{ background: rgba(96,165,250,.16); color: #93c5fd; border-color: rgba(96,165,250,.35); }}
          .lvl-C {{ background: rgba(167,139,250,.16); color: #c4b5fd; border-color: rgba(167,139,250,.35); }}
          .lvl-RX {{ background: rgba(148,163,184,.16); color: #cbd5e1; border-color: rgba(148,163,184,.35); }}
          .lvl-REMOVE {{ background: rgba(75,85,99,.18); color: #9ca3af; border-color: rgba(75,85,99,.4); }}
          .lvl-OTHER, .lvl-ERROR {{ background: rgba(251,191,36,.16); color: #fcd34d; border-color: rgba(251,191,36,.35); }}
          .lvl-DENY {{ background: rgba(248,113,113,.18); color: #fca5a5; border-color: rgba(248,113,113,.4); }}
          .legend {{ margin: 14px 0 0; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12px; }}

          .current-perm {{ background: var(--well-bg); border: 1px solid var(--glass-border); border-left: 3px solid var(--accent); border-radius: 6px; padding: 10px 12px; margin-bottom: 15px; font-size: 13px; }}
          .current-perm.bad {{ border-left-color: var(--danger); }}
          pre.report {{ background: var(--well-bg); border: 1px solid var(--glass-border); border-left: 3px solid var(--success); border-radius: 6px; padding: 14px; overflow-x: auto; font-family: Consolas, ui-monospace, monospace; font-size: 12px; line-height: 1.5; white-space: pre; margin: 0 0 12px; }}
          pre.report.bad {{ border-left-color: var(--danger); }}
        </style>
        <script>
          function openTab(tabName) {{
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById(tabName).classList.add('active');
            event.currentTarget.classList.add('active');
          }}
          
          const LEVEL_TEXT = {{
            "F": "Full Control", "M": "Read / Write / Delete", "C": "Read + Add Files",
            "RX": "Read Only", "REMOVE": "No Access", "OTHER": "Custom", "ERROR": "Unreadable"
          }};

          function shortName(p) {{
            const parts = p.split("\\\\").filter(Boolean);
            return parts.length ? parts[parts.length - 1] : p;
          }}

          function loadMatrix() {{
            const wrap = document.getElementById('matrix-wrap');
            if (!wrap) return;
            wrap.innerHTML = '<p class="muted">Reading permissions from the server…</p>';
            fetch('/api/matrix')
              .then(r => r.json())
              .then(d => {{
                if (!d.users.length) {{ wrap.innerHTML = '<p class="muted">No user accounts found.</p>'; return; }}
                if (!d.folders.length) {{ wrap.innerHTML = '<p class="muted">No folders found under the master share.</p>'; return; }}
                let h = '<table class="matrix"><thead><tr><th>Folder</th>';
                d.users.forEach(u => h += '<th>' + u + '</th>');
                h += '</tr></thead><tbody>';
                d.folders.forEach(f => {{
                  h += '<tr><th title="' + f + '">' + shortName(f) + '</th>';
                  d.users.forEach(u => {{
                    const c = d.grid[f][u];
                    const cls = c.deny ? 'DENY' : c.level;
                    let label = LEVEL_TEXT[c.level] || c.level;
                    if (c.deny) label = 'DENY';
                    if (c.inherited && c.level !== 'REMOVE') label += ' <i>(inh)</i>';
                    h += '<td><span class="pill lvl-' + cls + '" title="' + c.text + '">' + label + '</span></td>';
                  }});
                  h += '</tr>';
                }});
                h += '</tbody></table>';
                wrap.innerHTML = h;
              }})
              .catch(e => {{ wrap.innerHTML = '<p class="muted">Could not read permissions: ' + e + '</p>'; }});
          }}

          function syncPermissions() {{
            const user = document.querySelector('[name="user"]').value;
            const folder = document.querySelector('[name="folder"]').value;
            const levelSelect = document.querySelector('[name="level"]');
            const applyBtn = document.getElementById('apply-perm-btn');
            const cur = document.getElementById('current-perm');

            if (!user || !folder) return;

            cur.className = 'current-perm';
            cur.textContent = 'Checking current access…';
            applyBtn.textContent = "Checking Server…";
            applyBtn.disabled = true;

            fetch(`/api/check_perm?user=${{encodeURIComponent(user)}}&folder=${{encodeURIComponent(folder)}}`)
                .then(res => res.json())
                .then(data => {{
                    levelSelect.value = data.level === 'OTHER' ? 'RX' : data.level;
                    if (data.error) {{
                      cur.className = 'current-perm bad';
                      cur.textContent = 'Could not read this folder: ' + data.error;
                    }} else {{
                      cur.className = 'current-perm' + (data.deny ? ' bad' : '');
                      cur.innerHTML = 'On the server now: <b>' + data.describe + '</b>'
                        + (data.raw ? '<br><span class="muted mono">' + data.raw.replace(/</g, '&lt;') + '</span>' : '');
                    }}
                    applyBtn.textContent = "Apply Security Rule";
                    applyBtn.disabled = false;
                }})
                .catch(err => {{
                    cur.className = 'current-perm bad';
                    cur.textContent = 'Could not reach the server.';
                    applyBtn.textContent = "Apply Security Rule";
                    applyBtn.disabled = false;
                }});
          }}

          function loadNetworkView() {{
            const list = document.getElementById('share-list');
            const rep = document.getElementById('share-report');
            if (!list) return;
            list.innerHTML = '<p class="muted">Reading the share list…</p>';
            fetch('/api/network_view')
              .then(r => r.json())
              .then(d => {{
                if (d.error) {{
                  list.innerHTML = '<p class="muted">Could not read shares: ' + d.error + '</p>';
                  rep.textContent = d.error;
                  return;
                }}
                if (!d.shares.length) {{
                  list.innerHTML = '<p class="muted">Nothing is shared yet.</p>';
                }} else {{
                  let h = '<table class="matrix"><thead><tr><th>Tap this</th><th>Opens this folder</th>'
                        + '<th>Hides what you cannot open</th></tr></thead><tbody>';
                  d.shares.forEach(sh => {{
                    h += '<tr><th>\\\\server\\' + sh.name + '</th><td>' + sh.path + '</td><td>'
                       + (sh.abe ? '<span class="pill lvl-F">yes</span>'
                                 : '<span class="pill lvl-DENY">no</span>') + '</td></tr>';
                  }});
                  list.innerHTML = h + '</tbody></table>';
                }}
                rep.textContent = d.report;
                rep.className = d.report.indexOf('PROBLEMS FOUND') >= 0 ? 'report bad' : 'report';
              }})
              .catch(e => {{ list.innerHTML = '<p class="muted">Could not reach the server.</p>'; }});
          }}

          document.addEventListener("DOMContentLoaded", () => {{
            document.querySelector('[name="user"]').addEventListener('change', syncPermissions);
            document.querySelector('[name="folder"]').addEventListener('change', syncPermissions);
            syncPermissions();
            loadMatrix();
            loadNetworkView();
          }});
        </script>
        </head>
        <body>
        
        <div class="header">
            <h2>⛁ EasySMB Remote Dashboard</h2>
            <p>Server folder: {root_dir}</p>
            <p style="font-size:11px;opacity:.6;margin-top:4px;">Easy SMB Core v{APP_VERSION}</p>
        </div>
        
        <div class="tabs">
           <button class="tab-btn active" onclick="openTab('setup')">✦ Users & Folders</button>
           <button class="tab-btn" onclick="openTab('perms')">🔒 Security Rules</button>
           <button class="tab-btn" onclick="openTab('shares')">📡 Network Shares</button>
           <button class="tab-btn" onclick="openTab('snapraid')">⛁ SnapRAID</button>
        </div>
        
        <!-- TAB 1: USERS & FOLDERS -->
        <div id="setup" class="tab-content active">
            <div class="card">
                <h3>Create New User Account</h3>
                <p style="font-size: 13px; color: var(--text-muted); margin-top: -10px; margin-bottom: 15px;">Adds a local Windows account to the NAS server for network access.</p>
                <form method="POST" action="/create_user">
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Username</label>
                            <input type="text" name="username" required>
                        </div>
                        <div class="form-group">
                            <label>Password</label>
                            <input type="password" name="password" required>
                        </div>
                    </div>
                    <button type="submit" class="action-btn">Add User</button>
                </form>
            </div>
            
            <div class="card">
                <h3>Create New Folder</h3>
                <p style="font-size: 13px; color: var(--text-muted); margin-top: -10px; margin-bottom: 15px;">Creates a new directory inside the Master Share ({root_dir}).</p>
                <form method="POST" action="/create_folder">
                    <div class="form-group">
                        <label>New Folder Name</label>
                        <input type="text" name="folder_name" placeholder="e.g., Photos, Taxes, Documents" required>
                    </div>
                    <button type="submit" class="action-btn">Create Folder</button>
                </form>
            </div>
        </div>
        
        <!-- TAB 2: PERMISSIONS -->
        <div id="perms" class="tab-content">
            <div class="card">
                <h3>Who Can See What — Live</h3>
                <p style="font-size: 13px; color: var(--text-muted); margin-top: -10px; margin-bottom: 15px;">
                  Read straight off the server's folders. This is what Windows reports right now, not a saved copy.
                  <button type="button" class="link-btn" onclick="loadMatrix()">↺ Refresh</button>
                </p>
                <div id="matrix-wrap" class="matrix-wrap"><p class="muted">Loading current permissions…</p></div>
                <p class="legend">
                  <span class="pill lvl-F">Full Control</span>
                  <span class="pill lvl-M">Read / Write / Delete</span>
                  <span class="pill lvl-C">Read + Add</span>
                  <span class="pill lvl-RX">Read Only</span>
                  <span class="pill lvl-REMOVE">No Access</span>
                  <span class="pill lvl-DENY">DENY rule</span>
                  <span class="muted">· <i>(inh)</i> = inherited from the parent folder</span>
                </p>
            </div>

            <div class="card">
                <h3>Change a Rule</h3>
                <p style="font-size: 13px; color: var(--text-muted); margin-top: -10px; margin-bottom: 15px;">Pick a user and folder. The current setting loads automatically, and the change is verified on the server before it reports success.</p>
                <form method="POST" action="/perms">
                    <div class="form-group">
                        <label>Select User:</label>
                        <select name="user">{"".join(f'<option value="{u}">{u}</option>' for u in users)}</select>
                    </div>
                    <div class="form-group">
                        <label>Select Folder:</label>
                        <select name="folder">{"".join(f'<option value="{f}">{os.path.basename(f) or f}</option>' for f in folders)}</select>
                    </div>
                    <div id="current-perm" class="current-perm">Checking current access…</div>
                    <div class="form-group">
                        <label>Access Level:</label>
                        <select name="level" id="level-select">
                            <option value="RX">Read Only (View Files)</option>
                            <option value="C">Read + Add Files (no deleting)</option>
                            <option value="M">Modify (Read, Write, Delete)</option>
                            <option value="F">Full Control</option>
                            <option value="REMOVE">Revoke Access (Hide Folder via ABE)</option>
                        </select>
                    </div>
                    <button type="submit" id="apply-perm-btn" class="action-btn" style="background:#6366f1;">Apply Security Rule</button>
                </form>
            </div>
        </div>
        
        <!-- TAB 3: NETWORK SHARES -->
        <div id="shares" class="tab-content">
            <div class="card">
                <h3>What People See When They Tap The Server</h3>
                <p style="font-size: 13px; color: var(--text-muted); margin-top: -10px; margin-bottom: 15px;">
                  Phones and PCs show the SHARE list, not your folder tree. If the same folder is
                  published twice, or the parent folder is shared as well as its children, everything
                  appears more than once.
                  <button type="button" class="link-btn" onclick="loadNetworkView()">↺ Refresh</button>
                </p>
                <div id="share-list"><p class="muted">Loading…</p></div>
            </div>
            <div class="card">
                <h3>Diagnosis</h3>
                <pre id="share-report" class="report">Loading…</pre>
                <p style="font-size: 12px; color: var(--text-muted);">
                  Changing the share layout needs Administrator rights, so it is done from the
                  desktop app: <b>Step 5 → Update The Folder List</b>.
                </p>
            </div>
        </div>

        <!-- TAB 3: SNAPRAID -->
        <div id="snapraid" class="tab-content">
            <div class="card">
                <h3>SnapRAID Operations</h3>
                <p style="font-size: 13px; color: var(--text-muted); margin-top: -10px; margin-bottom: 15px;">Execute parity backup checks and maintenance on the drive pool.</p>
                <div class="grid-2">
                    <form method="POST" action="/snapraid?cmd=sync">
                        <button type="submit" class="action-btn success-btn">Run Parity Sync (Backup)</button>
                    </form>
                    <form method="POST" action="/snapraid?cmd=status">
                        <button type="submit" class="action-btn" style="background:var(--well-bg); border: 1px solid var(--glass-border);">Check Array Status</button>
                    </form>
                    <form method="POST" action="/snapraid?cmd=smart">
                        <button type="submit" class="action-btn" style="background:var(--well-bg); border: 1px solid var(--glass-border);">Check SMART Health</button>
                    </form>
                    <form method="POST" action="/snapraid?cmd=scrub">
                        <button type="submit" class="action-btn" style="background:var(--well-bg); border: 1px solid var(--glass-border);">Run Scrub (Check Bit-Rot)</button>
                    </form>
                </div>
            </div>
        </div>
        
        </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_POST(self):
        if not self.check_auth():
            self.require_auth()
            return
            
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8')
        post_qs = urllib.parse.parse_qs(post_data)
        
        output = ""
        title = "Command Result"
        
        if parsed.path == "/snapraid":
            title = "SnapRAID Log"
            cmd = qs.get("cmd", [""])[0]
            if cmd in ["status", "sync", "smart", "scrub"]:
                exe = r"C:\\SnapRAID\\snapraid.exe" if os.path.exists(r"C:\\SnapRAID\\snapraid.exe") else "snapraid"
                try:
                    res = subprocess.run([exe, cmd], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    output = res.stdout + res.stderr
                except Exception as e:
                    output = str(e)
                    
        elif parsed.path == "/perms":
            title = "Permissions Log"
            user = post_qs.get("user", [""])[0]
            folder = post_qs.get("folder", [""])[0]
            level = post_qs.get("level", [""])[0]

            if user and folder and level:
                try:
                    before = get_user_permission(folder, user)
                    lines = ["Folder:  %s" % folder,
                             "User:    %s" % user,
                             "",
                             "Before:  %s" % describe_permission(before)]
                    if before.get("error"):
                        lines.append("")
                        lines.append("FAILED - this folder could not be read:")
                        lines.append("  %s" % before["error"])
                    elif before.get("level") == level and not before.get("deny"):
                        lines.append("After:   %s (unchanged)" % describe_permission(before))
                        lines.append("")
                        lines.append("No action taken - '%s' already has this access to %s."
                                     % (user, os.path.basename(folder) or folder))
                    else:
                        ok, after, log = apply_user_permission(folder, user, level)
                        lines.append("After:   %s" % describe_permission(after))
                        lines.append("")
                        if ok:
                            lines.append("SUCCESS - the rule was written and then read back from the")
                            lines.append("folder to confirm Windows actually reports it.")
                        else:
                            lines.append("FAILED - the rule was sent but the folder does not report")
                            lines.append("'%s' afterwards. Nothing was silently assumed."
                                         % PERM_LABELS.get(level, level))
                        lines.append("")
                        lines.append("Command and output:")
                        for l in log.splitlines():
                            if l.strip():
                                lines.append("  %s" % l.strip())
                    output = "\n".join(lines)
                except Exception as e:
                    output = "FAILED - %s" % e

        elif parsed.path == "/create_user":
            title = "User Creation Log"
            username = post_qs.get("username", [""])[0].strip()
            password = post_qs.get("password", [""])[0].strip()
            if username and password:
                try:
                    cmd = ["net", "user", username, password, "/add", "/expires:never"]
                    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    if res.returncode == 0:
                        output = f"Success: User '{username}' was created on the server."
                    else:
                        output = f"Error creating user:\n{res.stderr}\n{res.stdout}"
                except Exception as e:
                    output = str(e)
            else:
                output = "Username or password cannot be empty."

        elif parsed.path == "/create_folder":
            title = "Folder Creation Log"
            folder_name = post_qs.get("folder_name", [""])[0].strip()
            config = load_config()
            root_dir = config.get("root_dir", "")
            if folder_name and root_dir and os.path.exists(root_dir):
                target_path = os.path.join(root_dir, folder_name)
                try:
                    os.makedirs(target_path, exist_ok=True)
                    output = f"Success: Created new directory at {target_path}"
                except Exception as e:
                    output = f"Failed to create folder: {str(e)}"
            else:
                output = "Invalid folder name or server root directory is not configured."
                    
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <title>{title}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #161719; color: #e5e7eb; padding: 30px 20px; text-align: center; }}
          .log-container {{ max-width: 800px; margin: 0 auto; text-align: left; background: rgba(33, 35, 39, 0.7); border: 1px solid rgba(59, 62, 69, 0.5); border-radius: 12px; padding: 25px; backdrop-filter: blur(12px); }}
          pre {{ background: #101113; padding: 15px; border-radius: 8px; color: #34d399; overflow-x: auto; white-space: pre-wrap; font-family: 'Consolas', monospace; font-size: 13px; line-height: 1.5; border: 1px solid rgba(59, 62, 69, 0.5); }}
          a.back-btn {{ background: #4d79ff; color: white; text-decoration: none; font-weight: bold; font-size: 14px; padding: 12px 24px; border-radius: 8px; display: inline-block; margin-bottom: 25px; transition: background 0.2s; }}
          a.back-btn:hover {{ background: #3b5bdb; }}
          h2 {{ margin-top: 0; color: #fff; }}
        </style>
        </head>
        <body>
        <a href="/" class="back-btn">← Back to Dashboard</a>
        <div class="log-container">
            <h2>{title}</h2>
            <pre>{output}</pre>
        </div>
        </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def headless_kill_command():
    """PowerShell that stops only OUR headless copies.

    Matching on '--headless' alone also matches unrelated software (VS Code
    helpers, for one), so the filter has to name this program as well.
    """
    exe = os.path.basename(os.path.abspath(sys.argv[0])).replace("'", "''")
    return ("Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%--headless%'\" | "
            "Where-Object { $_.CommandLine -like '*%s*' } | "
            "Invoke-CimMethod -MethodName Terminate" % exe)


def run_headless_server():
    """Initializes the background web server and proxies it through Tailscale."""
    # One instance only. ctypes.windll shares Win32's error slot across every
    # call, so GetLastError() read that way is unreliable and used to make the
    # dashboard exit silently at random. use_last_error captures it properly.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "EasyNAS_Headless_Mutex")
    if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
        sys.stderr.write("EasyNAS dashboard is already running; this copy will exit.\n")
        sys.exit(0)

    PORT = DASHBOARD_PORT
    # Binding to 0.0.0.0 opens it to Tailscale AND your local home network
    try:
        httpd = http.server.HTTPServer(("0.0.0.0", PORT), WebDashboardHandler)
    except OSError as e:
        sys.stderr.write("EasyNAS dashboard could not listen on port %d: %s\n" % (PORT, e))
        sys.exit(1)
    with httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


# =========================================================================
# DESKTOP GUI CLASSES
# =========================================================================
class FrostedGlassButton(tk.Canvas):
    def __init__(
        self, parent, text, command, width=180, height=38, radius=18, color_scheme="neutral", **kwargs
    ):
        super().__init__(
            parent, width=width, height=height, highlightthickness=0, bd=0, bg=parent["bg"], **kwargs
        )
        self.command = command
        self.text = text
        self.w = width
        self.h = height
        self.r = radius
        self.color_scheme = color_scheme
        self.is_hovered = False
        self.is_pressed = False

        if self.color_scheme == "accent":
            self.colors = {"base": "#2b384e", "hover": "#364763", "press": "#202b3d", "rim_light": "#6a88b5", "rim_dark": "#161e2b", "text": "#ffffff", "glow": "#4d79ff"}
        elif self.color_scheme == "danger":
            self.colors = {"base": "#4a2424", "hover": "#5e2e2e", "press": "#331919", "rim_light": "#944d4d", "rim_dark": "#1f0f0f", "text": "#ffcccc", "glow": "#ff6666"}
        elif self.color_scheme == "nav":
            self.colors = {"base": "#1a1b1e", "hover": "#25272c", "press": "#141517", "rim_light": "#36383f", "rim_dark": "#0e0f11", "text": "#9ca3af", "glow": "#4a4d55"}
        elif self.color_scheme == "nav_active":
            self.colors = {"base": "#282a2f", "hover": "#32353b", "press": "#1e2023", "rim_light": "#5c6370", "rim_dark": "#141517", "text": "#ffffff", "glow": "#828997"}
        elif self.color_scheme == "success":
            self.colors = {"base": "#1b4d3e", "hover": "#246652", "press": "#123329", "rim_light": "#34d399", "rim_dark": "#061a15", "text": "#ffffff", "glow": "#6ee7b7"}
        else:
            self.colors = {"base": "#282a2e", "hover": "#33363b", "press": "#1e2023", "rim_light": "#4a4d53", "rim_dark": "#141517", "text": "#e5e7eb", "glow": "#9ca3af"}

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.redraw()

    def _draw_rounded_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def redraw(self):
        self.delete("all")
        w, h, r = self.w, self.h, self.r
        self._draw_rounded_rect(1, 2, w - 1, h, r, fill=self.colors["rim_dark"])
        top_rim_color = self.colors["glow"] if self.is_hovered else self.colors["rim_light"]
        self._draw_rounded_rect(1, 1, w - 1, h - 1, r, fill=top_rim_color)
        fill_col = self.colors["press"] if self.is_pressed else self.colors["hover"] if self.is_hovered else self.colors["base"]
        offset = 2 if self.is_pressed else 1
        self._draw_rounded_rect(2, 1 + offset, w - 2, h - 2 + offset, r - 1, fill=fill_col)

        if not self.is_pressed:
            sheen = "#592b2b" if self.color_scheme == "danger" else "#2c2e33" if "nav" in self.color_scheme else ("#3d4147" if "neutral" in self.color_scheme else "#405370")
            self._draw_rounded_rect(4, 3, w - 4, int(h * 0.45), r - 2, fill=sheen)

        self.create_text(w // 2, (h // 2) + (1 if self.is_pressed else 0), text=self.text, fill=self.colors["text"], font=("Segoe UI", 9, "bold"))

    def _on_enter(self, e):
        self.is_hovered = True
        self.redraw()

    def _on_leave(self, e):
        self.is_hovered, self.is_pressed = False, False
        self.redraw()

    def _on_press(self, e):
        self.is_pressed = True
        self.redraw()

    def _on_release(self, e):
        if self.is_pressed and self.is_hovered:
            self.is_pressed = False
            self.redraw()
            if self.command:
                self.command()


class GlassSMBManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Easy SMB  -  Home Server Manager  v%s" % APP_VERSION)
        self.root.geometry("1140x880")
        self.root.minsize(1060, 800)

        icon_path = "app_icon.ico"
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except:
                pass

        self.current_process = None
        self.is_processing = False
        self.takeown_completed = False
        self.current_output_raw = ""
        self.current_output_title = ""

        self.palette = {
            "bg_dark": "#161719", "bg_tint": "#1a1b1e", "glass_card": "#212327", "glass_rim_light": "#3b3e45",
            "glass_rim_shadow": "#0d0e10", "well_bg": "#141517", "well_border": "#2c2f35", "well_inner_glow": "#1f2126",
            "text_bright": "#ffffff", "text_frost": "#e5e7eb", "text_muted": "#9ca3af", "text_glow": "#d1d5db",
            "terminal_bg": "#101113", "success": "#34d399", "error": "#f87171", "info_click": "#60a5fa"
        }

        self.discovered_users, self.active_subfolders, self.user_folder_permissions = [], [], {}
        # live_perms[user][folder] = what the server actually reports right now.
        # It is deliberately separate from user_folder_permissions (unsaved edits).
        self.live_perms = {}
        self._perm_loading = False
        self.all_local_users = []
        self._hidden_vars = {}
        self._action_seq = 0

        self.setup_window_backdrop()
        self.setup_ttk_styles()

        self.main_container = tk.Frame(self.root, bg=self.palette["bg_tint"])
        self.main_container.place(relx=0.5, rely=0.5, relwidth=0.96, relheight=0.96, anchor="center")

        self.header_frame = tk.Frame(self.main_container, bg=self.palette["bg_tint"])
        self.header_frame.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(self.header_frame, text="⛁ Easy SMB Core", bg=self.palette["bg_tint"], fg=self.palette["text_bright"], font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(self.header_frame, text="v%s" % APP_VERSION, bg=self.palette["bg_tint"],
                 fg=self.palette["text_muted"], font=("Segoe UI", 9)).pack(side="left", padx=(8, 0), pady=(10, 0))

        self.is_elevated = bool(is_admin())
        tk.Label(self.header_frame,
                 text=("✓ Running as Administrator" if self.is_elevated
                       else "⚠ VIEW ONLY — not running as Administrator"),
                 bg=self.palette["bg_tint"],
                 fg=self.palette["success"] if self.is_elevated else self.palette["error"],
                 font=("Segoe UI", 9, "bold")).pack(side="right", pady=(6, 0))

        self.notebook = ttk.Notebook(self.main_container)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(10, 4))

        self.tab_smb = tk.Frame(self.notebook, bg=self.palette["bg_tint"])
        self.tab_tailscale = tk.Frame(self.notebook, bg=self.palette["bg_tint"])
        self.tab_snapraid = tk.Frame(self.notebook, bg=self.palette["bg_tint"])
        self.tab_web = tk.Frame(self.notebook, bg=self.palette["bg_tint"])

        self.notebook.add(self.tab_smb, text="  ✦ People & Folders  ")
        self.notebook.add(self.tab_tailscale, text="  ☁ Use It Away From Home  ")
        self.notebook.add(self.tab_snapraid, text="  ⛁ Protect Against Drive Failure  ")
        self.notebook.add(self.tab_web, text="  🌐 Manage From Your Phone  ")

        self.build_smb_vertical_workflow()
        self.build_tailscale_tab()
        self.build_snapraid_tab()
        self.build_web_tab()
        self.build_activity_log()
        self.build_status_bar()

        self.refresh_system_users()

    def setup_window_backdrop(self):
        self.canvas = tk.Canvas(self.root, bg=self.palette["bg_dark"], highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)

        def on_resize(event):
            if event.widget == self.canvas:
                w, h = event.width, event.height
                self.canvas.delete("backdrop")
                self.canvas.create_oval(-80, -80, 420, 420, fill="#1f2124", outline="", tags="backdrop")
                self.canvas.create_oval(w - 380, h - 380, w + 120, h + 120, fill="#24262b", outline="", tags="backdrop")

        self.canvas.bind("<Configure>", on_resize)

    def setup_ttk_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=self.palette["bg_tint"], borderwidth=0, tabmargins=[0, 0, 0, 8])
        style.configure("TNotebook.Tab", background="#1e2024", foreground=self.palette["text_muted"], padding=[20, 8], font=("Segoe UI", 10, "bold"), borderwidth=1, relief="flat")
        style.map("TNotebook.Tab", background=[("selected", "#32353b"), ("active", "#282a2f")], foreground=[("selected", self.palette["text_bright"]), ("active", self.palette["text_frost"])])
        
        style.configure("Glass.TCheckbutton", background=self.palette["glass_rim_shadow"], foreground=self.palette["text_bright"], font=("Segoe UI", 9, "bold"))
        style.map("Glass.TCheckbutton", background=[("active", self.palette["glass_rim_shadow"])], foreground=[("active", self.palette["text_bright"])])
        
        style.configure("Dark.TCheckbutton", background=self.palette["glass_rim_shadow"], foreground=self.palette["text_frost"], font=("Segoe UI", 9))
        style.map("Dark.TCheckbutton", background=[("active", self.palette["glass_rim_shadow"])], foreground=[("active", "#ffffff")])
        
        style.configure("Glass.TRadiobutton", background=self.palette["glass_card"], foreground=self.palette["text_frost"], font=("Segoe UI", 9, "bold"))
        style.map("Glass.TRadiobutton", background=[("active", self.palette["glass_card"])], foreground=[("active", "#ffffff")])
        style.configure("Vertical.TScrollbar", background=self.palette["glass_card"], troughcolor=self.palette["well_bg"])

    def create_glass_card(self, parent, title=""):
        outer_rim = tk.Frame(parent, bg=self.palette["glass_rim_light"], padx=1, pady=1)
        shadow_rim = tk.Frame(outer_rim, bg=self.palette["glass_rim_shadow"], padx=1, pady=1)
        shadow_rim.pack(fill="both", expand=True)
        card = tk.Frame(shadow_rim, bg=self.palette["glass_card"], padx=14, pady=10)
        card.pack(fill="both", expand=True)
        if title:
            header_box = tk.Frame(card, bg=self.palette["glass_card"])
            header_box.pack(fill="x", pady=(0, 6))
            bead = tk.Canvas(header_box, width=8, height=8, bg=self.palette["glass_card"], highlightthickness=0)
            bead.pack(side="left", padx=(0, 8))
            bead.create_oval(0, 0, 7, 7, fill="#6b7280", outline="")
            tk.Label(header_box, text=title.upper(), bg=self.palette["glass_card"], fg=self.palette["text_glow"], font=("Segoe UI", 8, "bold")).pack(side="left")
        return outer_rim, card

    def create_glass_entry(self, parent, width=25, show=None):
        well_rim = tk.Frame(parent, bg=self.palette["well_border"], padx=1, pady=1)
        entry = tk.Entry(well_rim, width=width, show=show, bg=self.palette["well_bg"], fg=self.palette["text_bright"], insertbackground="#9ca3af", relief="flat", font=("Segoe UI", 10), highlightthickness=1, highlightbackground=self.palette["well_inner_glow"], highlightcolor="#6b7280")
        entry.pack(fill="both", expand=True)
        return well_rim, entry

    def build_smb_vertical_workflow(self):
        workflow_container = tk.Frame(self.tab_smb, bg=self.palette["bg_tint"])
        workflow_container.pack(fill="both", expand=True, pady=4)
        self.vert_nav_frame = tk.Frame(workflow_container, bg=self.palette["bg_tint"], width=190)
        self.vert_nav_frame.pack(side="left", fill="y", padx=(0, 8))
        self.vert_view_pane = tk.Frame(workflow_container, bg=self.palette["bg_tint"])
        self.vert_view_pane.pack(side="right", fill="both", expand=True)

        self.nav_btn_step1 = FrostedGlassButton(self.vert_nav_frame, text="People   (Step 1)", command=lambda: self.switch_vertical_tab(1), width=180, height=42, radius=16, color_scheme="nav_active")
        self.nav_btn_step1.pack(pady=4)
        self.nav_btn_step2 = FrostedGlassButton(self.vert_nav_frame, text="Folders & Access   (Steps 2-5)", command=lambda: self.switch_vertical_tab(2), width=180, height=42, radius=16, color_scheme="nav")
        self.nav_btn_step2.pack(pady=4)
        self.nav_btn_step3 = FrostedGlassButton(self.vert_nav_frame, text="Server Name   (Step 6)", command=lambda: self.switch_vertical_tab(3), width=180, height=42, radius=16, color_scheme="nav")
        self.nav_btn_step3.pack(pady=4)

        self.view_step1, self.view_step2, self.view_step3 = tk.Frame(self.vert_view_pane, bg=self.palette["bg_tint"]), tk.Frame(self.vert_view_pane, bg=self.palette["bg_tint"]), tk.Frame(self.vert_view_pane, bg=self.palette["bg_tint"])
        self.build_vview_step1()
        self.build_vview_step2()
        self.build_vview_step3()
        self.active_vtab = 1
        self.view_step1.pack(fill="both", expand=True)

    def switch_vertical_tab(self, tab_num):
        self.active_vtab = tab_num
        for view in [self.view_step1, self.view_step2, self.view_step3]: view.pack_forget()
        self.nav_btn_step1.color_scheme = "nav_active" if tab_num == 1 else "nav"
        self.nav_btn_step2.color_scheme = "nav_active" if tab_num == 2 else "nav"
        self.nav_btn_step3.color_scheme = "nav_active" if tab_num == 3 else "nav"
        for btn in [self.nav_btn_step1, self.nav_btn_step2, self.nav_btn_step3]: btn.redraw()

        if tab_num == 1: self.view_step1.pack(fill="both", expand=True)
        elif tab_num == 2:
            self.refresh_system_users()
            self.view_step2.pack(fill="both", expand=True)
        elif tab_num == 3: self.view_step3.pack(fill="both", expand=True)

    def build_vview_step1(self):
        user_rim, user_card = self.create_glass_card(self.view_step1, title="Step 1: Make An Account For Each Person")
        user_rim.pack(fill="x", pady=4)
        tk.Label(user_card, text="Create a login name and password for each person who will access the server.\nIf you already made accounts, you can skip to Step 2.", bg=self.palette["glass_card"], fg=self.palette["text_muted"], font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(0, 10))

        ugrid = tk.Frame(user_card, bg=self.palette["glass_card"])
        ugrid.pack(fill="x", pady=4)
        tk.Label(ugrid, text="New Username", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        _, self.ent_v_user = self.create_glass_entry(ugrid, width=22)
        self.ent_v_user.master.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)

        tk.Label(ugrid, text="New Password", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w")
        _, self.ent_v_pass = self.create_glass_entry(ugrid, width=22, show="*")
        self.ent_v_pass.master.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=4)

        btn_row = tk.Frame(ugrid, bg=self.palette["glass_card"])
        btn_row.grid(row=1, column=2, sticky="e", pady=4)
        FrostedGlassButton(btn_row, text="+ Add Person", command=self.create_user_only, width=180, height=34, radius=16, color_scheme="neutral").pack(side="left", padx=(0, 8))
        FrostedGlassButton(btn_row, text="+ Add Person & Continue", command=self.create_user_and_advance, width=220, height=34, radius=16, color_scheme="accent").pack(side="left")

        man_rim, man_card = self.create_glass_card(self.view_step1, title="Who Are The People Using This Server?")
        man_rim.pack(fill="x", pady=4)
        tk.Label(man_card,
                 text=("Untick any account that is not a NAS user - the PC's own sign-in account, a service\n"
                       "account, anything you do not want appearing in the permissions list. Windows built-in\n"
                       "accounts (Administrator, Guest, DefaultAccount) are always hidden automatically."),
                 bg=self.palette["glass_card"], fg=self.palette["text_muted"], font=("Segoe UI", 9),
                 justify="left").pack(anchor="w", pady=(0, 8))
        self.hidden_users_frame = tk.Frame(man_card, bg=self.palette["glass_card"])
        self.hidden_users_frame.pack(fill="x")

        det_rim, det_card = self.create_glass_card(self.view_step1, title="Every Windows Account On This PC")
        det_rim.pack(fill="both", expand=True, pady=6)
        self.lbl_user_summary = tk.Label(det_card, text="Scanning for accounts...", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Consolas", 9), justify="left")
        self.lbl_user_summary.pack(anchor="w", pady=4)
        FrostedGlassButton(det_card, text="Skip ahead →", command=lambda: self.switch_vertical_tab(2), width=180, height=32, radius=16, color_scheme="neutral").pack(anchor="w", pady=6)

    def build_vview_step2(self):
        root_rim, root_card = self.create_glass_card(self.view_step2, title="Step 2: Pick The Main Folder")
        root_rim.pack(fill="x", pady=4)
        rgrid = tk.Frame(root_card, bg=self.palette["glass_card"])
        rgrid.pack(fill="x")
        tk.Label(rgrid, text="Select the main hard drive folder where all files will live (e.g., D:\\EasyNAS)", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        path_box = tk.Frame(rgrid, bg=self.palette["glass_card"])
        path_box.grid(row=1, column=0, sticky="ew", pady=4)
        _, self.ent_nas_root = self.create_glass_entry(path_box, width=54)
        self.ent_nas_root.master.pack(side="left", fill="x", expand=True, padx=(0, 8))
        FrostedGlassButton(path_box, text="Browse...", command=self.browse_nas_root, width=100, height=32, radius=14, color_scheme="neutral").pack(side="right")

        tmpl_rim, tmpl_card = self.create_glass_card(self.view_step2, title="Step 3: Create The Folders")
        tmpl_rim.pack(fill="x", pady=4)
        self.var_struct_mode = tk.StringVar(value="scan")
        tbox = tk.Frame(tmpl_card, bg=self.palette["glass_card"])
        tbox.pack(fill="x", pady=2)
        ttk.Radiobutton(tbox, text="I already have folders (Scan existing)", variable=self.var_struct_mode, value="scan", style="Glass.TRadiobutton").pack(anchor="w", pady=2)
        ttk.Radiobutton(tbox, text="Create private folders for each user", variable=self.var_struct_mode, value="multi_private", style="Glass.TRadiobutton").pack(anchor="w", pady=2)
        ttk.Radiobutton(tbox, text="Create private folders AND one public 'Shared' folder", variable=self.var_struct_mode, value="multi_shared", style="Glass.TRadiobutton").pack(anchor="w", pady=2)
        
        action_row = tk.Frame(tmpl_card, bg=self.palette["glass_card"])
        action_row.pack(fill="x", pady=(6, 2))
        FrostedGlassButton(action_row, text="Create / Find Folders", command=self.apply_structure_template, width=200, height=34, radius=16, color_scheme="accent").pack(side="left", padx=(0, 10))

        matrix_rim, matrix_card = self.create_glass_card(self.view_step2, title="Step 4: Choose Who Can Open What")
        matrix_rim.pack(fill="both", expand=True, pady=4)
        matrix_split = tk.Frame(matrix_card, bg=self.palette["glass_card"])
        matrix_split.pack(fill="both", expand=True, pady=2)
        left_user_box = tk.Frame(matrix_split, bg=self.palette["glass_card"], width=200)
        left_user_box.pack(side="left", fill="y", padx=(0, 10))
        tk.Label(left_user_box, text="1. Pick a person:", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.user_listbox = tk.Listbox(left_user_box, bg=self.palette["well_bg"], fg=self.palette["text_bright"], selectbackground="#32353b", selectforeground="#ffffff", highlightthickness=1, highlightbackground=self.palette["well_border"], relief="flat", font=("Segoe UI", 10), height=7, exportselection=False)
        self.user_listbox.pack(fill="both", expand=True, pady=4)
        self.user_listbox.bind("<<ListboxSelect>>", self.on_user_selection_changed)

        right_perm_box = tk.Frame(matrix_split, bg=self.palette["glass_card"])
        right_perm_box.pack(side="right", fill="both", expand=True)

        head_row = tk.Frame(right_perm_box, bg=self.palette["glass_card"])
        head_row.pack(fill="x")
        self.lbl_perm_header = tk.Label(head_row, text="2. Choose what they can do:", bg=self.palette["glass_card"], fg=self.palette["text_glow"], font=("Segoe UI", 9, "bold"))
        self.lbl_perm_header.pack(side="left", anchor="w")
        FrostedGlassButton(head_row, text="↺ Refresh", command=self.refresh_live_perms_clicked, width=150, height=28, radius=13, color_scheme="neutral").pack(side="right")

        scroll_container = tk.Frame(right_perm_box, bg=self.palette["glass_card"])
        scroll_container.pack(fill="both", expand=True, pady=4)
        self.folder_scroll_canvas = tk.Canvas(scroll_container, bg=self.palette["well_bg"], highlightthickness=1, highlightbackground=self.palette["well_border"], height=130)
        scrollbar = ttk.Scrollbar(scroll_container, orient="vertical", command=self.folder_scroll_canvas.yview, style="Vertical.TScrollbar")
        self.folder_scroll_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.folder_scroll_canvas.pack(side="left", fill="both", expand=True)
        self.folder_inner_frame = tk.Frame(self.folder_scroll_canvas, bg=self.palette["well_bg"])
        self.canvas_frame = self.folder_scroll_canvas.create_window((0, 0), window=self.folder_inner_frame, anchor="nw")

        def on_inner_configure(e): self.folder_scroll_canvas.configure(scrollregion=self.folder_scroll_canvas.bbox("all"))
        def on_canvas_configure(e): self.folder_scroll_canvas.itemconfig(self.canvas_frame, width=e.width)
        self.folder_inner_frame.bind("<Configure>", on_inner_configure)
        self.folder_scroll_canvas.bind("<Configure>", on_canvas_configure)
        def _on_mousewheel(event): self.folder_scroll_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        scroll_container.bind("<Enter>", lambda e: self.folder_scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        scroll_container.bind("<Leave>", lambda e: self.folder_scroll_canvas.unbind_all("<MouseWheel>"))

        self.lbl_perm_summary = tk.Label(matrix_card, text="No unsaved changes — the panel matches the server.", bg=self.palette["glass_card"], fg=self.palette["success"], font=("Segoe UI", 9, "bold"), anchor="w", justify="left")
        self.lbl_perm_summary.pack(fill="x", pady=(8, 0))

        tk.Label(matrix_card,
                 text=("Each folder shows ON SERVER: what Windows reports right now. Change as many folders and users\n"
                       "as you like — nothing is written until you press a Save button. Rows you changed show ● UNSAVED.\n"
                       "After saving, every rule is read back from the folder and a pass / fail report opens automatically."),
                 bg=self.palette["glass_card"], fg=self.palette["text_muted"], font=("Segoe UI", 8),
                 anchor="w", justify="left").pack(fill="x", pady=(2, 0))

        apply_bar = tk.Frame(matrix_card, bg=self.palette["glass_card"])
        apply_bar.pack(fill="x", pady=(6, 2))
        FrostedGlassButton(apply_bar, text="⚡ Save All Changes", command=self.apply_all_configured_permissions, width=320, height=38, radius=18, color_scheme="accent").pack(side="right", padx=(0, 10))

        share_rim, share_card = self.create_glass_card(self.view_step2, title="Step 5: What People See When They Tap Your Server")
        share_rim.pack(fill="x", pady=4)
        self.var_share_mode = tk.StringVar(value=load_config().get("share_mode", SHARE_MODE_FOLDERS))
        sbox = tk.Frame(share_card, bg=self.palette["glass_card"])
        sbox.pack(fill="x", pady=2)
        ttk.Radiobutton(sbox, text="Show my top folders directly  (\\\\server \u2192 Users, Family_Shared, Resources)",
                        variable=self.var_share_mode, value=SHARE_MODE_FOLDERS,
                        command=self.save_share_mode, style="Glass.TRadiobutton").pack(anchor="w", pady=2)
        ttk.Radiobutton(sbox, text="Show one folder to open first  (\\\\server \u2192 FamilyNAS \u2192 Users, ...)",
                        variable=self.var_share_mode, value=SHARE_MODE_MASTER,
                        command=self.save_share_mode, style="Glass.TRadiobutton").pack(anchor="w", pady=2)
        tk.Label(share_card,
                 text=("Phones show the SHARE list, not the folder tree. Sharing the parent folder adds an extra\n"
                       "tap for everyone, and an old share left behind makes the same folder appear twice."),
                 bg=self.palette["glass_card"], fg=self.palette["text_muted"], font=("Segoe UI", 8),
                 justify="left").pack(anchor="w", pady=(4, 6))
        srow = tk.Frame(share_card, bg=self.palette["glass_card"])
        srow.pack(fill="x")
        FrostedGlassButton(srow, text="\U0001f50d See What People See", command=self.show_network_view,
                           width=250, height=34, radius=16, color_scheme="neutral").pack(side="left", padx=(0, 10))
        FrostedGlassButton(srow, text="\U0001f4e1 Update The Folder List", command=self.publish_network_shares,
                           width=220, height=34, radius=16, color_scheme="accent").pack(side="left")

    def build_vview_step3(self):
        dom_rim, dom_card = self.create_glass_card(self.view_step3, title="Step 6: Give Your Server An Easy Name")
        dom_rim.pack(fill="both", expand=True, pady=4)
        tk.Label(dom_card, text="Instead of forcing users to type in a random IP address like '100.x.x.x',\nyou can create a friendly name that all computers will understand.", bg=self.palette["glass_card"], fg=self.palette["text_muted"], font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(0, 12))

        info_box = tk.Frame(dom_card, bg=self.palette["well_bg"], padx=12, pady=10)
        info_box.pack(fill="x", pady=4)
        curr_hostname, local_ip = socket.gethostname(), "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception: pass

        tk.Label(info_box, text=f"Windows PC Name: {curr_hostname}\nLocal Router IP: {local_ip}\nCurrent Folder Address: \\\\{curr_hostname}\\<ShareName>", bg=self.palette["well_bg"], fg=self.palette["text_frost"], font=("Consolas", 9), justify="left").pack(anchor="w")

        alias_rim = tk.Frame(dom_card, bg=self.palette["glass_card"])
        alias_rim.pack(fill="x", pady=10)
        tk.Label(alias_rim, text="Type a new friendly name for this PC (e.g., EasyNAS):", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=2)
        abox = tk.Frame(alias_rim, bg=self.palette["glass_card"])
        abox.pack(fill="x", pady=4)
        _, self.ent_domain_alias = self.create_glass_entry(abox, width=32)
        self.ent_domain_alias.insert(0, "EasyNAS")
        self.ent_domain_alias.master.pack(side="left", padx=(0, 10))
        FrostedGlassButton(abox, text="Save This Name", command=self.apply_hosts_alias, width=180, height=34, radius=16, color_scheme="accent").pack(side="left")

        magic_box = tk.Frame(dom_card, bg=self.palette["glass_card"])
        magic_box.pack(fill="x", pady=(10, 0))
        magic_note = ("✦ REMOTE ACCESS NOTE:\n"
                      "If you are using Tailscale for remote phone access, clients can type the computer name\n"
                      "directly into their device thanks to Tailscale MagicDNS:\n\n"
                      f"   • iPhone/iPad (Files App):     smb://{curr_hostname.lower()}\n"
                      f"   • Android (Cx File Explorer):  Host: {curr_hostname.lower()}")
        tk.Label(magic_box, text=magic_note, bg=self.palette["glass_card"], fg=self.palette["text_muted"], font=("Consolas", 9), justify="left").pack(anchor="w")

    def build_tailscale_tab(self):
        panel = tk.Frame(self.tab_tailscale, bg=self.palette["bg_tint"])
        panel.pack(fill="both", expand=True, pady=6)

        card_rim, card = self.create_glass_card(panel, title="Reach Your Server From Anywhere (No Router Setup)")
        card_rim.pack(fill="x", pady=(0, 6))
        tk.Label(card, text="Tailscale safely connects devices to this server from anywhere in the world using an encrypted tunnel.\nIt bypasses your router settings automatically so you don't have to deal with port-forwarding.", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(0, 10))

        btn_row = tk.Frame(card, bg=self.palette["glass_card"])
        btn_row.pack(fill="x", pady=4)
        FrostedGlassButton(btn_row, text="1. Download & Install Tailscale", command=self.install_tailscale, width=220, height=36, radius=18, color_scheme="neutral").pack(side="left", padx=(0, 12))

        def open_tailscale_console():
            ts_path = r"C:\Program Files\Tailscale\tailscale.exe"

            def worker(log):
                step = log.begin("Find tailscale.exe")
                if not os.path.exists(ts_path):
                    log.fail(step, "Tailscale is not installed at %s. Use button 1 first." % ts_path)
                    log.skip_rest("The Tailscale sign-in page was not opened.")
                    return
                log.ok(step, ts_path)

                if not log.run("Bring the Tailscale connection up", [ts_path, "up"], shell=False,
                               detail="If this is the first time, finish sign-in in the browser."):
                    return

                step = log.begin("Open the Tailscale admin page")
                webbrowser.open("https://login.tailscale.com/admin/machines")
                log.ok(step, "Opened login.tailscale.com in your browser.")

                step = log.begin("Read this machine's Tailscale address")
                ok, out = run_console([ts_path, "ip", "-4"], shell=False)
                if ok and out.strip():
                    log.ok(step, "Reachable at %s from any of your Tailscale devices." % out.strip().splitlines()[0])
                else:
                    log.skip(step, "No Tailscale address yet - finish signing in, then press this again.", out)

            self.run_action("Connect this machine to Tailscale", worker)

        FrostedGlassButton(btn_row, text="2. Sign In To Tailscale", command=open_tailscale_console, width=260, height=36, radius=18, color_scheme="accent").pack(side="left")

        guide_rim, guide_frame = self.create_glass_card(panel, title="Step-by-Step Connection Guide")
        guide_rim.pack(fill="both", expand=True, pady=(6, 0))
        txt = tk.Text(guide_frame, bg=self.palette["well_bg"], fg=self.palette["text_frost"], font=("Consolas", 9), wrap="word", relief="flat", padx=12, pady=12)
        scroll = ttk.Scrollbar(guide_frame, orient="vertical", command=txt.yview, style="Vertical.TScrollbar")
        txt.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        guide_content = """TAILSCALE COMPREHENSIVE SETUP GUIDE

PHASE 1: INSTALLATION
1. Click '1. Download & Install Tailscale' above. Wait for the 'Success' notification.
2. Go to tailscale.com in your web browser and create a free account.
3. Come back here and click '2. Open Tailscale Login/Dashboard' to link this PC.

PHASE 2: SERVER SETUP (Run Unattended & Tags)
Normally, Tailscale turns off when you log out of Windows. For a server, you want it to run constantly in the background.
1. Look at your Windows System Tray (bottom right corner, near the clock). 
2. Right-click the Tailscale icon -> Preferences -> check "Run Unattended".
3. Tailscale will warn you about "Tags". 
   • Open the Tailscale Admin Console (login.tailscale.com) -> 'Access Controls' tab.
   • Click 'Definitions' -> 'Tags' tab -> '+ Create Tag'.
   • Tag Name: 'server'. Tag Owner: your Tailscale email. Save.
   • Go to the 'Machines' tab. Click (...) next to this NAS -> Edit ACL tags -> check 'tag:server'.

PHASE 3: FIXING DISCONNECTS (MagicDNS & Expiry)
1. Machines tab: Click (...) next to NAS -> "Disable Key Expiry". 
2. DNS tab: Toggle MagicDNS ON.

PHASE 4: CONNECTING CLIENT DEVICES
You must install Tailscale on any device connecting to this server. Devices must either be logged into the same admin account, or you must invite their accounts to your Tailscale network via the dashboard.

▶ Windows PCs & Laptops (Read carefully: Windows hides this feature!)
   1. Install Tailscale and log in. 
   2. Press the Windows Key + R on your keyboard to open the 'Run' window. (You do NOT need to dig into Windows Network settings or toggle any VPN switches).
   3. Type: \\\\EasyNAS (Or whatever you named the server. Do NOT use "smb://" like on phones. Use the two backslashes).
   4. Press Enter. Enter the local Windows Username and Password created in Step 1.
   5. Open the main folder. Security will magically hide folders they shouldn't see!

▶ Apple iPhone & iPad
   1. Install Tailscale from the App Store, log in, and ensure VPN is Active.
   2. Open 'Files' app -> 'Browse' -> (...) menu -> 'Connect to Server'.
   3. Enter: smb://EasyNAS (If it says Socket Not Connected over cellular, use the long FQDN from the dashboard: e.g., smb://easynas.yak-bebop.ts.net)
   4. Select 'Registered User' and enter their Windows Username/Password.

▶ Android Phones & Tablets
   1. Install Tailscale from Google Play, log in, and connect.
   2. Install 'Cx File Explorer' from Google Play.
   3. Open Cx File Explorer -> Network tab -> [+] New Location -> Remote -> SMB.
   4. Host: EasyNAS (Leave port blank). Enter credentials. Tap OK."""
        txt.insert("1.0", guide_content)
        txt.config(state="disabled")

    def build_snapraid_tab(self):
        panel = tk.Frame(self.tab_snapraid, bg=self.palette["bg_tint"])
        panel.pack(fill="both", expand=True, pady=6)

        card_rim, card = self.create_glass_card(panel, title="Protect Against A Dead Hard Drive (SnapRAID)")
        card_rim.pack(fill="x", pady=(0, 6))

        tk.Label(card, text="SnapRAID calculates backup math across independent hard drives to protect against disk failure.\nUse this dashboard to run manual health checks or schedule automatic background protection.", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(0, 8))

        btn_grid = tk.Frame(card, bg=self.palette["glass_card"])
        btn_grid.pack(fill="x", pady=4)

        FrostedGlassButton(btn_grid, text="1. Install SnapRAID", command=self.install_snapraid, width=180, height=36, radius=16, color_scheme="neutral").grid(row=0, column=0, padx=(0, 8), pady=4)
        FrostedGlassButton(btn_grid, text="2. Update The Backup Copy", command=self.snapraid_sync, width=220, height=36, radius=16, color_scheme="accent").grid(row=0, column=1, padx=8, pady=4)
        FrostedGlassButton(btn_grid, text="3. Run It Automatically Each Night", command=self.schedule_snapraid_tasks, width=220, height=36, radius=16, color_scheme="nav_active").grid(row=0, column=2, padx=8, pady=4)
        
        FrostedGlassButton(btn_grid, text="Check My Files Are OK", command=self.snapraid_status, width=180, height=36, radius=16, color_scheme="nav").grid(row=1, column=0, padx=(0, 8), pady=4)
        FrostedGlassButton(btn_grid, text="Check My Drives Are OK", command=self.snapraid_smart, width=220, height=36, radius=16, color_scheme="nav").grid(row=1, column=1, padx=8, pady=4)
        FrostedGlassButton(btn_grid, text="Recover Lost Files", command=self.snapraid_fix, width=220, height=36, radius=16, color_scheme="danger").grid(row=1, column=2, padx=8, pady=4)

        guide_rim, guide_frame = self.create_glass_card(panel, title="How This Protection Works")
        guide_rim.pack(fill="both", expand=True, pady=(6, 0))
        txt = tk.Text(guide_frame, bg=self.palette["well_bg"], fg=self.palette["text_frost"], font=("Consolas", 9), wrap="word", relief="flat", padx=12, pady=12)
        scroll = ttk.Scrollbar(guide_frame, orient="vertical", command=txt.yview, style="Vertical.TScrollbar")
        txt.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        guide_content = """SNAPRAID COMPREHENSIVE SETUP GUIDE

PHASE 1: THE GOLDEN RULE OF PARITY
1. You must dedicate at least one hard drive strictly for Backup Parity. 
   CRITICAL: Your parity drive MUST be equal to or larger than your largest single data drive.
   (e.g., If you have a 4TB drive and an 8TB drive holding data, your Parity drive MUST be at least 8TB).

PHASE 2: DRIVE PREPARATION & CONFIGURATION
1. Install drives, open Windows "Disk Management", format as NTFS, and assign clear letters (e.g., P: for Parity, D1: for Data).
2. Click "1. Install SnapRAID" above.
3. Open C:\\SnapRAID\\. Create a text file named: snapraid.conf
4. Define your disks exactly like this example:
   
   parity P:\\snapraid.parity
   content P:\\snapraid.content
   content C:\\SnapRAID\\snapraid.content
   content D1:\\snapraid.content
   data d1 D1:\\EasyNAS
   data d2 D2:\\EasyNAS

PHASE 3: INITIALIZATION & AUTOMATION
1. Click "2. Update Parity Backup" in the dashboard above. This calculates initial parity. Let it run overnight.
2. Click "3. Automate Nightly Backups". This tells Windows to automatically run a Sync daily at 2:00 AM and a Scrub (fixing bit-rot) every Sunday at 4:00 AM.

PHASE 4: DISASTER RECOVERY
If a drive dies or you accidentally delete a file:
• Click "Check File Health" above to see what files SnapRAID noticed are missing.
• To recover everything missing: Click "Recover Lost Data (Fix)" in the dashboard above."""
        txt.insert("1.0", guide_content)
        txt.config(state="disabled")

    def build_web_tab(self):
        panel = tk.Frame(self.tab_web, bg=self.palette["bg_tint"])
        panel.pack(fill="both", expand=True, pady=6)

        card_rim, card = self.create_glass_card(panel, title="Manage This Server From Your Phone")
        card_rim.pack(fill="x", pady=(0, 6))

        desc = (
            "Turns this program into a background service with a web dashboard. Changes you make there are applied\n"
            "to the server immediately - the same commands the desktop app runs - so you can set permissions and\n"
            "check SnapRAID from a phone without remoting into this machine."
        )
        tk.Label(card, text=desc, bg=self.palette["glass_card"], fg=self.palette["text_frost"],
                 font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(0, 12))

        tk.Label(card, text="Set Dashboard Password (to keep non-admins out):", bg=self.palette["glass_card"],
                 fg=self.palette["text_muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
        _, self.ent_web_pass = self.create_glass_entry(card, width=40, show="*")
        self.ent_web_pass.master.pack(anchor="w", pady=(2, 10))

        self.var_ts_serve = tk.BooleanVar(value=True)
        ttk.Checkbutton(card,
                        text="Also share it over Tailscale with HTTPS (recommended)",
                        variable=self.var_ts_serve, style="Glass.TCheckbutton").pack(anchor="w")
        tk.Label(card,
                 text=("Without this the dashboard still works, but only over plain http://, which means the\n"
                       "password is sent in a form anyone on your home network could read. Tailscale gives it a\n"
                       "real certificate and a proper name. It stays private to your tailnet - never the internet."),
                 bg=self.palette["glass_card"], fg=self.palette["text_muted"], font=("Segoe UI", 8),
                 justify="left").pack(anchor="w", pady=(2, 10))

        btn_row = tk.Frame(card, bg=self.palette["glass_card"])
        btn_row.pack(fill="x", pady=4)
        FrostedGlassButton(btn_row, text="Turn On Phone Access", command=self.deploy_headless_service,
                           width=280, height=38, radius=18, color_scheme="accent").pack(side="left", padx=(0, 12))
        FrostedGlassButton(btn_row, text="Turn Off Phone Access", command=self.remove_headless_service,
                           width=220, height=38, radius=18, color_scheme="danger").pack(side="left")

        ts_row = tk.Frame(card, bg=self.palette["glass_card"])
        ts_row.pack(fill="x", pady=(10, 2))
        FrostedGlassButton(ts_row, text="🔍 Check Remote Access", command=self.show_tailscale_serve_status,
                           width=210, height=34, radius=16, color_scheme="neutral").pack(side="left", padx=(0, 10))
        FrostedGlassButton(ts_row, text="🔒 Turn On Encrypted Access", command=self.enable_tailscale_serve,
                           width=250, height=34, radius=16, color_scheme="neutral").pack(side="left", padx=(0, 10))
        FrostedGlassButton(ts_row, text="Turn Off Encrypted Access", command=self.disable_tailscale_serve,
                           width=190, height=34, radius=16, color_scheme="neutral").pack(side="left")

        guide_rim, guide_frame = self.create_glass_card(panel, title="How To Open It On Your Phone")
        guide_rim.pack(fill="both", expand=True, pady=(6, 0))
        txt = tk.Text(guide_frame, bg=self.palette["well_bg"], fg=self.palette["text_frost"],
                      font=("Consolas", 9), wrap="word", relief="flat", padx=12, pady=12)
        txt.insert("1.0",
                   "HOW TO USE THE WEB PORTAL\n\n"
                   "1. Set a password and click 'Deploy Background Web Service'.\n"
                   "   The steps appear in the Activity box at the bottom, and the last one tells\n"
                   "   you the exact address to open.\n\n"
                   "2. There are two addresses, and they do the same thing:\n\n"
                   "   On your home network      http://<this-pc-ip>:50505\n"
                   "   From anywhere on Tailscale  https://<this-pc>.<your-tailnet>.ts.net\n\n"
                   "   Press 'Check Tailscale Sharing' to see your real addresses filled in.\n\n"
                   "3. The browser asks for a login. The username is 'admin' and the password is\n"
                   "   the one you set above.\n\n"
                   "WHAT THE TAILSCALE OPTION ACTUALLY CHANGES\n\n"
                   "Not much about access - the dashboard is already reachable at your Tailscale IP\n"
                   "on port 50505 as soon as it is deployed, because it listens on every network.\n"
                   "What it changes is encryption. Over plain http:// your dashboard password is\n"
                   "sent as base64, which is trivially readable by anyone sharing your home network\n"
                   "or wifi. Tailscale serve puts a real HTTPS certificate in front of it.\n\n"
                   "This uses 'tailscale serve', which is private to your own devices.\n"
                   "It never uses 'tailscale funnel', which would expose it to the public internet.\n\n"
                   "ANYTHING YOU CHANGE IN THE DASHBOARD IS LIVE\n\n"
                   "Permission changes made from a phone run the same icacls commands as the desktop\n"
                   "app, and are read back from the folder afterwards to confirm they took effect.\n"
                   "There is no separate save step and no syncing.")
        txt.config(state="disabled")

        scroll = ttk.Scrollbar(guide_frame, orient="vertical", command=txt.yview, style="Vertical.TScrollbar")
        txt.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

    def deploy_headless_service(self):
        web_pass = self.ent_web_pass.get().strip()
        root_dir = self.ent_nas_root.get().strip()
        if not root_dir:
            messagebox.showerror("Error", "Please select the Main Server Folder in the SMB tab first "
                                          "so the web server knows where your files live.")
            return
        if not web_pass:
            messagebox.showerror("Error", "Please enter a password to secure the dashboard.")
            return

        task_name = "EasyNAS_WebDashboard"

        def worker(log):
            step = log.begin("Stop any dashboard that is already running")
            run_console(["schtasks", "/end", "/tn", task_name], shell=False)
            run_powershell(headless_kill_command())
            log.ok(step, "Old copies stopped so they cannot hold the port.")

            step = log.begin("Save the dashboard settings")
            ok, err = save_config({"web_pass": web_pass, "root_dir": root_dir})
            if not ok:
                log.fail(step, "Could not write easynas_config.json next to the app.", err)
                log.skip_rest("The dashboard was not deployed.")
                return
            log.ok(step, "Saved to %s" % config_path())

            exe_path = os.path.abspath(sys.argv[0])
            if exe_path.endswith(".py") or exe_path.endswith(".pyw"):
                cmd = '"%s" "%s" --headless' % (sys.executable, exe_path)
            else:
                cmd = '"%s" --headless' % exe_path

            if not log.run("Register the background task",
                           ["schtasks", "/create", "/tn", task_name, "/tr", cmd,
                            "/sc", "onlogon", "/rl", "highest", "/f"], shell=False,
                           verify=lambda: (run_console(["schtasks", "/query", "/tn", task_name], shell=False)[0],
                                           "Windows Task Scheduler confirms the job exists.")):
                log.skip_rest("The dashboard is not running.")
                return

            if not log.run("Start the dashboard", ["schtasks", "/run", "/tn", task_name], shell=False):
                log.skip_rest("The task exists but did not start. Sign out and back in, or press Deploy again.")
                return

            step = log.begin("Confirm the dashboard is answering on port 50505")
            answered = False
            for _ in range(10):
                time.sleep(1)
                try:
                    sock = socket.create_connection(("127.0.0.1", 50505), timeout=2)
                    sock.close()
                    answered = True
                    break
                except Exception:
                    continue
            if answered:
                log.ok(step, "The dashboard is live at http://%s:%d"
                       % (self._best_local_ip(), DASHBOARD_PORT))
            else:
                log.fail(step, "The task started but nothing is listening on port %d after 10 seconds.\n"
                               "Check Task Scheduler for 'EasyNAS_WebDashboard', and make sure Windows "
                               "Firewall is not blocking the port." % DASHBOARD_PORT)
                log.skip_rest("Tailscale sharing was not attempted because the dashboard is not up.")
                return

            if self.var_ts_serve.get():
                self._tailscale_serve_steps(log)
            else:
                log.note("Share the dashboard over Tailscale",
                         "Turned off by the tick-box on this tab. The dashboard is still reachable "
                         "on your home network.")

        def done(log):
            if not log.failed:
                self.ent_web_pass.delete(0, tk.END)

        self.run_action("Turn on phone access", worker, on_done=done)

    # =========================================================================
    # TAILSCALE SERVE  (HTTPS for the remote dashboard)
    # =========================================================================
    def _tailscale_serve_steps(self, log, port=DASHBOARD_PORT):
        """Publish the dashboard over the tailnet with HTTPS. -> True if serving."""
        step = log.begin("Find tailscale.exe")
        exe = tailscale_exe()
        if not exe:
            log.skip(step, "Tailscale is not installed on this PC, so the dashboard will not be "
                           "shared over HTTPS. It is still reachable on your home network at "
                           "http://%s:%d" % (self._best_local_ip(), port))
            return False
        log.ok(step, exe)

        step = log.begin("Check this PC is signed in to Tailscale")
        dns_name, online, err = tailscale_self(exe)
        if err or not dns_name:
            log.fail(step, "Tailscale is installed but is not signed in yet. Open the "
                           "Tailscale app and sign in, then run this again.", err)
            return False
        log.ok(step, "%s (%s)" % (dns_name, "online" if online else "offline right now"))

        step = log.begin("Check the dashboard is answering on port %d" % port)
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.close()
            log.ok(step, "Something is listening locally, good.")
        except Exception as e:
            log.fail(step, "Nothing is listening on port %d yet, so Tailscale would be pointed at "
                           "a dead port. Deploy the dashboard first." % port, str(e))
            return False

        step = log.begin("Switch on the encrypted (https) address")
        attempts = []
        used = None
        for variant in SERVE_VARIANTS:
            args = [a.format(port=port) for a in variant]
            ok, out = run_console([exe] + args, shell=False, timeout=90)
            attempts.append("$ tailscale %s\n%s" % (" ".join(args), out))
            serving, _raw = tailscale_serving_port(exe, port)
            if serving:
                used = args
                break
        if used is None:
            log.fail(step, "None of the known 'tailscale serve' command shapes worked. Your "
                           "Tailscale version may expect something different - the attempts and "
                           "their replies are below.", "\n\n".join(attempts))
            return False
        log.ok(step, "Published with: tailscale %s" % " ".join(used), "\n\n".join(attempts))

        step = log.begin("Confirm the HTTPS address")
        serving, raw = tailscale_serving_port(exe, port)
        if not serving:
            log.fail(step, "Tailscale accepted the command but is no longer serving the port.", raw)
            return False
        log.ok(step,
               "Open  https://%s  from any phone or laptop signed in to Tailscale.\n"
               "The dashboard password now travels over HTTPS instead of plain text.\n"
               "Only your own signed-in devices can reach it - it is NOT on the public internet." % dns_name,
               raw)
        return True

    def enable_tailscale_serve(self):
        self.run_action("Turn on encrypted remote access",
                        lambda log: self._tailscale_serve_steps(log),
                        popup_on_success=True)

    def disable_tailscale_serve(self):
        def worker(log):
            step = log.begin("Find tailscale.exe")
            exe = tailscale_exe()
            if not exe:
                log.skip(step, "Tailscale is not installed - nothing to turn off.")
                return
            log.ok(step, exe)

            step = log.begin("Check what is being served")
            serving, raw = tailscale_serving_port(exe, DASHBOARD_PORT)
            if not serving:
                log.skip(step, "Port %d is not being served, so there is nothing to stop."
                         % DASHBOARD_PORT, raw)
                return
            log.ok(step, "Port %d is currently shared." % DASHBOARD_PORT, raw)

            step = log.begin("Turn off encrypted remote access")
            attempts = []
            for variant in SERVE_OFF_VARIANTS:
                ok, out = run_console([exe] + variant, shell=False, timeout=60)
                attempts.append("$ tailscale %s\n%s" % (" ".join(variant), out))
                still, _raw = tailscale_serving_port(exe, DASHBOARD_PORT)
                if not still:
                    log.ok(step, "Stopped with: tailscale %s\nThe dashboard is still reachable on "
                                 "your home network at http://%s:%d"
                           % (" ".join(variant), self._best_local_ip(), DASHBOARD_PORT),
                           "\n\n".join(attempts))
                    return
            log.fail(step, "Tailscale is still serving the port after every attempt.",
                     "\n\n".join(attempts))

        self.run_action("Turn off encrypted remote access", worker)

    def show_tailscale_serve_status(self):
        def worker(log):
            step = log.begin("Ask Tailscale what it is sharing")
            exe = tailscale_exe()
            if not exe:
                log.fail(step, "Tailscale is not installed on this PC.")
                return
            dns_name, online, err = tailscale_self(exe)
            serving, raw = tailscale_serving_port(exe, DASHBOARD_PORT)
            lines = ["Machine : %s" % (dns_name or "(not signed in)"),
                     "Online  : %s" % ("yes" if online else "no"),
                     "",
                     "Dashboard on your home network : http://%s:%d"
                     % (self._best_local_ip(), DASHBOARD_PORT)]
            if serving and dns_name:
                lines.append("Dashboard over Tailscale       : https://%s   (HTTPS, tailnet only)" % dns_name)
            else:
                lines.append("Dashboard over Tailscale       : not shared")
                lines.append("")
                lines.append("Press 'Share Dashboard over Tailscale' to turn it on.")
            log.ok(step, "\n".join(lines), "Raw 'tailscale serve status':\n%s" % (raw or "(no output)"))

        self.run_action("Check remote access", worker, popup_on_success=True, needs_admin=False)

    def _best_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def remove_headless_service(self):
        task_name = "EasyNAS_WebDashboard"

        def worker(log):
            step = log.begin("Check whether the dashboard task exists")
            exists = run_console(["schtasks", "/query", "/tn", task_name], shell=False)[0]
            if not exists:
                log.skip(step, "There is no '%s' task - nothing to remove." % task_name)
            else:
                log.ok(step, "Found it.")
                log.run("Stop the running dashboard", ["schtasks", "/end", "/tn", task_name], shell=False)
                log.run("Delete the scheduled task",
                        ["schtasks", "/delete", "/tn", task_name, "/f"], shell=False,
                        verify=lambda: (not run_console(["schtasks", "/query", "/tn", task_name], shell=False)[0],
                                        "The task is gone from Task Scheduler."))

            step = log.begin("Stop sharing it over Tailscale")
            exe = tailscale_exe()
            if not exe:
                log.skip(step, "Tailscale is not installed - nothing to unshare.")
            else:
                serving, raw = tailscale_serving_port(exe, DASHBOARD_PORT)
                if not serving:
                    log.skip(step, "It was not being shared over Tailscale.", raw)
                else:
                    stopped = False
                    for variant in SERVE_OFF_VARIANTS:
                        run_console([exe] + variant, shell=False, timeout=60)
                        still, _r = tailscale_serving_port(exe, DASHBOARD_PORT)
                        if not still:
                            stopped = True
                            log.ok(step, "Stopped with: tailscale %s" % " ".join(variant))
                            break
                    if not stopped:
                        log.fail(step, "Tailscale is still serving port %d." % DASHBOARD_PORT)

            step = log.begin("Close any leftover dashboard processes")
            ok, out = run_powershell(headless_kill_command())
            log.ok(step, "Done - port %d is free again." % DASHBOARD_PORT, out)

        self.run_action("Turn off phone access", worker)

    # =========================================================================
    # SNAPRAID COMMAND HANDLERS
    # =========================================================================
    def run_snapraid_cmd(self, cmd_arg, title):
        exe_path = r"C:\SnapRAID\snapraid.exe"

        def worker(log):
            step = log.begin("Locate snapraid.exe")
            exe = exe_path if os.path.exists(exe_path) else "snapraid"
            if exe == "snapraid":
                ok, out = run_console(["where", "snapraid"], shell=False)
                if not ok:
                    log.fail(step, "SnapRAID is not at %s and is not on the PATH. Install it from the "
                                   "Backup & Recovery tab first." % exe_path, out)
                    log.skip_rest("'%s' was not run." % cmd_arg)
                    return
                log.ok(step, "Found on the PATH: %s" % out.strip().splitlines()[0])
            else:
                log.ok(step, exe_path)

            step = log.begin("Run: snapraid %s" % cmd_arg)
            try:
                self.current_process = subprocess.Popen(
                    [exe, cmd_arg], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                stdout, _ = self.current_process.communicate()
                rc = self.current_process.returncode
            except Exception as e:
                log.fail(step, "SnapRAID could not be started.", str(e))
                return
            finally:
                self.current_process = None

            if rc == 0:
                log.ok(step, "snapraid %s finished cleanly." % cmd_arg, stdout)
            else:
                log.fail(step, "snapraid %s exited with code %s. The full output is below." % (cmd_arg, rc), stdout)

        self.run_action("SnapRAID - %s" % title, worker, popup_on_success=True,
                        needs_admin=(cmd_arg in ("sync", "fix", "scrub")))

    # These used to bail out silently when something else was running, so the
    # button simply did nothing. run_action owns the lock and explains itself.
    def snapraid_sync(self):
        self.run_snapraid_cmd("sync", "Update the backup copy")

    def snapraid_status(self):
        self.run_snapraid_cmd("status", "File health check")

    def snapraid_smart(self):
        self.run_snapraid_cmd("smart", "Hard drive health check")

    def snapraid_fix(self):
        if messagebox.askyesno(
                "Recover lost files?",
                "This rebuilds files that are missing or damaged, using the backup copy on "
                "your parity drive.\n\n"
                "It can take a long time, and it overwrites files in place. Only do this if "
                "you have actually lost data.\n\n"
                "Start the recovery?"):
            self.run_snapraid_cmd("fix", "Recover lost files")

    def schedule_snapraid_tasks(self):
        exe_path = r"C:\SnapRAID\snapraid.exe"

        def task_exists(name):
            ok, _ = run_console(["schtasks", "/query", "/tn", name], shell=False)
            return ok

        def worker(log):
            step = log.begin("Locate snapraid.exe")
            if not os.path.exists(exe_path):
                log.fail(step, "SnapRAID is not installed at %s, so the scheduled jobs would fail "
                               "every night. Install it first." % exe_path)
                log.skip_rest("No scheduled tasks were created.")
                return
            log.ok(step, exe_path)

            for name, args, sched, detail in [
                ("SnapRAID_Daily_Sync", ["/sc", "daily", "/st", "02:00"], "sync",
                 "Runs every night at 2:00 AM."),
                ("SnapRAID_Weekly_Scrub", ["/sc", "weekly", "/d", "SUN", "/st", "04:00"], "scrub",
                 "Runs on Sundays at 4:00 AM."),
            ]:
                log.run("Schedule '%s'" % name,
                        ["schtasks", "/create", "/tn", name, "/tr", "%s %s" % (exe_path, sched)]
                        + args + ["/rl", "highest", "/f"],
                        shell=False, detail=detail,
                        verify=lambda n=name: (task_exists(n), "Windows Task Scheduler confirms the job."))

        self.run_action("Schedule automatic SnapRAID jobs", worker)

    # =========================================================================
    # ACTIVITY LOG
    # A running account of what the app is doing. Every action writes each of
    # its steps here as it happens, so nothing is ever a silent black box.
    # =========================================================================
    def build_activity_log(self):
        self.activity_open = True
        self._log_steps = set()

        wrap = tk.Frame(self.main_container, bg=self.palette["glass_rim_light"], padx=1, pady=1)
        wrap.pack(fill="x", padx=12, pady=(4, 0))
        shadow = tk.Frame(wrap, bg=self.palette["glass_rim_shadow"], padx=1, pady=1)
        shadow.pack(fill="both", expand=True)
        card = tk.Frame(shadow, bg=self.palette["glass_card"], padx=10, pady=6)
        card.pack(fill="both", expand=True)

        bar = tk.Frame(card, bg=self.palette["glass_card"])
        bar.pack(fill="x")
        self.lbl_activity_title = tk.Label(bar, text="Activity  —  idle", bg=self.palette["glass_card"],
                                           fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold"))
        self.lbl_activity_title.pack(side="left")

        self.btn_activity_toggle = tk.Button(
            bar, text="Hide ▾", bg=self.palette["glass_card"], fg=self.palette["text_muted"],
            font=("Segoe UI", 8, "bold"), relief="flat", padx=8, command=self.toggle_activity_log)
        self.btn_activity_toggle.pack(side="right")
        tk.Button(bar, text="Clear", bg=self.palette["glass_card"], fg=self.palette["text_muted"],
                  font=("Segoe UI", 8, "bold"), relief="flat", padx=8,
                  command=self.clear_activity_log).pack(side="right")
        tk.Button(bar, text="Copy", bg=self.palette["glass_card"], fg=self.palette["text_muted"],
                  font=("Segoe UI", 8, "bold"), relief="flat", padx=8,
                  command=self.copy_activity_log).pack(side="right")
        tk.Button(bar, text="Save…", bg=self.palette["glass_card"], fg=self.palette["text_muted"],
                  font=("Segoe UI", 8, "bold"), relief="flat", padx=8,
                  command=self.save_activity_log).pack(side="right")

        self.activity_body = tk.Frame(card, bg=self.palette["glass_card"])
        self.activity_body.pack(fill="both", expand=True, pady=(6, 0))

        well = tk.Frame(self.activity_body, bg=self.palette["well_border"], padx=1, pady=1)
        well.pack(fill="both", expand=True)
        self.txt_activity = tk.Text(well, bg=self.palette["terminal_bg"], fg=self.palette["text_glow"],
                                    relief="flat", font=("Consolas", 9), wrap="word", height=9,
                                    padx=8, pady=6, insertbackground=self.palette["text_bright"])
        scroll = ttk.Scrollbar(well, orient="vertical", command=self.txt_activity.yview,
                               style="Vertical.TScrollbar")
        self.txt_activity.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.txt_activity.pack(side="left", fill="both", expand=True)

        self.txt_activity.tag_configure("head", foreground=self.palette["text_bright"],
                                        font=("Consolas", 9, "bold"), spacing1=6)
        self.txt_activity.tag_configure("run", foreground=self.palette["info_click"])
        self.txt_activity.tag_configure("ok", foreground=self.palette["success"])
        self.txt_activity.tag_configure("fail", foreground=self.palette["error"])
        self.txt_activity.tag_configure("skip", foreground=self.palette["text_muted"])
        self.txt_activity.tag_configure("out", foreground=self.palette["text_muted"])
        self.txt_activity.config(state="disabled")

        self._log_raw("Ready. Anything this app does will be listed here, step by step.\n", "skip")

    def toggle_activity_log(self):
        self.activity_open = not self.activity_open
        if self.activity_open:
            self.activity_body.pack(fill="both", expand=True, pady=(6, 0))
            self.btn_activity_toggle.config(text="Hide ▾")
        else:
            self.activity_body.pack_forget()
            self.btn_activity_toggle.config(text="Show ▸")

    def clear_activity_log(self):
        self.txt_activity.config(state="normal")
        self.txt_activity.delete("1.0", "end")
        self.txt_activity.config(state="disabled")
        self._log_steps = set()

    def copy_activity_log(self):
        text = self.txt_activity.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status("Activity log copied to the clipboard.", "success")

    def save_activity_log(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
            initialfile="easysmb-activity-%s.txt" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        if not path:
            return
        try:
            with io.open(path, "w", encoding="utf-8") as f:
                f.write(self.txt_activity.get("1.0", "end-1c"))
            self.set_status("Activity log saved to %s" % path, "success")
        except Exception as e:
            self.set_status("Could not save the log. (Click for details)", "error",
                            raw_output=str(e), title="Save Failed")

    def _log_raw(self, text, tag="out"):
        self.txt_activity.config(state="normal")
        start = self.txt_activity.index("end-1c")
        self.txt_activity.insert("end", text)
        self.txt_activity.tag_add(tag, start, self.txt_activity.index("end-1c"))
        self.txt_activity.see("end")
        self.txt_activity.config(state="disabled")

    def _render_step(self, s, action_id):
        """Draw (or redraw in place) one step of the running action."""
        txt = self.txt_activity
        tag = "s_%s_%d" % (action_id, s["n"])
        status = s["status"]
        icon = {"running": "▸", "ok": "✓", "fail": "✗", "skip": "–"}.get(status, "•")
        style = {"running": "run", "ok": "ok", "fail": "fail", "skip": "skip"}.get(status, "out")

        line = "  %s  %d. %s%s\n" % (icon, s["n"], s["label"], "…" if status == "running" else "")
        if s["detail"]:
            for l in s["detail"].splitlines():
                line += "        %s\n" % l
        if status == "fail" and s["output"]:
            for l in s["output"].splitlines()[:6]:
                line += "        | %s\n" % l

        txt.config(state="normal")
        rng = txt.tag_ranges(tag)
        if rng:
            insert_at = txt.index(rng[0])
            txt.delete(rng[0], rng[1])
        else:
            # "end" sits after the final newline, but insert() puts text before
            # it. Using "end-1c" keeps the index and the insertion in step, so
            # the tag really covers the line we just drew.
            insert_at = txt.index("end-1c")
        start = txt.index(insert_at)
        txt.insert(insert_at, line)
        end = txt.index("%s + %d chars" % (start, len(line)))
        txt.tag_add(tag, start, end)
        for t in ("run", "ok", "fail", "skip", "out", "head"):
            txt.tag_remove(t, start, end)
        txt.tag_add(style, start, end)
        txt.see("end")
        txt.config(state="disabled")

    # =========================================================================
    # ACTION RUNNER
    # One place that owns the concurrency lock, the background thread, the
    # live log, the final status line and the report popup.
    # =========================================================================
    def run_action(self, title, worker, on_done=None, popup_on_success=False, needs_admin=True):
        """worker(log) does the work on a background thread, one log step at a time."""
        if getattr(self, "is_processing", False):
            self.set_status("Another task is still running. Wait for it, or press Stop.", "error",
                            raw_output="EasySMB runs one action at a time so two jobs cannot fight "
                                       "over the same folders.", title="Already Busy")
            return None

        if needs_admin and not getattr(self, "is_elevated", True):
            # Say it once, clearly, instead of letting every step fail with a
            # bare "Access is denied" from icacls or PowerShell.
            self.set_status("'%s' needs administrator rights. (Click for details)" % title, "error",
                            raw_output=(
                                "EasySMB is running in view-only mode, so nothing can be changed.\n\n"
                                "This action would have to write to Windows:\n  %s\n\n"
                                "To fix it: close EasySMB, right-click it and choose\n"
                                "'Run as administrator', then try again.\n\n"
                                "Reading is unaffected - the permissions panel and the network view "
                                "still show you the real state of the server." % title),
                            title="Administrator Rights Required")
            self.show_output_popup("error")
            return None

        self.is_processing = True
        action_id = getattr(self, "_action_seq", 0) + 1
        self._action_seq = action_id

        log = ActionLog(title, notify=lambda lg, st, aid=action_id: self.root.after(
            0, lambda s=dict(st): self._render_step(s, aid)))

        if not self.activity_open:
            self.toggle_activity_log()
        self._log_raw("\n%s\n" % title, "head")
        self.lbl_activity_title.config(text="Activity  —  %s" % title, fg=self.palette["info_click"])
        self.set_status("%s — starting…" % title, "info")

        def process():
            try:
                worker(log)
            except Exception:
                log.fail(log.begin("Unexpected error"),
                         "The action stopped early and did not finish.", traceback.format_exc())
            finally:
                log.finished = datetime.datetime.now()
                self.is_processing = False
                self.root.after(0, lambda: self._action_finished(log, on_done, popup_on_success))

        threading.Thread(target=process, daemon=True).start()
        return log

    def _action_finished(self, log, on_done=None, popup_on_success=False):
        counts = log.counts
        headline = log.headline()
        status_type = "error" if log.failed else "success"

        self._log_raw("  %s  %d ok, %d failed, %d skipped\n"
                      % ("✗" if log.failed else "✓", counts[STEP_OK], counts[STEP_FAIL], counts[STEP_SKIP]),
                      "fail" if log.failed else "ok")
        self.lbl_activity_title.config(
            text="Activity  —  %s" % ("last action FAILED" if log.failed else "last action succeeded"),
            fg=self.palette["error"] if log.failed else self.palette["success"])

        self.set_status(headline, status_type, raw_output=log.report(), title=log.title)
        if log.failed or popup_on_success:
            self.show_output_popup(status_type)
        if on_done:
            try:
                on_done(log)
            except Exception:
                pass

    # =========================================================================
    # REVISED STATUS BAR & POPUPS
    # =========================================================================
    def build_status_bar(self):
        status_rim = tk.Frame(self.main_container, bg=self.palette["glass_rim_light"], padx=1, pady=1)
        status_rim.pack(fill="x", pady=(4, 8), padx=12)
        status_shadow = tk.Frame(status_rim, bg=self.palette["glass_rim_shadow"], padx=1, pady=1)
        status_shadow.pack(fill="both", expand=True)
        status_card = tk.Frame(status_shadow, bg=self.palette["glass_card"], padx=14, pady=8)
        status_card.pack(fill="both", expand=True)

        self.lbl_status = tk.Label(status_card, text="Ready.", bg=self.palette["glass_card"], fg=self.palette["text_muted"], font=("Segoe UI", 10, "bold"))
        self.lbl_status.pack(side="left", fill="x", expand=True, anchor="w")
        FrostedGlassButton(status_card, text="🛑 Stop", command=self.stop_current_operation, width=150, height=34, radius=16, color_scheme="danger").pack(side="right")

    def set_status(self, msg, status_type="info", raw_output=None, title="Operation Details"):
        color_map = {"info": self.palette["text_muted"], "success": self.palette["success"], "error": self.palette["error"]}
        self.lbl_status.config(text=msg, fg=color_map.get(status_type, self.palette["text_muted"]))
        
        if raw_output:
            self.current_output_raw = raw_output
            self.current_output_title = title
            self.lbl_status.config(cursor="hand2")
            if status_type == "success":
                self.lbl_status.config(text=msg + " (Click to view output)", fg=self.palette["info_click"])
            self.lbl_status.bind("<Button-1>", lambda e: self.show_output_popup(status_type))
        else:
            self.current_output_raw = ""
            self.lbl_status.config(cursor="")
            self.lbl_status.unbind("<Button-1>")

    def translate_error(self, err_text):
        err_lower = err_text.lower()
        if "access is denied" in err_lower or "error 5" in err_lower: return "Windows blocked this action. Ensure you have Administrative rights and file ownership."
        if "already exists" in err_lower: return "The user account or share name you are trying to create already exists."
        if "cannot find path" in err_lower: return "The specified folder path does not exist or was moved."
        if "winget" in err_lower and "agreements" in err_lower: return "Windows Package Manager requires you to accept terms."
        return "An unexpected system execution occurred. See the technical details below."

    def show_output_popup(self, status_type):
        if not getattr(self, "current_output_raw", None): return

        popup = tk.Toplevel(self.root)
        popup.title(self.current_output_title)
        popup.geometry("700x500")
        popup.configure(bg=self.palette["bg_tint"])
        popup.transient(self.root)
        popup.grab_set()

        header_color = self.palette["error"] if status_type == "error" else self.palette["success"]
        tk.Label(popup, text=self.current_output_title, fg=header_color, bg=self.palette["bg_tint"], font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(16, 4))
        
        if status_type == "error":
            layman_err = self.translate_error(self.current_output_raw)
            tk.Label(popup, text=layman_err, fg=self.palette["text_bright"], bg=self.palette["bg_tint"], font=("Segoe UI", 10), wraplength=660, justify="left").pack(anchor="w", padx=16, pady=4)

        tk.Label(popup, text="Raw Console Output:", fg=self.palette["text_muted"], bg=self.palette["bg_tint"], font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(8, 4))

        well_f = tk.Frame(popup, bg=self.palette["well_border"], padx=1, pady=1)
        well_f.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        txt = tk.Text(well_f, bg=self.palette["terminal_bg"], fg=self.palette["text_glow"], relief="flat", font=("Consolas", 9), wrap="word", padx=8, pady=8)
        txt.insert("1.0", self.current_output_raw)
        txt.config(state="disabled")
        
        scroll = ttk.Scrollbar(well_f, orient="vertical", command=txt.yview, style="Vertical.TScrollbar")
        txt.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

    # =========================================================================
    # SILENT COMMAND EXECUTION HELPERS
    # =========================================================================
    def stop_current_operation(self):
        self.is_processing = False 
        if self.current_process and self.current_process.poll() is None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.current_process.pid)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                self.set_status("Active process killed.", "error", raw_output="The task was forcefully terminated by the user.", title="Operation Aborted")
            except Exception as e:
                self.set_status("Error aborting process", "error", raw_output=str(e), title="Termination Error")
            finally:
                self.current_process = None
        else:
            self.set_status("No active background task to cancel.", "info")

    def _background_takeown(self, root_dir):
        """Claim ownership of the folder tree once per session.

        Files copied in from another PC can end up owned by that PC's account,
        which later blocks permission changes with a bare "Access is denied".
        This used to run invisibly and report "session sweep completed", which
        told nobody anything.
        """
        def note(text, tag):
            self.root.after(0, lambda: self._log_raw(text, tag))

        note("  ▸  Taking ownership of %s so permissions can be changed…\n" % root_dir, "run")
        ok, out = run_console('takeown /F "%s" /R /D Y' % root_dir, timeout=900)
        if ok:
            note("  ✓  Ownership check finished for %s\n" % root_dir, "ok")
            return
        note("  ✗  Could not take ownership of %s — permission changes may fail.\n" % root_dir, "fail")
        self.root.after(0, lambda: self.set_status(
            "Could not take ownership of the server folder. (Click for details)", "error",
            raw_output=out, title="Ownership Check Failed"))

    def refresh_system_users(self):
        def fetch():
            hidden_list = load_config().get("hidden_users", [])
            users, hidden_found, err = list_local_users(hidden_list)
            everyone, _err2 = list_all_local_users()

            def update_ui():
                self.all_local_users = everyone
                if err:
                    self.set_status("Could not read the Windows account list. (Click for details)",
                                    "error", raw_output=err, title="Account Lookup Failed")
                    return
                self.discovered_users = users
                note = "Detected NAS Accounts (%d):\n%s" % (len(users), ", ".join(users) or "none")
                if hidden_found:
                    note += "\nHidden from this app: %s" % ", ".join(hidden_found)
                self.lbl_user_summary.config(text=note)

                curr_sel = self.user_listbox.curselection()
                self.user_listbox.delete(0, tk.END)
                for user in users:
                    self.user_listbox.insert(tk.END, user)
                if users:
                    idx = curr_sel[0] if curr_sel and curr_sel[0] < len(users) else 0
                    self.user_listbox.selection_set(idx)
                    self.on_user_selection_changed()
                else:
                    self.rebuild_permissions_ui()
                self.rebuild_hidden_users_ui()

            self.root.after(0, update_ui)

        threading.Thread(target=fetch, daemon=True).start()

    def rebuild_hidden_users_ui(self):
        """One checkbox per Windows account: on = this app manages it."""
        box = getattr(self, "hidden_users_frame", None)
        if box is None:
            return
        for w in box.winfo_children():
            w.destroy()

        everyone = getattr(self, "all_local_users", [])
        if not everyone:
            tk.Label(box, text="No local accounts found yet.", bg=self.palette["glass_card"],
                     fg=self.palette["text_muted"], font=("Segoe UI", 9)).pack(anchor="w")
            return

        hidden = {h.lower() for h in load_config().get("hidden_users", [])}
        self._hidden_vars = {}
        grid = tk.Frame(box, bg=self.palette["glass_card"])
        grid.pack(fill="x")
        for i, name in enumerate(everyone):
            var = tk.BooleanVar(value=name.lower() not in hidden)
            self._hidden_vars[name] = var
            ttk.Checkbutton(grid, text=name, variable=var, style="Glass.TCheckbutton",
                            command=lambda n=name: self._toggle_managed_user(n)).grid(
                row=i // 3, column=i % 3, sticky="w", padx=(0, 24), pady=2)

    def _toggle_managed_user(self, name):
        var = self._hidden_vars.get(name)
        if var is None:
            return
        hidden = [h for h in load_config().get("hidden_users", []) if h.lower() != name.lower()]
        if not var.get():
            hidden.append(name)
        ok, err = save_config({"hidden_users": hidden})
        if not ok:
            self.set_status("Could not save the account list. (Click for details)", "error",
                            raw_output=err, title="Save Failed")
            return
        self.set_status("%s is now %s by EasySMB."
                        % (name, "managed" if var.get() else "ignored"), "success")
        self.refresh_system_users()

    def create_user_only(self): self._exec_create_user(advance_tabs=False)
    def create_user_and_advance(self): self._exec_create_user(advance_tabs=True)

    def _exec_create_user(self, advance_tabs=False):
        username, password = self.ent_v_user.get().strip(), self.ent_v_pass.get().strip()
        if not username or not password:
            messagebox.showerror("Input Error", "Please enter both Username and Password.")
            return

        def worker(log):
            step = log.begin("Check whether '%s' already exists" % username)
            existing, err = list_all_local_users()
            if err:
                log.fail(step, "Could not read the account list.", err)
                log.skip_rest("The account was not created because the account list could not be read.")
                return
            if username.lower() in [u.lower() for u in existing]:
                log.fail(step, "An account called '%s' is already on this PC. Pick another name, "
                               "or delete the old account first." % username)
                log.skip_rest("Nothing was changed.")
                return
            log.ok(step, "No account by that name yet.")

            if not log.run("Create the Windows account '%s'" % username,
                           ["net", "user", username, password, "/add", "/expires:never"], shell=False,
                           verify=lambda: ((username.lower() in [u.lower() for u in list_all_local_users()[0]]),
                                           "Windows now lists the account.")):
                log.skip_rest("Because the account was not created, nothing else was attempted.")
                return

            log.run("Stop the password from expiring",
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                     "Set-LocalUser -Name %s -PasswordNeverExpires $true" % _ps_quote(username)],
                    shell=False, detail="The account will not be locked out over time.")

            step = log.begin("Refresh the user list in this app")
            self.root.after(0, self.refresh_system_users)
            log.ok(step, "'%s' will appear in the permissions panel." % username)

        def done(log):
            if not log.failed:
                self.ent_v_user.delete(0, tk.END)
                self.ent_v_pass.delete(0, tk.END)
                if advance_tabs:
                    self.switch_vertical_tab(2)

        self.run_action("Create user account '%s'" % username, worker, on_done=done)

    def browse_nas_root(self):
        folder = filedialog.askdirectory()
        if folder:
            self.ent_nas_root.delete(0, tk.END)
            self.ent_nas_root.insert(0, os.path.normpath(folder))
            self.scan_or_populate_folders()

    def apply_structure_template(self):
        root_dir = self.ent_nas_root.get().strip()
        if not root_dir:
            messagebox.showerror("Missing Path", "Please select or enter the NAS Root Directory first.")
            return
        mode = self.var_struct_mode.get()
        users = list(self.discovered_users)

        def make(path):
            def fn():
                if os.path.isdir(path):
                    return True, "Already there - left alone.", ""
                os.makedirs(path, exist_ok=True)
                if not os.path.isdir(path):
                    return False, "Windows reported no error but the folder is not there.", ""
                return True, "Created.", ""
            return fn

        def worker(log):
            log.do("Main server folder: %s" % root_dir, make(root_dir))

            if mode in ("multi_private", "multi_shared"):
                if not users:
                    log.fail(log.begin("Create a folder for each user"),
                             "No user accounts were found, so there are no personal folders to create. "
                             "Create the accounts first on Step 1.")
                users_dir = os.path.join(root_dir, "Users")
                log.do("Users folder", make(users_dir))
                for u in users:
                    log.do("Personal folder for %s" % u, make(os.path.join(users_dir, u)))

            if mode == "multi_shared":
                log.do("Shared folder everyone can use", make(os.path.join(root_dir, "Shared")))

            step = log.begin("Re-scan the folder tree")
            folders = list_managed_folders(root_dir)
            self.root.after(0, lambda f=folders: self._adopt_scanned_folders(f))
            log.ok(step, "Found %d folder%s to manage." % (len(folders), "" if len(folders) == 1 else "s"))

        self.run_action("Build the folder layout", worker)

    def scan_or_populate_folders(self):
        root_dir = self.ent_nas_root.get().strip()
        if not root_dir or not os.path.exists(root_dir):
            return

        if not self.takeown_completed:
            self.takeown_completed = True
            threading.Thread(target=self._background_takeown, args=(root_dir,), daemon=True).start()

        try:
            folders = list_managed_folders(root_dir)
        except Exception as e:
            self.set_status("Could not read the folder tree. (Click for details)", "error",
                            raw_output=str(e), title="Directory Scan Error")
            return
        self._adopt_scanned_folders(folders)

    def _adopt_scanned_folders(self, folders):
        """Take a fresh folder list and drop every cached permission reading."""
        self.active_subfolders = folders
        self.live_perms = {}
        self.user_folder_permissions = {}
        self.rebuild_permissions_ui()

    # =========================================================================
    # PERMISSION STATE
    # self.live_perms      = what the server reports right now (read from disk)
    # self.user_folder_permissions = what you have toggled but not yet saved
    # Keeping them apart is what makes "current vs pending" visible.
    # =========================================================================
    def _selected_user(self):
        sel = self.user_listbox.curselection()
        if not sel or not self.discovered_users:
            return None
        try:
            return self.discovered_users[sel[0]]
        except IndexError:
            return None

    def _perm_state_level(self, state):
        """The level the checkboxes currently describe."""
        if not state["enabled"].get():
            return "REMOVE"
        if state["full"].get():
            return "F"
        if state["delete"].get():
            return "M"
        if state["create"].get():
            return "C"
        if state["read"].get():
            return "RX"
        return "REMOVE"

    def _sync_perm_state(self, state, source):
        """Keep the checkboxes describing a permission Windows can actually
        express. Without this you could tick 'Delete' with 'Read' unticked and
        silently get Modify anyway."""
        if state.get("_syncing"):
            return
        state["_syncing"] = True
        try:
            en, full = state["enabled"], state["full"]
            rd, cr, dl = state["read"], state["create"], state["delete"]
            if source == "enabled":
                if en.get():
                    if not (rd.get() or cr.get() or dl.get() or full.get()):
                        rd.set(True)
                else:
                    for v in (full, rd, cr, dl):
                        v.set(False)
            elif source == "full":
                if full.get():
                    en.set(True)
                    for v in (rd, cr, dl):
                        v.set(True)
            else:
                # A granular right changed. Ticking one grants the rights it
                # depends on; unticking one drops the rights that depend on it.
                if state[source].get():
                    if source == "delete":
                        cr.set(True)
                        rd.set(True)
                    elif source == "create":
                        rd.set(True)
                else:
                    if source == "read":
                        cr.set(False)
                        dl.set(False)
                    elif source == "create":
                        dl.set(False)
                if not (rd.get() and cr.get() and dl.get()):
                    full.set(False)
                en.set(bool(rd.get() or cr.get() or dl.get()))
        finally:
            state["_syncing"] = False
        self._refresh_perm_row(state)
        self._update_perm_summary()

    def _seed_perm_state(self, state, info):
        """Point the checkboxes at a level without firing the linkage rules."""
        level = info.get("level", "REMOVE")
        was, state["_syncing"] = state.get("_syncing"), True
        try:
            state["enabled"].set(level != "REMOVE")
            state["full"].set(level == "F")
            state["read"].set(level in ("F", "M", "C", "RX"))
            state["create"].set(level in ("F", "M", "C"))
            state["delete"].set(level in ("F", "M"))
        finally:
            state["_syncing"] = was

    def _make_perm_state(self, info):
        """Create the editable checkbox vars for one folder, seeded from disk."""
        state = {
            "enabled": tk.BooleanVar(), "full": tk.BooleanVar(), "read": tk.BooleanVar(),
            "create": tk.BooleanVar(), "delete": tk.BooleanVar(),
            "_syncing": False, "_widgets": {},
        }
        self._seed_perm_state(state, info)
        for key in ("enabled", "full", "read", "create", "delete"):
            state[key].trace_add("write", lambda *a, s=state, k=key: self._sync_perm_state(s, k))
        return state

    def revert_perm_row(self, user, fpath):
        """Undo unsaved edits on one folder, back to what the server reports."""
        state = (self.user_folder_permissions.get(user) or {}).get(fpath)
        if not state:
            return
        self._seed_perm_state(state, state.get("_live") or {})
        self._refresh_perm_row(state)
        self._update_perm_summary()

    def _badge_color(self, info):
        if info.get("error"):
            return self.palette["error"]
        if info.get("deny"):
            return self.palette["error"]
        level = info.get("level", "REMOVE")
        if level == "REMOVE":
            return self.palette["text_muted"]
        if level == "OTHER":
            return self.palette["info_click"]
        if level == "F":
            return self.palette["success"]
        return self.palette["text_frost"]

    def _refresh_perm_row(self, state):
        """Repaint one row's 'on the server' badge and 'will apply' line."""
        w = state.get("_widgets") or {}
        if not w:
            return
        info = state.get("_live") or {}
        want = self._perm_state_level(state)
        current = info.get("level", "REMOVE")
        dirty = want != current

        try:
            if w.get("badge") and w["badge"].winfo_exists():
                w["badge"].config(text="ON SERVER: " + describe_permission(info), fg=self._badge_color(info))
            if w.get("dirty") and w["dirty"].winfo_exists():
                w["dirty"].config(text="  ● UNSAVED" if dirty else "  ✓ saved",
                                  fg=self.palette["info_click"] if dirty else self.palette["success"])
            if w.get("pending") and w["pending"].winfo_exists():
                if dirty:
                    w["pending"].config(
                        text="Will change:  %s  →  %s        (press Save to write it)"
                             % (PERM_LABELS.get(current, current), PERM_LABELS.get(want, want)),
                        fg=self.palette["info_click"])
                else:
                    w["pending"].config(
                        text="Matches the server:  %s" % PERM_LABELS.get(current, current),
                        fg=self.palette["text_muted"])
            if w.get("note") and w["note"].winfo_exists():
                notes = []
                if info.get("error"):
                    notes.append("This folder could not be read: %s" % info["error"])
                if info.get("deny"):
                    notes.append("A DENY rule exists for this user and overrides anything granted here.")
                elif info.get("inherited") and current != "REMOVE":
                    notes.append("This access is inherited from the parent folder. "
                                 "Use 'Save All Changes' once to break inheritance on the subfolders.")
                if current == "OTHER":
                    notes.append("This folder has a permission this app did not set; saving will replace it.")
                w["note"].config(text="\n".join(notes))
                if notes:
                    w["note"].pack(fill="x", pady=(2, 4))
                else:
                    w["note"].pack_forget()
        except tk.TclError:
            pass

    def _pending_changes(self):
        """Every (user, folder, from, to, state) that differs from the server."""
        changes = []
        for user, fmap in self.user_folder_permissions.items():
            live = self.live_perms.get(user) or {}
            for fpath, state in fmap.items():
                info = live.get(fpath)
                if info is None:
                    continue
                want = self._perm_state_level(state)
                if want != info.get("level", "REMOVE"):
                    changes.append((user, fpath, info.get("level", "REMOVE"), want, state))
        return changes

    def _update_perm_summary(self):
        if not hasattr(self, "lbl_perm_summary"):
            return
        changes = self._pending_changes()
        try:
            if not changes:
                self.lbl_perm_summary.config(
                    text="No unsaved changes — the panel matches the server.",
                    fg=self.palette["success"])
            else:
                users = len({c[0] for c in changes})
                self.lbl_perm_summary.config(
                    text="● %d unsaved change%s across %d user%s — nothing is written until you press Save."
                         % (len(changes), "" if len(changes) == 1 else "s",
                            users, "" if users == 1 else "s"),
                    fg=self.palette["info_click"])
        except tk.TclError:
            pass

    def load_live_perms(self, user, force=False, discard_edits=False):
        """Read this user's real permissions for every folder, off the UI thread."""
        if getattr(self, "_perm_loading", False):
            return
        if not user or not self.active_subfolders:
            return
        if not force and user in self.live_perms:
            return

        self._perm_loading = True
        folders = list(self.active_subfolders)
        if discard_edits:
            self.user_folder_permissions.pop(user, None)
        self.set_status("Checking what '%s' can currently open..." % user, "info")

        def work():
            snap = {}
            for fpath in folders:
                snap[fpath] = get_user_permission(fpath, user)

            def done():
                self._perm_loading = False
                self.live_perms[user] = snap
                bad = [f for f in folders if snap[f].get("error")]
                if bad:
                    self.set_status(
                        "Loaded '%s' — %d folder(s) could not be read. (Click for details)" % (user, len(bad)),
                        "error",
                        raw_output="\n\n".join("%s\n    %s" % (f, snap[f]["error"]) for f in bad),
                        title="Folders That Could Not Be Read")
                else:
                    self.set_status("Live permissions for '%s' loaded from the server." % user, "success")
                if self._selected_user() == user:
                    self.rebuild_permissions_ui()
                else:
                    self._update_perm_summary()

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def refresh_live_perms_clicked(self):
        user = self._selected_user()
        if not user:
            self.set_status("Select a user first.", "info")
            return
        if self._pending_changes() and not messagebox.askyesno(
                "Discard unsaved changes?",
                "Re-reading the server will discard the changes you have not saved yet.\n\nContinue?"):
            return
        self.load_live_perms(user, force=True, discard_edits=True)

    def rebuild_permissions_ui(self):
        for widget in self.folder_inner_frame.winfo_children():
            widget.destroy()

        def message(text):
            tk.Label(self.folder_inner_frame, text=text, bg=self.palette["well_bg"],
                     fg=self.palette["text_muted"], font=("Segoe UI", 9), justify="left",
                     wraplength=520).pack(anchor="w", padx=10, pady=10)
            self._update_perm_summary()

        selected_user = self._selected_user()
        if not selected_user:
            self.lbl_perm_header.config(text="2. Choose what they can do:")
            return message("Pick a person on the left to see what they can open.")

        self.lbl_perm_header.config(text="What '%s' can do in each folder:" % selected_user)
        self.user_folder_permissions.setdefault(selected_user, {})

        if not self.active_subfolders:
            return message("No folders found yet. Pick the main folder above, "
                           "then press 'Create / Find Folders'.")

        live = self.live_perms.get(selected_user)
        if live is None:
            self.load_live_perms(selected_user)
            return message("Checking what '%s' can currently open..." % selected_user)

        for fpath in self.active_subfolders:
            info = live.get(fpath) or {"level": "REMOVE", "label": PERM_LABELS["REMOVE"],
                                       "inherited": False, "deny": False, "raw": "", "error": ""}

            state = self.user_folder_permissions[selected_user].get(fpath)
            if state is None:
                state = self._make_perm_state(info)
                self.user_folder_permissions[selected_user][fpath] = state
            state["_live"] = info
            state["_widgets"] = {}

            row_f = tk.Frame(self.folder_inner_frame, bg=self.palette["well_bg"])
            row_f.pack(fill="x", padx=8, pady=3)

            perm_f = tk.Frame(row_f, bg=self.palette["glass_rim_shadow"], padx=24, pady=6)

            def make_toggle(frame, btn):
                def _toggle():
                    if frame.winfo_ismapped():
                        frame.pack_forget()
                        btn.config(text=btn.cget("text").replace("▼", "▶", 1))
                    else:
                        frame.pack(fill="x", pady=(0, 6))
                        btn.config(text=btn.cget("text").replace("▶", "▼", 1))
                    self.folder_scroll_canvas.configure(scrollregion=self.folder_scroll_canvas.bbox("all"))
                return _toggle

            head_f = tk.Frame(row_f, bg=self.palette["glass_card"])
            head_f.pack(fill="x")

            lbl_dirty = tk.Label(head_f, text="", bg=self.palette["glass_card"],
                                 fg=self.palette["success"], font=("Segoe UI", 8, "bold"))
            lbl_dirty.pack(side="right", padx=(4, 10))
            lbl_badge = tk.Label(head_f, text="", bg=self.palette["glass_card"],
                                 fg=self.palette["text_muted"], font=("Segoe UI", 8, "bold"))
            lbl_badge.pack(side="right", padx=4)

            btn_header = tk.Button(head_f, text="▶ %s" % fpath, bg=self.palette["glass_card"],
                                   fg=self.palette["text_bright"], font=("Segoe UI", 9, "bold"),
                                   relief="flat", anchor="w", padx=10)
            btn_header.pack(side="left", fill="x", expand=True)
            btn_header.config(command=make_toggle(perm_f, btn_header))

            top_grid = tk.Frame(perm_f, bg=self.palette["glass_rim_shadow"])
            top_grid.pack(fill="x", pady=(4, 8))
            ttk.Checkbutton(top_grid, text="Let this person see the folder", variable=state["enabled"],
                            style="Glass.TCheckbutton").pack(anchor="w", pady=2)
            ttk.Checkbutton(top_grid, text="Full access (open, add, and delete)", variable=state["full"],
                            style="Glass.TCheckbutton").pack(anchor="w", pady=2)

            tk.Frame(perm_f, bg=self.palette["glass_rim_light"], height=1).pack(fill="x", pady=4)

            btm_grid = tk.Frame(perm_f, bg=self.palette["glass_rim_shadow"])
            btm_grid.pack(fill="x", pady=(4, 8))
            ttk.Checkbutton(btm_grid, text="Open and read files", variable=state["read"],
                            style="Dark.TCheckbutton").grid(row=0, column=0, sticky="w", padx=(0, 20), pady=4)
            ttk.Checkbutton(btm_grid, text="Add files and folders", variable=state["create"],
                            style="Dark.TCheckbutton").grid(row=0, column=1, sticky="w", padx=(0, 20), pady=4)
            ttk.Checkbutton(btm_grid, text="Delete and move files", variable=state["delete"],
                            style="Dark.TCheckbutton").grid(row=0, column=2, sticky="w", padx=(0, 20), pady=4)

            lbl_pending = tk.Label(perm_f, text="", bg=self.palette["glass_rim_shadow"],
                                   fg=self.palette["text_muted"], font=("Segoe UI", 9, "bold"),
                                   anchor="w", justify="left")
            lbl_pending.pack(fill="x", pady=(2, 2))

            lbl_note = tk.Label(perm_f, text="", bg=self.palette["glass_rim_shadow"],
                                fg=self.palette["info_click"], font=("Segoe UI", 8),
                                anchor="w", justify="left", wraplength=470)

            save_btn_frame = tk.Frame(perm_f, bg=self.palette["glass_rim_shadow"])
            save_btn_frame.pack(fill="x", pady=4)
            if info.get("raw"):
                FrostedGlassButton(
                    save_btn_frame, text="🔍 Show the Windows rule",
                    command=lambda i=info, f=fpath, u=selected_user: self.show_raw_acl(u, f, i),
                    width=140, height=32, radius=14, color_scheme="neutral").pack(side="left")
            FrostedGlassButton(
                save_btn_frame, text="↺ Undo my edits",
                command=lambda u=selected_user, f=fpath: self.revert_perm_row(u, f),
                width=140, height=32, radius=14, color_scheme="neutral").pack(side="left", padx=(8, 0))
            FrostedGlassButton(
                save_btn_frame, text="💾 Save this folder now",
                command=lambda u=selected_user, f=fpath, s=state: self.apply_single_folder_permissions(u, f, s),
                width=200, height=32, radius=14, color_scheme="neutral").pack(side="right")

            state["_widgets"] = {"badge": lbl_badge, "dirty": lbl_dirty,
                                 "pending": lbl_pending, "note": lbl_note, "header": btn_header}
            self._refresh_perm_row(state)

        self._update_perm_summary()

    def show_raw_acl(self, user, fpath, info):
        self.set_status("Raw Windows rule for '%s' on %s (Click to view)" % (user, os.path.basename(fpath)),
                        "success",
                        raw_output="Folder: %s\nUser:   %s\nLevel:  %s\n\nMatching Windows ACL entries:\n%s"
                                   % (fpath, user, describe_permission(info), info.get("raw") or "(none)"),
                        title="Raw Permission Entry")
        self.show_output_popup("success")

    def on_user_selection_changed(self, event=None):
        self.rebuild_permissions_ui()

    # =========================================================================
    # SAVING - every write is read back and confirmed before it is reported
    # =========================================================================
    def _apply_permission_changes(self, targets, title, publish_share=False):
        """targets: [(user, folder, state)]. Each write is verified on the server."""
        root_dir = self.ent_nas_root.get().strip()
        if publish_share and (not root_dir or not os.path.exists(root_dir)):
            messagebox.showerror("Error", "NAS root directory does not exist.")
            return

        plan = []
        for user, fpath, state in targets:
            info = (self.live_perms.get(user) or {}).get(fpath) or {}
            plan.append({"user": user, "folder": fpath,
                         "want": self._perm_state_level(state),
                         "before": info.get("level", "REMOVE")})

        if not plan and not publish_share:
            self.set_status("Nothing to save - this folder already matches the server.", "info")
            return

        def worker(log):
            if publish_share:
                if not self._share_steps(log, root_dir):
                    log.skip_rest("Permissions were not written because the share setup failed above. "
                                  "Fix that first - the per-user rules depend on it.")
                    return

            if not plan:
                log.note("Apply per-user permissions", "No permission changes were pending.")
                return

            for item in plan:
                label = "%s  ->  %s" % (item["user"], os.path.basename(item["folder"]) or item["folder"])
                step = log.begin(label)
                ok, after, cmdlog = apply_user_permission(item["folder"], item["user"], item["want"])
                item["after"] = after
                self.live_perms.setdefault(item["user"], {})[item["folder"]] = after

                change = "%s  ->  %s" % (PERM_LABELS.get(item["before"], item["before"]),
                                         PERM_LABELS.get(item["want"], item["want"]))
                if ok and item["before"] == item["want"]:
                    log.skip(step, "%s\nAlready correct - re-applied and confirmed." % change)
                elif ok:
                    log.ok(step, "%s\nConfirmed by reading the folder back." % change)
                else:
                    log.fail(step, "%s\nThe folder still reports %s."
                             % (change, describe_permission(after)), cmdlog)

        def done(log):
            if self._selected_user():
                self.rebuild_permissions_ui()
            else:
                self._update_perm_summary()

        self.run_action(title, worker, on_done=done, popup_on_success=True)

    def _share_steps(self, log, root_dir, mode=None):
        """Publish the share layout. Returns False if something failed."""
        mode = mode or load_config().get("share_mode", SHARE_MODE_FOLDERS)

        step = log.begin("Read the current share list")
        shares, err = list_smb_shares()
        if err:
            log.fail(step, "Could not ask Windows what is shared right now.", err)
            return False
        mine = [s for s in shares if not s["special"]]
        log.ok(step, "Windows currently publishes %d share%s: %s"
               % (len(mine), "" if len(mine) == 1 else "s",
                  ", ".join(sorted(s["name"] for s in mine)) or "none"))

        planned = plan_shares(root_dir, mode)
        if not planned:
            log.fail(log.begin("Work out what to publish"),
                     "There are no folders inside %s yet, so there is nothing to share. "
                     "Create the folder layout first." % root_dir)
            return False

        step = log.begin("Work out what the network should show")
        if mode == SHARE_MODE_FOLDERS:
            log.ok(step, "Tapping the server will show: %s\n(no wrapper folder to open first)"
                   % ", ".join(n for n, _p in planned))
        else:
            log.ok(step, "A single share '%s' - users open it, then pick a folder inside."
                   % planned[0][0])

        # --- remove leftovers, one visible step each -------------------------
        stale = shares_to_remove(shares, root_dir, planned)
        if not stale:
            log.note("Remove leftover shares", "None found - the share list is already clean.")
        for sh in stale:
            log.run("Remove leftover share '%s'  (%s)" % (sh["name"], sh["path"]),
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                     "Remove-SmbShare -Name %s -Force" % _ps_quote(sh["name"])],
                    shell=False,
                    detail="This one was making folders appear twice on phones.",
                    verify=lambda n=sh["name"]: (not share_exists(n), "Gone from the share list."))

        # --- NTFS groundwork -------------------------------------------------
        log.run("Let everyone walk through the top folder (%s)" % root_dir,
                'icacls "%s" /grant "Authenticated Users":(RX)' % root_dir,
                detail="Needed so users can reach the folders inside; it does not expose the contents.")

        for _name, path in planned:
            log.run("Lock down %s" % os.path.basename(path),
                    'icacls "%s" /inheritance:r /grant:r "Administrators":(OI)(CI)F' % path,
                    detail="Stops this folder inheriting access from its parent, so your "
                           "per-user rules are the only thing that applies.")

        # --- publish ----------------------------------------------------------
        existing = {s["name"].lower(): s for s in shares}
        all_ok = True
        for name, path in planned:
            current = existing.get(name.lower())
            if current and _norm(current["path"]) != _norm(path):
                if not is_under_root(current["path"], root_dir):
                    # Someone else's share happens to have the same name. Deleting
                    # it would break something this app knows nothing about.
                    log.fail(log.begin("Publish share '%s'" % name),
                             "This PC already has a share called '%s' pointing at %s, which is "
                             "outside your NAS folder.\nEasySMB will not remove a share it did not "
                             "create. Either rename the folder %s, or delete that share yourself in "
                             "Windows, then run this again." % (name, current["path"], path))
                    all_ok = False
                    continue
                log.run("Remove the old '%s' share pointing at the wrong folder" % name,
                        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                         "Remove-SmbShare -Name %s -Force" % _ps_quote(name)], shell=False,
                        detail="It pointed at %s instead of %s." % (current["path"], path))
                current = None

            if current:
                script = ("Set-SmbShare -Name %s -FolderEnumerationMode AccessBased -Force; "
                          "Grant-SmbShareAccess -Name %s -AccountName 'Authenticated Users' "
                          "-AccessRight Change -Force"
                          % (_ps_quote(name), _ps_quote(name)))
                label = "Update share '%s'" % name
            else:
                script = ("New-SmbShare -Name %s -Path %s -ChangeAccess 'Authenticated Users' "
                          "-FolderEnumerationMode AccessBased"
                          % (_ps_quote(name), _ps_quote(path)))
                label = "Publish share '%s'  ->  %s" % (name, path)

            def verify(n=name, p=path):
                live, e = list_smb_shares()
                if e:
                    return False, "Could not read the share list back: %s" % e
                for sh in live:
                    if sh["name"].lower() == n.lower():
                        if _norm(sh["path"]) != _norm(p):
                            return False, "The share exists but points at %s." % sh["path"]
                        if not sh["abe"]:
                            return False, "The share exists but access-based enumeration is off."
                        return True, "Live at \\\\%s\\%s - folders the user cannot open stay hidden." % (
                            socket.gethostname(), n)
                return False, "Windows accepted the command but the share is not in the list."

            if not log.run(label, ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                           shell=False, verify=verify):
                all_ok = False

        step = log.begin("Confirm what the network now shows")
        final, err = list_smb_shares()
        if err:
            log.fail(step, "Could not read the final share list.", err)
            return False
        visible = sorted(s["name"] for s in final if not s["special"])
        log.ok(step, "Tapping \\\\%s now shows: %s" % (self._best_local_ip(), ", ".join(visible) or "nothing"),
               diagnose_network_view(root_dir, final))
        return all_ok

    def save_share_mode(self):
        mode = self.var_share_mode.get()
        ok, err = save_config({"share_mode": mode})
        if not ok:
            self.set_status("Could not save the share setting. (Click for details)", "error",
                            raw_output=err, title="Save Failed")
            return
        self.set_status("Saved. Press 'Update The Folder List' to apply it.", "success")

    def publish_network_shares(self):
        root_dir = self.ent_nas_root.get().strip()
        if not root_dir or not os.path.exists(root_dir):
            messagebox.showerror("Error", "Pick the main server folder first (Step 2).")
            return
        self.run_action("Update the folder list", lambda log: self._share_steps(log, root_dir),
                        popup_on_success=True)

    def show_network_view(self):
        root_dir = self.ent_nas_root.get().strip()
        if not root_dir:
            messagebox.showerror("Error", "Pick the main server folder first (Step 2).")
            return

        def worker(log):
            step = log.begin("Ask Windows what is currently shared")
            shares, err = list_smb_shares()
            if err:
                log.fail(step, "Could not read the share list.", err)
                return
            report = diagnose_network_view(root_dir, shares)
            problems = "PROBLEMS FOUND" in report
            if problems:
                log.fail(step, "Found layout problems - the full explanation is below.", report)
            else:
                log.ok(step, "The share layout is clean.", report)

        self.run_action("See what people see", worker, popup_on_success=True,
                        needs_admin=False)

    def apply_single_folder_permissions(self, user, fpath, state):
        self._apply_permission_changes(
            [(user, fpath, state)],
            title="Save permissions - %s" % (os.path.basename(fpath) or fpath))

    def apply_all_configured_permissions(self):
        changes = self._pending_changes()
        if not changes:
            if not messagebox.askyesno(
                    "No permission changes",
                    "Every folder already matches the server, so there is nothing to write.\n\n"
                    "Do you still want to re-publish the network shares and re-apply the folder "
                    "lockdown?"):
                return
        targets = [(u, f, s) for (u, f, _b, _w, s) in changes]
        self._apply_permission_changes(targets, title="Save all changes",
                                       publish_share=True)

    def apply_hosts_alias(self):
        alias = self.ent_domain_alias.get().strip()
        if not alias:
            return messagebox.showerror("Error", "Please enter a valid alias.")
        alias_clean = re.sub(r"[^a-zA-Z0-9\.\-_]", "", alias)
        hosts_path = r"C:\Windows\System32\drivers\etc\hosts"

        def worker(log):
            step = log.begin("Read the Windows hosts file")
            try:
                with io.open(hosts_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception as e:
                log.fail(step, "Could not open %s. The app needs to be running as Administrator." % hosts_path, str(e))
                log.skip_rest("The hosts file was not changed.")
                return
            log.ok(step, "%d lines read." % len(content.splitlines()))

            if re.search(r"\s%s\s*$" % re.escape(alias_clean), content, re.M):
                log.note("Add '%s'" % alias_clean, "Already in the hosts file - nothing to do.")
                return

            step = log.begin("Add '%s' pointing at this machine" % alias_clean)
            try:
                with io.open(hosts_path, "a", encoding="utf-8") as f:
                    f.write("\n127.0.0.1\t%s\n" % alias_clean)
            except Exception as e:
                log.fail(step, "Could not write to the hosts file.", str(e))
                return
            with io.open(hosts_path, "r", encoding="utf-8", errors="replace") as f:
                now = f.read()
            if alias_clean in now:
                log.ok(step, "Confirmed in the file: 127.0.0.1  %s" % alias_clean)
            else:
                log.fail(step, "The write reported success but the entry is not in the file.")

        self.run_action("Add hosts alias '%s'" % alias_clean, worker)

    def install_tailscale(self):
        self._winget_install("Tailscale.Tailscale", "Tailscale")

    def install_snapraid(self):
        self._winget_install("SnapRAID.SnapRAID", "SnapRAID")

    def _winget_install(self, package_id, friendly):
        def worker(log):
            step = log.begin("Check that Windows Package Manager is available")
            ok, out = run_console(["winget", "--version"], shell=False)
            if not ok:
                log.fail(step, "winget is not available on this PC. Install 'App Installer' from the "
                               "Microsoft Store, then try again.", out)
                log.skip_rest("%s was not installed." % friendly)
                return
            log.ok(step, "winget %s" % out.strip().splitlines()[0] if out.strip() else "found")

            step = log.begin("Download and install %s" % friendly)
            ok, out = run_console(["winget", "install", "-e", "--id", package_id, "--silent",
                                   "--accept-package-agreements", "--accept-source-agreements"],
                                  shell=False, timeout=900)
            low = out.lower()
            if ok:
                log.ok(step, "%s installed." % friendly, out)
            elif "already installed" in low or "no applicable upgrade" in low:
                log.skip(step, "%s is already installed - nothing to do." % friendly, out)
            else:
                log.fail(step, "winget could not install %s." % friendly, out)

        self.run_action("Install %s" % friendly, worker)



def launch_desktop_app():
    """Start the window, elevating first unless the user declines."""
    if not is_admin():
        started, reason = run_as_admin()
        if started:
            return                      # the elevated copy takes over
        # Elevation did not happen. Say so, and offer a look-but-do-not-touch
        # session, because reading permissions and shares works fine unelevated.
        probe = tk.Tk()
        probe.withdraw()
        carry_on = messagebox.askyesno(
            "Administrator rights not granted",
            "%s\n\nOpen EasySMB anyway in view-only mode?\n\n"
            "You will be able to see everyone's permissions and what the network "
            "shows, but saving changes will be blocked until you reopen as "
            "Administrator." % reason)
        probe.destroy()
        if not carry_on:
            return

    root = tk.Tk()
    app = GlassSMBManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    if "--headless" in sys.argv:
        run_headless_server()
    else:
        launch_desktop_app()
