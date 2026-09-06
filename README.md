# Easy SMB Core

A lightweight, layman-friendly Windows GUI designed to make deploying a secure, private home server or NAS incredibly simple. Built with a sleek, frosted-glass aesthetic, this standalone executable replaces complex terminal commands with intuitive button clicks.

## Key Features

* **Visual Security Manager (Access-Based Enumeration):** Checkboxes automatically lock down user folders and apply ABE. Users connecting to the master share will magically only see the folders they have permission to access.
* **One-Click User Creation:** Generate standard local Windows credentials dedicated strictly to network access.
* **Friendly Device Mapping:** Edit the Windows `hosts` file directly from the app to assign memorable network names (e.g., `FamilyNAS`).
* **Tailscale Integration:** One-click engine installation and in-app remote access setup. Securely map your drives anywhere in the world without touching your router's port-forwarding settings.
* **Interactive SnapRAID Dashboard:** Ditch the command line. An interactive UI to install SnapRAID, run health checks, initiate disaster recovery operations, and silently automate nightly parity backups via Windows Task Scheduler.
* **Smart Output Interception:** If a system command fails, the UI pops up a clean, frosted-glass terminal window detailing the raw system logs and offering plain-English troubleshooting.

## Getting Started

1. Download the latest `smb_manager_GUI.exe` from the **Releases** section of this repository.
2. Ensure you have your `app_icon.ico` logo in the same folder if you want the custom taskbar branding.
3. Right-click the `.exe` and select **Run as Administrator**.
4. Follow the numbered tabs across the top to build your local server, set up remote access, and configure your parity protection.

## Running from Source

1. Clone this repository to your local machine.
2. Ensure Python 3.x is installed.
3. Run the script:
   ```cmd
   python smb_manager_GUI.pyw