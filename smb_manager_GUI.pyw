import ctypes
import os
import re
import socket
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


def is_admin():
    """Check if the script is currently running with administrative rights."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_as_admin():
    """Re-launch the current script with elevated Administrator privileges."""
    if not is_admin():
        executable = sys.executable
        if executable.lower().endswith("python.exe"):
            executable = executable.replace("python.exe", "pythonw.exe")
            
        ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            " ".join(f'"{arg}"' for arg in sys.argv),
            None,
            1,
        )
        sys.exit(0)


class FrostedGlassButton(tk.Canvas):
    """Skeuomorphic button with rounded corners and liquid specular sheen."""

    def __init__(
        self,
        parent,
        text,
        command,
        width=180,
        height=38,
        radius=18,
        color_scheme="neutral",
        **kwargs,
    ):
        super().__init__(
            parent,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=parent["bg"],
            **kwargs,
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
            self.colors = {
                "base": "#2b384e",
                "hover": "#364763",
                "press": "#202b3d",
                "rim_light": "#6a88b5",
                "rim_dark": "#161e2b",
                "text": "#ffffff",
                "glow": "#4d79ff",
            }
        elif self.color_scheme == "danger":
            self.colors = {
                "base": "#4a2424",
                "hover": "#5e2e2e",
                "press": "#331919",
                "rim_light": "#944d4d",
                "rim_dark": "#1f0f0f",
                "text": "#ffcccc",
                "glow": "#ff6666",
            }
        elif self.color_scheme == "nav":
            self.colors = {
                "base": "#1a1b1e",
                "hover": "#25272c",
                "press": "#141517",
                "rim_light": "#36383f",
                "rim_dark": "#0e0f11",
                "text": "#9ca3af",
                "glow": "#4a4d55",
            }
        elif self.color_scheme == "nav_active":
            self.colors = {
                "base": "#282a2f",
                "hover": "#32353b",
                "press": "#1e2023",
                "rim_light": "#5c6370",
                "rim_dark": "#141517",
                "text": "#ffffff",
                "glow": "#828997",
            }
        else:  # Neutral Frosted
            self.colors = {
                "base": "#282a2e",
                "hover": "#33363b",
                "press": "#1e2023",
                "rim_light": "#4a4d53",
                "rim_dark": "#141517",
                "text": "#e5e7eb",
                "glow": "#9ca3af",
            }

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

        self.redraw()

    def _draw_rounded_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def redraw(self):
        self.delete("all")
        w, h, r = self.w, self.h, self.r

        self._draw_rounded_rect(1, 2, w - 1, h, r, fill=self.colors["rim_dark"])
        top_rim_color = self.colors["glow"] if self.is_hovered else self.colors["rim_light"]
        self._draw_rounded_rect(1, 1, w - 1, h - 1, r, fill=top_rim_color)

        fill_col = self.colors["base"]
        if self.is_pressed:
            fill_col = self.colors["press"]
        elif self.is_hovered:
            fill_col = self.colors["hover"]

        offset = 2 if self.is_pressed else 1
        self._draw_rounded_rect(2, 1 + offset, w - 2, h - 2 + offset, r - 1, fill=fill_col)

        if not self.is_pressed:
            sheen = "#3d4147" if "neutral" in self.color_scheme else "#405370"
            if self.color_scheme == "danger":
                sheen = "#592b2b"
            elif "nav" in self.color_scheme:
                sheen = "#2c2e33"
            self._draw_rounded_rect(4, 3, w - 4, int(h * 0.45), r - 2, fill=sheen)

        self.create_text(
            w // 2,
            (h // 2) + (1 if self.is_pressed else 0),
            text=self.text,
            fill=self.colors["text"],
            font=("Segoe UI", 9, "bold"),
        )

    def _on_enter(self, e):
        self.is_hovered = True
        self.redraw()

    def _on_leave(self, e):
        self.is_hovered = False
        self.is_pressed = False
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
        self.root.title("Glass SMB & Private Storage Core (Administrator)")
        self.root.geometry("1140x860")
        self.root.minsize(1060, 800)

        self.current_process = None
        self.is_processing = False
        self.current_error_raw = ""

        # Neutral Frosted Glass Palette
        self.palette = {
            "bg_dark": "#161719",
            "bg_tint": "#1a1b1e",
            "glass_card": "#212327",
            "glass_rim_light": "#3b3e45",
            "glass_rim_shadow": "#0d0e10",
            "well_bg": "#141517",
            "well_border": "#2c2f35",
            "well_inner_glow": "#1f2126",
            "text_bright": "#ffffff",
            "text_frost": "#e5e7eb",
            "text_muted": "#9ca3af",
            "text_glow": "#d1d5db",
            "terminal_bg": "#101113",
            "success": "#34d399",
            "error": "#f87171",
        }

        self.discovered_users = []
        self.active_subfolders = []
        self.user_folder_permissions = {}

        self.setup_window_backdrop()
        self.setup_ttk_styles()

        self.main_container = tk.Frame(self.root, bg=self.palette["bg_tint"])
        self.main_container.place(relx=0.5, rely=0.5, relwidth=0.96, relheight=0.96, anchor="center")

        self.notebook = ttk.Notebook(self.main_container)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(10, 4))

        self.tab_smb = tk.Frame(self.notebook, bg=self.palette["bg_tint"])
        self.tab_tailscale = tk.Frame(self.notebook, bg=self.palette["bg_tint"])
        self.tab_snapraid = tk.Frame(self.notebook, bg=self.palette["bg_tint"])

        self.notebook.add(self.tab_smb, text="  ✦ User & SMB Vault  ")
        self.notebook.add(self.tab_tailscale, text="  ☁ Remote Mesh (Tailscale)  ")
        self.notebook.add(self.tab_snapraid, text="  ⛁ Parity Pool (SnapRAID)  ")

        self.build_smb_vertical_workflow()
        self.build_tailscale_tab()
        self.build_snapraid_tab()
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
        style.configure(
            "TNotebook.Tab",
            background="#1e2024",
            foreground=self.palette["text_muted"],
            padding=[20, 8],
            font=("Segoe UI", 10, "bold"),
            borderwidth=1,
            relief="flat",
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#32353b"), ("active", "#282a2f")],
            foreground=[("selected", self.palette["text_bright"]), ("active", self.palette["text_frost"])],
        )

        style.configure(
            "Glass.TCheckbutton",
            background=self.palette["well_bg"],
            foreground=self.palette["text_frost"],
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Glass.TCheckbutton",
            background=[("active", self.palette["well_bg"])],
            foreground=[("active", "#ffffff")],
        )

        style.configure(
            "Glass.TRadiobutton",
            background=self.palette["glass_card"],
            foreground=self.palette["text_frost"],
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Glass.TRadiobutton",
            background=[("active", self.palette["glass_card"])],
            foreground=[("active", "#ffffff")],
        )
        
        style.configure(
            "Dark.TCheckbutton",
            background=self.palette["glass_rim_shadow"], 
            foreground=self.palette["text_bright"],
            font=("Segoe UI", 9),
        )
        style.map(
            "Dark.TCheckbutton",
            background=[("active", self.palette["glass_rim_shadow"])],
            foreground=[("active", "#ffffff")],
        )
        
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

            lbl = tk.Label(
                header_box,
                text=title.upper(),
                bg=self.palette["glass_card"],
                fg=self.palette["text_glow"],
                font=("Segoe UI", 8, "bold"),
            )
            lbl.pack(side="left")

        return outer_rim, card

    def create_glass_entry(self, parent, width=25, show=None):
        well_rim = tk.Frame(parent, bg=self.palette["well_border"], padx=1, pady=1)
        entry = tk.Entry(
            well_rim,
            width=width,
            show=show,
            bg=self.palette["well_bg"],
            fg=self.palette["text_bright"],
            insertbackground="#9ca3af",
            relief="flat",
            font=("Segoe UI", 10),
            highlightthickness=1,
            highlightbackground=self.palette["well_inner_glow"],
            highlightcolor="#6b7280",
        )
        entry.pack(fill="both", expand=True)
        return well_rim, entry

    def build_smb_vertical_workflow(self):
        workflow_container = tk.Frame(self.tab_smb, bg=self.palette["bg_tint"])
        workflow_container.pack(fill="both", expand=True, pady=4)

        self.vert_nav_frame = tk.Frame(workflow_container, bg=self.palette["bg_tint"], width=190)
        self.vert_nav_frame.pack(side="left", fill="y", padx=(0, 8))

        self.vert_view_pane = tk.Frame(workflow_container, bg=self.palette["bg_tint"])
        self.vert_view_pane.pack(side="right", fill="both", expand=True)

        self.nav_btn_step1 = FrostedGlassButton(
            self.vert_nav_frame, text="1. Initial Setup", command=lambda: self.switch_vertical_tab(1),
            width=180, height=42, radius=16, color_scheme="nav_active"
        )
        self.nav_btn_step1.pack(pady=4)

        self.nav_btn_step2 = FrostedGlassButton(
            self.vert_nav_frame, text="2. Permissions", command=lambda: self.switch_vertical_tab(2),
            width=180, height=42, radius=16, color_scheme="nav"
        )
        self.nav_btn_step2.pack(pady=4)

        self.nav_btn_step3 = FrostedGlassButton(
            self.vert_nav_frame, text="3. Domain Setup", command=lambda: self.switch_vertical_tab(3),
            width=180, height=42, radius=16, color_scheme="nav"
        )
        self.nav_btn_step3.pack(pady=4)

        self.view_step1 = tk.Frame(self.vert_view_pane, bg=self.palette["bg_tint"])
        self.view_step2 = tk.Frame(self.vert_view_pane, bg=self.palette["bg_tint"])
        self.view_step3 = tk.Frame(self.vert_view_pane, bg=self.palette["bg_tint"])

        self.build_vview_step1()
        self.build_vview_step2()
        self.build_vview_step3()

        self.active_vtab = 1
        self.view_step1.pack(fill="both", expand=True)

    def switch_vertical_tab(self, tab_num):
        self.active_vtab = tab_num
        self.view_step1.pack_forget()
        self.view_step2.pack_forget()
        self.view_step3.pack_forget()

        self.nav_btn_step1.color_scheme = "nav_active" if tab_num == 1 else "nav"
        self.nav_btn_step2.color_scheme = "nav_active" if tab_num == 2 else "nav"
        self.nav_btn_step3.color_scheme = "nav_active" if tab_num == 3 else "nav"
        self.nav_btn_step1.redraw()
        self.nav_btn_step2.redraw()
        self.nav_btn_step3.redraw()

        if tab_num == 1:
            self.view_step1.pack(fill="both", expand=True)
        elif tab_num == 2:
            self.refresh_system_users()
            self.view_step2.pack(fill="both", expand=True)
        elif tab_num == 3:
            self.view_step3.pack(fill="both", expand=True)

    def build_vview_step1(self):
        user_rim, user_card = self.create_glass_card(self.view_step1, title="Step 1: Create Local User Accounts")
        user_rim.pack(fill="x", pady=4)

        desc = (
            "Create standard local Windows credentials dedicated for SMB and network file access.\n"
            "You can add Admins or solitary accounts and stay here, or advance to map their folders."
        )
        tk.Label(
            user_card, text=desc, bg=self.palette["glass_card"], fg=self.palette["text_muted"],
            font=("Segoe UI", 9), justify="left",
        ).pack(anchor="w", pady=(0, 10))

        ugrid = tk.Frame(user_card, bg=self.palette["glass_card"])
        ugrid.pack(fill="x", pady=4)

        tk.Label(ugrid, text="Username", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        _, self.ent_v_user = self.create_glass_entry(ugrid, width=22)
        self.ent_v_user.master.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)

        tk.Label(ugrid, text="Password", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w")
        _, self.ent_v_pass = self.create_glass_entry(ugrid, width=22, show="*")
        self.ent_v_pass.master.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=4)

        btn_row = tk.Frame(ugrid, bg=self.palette["glass_card"])
        btn_row.grid(row=1, column=2, sticky="e", pady=4)

        FrostedGlassButton(
            btn_row, text="+ Add User (Stay Here)", command=self.create_user_only,
            width=180, height=34, radius=16, color_scheme="neutral"
        ).pack(side="left", padx=(0, 8))

        FrostedGlassButton(
            btn_row, text="+ Add & Next →", command=self.create_user_and_advance,
            width=160, height=34, radius=16, color_scheme="accent"
        ).pack(side="left")

        det_rim, det_card = self.create_glass_card(self.view_step1, title="Currently Detected Local Accounts")
        det_rim.pack(fill="both", expand=True, pady=6)

        self.lbl_user_summary = tk.Label(
            det_card, text="Scanning system accounts...", bg=self.palette["glass_card"],
            fg=self.palette["text_frost"], font=("Consolas", 9), justify="left",
        )
        self.lbl_user_summary.pack(anchor="w", pady=4)

        FrostedGlassButton(
            det_card, text="Skip to Permissions →", command=lambda: self.switch_vertical_tab(2),
            width=180, height=32, radius=16, color_scheme="neutral",
        ).pack(anchor="w", pady=6)

    def build_vview_step2(self):
        root_rim, root_card = self.create_glass_card(self.view_step2, title="Step 2: NAS Root Directory")
        root_rim.pack(fill="x", pady=4)

        rgrid = tk.Frame(root_card, bg=self.palette["glass_card"])
        rgrid.pack(fill="x")

        tk.Label(rgrid, text="Select NAS Root Path (e.g., D:\\MainNAS)", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")

        path_box = tk.Frame(rgrid, bg=self.palette["glass_card"])
        path_box.grid(row=1, column=0, sticky="ew", pady=4)

        _, self.ent_nas_root = self.create_glass_entry(path_box, width=54)
        self.ent_nas_root.master.pack(side="left", fill="x", expand=True, padx=(0, 8))

        FrostedGlassButton(
            path_box, text="Browse...", command=self.browse_nas_root,
            width=100, height=32, radius=14, color_scheme="neutral",
        ).pack(side="right")

        tmpl_rim, tmpl_card = self.create_glass_card(self.view_step2, title="Step 3: Directory Structure Template")
        tmpl_rim.pack(fill="x", pady=4)

        self.var_struct_mode = tk.StringVar(value="scan")

        tbox = tk.Frame(tmpl_card, bg=self.palette["glass_card"])
        tbox.pack(fill="x", pady=2)

        ttk.Radiobutton(tbox, text="Use Existing (Scan for folders)", variable=self.var_struct_mode, value="scan", style="Glass.TRadiobutton").pack(anchor="w", pady=2)
        ttk.Radiobutton(tbox, text="<Multi-User, Private, No Sharing>  Root\\Users\\[Usernames]", variable=self.var_struct_mode, value="multi_private", style="Glass.TRadiobutton").pack(anchor="w", pady=2)
        ttk.Radiobutton(tbox, text="<Multi-User, Private & Sharing>  Root\\Shared  +  Root\\Users\\[Usernames]", variable=self.var_struct_mode, value="multi_shared", style="Glass.TRadiobutton").pack(anchor="w", pady=2)
        ttk.Radiobutton(tbox, text="<Single User>  Root", variable=self.var_struct_mode, value="single_user", style="Glass.TRadiobutton").pack(anchor="w", pady=2)

        action_row = tk.Frame(tmpl_card, bg=self.palette["glass_card"])
        action_row.pack(fill="x", pady=(6, 2))

        FrostedGlassButton(
            action_row, text="Auto-Create / Populate Structure", command=self.apply_structure_template,
            width=260, height=34, radius=16, color_scheme="accent",
        ).pack(side="left", padx=(0, 10))

        matrix_rim, matrix_card = self.create_glass_card(self.view_step2, title="Step 4: User & Folder Permissions Matrix")
        matrix_rim.pack(fill="both", expand=True, pady=4)

        matrix_split = tk.Frame(matrix_card, bg=self.palette["glass_card"])
        matrix_split.pack(fill="both", expand=True, pady=2)

        left_user_box = tk.Frame(matrix_split, bg=self.palette["glass_card"], width=200)
        left_user_box.pack(side="left", fill="y", padx=(0, 10))

        tk.Label(left_user_box, text="Select User:", bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold")).pack(anchor="w")

        self.user_listbox = tk.Listbox(
            left_user_box,
            bg=self.palette["well_bg"], fg=self.palette["text_bright"],
            selectbackground="#32353b", selectforeground="#ffffff",
            highlightthickness=1, highlightbackground=self.palette["well_border"],
            relief="flat", font=("Segoe UI", 10), height=7,
            exportselection=False
        )
        self.user_listbox.pack(fill="both", expand=True, pady=4)
        self.user_listbox.bind("<<ListboxSelect>>", self.on_user_selection_changed)

        right_perm_box = tk.Frame(matrix_split, bg=self.palette["glass_card"])
        right_perm_box.pack(side="right", fill="both", expand=True)

        self.lbl_perm_header = tk.Label(
            right_perm_box, text="Folder Access for Selected User:", bg=self.palette["glass_card"],
            fg=self.palette["text_glow"], font=("Segoe UI", 9, "bold"),
        )
        self.lbl_perm_header.pack(anchor="w")

        scroll_container = tk.Frame(right_perm_box, bg=self.palette["glass_card"])
        scroll_container.pack(fill="both", expand=True, pady=4)

        self.folder_scroll_canvas = tk.Canvas(
            scroll_container, bg=self.palette["well_bg"], highlightthickness=1,
            highlightbackground=self.palette["well_border"], height=130,
        )
        
        scrollbar = ttk.Scrollbar(scroll_container, orient="vertical", command=self.folder_scroll_canvas.yview, style="Vertical.TScrollbar")
        self.folder_scroll_canvas.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side="right", fill="y")
        self.folder_scroll_canvas.pack(side="left", fill="both", expand=True)

        self.folder_inner_frame = tk.Frame(self.folder_scroll_canvas, bg=self.palette["well_bg"])
        self.canvas_frame = self.folder_scroll_canvas.create_window((0, 0), window=self.folder_inner_frame, anchor="nw")

        def on_inner_configure(e):
            self.folder_scroll_canvas.configure(scrollregion=self.folder_scroll_canvas.bbox("all"))
            
        def on_canvas_configure(e):
            self.folder_scroll_canvas.itemconfig(self.canvas_frame, width=e.width)

        self.folder_inner_frame.bind("<Configure>", on_inner_configure)
        self.folder_scroll_canvas.bind("<Configure>", on_canvas_configure)
        
        def _on_mousewheel(event):
            self.folder_scroll_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        def _bind_mousewheel(event):
            self.folder_scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel)
        def _unbind_mousewheel(event):
            self.folder_scroll_canvas.unbind_all("<MouseWheel>")
            
        scroll_container.bind("<Enter>", _bind_mousewheel)
        scroll_container.bind("<Leave>", _unbind_mousewheel)

        apply_bar = tk.Frame(matrix_card, bg=self.palette["glass_card"])
        apply_bar.pack(fill="x", pady=(6, 2))

        FrostedGlassButton(
            apply_bar, text="⚡ Apply All Permissions & Publish Shares", command=self.apply_all_configured_permissions,
            width=360, height=38, radius=18, color_scheme="accent",
        ).pack(side="left", padx=(0, 10))

    def build_vview_step3(self):
        dom_rim, dom_card = self.create_glass_card(self.view_step3, title="Step 5: Friendly Hostname & Domain Mapping")
        dom_rim.pack(fill="both", expand=True, pady=4)

        desc = (
            "Network SMB shares are mapped via hostnames or IP addresses (e.g., \\\\100.x.y.z\\Share).\n"
            "Here you can configure a memorable friendly name for local devices or over Tailscale."
        )
        tk.Label(
            dom_card, text=desc, bg=self.palette["glass_card"], fg=self.palette["text_muted"],
            font=("Segoe UI", 9), justify="left",
        ).pack(anchor="w", pady=(0, 12))

        info_box = tk.Frame(dom_card, bg=self.palette["well_bg"], padx=12, pady=10)
        info_box.pack(fill="x", pady=4)

        curr_hostname = socket.gethostname()
        local_ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

        self.lbl_host_info = tk.Label(
            info_box,
            text=f"Local Computer Name: {curr_hostname}\nLocal LAN IP Address: {local_ip}\nStandard SMB Path: \\\\{curr_hostname}\\<ShareName>",
            bg=self.palette["well_bg"], fg=self.palette["text_frost"],
            font=("Consolas", 9), justify="left",
        )
        self.lbl_host_info.pack(anchor="w")

        alias_rim = tk.Frame(dom_card, bg=self.palette["glass_card"])
        alias_rim.pack(fill="x", pady=10)

        tk.Label(
            alias_rim, text="Friendly Alias for this PC (e.g., FamilyNAS or local.familynas):",
            bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=2)

        abox = tk.Frame(alias_rim, bg=self.palette["glass_card"])
        abox.pack(fill="x", pady=4)

        _, self.ent_domain_alias = self.create_glass_entry(abox, width=32)
        self.ent_domain_alias.insert(0, "FamilyNAS")
        self.ent_domain_alias.master.pack(side="left", padx=(0, 10))

        FrostedGlassButton(
            abox, text="Apply Alias to hosts", command=self.apply_hosts_alias,
            width=180, height=34, radius=16, color_scheme="accent",
        ).pack(side="left")

        magic_box = tk.Frame(dom_card, bg=self.palette["glass_card"])
        magic_box.pack(fill="x", pady=(10, 0))

        magic_note = (
            "✦ TAILSCALE MAGICDNS FOR REMOTE PHONES:\n"
            "Tailscale includes built-in 'MagicDNS'. When MagicDNS is active in your Tailscale admin console,\n"
            "all family members can connect to this NAS using just the device name:\n\n"
            f"   • iOS Files App:     smb://{curr_hostname.lower()}\n"
            f"   • Android Cx Explorer:  Host: {curr_hostname.lower()}\n\n"
            "No manual IP addresses or DNS editing required on their phones!"
        )
        tk.Label(
            magic_box, text=magic_note, bg=self.palette["glass_card"], fg=self.palette["text_muted"],
            font=("Consolas", 9), justify="left",
        ).pack(anchor="w")

    def build_tailscale_tab(self):
        panel = tk.Frame(self.tab_tailscale, bg=self.palette["bg_tint"])
        panel.pack(fill="both", expand=True, pady=6)

        card_rim, card = self.create_glass_card(panel, title="Encrypted Mesh Bridge & Server Authentication")
        card_rim.pack(fill="x", pady=(0, 6))

        tk.Label(
            card,
            text="Tailscale provisions an encrypted tunnel directly between devices.\nRequires zero router port-forwarding and assigns a permanent IP.",
            bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9), justify="left",
        ).pack(anchor="w", pady=(0, 10))

        btn_row = tk.Frame(card, bg=self.palette["glass_card"])
        btn_row.pack(fill="x", pady=4)

        FrostedGlassButton(
            btn_row, text="Install Tailscale Engine", command=self.install_tailscale,
            width=200, height=36, radius=18, color_scheme="neutral",
        ).pack(side="left", padx=(0, 12))

        def auth_tailscale():
            ts_path = r"C:\Program Files\Tailscale\tailscale.exe"
            if os.path.exists(ts_path):
                self.run_cmd_thread([ts_path, "up"], "Tailscale web login active.")
            else:
                self.set_status("Tailscale not found. Please install it first.", "error")

        FrostedGlassButton(
            btn_row, text="Authenticate Node (Login)", command=auth_tailscale,
            width=240, height=36, radius=18, color_scheme="accent",
        ).pack(side="left")

        guide_rim, guide_frame = self.create_glass_card(panel, title="Comprehensive Setup & Client Connection Guide")
        guide_rim.pack(fill="both", expand=True, pady=(6, 0))

        txt = tk.Text(
            guide_frame, bg=self.palette["well_bg"], fg=self.palette["text_frost"],
            font=("Consolas", 9), wrap="word", relief="flat", padx=12, pady=12
        )
        
        scroll = ttk.Scrollbar(guide_frame, orient="vertical", command=txt.yview, style="Vertical.TScrollbar")
        txt.configure(yscrollcommand=scroll.set)
        
        scroll.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        guide_content = """TAILSCALE COMPREHENSIVE SETUP GUIDE

PHASE 1: SERVER CONFIGURATION (DASHBOARD)
1. Authenticate Node: Click the 'Authenticate Node' button above to log this server into Tailscale.
2. Disable Key Expiry: 
   • Go to login.tailscale.com -> Machines in your browser.
   • Click the (...) menu next to this NAS PC and select "Disable Key Expiry".
     (This ensures your server doesn't randomly disconnect after 180 days).
3. Enable MagicDNS:
   • Go to the DNS tab in the Tailscale dashboard.
   • Toggle MagicDNS ON. This lets devices connect using the computer's name (e.g., FamilyNAS) instead of an IP address.

PHASE 2: CONNECTING CLIENT DEVICES
Each device must have the Tailscale app installed and logged in to the SAME account you used for the server.

▶ Windows PCs & Laptops
   1. Install Tailscale and log in.
   2. Open File Explorer. In the top address bar, type: \\\\FamilyNAS (or your PC's name) and press Enter.
   3. Enter the local Windows Username and Password you created for them in Step 1.
   4. Right-click the folder and select "Pin to Quick Access".

▶ Apple iPhone & iPad (Native Support)
   1. Install Tailscale from the App Store, log in, and ensure the VPN is Active.
   2. Open the native Apple 'Files' app.
   3. Tap 'Browse' at the bottom, then the (...) menu in the top right.
   4. Select 'Connect to Server'.
   5. Enter: smb://FamilyNAS
   6. Select 'Registered User' and enter their Windows credentials.

▶ Android Phones & Tablets (Cx File Explorer)
   Android does not have native SMB support. You must use a trusted 3rd-party file manager.
   1. Install Tailscale from Google Play, log in, and connect.
   2. Install 'Cx File Explorer' from Google Play (recommended, no ads, great UI).
   3. Open Cx File Explorer -> Network tab -> [+] New Location -> Remote -> SMB.
   4. Host: FamilyNAS
   5. Port: (Leave blank)
   6. Username & Password: Enter their specific credentials.
   7. Check "Display password" to verify, then tap OK.
   8. A permanent shortcut will now exist on the Network tab to easily access their files."""
        
        txt.insert("1.0", guide_content)
        txt.config(state="disabled")

    def build_snapraid_tab(self):
        panel = tk.Frame(self.tab_snapraid, bg=self.palette["bg_tint"])
        panel.pack(fill="both", expand=True, pady=6)

        card_rim, card = self.create_glass_card(panel, title="SnapRAID Parity Architecture")
        card_rim.pack(fill="x", pady=(0, 6))

        tk.Label(
            card,
            text="SnapRAID generates array parity across storage drives without disk striping.\nDisks remain readable individually on any machine.",
            bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9), justify="left",
        ).pack(anchor="w", pady=(0, 10))

        FrostedGlassButton(
            card, text="Install SnapRAID (via Winget)", command=self.install_snapraid,
            width=220, height=36, radius=18, color_scheme="neutral",
        ).pack(anchor="w", pady=4)

        guide_rim, guide_frame = self.create_glass_card(panel, title="Comprehensive Parity Setup Guide")
        guide_rim.pack(fill="both", expand=True, pady=(6, 0))

        txt = tk.Text(
            guide_frame, bg=self.palette["well_bg"], fg=self.palette["text_frost"],
            font=("Consolas", 9), wrap="word", relief="flat", padx=12, pady=12
        )
        
        scroll = ttk.Scrollbar(guide_frame, orient="vertical", command=txt.yview, style="Vertical.TScrollbar")
        txt.configure(yscrollcommand=scroll.set)
        
        scroll.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        guide_content = """SNAPRAID COMPREHENSIVE SETUP GUIDE

SnapRAID is a backup program for disk arrays. It stores parity data to rescue your files if a hard drive fails.

PHASE 1: DISK PREPARATION
1. Dedicate at least one hard drive strictly for Parity. 
   CRITICAL: Your parity drive MUST be equal to or larger than your largest data drive.
2. Format your drives in Windows (NTFS) and assign them clear drive letters 
   (e.g., P: for Parity, D1: for Data 1, D2: for Data 2).

PHASE 2: CONFIGURATION
1. SnapRAID uses a configuration file located at C:\\SnapRAID\\snapraid.conf.
2. Open this file in Notepad and define your disks exactly like this:
   
   parity P:\\snapraid.parity
   content P:\\snapraid.content
   content C:\\SnapRAID\\snapraid.content
   content D1:\\snapraid.content
   data d1 D1:\\FamilyNAS
   data d2 D2:\\FamilyNAS
   
   (Note: 'content' files are small index files. Keep multiple copies across different drives so SnapRAID always knows where your files were).

PHASE 3: INITIALIZATION & SYNC
1. Open Command Prompt as Administrator.
2. Run: snapraid sync
3. This first sync will take a long time depending on how much data you have. It calculates the parity blocks across all your drives.

PHASE 4: ROUTINE MAINTENANCE (AUTOMATION)
To keep your safety net updated, use the Windows Task Scheduler to run these commands in the background:
• snapraid sync (Run Daily at 2 AM): Updates the parity with any new or modified files.
• snapraid scrub (Run Weekly): Checks the disks for silent data corruption (bit rot) and fixes it.

PHASE 5: RESTORING LOST DATA
If a drive fails or you accidentally delete a file:
• Undelete a file: snapraid fix -f "FileName.ext"
• Restore a whole drive: Replace the dead drive, update the drive letter in snapraid.conf if necessary, and run: snapraid fix -d d1"""
        
        txt.insert("1.0", guide_content)
        txt.config(state="disabled")

    # =========================================================================
    # REVISED STATUS BAR
    # =========================================================================
    def build_status_bar(self):
        status_rim = tk.Frame(self.main_container, bg=self.palette["glass_rim_light"], padx=1, pady=1)
        status_rim.pack(fill="x", pady=(4, 8), padx=12)
        
        status_shadow = tk.Frame(status_rim, bg=self.palette["glass_rim_shadow"], padx=1, pady=1)
        status_shadow.pack(fill="both", expand=True)
        
        status_card = tk.Frame(status_shadow, bg=self.palette["glass_card"], padx=14, pady=8)
        status_card.pack(fill="both", expand=True)

        self.lbl_status = tk.Label(
            status_card,
            text="Ready.",
            bg=self.palette["glass_card"], fg=self.palette["text_muted"],
            font=("Segoe UI", 10, "bold")
        )
        self.lbl_status.pack(side="left", fill="x", expand=True, anchor="w")

        FrostedGlassButton(
            status_card, text="🛑 Stop", command=self.stop_current_operation,
            width=100, height=34, radius=16, color_scheme="danger",
        ).pack(side="right")

    def set_status(self, msg, status_type="info", raw_error=None):
        """Updates the status bar. Converts logs into a sleek message format."""
        color_map = {
            "info": self.palette["text_muted"],
            "success": self.palette["success"],
            "error": self.palette["error"],
        }
        
        self.lbl_status.config(text=msg, fg=color_map.get(status_type, self.palette["text_muted"]))
        
        if status_type == "error" and raw_error:
            self.current_error_raw = raw_error
            self.lbl_status.config(cursor="hand2")
            self.lbl_status.bind("<Button-1>", lambda e: self.show_error_popup())
        else:
            self.current_error_raw = ""
            self.lbl_status.config(cursor="")
            self.lbl_status.unbind("<Button-1>")

    def translate_error(self, err_text):
        """Attempts to simplify cryptic Windows terminal errors."""
        err_lower = err_text.lower()
        if "access is denied" in err_lower or "error 5" in err_lower:
            return "Windows blocked this action. Ensure you have Administrative rights and that the file/folder isn't currently locked by another program."
        if "already exists" in err_lower:
            return "The user account or share name you are trying to create already exists."
        if "no mapping between account names" in err_lower:
            return "Windows could not find the user account. It may not have been created successfully."
        if "cannot find path" in err_lower:
            return "The specified folder path does not exist or was moved."
        if "winget" in err_lower and "agreements" in err_lower:
            return "Windows Package Manager requires you to accept terms. Try running the installer manually once via Command Prompt."
        
        return "An unexpected system command failure occurred. See the technical details below."

    def show_error_popup(self):
        """Displays the layman's error and the raw output in a custom window."""
        if not getattr(self, "current_error_raw", None): return

        popup = tk.Toplevel(self.root)
        popup.title("Error Details")
        popup.geometry("600x400")
        popup.configure(bg=self.palette["bg_tint"])
        popup.transient(self.root)
        popup.grab_set()

        title_lbl = tk.Label(popup, text="Operation Failed", fg=self.palette["error"], bg=self.palette["bg_tint"], font=("Segoe UI", 12, "bold"))
        title_lbl.pack(anchor="w", padx=16, pady=(16, 4))
        
        layman_err = self.translate_error(self.current_error_raw)
        desc_lbl = tk.Label(popup, text=layman_err, fg=self.palette["text_bright"], bg=self.palette["bg_tint"], font=("Segoe UI", 10), wraplength=560, justify="left")
        desc_lbl.pack(anchor="w", padx=16, pady=4)

        tk.Label(popup, text="Raw Command Output:", fg=self.palette["text_muted"], bg=self.palette["bg_tint"], font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(16, 4))

        well_f = tk.Frame(popup, bg=self.palette["well_border"], padx=1, pady=1)
        well_f.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        txt = tk.Text(
            well_f, bg=self.palette["terminal_bg"], fg=self.palette["text_glow"],
            relief="flat", font=("Consolas", 9), wrap="word", padx=8, pady=8
        )
        txt.insert("1.0", self.current_error_raw)
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
            self.current_process = subprocess.Popen(
                command_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            stdout, stderr = self.current_process.communicate()
            rc = self.current_process.returncode

            if rc == 0:
                if success_msg:
                    self.set_status(success_msg, "success")
                return True
            else:
                raw = stderr.strip() or stdout.strip()
                self.set_status("Error Occurred! (Click for details)", "error", raw)
                return False
        except Exception as e:
            self.set_status("Error Occurred! (Click for details)", "error", str(e))
            return False
        finally:
            self.current_process = None

    def run_quiet_cmd(self, cmd, use_shell=False):
        """Runs command silently. Aborts and updates status on failure."""
        if not self.is_processing:
            return False
        try:
            self.current_process = subprocess.Popen(
                cmd, shell=use_shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, creationflags=subprocess.CREATE_NO_WINDOW
            )
            stdout, stderr = self.current_process.communicate()
            
            if self.current_process.returncode != 0:
                raw = stderr.strip() or stdout.strip()
                self.set_status("Error Occurred! (Click for details)", "error", raw)
                self.is_processing = False
                return False
            return True
        except Exception as e:
            self.set_status("Error Occurred! (Click for details)", "error", str(e))
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
                self.set_status("Active process killed.", "error", "The task was forcefully terminated by the user.")
            except Exception as e:
                self.set_status("Error aborting process", "error", str(e))
            finally:
                self.current_process = None
        else:
            self.set_status("No active background task to cancel.", "info")

    def refresh_system_users(self):
        def fetch():
            ps_cmd = "Get-LocalUser | Where-Object { $_.Enabled -eq $True } | Select-Object -ExpandProperty Name"
            res = subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if res.returncode == 0:
                raw_users = [u.strip() for u in res.stdout.splitlines() if u.strip()]
                filtered = []
                for u in raw_users:
                    ul = u.lower()
                    if ul in ["administrator", "guest", "defaultaccount", "wdagutilityaccount", "family storage"]:
                        continue
                    if ul.startswith("defaultuser"):
                        continue
                    filtered.append(u)
                
                self.discovered_users = filtered

                def update_ui():
                    self.lbl_user_summary.config(
                        text=f"Detected Active Accounts ({len(filtered)}):\n" + ", ".join(filtered)
                    )
                    curr_sel = self.user_listbox.curselection()
                    self.user_listbox.delete(0, tk.END)
                    for user in self.discovered_users:
                        self.user_listbox.insert(tk.END, user)
                    if self.discovered_users:
                        idx = curr_sel[0] if curr_sel and curr_sel[0] < len(self.discovered_users) else 0
                        self.user_listbox.selection_set(idx)
                        self.on_user_selection_changed()

                self.root.after(0, update_ui)

        threading.Thread(target=fetch, daemon=True).start()

    def create_user_only(self):
        self._exec_create_user(advance_tabs=False)

    def create_user_and_advance(self):
        self._exec_create_user(advance_tabs=True)

    def _exec_create_user(self, advance_tabs=False):
        username = self.ent_v_user.get().strip()
        password = self.ent_v_pass.get().strip()

        if not username or not password:
            messagebox.showerror("Input Error", "Please enter both Username and Password.")
            return

        self.set_status(f"Creating user '{username}'...", "info")
        def process():
            cmd = ["net", "user", username, password, "/add", "/expires:never"]
            ok = self.run_cmd(cmd, f"Success! User '{username}' created.")
            if ok:
                self.root.after(0, lambda: self.ent_v_user.delete(0, tk.END))
                self.root.after(0, lambda: self.ent_v_pass.delete(0, tk.END))
                self.refresh_system_users()
                if advance_tabs:
                    self.root.after(0, lambda: self.switch_vertical_tab(2))

        threading.Thread(target=process, daemon=True).start()

    def browse_nas_root(self):
        folder = filedialog.askdirectory()
        if folder:
            norm = os.path.normpath(folder)
            self.ent_nas_root.delete(0, tk.END)
            self.ent_nas_root.insert(0, norm)
            self.scan_or_populate_folders()

    def on_structure_mode_change(self):
        pass

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
                for user in self.discovered_users:
                    os.makedirs(os.path.join(users_dir, user), exist_ok=True)
                self.set_status(f"Created Private Structure under {users_dir}", "success")

            elif mode == "multi_shared":
                shared_dir = os.path.join(root_dir, "Shared")
                users_dir = os.path.join(root_dir, "Users")
                os.makedirs(shared_dir, exist_ok=True)
                os.makedirs(users_dir, exist_ok=True)
                for user in self.discovered_users:
                    os.makedirs(os.path.join(users_dir, user), exist_ok=True)
                self.set_status(f"Created Shared & Private Structure under {root_dir}", "success")

            elif mode == "single_user":
                self.set_status(f"Configured Single User root: {root_dir}", "success")

            self.scan_or_populate_folders()

        except Exception as e:
            self.set_status("Error Occurred! (Click for details)", "error", str(e))

    def scan_or_populate_folders(self):
        root_dir = self.ent_nas_root.get().strip()
        if not root_dir or not os.path.exists(root_dir):
            return

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
                            u_full = os.path.join(full, u_item)
                            if os.path.isdir(u_full):
                                folders.append(u_full)
        except Exception as e:
            self.set_status("Error Occurred! (Click for details)", "error", str(e))

        unique_map = {}
        for f in folders:
            unique_map[f.lower()] = f
            
        self.active_subfolders = sorted(list(unique_map.values()))
        self.set_status(f"Discovered {len(self.active_subfolders)} folders under root.", "info")
        self.rebuild_permissions_ui()

    def rebuild_permissions_ui(self):
        for widget in self.folder_inner_frame.winfo_children():
            widget.destroy()

        sel = self.user_listbox.curselection()
        if not sel or not self.discovered_users:
            tk.Label(
                self.folder_inner_frame,
                text="No user selected or accounts available.",
                bg=self.palette["well_bg"], fg=self.palette["text_muted"], font=("Segoe UI", 9),
            ).pack(anchor="w", padx=10, pady=10)
            return

        selected_user = self.discovered_users[sel[0]]
        self.lbl_perm_header.config(text=f"Folder Access for User: '{selected_user}'")

        if selected_user not in self.user_folder_permissions:
            self.user_folder_permissions[selected_user] = {}

        for fpath in self.active_subfolders:
            rel_name = os.path.basename(fpath) or fpath
            is_own_home = (rel_name.lower() == selected_user.lower())
            is_shared = ("shared" in fpath.lower())

            if fpath not in self.user_folder_permissions[selected_user]:
                should_enable = is_own_home or is_shared
                self.user_folder_permissions[selected_user][fpath] = {
                    "enabled": tk.BooleanVar(value=should_enable),
                    "read": tk.BooleanVar(value=True),
                    "upload": tk.BooleanVar(value=not is_own_home),
                    "create": tk.BooleanVar(value=not is_own_home),
                    "delete": tk.BooleanVar(value=is_own_home),
                    "full": tk.BooleanVar(value=is_own_home),
                }

            state = self.user_folder_permissions[selected_user][fpath]

            row_f = tk.Frame(self.folder_inner_frame, bg=self.palette["well_bg"])
            row_f.pack(fill="x", padx=8, pady=3)

            perm_f = tk.Frame(row_f, bg=self.palette["glass_rim_shadow"], padx=24, pady=6)
            
            def make_toggle(frame, var):
                def _toggle():
                    if var.get():
                        frame.pack(fill="x", pady=(2, 6))
                    else:
                        frame.pack_forget()
                    self.folder_scroll_canvas.configure(scrollregion=self.folder_scroll_canvas.bbox("all"))
                return _toggle

            chk_folder = ttk.Checkbutton(
                row_f, text=fpath, variable=state["enabled"], style="Glass.TCheckbutton",
                command=make_toggle(perm_f, state["enabled"])
            )
            chk_folder.pack(anchor="w", pady=2)

            p_grid = tk.Frame(perm_f, bg=self.palette["glass_rim_shadow"])
            p_grid.pack(fill="x")
            
            ttk.Checkbutton(p_grid, text="Read Only", variable=state["read"], style="Dark.TCheckbutton").grid(row=0, column=0, sticky="w", padx=(0,15), pady=2)
            ttk.Checkbutton(p_grid, text="Upload Media", variable=state["upload"], style="Dark.TCheckbutton").grid(row=0, column=1, sticky="w", padx=(0,15), pady=2)
            ttk.Checkbutton(p_grid, text="Create New Folders", variable=state["create"], style="Dark.TCheckbutton").grid(row=0, column=2, sticky="w", padx=(0,15), pady=2)
            ttk.Checkbutton(p_grid, text="Delete Media", variable=state["delete"], style="Dark.TCheckbutton").grid(row=1, column=0, sticky="w", padx=(0,15), pady=2)
            ttk.Checkbutton(p_grid, text="Full Access", variable=state["full"], style="Dark.TCheckbutton").grid(row=1, column=1, sticky="w", padx=(0,15), pady=2)

            make_toggle(perm_f, state["enabled"])()

    def on_user_selection_changed(self, event=None):
        self.rebuild_permissions_ui()

    def apply_all_configured_permissions(self):
        if getattr(self, "is_processing", False):
            self.set_status("Process already running. Please wait or press Stop.", "error", "Task blocked due to concurrency lock.")
            return

        root_dir = self.ent_nas_root.get().strip()
        if not root_dir or not os.path.exists(root_dir):
            messagebox.showerror("Error", "NAS root directory does not exist.")
            return
            
        self.is_processing = True
        self.set_status("Starting Batch Permissions & SMB Share Deployment...", "info")

        def process():
            try:
                for user, fmap in self.user_folder_permissions.items():
                    for fpath, state in fmap.items():
                        if not self.is_processing:
                            return 

                        if state["enabled"].get():
                            
                            if state["full"].get():
                                ntfs_perm = "F"
                                ps_access = "Full"
                            elif state["delete"].get() or state["create"].get() or state["upload"].get():
                                ntfs_perm = "M"
                                ps_access = "Change"
                            else:
                                ntfs_perm = "R"
                                ps_access = "Read"

                            rel_name = os.path.basename(fpath)
                            is_private = (rel_name.lower() == user.lower())

                            self.set_status(f"Applying permissions for {user} -> {rel_name}...", "info")

                            if is_private:
                                if not self.run_quiet_cmd(f'icacls "{fpath}" /inheritance:r', use_shell=True): return
                                if not self.run_quiet_cmd(f'icacls "{fpath}" /grant:r "Administrators":(OI)(CI)F', use_shell=True): return

                            if not self.run_quiet_cmd(f'icacls "{fpath}" /grant:r "{user}":(OI)(CI){ntfs_perm} /T', use_shell=True): return

                            share_name = rel_name if rel_name else "RootNAS"
                            
                            if ps_access == "Full":
                                creation_args = f"-FullAccess 'Administrators', '{user}'"
                            else:
                                creation_args = f"-FullAccess 'Administrators' -{ps_access}Access '{user}'"

                            ps_cmd = (
                                f"if (Get-SmbShare -Name '{share_name}' -ErrorAction SilentlyContinue) {{ "
                                f"  Revoke-SmbShareAccess -Name '{share_name}' -AccountName Everyone -Force; "
                                f"  Grant-SmbShareAccess -Name '{share_name}' -AccountName '{user}' -AccessRight {ps_access} -Force "
                                f"}} else {{ "
                                f"  New-SmbShare -Name '{share_name}' -Path '{fpath}' {creation_args} "
                                f"}}"
                            )
                            
                            if not self.run_quiet_cmd(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd], use_shell=False): return

                if self.is_processing:
                    self.set_status("Success! All Permissions and SMB Shares published.", "success")
            finally:
                self.is_processing = False

        threading.Thread(target=process, daemon=True).start()

    def apply_hosts_alias(self):
        alias = self.ent_domain_alias.get().strip()
        if not alias:
            messagebox.showerror("Error", "Please enter a valid alias.")
            return

        alias_clean = re.sub(r"[^a-zA-Z0-9\.\-_]", "", alias)
        hosts_path = r"C:\Windows\System32\drivers\etc\hosts"

        try:
            with open(hosts_path, "r") as f:
                content = f.read()

            entry = f"\n127.0.0.1\t{alias_clean}\n"
            if alias_clean in content:
                self.set_status(f"Alias '{alias_clean}' already exists in hosts file.", "info")
            else:
                with open(hosts_path, "a") as f:
                    f.write(entry)
                self.set_status(f"Success! Added alias '{alias_clean}' -> 127.0.0.1 in hosts file.", "success")

        except Exception as e:
            self.set_status("Error Occurred! (Click for details)", "error", str(e))

    def install_tailscale(self):
        cmd = [
            "winget", "install", "-e", "--id", "Tailscale.Tailscale", 
            "--silent", "--accept-package-agreements", "--accept-source-agreements"
        ]
        self.set_status("Fetching Tailscale via Windows Package Manager...", "info")
        self.run_cmd_thread(cmd, "Success! Tailscale installed.")

    def install_snapraid(self):
        cmd = [
            "winget", "install", "-e", "--id", "SnapRAID.SnapRAID", 
            "--silent", "--accept-package-agreements", "--accept-source-agreements"
        ]
        self.set_status("Fetching SnapRAID via Windows Package Manager...", "info")
        self.run_cmd_thread(cmd, "Success! SnapRAID installed.")


if __name__ == "__main__":
    if not is_admin():
        run_as_admin()
    else:
        root = tk.Tk()
        app = GlassSMBManagerApp(root)
        root.mainloop()