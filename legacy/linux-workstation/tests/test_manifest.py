from devos import manifest
from devos.provision import handlers


def test_manifest_loads_and_is_consistent():
    man = manifest.load()
    assert len(man.tools) > 50
    for tool in man.tools:
        if tool.method == "handler":
            assert tool.install["handler"] in handlers.INSTALLERS, tool.id
        if not tool.redistributable:
            assert tool.license_url.startswith("https://"), tool.id


def test_every_required_spec_tool_is_present():
    ids = {t.id for t in manifest.load().tools}
    for required in ("vscode", "vscodium", "android-studio", "pycharm", "intellij-idea", "webstorm", "clion",
                     "rider", "android-sdk", "flutter", "node", "python", "java", "kotlin", "rust", "go",
                     "dotnet", "php", "docker", "podman", "distrobox", "kvm", "wine", "bottles",
                     "claude-code", "codex", "gemini-cli", "ollama", "google-chrome", "git", "network"):
        assert required in ids, required


def test_android_and_chrome_are_never_bundled():
    man = manifest.load()
    for tid in ("android-sdk", "android-studio", "google-chrome", "vscode", "claude-code"):
        assert not man.get(tid).redistributable
