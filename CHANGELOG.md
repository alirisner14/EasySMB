# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.0] - 2026-09-17
### Added
- **Any drive can be opened over the network, not just the main one.** SnapRAID
  protects drives from failing; it does not join them together, so a second or third
  drive is simply a separate place that has to be published to be reachable. The
  Archive tab now has an "Other Drives On This Server" list - add a scratch drive, a
  media drive, whatever you have, and it is published as its own folder and appears
  in the permissions panel like everything else.
  - Adding the SnapRAID **parity** drive is refused: parity holds the protection, not
    files, and sharing it invites somebody to delete the thing guarding every other
    drive.
  - Two drives wanting the same share name are disambiguated rather than one quietly
    replacing the other.
  - Unticking a drive takes it off the network without touching anything on it.
- **The archive drive can be opened over the network like any other folder.**
  Turn it on from the Archive tab and it is published as its own share, so tapping
  the server shows it next to Users, Family_Shared and Resources. You can browse it
  and drag files into it from any device.
  - Its folders now appear in the permissions panel, so who can open the archive is
    set the same way as every other folder.
  - Archived folders take the archive drive's access rules rather than being locked
    to administrators. With the setting off, the old admin-only behaviour is kept.
  - A name clash is handled: if a real folder is already called Archive, the drive is
    published as ArchiveDrive instead of quietly replacing it.
  - The tab spells out the trade-off - anything reachable for dragging files in is
    also reachable for deleting them out, and SnapRAID protects against a dead drive,
    not a deleted file.

### Note on SnapRAID
SnapRAID does not pool drives. It computes parity so a failed drive can be rebuilt;
each drive keeps its own filesystem and files stay where you put them. Its built-in
`pool` option makes a read-only view out of symbolic links, and on Windows every
client has to be configured with `fsutil behavior set SymlinkEvaluation`, which
phones and tablets cannot do. For one merged folder across drives, SnapRAID's own
documentation points at a separate pooling tool such as StableBit DrivePool.
Publishing the archive as its own share is the option that needs no extra software.

## [2.1.0] - 2026-09-17
### Fixed
- **Every button on the web dashboard did nothing.** One line of the dashboard's
  JavaScript built a UNC path as `'<tr><th>\\server\' + sh.name`, where the
  trailing `\'` escaped the closing quote instead of producing a backslash, so the
  string literal never ended. That is a syntax
  error, and a syntax error kills the entire `<script>` block - so `openTab()` was
  never defined and no tab, button or form on the page responded. The Python file
  imported perfectly and the page rendered, which is why it shipped.
- **Icons showed as `â›`, `ðŸ"'` and similar mojibake.** The page is UTF-8 but said so
  nowhere: no `charset` on the `Content-Type` header and no `<meta charset>` in the
  HTML, so browsers fell back to windows-1252. Both are now declared.

### Added
- **Archive Old Files.** Move a finished folder off the main drive onto a second
  drive, from the desktop app or from your phone. It is never a plain move: the
  folder is copied, every file is counted and sized against the original, and only
  then is the original deleted. If anything does not match, nothing is removed and
  the original stays exactly where it was.
  - Permissions travel with the files. A cross-drive move is a copy plus a delete,
    so files would otherwise arrive with the destination's rules; the copy uses
    `robocopy /SEC`. (`/COPYALL`, the usual advice, also copies audit settings and
    fails with "You do not have the Manage Auditing user right".)
  - Archived folders are locked to administrators and are never published as a
    share, so nobody can delete them from a phone.
  - `snapraid.conf` is read to work out what the archive drive is. Archiving to a
    **parity** drive is refused outright; archiving to a data drive reminds you that
    parity is stale until you sync; a drive outside the array is flagged as having
    no protection.
  - Refuses to archive the server root, anything outside it, or into a folder inside
    the server; never overwrites an existing archive; checks free space first.
  - From the dashboard the job runs in the background with live progress, so a long
    copy does not time out on a phone.
- `tools/check_dashboard_js.py`, run by `compile.bat`, parses the dashboard's
  JavaScript with `node --check` so a dead page cannot ship again. Skipped when node
  is not installed.

## [2.0.1] - 2026-09-13
### Fixed
- **"Turn on phone access" crashed immediately** with `TypeError: must be real
  number, not str`. The PowerShell command that stops an already-running dashboard
  contains literal percent signs (`LIKE '%--headless%'`) and was being built with
  %-formatting, so Python read `%--he` as a float conversion. The file parsed and
  imported perfectly; it only failed when the button was pressed.
- The same command could collapse into a match-everything filter if `sys.argv[0]`
  was degenerate, which would have terminated unrelated programs. It now matches
  nothing rather than everything in that case.

### Added
- `tools/check_format_strings.py`, run automatically by `compile.bat`, so this class
  of runtime-only bug cannot ship again. A syntax check does not catch it.
- Tests covering the deploy and remove paths for phone access, which had never been
  executed — that gap is why the bug shipped.

## [2.0.0] - 2026-09-12

A large release focused on one idea: **the program should never quietly do nothing.**
Permissions are read from the real filesystem, every write is confirmed afterwards,
and every action reports itself step by step.

### Breaking
- **Folders are now published individually instead of behind one wrapper share.**
  Tapping the server used to show a single folder (e.g. `FamilyNAS`) that everyone had
  to open before reaching anything. It now shows your top-level folders directly, so
  `\\server\FamilyNAS\Users` becomes `\\server\Users`. **Any mapped drives or phone
  bookmarks pointing at the old path need updating.** The previous behaviour is still
  available in Step 5 if you prefer it.

### Added
- **Live permission display.** Every folder shows what Windows reports right now
  (`ON SERVER: Read Only (inherited)`), separately from your unsaved edits. A running
  count shows how many changes are pending, each changed row is marked `● UNSAVED`,
  and there are per-row "Undo my edits" and panel-wide "Refresh" buttons.
- **Verified saves.** Every permission write is read back off the folder afterwards
  and reported as confirmed or failed. `icacls` frequently exits successfully without
  changing anything — most often because the folder inherits its access from its
  parent — and that is now caught and explained instead of reported as success.
- **Activity box.** A running log along the bottom of the window. Every action writes
  each step as it happens, turning into a tick or a cross with the real console output
  attached. Failures name the step number and what went wrong. Copy / Save / Clear.
- **"See What People See".** Explains, in plain words, what your server looks like
  from a phone — duplicate shares, folders reachable two different ways, folders
  visible to people who cannot open them — and what to do about it.
- **Per-account visibility.** Untick any Windows account that is not a real user (the
  PC's own sign-in account, service accounts) and it stops appearing in the
  permissions list. Shared with the web dashboard.
- **Tailscale HTTPS, for real this time.** The 1.1.0 notes claimed the dashboard was
  routed through Tailscale Serve. No code ever did this, so the HTTPS address the app
  told you to visit never existed. It now runs `tailscale serve` as a tracked step,
  confirms it took effect, and shows you the actual address. Uses `serve` (private to
  your devices), never `funnel` (public internet).
- **Network Shares tab in the web dashboard**, plus a live permissions matrix showing
  every person against every folder.
- **View-only mode.** Declining the administrator prompt now offers a read-only
  session instead of the program silently closing. The header always shows which mode
  you are in, and actions that need administrator rights say so once, clearly, instead
  of failing eight times with "Access is denied".
- `compile.bat` now actually builds the program. It deletes the previous build first
  (PyInstaller does not - a failed build otherwise leaves the old exe in `dist\`,
  easy to mistake for the new one), checks Python and PyInstaller are present,
  verifies the script parses before the slow part, and stops with a readable reason
  instead of a Python traceback. If the previous exe cannot be deleted because it is
  still running, it says so.

### Fixed
- **Permissions were frequently read wrong.** The old reader matched a username
  anywhere in a line of `icacls` output, so a user named `media` matched the folder
  path `C:\NAS\Media`. Its patterns (`:.*F`, a bare `W`) matched almost any line with
  a capital letter in the path. Replaced with a real parser that matches accounts
  exactly and understands combined masks, Windows' expanded rights form, wrapped
  lines, inherited entries, and DENY rules.
- **Old shares pointing at the main folder could never be removed.** The cleanup
  matched `-like 'D:\Root\*'`, which never matches a share pointing at `D:\Root`
  itself — the most likely reason folders appeared twice on phones.
- **Publishing a share could delete an unrelated one.** A planned share whose name
  collided with an existing share elsewhere on the PC (for example a `Users` share
  pointing at `C:\Users`) would have removed it. It is now left alone and reported.
- **Stopping the dashboard could close unrelated programs.** It terminated every
  process whose command line contained `--headless`, which on a developer machine
  includes editor helpers. Now scoped to this program.
- **The dashboard could silently refuse to start.** Its single-instance check read
  `GetLastError()` through `ctypes.windll`, which shares one error slot across all
  calls and returns stale values. A spurious result made the server exit with no
  message. Port binding failures are now reported too.
- **Automatic elevation did nothing when run as a script.** It passed a relative
  script path with no working directory; elevated processes start in `System32`, so
  the file was never found. It also passed the built `.exe` its own path as an
  argument.
- **Declining the administrator prompt looked like a crash.** The result was ignored
  and the program exited regardless. It now distinguishes "you clicked No" from
  "your account cannot elevate" and says which.
- **Permission checkboxes could describe rules Windows cannot express.** Ticking
  "Delete" while "Read" was unticked silently applied Modify. Rights now follow each
  other in both directions.
- Folders nested under something other than `Users` are now managed. The scanner only
  ever descended into a folder literally named "Users", so `Resources\...` was missed.
- Built-in Windows accounts are identified by their SID rather than by name, so
  renamed or non-English accounts are still recognised.
- SnapRAID buttons no longer do nothing when another task is running — they say so.
- The once-per-session ownership step ran invisibly and reported "session sweep
  completed". It now appears in the Activity box in plain words, and reports failure.

### Changed
- Plain-language pass across the whole interface. "Headless Web Portal" is now
  "Manage From Your Phone", "Uncloak Folder" is "Let this person see the folder",
  and so on. Step numbering runs 1 to 6 without repeating — there were previously
  two different cards both labelled "Step 5".
- The "How to Access the Portal" instructions described an HTTPS address that did not
  exist. Rewritten to match what the program actually does.

## [1.1.1] - 2026-09-09
### Fixed
- Restored `apply_hosts_alias`, `install_tailscale` and `install_snapraid`, which had
  gone missing from the file and made those buttons raise an error.

## [1.1.0] - 2026-09-08
### Added
- **2-in-1 Headless Web Portal:** The executable can now deploy itself as a background Windows task (`--headless` flag) to host a lightweight HTML dashboard over an invisible local port.
- **Tailscale Serve Integration:** The background service automatically routes the dashboard through Tailscale, generating an official HTTPS URL for secure remote administration.
  *(Note: this was never actually implemented — see 2.0.0.)*
- **Basic Auth Security:** Added a password gate to the web portal to prevent unauthorized LAN or Tailnet users from accessing the administrative controls.
- **Granular Single-Folder Saves:** Users can now update permissions for a single folder instantly without having to recalculate the entire server matrix.
- **Simplified UI:** Folder security options have been redesigned into an expandable accordion layout with layman-friendly permission definitions (e.g., "Delete / Move Files").

## [1.0.1] - 2026-09-06
### Fixed
- Automated `takeown` execution. The program now silently reclaims local administrative ownership over all subfolders once per session before applying NTFS locks, resolving "Access is denied" errors for files uploaded or renamed remotely.

## [1.0.0] - 2026-09-06
### Added
- Initial release of Easy SMB Core.
- Frosted-glass GUI for Access-Based Enumeration (ABE) management.
- Tailscale automated setup and remote connection guides.
- SnapRAID integration with automated Task Scheduler routines.
- Custom error interception and plain-English troubleshooting UI.

[2.2.0]: https://github.com/alirisner14/EasySMB/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/alirisner14/EasySMB/compare/v2.0.1...v2.1.0
[2.0.1]: https://github.com/alirisner14/EasySMB/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/alirisner14/EasySMB/compare/v1.1.1...v2.0.0
[1.1.1]: https://github.com/alirisner14/EasySMB/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/alirisner14/EasySMB/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/alirisner14/EasySMB/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/alirisner14/EasySMB/releases/tag/v1.0.0
