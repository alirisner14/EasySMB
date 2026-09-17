# Easy SMB Core

Turn a spare Windows PC into a private home file server — without the command line.

You pick a folder, tick who's allowed to open what, and press Save. Everyone in the
house can then reach their files from a laptop, a phone or a tablet. There's also a
web page you can open from your phone to change things later, so you don't have to
go and sit at the server.

## What it does

**Shows you the real permissions, always.** Every folder displays what Windows
actually reports right now — `ON SERVER: Read Only`, `Full Control (inherited)`,
`No Access`. Change as much as you like across as many people as you like; nothing
is written until you press Save, and rows you've changed are marked `● UNSAVED`.

**Tells you whether the save worked.** After saving, every rule is read back off the
folder and you get a pass/fail report. Windows tools routinely report success while
changing nothing — this catches that.

```
  ✓  1. Read the current share list
  ✓  5. Lock down Family_Shared
  ✗  8. Publish share 'Resources'
        The command reported an error.
        | New-SmbShare : Access is denied.
  ✗  7 ok, 3 failed, 1 skipped
```

**Every action reports itself step by step.** There's an Activity box along the
bottom. Each step announces itself, then turns into a tick or a cross with the real
error text attached. If something fails you're told which step, and what it said.

**Fixes the "why do I see everything twice on my phone?" problem.** Tapping a server
from a phone shows its *share list*, not its folder tree. Share the parent folder as
well as its children and every folder shows up twice. There's a button that explains
what your server currently looks like from outside, in plain words, and a setting to
publish your top-level folders directly so there's no wrapper folder to tap through.

**Hides accounts that aren't people.** The PC's own sign-in account, service
accounts — untick them and they stop appearing in the permissions list. Windows
built-ins (Administrator, Guest, DefaultAccount) are hidden automatically.

**Manage it from your phone.** A password-protected web dashboard, optionally behind
a real HTTPS certificate via Tailscale. Changes made there are applied immediately —
same commands, same read-back check.

**Protects against a dead hard drive.** SnapRAID install, health checks, recovery,
and a nightly automatic backup job.

## Getting started

1. Download `smb_manager_GUI.exe` from [Releases](https://github.com/alirisner14/EasySMB/releases).
2. Put it in a permanent folder.
3. Double-click it. Windows asks for administrator permission — say yes. It needs
   that to change folder permissions and publish shares.
4. Work through the tabs left to right. The steps are numbered 1 to 6.

If you say no to the administrator prompt, the app offers to open in view-only mode
so you can still look at everyone's permissions without changing anything. The header
tells you which mode you're in.

## The six steps

| Step | Tab | What you do |
|-----|-----|-----|
| 1 | People | Make a Windows account for each person, and untick any account that isn't a real user |
| 2 | Folders & Access | Pick the main folder everything lives in |
| 3 | Folders & Access | Create the folder layout, or scan the one you already have |
| 4 | Folders & Access | Tick who can open, add to, and delete from each folder |
| 5 | Folders & Access | Choose what people see when they tap your server, and apply it |
| 6 | Server Name | Give the server a friendly name instead of an IP address |

## Opening your files from another device

**Windows:** In File Explorer's address bar, type `\\` followed by the server's IP —
for example `\\192.168.1.50`.

**Mac:** Finder → Go → Connect to Server → `smb://192.168.1.50`

**iPhone / iPad:** Files app → Browse → ⋯ → Connect to Server → `smb://192.168.1.50`

**Android:** Most file managers have a "Network" or "SMB" option.

Sign in with the Windows username and password you made in Step 1. You'll only see
the folders you have access to.

## Managing it from your phone

On the "Manage From Your Phone" tab, set a password and press **Activate Remote
Dashboard**. The last step of the report tells you the exact address to open.

You get two addresses, and they do the same thing:

- On your home network: `http://<server-ip>:50505`
- From anywhere, via Tailscale: `https://<pc-name>.<your-tailnet>.ts.net`

The Tailscale one is worth turning on. Over plain `http://` your dashboard password
travels in a form anyone sharing your network can read; Tailscale puts a real HTTPS
certificate in front of it. It uses `tailscale serve`, which is private to your own
devices — never `tailscale funnel`, which would put it on the public internet.

The dashboard's username is `admin` and the password is the one you set.

## Reaching it away from home

The "Use It Away From Home" tab installs [Tailscale](https://tailscale.com), which
links your devices together privately without touching your router's port
forwarding. Install it, sign in on the server and on your phone, and the server is
reachable from anywhere.

## Running from source

Requires Python 3.8+ on Windows.

```cmd
python smb_manager_GUI.pyw
```

It will ask for administrator rights and relaunch itself.

## Building the .exe

Double-click **`compile.bat`**. The build lands in `dist\smb_manager_GUI.exe`, and
the `.spec` sets `uac_admin=True`, so the finished program always asks for
administrator rights when it starts.

Use the script rather than calling PyInstaller directly. Running

```cmd
python -m PyInstaller --noconfirm --clean smb_manager_GUI.spec
```

works when everything goes right, but **it does not delete the previous build**.
`--clean` only clears PyInstaller's own cache. If a build fails, the old
`dist\smb_manager_GUI.exe` is left exactly where it was, so it is easy to pick up
last week's program thinking it is the one you just built.

`compile.bat` avoids that, and:

- deletes the previous `build\` and `dist\` before compiling, and stops with a clear
  message if it cannot — usually because EasySMB is still running
- checks Python and PyInstaller are installed, offering to install PyInstaller
- verifies the script parses *before* the slow part, leaving your last working exe
  untouched if it doesn't
- fails with a readable reason instead of a Python traceback

## Where settings live

`easynas_config.json`, next to the program. It holds your main folder, the accounts
you've hidden, the share layout, and the dashboard password. Copy it alongside the
`.exe` if you move an existing setup.

## The archive drive

The "Archive Old Files" tab moves a finished folder off your main drive onto a
second drive. It is never a plain move: the folder is copied, every file is
counted and sized against the original, and only then is the original deleted.
If anything does not match, nothing is removed.

Tick **"Open it over the network too"** and the archive drive is published as its
own folder, so tapping the server shows it beside Users, Family_Shared and
Resources. You can browse it and drag files into it from any device, and who can
open it is set in the permissions panel like any other folder. Leave it unticked
and only administrators signed in to the server can reach it.

Worth being clear about: anything reachable for dragging files **in** is also
reachable for deleting them **out**.

### SnapRAID does not merge your drives

SnapRAID computes parity so a failed drive can be rebuilt. It does not pool
drives — each one keeps its own filesystem, and files stay on whichever drive you
put them on. That is why the archive drive is a separate place rather than part
of one big folder.

SnapRAID has a `pool` option, but it builds a **read-only** view out of symbolic
links, and on Windows every client has to be configured with
`fsutil behavior set SymlinkEvaluation` — which phones and tablets cannot do.

If you genuinely want one merged folder spanning every drive, that needs a
separate pooling layer; SnapRAID's own documentation points at
[StableBit DrivePool](https://stablebit.com/DrivePool) for Windows, which runs
alongside SnapRAID. Publishing the archive as its own share is the option that
needs no extra software.

## Licence

MIT — see [LICENSE](LICENSE).
