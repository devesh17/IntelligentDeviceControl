#!/usr/bin/env python3
"""Phase 2 — Execution Engine (The Automator)

Two execution modes
-------------------
Single-task mode
    python automator.py "element name"
    Pulls the element from elements/registry.json, finds it on screen, taps it.

Sequence mode
    python automator.py --sequence my_workflow
    Loads elements/registry_my_workflow.json and executes every step in order.
    Supports: click, verify, conditional_click, wait, swipe, input_text, press_key.

Interactive loop
    python automator.py
    Continuous prompt — type element names or control commands.

Extra flags
-----------
    --list, -l           list saved elements (registry.json)
    --workflows          list saved workflow files
    --threshold 0.75     override default match confidence
    --dry-run, -n        find elements but suppress all device actions
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2

from utils.adb import (
    capture_screenshot, adb_tap,
    adb_swipe, adb_press_key, adb_input_text,
)

# ─────────────────────────────────────────────────────────────────────────────
# Paths & defaults
# ─────────────────────────────────────────────────────────────────────────────

REGISTRY_FILE    = Path("elements") / "registry.json"
ELEMENTS_DIR     = Path("elements")
TEMP_SCREENSHOT  = "automator_screen.png"
DEFAULT_THRESHOLD = 0.80

# ─────────────────────────────────────────────────────────────────────────────
# Registry helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_simple_registry() -> dict:
    if not REGISTRY_FILE.exists():
        sys.exit(
            "[ERROR] No element registry found.\n"
            "  Run  python trainer.py  first to save UI elements."
        )
    with open(REGISTRY_FILE) as f:
        return json.load(f)


def load_workflow(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"[ERROR] Workflow file not found: {path}")
    with open(path) as f:
        return json.load(f)


def list_workflows() -> list:
    return sorted(ELEMENTS_DIR.glob("registry_*.json"))


def print_simple_registry() -> None:
    reg = load_simple_registry()
    if not reg:
        print("registry.json is empty. Run trainer.py to add elements.")
        return
    print(f"\n{'Element':<32} {'Centre':>20}  Snip file")
    print("─" * 80)
    for name, e in reg.items():
        centre = f"({e['center_x']}, {e['center_y']})"
        print(f"{name:<32} {centre:>20}  {e['image']}")
    print()


def print_workflows() -> None:
    wfs = list_workflows()
    if not wfs:
        print("No workflow files found in elements/.")
        return
    print(f"\n{'Workflow name':<28} {'Steps':>6}  File")
    print("─" * 70)
    for p in wfs:
        try:
            wf = load_workflow(p)
            name   = wf.get("workflow_name", p.stem)
            nsteps = len(wf.get("steps", []))
            print(f"{name:<28} {nsteps:>6}  {p.name}")
        except Exception as exc:
            print(f"  {p.name}  — unreadable ({exc})")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Template matching
# ─────────────────────────────────────────────────────────────────────────────

def find_element(
    screenshot: str,
    template: str,
    threshold: float,
) -> Optional[Tuple[int, int, float]]:
    """Match *template* inside *screenshot*.

    Returns (centre_x, centre_y, confidence) or None if not found.
    """
    src = cv2.imread(screenshot)
    tpl = cv2.imread(template)

    if src is None:
        sys.exit(f"[ERROR] Cannot read screenshot: {screenshot}")
    if tpl is None:
        print(f"[WARN] Cannot read snip file: {template}")
        return None

    th, tw = tpl.shape[:2]
    sh, sw = src.shape[:2]

    if th > sh or tw > sw:
        print(f"[WARN] Snip ({tw}×{th}) larger than screenshot ({sw}×{sh}).")
        return None

    result = cv2.matchTemplate(src, tpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < threshold:
        return None

    cx = max_loc[0] + tw // 2
    cy = max_loc[1] + th // 2
    return cx, cy, float(max_val)


def _fresh_match(
    entry: dict,
    threshold: float,
    label: str = "",
) -> Optional[Tuple[int, int, float]]:
    """Capture a fresh screenshot and find *entry* on it."""
    capture_screenshot(TEMP_SCREENSHOT)
    return find_element(TEMP_SCREENSHOT, entry["image"], threshold)


# ─────────────────────────────────────────────────────────────────────────────
# Single-task execution
# ─────────────────────────────────────────────────────────────────────────────

def run_single(element_name: str, threshold: float, dry_run: bool) -> bool:
    """Find *element_name* on screen and tap it. Returns True on success."""
    registry = load_simple_registry()

    if element_name not in registry:
        available = ", ".join(f"'{k}'" for k in registry)
        print(f"[ERROR] '{element_name}' not in registry.\nAvailable: {available}")
        return False

    entry = registry[element_name]
    if not Path(entry["image"]).exists():
        print(f"[ERROR] Snip missing: {entry['image']}")
        return False

    print("[1/3] Capturing screenshot…")
    capture_screenshot(TEMP_SCREENSHOT)

    print(f"[2/3] Searching for '{element_name}' (threshold ≥ {threshold:.0%})…")
    result = find_element(TEMP_SCREENSHOT, entry["image"], threshold)

    if result is None:
        print(
            f"[FAIL] '{element_name}' not found.\n"
            f"       Confidence below {threshold:.0%}. Try --threshold {threshold - 0.05:.2f} "
            f"or retrain the element."
        )
        return False

    cx, cy, conf = result
    print(f"[MATCH] Confidence {conf:.1%}  →  ({cx}, {cy})")

    if dry_run:
        print("[3/3] DRY-RUN — tap suppressed.")
    else:
        print(f"[3/3] Tapping ({cx}, {cy})…")
        adb_tap(cx, cy)
        print("[DONE]")

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Sequence (workflow) execution
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_entry(elements: dict, name: str) -> Optional[dict]:
    """Get an element entry from the embedded workflow dict."""
    e = elements.get(name)
    if e is None:
        print(f"[WARN] Element '{name}' not found in workflow elements.")
    return e


def _do_click(step: dict, elements: dict, threshold: float,
              dry_run: bool) -> bool:
    entry = _resolve_entry(elements, step.get("element_name", ""))
    if entry is None:
        return False
    thr = step.get("threshold", threshold)
    capture_screenshot(TEMP_SCREENSHOT)
    result = find_element(TEMP_SCREENSHOT, entry["image"], thr)
    if result is None:
        print(f"       Not found (threshold {thr:.0%}).")
        return False
    cx, cy, conf = result
    print(f"       Match {conf:.1%}  →  ({cx}, {cy})")
    if not dry_run:
        adb_tap(cx, cy)
    return True


def _do_verify(step: dict, elements: dict, threshold: float) -> bool:
    entry = _resolve_entry(elements, step.get("element_name", ""))
    if entry is None:
        return False
    thr = step.get("threshold", threshold)
    capture_screenshot(TEMP_SCREENSHOT)
    result = find_element(TEMP_SCREENSHOT, entry["image"], thr)
    found = result is not None
    if found:
        _, _, conf = result
        print(f"       Found  (confidence {conf:.1%})")
    else:
        print(f"       Not found (threshold {thr:.0%}).")
    return found


def _do_conditional_click(step: dict, elements: dict, threshold: float,
                           dry_run: bool) -> bool:
    cond_entry = _resolve_entry(elements, step.get("condition_name", ""))
    if cond_entry is None:
        return False

    # Capture once and reuse for both checks
    capture_screenshot(TEMP_SCREENSHOT)

    cond_thr = step.get("condition_threshold", threshold)
    cond_res = find_element(TEMP_SCREENSHOT, cond_entry["image"], cond_thr)

    if cond_res is None:
        on_false = step.get("on_condition_false", "skip")
        print(f"       Condition not met → {on_false}")
        return on_false != "stop"

    print(f"       Condition met (confidence {cond_res[2]:.1%})")

    # Determine action target
    if step.get("action_same_as_cond"):
        cx, cy = cond_res[0], cond_res[1]
        print(f"       Clicking condition element at ({cx}, {cy})")
    else:
        act_entry = _resolve_entry(elements, step.get("element_name", ""))
        if act_entry is None:
            return False
        act_thr = step.get("threshold", threshold)
        act_res = find_element(TEMP_SCREENSHOT, act_entry["image"], act_thr)
        if act_res is None:
            print(f"       Action element not found (threshold {act_thr:.0%}).")
            return False
        cx, cy = act_res[0], act_res[1]
        print(f"       Clicking action element at ({cx}, {cy})")

    if not dry_run:
        adb_tap(cx, cy)
    return True


def _handle_on_not_found(policy: str, label: str) -> bool:
    """Return True to continue, False to abort the workflow."""
    if policy == "stop":
        print(f"[ABORT] '{label}' — on_not_found=stop. Halting workflow.")
        return False
    if policy == "skip":
        print(f"       Skipping step.")
    else:
        print(f"       Continuing despite failure (on_not_found=continue).")
    return True


def execute_sequence(workflow_path: Path, threshold: float,
                     dry_run: bool) -> bool:
    """Run all steps in a workflow JSON file.

    Returns True when the workflow completes (or a step is skipped/continued),
    False if a step aborts the run.
    """
    wf       = load_workflow(workflow_path)
    steps    = wf.get("steps", [])
    elements = wf.get("elements", {})
    name     = wf.get("workflow_name", workflow_path.name)

    print(f"\n{'─'*60}")
    print(f"  WORKFLOW: {name}  ({len(steps)} steps)"
          + ("  [DRY-RUN]" if dry_run else ""))
    print(f"{'─'*60}")

    for i, step in enumerate(steps, 1):
        t     = step["type"]
        label = step["label"]

        print(f"\n[{i}/{len(steps)}] {label}  [{t}]")

        wb = step.get("wait_before", 0.0)
        if wb and not dry_run:
            print(f"       Waiting {wb}s before…")
            time.sleep(wb)

        ok = True

        if t == "click":
            ok = _do_click(step, elements, threshold, dry_run)
            if not ok:
                if not _handle_on_not_found(step.get("on_not_found", "stop"), label):
                    return False

        elif t == "verify":
            found = _do_verify(step, elements, threshold)
            if not found:
                if not _handle_on_not_found(step.get("on_not_found", "stop"), label):
                    return False

        elif t == "conditional_click":
            result = _do_conditional_click(step, elements, threshold, dry_run)
            if not result and step.get("on_condition_false", "skip") == "stop":
                return False

        elif t == "wait":
            d = step.get("duration", 1.0)
            print(f"       Pausing {d}s…")
            if not dry_run:
                time.sleep(d)

        elif t == "swipe":
            x1, y1 = step.get("swipe_x1", 0), step.get("swipe_y1", 0)
            x2, y2 = step.get("swipe_x2", 0), step.get("swipe_y2", 0)
            dur    = step.get("swipe_duration_ms", 300)
            print(f"       Swiping ({x1},{y1}) → ({x2},{y2})  {dur} ms")
            if not dry_run:
                adb_swipe(x1, y1, x2, y2, dur)

        elif t == "input_text":
            text = step.get("text", "")
            print(f"       Typing: {text!r}")
            if not dry_run:
                adb_input_text(text)

        elif t == "press_key":
            key = step.get("key", "BACK")
            print(f"       Key: {key}")
            if not dry_run:
                adb_press_key(key)

        else:
            print(f"       [WARN] Unknown step type '{t}' — skipping.")

        wa = step.get("wait_after", 0.0)
        if wa and not dry_run:
            time.sleep(wa)

        print(f"       ✓")

    print(f"\n{'─'*60}")
    print(f"  WORKFLOW COMPLETE: {name}")
    print(f"{'─'*60}\n")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Interactive loop
# ─────────────────────────────────────────────────────────────────────────────

_HELP = """\
Commands
────────
  <element name>              capture screen, find element, tap it
  sequence <workflow name>    execute a full workflow (registry_<name>.json)
  list                        show elements in registry.json
  workflows                   show saved workflow files
  threshold <0.0-1.0>         set match confidence threshold (current: {threshold:.0%})
  dry-run on|off              toggle tap suppression (current: {dry_run})
  help                        show this message
  quit / exit / q             exit
"""


def interactive_loop(threshold: float, dry_run: bool) -> None:
    print("\n" + "─" * 56)
    print("  Android Automator  —  interactive mode")
    print("─" * 56)
    print(_HELP.format(threshold=threshold, dry_run=dry_run))

    while True:
        try:
            raw = input("automator> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[EXIT] Goodbye.")
            break

        if not raw:
            continue

        cmd = raw.lower()

        if cmd in ("quit", "exit", "q"):
            print("[EXIT] Goodbye.")
            break

        if cmd == "help":
            print(_HELP.format(threshold=threshold, dry_run=dry_run))
            continue

        if cmd == "list":
            print_simple_registry()
            continue

        if cmd == "workflows":
            print_workflows()
            continue

        if cmd.startswith("threshold "):
            parts = cmd.split()
            try:
                val = float(parts[1])
                if 0.0 < val <= 1.0:
                    threshold = val
                    print(f"[SET] Threshold → {threshold:.0%}")
                else:
                    print("[ERROR] Value must be > 0.0 and ≤ 1.0.")
            except (IndexError, ValueError):
                print("[ERROR] Usage:  threshold 0.75")
            continue

        if cmd.startswith("dry-run "):
            toggle = raw.split()[1].lower() if len(raw.split()) > 1 else ""
            if toggle == "on":
                dry_run = True
                print("[SET] Dry-run ON — device actions suppressed.")
            elif toggle == "off":
                dry_run = False
                print("[SET] Dry-run OFF — device actions live.")
            else:
                print("[ERROR] Usage:  dry-run on|off")
            continue

        if cmd.startswith("sequence "):
            wf_name = raw[len("sequence "):].strip()
            slug    = wf_name.lower().replace(" ", "_")
            path    = Path("elements") / f"registry_{slug}.json"
            if not path.exists():
                # Also try exact match
                candidates = list(Path("elements").glob(f"registry_{slug}*.json"))
                if candidates:
                    path = candidates[0]
                else:
                    print(f"[ERROR] Workflow '{wf_name}' not found.")
                    print("        Run  workflows  to list available workflows.")
                    print()
                    continue
            execute_sequence(path, threshold=threshold, dry_run=dry_run)
            continue

        # Anything else → single-element tap
        run_single(raw, threshold=threshold, dry_run=dry_run)
        print()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Automator — find a saved UI element and tap it, or run a full workflow.\n"
            "No arguments → interactive loop."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "element", nargs="?",
        help="Element name for a one-shot tap (single-task mode)."
    )
    parser.add_argument(
        "--sequence", "-s", metavar="WORKFLOW",
        help="Execute a saved workflow (sequence mode)."
    )
    parser.add_argument(
        "--list", "-l", action="store_true",
        help="List elements in registry.json and exit."
    )
    parser.add_argument(
        "--workflows", "-w", action="store_true",
        help="List available workflow files and exit."
    )
    parser.add_argument(
        "--threshold", "-t", type=float, default=DEFAULT_THRESHOLD,
        metavar="0.0-1.0",
        help=f"Minimum match confidence (default {DEFAULT_THRESHOLD})."
    )
    parser.add_argument(
        "--dry-run", "-n", action="store_true",
        help="Find elements / plan steps but do NOT send any device actions."
    )
    args = parser.parse_args()

    if not (0.0 < args.threshold <= 1.0):
        sys.exit("[ERROR] --threshold must be between 0.0 (exclusive) and 1.0.")

    # ── Info-only modes ───────────────────────────────────────────────────
    if args.list:
        print_simple_registry()
        return
    if args.workflows:
        print_workflows()
        return

    # ── Sequence mode ─────────────────────────────────────────────────────
    if args.sequence:
        slug = args.sequence.strip().lower().replace(" ", "_")
        path = Path("elements") / f"registry_{slug}.json"
        if not path.exists():
            candidates = list(Path("elements").glob(f"registry_{slug}*.json"))
            if candidates:
                path = candidates[0]
            else:
                sys.exit(
                    f"[ERROR] Workflow '{args.sequence}' not found.\n"
                    f"  Run  python automator.py --workflows  to list available ones."
                )
        ok = execute_sequence(path, threshold=args.threshold,
                              dry_run=args.dry_run)
        sys.exit(0 if ok else 1)

    # ── Single-task mode ──────────────────────────────────────────────────
    if args.element:
        ok = run_single(args.element, threshold=args.threshold,
                        dry_run=args.dry_run)
        sys.exit(0 if ok else 1)

    # ── Interactive loop ──────────────────────────────────────────────────
    interactive_loop(threshold=args.threshold, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
