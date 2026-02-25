#!/usr/bin/env python3
"""Phase 1 — Visual Trainer (Script Maker)

Three-panel desktop GUI for building Android automation workflows.

Layout
------
  Left   : step list (Treeview) — add / remove / reorder steps
  Centre : device screenshot canvas — drag to mark regions / swipe points
  Right  : step properties panel — configure every parameter of the selected step

Workflow types
--------------
  click             Find an element and tap it.
  verify            Check element presence without tapping.
  conditional_click Tap an element only if a condition element is visible.
  wait              Pause for N seconds.
  swipe             Perform a swipe gesture (mark start + end on canvas).
  input_text        Type text into the focused field.
  press_key         Send a hardware key event.

Saving
------
  Save Workflow  → elements/registry_<name>.json  (multi-step sequence)
  Save Element   → elements/registry.json          (single element entry)

Usage
-----
    python trainer.py              # auto-capture screenshot → open GUI
    python trainer.py --shot PATH  # reuse existing screenshot
    python trainer.py --list       # print registry / workflows, then exit
"""

import argparse
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from PIL import Image, ImageTk

from utils.adb import capture_screenshot

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

ELEMENTS_DIR   = Path("elements")
TEMP_SHOT      = "trainer_screen.png"

STEP_TYPES = [
    "click",
    "verify",
    "conditional_click",
    "wait",
    "swipe",
    "input_text",
    "press_key",
]

STEP_LABELS = {
    "click":             "Click Element",
    "verify":            "Verify Presence",
    "conditional_click": "Conditional Click",
    "wait":              "Wait",
    "swipe":             "Swipe",
    "input_text":        "Input Text",
    "press_key":         "Press Key",
}

STEP_DESCRIPTIONS = {
    "click":             "Find an element on screen and tap it.",
    "verify":            "Check if an element is visible (no tap).",
    "conditional_click": "Tap an element only if a condition element is found.",
    "wait":              "Pause execution for a set number of seconds.",
    "swipe":             "Perform a swipe gesture between two points.",
    "input_text":        "Type text into the currently focused input field.",
    "press_key":         "Send a hardware key event (Back, Home, Enter …).",
}

ON_NOT_FOUND_OPTIONS   = ["stop", "skip", "continue"]
ON_CONDITION_OPTIONS   = ["skip", "stop", "continue"]
ELEMENT_TYPES          = {"click", "verify", "conditional_click"}

KEY_OPTIONS = [
    "BACK", "HOME", "MENU", "POWER",
    "VOLUME_UP", "VOLUME_DOWN",
    "ENTER", "DELETE", "TAB",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
    "APP_SWITCH", "CAMERA", "SEARCH",
]

# Mark-mode → rubber-band colour
MARK_COLOURS = {
    "region":      "#FF3333",
    "condition":   "#33AAFF",
    "action":      "#33FF66",
}

# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

def new_step(step_type: str) -> dict:
    """Return a new step dict with sensible defaults for every field."""
    return {
        "id":    str(uuid.uuid4())[:8],
        "type":  step_type,
        "label": STEP_LABELS[step_type],
        # ── element (click / verify) ──────────────────────────────────────
        "element_name":     "",
        "element_image":    "",
        "element_center_x": 0,
        "element_center_y": 0,
        "element_bounds":   {},
        "threshold":        0.80,
        "on_not_found":     "stop",
        # ── conditional_click ─────────────────────────────────────────────
        "condition_name":         "",
        "condition_image":        "",
        "condition_center_x":     0,
        "condition_center_y":     0,
        "condition_bounds":       {},
        "condition_threshold":    0.80,
        "action_same_as_cond":    False,
        "on_condition_false":     "skip",
        # ── wait ──────────────────────────────────────────────────────────
        "duration": 1.0,
        # ── swipe ─────────────────────────────────────────────────────────
        "swipe_x1": 0, "swipe_y1": 0,
        "swipe_x2": 0, "swipe_y2": 0,
        "swipe_duration_ms": 300,
        # ── input_text ────────────────────────────────────────────────────
        "text": "",
        # ── press_key ─────────────────────────────────────────────────────
        "key": "BACK",
        # ── timing (shared) ───────────────────────────────────────────────
        "wait_before": 0.0,
        "wait_after":  0.0,
    }

# ─────────────────────────────────────────────────────────────────────────────
# Registry / workflow I/O
# ─────────────────────────────────────────────────────────────────────────────

def load_simple_registry() -> dict:
    p = ELEMENTS_DIR / "registry.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_simple_registry(data: dict) -> None:
    ELEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    (ELEMENTS_DIR / "registry.json").write_text(json.dumps(data, indent=2))


def save_workflow(name: str, steps: list, elements: dict,
                  base_dir: Path = ELEMENTS_DIR) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    slug = name.strip().lower().replace(" ", "_")
    path = base_dir / f"registry_{slug}.json"
    path.write_text(json.dumps({
        "workflow_name": name,
        "saved":         datetime.now().isoformat(timespec="seconds"),
        "steps":         steps,
        "elements":      elements,
    }, indent=2))
    return path


def load_workflow(path: Path) -> dict:
    return json.loads(path.read_text())


def list_workflows() -> list:
    return sorted(ELEMENTS_DIR.glob("registry_*.json"))


# ─────────────────────────────────────────────────────────────────────────────
# Trainer Application
# ─────────────────────────────────────────────────────────────────────────────

class TrainerApp:
    # ──────────────────────────────────────────────────────────────────────
    # Init
    # ──────────────────────────────────────────────────────────────────────

    def __init__(self, screenshot_path: str) -> None:
        self.screenshot_path  = screenshot_path
        self.steps: list      = []
        self.elements: dict   = {}
        self._sel_idx: Optional[int] = None
        self._scale: float    = 1.0
        self._native_w        = 0
        self._native_h        = 0
        self._mark_mode: Optional[str] = None   # region | condition | action | swipe_start | swipe_end
        self._rb_start        = (0, 0)
        self._rb_rect         = None
        self._prop_widgets: dict = {}
        self._work_dir: Path  = ELEMENTS_DIR     # active folder for all I/O
        self._folder_var: Optional[tk.StringVar] = None  # combobox var (set in toolbar)

        self.root = tk.Tk()
        self.root.title("Android Automation Trainer")
        try:
            self.root.state("zoomed")
        except tk.TclError:
            self.root.attributes("-zoomed", True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._style()
        self._build_ui()
        self._load_screenshot(screenshot_path)
        self.root.mainloop()

    def _style(self) -> None:
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Header.TLabel", font=("Segoe UI", 9, "bold"))
        s.configure("Status.TLabel", relief="sunken", padding=(4, 1))
        s.configure("Mark.TButton", padding=(4, 2))

    # ──────────────────────────────────────────────────────────────────────
    # UI construction
    # ──────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_menu()
        self._build_toolbar()
        self._build_status_bar()

        paned = tk.PanedWindow(
            self.root, orient=tk.HORIZONTAL,
            sashwidth=5, sashrelief="raised", bg="#aaaaaa"
        )
        paned.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        left = ttk.Frame(paned, width=240)
        left.pack_propagate(False)
        self._build_step_list(left)
        paned.add(left, minsize=200)

        center = ttk.Frame(paned)
        self._build_canvas(center)
        paned.add(center, minsize=320)

        self._props_outer = ttk.Frame(paned, width=320)
        self._props_outer.pack_propagate(False)
        paned.add(self._props_outer, minsize=290)
        self._refresh_props()

    # ── Menu ───────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        mb = tk.Menu(self.root)
        self.root.config(menu=mb)

        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="New Workflow",       command=self._cmd_new)
        fm.add_command(label="Load Workflow…",     command=self._cmd_load)
        fm.add_separator()
        fm.add_command(label="Save Workflow…",     command=self._cmd_save_workflow)
        fm.add_command(label="Save Single Element",command=self._cmd_save_element)
        fm.add_separator()
        fm.add_command(label="Exit",               command=self.root.destroy)
        mb.add_cascade(label="File", menu=fm)

        sm = tk.Menu(mb, tearoff=0)
        sm.add_command(label="Add Step",    command=self._cmd_add_step)
        sm.add_command(label="Remove Step", command=self._cmd_remove_step)
        sm.add_command(label="Move Up",     command=self._cmd_move_up)
        sm.add_command(label="Move Down",   command=self._cmd_move_down)
        sm.add_command(label="Duplicate",   command=self._cmd_duplicate)
        mb.add_cascade(label="Steps", menu=sm)

        dm = tk.Menu(mb, tearoff=0)
        dm.add_command(label="Capture Screenshot",   command=self._cmd_capture)
        dm.add_command(label="Clear Canvas Overlays",command=self._cmd_clear_overlays)
        dm.add_separator()
        dm.add_command(label="List Elements",        command=self._cmd_list_elements)
        mb.add_cascade(label="Device", menu=dm)

        vm = tk.Menu(mb, tearoff=0)
        vm.add_command(label="Browse Folder…",       command=self._cmd_browse_folder)
        vm.add_command(label="Refresh Quick-Load",   command=self._refresh_folder_combobox)
        mb.add_cascade(label="Folder", menu=vm)

    # ── Toolbar ────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        tb = ttk.Frame(self.root, relief="raised")
        tb.pack(fill="x", padx=4, pady=(4, 0))

        buttons = [
            ("📷 Capture",      self._cmd_capture),
            None,
            ("+ Step",          self._cmd_add_step),
            ("⎘ Duplicate",     self._cmd_duplicate),
            ("✕ Remove",        self._cmd_remove_step),
            ("▲ Up",            self._cmd_move_up),
            ("▼ Down",          self._cmd_move_down),
            None,
            ("💾 Save Workflow", self._cmd_save_workflow),
            ("📌 Save Element",  self._cmd_save_element),
            ("📂 Load",          self._cmd_load),
            ("📁 Folder",        self._cmd_browse_folder),
            None,
            ("🗑 Clear All",     self._cmd_new),
        ]
        for item in buttons:
            if item is None:
                ttk.Separator(tb, orient="vertical").pack(
                    side="left", padx=4, pady=2, fill="y"
                )
            else:
                text, cmd = item
                ttk.Button(tb, text=text, command=cmd).pack(
                    side="left", padx=2, pady=2
                )

        # Quick-Load combobox — right side of toolbar
        ttk.Separator(tb, orient="vertical").pack(
            side="left", padx=4, pady=2, fill="y"
        )
        ttk.Label(tb, text="Quick Load:").pack(side="left", padx=(2, 2))
        self._folder_var = tk.StringVar()
        self._ql_combo = ttk.Combobox(
            tb, textvariable=self._folder_var,
            state="readonly", width=32
        )
        self._ql_combo.pack(side="left", padx=2, pady=2)
        self._ql_combo.bind("<<ComboboxSelected>>", self._on_quick_load)
        self._refresh_folder_combobox()

    # ── Status bar ─────────────────────────────────────────────────────────

    def _build_status_bar(self) -> None:
        self._status_var = tk.StringVar(value="Ready")
        ttk.Label(
            self.root, textvariable=self._status_var,
            style="Status.TLabel", anchor="w"
        ).pack(fill="x", side="bottom", padx=4, pady=(0, 4))

    # ── Step list (left panel) ──────────────────────────────────────────────

    def _build_step_list(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="WORKFLOW STEPS", style="Header.TLabel").pack(
            fill="x", padx=6, pady=(6, 2)
        )

        fr = ttk.Frame(parent)
        fr.pack(fill="both", expand=True, padx=4)

        tv = ttk.Treeview(
            fr, columns=("num", "type", "label"),
            show="headings", selectmode="browse"
        )
        tv.heading("num",   text="#",     anchor="center")
        tv.heading("type",  text="Type",  anchor="w")
        tv.heading("label", text="Label", anchor="w")
        tv.column("num",   width=28,  stretch=False, anchor="center")
        tv.column("type",  width=88,  stretch=False)
        tv.column("label", width=110, stretch=True)

        sb = ttk.Scrollbar(fr, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tv.bind("<<TreeviewSelect>>", self._on_step_select)
        tv.bind("<Button-3>",         self._on_step_right_click)   # right-click menu
        self._step_tv = tv

        # Mini-toolbar below list
        btns = ttk.Frame(parent)
        btns.pack(fill="x", padx=4, pady=2)
        for text, cmd in (("+ Add", self._cmd_add_step),
                          ("✕",    self._cmd_remove_step),
                          ("▲",    self._cmd_move_up),
                          ("▼",    self._cmd_move_down)):
            ttk.Button(btns, text=text, command=cmd, width=5).pack(side="left", padx=1)

    # ── Canvas (centre panel) ───────────────────────────────────────────────

    def _build_canvas(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="DEVICE SCREENSHOT", style="Header.TLabel").pack(
            fill="x", padx=6, pady=(6, 2)
        )

        # Mark-mode action bar
        mf = ttk.Frame(parent)
        mf.pack(fill="x", padx=4, pady=(0, 2))
        ttk.Label(mf, text="Mark:").pack(side="left", padx=(0, 4))

        def mk_btn(text, mode):
            return ttk.Button(
                mf, text=text, style="Mark.TButton",
                command=lambda: self._set_mark_mode(mode),
                state="disabled"
            )

        self._btn_region  = mk_btn("📐 Region",      "region")
        self._btn_cond    = mk_btn("🔎 Condition",   "condition")
        self._btn_action  = mk_btn("🖱 Action",      "action")
        self._btn_sw_s    = mk_btn("● Swipe Start",  "swipe_start")
        self._btn_sw_e    = mk_btn("○ Swipe End",    "swipe_end")
        for b in (self._btn_region, self._btn_cond, self._btn_action,
                  self._btn_sw_s, self._btn_sw_e):
            b.pack(side="left", padx=2)

        # Canvas + scrollbars
        cf = ttk.Frame(parent)
        cf.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._canvas = tk.Canvas(cf, cursor="arrow", bg="#1a1a2e",
                                 highlightthickness=0)
        hbar = ttk.Scrollbar(cf, orient="horizontal", command=self._canvas.xview)
        vbar = ttk.Scrollbar(cf, orient="vertical",   command=self._canvas.yview)
        self._canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        hbar.pack(side="bottom", fill="x")
        vbar.pack(side="right",  fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._canvas.bind("<ButtonPress-1>",  self._on_canvas_press)
        self._canvas.bind("<B1-Motion>",       self._on_canvas_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self._canvas.bind("<Motion>",          self._on_canvas_motion)

    # ──────────────────────────────────────────────────────────────────────
    # Screenshot loading
    # ──────────────────────────────────────────────────────────────────────

    def _load_screenshot(self, path: str) -> None:
        img = Image.open(path)
        self._native_w, self._native_h = img.width, img.height
        self._scale = min(820 / img.width, 720 / img.height, 1.0)
        dw = int(img.width  * self._scale)
        dh = int(img.height * self._scale)
        disp = img.resize((dw, dh), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(disp)
        self._canvas.delete("all")
        self._canvas.create_image(0, 0, anchor="nw", image=self._photo,
                                  tags="screenshot")
        self._canvas.configure(scrollregion=(0, 0, dw, dh))
        self._canvas.configure(cursor="arrow")
        self._set_status(
            f"Screenshot: {self._native_w}×{self._native_h} px  "
            f"(display scale {self._scale:.2f}×)"
        )

    # ──────────────────────────────────────────────────────────────────────
    # Canvas event handlers
    # ──────────────────────────────────────────────────────────────────────

    def _on_canvas_press(self, event: tk.Event) -> None:
        if self._mark_mode is None:
            return
        self._rb_start = (event.x, event.y)
        if self._rb_rect:
            self._canvas.delete(self._rb_rect)
            self._rb_rect = None

    def _on_canvas_drag(self, event: tk.Event) -> None:
        if self._mark_mode not in MARK_COLOURS:
            return
        if self._rb_rect:
            self._canvas.delete(self._rb_rect)
        colour = MARK_COLOURS[self._mark_mode]
        self._rb_rect = self._canvas.create_rectangle(
            *self._rb_start, event.x, event.y,
            outline=colour, width=2, dash=(4, 2)
        )

    def _on_canvas_release(self, event: tk.Event) -> None:
        mode = self._mark_mode
        if mode is None:
            return

        if mode in MARK_COLOURS:                     # rubber-band → snip
            x1 = min(self._rb_start[0], event.x)
            y1 = min(self._rb_start[1], event.y)
            x2 = max(self._rb_start[0], event.x)
            y2 = max(self._rb_start[1], event.y)
            if (x2 - x1) < 5 or (y2 - y1) < 5:
                self._set_status("Selection too small — draw a larger rectangle.")
                return
            self._commit_region(mode, x1, y1, x2, y2)

        elif mode in ("swipe_start", "swipe_end"):   # single click → point
            step = self._current_step()
            if step is None:
                return
            nx = int(event.x / self._scale)
            ny = int(event.y / self._scale)
            label = "S" if mode == "swipe_start" else "E"
            colour = "#FF9933" if mode == "swipe_start" else "#FF3333"
            if mode == "swipe_start":
                step["swipe_x1"], step["swipe_y1"] = nx, ny
            else:
                step["swipe_x2"], step["swipe_y2"] = nx, ny
            self._draw_point_marker(event.x, event.y, label, colour)
            self._set_mark_mode(None)
            self._refresh_props()
            self._set_status(f"Swipe {label} set: ({nx}, {ny})")

    def _on_canvas_motion(self, event: tk.Event) -> None:
        if self._mark_mode:
            nx = int(event.x / self._scale)
            ny = int(event.y / self._scale)
            self._status_var.set(
                f"Mode: {self._mark_mode}  |  device pixel ({nx}, {ny})"
            )

    # ── Region commit ──────────────────────────────────────────────────────

    def _commit_region(self, mode: str, x1: int, y1: int,
                       x2: int, y2: int) -> None:
        step = self._current_step()
        if step is None:
            return

        # Scale display coords → native device coords
        nx1, ny1 = int(x1 / self._scale), int(y1 / self._scale)
        nx2, ny2 = int(x2 / self._scale), int(y2 / self._scale)
        cx, cy   = (nx1 + nx2) // 2, (ny1 + ny2) // 2

        # Build snip filename
        slug   = step["label"].lower().replace(" ", "_")
        suffix = {"region": "", "condition": "_cond", "action": "_act"}[mode]
        self._work_dir.mkdir(parents=True, exist_ok=True)
        img_path = self._work_dir / f"{slug}{suffix}_{step['id']}.png"
        Image.open(self.screenshot_path).crop((nx1, ny1, nx2, ny2)).save(img_path)

        elem_data = {
            "image": str(img_path), "center_x": cx, "center_y": cy,
            "bounds": {"x1": nx1, "y1": ny1, "x2": nx2, "y2": ny2},
        }

        if mode == "region":
            step["element_name"]     = step["label"]
            step["element_image"]    = str(img_path)
            step["element_center_x"] = cx
            step["element_center_y"] = cy
            step["element_bounds"]   = elem_data["bounds"]
            self.elements[step["element_name"]] = elem_data

        elif mode == "condition":
            step["condition_name"]      = f"{step['label']}_cond"
            step["condition_image"]     = str(img_path)
            step["condition_center_x"]  = cx
            step["condition_center_y"]  = cy
            step["condition_bounds"]    = elem_data["bounds"]
            self.elements[step["condition_name"]] = elem_data

        elif mode == "action":
            step["element_name"]     = f"{step['label']}_act"
            step["element_image"]    = str(img_path)
            step["element_center_x"] = cx
            step["element_center_y"] = cy
            step["element_bounds"]   = elem_data["bounds"]
            self.elements[step["element_name"]] = elem_data

        # Draw overlay on canvas
        colour = MARK_COLOURS[mode]
        self._canvas.create_rectangle(
            x1, y1, x2, y2, outline=colour, width=2, tags="overlay"
        )
        self._canvas.create_text(
            (x1 + x2) // 2, (y1 + y2) // 2,
            text=f"({cx},{cy})", fill=colour,
            font=("Segoe UI", 8, "bold"), tags="overlay"
        )
        self._canvas.create_text(
            x1 + 3, y1 + 3, anchor="nw",
            text=mode, fill=colour,
            font=("Segoe UI", 7), tags="overlay"
        )

        self._set_mark_mode(None)
        self._update_step_list()
        self._refresh_props()
        self._set_status(
            f"[{mode}] snip saved → {img_path.name}  centre=({cx}, {cy})"
        )

    def _draw_point_marker(self, cx: int, cy: int,
                           label: str, colour: str) -> None:
        r = 9
        self._canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=colour, outline="white", width=2, tags="overlay"
        )
        self._canvas.create_text(
            cx, cy, text=label, fill="white",
            font=("Segoe UI", 8, "bold"), tags="overlay"
        )

    # ──────────────────────────────────────────────────────────────────────
    # Mark mode
    # ──────────────────────────────────────────────────────────────────────

    def _set_mark_mode(self, mode: Optional[str]) -> None:
        self._mark_mode = mode
        self._canvas.configure(cursor="crosshair" if mode else "arrow")
        if mode is None:
            self._set_status("Ready")

    def _update_mark_buttons(self) -> None:
        step = self._current_step()
        t    = step["type"] if step else None
        en   = "normal"
        dis  = "disabled"

        self._btn_region.configure(
            state=(en if t in ("click", "verify") else dis))
        self._btn_cond.configure(
            state=(en if t == "conditional_click" else dis))
        self._btn_action.configure(
            state=(en if t == "conditional_click"
                   and not (step or {}).get("action_same_as_cond") else dis))
        self._btn_sw_s.configure(
            state=(en if t == "swipe" else dis))
        self._btn_sw_e.configure(
            state=(en if t == "swipe" else dis))

    # ──────────────────────────────────────────────────────────────────────
    # Step list management
    # ──────────────────────────────────────────────────────────────────────

    def _on_step_select(self, _event: tk.Event) -> None:
        sel = self._step_tv.selection()
        if not sel:
            return
        row = self._step_tv.item(sel[0])["values"]
        self._sel_idx = int(row[0]) - 1
        self._update_mark_buttons()
        self._refresh_props()

    def _current_step(self) -> Optional[dict]:
        if self._sel_idx is None or self._sel_idx >= len(self.steps):
            return None
        return self.steps[self._sel_idx]

    def _update_step_list(self) -> None:
        self._step_tv.delete(*self._step_tv.get_children())
        for i, s in enumerate(self.steps, 1):
            self._step_tv.insert("", "end", values=(i, s["type"], s["label"]))
        children = self._step_tv.get_children()
        if children and self._sel_idx is not None:
            idx = min(self._sel_idx, len(children) - 1)
            self._step_tv.selection_set(children[idx])
            self._step_tv.see(children[idx])

    # ──────────────────────────────────────────────────────────────────────
    # Properties panel
    # ──────────────────────────────────────────────────────────────────────

    def _refresh_props(self) -> None:
        for w in self._props_outer.winfo_children():
            w.destroy()
        self._prop_widgets = {}

        step = self._current_step()
        if step is None:
            ttk.Label(
                self._props_outer,
                text="Select a step\nto view its properties.",
                justify="center", foreground="#777777",
            ).pack(expand=True)
            return

        # Scrollable inner frame
        canvas = tk.Canvas(self._props_outer, highlightthickness=0)
        sb     = ttk.Scrollbar(self._props_outer, orient="vertical",
                               command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = ttk.Frame(canvas)
        cwin  = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(cwin, width=e.width))

        P   = inner
        PAD = {"padx": 8, "pady": 3}

        # ── Header ─────────────────────────────────────────────────────
        ttk.Label(P, text="STEP PROPERTIES",
                  style="Header.TLabel").pack(anchor="w", **PAD)
        ttk.Separator(P, orient="horizontal").pack(fill="x", padx=8, pady=2)

        # ── Common: Label ──────────────────────────────────────────────
        f = ttk.Frame(P); f.pack(fill="x", **PAD)
        ttk.Label(f, text="Label:", width=15).pack(side="left")
        lv = tk.StringVar(value=step["label"])
        ttk.Entry(f, textvariable=lv).pack(side="left", fill="x", expand=True)

        # ── Common: Type ───────────────────────────────────────────────
        f = ttk.Frame(P); f.pack(fill="x", **PAD)
        ttk.Label(f, text="Step Type:", width=15).pack(side="left")
        tv_var = tk.StringVar(value=step["type"])
        ttk.Combobox(
            f, textvariable=tv_var, values=STEP_TYPES,
            state="readonly", width=20
        ).pack(side="left")
        ttk.Separator(P, orient="horizontal").pack(fill="x", padx=8, pady=6)

        # ── Type-specific ──────────────────────────────────────────────
        t = step["type"]
        if t in ("click", "verify"):
            self._props_click_verify(P, step, PAD)
        elif t == "conditional_click":
            self._props_conditional(P, step, PAD)
        elif t == "wait":
            self._props_wait(P, step, PAD)
        elif t == "swipe":
            self._props_swipe(P, step, PAD)
        elif t == "input_text":
            self._props_input_text(P, step, PAD)
        elif t == "press_key":
            self._props_press_key(P, step, PAD)

        # ── Timing (shared) ────────────────────────────────────────────
        ttk.Separator(P, orient="horizontal").pack(fill="x", padx=8, pady=6)
        ttk.Label(P, text="Timing", style="Header.TLabel").pack(anchor="w", **PAD)
        wb_var = tk.DoubleVar(value=step.get("wait_before", 0.0))
        wa_var = tk.DoubleVar(value=step.get("wait_after",  0.0))
        for label_text, var in (("Wait Before (s):", wb_var),
                                ("Wait After (s):",  wa_var)):
            f = ttk.Frame(P); f.pack(fill="x", **PAD)
            ttk.Label(f, text=label_text, width=16).pack(side="left")
            ttk.Spinbox(f, textvariable=var,
                        from_=0.0, to=60.0, increment=0.5,
                        width=8, format="%.1f").pack(side="left")

        # ── Apply ──────────────────────────────────────────────────────
        ttk.Separator(P, orient="horizontal").pack(fill="x", padx=8, pady=6)

        captured_t = t   # capture for closure

        def _apply() -> None:
            step["label"]       = lv.get().strip() or step["label"]
            step["wait_before"] = wb_var.get()
            step["wait_after"]  = wa_var.get()
            new_type = tv_var.get()
            if new_type == captured_t:
                self._apply_type_fields(step, captured_t)
            else:
                # Type changed: reset type-specific fields to defaults
                defaults = new_step(new_type)
                for k in defaults:
                    if k not in ("id", "label", "wait_before", "wait_after"):
                        step[k] = defaults[k]
                step["type"] = new_type
            self._update_step_list()
            self._update_mark_buttons()
            self._refresh_props()
            self._set_status(f"Updated: {step['label']}")

        ttk.Button(P, text="✔  Apply Changes", command=_apply).pack(
            pady=8, padx=8, fill="x"
        )

        self._prop_widgets.update({
            "lv": lv, "tv_var": tv_var,
            "wb_var": wb_var, "wa_var": wa_var,
        })

    # ── Props: click / verify ───────────────────────────────────────────────

    def _props_click_verify(self, P, step: dict, PAD: dict) -> None:
        lf = ttk.LabelFrame(P, text="Target Element", padding=6)
        lf.pack(fill="x", padx=8, pady=4)

        nv = tk.StringVar(value=step.get("element_name", ""))
        f = ttk.Frame(lf); f.pack(fill="x", pady=2)
        ttk.Label(f, text="Name:", width=12).pack(side="left")
        ttk.Entry(f, textvariable=nv, width=22).pack(
            side="left", fill="x", expand=True)

        img = step.get("element_image", "")
        cx  = step.get("element_center_x", 0)
        cy  = step.get("element_center_y", 0)
        ok  = bool(img)
        sr  = ttk.Frame(lf); sr.pack(fill="x", pady=(2, 4))
        ttk.Label(
            sr,
            text=(f"✓  {Path(img).name}   centre=({cx}, {cy})"
                  if ok else "⚠  Not set — use  📐 Region  button above"),
            foreground="#006600" if ok else "#994400",
            wraplength=220, justify="left",
        ).pack(side="left", fill="x", expand=True)
        if ok:
            ttk.Button(
                sr, text="✕ Clear", width=8,
                command=lambda s=step: self._cmd_clear_element(s)
            ).pack(side="right", padx=(4, 0))

        lf2 = ttk.LabelFrame(P, text="Match Settings", padding=6)
        lf2.pack(fill="x", padx=8, pady=4)

        thr_var = tk.DoubleVar(value=step.get("threshold", 0.80))
        onf_var = tk.StringVar(value=step.get("on_not_found", "stop"))

        f = ttk.Frame(lf2); f.pack(fill="x", pady=2)
        ttk.Label(f, text="Threshold:", width=14).pack(side="left")
        ttk.Spinbox(f, textvariable=thr_var,
                    from_=0.10, to=1.0, increment=0.05,
                    width=8, format="%.2f").pack(side="left")
        ttk.Label(lf2, text="(0.80 = 80% similarity required)",
                  foreground="#666666").pack(anchor="w")

        f = ttk.Frame(lf2); f.pack(fill="x", pady=2)
        ttk.Label(f, text="On Not Found:", width=14).pack(side="left")
        ttk.Combobox(f, textvariable=onf_var,
                     values=ON_NOT_FOUND_OPTIONS,
                     state="readonly", width=12).pack(side="left")
        ttk.Label(lf2,
                  text="stop = abort workflow\nskip = skip this step\ncontinue = proceed anyway",
                  foreground="#666666", justify="left").pack(anchor="w")

        self._prop_widgets.update({
            "elem_nv": nv, "thr_var": thr_var, "onf_var": onf_var
        })

    # ── Props: conditional_click ────────────────────────────────────────────

    def _props_conditional(self, P, step: dict, PAD: dict) -> None:
        # Condition section
        lf1 = ttk.LabelFrame(P, text="Condition  (check this first)", padding=6)
        lf1.pack(fill="x", padx=8, pady=4)

        cn_var = tk.StringVar(value=step.get("condition_name", ""))
        f = ttk.Frame(lf1); f.pack(fill="x", pady=2)
        ttk.Label(f, text="Name:", width=12).pack(side="left")
        ttk.Entry(f, textvariable=cn_var, width=22).pack(
            side="left", fill="x", expand=True)

        ci = step.get("condition_image", "")
        cx = step.get("condition_center_x", 0)
        cy = step.get("condition_center_y", 0)
        cr = ttk.Frame(lf1); cr.pack(fill="x", pady=(2, 2))
        ttk.Label(cr,
                  text=(f"✓  {Path(ci).name}  ({cx},{cy})"
                        if ci else "⚠  Use  🔎 Condition  button"),
                  foreground="#006600" if ci else "#994400").pack(side="left", fill="x", expand=True)
        if ci:
            ttk.Button(
                cr, text="✕ Clear", width=8,
                command=lambda s=step: self._cmd_clear_condition(s)
            ).pack(side="right", padx=(4, 0))

        ct_var = tk.DoubleVar(value=step.get("condition_threshold", 0.80))
        f = ttk.Frame(lf1); f.pack(fill="x", pady=2)
        ttk.Label(f, text="Threshold:", width=12).pack(side="left")
        ttk.Spinbox(f, textvariable=ct_var,
                    from_=0.1, to=1.0, increment=0.05,
                    width=8, format="%.2f").pack(side="left")

        same_var = tk.BooleanVar(value=step.get("action_same_as_cond", False))
        ttk.Checkbutton(lf1, text="Click same element (no separate action region)",
                        variable=same_var).pack(anchor="w", pady=4)

        # Action section
        lf2 = ttk.LabelFrame(P, text="Action  (click this when condition met)", padding=6)
        lf2.pack(fill="x", padx=8, pady=4)

        an_var = tk.StringVar(value=step.get("element_name", ""))
        f = ttk.Frame(lf2); f.pack(fill="x", pady=2)
        ttk.Label(f, text="Name:", width=12).pack(side="left")
        ttk.Entry(f, textvariable=an_var, width=22).pack(
            side="left", fill="x", expand=True)

        ai = step.get("element_image", "")
        ax = step.get("element_center_x", 0)
        ay = step.get("element_center_y", 0)
        ar = ttk.Frame(lf2); ar.pack(fill="x", pady=(2, 2))
        ttk.Label(ar,
                  text=(f"✓  {Path(ai).name}  ({ax},{ay})"
                        if ai else "⚠  Use  🖱 Action  button (or check 'same element' above)"),
                  foreground="#006600" if ai else "#994400",
                  wraplength=220, justify="left").pack(side="left", fill="x", expand=True)
        if ai:
            ttk.Button(
                ar, text="✕ Clear", width=8,
                command=lambda s=step: self._cmd_clear_action(s)
            ).pack(side="right", padx=(4, 0))

        at_var  = tk.DoubleVar(value=step.get("threshold", 0.80))
        ocf_var = tk.StringVar(value=step.get("on_condition_false", "skip"))

        f = ttk.Frame(lf2); f.pack(fill="x", pady=2)
        ttk.Label(f, text="Threshold:", width=14).pack(side="left")
        ttk.Spinbox(f, textvariable=at_var,
                    from_=0.1, to=1.0, increment=0.05,
                    width=8, format="%.2f").pack(side="left")

        f = ttk.Frame(lf2); f.pack(fill="x", pady=2)
        ttk.Label(f, text="On Cond False:", width=14).pack(side="left")
        ttk.Combobox(f, textvariable=ocf_var,
                     values=ON_CONDITION_OPTIONS,
                     state="readonly", width=12).pack(side="left")

        self._prop_widgets.update({
            "cn_var": cn_var, "ct_var": ct_var, "same_var": same_var,
            "an_var": an_var, "at_var": at_var, "ocf_var": ocf_var,
        })

    # ── Props: wait ────────────────────────────────────────────────────────

    def _props_wait(self, P, step: dict, PAD: dict) -> None:
        lf = ttk.LabelFrame(P, text="Wait Duration", padding=6)
        lf.pack(fill="x", padx=8, pady=4)
        dv = tk.DoubleVar(value=step.get("duration", 1.0))
        f = ttk.Frame(lf); f.pack(fill="x", pady=2)
        ttk.Label(f, text="Seconds:", width=10).pack(side="left")
        ttk.Spinbox(f, textvariable=dv, from_=0.1, to=300.0,
                    increment=0.5, width=8, format="%.1f").pack(side="left")
        self._prop_widgets["dur_var"] = dv

    # ── Props: swipe ───────────────────────────────────────────────────────

    def _props_swipe(self, P, step: dict, PAD: dict) -> None:
        lf = ttk.LabelFrame(P, text="Swipe Gesture", padding=6)
        lf.pack(fill="x", padx=8, pady=4)

        x1, y1 = step.get("swipe_x1", 0), step.get("swipe_y1", 0)
        x2, y2 = step.get("swipe_x2", 0), step.get("swipe_y2", 0)
        has_pts = (x1, y1, x2, y2) != (0, 0, 0, 0)
        sr = ttk.Frame(lf); sr.pack(fill="x", pady=(0, 2))
        info = ttk.Frame(sr); info.pack(side="left", fill="x", expand=True)
        ttk.Label(info, text=f"Start: ({x1}, {y1})").pack(anchor="w")
        ttk.Label(info, text=f"End:   ({x2}, {y2})").pack(anchor="w")
        if has_pts:
            ttk.Button(
                sr, text="✕ Reset", width=8,
                command=lambda s=step: self._cmd_clear_swipe(s)
            ).pack(side="right", anchor="n", padx=(4, 0))
        ttk.Label(lf,
                  text="Use  ● Swipe Start  then  ○ Swipe End  buttons above.",
                  foreground="#555555", wraplength=260).pack(anchor="w", pady=4)

        dv = tk.IntVar(value=step.get("swipe_duration_ms", 300))
        f = ttk.Frame(lf); f.pack(fill="x", pady=2)
        ttk.Label(f, text="Duration (ms):", width=14).pack(side="left")
        ttk.Spinbox(f, textvariable=dv, from_=50, to=10000,
                    increment=50, width=8).pack(side="left")
        self._prop_widgets["swipe_dur_var"] = dv

    # ── Props: input_text ──────────────────────────────────────────────────

    def _props_input_text(self, P, step: dict, PAD: dict) -> None:
        lf = ttk.LabelFrame(P, text="Text to Type", padding=6)
        lf.pack(fill="x", padx=8, pady=4)
        tv = tk.StringVar(value=step.get("text", ""))
        ttk.Entry(lf, textvariable=tv, width=32).pack(fill="x", pady=2)
        ttk.Label(lf, text="Note: spaces are sent as %s via ADB.",
                  foreground="#555555").pack(anchor="w")
        self._prop_widgets["text_var"] = tv

    # ── Props: press_key ───────────────────────────────────────────────────

    def _props_press_key(self, P, step: dict, PAD: dict) -> None:
        lf = ttk.LabelFrame(P, text="Hardware Key Event", padding=6)
        lf.pack(fill="x", padx=8, pady=4)
        kv = tk.StringVar(value=step.get("key", "BACK"))
        ttk.Combobox(lf, textvariable=kv, values=KEY_OPTIONS,
                     state="readonly", width=22).pack(pady=2)
        self._prop_widgets["key_var"] = kv

    # ── Apply type-specific fields back to step dict ────────────────────────

    def _apply_type_fields(self, step: dict, t: str) -> None:
        w = self._prop_widgets
        if t in ("click", "verify"):
            if "elem_nv"  in w: step["element_name"] = w["elem_nv"].get().strip() or step["element_name"]
            if "thr_var"  in w: step["threshold"]    = w["thr_var"].get()
            if "onf_var"  in w: step["on_not_found"] = w["onf_var"].get()
        elif t == "conditional_click":
            if "cn_var"   in w: step["condition_name"]      = w["cn_var"].get()
            if "ct_var"   in w: step["condition_threshold"] = w["ct_var"].get()
            if "same_var" in w: step["action_same_as_cond"] = w["same_var"].get()
            if "an_var"   in w: step["element_name"]        = w["an_var"].get()
            if "at_var"   in w: step["threshold"]           = w["at_var"].get()
            if "ocf_var"  in w: step["on_condition_false"]  = w["ocf_var"].get()
        elif t == "wait":
            if "dur_var"      in w: step["duration"]          = w["dur_var"].get()
        elif t == "swipe":
            if "swipe_dur_var" in w: step["swipe_duration_ms"] = w["swipe_dur_var"].get()
        elif t == "input_text":
            if "text_var" in w: step["text"] = w["text_var"].get()
        elif t == "press_key":
            if "key_var"  in w: step["key"]  = w["key_var"].get()

    # ──────────────────────────────────────────────────────────────────────
    # Command handlers
    # ──────────────────────────────────────────────────────────────────────

    def _cmd_capture(self) -> None:
        self._set_status("Capturing screenshot from device…")
        self.root.update()
        try:
            capture_screenshot(TEMP_SHOT)
            self.screenshot_path = TEMP_SHOT
            self._canvas.delete("overlay")
            self._load_screenshot(TEMP_SHOT)
        except SystemExit as exc:
            messagebox.showerror("ADB Error", str(exc))

    def _cmd_add_step(self) -> None:
        dlg = _TypePickerDialog(self.root)
        if dlg.result is None:
            return
        step = new_step(dlg.result)
        self.steps.append(step)
        self._sel_idx = len(self.steps) - 1
        self._update_step_list()
        self._update_mark_buttons()
        self._refresh_props()
        self._set_status(f"Added: {step['label']}")

    def _cmd_remove_step(self) -> None:
        if self._sel_idx is None or not self.steps:
            return
        removed = self.steps.pop(self._sel_idx)
        self._sel_idx = (
            min(self._sel_idx, len(self.steps) - 1) if self.steps else None
        )
        self._update_step_list()
        self._update_mark_buttons()
        self._refresh_props()
        self._set_status(f"Removed: {removed['label']}")

    def _cmd_duplicate(self) -> None:
        step = self._current_step()
        if step is None:
            return
        import copy
        dup = copy.deepcopy(step)
        dup["id"]    = str(uuid.uuid4())[:8]
        dup["label"] = step["label"] + " (copy)"
        self.steps.insert(self._sel_idx + 1, dup)
        self._sel_idx += 1
        self._update_step_list()
        self._refresh_props()
        self._set_status(f"Duplicated: {step['label']}")

    def _cmd_move_up(self) -> None:
        idx = self._sel_idx
        if idx is None or idx == 0:
            return
        self.steps[idx], self.steps[idx - 1] = self.steps[idx - 1], self.steps[idx]
        self._sel_idx = idx - 1
        self._update_step_list()

    def _cmd_move_down(self) -> None:
        idx = self._sel_idx
        if idx is None or idx >= len(self.steps) - 1:
            return
        self.steps[idx], self.steps[idx + 1] = self.steps[idx + 1], self.steps[idx]
        self._sel_idx = idx + 1
        self._update_step_list()

    def _cmd_new(self) -> None:
        if self.steps and not messagebox.askyesno(
                "Clear All", "Discard all steps and start fresh?"):
            return
        self.steps.clear()
        self.elements.clear()
        self._sel_idx = None
        self._canvas.delete("overlay")
        self._update_step_list()
        self._update_mark_buttons()
        self._refresh_props()
        self._set_status("Cleared.")

    def _cmd_clear_overlays(self) -> None:
        self._canvas.delete("overlay")
        self._set_status("Canvas overlays cleared.")

    def _cmd_save_workflow(self) -> None:
        if not self.steps:
            messagebox.showwarning("Empty", "Add at least one step before saving.")
            return
        name = simpledialog.askstring(
            "Workflow Name", "Enter a name for this workflow:",
            parent=self.root
        )
        if not name or not name.strip():
            return
        path = save_workflow(name.strip(), self.steps, self.elements,
                             base_dir=self._work_dir)
        messagebox.showinfo("Saved", f"Workflow saved:\n{path}")
        self._refresh_folder_combobox()
        self._set_status(f"Saved workflow → {path.name}")

    def _cmd_save_element(self) -> None:
        step = self._current_step()
        if step is None:
            messagebox.showwarning("No Step", "Select a step first.")
            return
        if not step.get("element_image"):
            messagebox.showwarning(
                "No Region",
                "Mark a region on the canvas first (📐 Region button)."
            )
            return
        name = step.get("element_name") or step["label"]
        reg_path = self._work_dir / "registry.json"
        reg  = json.loads(reg_path.read_text()) if reg_path.exists() else {}
        reg[name] = {
            "image":    step["element_image"],
            "center_x": step["element_center_x"],
            "center_y": step["element_center_y"],
            "bounds":   step["element_bounds"],
        }
        self._work_dir.mkdir(parents=True, exist_ok=True)
        reg_path.write_text(json.dumps(reg, indent=2))
        messagebox.showinfo("Saved", f"Element '{name}' → {reg_path}")
        self._refresh_folder_combobox()
        self._set_status(f"Element saved: {name}")

    def _cmd_load(self) -> None:
        paths = sorted(self._work_dir.glob("registry_*.json"))
        if not paths:
            messagebox.showinfo(
                "No Workflows",
                f"No workflow files found in:\n{self._work_dir}\n\n"
                "Save a workflow first, or use  📁 Folder  to browse another directory."
            )
            return
        dlg = _WorkflowPickerDialog(self.root, paths)
        if dlg.result is None:
            return
        wf = load_workflow(dlg.result)
        self.steps    = wf.get("steps", [])
        self.elements = wf.get("elements", {})
        self._sel_idx = 0 if self.steps else None
        self._canvas.delete("overlay")
        self._update_step_list()
        self._update_mark_buttons()
        self._refresh_props()
        self._set_status(
            f"Loaded: {wf.get('workflow_name', dlg.result.name)} "
            f"({len(self.steps)} steps)"
        )

    def _cmd_list_elements(self) -> None:
        reg_path = self._work_dir / "registry.json"
        reg  = json.loads(reg_path.read_text()) if reg_path.exists() else {}
        lines = [
            f"{nm}: centre=({e['center_x']}, {e['center_y']})"
            for nm, e in reg.items()
        ]
        messagebox.showinfo(
            f"Elements in {reg_path.parent.name}/registry.json",
            "\n".join(lines) if lines else "registry.json is empty."
        )

    # ──────────────────────────────────────────────────────────────────────
    # Step right-click context menu
    # ──────────────────────────────────────────────────────────────────────

    def _on_step_right_click(self, event: tk.Event) -> None:
        """Select the row under the cursor then show the context menu."""
        iid = self._step_tv.identify_row(event.y)
        if iid:
            self._step_tv.selection_set(iid)
            row = self._step_tv.item(iid)["values"]
            self._sel_idx = int(row[0]) - 1
            self._update_mark_buttons()
            self._refresh_props()
        self._show_step_context_menu(event)

    def _show_step_context_menu(self, event: tk.Event) -> None:
        step = self._current_step()
        menu = tk.Menu(self.root, tearoff=0)

        if step:
            t = step["type"]
            menu.add_command(
                label=f'✕  Delete  "{step["label"]}"',
                command=self._cmd_remove_step
            )
            menu.add_separator()

            # Clear element region
            has_elem = bool(step.get("element_image"))
            menu.add_command(
                label="🗑  Clear Element Region",
                command=lambda: self._cmd_clear_element(step),
                state="normal" if has_elem else "disabled"
            )

            # Clear condition / action (conditional_click only)
            has_cond = bool(step.get("condition_image"))
            has_act  = bool(step.get("element_image") and t == "conditional_click")
            menu.add_command(
                label="🗑  Clear Condition Region",
                command=lambda: self._cmd_clear_condition(step),
                state="normal" if has_cond else "disabled"
            )
            menu.add_command(
                label="🗑  Clear Action Region",
                command=lambda: self._cmd_clear_action(step),
                state="normal" if has_act else "disabled"
            )

            # Clear swipe points (swipe only)
            has_swipe = (t == "swipe" and
                         (step.get("swipe_x1") or step.get("swipe_y1") or
                          step.get("swipe_x2") or step.get("swipe_y2")))
            menu.add_command(
                label="🗑  Reset Swipe Points",
                command=lambda: self._cmd_clear_swipe(step),
                state="normal" if has_swipe else "disabled"
            )

            menu.add_separator()
            menu.add_command(label="⎘  Duplicate Step", command=self._cmd_duplicate)
            menu.add_separator()
            menu.add_command(label="▲  Move Up",   command=self._cmd_move_up)
            menu.add_command(label="▼  Move Down", command=self._cmd_move_down)
        else:
            menu.add_command(label="+ Add Step", command=self._cmd_add_step)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ──────────────────────────────────────────────────────────────────────
    # Clear / reset individual step fields
    # ──────────────────────────────────────────────────────────────────────

    def _cmd_clear_element(self, step: dict) -> None:
        """Wipe the element snip data from a step."""
        step["element_name"]     = ""
        step["element_image"]    = ""
        step["element_center_x"] = 0
        step["element_center_y"] = 0
        step["element_bounds"]   = {}
        self._refresh_props()
        self._set_status(f"Element region cleared for '{step['label']}'")

    def _cmd_clear_condition(self, step: dict) -> None:
        """Wipe the condition snip data from a conditional_click step."""
        step["condition_name"]      = ""
        step["condition_image"]     = ""
        step["condition_center_x"]  = 0
        step["condition_center_y"]  = 0
        step["condition_bounds"]    = {}
        self._refresh_props()
        self._set_status(f"Condition region cleared for '{step['label']}'")

    def _cmd_clear_action(self, step: dict) -> None:
        """Wipe the action snip data from a conditional_click step."""
        step["element_name"]     = ""
        step["element_image"]    = ""
        step["element_center_x"] = 0
        step["element_center_y"] = 0
        step["element_bounds"]   = {}
        self._refresh_props()
        self._set_status(f"Action region cleared for '{step['label']}'")

    def _cmd_clear_swipe(self, step: dict) -> None:
        """Reset swipe start/end coordinates."""
        step["swipe_x1"] = step["swipe_y1"] = 0
        step["swipe_x2"] = step["swipe_y2"] = 0
        self._refresh_props()
        self._set_status(f"Swipe points reset for '{step['label']}'")

    # ──────────────────────────────────────────────────────────────────────
    # Folder browser + Quick-Load combobox
    # ──────────────────────────────────────────────────────────────────────

    def _cmd_browse_folder(self) -> None:
        """Let user pick any folder; trainer scans it for registry files."""
        chosen = filedialog.askdirectory(
            title="Select folder containing registry files",
            initialdir=str(self._work_dir),
        )
        if not chosen:
            return
        self._work_dir = Path(chosen)
        self._refresh_folder_combobox()
        self._set_status(f"Active folder: {self._work_dir}")

    def _refresh_folder_combobox(self) -> None:
        """Populate the Quick-Load combobox with all registry files in work_dir."""
        if self._folder_var is None:
            return

        entries: list[str] = []
        self._ql_paths: list[Optional[Path]] = []   # parallel list of Paths

        # 1. Simple element registry
        reg_path = self._work_dir / "registry.json"
        if reg_path.exists():
            try:
                reg = json.loads(reg_path.read_text())
                entries.append(f"📋  registry.json  ({len(reg)} elements)")
                self._ql_paths.append(reg_path)
            except Exception:
                pass

        # 2. Workflow files
        for p in sorted(self._work_dir.glob("registry_*.json")):
            try:
                wf = load_workflow(p)
                name   = wf.get("workflow_name", p.stem)
                nsteps = len(wf.get("steps", []))
                entries.append(f"⚙  {name}  ({nsteps} steps)")
                self._ql_paths.append(p)
            except Exception:
                entries.append(f"⚙  {p.name}  (unreadable)")
                self._ql_paths.append(p)

        if entries:
            self._ql_combo["values"] = entries
            self._folder_var.set(f"— {len(entries)} file(s) in {self._work_dir.name}/ —")
        else:
            self._ql_combo["values"] = ["(no registry files found)"]
            self._folder_var.set(f"— empty: {self._work_dir.name}/ —")
            self._ql_paths = [None]

    def _on_quick_load(self, _event: tk.Event) -> None:
        """Called when user picks an entry in the Quick-Load combobox."""
        try:
            idx  = self._ql_combo.current()
            path = self._ql_paths[idx]
        except (AttributeError, IndexError):
            return

        if path is None:
            return

        # Simple registry: show viewer dialog
        if path.name == "registry.json":
            try:
                reg = json.loads(path.read_text())
            except Exception as exc:
                messagebox.showerror("Error", str(exc))
                return
            _ElementViewerDialog(self.root, reg, path)
            return

        # Workflow file: load into editor
        try:
            wf = load_workflow(path)
        except Exception as exc:
            messagebox.showerror("Error reading workflow", str(exc))
            return

        if self.steps and not messagebox.askyesno(
                "Load Workflow",
                f"Load '{wf.get('workflow_name', path.name)}'?\n"
                "Unsaved changes to the current workflow will be lost."):
            return

        self.steps    = wf.get("steps", [])
        self.elements = wf.get("elements", {})
        self._sel_idx = 0 if self.steps else None
        self._canvas.delete("overlay")
        self._update_step_list()
        self._update_mark_buttons()
        self._refresh_props()
        self._set_status(
            f"Loaded: {wf.get('workflow_name', path.name)} "
            f"({len(self.steps)} steps)"
        )

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)
        self.root.update_idletasks()

    def _on_close(self) -> None:
        if self.steps:
            if messagebox.askyesno("Quit", "Quit without saving workflow?"):
                self.root.destroy()
        else:
            self.root.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Helper dialogs
# ─────────────────────────────────────────────────────────────────────────────

class _TypePickerDialog(tk.Toplevel):
    """Modal dialog to pick a step type."""

    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.title("Add Step — Choose Type")
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        ttk.Label(self, text="Select an action type:",
                  font=("Segoe UI", 10, "bold")).pack(padx=16, pady=(12, 6))

        for stype in STEP_TYPES:
            row = ttk.Frame(self)
            row.pack(fill="x", padx=12, pady=2)
            ttk.Button(
                row, text=f"  {STEP_LABELS[stype]}", width=22,
                command=lambda s=stype: self._pick(s)
            ).pack(side="left")
            ttk.Label(
                row, text=STEP_DESCRIPTIONS[stype],
                foreground="#555555", wraplength=280
            ).pack(side="left", padx=8)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=6)
        ttk.Button(self, text="Cancel", command=self.destroy).pack(pady=(0, 10))
        self.transient(parent)
        self.wait_window()

    def _pick(self, stype: str) -> None:
        self.result = stype
        self.destroy()


class _ElementViewerDialog(tk.Toplevel):
    """Read-only viewer for a registry.json element list."""

    def __init__(self, parent: tk.Tk, registry: dict, path: Path) -> None:
        super().__init__(parent)
        self.title(f"Elements — {path.name}")
        self.resizable(True, True)
        self.grab_set()

        ttk.Label(
            self, text=f"{len(registry)} element(s) in {path}",
            font=("Segoe UI", 9, "bold")
        ).pack(padx=12, pady=(10, 4), anchor="w")

        cols = ("name", "centre", "snip")
        tv   = ttk.Treeview(self, columns=cols, show="headings", height=14)
        tv.heading("name",   text="Element Name")
        tv.heading("centre", text="Centre")
        tv.heading("snip",   text="Snip file")
        tv.column("name",   width=180)
        tv.column("centre", width=110, anchor="center")
        tv.column("snip",   width=240)
        sb = ttk.Scrollbar(self, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=4)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)

        for name, e in registry.items():
            cx   = e.get("center_x", "?")
            cy   = e.get("center_y", "?")
            snip = Path(e.get("image", "")).name
            tv.insert("", "end", values=(name, f"({cx}, {cy})", snip))

        ttk.Button(self, text="Close", command=self.destroy).pack(pady=8)
        self.transient(parent)
        self.wait_window()


class _WorkflowPickerDialog(tk.Toplevel):
    """Modal dialog to select a saved workflow file."""

    def __init__(self, parent: tk.Tk, paths: list) -> None:
        super().__init__(parent)
        self.title("Load Workflow")
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        ttk.Label(self, text="Select a workflow:",
                  font=("Segoe UI", 10, "bold")).pack(padx=16, pady=(12, 4))

        lb = tk.Listbox(self, width=52, height=min(len(paths), 12))
        lb.pack(padx=12, pady=4)
        for p in paths:
            try:
                wf = load_workflow(p)
                n  = wf.get("workflow_name", p.stem)
                s  = len(wf.get("steps", []))
                lb.insert("end", f"{n}  ({s} steps)  — {p.name}")
            except Exception:
                lb.insert("end", p.name)

        def _load():
            sel = lb.curselection()
            if sel:
                self.result = paths[sel[0]]
                self.destroy()

        row = ttk.Frame(self)
        row.pack(pady=8)
        ttk.Button(row, text="Load",   command=_load).pack(side="left",  padx=8)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left", padx=8)
        self.transient(parent)
        self.wait_window()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Visual Trainer — build Android automation workflows graphically."
    )
    ap.add_argument("--shot", metavar="PATH",
                    help="Reuse an existing screenshot instead of capturing a new one.")
    ap.add_argument("--list", "-l", action="store_true",
                    help="Print saved elements and workflows, then exit.")
    args = ap.parse_args()

    if args.list:
        reg = load_simple_registry()
        print(f"\nSimple elements (registry.json): {len(reg)}")
        for nm, e in reg.items():
            print(f"  {nm:<30} centre=({e['center_x']}, {e['center_y']})")
        wfs = list_workflows()
        print(f"\nWorkflows ({len(wfs)}):")
        for p in wfs:
            try:
                wf = load_workflow(p)
                print(f"  {wf['workflow_name']:<28} {len(wf.get('steps',[]))} steps  — {p.name}")
            except Exception:
                print(f"  {p.name}  (unreadable)")
        return

    shot = args.shot
    if not shot:
        print("[TRAINER] Capturing screenshot from device…")
        capture_screenshot(TEMP_SHOT)
        shot = TEMP_SHOT

    if not Path(shot).exists():
        import sys
        sys.exit(f"[ERROR] File not found: {shot}")

    TrainerApp(shot)


if __name__ == "__main__":
    main()
