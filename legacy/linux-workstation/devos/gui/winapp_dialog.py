"""Handler for double-clicked .exe/.msi files: analyse, recommend, run."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .. import winapp, winvm
from .common import error, heading, info, make_app


class WinAppDialog(QDialog):
    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self.setWindowTitle(f"Open {path.name}")
        self.resize(560, 300)
        lay = QVBoxLayout(self)
        try:
            self.result_ = winapp.analyze(path)
        except Exception as exc:  # noqa: BLE001
            lay.addWidget(QLabel(f"Cannot analyse {path.name}: {exc}"))
            self.result_ = None
            return
        r = self.result_
        lay.addWidget(heading(r.label))
        meta = ", ".join(filter(None, [r.kind, r.arch, r.product, ".NET" if r.is_dotnet else ""]))
        lay.addWidget(QLabel(f"<b>{path.name}</b> — {meta}"))
        lay.addWidget(QLabel("<br>".join(f"• {x}" for x in r.reasons), wordWrap=True))
        lay.addWidget(QLabel("<i>This is an automatic recommendation; you can choose any option.</i>"))
        lay.addStretch()
        row = QHBoxLayout()
        buttons = [("Run with Wine", self.run_wine), ("Open in Bottles", self.run_bottles),
                   ("Use Windows VM", self.run_vm)]
        order = {winapp.WINE: 0, winapp.BOTTLES: 1, winapp.VM: 2}[r.category]
        for i, (label, slot) in enumerate(buttons):
            b = QPushButton(label + ("  (recommended)" if i == order else ""))
            b.clicked.connect(slot)
            if i == order:
                b.setDefault(True)
            row.addWidget(b)
        lay.addLayout(row)

    def run_wine(self):
        if self.path.suffix.lower() == ".msi":
            cmd = ["wine", "msiexec", "/i", str(self.path)]
        else:
            cmd = ["wine", str(self.path)]
        subprocess.Popen(cmd, cwd=str(self.path.parent), start_new_session=True)
        self.accept()

    def run_bottles(self):
        if not shutil.which("flatpak"):
            error(self, "Flatpak is not installed.")
            return
        # Bottles' CLI runs the program in its default bottle; the GUI opens to manage prefixes
        subprocess.Popen(["flatpak", "run", "--file-forwarding", "com.usebottles.bottles", "@@", str(self.path), "@@"],
                         start_new_session=True)
        self.accept()

    def run_vm(self):
        share = winvm.share_dir() / "WindowsShare"
        dest = share / self.path.name
        if self.path.resolve() != dest.resolve():
            shutil.copy2(self.path, dest)
        winvm.write_guest_instructions()
        if winvm.state() == "not created":
            subprocess.Popen(["devos-center", "--page", "windows"], start_new_session=True)
        else:
            try:
                winvm.start()
                winvm.open_viewer()
            except RuntimeError as exc:
                error(self, str(exc))
                return
        info(self, f"Copied to the shared folder. Inside Windows open Z:\\WindowsShare\\{self.path.name}")
        self.accept()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    app = make_app("Windows Application", "wine")
    if not argv:
        print("usage: devos-winapp FILE.exe|FILE.msi", file=sys.stderr)
        return 2
    dlg = WinAppDialog(Path(argv[0]).resolve())
    dlg.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
