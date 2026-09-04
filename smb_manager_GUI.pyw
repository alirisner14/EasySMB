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
        self.build_console_bar()

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

        card_rim, card = self.create_glass_card(panel, title="Encrypted Mesh Bridge")
        card_rim.pack(fill="both", expand=True)

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

        FrostedGlassButton(
            btn_row, text="Authenticate Node (tailscale up)", command=lambda: self.run_cmd_thread(["tailscale", "up"], "Tailscale web login active."),
            width=240, height=36, radius=18, color_scheme="accent",
        ).pack(side="left")

        guide_rim, guide_frame = self.create_glass_card(card, title="Mobile Client Setup")
        guide_rim.pack(fill="both", expand=True, pady=(12, 0))

        guide_content = (
            "📱 CONNECTING PHONES & EXTERNAL CLIENTS:\n\n"
            "1. Apple iOS (Files App):\n"
            "   • Open 'Files' → tap '...' (top right) → select 'Connect to Server'.\n"
            "   • Enter: smb://100.x.y.z  (or smb://<hostname> if MagicDNS is enabled).\n"
            "   • Authenticate as Registered User with the SMB username & password.\n\n"
            "2. Android (Cx File Explorer / Solid Explorer):\n"
            "   • In Cx File Explorer: Network → New Location → SMB/LAN.\n"
            "   • Enter host IP (or hostname) and SMB credentials."
        )
        tk.Label(
            guide_frame, text=guide_content, bg=self.palette["glass_card"], fg=self.palette["text_muted"],
            font=("Consolas", 9), justify="left",
        ).pack(anchor="w")

    def build_snapraid_tab(self):
        panel = tk.Frame(self.tab_snapraid, bg=self.palette["bg_tint"])
        panel.pack(fill="both", expand=True, pady=6)

        card_rim, card = self.create_glass_card(panel, title="SnapRAID Parity Architecture")
        card_rim.pack(fill="both", expand=True)

        tk.Label(
            card,
            text="SnapRAID generates array parity across storage drives without disk striping.\nDisks remain readable individually on any machine.",
            bg=self.palette["glass_card"], fg=self.palette["text_frost"], font=("Segoe UI", 9), justify="left",
        ).pack(anchor="w", pady=(0, 10))

        FrostedGlassButton(
            card, text="Install SnapRAID (via Winget)", command=self.install_snapraid,
            width=220, height=36, radius=18, color_scheme="neutral",
        ).pack(anchor="w", pady=4)

        guide_rim, guide_frame = self.create_glass_card(card, title="Parity Setup Guide")
        guide_rim.pack(fill="both", expand=True, pady=(12, 0))

        guide_content = (
            "✦ SNAPRAID CHECKLIST:\n\n"
            "1. Parity Sizing: The parity drive MUST be ≥ your largest data drive.\n"
            "2. Configuration (C:\\SnapRAID\\snapraid.conf):\n"
            "     parity P:\\snapraid.parity\n"
            "     content P:\\snapraid.content\n"
            "     content D:\\snapraid.content\n"
            "     data d1 D:\\MainNAS\n"
            "3. Routine Maintenance:\n"
            "   • snapraid sync  (Recalculates parity snapshots)\n"
            "   • snapraid scrub (Scans silent data decay and bit rot)"
        )
        tk.Label(
            guide_frame, text=guide_content, bg=self.palette["glass_card"], fg=self.palette["text_muted"],
            font=("Consolas", 9), justify="left",
        ).pack(anchor="w")

    def build_console_bar(self):
        console_rim, console_card = self.create_glass_card(self.main_container, title="Console")
        console_rim.pack(fill="x", pady=(4, 8), padx=12)

        crow = tk.Frame(console_card, bg=self.palette["glass_card"])
        crow.pack(fill="x")

        self.txt_log = tk.Text(
            crow,
            bg=self.palette["terminal_bg"], fg=self.palette["text_glow"], insertbackground="white",
            relief="flat", font=("Consolas", 9), height=5, wrap="word",
            highlightthickness=1, highlightbackground=self.palette["well_border"],
        )
        
        scrollbar = ttk.Scrollbar(crow, orient="vertical", command=self.txt_log.yview, style="Vertical.TScrollbar")
        self.txt_log.configure(yscrollcommand=scrollbar.set)
        
        btn_stop = FrostedGlassButton(
            crow, text="🛑 Stop", command=self.stop_current_operation,
            width=100, height=34, radius=16, color_scheme="danger",
        )
        
        btn_stop.pack(side="right", padx=(10, 0))
        scrollbar.pack(side="right", fill="y")
        self.txt_log.pack(side="left", fill="both", expand=True)

    def log(self, text):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

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
                    self.log(f"[✓] {success_msg}")
                return True
            else:
                self.log(f"[!] Failed:\n{stderr.strip() or stdout.strip()}")
                return False
        except Exception as e:
            self.log(f"[FATAL] {str(e)}")
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
                self.log("[🛑] Active process killed.")
            except Exception as e:
                self.log(f"[!] Error aborting process: {e}")
            finally:
                self.current_process = None
                
        self.log("[🛑] Operations halted.")

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

        def process():
            cmd = ["net", "user", username, password, "/add", "/expires:never"]
            ok = self.run_cmd(cmd, f"User '{username}' created successfully.")
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
                self.log(f"[✓] Created Private Structure under {users_dir}")

            elif mode == "multi_shared":
                shared_dir = os.path.join(root_dir, "Shared")
                users_dir = os.path.join(root_dir, "Users")
                os.makedirs(shared_dir, exist_ok=True)
                os.makedirs(users_dir, exist_ok=True)
                for user in self.discovered_users:
                    os.makedirs(os.path.join(users_dir, user), exist_ok=True)
                self.log(f"[✓] Created Shared & Private Structure under {root_dir}")

            elif mode == "single_user":
                self.log(f"[✓] Configured Single User root: {root_dir}")

            self.scan_or_populate_folders()

        except Exception as e:
            self.log(f"[!] Failed creating directory structure: {e}")

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
            self.log(f"[!] Scan warning: {e}")

        unique_map = {}
        for f in folders:
            unique_map[f.lower()] = f
            
        self.active_subfolders = sorted(list(unique_map.values()))
        self.log(f"[i] Discovered {len(self.active_subfolders)} unique folders under root.")
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
            self.log("[!] Process already running. Please wait or press Stop.")
            return

        root_dir = self.ent_nas_root.get().strip()
        if not root_dir or not os.path.exists(root_dir):
            messagebox.showerror("Error", "NAS root directory does not exist.")
            return
            
        self.is_processing = True

        def run_quiet_cmd(cmd, use_shell=False):
            if not self.is_processing:
                return False
            try:
                self.current_process = subprocess.Popen(
                    cmd, shell=use_shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, creationflags=subprocess.CREATE_NO_WINDOW
                )
                stdout, stderr = self.current_process.communicate()
                return self.current_process.returncode == 0
            except Exception as e:
                self.log(f"[!] Error executing background task: {e}")
                return False
            finally:
                self.current_process = None

        def process():
            try:
                self.log("[*] Starting Batch Permissions & SMB Share Deployment...")

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

                            if is_private:
                                run_quiet_cmd(f'icacls "{fpath}" /inheritance:r', use_shell=True)
                                if not self.is_processing: return
                                
                                run_quiet_cmd(f'icacls "{fpath}" /grant:r "Administrators":(OI)(CI)F', use_shell=True)
                                if not self.is_processing: return

                            run_quiet_cmd(f'icacls "{fpath}" /grant:r "{user}":(OI)(CI){ntfs_perm} /T', use_shell=True)
                            if not self.is_processing: return

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
                            
                            run_quiet_cmd(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd], use_shell=False)
                            
                            if self.is_processing:
                                self.log(f"[✓] Configured '{share_name}' for '{user}' (Rights: {ps_access})")

                if self.is_processing:
                    self.log("[✓] All Permissions and SMB Shares published successfully.")
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
                self.log(f"[i] Alias '{alias_clean}' already exists in hosts file.")
            else:
                with open(hosts_path, "a") as f:
                    f.write(entry)
                self.log(f"[✓] Added alias '{alias_clean}' -> 127.0.0.1 in hosts file.")

            messagebox.showinfo("Success", f"Alias '{alias_clean}' successfully mapped!")
        except Exception as e:
            self.log(f"[!] Failed editing hosts file: {e}")

    def install_tailscale(self):
        cmd = ["winget", "install", "-e", "--id", "Tailscale.Tailscale", "--silent"]
        self.log("Fetching Tailscale via Windows Package Manager...")
        self.run_cmd_thread(cmd, "Tailscale package successfully installed.")

    def install_snapraid(self):
        cmd = ["winget", "install", "-e", "--id", "SnapRAID.SnapRAID", "--silent"]
        self.log("Fetching SnapRAID via Windows Package Manager...")
        self.run_cmd_thread(cmd, "SnapRAID package successfully installed.")


if __name__ == "__main__":
    if not is_admin():
        run_as_admin()
    else:
        root = tk.Tk()
        app = GlassSMBManagerApp(root)
        root.mainloop()