"""Instantiates every DevOS window offscreen and saves screenshots (build-time GUI check)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from devos.gui.center import Center  # noqa: E402
from devos.gui.vibe_app import VibeWindow  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(out, exist_ok=True)
for page in [p[0] for p in Center.PAGES]:
    w = Center(page)
    w.show()
    for _ in range(20):
        app.processEvents()
    w.grab().save(os.path.join(out, f"center-{page}.png"))
    w.close()
v = VibeWindow()
v.show()
for _ in range(20):
    app.processEvents()
v.grab().save(os.path.join(out, "vibe.png"))
print("GUI smoke OK:", sorted(os.listdir(out)))
os._exit(0)
