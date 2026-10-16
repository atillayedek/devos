"""Vibe Coding — describe an app in natural language; a real agent builds it in ~/Developer."""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                             QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from .. import accounts, vibe
from .common import Worker, error, heading, make_app, run_in_terminal


class VibeWindow(QMainWindow):
    def __init__(self, project_path: str | None = None):
        super().__init__()
        self.setWindowTitle("Vibe Coding — DevOS")
        self.resize(820, 620)
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.addWidget(heading("Vibe Coding", 18))
        lay.addWidget(QLabel("Describe what you want to build. The selected agent works on real files in "
                             "~/Developer, runs real commands and asks before risky actions. Your IDE sees "
                             "every change live.", wordWrap=True))
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("e.g. Create a production-ready marketplace with Next.js and Supabase: "
                                       "auth, listings, search, Stripe-ready checkout, tests and a README.")
        lay.addWidget(self.prompt, 1)
        form = QFormLayout()
        self.name = QLineEdit(placeholderText="my-app")
        if project_path:
            self.name.setText(project_path.rstrip("/").split("/")[-1])
        form.addRow("Project name", self.name)
        self.clone = QLineEdit(placeholderText="optional: GitHub URL to clone instead of starting empty")
        form.addRow("Start from repository", self.clone)
        agent_row = QHBoxLayout()
        self.agent = QComboBox()
        for a in vibe.AGENTS:
            self.agent.addItem(a.name, a)
        self.agent.currentIndexChanged.connect(self.check_agent)
        self.agent_status = QLabel()
        self.sign_in = QPushButton("Sign in")
        self.sign_in.clicked.connect(self.do_sign_in)
        agent_row.addWidget(self.agent)
        agent_row.addWidget(self.agent_status, 1)
        agent_row.addWidget(self.sign_in)
        form.addRow("Agent", agent_row)
        self.model = QComboBox(editable=True)
        form.addRow("Local model", self.model)
        self.ide = QComboBox()
        self.ide.addItem("Don't open an IDE", None)
        for key, label in vibe.available_ides().items():
            self.ide.addItem(label, key)
        if self.ide.count() > 1:
            self.ide.setCurrentIndex(1)
        form.addRow("Open in IDE", self.ide)
        self.git = QCheckBox("Initialise a Git repository")
        self.git.setChecked(True)
        form.addRow("", self.git)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch()
        self.start = QPushButton("Start building")
        self.start.setDefault(True)
        self.start.clicked.connect(self.launch)
        row.addWidget(self.start)
        lay.addLayout(row)
        self.setCentralWidget(root)
        QTimer.singleShot(0, self.check_agent)

    def current(self) -> vibe.Agent:
        return self.agent.currentData()

    def check_agent(self):
        agent = self.current()
        self.model.setEnabled(agent.id == "local")
        self.agent_status.setText("checking…")
        self.sign_in.hide()
        if not vibe.agent_available(agent):
            self.agent_status.setText(f"<span style='color:#d64545'>{agent.binary} is not installed — "
                                      "Developer Center › Tools can install it</span>")
            self.start.setEnabled(False)
            return
        self._w = Worker(vibe.agent_signed_in, agent)
        self._w.done.connect(lambda res, a=agent: self._status(a, *res))
        self._w.start()

    def _status(self, agent, ok, detail):
        if agent is not self.current():
            return
        color = "#2e9d5b" if ok else "#c88a12"
        self.agent_status.setText(f"<span style='color:{color}'>{detail}</span>")
        self.sign_in.setVisible(not ok and agent.provider is not None)
        self.start.setEnabled(ok)
        if agent.id == "local":
            self.model.clear()
            self.model.addItems(vibe.ollama_models() or ["qwen2.5-coder:7b"])
            if not ok:
                self.agent_status.setText(self.agent_status.text() + " — type a model name and press Start to "
                                          "download it with Ollama first")
                self.start.setEnabled(True)

    def do_sign_in(self):
        p = accounts.get(self.current().provider)
        run_in_terminal(accounts.login_command(p), f"Sign in: {p.name}")
        QTimer.singleShot(15000, self.check_agent)

    def launch(self):
        name = self.name.text().strip() or "my-app"
        prompt = self.prompt.toPlainText().strip()
        agent = self.current()
        try:
            if agent.id == "local" and self.model.currentText() not in vibe.ollama_models():
                vibe.pull_model(self.model.currentText())
                error(self, "The model is downloading in a terminal. Press Start again when it has finished.")
                return
            workdir = vibe.prepare_workspace(name, git_init=self.git.isChecked(),
                                             clone_url=self.clone.text().strip() or None)
            vibe.launch(agent, workdir, prompt, model=self.model.currentText() or None, ide=self.ide.currentData())
        except Exception as exc:  # noqa: BLE001
            error(self, str(exc))
            return
        self.statusBar().showMessage(f"{agent.name} is working in {workdir}", 15000)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    app = make_app("Vibe Coding", "devos-vibe")
    win = VibeWindow(argv[0] if argv else None)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
