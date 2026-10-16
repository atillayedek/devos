"""DevOS Developer Center — manage, repair and verify the developer workstation.

Also hosts the "DevOS AI Center" and "Accounts & AI" pages (opened directly with
--page ai / --page accounts from their own menu entries).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSpinBox,
                             QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTextBrowser,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QDialog, QDialogButtonBox)

from .. import accounts, health, manifest as manifest_mod, paths, vibe, winvm
from .common import (CommandDialog, Worker, badge, error, heading, info, make_app, run_in_terminal, STATUS_TEXT)

SERVICES = [
    ("postgresql.service", "PostgreSQL"), ("mariadb.service", "MariaDB"), ("redis-server.service", "Redis"),
    ("docker.service", "Docker Engine"), ("libvirtd.service", "libvirt (VMs)"), ("ollama.service", "Ollama"),
]


def pkexec(*args: str) -> list[str]:
    return ["pkexec", *args]


def load_report() -> dict | None:
    f = paths.user_state_dir() / "health.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


# ===================================================================== pages
class LicenseDialog(QDialog):
    """Shows the vendor licenses of the tools about to be downloaded; user must accept."""

    def __init__(self, tools: list[manifest_mod.Tool], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Complete Developer Environment Setup")
        self.resize(700, 480)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("The following tools are downloaded from their vendors' official sources. "
                             "They are not open source; installing them means you accept their licenses:"))
        browser = QTextBrowser(openExternalLinks=True)
        browser.setHtml("<ul>" + "".join(
            f'<li><b>{t.name}</b> — {t.license}<br><a href="{t.license_url}">{t.license_url}</a></li>'
            for t in tools) + "</ul>")
        lay.addWidget(browser)
        self.accept_box = QCheckBox("I have read and accept these license agreements")
        lay.addWidget(self.accept_box)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Download and install")
        bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.accept_box.toggled.connect(bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class OverviewPage(QWidget):
    def __init__(self, center: "Center"):
        super().__init__()
        self.center = center
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Developer Environment", 18))
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)
        f = self.status.font()
        f.setPointSize(16)
        self.status.setFont(f)
        lay.addWidget(self.status)
        self.summary = QLabel(wordWrap=True)
        lay.addWidget(self.summary)
        self.banner = QGroupBox("Developer Environment Setup incomplete")
        bl = QVBoxLayout(self.banner)
        self.banner_text = QLabel(wordWrap=True)
        bl.addWidget(self.banner_text)
        self.complete_btn = QPushButton("Complete setup (download remaining official tools)")
        self.complete_btn.clicked.connect(center.complete_setup)
        bl.addWidget(self.complete_btn)
        lay.addWidget(self.banner)
        self.failing = QTextBrowser()
        lay.addWidget(self.failing, 1)
        row = QHBoxLayout()
        self.check_btn = QPushButton("Run health check now")
        self.check_btn.clicked.connect(center.run_health)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        row.addWidget(self.check_btn)
        row.addWidget(self.progress)
        row.addStretch()
        lay.addLayout(row)

    def show_report(self, report: dict | None):
        if not report:
            self.status.setText("Developer Environment: <b>NOT CHECKED YET</b>")
            self.summary.setText("Run a health check to verify every installed tool.")
            self.banner.hide()
            self.failing.clear()
            return
        ready = report["overall"] == "READY"
        color = "#2e9d5b" if ready else "#d64545"
        self.status.setText(f'Developer Environment: <b style="color:{color}">{report["overall"]}</b>')
        tools = report["tools"]
        counts = {s: sum(t["status"] == s for t in tools) for s in ("working", "failed", "missing")}
        self.summary.setText(f"{counts['working']} working · {counts['failed']} broken · {counts['missing']} not "
                             f"installed — checked {report['generated'][:19].replace('T', ' ')} UTC")
        missing_prop = [t for t in tools if t["status"] == "missing" and not t["redistributable"]]
        self.banner.setVisible(bool(missing_prop))
        self.banner_text.setText("These tools could not be installed during setup (no internet or licenses not "
                                 "accepted): " + ", ".join(t["name"] for t in missing_prop))
        bad = [t for t in tools if t["required"] and t["status"] != "working"]
        html = "<h3>Action required</h3><ul>" if bad else "<p>All required tools passed their checks.</p>"
        for t in bad:
            detail = next((c["detail"] for c in t["checks"] if not c["ok"]), "")
            html += f"<li><b>{t['name']}</b> — {STATUS_TEXT[t['status']]}: {detail}</li>"
        self.failing.setHtml(html + ("</ul>" if bad else ""))


class ToolsPage(QWidget):
    def __init__(self, center: "Center"):
        super().__init__()
        self.center = center
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Tools & SDKs"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Tool", "Status", "Version / detail"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        for label, slot in (("Launch", self.launch), ("Repair / reinstall", self.repair),
                            ("Update", self.repair), ("Show check details", self.details)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)

    def show_report(self, report: dict | None):
        self.tree.clear()
        if not report:
            return
        cats: dict[str, QTreeWidgetItem] = {}
        for t in report["tools"]:
            parent = cats.get(t["category"])
            if parent is None:
                parent = cats[t["category"]] = QTreeWidgetItem(self.tree, [t["category"]])
                parent.setExpanded(True)
            detail = t["version"] or next((c["detail"] for c in t["checks"] if not c["ok"]), "")
            item = QTreeWidgetItem(parent, [t["name"] + ("" if t["required"] else "  (optional)"),
                                            STATUS_TEXT[t["status"]], detail])
            item.setData(0, Qt.ItemDataRole.UserRole, t)
            item.setForeground(1, self.palette().text() if t["status"] == "working" else Qt.GlobalColor.red)

    def _selected(self) -> dict | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def launch(self):
        t = self._selected()
        if not t or not t.get("desktop"):
            info(self, "Select a tool with a desktop application.")
            return
        entry = next((d / t["desktop"] for d in health._desktop_dirs() if (d / t["desktop"]).is_file()), None)
        if not entry:
            error(self, f"{t['desktop']} is not installed.")
            return
        subprocess.Popen(["gio", "launch", str(entry)], start_new_session=True)

    def repair(self):
        t = self._selected()
        if not t:
            return
        self.center.provision(["--tools", t["id"]], tools=[t["id"]])

    def details(self):
        t = self._selected()
        if t:
            QMessageBox.information(self, t["name"], "\n".join(
                f"[{'OK' if c['ok'] else 'FAIL'}] {c['kind']}: {c['detail']}" for c in t["checks"]))


class ServicesPage(QWidget):
    def __init__(self, center):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Services"))
        lay.addWidget(QLabel("Heavy services do not start at boot. Start them only when you need them."))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Service", "State", "Start at boot", "Unit"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for label, action in (("Start", "start"), ("Stop", "stop"), ("Restart", "restart"),
                              ("Enable at boot", "enable"), ("Disable at boot", "disable")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, a=action: self.act(a))
            row.addWidget(b)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        row.addStretch()
        lay.addLayout(row)
        self.refresh()

    def refresh(self):
        self.table.setRowCount(0)
        for unit, name in SERVICES:
            active = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip()
            enabled = subprocess.run(["systemctl", "is-enabled", unit], capture_output=True, text=True).stdout.strip()
            if enabled in ("", "not-found"):
                active, enabled = "not installed", "-"
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, v in enumerate((name, active, enabled, unit)):
                self.table.setItem(r, c, QTableWidgetItem(v))

    def act(self, action: str):
        row = self.table.currentRow()
        if row < 0:
            return
        unit = self.table.item(row, 3).text()
        res = subprocess.run(["pkexec", "/usr/bin/devos-service", action, unit], capture_output=True, text=True)
        if res.returncode != 0:
            error(self, res.stderr.strip() or f"{action} {unit} failed")
        self.refresh()


class AICenterPage(QWidget):
    def __init__(self, center):
        super().__init__()
        self.center = center
        lay = QVBoxLayout(self)
        lay.addWidget(heading("DevOS AI Center"))
        lay.addWidget(QLabel("AI is optional. Every tool in DevOS works without any account or AI provider."))
        self.agents = QTableWidget(0, 3)
        self.agents.setHorizontalHeaderLabels(["Coding agent", "Installed", "Account / model"])
        self.agents.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.agents)
        box = QGroupBox("Local models (Ollama)")
        bl = QVBoxLayout(box)
        self.models = QListWidget()
        bl.addWidget(self.models)
        row = QHBoxLayout()
        self.model_name = QLineEdit(placeholderText="model name, e.g. qwen2.5-coder:7b")
        pull = QPushButton("Download model")
        pull.clicked.connect(self.pull)
        row.addWidget(self.model_name)
        row.addWidget(pull)
        bl.addLayout(row)
        lay.addWidget(box)
        row = QHBoxLayout()
        vibe_btn = QPushButton("Open Vibe Coding")
        vibe_btn.clicked.connect(lambda: subprocess.Popen(["devos-vibe"], start_new_session=True))
        acc_btn = QPushButton("Accounts & AI…")
        acc_btn.clicked.connect(lambda: center.goto("accounts"))
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        for b in (vibe_btn, acc_btn, refresh):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        self._w = Worker(lambda: [(a, vibe.agent_available(a), vibe.agent_signed_in(a) if vibe.agent_available(a)
                                   else (False, "")) for a in vibe.AGENTS])
        self._w.done.connect(self._show)
        self._w.start()
        self.models.clear()
        for m in vibe.ollama_models():
            self.models.addItem(m)
        if not self.models.count():
            self.models.addItem("(no local model downloaded — Ollama service must be running)")

    def _show(self, rows):
        self.agents.setRowCount(0)
        for agent, installed, (ok, detail) in rows:
            r = self.agents.rowCount()
            self.agents.insertRow(r)
            self.agents.setItem(r, 0, QTableWidgetItem(agent.name))
            self.agents.setItem(r, 1, QTableWidgetItem("yes" if installed else "no"))
            self.agents.setItem(r, 2, QTableWidgetItem(("✓ " if ok else "✗ ") + detail if installed else "-"))

    def pull(self):
        name = self.model_name.text().strip()
        if name:
            vibe.pull_model(name)


class AccountsPage(QWidget):
    def __init__(self, center):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Accounts & AI"))
        lay.addWidget(QLabel("Connecting accounts is optional. Sign-in uses each provider's official login; "
                             "API keys are kept in the system keyring (KWallet), never in plain text.",
                             wordWrap=True))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Provider", "Status", "Detail"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for label, slot in (("Sign in", self.sign_in), ("Sign out", self.sign_out), ("Store API key…", self.store_key),
                            ("Remove API key", self.remove_key), ("Open website", self.open_web),
                            ("Refresh", self.refresh)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        QTimer.singleShot(0, self.refresh)

    def _provider(self) -> accounts.Provider | None:
        r = self.table.currentRow()
        return accounts.PROVIDERS[r] if r >= 0 else None

    def refresh(self):
        self.table.setRowCount(len(accounts.PROVIDERS))
        for r, p in enumerate(accounts.PROVIDERS):
            self.table.setItem(r, 0, QTableWidgetItem(p.name))
            self.table.setItem(r, 1, QTableWidgetItem("checking…"))
        self._w = Worker(lambda: [accounts.status(p) for p in accounts.PROVIDERS])
        self._w.done.connect(self._show)
        self._w.start()

    def _show(self, statuses):
        for r, (st, detail) in enumerate(statuses):
            lbl = QLabel(badge(st))
            self.table.setCellWidget(r, 1, lbl)
            self.table.setItem(r, 1, QTableWidgetItem(""))
            p = accounts.PROVIDERS[r]
            self.table.setItem(r, 2, QTableWidgetItem(detail + (f" — {p.note}" if p.note else "")))

    def sign_in(self):
        p = self._provider()
        if not p:
            return
        if p.cli and not shutil.which(p.cli):
            error(self, f"{p.cli} is not installed. Install it from Developer Center > Tools.")
            return
        cmd = accounts.login_command(p)
        if p.id == "jetbrains":
            subprocess.Popen(cmd, start_new_session=True)
        else:
            run_in_terminal(cmd, f"Sign in: {p.name}")

    def sign_out(self):
        p = self._provider()
        if p and p.logout:
            run_in_terminal(p.logout, f"Sign out: {p.name}")
        elif p:
            info(self, f"{p.name} has no CLI sign-out; remove the account in the provider's own app.")

    def store_key(self):
        p = self._provider()
        if not p or not p.api_key_env:
            info(self, "This provider uses its own sign-in; no API key is needed.")
            return
        key, ok = QInputDialog.getText(self, p.name, f"{p.api_key_env} (stored in the system keyring):",
                                       QLineEdit.EchoMode.Password)
        if ok and key:
            try:
                accounts.store_api_key(p.id, key)
            except Exception as exc:  # noqa: BLE001
                error(self, str(exc))
            self.refresh()

    def remove_key(self):
        p = self._provider()
        if p:
            accounts.delete_api_key(p.id)
            self.refresh()

    def open_web(self):
        p = self._provider()
        if p and p.web:
            webbrowser.open(p.web)


class GitPage(QWidget):
    def __init__(self, center):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Git & GitHub"))
        form = QFormLayout()
        self.name = QLineEdit(self._git("user.name"))
        self.email = QLineEdit(self._git("user.email"))
        form.addRow("Name", self.name)
        form.addRow("Email", self.email)
        lay.addLayout(form)
        save = QPushButton("Save Git identity")
        save.clicked.connect(self.save)
        lay.addWidget(save)
        self.key_label = QLabel(wordWrap=True)
        lay.addWidget(self.key_label)
        row = QHBoxLayout()
        for label, slot in (("Create SSH key", self.ssh_key), ("Upload SSH key to GitHub", self.upload),
                            ("Use GitHub CLI as Git credential helper", self.cred),
                            ("Clone a repository…", self.clone)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        lay.addStretch()
        self.update_key()

    @staticmethod
    def _git(key: str) -> str:
        return subprocess.run(["git", "config", "--global", key], capture_output=True, text=True).stdout.strip()

    def save(self):
        for k, w in (("user.name", self.name), ("user.email", self.email)):
            if w.text().strip():
                subprocess.run(["git", "config", "--global", k, w.text().strip()], check=False)
        subprocess.run(["git", "config", "--global", "init.defaultBranch", "main"], check=False)
        info(self, "Git identity saved.")

    def update_key(self):
        pub = Path.home() / ".ssh" / "id_ed25519.pub"
        self.key_label.setText(f"SSH key: {pub}" if pub.exists() else "No SSH key yet (~/.ssh/id_ed25519).")

    def ssh_key(self):
        run_in_terminal(["ssh-keygen", "-t", "ed25519", "-C", self.email.text() or getpass.getuser()],
                        "Create SSH key")

    def upload(self):
        pub = Path.home() / ".ssh" / "id_ed25519.pub"
        if not pub.exists():
            error(self, "Create an SSH key first.")
            return
        run_in_terminal(["gh", "ssh-key", "add", str(pub), "--title", f"DevOS {os.uname().nodename}"],
                        "Upload SSH key")

    def cred(self):
        run_in_terminal(["gh", "auth", "setup-git"], "GitHub credential helper")

    def clone(self):
        url, ok = QInputDialog.getText(self, "Clone", "Repository (URL or owner/name):")
        if ok and url:
            dest = paths.developer_dir()
            dest.mkdir(exist_ok=True)
            cmd = ["gh", "repo", "clone", url] if "/" in url and "://" not in url and "@" not in url else ["git", "clone", url]
            run_in_terminal(cmd, "Clone", workdir=str(dest))


class WindowsPage(QWidget):
    def __init__(self, center):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Windows Development Environment"))
        lay.addWidget(QLabel(
            "Windows-only tools such as <b>Visual Studio</b> run inside a Windows VM on KVM — they do not run "
            "natively on Linux. Provide your own Windows ISO and license. ~/Developer is shared with the VM. "
            "For lighter Windows apps use Wine or Bottles.", wordWrap=True))
        self.state = QLabel()
        lay.addWidget(self.state)
        self.problems = QLabel(wordWrap=True)
        self.problems.setStyleSheet("color:#d64545")
        lay.addWidget(self.problems)
        box = QGroupBox("Create the Windows VM")
        form = QFormLayout(box)
        iso_row = QHBoxLayout()
        self.iso = QLineEdit(placeholderText="Windows 10/11 ISO from microsoft.com")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.pick_iso)
        iso_row.addWidget(self.iso)
        iso_row.addWidget(browse)
        form.addRow("Windows ISO", iso_row)
        total_mb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1 << 20)
        self.ram = QSpinBox(minimum=4096, maximum=max(4096, total_mb - 2048), singleStep=1024,
                            value=min(8192, max(4096, total_mb // 2)))
        self.cpus = QSpinBox(minimum=2, maximum=max(2, os.cpu_count() or 2), value=max(2, (os.cpu_count() or 4) // 2))
        self.disk = QSpinBox(minimum=64, maximum=2048, value=128, suffix=" GB")
        form.addRow("Memory (MB)", self.ram)
        form.addRow("CPU cores", self.cpus)
        form.addRow("Disk", self.disk)
        self.create_btn = QPushButton("Create VM and start Windows setup")
        self.create_btn.clicked.connect(self.create)
        form.addRow(self.create_btn)
        lay.addWidget(box)
        self.bar = QProgressBar()
        self.bar.hide()
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        for label, slot in (("Start", self.start), ("Open window", winvm.open_viewer), ("Shut down", self.stop),
                            ("Open in virt-manager", lambda: subprocess.Popen(["virt-manager"], start_new_session=True))):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        vs = QGroupBox("Visual Studio (inside the VM)")
        vl = QHBoxLayout(vs)
        self.vs_edition = QComboBox()
        self.vs_edition.addItems(list(winvm.VS_BOOTSTRAPPERS))
        send = QPushButton("Download official installer into the shared folder")
        send.clicked.connect(self.send_vs)
        vl.addWidget(self.vs_edition)
        vl.addWidget(send)
        lay.addWidget(vs)
        compat = QHBoxLayout()
        for label, cmd in (("Open Bottles", ["flatpak", "run", "com.usebottles.bottles"]),
                           ("Wine configuration", ["winecfg"]), ("Winetricks", ["winetricks", "--gui"])):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, c=cmd: subprocess.Popen(c, start_new_session=True))
            compat.addWidget(b)
        compat.addStretch()
        lay.addLayout(compat)
        lay.addStretch()
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        self.state.setText(f"VM state: <b>{winvm.state()}</b>")
        probs = winvm.prerequisites()
        self.problems.setText("\n".join(probs))
        self.create_btn.setEnabled(not probs and winvm.state() == "not created")

    def pick_iso(self):
        f, _ = QFileDialog.getOpenFileName(self, "Windows ISO", str(Path.home() / "Downloads"), "ISO images (*.iso)")
        if f:
            self.iso.setText(f)

    def _progress(self, done, total):
        QTimer.singleShot(0, lambda: (self.bar.setMaximum(total or 0), self.bar.setValue(done)))

    def create(self):
        iso = Path(self.iso.text())
        if not iso.is_file():
            error(self, "Choose your Windows ISO first.")
            return
        self.bar.show()
        self._w = Worker(winvm.create, iso, self.ram.value(), self.cpus.value(), self.disk.value(),
                         progress=self._progress)
        self._w.done.connect(lambda _: (self.bar.hide(), winvm.write_guest_instructions(), winvm.open_viewer(),
                                        self.refresh(), info(self, "VM created. Windows setup is running; see "
                                                                   "~/Developer/WindowsShare/README-DevOS-Windows-VM.txt")))
        self._w.failed.connect(lambda e: (self.bar.hide(), error(self, e), self.refresh()))
        self._w.start()

    def start(self):
        try:
            winvm.start()
            winvm.open_viewer()
        except RuntimeError as exc:
            error(self, str(exc))
        self.refresh()

    def stop(self):
        winvm.shutdown()
        QTimer.singleShot(3000, self.refresh)

    def send_vs(self):
        self.bar.show()
        self._w = Worker(winvm.send_visual_studio_installer, self.vs_edition.currentText(), progress=self._progress)
        self._w.done.connect(lambda p: (self.bar.hide(), winvm.write_guest_instructions(),
                                        info(self, f"Saved {p}.\nIn Windows it is Z:\\WindowsShare\\{p.name}")))
        self._w.failed.connect(lambda e: (self.bar.hide(), error(self, e)))
        self._w.start()


# ==================================================================== window
class Center(QMainWindow):
    PAGES = [("overview", "Overview", "dialog-information"), ("tools", "Tools & SDKs", "applications-development"),
             ("services", "Services", "preferences-system"), ("ai", "AI Center", "preferences-desktop-ai"),
             ("accounts", "Accounts & AI", "system-users"), ("git", "Git & GitHub", "vcs-normal"),
             ("windows", "Windows Development", "computer")]

    def __init__(self, page: str = "overview"):
        super().__init__()
        self.setWindowTitle("DevOS Developer Center")
        self.resize(1100, 720)
        self.man = manifest_mod.load()
        split = QSplitter()
        self.nav = QListWidget()
        self.nav.setMaximumWidth(220)
        self.stack = QStackedWidget()
        self.overview = OverviewPage(self)
        self.tools = ToolsPage(self)
        widgets = {"overview": self.overview, "tools": self.tools, "services": ServicesPage(self),
                   "ai": AICenterPage(self), "accounts": AccountsPage(self), "git": GitPage(self),
                   "windows": WindowsPage(self)}
        from PyQt6.QtGui import QIcon
        for key, label, icon in self.PAGES:
            QListWidgetItem(QIcon.fromTheme(icon), label, self.nav)
            self.stack.addWidget(widgets[key])
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        split.addWidget(self.nav)
        split.addWidget(self.stack)
        self.setCentralWidget(split)
        self.goto(page)
        report = load_report()
        self.show_report(report)
        if report is None:
            QTimer.singleShot(200, self.run_health)

    def goto(self, page: str):
        keys = [p[0] for p in self.PAGES]
        self.nav.setCurrentRow(keys.index(page) if page in keys else 0)

    def show_report(self, report):
        self.overview.show_report(report)
        self.tools.show_report(report)

    def run_health(self):
        self.overview.check_btn.setEnabled(False)
        self.overview.progress.show()
        self._hw = Worker(lambda: (lambda r: (health.save_user_report(r), r)[1])(health.run_all()))
        self._hw.done.connect(self._health_done)
        self._hw.failed.connect(lambda e: (error(self, e), self._health_done(None)))
        self._hw.start()

    def _health_done(self, report):
        self.overview.check_btn.setEnabled(True)
        self.overview.progress.hide()
        if report:
            self.show_report(report)

    def provision(self, extra: list[str], tools: list[str] | None = None):
        proprietary = [t for t in self.man.proprietary() if not tools or t.id in tools]
        args = ["/usr/bin/devos-provision", "--target-user", getpass.getuser(), *extra]
        if proprietary:
            dlg = LicenseDialog(proprietary, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            args += ["--accept-licenses", *[t.id for t in proprietary], "--accepted-via", "developer-center"]
        CommandDialog("DevOS provisioning", pkexec(*args), self, on_finish=lambda ok: self.run_health()).exec()

    def complete_setup(self):
        report = load_report() or {"tools": []}
        missing = [t["id"] for t in report["tools"] if t["status"] != "working"]
        self.provision(["--only-missing", "--tools", *missing], tools=missing)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="devos-center")
    ap.add_argument("--page", default="overview", choices=[p[0] for p in Center.PAGES])
    args, qt_args = ap.parse_known_args(argv)
    app = make_app("DevOS Developer Center", "devos-center")
    win = Center(args.page)
    if args.page == "ai":
        win.setWindowTitle("DevOS AI Center")
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
