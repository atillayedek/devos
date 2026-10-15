"""Vibe Coding core: real workspaces in ~/Developer driven by real coding agents.

The agent is the provider's official CLI running in a real terminal inside the
project directory, so it creates files, runs commands, installs dependencies,
builds, starts dev servers, etc. on the actual system — and every agent keeps its
own approval prompt for commands and edits (DevOS never enables "yolo"/auto-approve
modes). The same files are opened in the user's IDE, which sees every change live.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import urllib.request
import json
from dataclasses import dataclass
from pathlib import Path

from . import accounts, paths


@dataclass
class Agent:
    id: str
    name: str
    binary: str
    provider: str | None   # accounts provider id for sign-in status


AGENTS = [
    Agent("claude", "Claude Code", "claude", "anthropic"),
    Agent("codex", "OpenAI Codex", "codex", "openai"),
    Agent("gemini", "Gemini CLI", "gemini", "google"),
    Agent("local", "Local model (Aider + Ollama)", "aider", None),
]

IDES = {
    "code": "Visual Studio Code", "codium": "VSCodium", "pycharm": "PyCharm", "intellij-idea": "IntelliJ IDEA",
    "webstorm": "WebStorm", "android-studio": "Android Studio", "kate": "Kate",
}

AGENT_GUIDE = """# DevOS workspace

This project lives in a DevOS developer workstation. Preinstalled toolchains you can use directly:
Node.js LTS (npm, pnpm, yarn, bun, deno, TypeScript), Python 3 (uv, pip, pipx, poetry, venv),
Java 21 (+ Temurin 17/25), Gradle, Maven, Kotlin, Android SDK (adb, emulator, sdkmanager) and
Android Studio, Flutter/Dart, GCC/Clang/CMake/Ninja, .NET SDK, Rust (cargo), Go, PHP + Composer,
PostgreSQL, MariaDB, SQLite, Redis, Docker/Podman (+ compose), kubectl/helm, Supabase/Firebase CLIs.

Rules for agents working here:
- Work only inside this project directory unless the user asks otherwise.
- Ask the user before system-level changes (sudo, apt, systemctl, editing files outside the project,
  deleting data, pushing to remotes, deploying, or spending money on cloud resources).
- Database services are stopped by default; start them with `devos-service start postgresql` etc.
- Preview web apps with the dev server and open them with `xdg-open http://localhost:<port>`.
"""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "project"


def project_dir(name: str) -> Path:
    return paths.developer_dir() / slugify(name)


def agent_available(agent: Agent) -> bool:
    return shutil.which(agent.binary) is not None


def agent_signed_in(agent: Agent) -> tuple[bool, str]:
    if agent.provider is None:
        models = ollama_models()
        return (bool(models), f"{len(models)} local model(s)" if models else "no local model downloaded")
    st, detail = accounts.status(accounts.get(agent.provider))
    return st == accounts.CONNECTED, detail


def ollama_models() -> list[str]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except OSError:
        return []


def prepare_workspace(name: str, *, git_init: bool = True, clone_url: str | None = None) -> Path:
    d = project_dir(name)
    if clone_url:
        if d.exists() and any(d.iterdir()):
            raise RuntimeError(f"{d} already exists and is not empty")
        d.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", clone_url, str(d)], check=True)
    else:
        d.mkdir(parents=True, exist_ok=True)
        if git_init and not (d / ".git").exists():
            subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True)
    for guide in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        f = d / guide
        if not f.exists():
            f.write_text(AGENT_GUIDE)
    return d


def agent_command(agent: Agent, prompt: str, model: str | None = None) -> list[str]:
    if agent.id == "claude":
        return ["claude", prompt] if prompt else ["claude"]
    if agent.id == "codex":
        return ["codex", prompt] if prompt else ["codex"]
    if agent.id == "gemini":
        return ["gemini", "--prompt-interactive", prompt] if prompt else ["gemini"]
    if agent.id == "local":
        cmd = ["aider", "--model", f"ollama_chat/{model or (ollama_models() or ['qwen2.5-coder'])[0]}",
               "--no-auto-commits"]
        if not prompt:
            return cmd
        # aider's --message handles one request and exits; continue in an interactive session afterwards
        return ["bash", "-c", f"{shlex.join(cmd + ['--message', prompt])}; exec {shlex.join(cmd)}"]
    raise ValueError(agent.id)


def terminal_command(workdir: Path, argv: list[str], title: str) -> list[str]:
    inner = f"{shlex.join(argv)}; echo; echo '[agent exited — this terminal stays open]'; exec bash -l"
    if shutil.which("konsole"):
        return ["konsole", "--workdir", str(workdir), "-p", f"tabtitle={title}", "-e", "bash", "-lc", inner]
    return ["x-terminal-emulator", "-e", "bash", "-lc", f"cd {shlex.quote(str(workdir))} && {inner}"]


def launch(agent: Agent, workdir: Path, prompt: str, *, model: str | None = None,
           ide: str | None = None, terminal: bool = True) -> subprocess.Popen:
    env = dict(os.environ)
    if agent.id == "local":
        env["OLLAMA_API_BASE"] = "http://127.0.0.1:11434"
    elif agent.provider:
        env.update(accounts.agent_environment(agent.provider))
    if ide:
        open_in_ide(ide, workdir)
    argv = agent_command(agent, prompt, model)
    cmd = terminal_command(workdir, argv, f"Vibe · {agent.name} · {workdir.name}") if terminal else argv
    return subprocess.Popen(cmd, cwd=workdir, env=env, start_new_session=True)


def open_in_ide(ide: str, workdir: Path) -> None:
    binary = shutil.which(ide)
    if not binary:
        raise RuntimeError(f"{IDES.get(ide, ide)} is not installed")
    subprocess.Popen([binary, str(workdir)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def available_ides() -> dict[str, str]:
    return {k: v for k, v in IDES.items() if shutil.which(k)}


def pull_model(model: str) -> subprocess.Popen:
    """User-requested model download, shown in a terminal with Ollama's own progress."""
    return subprocess.Popen(terminal_command(Path.home(), ["ollama", "pull", model], f"ollama pull {model}"),
                            start_new_session=True)
