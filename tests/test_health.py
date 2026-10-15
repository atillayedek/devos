import sys

from devos import health
from devos.manifest import Tool


def tool(checks, required=True):
    return Tool(id="t", name="T", category="c", stage="languages", required=required, redistributable=True,
                license="MIT", install={"method": "apt", "packages": []}, health=checks)


def test_working_tool_reports_version():
    r = health.check_tool(tool([{"cmd": [sys.executable, "--version"]}]))
    assert r["status"] == "working"
    assert "Python" in r["version"]


def test_missing_binary_is_missing_not_working():
    r = health.check_tool(tool([{"cmd": ["devos-definitely-not-installed", "--version"]}]))
    assert r["status"] == "missing"


def test_broken_binary_is_failed():
    r = health.check_tool(tool([{"cmd": [sys.executable, "-c", "import sys; sys.exit(3)"]}]))
    assert r["status"] == "failed"


def test_expect_mismatch_fails():
    r = health.check_tool(tool([{"cmd": [sys.executable, "--version"], "expect": "^NotPython"}]))
    assert r["status"] == "failed"


def test_partial_install_is_failed_not_missing():
    r = health.check_tool(tool([{"cmd": [sys.executable, "--version"]},
                                {"cmd": ["devos-definitely-not-installed"]}]))
    assert r["status"] == "failed"


def test_missing_path_special():
    r = health.check_tool(tool([{"special": "path:/nonexistent/devos"}]))
    assert r["status"] == "missing"
