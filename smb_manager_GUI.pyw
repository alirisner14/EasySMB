import base64
import ctypes
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
import socketserver
from tkinter import filedialog, messagebox, ttk


# =========================================================================
# SYSTEM & ADMIN HELPER FUNCTIONS
# =========================================================================
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_as_admin():
    if not is_admin():
        executable = sys.executable
        if executable.lower().endswith("python.exe"):
            executable = executable.replace("python.exe", "pythonw.exe")
            
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, " ".join(f'"{arg}"' for arg in sys.argv), None, 1
        )
        sys.exit(0)


def load_config():
    """Loads settings for the headless web server."""
    try:
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        with open(os.path.join(base_dir, "easynas_config.json"), "r") as f:
            return json.load(f)
    except:
        return {"root_dir": "C:\\", "web_pass": ""}


# =========================================================================
# HEADLESS WEB DASHBOARD SERVER (RUNS WHEN --headless IS PASSED)
# =========================================================================
class WebDashboardHandler(http.server.BaseHTTPRequestHandler):
    def check_auth(self):
        config = load_config()
        expected_pass = config.get("web_pass", "")
        if not expected_pass:
            return True  # If no password was set, allow access
            
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
        res = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-LocalUser | Where-Object { $_.Enabled -eq $True } | Select-Object -ExpandProperty Name"], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        users = [u.strip() for u in res.stdout.splitlines() if u.strip()]
        return [u for u in users if u.lower() not in ["administrator", "guest", "defaultaccount", "wdagutilityaccount", "family storage"] and not u.lower().startswith("defaultuser")]

    def get_folders(self):
        config = load_config()
        root_dir = config.get("root_dir", "")
        if not root_dir or not os.path.exists(root_dir): return []
        folders = [os.path.normpath(root_dir)]
        for item in os.listdir(root_dir):
            full = os.path.join(root_dir, item)
            if os.path.isdir(full):
                folders.append(full)
                if item.lower() == "users":
                    for u in os.listdir(full):
                        if os.path.isdir(os.path.join(full, u)): folders.append(os.path.join(full, u))
        return sorted(list({f.lower(): f for f in folders}.values()))

    def do_GET(self):
        if not self.check_auth():
            self.require_auth()
            return
            
        users = self.get_users()
        folders = self.get_folders()
        
        html = f"""
        <html><head><title>EasyNAS Dashboard</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          body {{ font-family: 'Segoe UI', sans-serif; background: #161719; color: #e5e7eb; padding: 20px; }}
          .card {{ background: #212327; padding: 20px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
          button {{ background: #4d79ff; color: white; border: none; padding: 12px 20px; border-radius: 8px; cursor: pointer; font-weight: bold; width: 100%; margin-bottom: 10px; font-size: 14px; }}
          button:hover {{ background: #3b5bdb; }}
          select, input {{ width: 100%; padding: 12px; margin-bottom: 15px; background: #141517; color: white; border: 1px solid #2c2f35; border-radius: 6px; font-size: 14px; }}
          h2, h3 {{ color: #ffffff; margin-top: 0; }}
        </style>
        </head><body>
        <h2>EasyNAS Remote Dashboard</h2>
        
        <div class="card">
          <h3>⛁ SnapRAID Control</h3>
          <form method="POST" action="/snapraid?cmd=status"><button type="submit">Check Array Status</button></form>
          <form method="POST" action="/snapraid?cmd=sync"><button type="submit" style="background:#34d399; color:#061a15;">Run Parity Sync</button></form>
          <form method="POST" action="/snapraid?cmd=smart"><button type="submit">Check SMART Hardware Health</button></form>
        </div>
        
        <div class="card">
          <h3>✦ Quick Folder Permissions</h3>
          <form method="POST" action="/perms">
             <label>Select User:</label>
             <select name="user">{"".join(f'<option value="{u}">{u}</option>' for u in users)}</select>
             <label>Select Folder:</label>
             <select name="folder">{"".join(f'<option value="{f}">{os.path.basename(f)}</option>' for f in folders)}</select>
             <label>Access Level:</label>
             <select name="level">
                <option value="RX">Read Only (Visible)</option>
                <option value="M">Modify (Read, Write, Delete)</option>
                <option value="F">Full Control</option>
                <option value="REMOVE">Revoke Access (Hide Folder)</option>
             </select>
             <button type="submit" style="background:#6366f1;">Apply Permission Target</button>
          </form>
        </div>
        </body></html>
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
        if parsed.path == "/snapraid":
            cmd = qs.get("cmd", [""])[0]
            if cmd in ["status", "sync", "smart"]:
                exe = r"C:\\SnapRAID\\snapraid.exe" if os.path.exists(r"C:\\SnapRAID\\snapraid.exe") else "snapraid"
                try:
                    res = subprocess.run([exe, cmd], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    output = res.stdout + res.stderr
                except Exception as e:
                    output = str(e)
                    
        elif parsed.path == "/perms":
            user = post_qs.get("user", [""])[0]
            folder = post_qs.get("folder", [""])[0]
            level = post_qs.get("level", [""])[0]
            if user and folder and level:
                try:
                    if level == "REMOVE":
                        cmd = f'icacls "{folder}" /remove "{user}"'
                    else:
                        cmd = f'icacls "{folder}" /grant:r "{user}":(OI)(CI){level} /T'
                    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    output = f"Applied {level} to {user} for folder {os.path.basename(folder)}\n\n" + res.stdout + res.stderr
                except Exception as e:
                    output = str(e)
                    
        html = f"""
        <html><head><title>Command Result</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>body {{ font-family: 'Segoe UI'; background: #161719; color: #e5e7eb; padding: 20px; }}
        pre {{ background: #101113; padding: 15px; border-radius: 8px; color: #34d399; overflow-x: auto; white-space: pre-wrap; }}
        a {{ color: #4d79ff; text-decoration: none; font-weight: bold; font-size: 16px; padding: 10px; background: #212327; border-radius: 6px; display: inline-block; margin-bottom: 20px; }}
        a:hover {{ background: #2c2f35; }}</style>
        </head><body>
        <a href="/">← Back to Dashboard</a>
        <h2>Output Log:</h2>
        <pre>{output}</pre>
        </body></html>
        """
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def run_headless_server():
    """Initializes the background web server and proxies it through Tailscale."""
    ts_process = subprocess.Popen(["tailscale", "serve", "localhost:5050"], creationflags=subprocess.CREATE_NO_WINDOW)
    handler = WebDashboardHandler
    with socketserver.TCPServer(("127.0.0.1", 5050), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            ts_process.terminate()


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
        self.root.title("Easy SMB & Private Storage Core (Administrator)")
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

        self.setup_window_backdrop()
        self.setup_ttk_styles()

        self.main_container = tk.Frame(self.root, bg=self.palette["bg_tint"])
        self.main_container.place(relx=0.5, rely=0.5, relwidth=0.96, relheight=0.96, anchor="center")

        self.header_frame = tk.Frame(self.main_container, bg=self.palette["bg_tint"])
        self.header_frame.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(self.header_frame, text="⛁ Easy SMB Core", bg=self.palette["bg_tint"], fg=self.palette["text_bright"], font=("Segoe UI", 16, "bold")).pack(side="left")

        self.notebook = ttk.Notebook(self.main_container)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(10, 4))

        self.tab_smb = tk.Frame(self.notebook, bg=self.palette["bg_tint"])
        self.tab_tailscale = tk.Frame(self.notebook, bg=self.palette["bg_tint"])
        self.tab_snapraid = tk.Frame(self.notebook, bg=self.palette["bg_tint"])
        self.tab_web = tk.Frame(self.notebook, bg=self.palette["bg_tint"])

        self.notebook.add(self.tab_smb, text="  ✦ User & Folder Setup  ")
        self.notebook.add(self.tab_tailscale, text="  ☁ Remote Access (Tailscale)  ")
        self.notebook.add(self.tab_snapraid, text="  ⛁ Backup & Recovery  ")
        self.notebook.add(self.tab_web, text="  🌐 Headless Web Portal  ")

        self.build_smb_vertical_workflow()
        self.build_tailscale_tab()
        self.build_snapraid_tab()
        self.build_web_tab()
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

        self.nav_btn_step1 = FrostedGlassButton(self.vert_nav_frame, text="1. Create Users", command=lambda: self.switch_vertical_tab(1), width=180, height=42, radius=16, color_scheme="nav_active")
        self.nav_btn_step1.pack(pady=4)
        self.nav_btn_step2 = FrostedGlassButton(self.vert_nav_frame, text="2. Folders & Security", command=lambda: self.switch_vertical_tab(2), width=180, height=42, radius=16, color_scheme="nav")
        self.nav_btn_step2.pack(pady=4)
        self.nav_btn_step3 = FrostedGlassButton(self.vert_nav_frame, text="3. Server Name", command=lambda: self.switch_vertical_tab(3), width=180, height=42, radius=16, color_scheme="nav")
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
        user_rim, user_card = self.create_glass_card(self.view_step1, title="Step 1: Create Local User Accounts")
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
        FrostedGlassButton(btn_row, text="+ Add User (Stay Here)", command=self.create_user_only, width=180, height=34, radius=16, color_scheme="neutral").pack(side="left", padx=(0, 8))
        FrostedGlassButton(btn_row, text="+ Add & Continue to Step 2", command=self.create_user_and_advance, width=220, height=34, radius=16, color_scheme="accent").pack(side="left")

        det_rim, det_card = self.create_glass_card(self.view_step1, title="Currently Active Accounts on this PC")
        det_rim.pack(fill="both", expand=True, pady=6)
        self.lbl_user_summary = tk.Label(det_card, text="Scanning for accounts...", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Consolas", 9), justify="left")
        self.lbl_user_summary.pack(anchor="w", pady=4)
        FrostedGlassButton(det_card, text="Skip to Step 2 →", command=lambda: self.switch_vertical_tab(2), width=180, height=32, radius=16, color_scheme="neutral").pack(anchor="w", pady=6)

    def build_vview_step2(self):
        root_rim, root_card = self.create_glass_card(self.view_step2, title="Step 2: Choose Main Server Folder")
        root_rim.pack(fill="x", pady=4)
        rgrid = tk.Frame(root_card, bg=self.palette["glass_card"])
        rgrid.pack(fill="x")
        tk.Label(rgrid, text="Select the main hard drive folder where all files will live (e.g., D:\\EasyNAS)", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        path_box = tk.Frame(rgrid, bg=self.palette["glass_card"])
        path_box.grid(row=1, column=0, sticky="ew", pady=4)
        _, self.ent_nas_root = self.create_glass_entry(path_box, width=54)
        self.ent_nas_root.master.pack(side="left", fill="x", expand=True, padx=(0, 8))
        FrostedGlassButton(path_box, text="Browse...", command=self.browse_nas_root, width=100, height=32, radius=14, color_scheme="neutral").pack(side="right")

        tmpl_rim, tmpl_card = self.create_glass_card(self.view_step2, title="Step 3: Auto-Build Folder Layout")
        tmpl_rim.pack(fill="x", pady=4)
        self.var_struct_mode = tk.StringVar(value="scan")
        tbox = tk.Frame(tmpl_card, bg=self.palette["glass_card"])
        tbox.pack(fill="x", pady=2)
        ttk.Radiobutton(tbox, text="I already have folders (Scan existing)", variable=self.var_struct_mode, value="scan", style="Glass.TRadiobutton").pack(anchor="w", pady=2)
        ttk.Radiobutton(tbox, text="Create private folders for each user", variable=self.var_struct_mode, value="multi_private", style="Glass.TRadiobutton").pack(anchor="w", pady=2)
        ttk.Radiobutton(tbox, text="Create private folders AND one public 'Shared' folder", variable=self.var_struct_mode, value="multi_shared", style="Glass.TRadiobutton").pack(anchor="w", pady=2)
        
        action_row = tk.Frame(tmpl_card, bg=self.palette["glass_card"])
        action_row.pack(fill="x", pady=(6, 2))
        FrostedGlassButton(action_row, text="Create / Scan Folders", command=self.apply_structure_template, width=200, height=34, radius=16, color_scheme="accent").pack(side="left", padx=(0, 10))

        matrix_rim, matrix_card = self.create_glass_card(self.view_step2, title="Step 4: Lock Folders & Set Permissions")
        matrix_rim.pack(fill="both", expand=True, pady=4)
        matrix_split = tk.Frame(matrix_card, bg=self.palette["glass_card"])
        matrix_split.pack(fill="both", expand=True, pady=2)
        left_user_box = tk.Frame(matrix_split, bg=self.palette["glass_card"], width=200)
        left_user_box.pack(side="left", fill="y", padx=(0, 10))
        tk.Label(left_user_box, text="1. Select a User:", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.user_listbox = tk.Listbox(left_user_box, bg=self.palette["well_bg"], fg=self.palette["text_bright"], selectbackground="#32353b", selectforeground="#ffffff", highlightthickness=1, highlightbackground=self.palette["well_border"], relief="flat", font=("Segoe UI", 10), height=7, exportselection=False)
        self.user_listbox.pack(fill="both", expand=True, pady=4)
        self.user_listbox.bind("<<ListboxSelect>>", self.on_user_selection_changed)

        right_perm_box = tk.Frame(matrix_split, bg=self.palette["glass_card"])
        right_perm_box.pack(side="right", fill="both", expand=True)
        self.lbl_perm_header = tk.Label(right_perm_box, text="2. Configure Subfolder Access:", bg=self.palette["glass_card"], fg=self.palette["text_glow"], font=("Segoe UI", 9, "bold"))
        self.lbl_perm_header.pack(anchor="w")

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

        apply_bar = tk.Frame(matrix_card, bg=self.palette["glass_card"])
        apply_bar.pack(fill="x", pady=(6, 2))
        FrostedGlassButton(apply_bar, text="⚡ Save All Changes & Publish Server", command=self.apply_all_configured_permissions, width=320, height=38, radius=18, color_scheme="accent").pack(side="right", padx=(0, 10))

    def build_vview_step3(self):
        dom_rim, dom_card = self.create_glass_card(self.view_step3, title="Step 5: Memorable Server Name")
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
        FrostedGlassButton(abox, text="Save Friendly Name", command=self.apply_hosts_alias, width=180, height=34, radius=16, color_scheme="accent").pack(side="left")

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

        card_rim, card = self.create_glass_card(panel, title="Install Remote Access (No Router Config Needed)")
        card_rim.pack(fill="x", pady=(0, 6))
        tk.Label(card, text="Tailscale safely connects devices to this server from anywhere in the world using an encrypted tunnel.\nIt bypasses your router settings automatically so you don't have to deal with port-forwarding.", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(0, 10))

        btn_row = tk.Frame(card, bg=self.palette["glass_card"])
        btn_row.pack(fill="x", pady=4)
        FrostedGlassButton(btn_row, text="1. Download & Install Tailscale", command=self.install_tailscale, width=220, height=36, radius=18, color_scheme="neutral").pack(side="left", padx=(0, 12))

        def open_tailscale_console():
            ts_path = r"C:\Program Files\Tailscale\tailscale.exe"
            if os.path.exists(ts_path):
                self.run_cmd_thread([ts_path, "up"], "Tailscale engine connected.")
                webbrowser.open("https://login.tailscale.com/admin/machines")
            else:
                self.set_status("Tailscale not found. Please install it first.", "error")

        FrostedGlassButton(btn_row, text="2. Open Tailscale Login/Dashboard", command=open_tailscale_console, width=260, height=36, radius=18, color_scheme="accent").pack(side="left")

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

        card_rim, card = self.create_glass_card(panel, title="Hard Drive Parity Backup (SnapRAID)")
        card_rim.pack(fill="x", pady=(0, 6))

        tk.Label(card, text="SnapRAID calculates backup math across independent hard drives to protect against disk failure.\nUse this dashboard to run manual health checks or schedule automatic background protection.", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(0, 8))

        btn_grid = tk.Frame(card, bg=self.palette["glass_card"])
        btn_grid.pack(fill="x", pady=4)

        FrostedGlassButton(btn_grid, text="1. Install SnapRAID", command=self.install_snapraid, width=180, height=36, radius=16, color_scheme="neutral").grid(row=0, column=0, padx=(0, 8), pady=4)
        FrostedGlassButton(btn_grid, text="2. Update Parity Backup (Sync)", command=self.snapraid_sync, width=220, height=36, radius=16, color_scheme="accent").grid(row=0, column=1, padx=8, pady=4)
        FrostedGlassButton(btn_grid, text="3. Automate Nightly Backups", command=self.schedule_snapraid_tasks, width=220, height=36, radius=16, color_scheme="nav_active").grid(row=0, column=2, padx=8, pady=4)
        
        FrostedGlassButton(btn_grid, text="Check File Health (Status)", command=self.snapraid_status, width=180, height=36, radius=16, color_scheme="nav").grid(row=1, column=0, padx=(0, 8), pady=4)
        FrostedGlassButton(btn_grid, text="Check Hard Drive Health (SMART)", command=self.snapraid_smart, width=220, height=36, radius=16, color_scheme="nav").grid(row=1, column=1, padx=8, pady=4)
        FrostedGlassButton(btn_grid, text="Recover Lost Data (Fix)", command=self.snapraid_fix, width=220, height=36, radius=16, color_scheme="danger").grid(row=1, column=2, padx=8, pady=4)

        guide_rim, guide_frame = self.create_glass_card(panel, title="How to Setup Parity Protection")
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

        card_rim, card = self.create_glass_card(panel, title="2-in-1 Headless Web Dashboard")
        card_rim.pack(fill="x", pady=(0, 6))

        desc = (
            "Transform this program into a standalone background service. Once deployed, it runs a lightweight web\n"
            "dashboard securely over Tailscale. You can check SnapRAID and set folder permissions remotely from any\n"
            "browser in your Tailscale network without needing to Remote Desktop into this server."
        )
        tk.Label(card, text=desc, bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(0, 12))

        tk.Label(card, text="Set Dashboard Password (to keep non-admins out):", bg=self.palette["glass_card"], fg=self.palette["text_muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
        _, self.ent_web_pass = self.create_glass_entry(card, width=40, show="*")
        self.ent_web_pass.master.pack(anchor="w", pady=(2, 10))

        btn_row = tk.Frame(card, bg=self.palette["glass_card"])
        btn_row.pack(fill="x", pady=4)
        
        FrostedGlassButton(btn_row, text="Deploy Background Web Service", command=self.deploy_headless_service, width=280, height=38, radius=18, color_scheme="accent").pack(side="left", padx=(0, 12))
        FrostedGlassButton(btn_row, text="Stop & Remove Service", command=self.remove_headless_service, width=220, height=38, radius=18, color_scheme="danger").pack(side="left")

        guide_rim, guide_frame = self.create_glass_card(panel, title="How to Access the Portal")
        guide_rim.pack(fill="both", expand=True, pady=(6, 0))
        txt = tk.Text(guide_frame, bg=self.palette["well_bg"], fg=self.palette["text_frost"], font=("Consolas", 9), wrap="word", relief="flat", padx=12, pady=12)
        txt.insert("1.0", "HOW TO USE THE WEB PORTAL\n\n"
                          "1. Set a password and click 'Deploy Background Web Service' above.\n"
                          "2. On your phone or laptop, ensure you are connected to Tailscale.\n"
                          "3. Open your web browser and go to your server's Tailscale HTTPS address.\n"
                          "   (e.g., https://easynas.your-tailnet.ts.net)\n\n"
                          "SECURITY NOTE:\n"
                          "Your browser will immediately pop up a login prompt. Type 'admin' as the username, and the password you set above. Because this is routed exclusively through Tailscale, the connection remains fully encrypted and invisible to the public internet.")
        txt.config(state="disabled")
        
        scroll = ttk.Scrollbar(guide_frame, orient="vertical", command=txt.yview, style="Vertical.TScrollbar")
        txt.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

    def deploy_headless_service(self):
        web_pass = self.ent_web_pass.get().strip()
        root_dir = self.ent_nas_root.get().strip()
        if not root_dir:
            messagebox.showerror("Error", "Please select the Main Server Folder in the SMB tab first so the web server knows where your files live.")
            return
        if not web_pass:
            messagebox.showerror("Error", "Please enter a password to secure the dashboard.")
            return
            
        config = {"web_pass": web_pass, "root_dir": root_dir}
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        with open(os.path.join(base_dir, "easynas_config.json"), "w") as f:
            json.dump(config, f)
            
        exe_path = os.path.abspath(sys.argv[0])
        if exe_path.endswith(".py") or exe_path.endswith(".pyw"):
            cmd = f'"{sys.executable}" "{exe_path}" --headless'
        else:
            cmd = f'"{exe_path}" --headless'
            
        task_cmd = ['schtasks', '/create', '/tn', 'EasyNAS_WebDashboard', '/tr', cmd, '/sc', 'onlogon', '/rl', 'highest', '/f']
        res = subprocess.run(task_cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        
        if res.returncode == 0:
            subprocess.run(['schtasks', '/run', '/tn', 'EasyNAS_WebDashboard'], creationflags=subprocess.CREATE_NO_WINDOW)
            self.set_status("Success! Headless web service deployed and running.", "success")
            self.ent_web_pass.delete(0, tk.END)
        else:
            self.set_status("Failed to create background task. (Click for details)", "error", raw_output=res.stderr.decode())

    def remove_headless_service(self):
        subprocess.run(['schtasks', '/end', '/tn', 'EasyNAS_WebDashboard'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        res = subprocess.run(['schtasks', '/delete', '/tn', 'EasyNAS_WebDashboard', '/f'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if res.returncode == 0:
            self.set_status("Success! Headless service stopped and removed.", "success")
        else:
            self.set_status("Could not remove task or it does not exist.", "info")

    # =========================================================================
    # SNAPRAID COMMAND HANDLERS
    # =========================================================================
    def run_snapraid_cmd(self, cmd_arg, title):
        exe_path = r"C:\SnapRAID\snapraid.exe"
        if not os.path.exists(exe_path):
            exe_path = "snapraid"
            
        self.set_status(f"Running SnapRAID {cmd_arg}...", "info")
        def process():
            try:
                self.current_process = subprocess.Popen(
                    [exe_path, cmd_arg], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, creationflags=subprocess.CREATE_NO_WINDOW
                )
                stdout, _ = self.current_process.communicate()
                rc = self.current_process.returncode
                
                if rc == 0:
                    self.set_status(f"Success! SnapRAID {cmd_arg} completed.", "success", raw_output=stdout, title=f"SnapRAID {title}")
                else:
                    self.set_status(f"SnapRAID {cmd_arg} finished with warnings/errors. (Click for details)", "error", raw_output=stdout, title=f"SnapRAID {title}")
            except Exception as e:
                self.set_status(f"Failed to execute SnapRAID {cmd_arg}.", "error", raw_output=str(e), title="Execution Error")
            finally:
                self.current_process = None
                self.is_processing = False

        self.is_processing = True
        threading.Thread(target=process, daemon=True).start()

    def snapraid_sync(self):
        if getattr(self, "is_processing", False): return
        self.run_snapraid_cmd("sync", "Parity Sync")

    def snapraid_status(self):
        if getattr(self, "is_processing", False): return
        self.run_snapraid_cmd("status", "Array Status")

    def snapraid_smart(self):
        if getattr(self, "is_processing", False): return
        self.run_snapraid_cmd("smart", "SMART Hardware Health")

    def snapraid_fix(self):
        if getattr(self, "is_processing", False): return
        confirm = messagebox.askyesno("Confirm Disaster Recovery", "WARNING: You are about to initiate a drive fix/rebuild.\n\nSnapRAID will attempt to reconstruct any missing or corrupted files based on your parity data.\n\nAre you sure you want to proceed?")
        if confirm:
            self.run_snapraid_cmd("fix", "Disaster Recovery (Fix)")

    def schedule_snapraid_tasks(self):
        self.set_status("Configuring elevated Task Scheduler routines...", "info")
        exe_path = r"C:\SnapRAID\snapraid.exe"
        
        def process():
            try:
                sync_cmd = ['schtasks', '/create', '/tn', 'SnapRAID_Daily_Sync', '/tr', f'{exe_path} sync', '/sc', 'daily', '/st', '02:00', '/rl', 'highest', '/f']
                subprocess.run(sync_cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                
                scrub_cmd = ['schtasks', '/create', '/tn', 'SnapRAID_Weekly_Scrub', '/tr', f'{exe_path} scrub', '/sc', 'weekly', '/d', 'SUN', '/st', '04:00', '/rl', 'highest', '/f']
                subprocess.run(scrub_cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                
                self.set_status("Success! Auto Sync & Scrub scheduled via Windows Tasks.", "success")
            except Exception as e:
                self.set_status("Error scheduling tasks. (Click for details)", "error", raw_output=str(e), title="Task Scheduler Error")
                
        threading.Thread(target=process, daemon=True).start()

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
        FrostedGlassButton(status_card, text="🛑 Stop current task", command=self.stop_current_operation, width=150, height=34, radius=16, color_scheme="danger").pack(side="right")

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
    def run_cmd(self, command_list, success_msg=""):
        try:
            self.current_process = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            stdout, stderr = self.current_process.communicate()
            if self.current_process.returncode == 0:
                self.set_status(success_msg, "success")
                return True
            else:
                self.set_status("Error Occurred! (Click for details)", "error", raw_output=stderr.strip() or stdout.strip(), title="Command Failed")
                return False
        except Exception as e:
            self.set_status("Error Occurred! (Click for details)", "error", raw_output=str(e), title="Execution Error")
            return False
        finally:
            self.current_process = None

    def run_quiet_cmd(self, cmd, use_shell=False):
        if not self.is_processing: return False
        try:
            self.current_process = subprocess.Popen(cmd, shell=use_shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            stdout, stderr = self.current_process.communicate()
            if self.current_process.returncode != 0:
                self.set_status("Error Occurred! (Click for details)", "error", raw_output=stderr.strip() or stdout.strip(), title="Background Task Failed")
                self.is_processing = False
                return False
            return True
        except Exception as e:
            self.set_status("Error Occurred! (Click for details)", "error", raw_output=str(e), title="Execution Error")
            self.is_processing = False
            return False
        finally:
            self.current_process = None

    def run_cmd_thread(self, command_list, success_msg=""):
        threading.Thread(target=self.run_cmd, args=(command_list, success_msg), daemon=True).start()

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
        try:
            takeown_cmd = f'takeown /F "{root_dir}" /R /D Y'
            subprocess.run(takeown_cmd, shell=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.set_status("First-time session sweep completed. Folder ownership optimized.", "success")
        except Exception as e:
            self.set_status("Background sweep failed. (Click for details)", "error", raw_output=str(e), title="Sweep Error")

    def refresh_system_users(self):
        def fetch():
            ps_cmd = "Get-LocalUser | Where-Object { $_.Enabled -eq $True } | Select-Object -ExpandProperty Name"
            res = subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if res.returncode == 0:
                raw_users = [u.strip() for u in res.stdout.splitlines() if u.strip()]
                filtered = [u for u in raw_users if u.lower() not in ["administrator", "guest", "defaultaccount", "wdagutilityaccount", "family storage"] and not u.lower().startswith("defaultuser")]
                self.discovered_users = filtered
                def update_ui():
                    self.lbl_user_summary.config(text=f"Detected Active Accounts ({len(filtered)}):\n" + ", ".join(filtered))
                    curr_sel = self.user_listbox.curselection()
                    self.user_listbox.delete(0, tk.END)
                    for user in self.discovered_users: self.user_listbox.insert(tk.END, user)
                    if self.discovered_users:
                        self.user_listbox.selection_set(curr_sel[0] if curr_sel and curr_sel[0] < len(self.discovered_users) else 0)
                        self.on_user_selection_changed()
                self.root.after(0, update_ui)
        threading.Thread(target=fetch, daemon=True).start()

    def create_user_only(self): self._exec_create_user(advance_tabs=False)
    def create_user_and_advance(self): self._exec_create_user(advance_tabs=True)

    def _exec_create_user(self, advance_tabs=False):
        username, password = self.ent_v_user.get().strip(), self.ent_v_pass.get().strip()
        if not username or not password:
            messagebox.showerror("Input Error", "Please enter both Username and Password.")
            return
        self.set_status(f"Creating user '{username}'...", "info")
        def process():
            if self.run_cmd(["net", "user", username, password, "/add", "/expires:never"], f"Success! User '{username}' created."):
                self.root.after(0, lambda: self.ent_v_user.delete(0, tk.END))
                self.root.after(0, lambda: self.ent_v_pass.delete(0, tk.END))
                self.refresh_system_users()
                if advance_tabs: self.root.after(0, lambda: self.switch_vertical_tab(2))
        threading.Thread(target=process, daemon=True).start()

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
        try:
            os.makedirs(root_dir, exist_ok=True)
            if mode == "multi_private":
                users_dir = os.path.join(root_dir, "Users")
                os.makedirs(users_dir, exist_ok=True)
                for user in self.discovered_users: os.makedirs(os.path.join(users_dir, user), exist_ok=True)
                self.set_status(f"Created Private Structure under {users_dir}", "success")
            elif mode == "multi_shared":
                shared_dir, users_dir = os.path.join(root_dir, "Shared"), os.path.join(root_dir, "Users")
                os.makedirs(shared_dir, exist_ok=True)
                os.makedirs(users_dir, exist_ok=True)
                for user in self.discovered_users: os.makedirs(os.path.join(users_dir, user), exist_ok=True)
                self.set_status(f"Created Shared & Private Structure under {root_dir}", "success")
            elif mode == "single_user":
                self.set_status(f"Configured Single User root: {root_dir}", "success")
            self.scan_or_populate_folders()
        except Exception as e:
            self.set_status("Error Occurred! (Click for details)", "error", raw_output=str(e), title="Directory Creation Error")

    def scan_or_populate_folders(self):
        root_dir = self.ent_nas_root.get().strip()
        if not root_dir or not os.path.exists(root_dir): return
        
        if not self.takeown_completed:
            self.takeown_completed = True
            self.set_status("Reclaiming administrative ownership (First-time session sweep)...", "info")
            threading.Thread(target=self._background_takeown, args=(root_dir,), daemon=True).start()

        folders = []
        try:
            root_norm = os.path.normpath(root_dir)
            folders.append(root_norm)
            for item in os.listdir(root_norm):
                full = os.path.join(root_norm, item)
                if os.path.isdir(full):
                    folders.append(full)
                    if item.lower() == "users":
                        for u_item in os.listdir(full):
                            if os.path.isdir(os.path.join(full, u_item)): folders.append(os.path.join(full, u_item))
        except Exception as e:
            self.set_status("Error Occurred! (Click for details)", "error", raw_output=str(e), title="Directory Scan Error")

        self.active_subfolders = sorted(list({f.lower(): f for f in folders}.values()))
        self.rebuild_permissions_ui()

    def rebuild_permissions_ui(self):
        for widget in self.folder_inner_frame.winfo_children(): widget.destroy()
        sel = self.user_listbox.curselection()
        if not sel or not self.discovered_users:
            tk.Label(self.folder_inner_frame, text="No user selected or accounts available.", bg=self.palette["well_bg"], fg=self.palette["text_muted"], font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=10)
            return

        selected_user = self.discovered_users[sel[0]]
        self.lbl_perm_header.config(text=f"Folder Access for User: '{selected_user}'")
        if selected_user not in self.user_folder_permissions: self.user_folder_permissions[selected_user] = {}

        for fpath in self.active_subfolders:
            rel_name = os.path.basename(fpath) or fpath
            is_own_home = (rel_name.lower() == selected_user.lower())

            if fpath not in self.user_folder_permissions[selected_user]:
                self.user_folder_permissions[selected_user][fpath] = {
                    "enabled": tk.BooleanVar(value=is_own_home),
                    "full": tk.BooleanVar(value=is_own_home),
                    "read": tk.BooleanVar(value=False),
                    "create": tk.BooleanVar(value=False),
                    "delete": tk.BooleanVar(value=False),
                }

            state = self.user_folder_permissions[selected_user][fpath]
            row_f = tk.Frame(self.folder_inner_frame, bg=self.palette["well_bg"])
            row_f.pack(fill="x", padx=8, pady=3)
            
            def make_toggle(frame):
                def _toggle():
                    if frame.winfo_ismapped(): frame.pack_forget()
                    else: frame.pack(fill="x", pady=(0, 6))
                    self.folder_scroll_canvas.configure(scrollregion=self.folder_scroll_canvas.bbox("all"))
                return _toggle

            perm_f = tk.Frame(row_f, bg=self.palette["glass_rim_shadow"], padx=24, pady=6)
            btn_header = tk.Button(row_f, text=f"▶ {fpath}", bg=self.palette["glass_card"], fg=self.palette["text_bright"], font=("Segoe UI", 9, "bold"), relief="flat", anchor="w", padx=10, command=make_toggle(perm_f))
            btn_header.pack(fill="x")
            
            top_grid = tk.Frame(perm_f, bg=self.palette["glass_rim_shadow"])
            top_grid.pack(fill="x", pady=(4, 8))
            ttk.Checkbutton(top_grid, text="Visible to User (Uncloak Folder)", variable=state["enabled"], style="Glass.TCheckbutton").pack(anchor="w", pady=2)
            ttk.Checkbutton(top_grid, text="FULL ACCESS (Read, Write, & Delete)", variable=state["full"], style="Glass.TCheckbutton").pack(anchor="w", pady=2)
            
            tk.Frame(perm_f, bg=self.palette["glass_rim_light"], height=1).pack(fill="x", pady=4)
            
            btm_grid = tk.Frame(perm_f, bg=self.palette["glass_rim_shadow"])
            btm_grid.pack(fill="x", pady=(4, 8))
            ttk.Checkbutton(btm_grid, text="Read / View Files", variable=state["read"], style="Dark.TCheckbutton").grid(row=0, column=0, sticky="w", padx=(0,20), pady=4)
            ttk.Checkbutton(btm_grid, text="Add / Create Sub-Folders", variable=state["create"], style="Dark.TCheckbutton").grid(row=0, column=1, sticky="w", padx=(0,20), pady=4)
            ttk.Checkbutton(btm_grid, text="Delete / Move Files", variable=state["delete"], style="Dark.TCheckbutton").grid(row=0, column=2, sticky="w", padx=(0,20), pady=4)
            
            save_btn_frame = tk.Frame(perm_f, bg=self.palette["glass_rim_shadow"])
            save_btn_frame.pack(fill="x", pady=4)
            FrostedGlassButton(save_btn_frame, text="💾 Save This Folder Only", command=lambda u=selected_user, f=fpath, s=state: self.apply_single_folder_permissions(u, f, s), width=200, height=32, radius=14, color_scheme="neutral").pack(side="right")

    def on_user_selection_changed(self, event=None):
        self.rebuild_permissions_ui()

    def apply_single_folder_permissions(self, user, fpath, state):
        if getattr(self, "is_processing", False):
            self.set_status("Process already running.", "error", raw_output="Task blocked due to concurrency lock.", title="Concurrency Lock")
            return
            
        self.is_processing = True
        
        def process():
            try:
                self.set_status(f"Updating permissions for {user} -> {os.path.basename(fpath)}...", "info")
                if not state["enabled"].get():
                    self.run_quiet_cmd(f'icacls "{fpath}" /remove "{user}"', use_shell=True)
                else:
                    if state["full"].get(): ntfs_perm = "F"
                    elif state["delete"].get(): ntfs_perm = "M"
                    elif state["create"].get(): ntfs_perm = "(RX,W)"
                    elif state["read"].get(): ntfs_perm = "RX"
                    else: ntfs_perm = "RX"
                    
                    self.run_quiet_cmd(f'icacls "{fpath}" /grant:r "{user}":(OI)(CI){ntfs_perm} /T', use_shell=True)
                    
                if self.is_processing: 
                    self.set_status(f"Success! Saved folder rules for {user}.", "success")
            finally:
                self.is_processing = False
                
        threading.Thread(target=process, daemon=True).start()

    def apply_all_configured_permissions(self):
        if getattr(self, "is_processing", False):
            self.set_status("Process already running. Please wait or press Stop.", "error", raw_output="Task blocked due to concurrency lock.", title="Concurrency Lock")
            return

        root_dir = self.ent_nas_root.get().strip()
        if not root_dir or not os.path.exists(root_dir):
            messagebox.showerror("Error", "NAS root directory does not exist.")
            return
            
        self.is_processing = True

        def process():
            try:
                master_share = os.path.basename(root_dir.rstrip("\\/"))
                if not master_share or (len(master_share) == 2 and master_share[1] == ':'): master_share = "RootNAS"

                self.set_status(f"Configuring Master Share: '{master_share}' with ABE...", "info")
                ps_cleanup = f"Get-SmbShare | Where-Object {{ $_.Path -like '{root_dir}\\*' -and $_.Name -ne '{master_share}' }} | Remove-SmbShare -Force"
                if not self.run_quiet_cmd(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cleanup], use_shell=False): return
                
                ps_master = f"""
                if (Get-SmbShare -Name '{master_share}' -ErrorAction SilentlyContinue) {{
                    Set-SmbShare -Name '{master_share}' -FolderEnumerationMode AccessBased -Force
                    Grant-SmbShareAccess -Name '{master_share}' -AccountName 'Authenticated Users' -AccessRight Change -Force
                }} else {{
                    New-SmbShare -Name '{master_share}' -Path '{root_dir}' -ChangeAccess 'Authenticated Users' -FolderEnumerationMode AccessBased
                }}
                """
                if not self.run_quiet_cmd(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_master], use_shell=False): return
                if not self.run_quiet_cmd(f'icacls "{root_dir}" /grant "Authenticated Users":(RX)', use_shell=True): return

                for fpath in self.active_subfolders:
                    if not self.is_processing: return
                    self.run_quiet_cmd(f'icacls "{fpath}" /inheritance:r', use_shell=True)
                    self.run_quiet_cmd(f'icacls "{fpath}" /grant:r "Administrators":(OI)(CI)F', use_shell=True)

                for user, fmap in self.user_folder_permissions.items():
                    for fpath, state in fmap.items():
                        if not self.is_processing: return 
                        if not state["enabled"].get():
                            self.run_quiet_cmd(f'icacls "{fpath}" /remove "{user}"', use_shell=True)
                        else:
                            if state["full"].get(): ntfs_perm = "F"
                            elif state["delete"].get(): ntfs_perm = "M"
                            elif state["create"].get(): ntfs_perm = "(RX,W)"
                            elif state["read"].get(): ntfs_perm = "RX"
                            else: ntfs_perm = "RX"
                            
                            self.set_status(f"Applying permissions for {user} -> {os.path.basename(fpath)}...", "info")
                            self.run_quiet_cmd(f'icacls "{fpath}" /grant:r "{user}":(OI)(CI){ntfs_perm} /T', use_shell=True)

                if self.is_processing: self.set_status(f"Success! Saved all changes & published Master Share.", "success")
            finally:
                self.is_processing = False

        threading.Thread(target=process, daemon=True).start()

    def apply_hosts_alias(self):
        alias = self.ent_domain_alias.get().strip()
        if not alias: return messagebox.showerror("Error", "Please enter a valid alias.")
        alias_clean = re.sub(r"[^a-zA-Z0-9\.\-_]", "", alias)
        hosts_path = r"C:\Windows\System32\drivers\etc\hosts"

        try:
            with open(hosts_path, "r") as f: content = f.read()
            if alias_clean in content: self.set_status(f"Alias '{alias_clean}' already exists in hosts file.", "info")
            else:
                with open(hosts_path, "a") as f: f.write(f"\n127.0.0.1\t{alias_clean}\n")
                self.set_status(f"Success! Added alias '{alias_clean}' -> 127.0.0.1 in hosts file.", "success")
        except Exception as e:
            self.set_status("Error Occurred! (Click for details)", "error", raw_output=str(e), title="Hosts File Edit Error")

    def install_tailscale(self):
        cmd = ["winget", "install", "-e", "--id", "Tailscale.Tailscale", "--silent", "--accept-package-agreements", "--accept-source-agreements"]
        self.set_status("Fetching Tailscale via Windows Package Manager...", "info")
        self.run_cmd_thread(cmd, "Success! Tailscale installed.")

    def install_snapraid(self):
        cmd = ["winget", "install", "-e", "--id", "SnapRAID.SnapRAID", "--silent", "--accept-package-agreements", "--accept-source-agreements"]
        self.set_status("Fetching SnapRAID via Windows Package Manager...", "info")
        self.run_cmd_thread(cmd, "Success! SnapRAID installed.")


if __name__ == "__main__":
    if "--headless" in sys.argv:
        run_headless_server()
    else:
        if not is_admin():
            run_as_admin()
        else:
            root = tk.Tk()
            app = GlassSMBManagerApp(root)
            root.mainloop()