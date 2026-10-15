"""Shared Qt helpers for the DevOS desktop apps."""

from __future__ import annotations

import shlex
import subprocess
import sys
from typing import Callable

from PyQt6.QtCore import QObject, QProcess, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit,
                             QVBoxLayout, QWidget)

STATUS_COLORS = {"working": "#2e9d5b", "failed": "#d64545", "missing": "#c88a12",
                 "connected": "#2e9d5b", "not-connected": "#8a8f98", "cli-missing": "#c88a12", "unknown": "#8a8f98"}
STATUS_TEXT = {"working": "Installed / Working", "failed": "Installed / Broken", "missing": "Not installed",
               "connected": "Connected", "not-connected": "Not connected", "cli-missing": "Tool missing",
               "unknown": "Unknown"}


def make_app(name: str, icon: str) -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(name)
    app.setDesktopFileName(icon)
    app.setWindowIcon(QIcon.fromTheme(icon, QIcon.fromTheme("applications-development")))
    return app


def badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#8a8f98")
    return f'<span style="color:{color};font-weight:600">● {STATUS_TEXT.get(status, status)}</span>'


def heading(text: str, size: int = 15) -> QLabel:
    lbl = QLabel(text)
    f = lbl.font()
    f.setPointSize(size)
    f.setBold(True)
    lbl.setFont(f)
    return lbl


class Worker(QThread):
    """Runs a blocking Python callable off the UI thread."""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)

    def __init__(self, fn: Callable, *args, parent: QObject | None = None, **kwargs):
        super().__init__(parent)
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self):
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            self.failed.emit(str(exc))


class CommandDialog(QDialog):
    """Runs a (possibly pkexec-elevated) command and streams its real output."""

    def __init__(self, title: str, argv: list[str], parent: QWidget | None = None, on_finish: Callable | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 520)
        self._on_finish = on_finish
        lay = QVBoxLayout(self)
        self.status = QLabel(f"Running: {shlex.join(argv)}")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setFont(QFont("monospace", 9))
        lay.addWidget(self.log)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setEnabled(False)
        lay.addWidget(self.buttons)
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(lambda e: self.log.appendPlainText(f"[error] {self.proc.errorString()}"))
        self.proc.start(argv[0], argv[1:])

    def _read(self):
        self.log.appendPlainText(bytes(self.proc.readAllStandardOutput()).decode(errors="replace").rstrip())

    def _finished(self, code, _status):
        ok = code == 0
        if code in (126, 127) and "pkexec" in self.proc.program():
            self.status.setText("Authorization was cancelled or denied.")
        else:
            self.status.setText("Finished successfully." if ok else f"Failed (exit code {code}). See the log above.")
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setEnabled(True)
        if self._on_finish:
            self._on_finish(ok)


def run_in_terminal(argv: list[str], title: str, workdir: str | None = None) -> None:
    inner = f"{shlex.join(argv)}; echo; read -rp 'Press Enter to close…' _"
    cmd = ["konsole", "-p", f"tabtitle={title}", "-e", "bash", "-lc", inner]
    if workdir:
        cmd[1:1] = ["--workdir", workdir]
    subprocess.Popen(cmd, start_new_session=True)


def error(parent, text: str) -> None:
    QMessageBox.critical(parent, "DevOS", text)


def info(parent, text: str) -> None:
    QMessageBox.information(parent, "DevOS", text)
