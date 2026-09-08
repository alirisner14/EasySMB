# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-09-08
### Added
- **2-in-1 Headless Web Portal:** The executable can now deploy itself as a background Windows task (`--headless` flag) to host a lightweight HTML dashboard over an invisible local port.
- **Tailscale Serve Integration:** The background service automatically routes the dashboard through Tailscale, generating an official HTTPS URL for secure remote administration.
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