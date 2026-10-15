"""Accounts & AI — provider registry, real connection status and secure secrets.

Nothing here fakes a login. "Connected" is reported only when the provider's
own official CLI (or a key the user stored in the system keyring) says so.
Sign-in always runs the provider's official OAuth / device-login flow in a
terminal; DevOS never sees or stores the resulting OAuth tokens itself — each
CLI keeps them in its own (keyring-backed where supported) credential store.
API keys the user chooses to paste are stored in the Secret Service keyring
(KWallet on Plasma), never in plain text files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

KEYRING_SERVICE = "devos"
CONNECTED, NOT_CONNECTED, CLI_MISSING, UNKNOWN = "connected", "not-connected", "cli-missing", "unknown"


@dataclass
class Provider:
    id: str
    name: str
    cli: str | None
    login: list[str] | None            # official interactive / device login command
    status: list[str] | None = None    # official command whose exit 0 means "signed in"
    status_json_key: str | None = None  # if set, `status` prints JSON and this boolean key decides
    status_files: list[str] = field(default_factory=list)  # provider-managed credential files
    api_key_env: str | None = None     # env var name for an optional API key kept in the keyring
    logout: list[str] | None = None
    web: str | None = None
    note: str = ""


PROVIDERS: list[Provider] = [
    Provider("openai", "OpenAI (Codex)", "codex", ["codex", "login"], ["codex", "login", "status"],
             status_files=["~/.codex/auth.json"],
             api_key_env="OPENAI_API_KEY", logout=["codex", "logout"], web="https://platform.openai.com/"),
    Provider("anthropic", "Anthropic (Claude Code)", "claude", ["claude", "auth", "login"],
             ["claude", "auth", "status", "--json"], status_json_key="loggedIn", api_key_env="ANTHROPIC_API_KEY", logout=["claude", "auth", "logout"],
             web="https://console.anthropic.com/", note="Claude Code opens its official browser sign-in."),
    Provider("google", "Google (Gemini CLI)", "gemini", ["gemini"], status_files=["~/.gemini/oauth_creds.json"],
             api_key_env="GEMINI_API_KEY", web="https://aistudio.google.com/",
             note="Gemini CLI asks for 'Login with Google' on first start."),
    Provider("github", "GitHub", "gh", ["gh", "auth", "login", "--web", "--git-protocol", "https"],
             ["gh", "auth", "status"], logout=["gh", "auth", "logout"], web="https://github.com/"),
    Provider("gitlab", "GitLab", "glab", ["glab", "auth", "login"], ["glab", "auth", "status"],
             web="https://gitlab.com/"),
    Provider("jetbrains", "JetBrains", None, ["/opt/jetbrains/jetbrains-toolbox/bin/jetbrains-toolbox"],
             web="https://account.jetbrains.com/",
             note="Sign in inside JetBrains Toolbox or any JetBrains IDE (Help > Register)."),
    Provider("microsoft", "Microsoft / Azure", "az", ["az", "login", "--use-device-code"],
             ["az", "account", "show"], logout=["az", "logout"], web="https://portal.azure.com/"),
    Provider("docker", "Docker Hub", "docker", ["docker", "login"],
             status_files=[], web="https://hub.docker.com/",
             note="Credentials are stored through docker-credential-secretservice (system keyring)."),
    Provider("supabase", "Supabase", "supabase", ["supabase", "login"], ["supabase", "projects", "list"],
             logout=["supabase", "logout"], web="https://supabase.com/dashboard"),
    Provider("firebase", "Firebase / Google Cloud", "gcloud", ["gcloud", "auth", "login"],
             ["gcloud", "auth", "print-access-token", "--quiet"], logout=["gcloud", "auth", "revoke"],
             web="https://console.firebase.google.com/",
             note="Firebase CLI: run 'firebase login' after Google Cloud sign-in."),
    Provider("aws", "AWS", "aws", ["aws", "configure", "sso"], ["aws", "sts", "get-caller-identity"],
             web="https://console.aws.amazon.com/"),
    Provider("azure-devops", "Azure DevOps", "az", ["az", "login", "--use-device-code"],
             ["az", "account", "show"], web="https://dev.azure.com/"),
    Provider("cloudflare", "Cloudflare", "wrangler", ["wrangler", "login"], ["wrangler", "whoami"],
             logout=["wrangler", "logout"], web="https://dash.cloudflare.com/"),
]


def get(provider_id: str) -> Provider:
    return next(p for p in PROVIDERS if p.id == provider_id)


# ----------------------------------------------------------------- secrets
def _keyring():
    import keyring  # python3-keyring with the SecretService backend (KWallet / GNOME Keyring)
    backend = keyring.get_keyring()
    name = type(backend).__module__ + "." + type(backend).__name__
    if "fail" in name.lower() or "plaintext" in name.lower() or "null" in name.lower():
        raise RuntimeError(f"no secure keyring backend available ({name}); refusing to store secrets")
    return keyring


def store_api_key(provider_id: str, value: str) -> None:
    if not value.strip():
        raise ValueError("empty key")
    _keyring().set_password(KEYRING_SERVICE, f"{provider_id}:api-key", value.strip())


def load_api_key(provider_id: str) -> str | None:
    try:
        return _keyring().get_password(KEYRING_SERVICE, f"{provider_id}:api-key")
    except Exception:  # noqa: BLE001 - locked/missing wallet means "no key"
        return None


def delete_api_key(provider_id: str) -> None:
    try:
        _keyring().delete_password(KEYRING_SERVICE, f"{provider_id}:api-key")
    except Exception:  # noqa: BLE001
        pass


def agent_environment(provider_id: str) -> dict[str, str]:
    """Env for launching one agent process: injects only that provider's key, if stored."""
    p = get(provider_id)
    key = load_api_key(provider_id) if p.api_key_env else None
    return {p.api_key_env: key} if key else {}


# ------------------------------------------------------------------ status
def status(p: Provider, timeout: int = 20) -> tuple[str, str]:
    if p.cli and not shutil.which(p.cli):
        return CLI_MISSING, f"{p.cli} is not installed"
    if p.id == "docker":
        return _docker_status()
    if p.status:
        try:
            res = subprocess.run(p.status, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return UNKNOWN, "status check timed out (offline?)"
        out = (res.stdout + res.stderr).strip().splitlines()
        if p.status_json_key:
            try:
                if res.returncode == 0 and json.loads(res.stdout).get(p.status_json_key) is True:
                    return CONNECTED, "signed in"
            except ValueError:
                pass
        elif res.returncode == 0:
            return CONNECTED, (out[0] if out else "signed in")[:120]
    for f in p.status_files:
        if Path(os.path.expanduser(f)).is_file():
            return CONNECTED, "signed in (provider credentials present)"
    if p.api_key_env and load_api_key(p.id):
        return CONNECTED, "API key stored in system keyring"
    if p.id == "jetbrains":
        return UNKNOWN, "JetBrains licenses are managed inside Toolbox / the IDEs"
    return NOT_CONNECTED, "not signed in"


def _docker_status() -> tuple[str, str]:
    cfg = Path.home() / ".docker" / "config.json"
    try:
        data = json.loads(cfg.read_text())
    except (OSError, ValueError):
        return NOT_CONNECTED, "not signed in"
    if data.get("auths") or data.get("credHelpers"):
        if data.get("credsStore") == "secretservice":
            res = subprocess.run(["docker-credential-secretservice", "list"], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip() not in ("", "{}"):
                return CONNECTED, "registry credentials in system keyring"
        elif data.get("auths"):
            return CONNECTED, "registry credentials configured"
    return NOT_CONNECTED, "not signed in"


def ensure_docker_keyring_store() -> None:
    """Make `docker login` store credentials in the Secret Service instead of base64 in config.json."""
    cfg = Path.home() / ".docker" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(cfg.read_text())
    except (OSError, ValueError):
        data = {}
    if data.get("credsStore") != "secretservice" and shutil.which("docker-credential-secretservice"):
        data["credsStore"] = "secretservice"
        cfg.write_text(json.dumps(data, indent=2) + "\n")


def login_command(p: Provider) -> list[str]:
    if p.id == "docker":
        ensure_docker_keyring_store()
    return list(p.login or [])
