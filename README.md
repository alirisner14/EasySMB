# Easy SMB Core

A lightweight, user-friendly Windows GUI designed to make deploying a secure, private home server or NAS incredibly simple. Built with a sleek, frosted-glass aesthetic, this standalone executable replaces complex terminal commands with intuitive button clicks and includes a built-in headless web portal for remote administration.

## Key Features

* **Visual Security Manager (Access-Based Enumeration):** Checkboxes automatically lock down user folders and apply ABE. Users connecting to the master share will magically only see the folders they have permission to access.
* **2-in-1 Headless Web Portal:** Deploy the app as a silent background service. It utilizes Tailscale Serve to broadcast a secure, password-protected web dashboard so you can manage permissions and run parity checks from your phone, anywhere in the world.
* **One-Click User Creation:** Generate standard local Windows credentials dedicated strictly to network access.
* **Tailscale Integration:** One-click engine installation and in-app remote access setup. Securely map your drives without touching your router's port-forwarding settings. 
* **Interactive SnapRAID Dashboard:** An interactive UI to install SnapRAID, run health checks, initiate disaster recovery operations, and silently automate nightly parity backups via Windows Task Scheduler.
* **Smart Output Interception:** If a system command fails, the UI pops up a clean terminal window detailing the raw system logs and offering plain-English troubleshooting.

## Getting Started

1. Download the latest `smb_manager_GUI.exe` from the **Releases** section of this repository.
2. Place the executable in your desired permanent folder (alongside your `app_icon.ico` logo for custom branding).
3. Right-click the `.exe` and select **Run as Administrator** (required for assigning folder permissions, editing the hosts file, and scheduling tasks).
4. Follow the numbered tabs across the top to build your local server, set up remote access, and configure your parity protection.

## Running from Source

1. Clone this repository to your local machine.
2. Ensure Python 3.x is installed.
3. Run the script:
   ```cmd
   python smb_manager_GUI.pyw

## Compiling your own standalone .EXE
pip install pyinstaller
python -m PyInstaller --noconsole --onefile --uac-admin --icon=app_icon.ico smb_manager_GUI.pyw