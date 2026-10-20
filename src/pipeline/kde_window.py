"""Przeniesienie okna na wybrany monitor i maksymalizacja w KDE Plasma 6 (KWin script przez qdbus6).

Działa na Wayland i X11, bo okno przesuwa kompozytor, a nie aplikacja.
"""

import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

KWIN_SCRIPT = """
const out = workspace.screens.find(s => s.name === "{output}");
for (const w of workspace.windowList()) {{
    if (w.pid === {pid} && w.normalWindow) {{
        if (out) workspace.sendClientToScreen(w, out);
        w.setMaximize(true, true);
        workspace.activeWindow = w;
    }}
}}
"""


def place_window(pid, output="DP-8"):
    qdbus = shutil.which("qdbus6")
    if not qdbus:
        return False
    name = f"place-{pid}"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(KWIN_SCRIPT.format(pid=pid, output=output))
        path = f.name
    try:
        sid = subprocess.run([qdbus, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.loadScript", path, name],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        subprocess.run([qdbus, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.start"],
                       capture_output=True, timeout=5)
        subprocess.run([qdbus, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", name],
                       capture_output=True, timeout=5)
        return sid.lstrip("-").isdigit()
    finally:
        Path(path).unlink(missing_ok=True)


def place_window_later(pid, output="DP-8", delays=(1.5, 3.0)):
    """Wywołaj przed blokującą pętlą GUI; okno musi już istnieć, więc próbujemy kilka razy."""
    def run():
        for d in delays:
            time.sleep(d)
            place_window(pid, output)
    threading.Thread(target=run, daemon=True).start()
