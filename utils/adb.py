"""ADB utility — device detection, screenshot capture, tap injection."""

import subprocess
import sys
from pathlib import Path


def _adb(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["adb", *args], capture_output=True)


def get_device() -> str:
    """Return the serial of the first connected ADB device.

    Exits with a clear message when no device is found so callers
    don't need to handle None.
    """
    result = _adb("devices")
    lines = result.stdout.decode(errors="replace").strip().splitlines()[1:]
    for line in lines:
        if "\tdevice" in line:
            return line.split("\t")[0].strip()
    sys.exit(
        "[ERROR] No Android device detected.\n"
        "  • Connect via USB and enable USB Debugging (Settings → Developer options).\n"
        "  • Run:  adb devices  to verify."
    )


def capture_screenshot(output_path: str) -> None:
    """Capture the device screen and write it as a PNG to *output_path*."""
    device = get_device()
    result = subprocess.run(
        ["adb", "-s", device, "exec-out", "screencap", "-p"],
        capture_output=True,
    )
    if result.returncode != 0:
        sys.exit(f"[ERROR] screencap failed: {result.stderr.decode(errors='replace')}")
    Path(output_path).write_bytes(result.stdout)


def adb_tap(x: int, y: int) -> None:
    """Send a hardware tap to the device at pixel coordinates (*x*, *y*)."""
    device = get_device()
    subprocess.run(
        ["adb", "-s", device, "shell", "input", "tap", str(x), str(y)],
        check=True,
        capture_output=True,
    )


def adb_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
    """Swipe from (x1, y1) to (x2, y2) over *duration_ms* milliseconds."""
    device = get_device()
    subprocess.run(
        ["adb", "-s", device, "shell", "input", "swipe",
         str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
        check=True, capture_output=True,
    )


def adb_press_key(keycode: str) -> None:
    """Send a hardware key event, e.g. BACK, HOME, ENTER."""
    device = get_device()
    subprocess.run(
        ["adb", "-s", device, "shell", "input", "keyevent", keycode],
        check=True, capture_output=True,
    )


def adb_input_text(text: str) -> None:
    """Type *text* into the currently focused field.

    Spaces must be sent as '%s'; other special characters should be
    URL-encoded or passed via a key-event workaround.
    """
    device = get_device()
    escaped = text.replace(" ", "%s")
    subprocess.run(
        ["adb", "-s", device, "shell", "input", "text", escaped],
        check=True, capture_output=True,
    )
