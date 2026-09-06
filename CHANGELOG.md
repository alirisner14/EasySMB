# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-09-06
### Added
- Automated `takeown` execution. The program now silently reclaims local administrative ownership over all subfolders before applying NTFS locks. This completely resolves the "Access is denied" error when assigning permissions to files uploaded or renamed remotely.

## [1.0.0] - 2026-09-06
### Added
- Initial release of Easy SMB Core.
- Frosted-glass GUI for Access-Based Enumeration (ABE) management.
- Tailscale automated setup and remote connection guides.
- SnapRAID integration with automated Task Scheduler routines.
- Custom error interception and plain-English troubleshooting UI.