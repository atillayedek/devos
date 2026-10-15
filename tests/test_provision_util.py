import pytest

from devos.provision.handlers import _latest_stable
from devos.provision.util import ProvisionError, checksum_from_sums, download

SUMS = """\
4f1e  other.tar.gz
ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12 *bun-linux-x64.zip
"""


def test_checksum_from_sums_picks_named_file():
    assert checksum_from_sums(SUMS, "bun-linux-x64.zip").startswith("ab12")


def test_checksum_single_digest_file():
    digest = "a" * 64
    assert checksum_from_sums(digest + "\n", "anything") == digest


def test_checksum_missing_raises():
    with pytest.raises(ProvisionError):
        checksum_from_sums(SUMS, "nope.zip")


def test_download_refuses_unverified_and_plain_http():
    with pytest.raises(ProvisionError):
        download("http://example.com/x", sha256="0" * 64)
    with pytest.raises(ProvisionError):
        download("https://example.com/x")


LISTING = """
  platforms;android-35        | 2 | Android SDK Platform 35
  platforms;android-36        | 1 | Android SDK Platform 36
  platforms;android-CANARY    | 1 | preview
  build-tools;36.0.0          | 36.0.0 | Android SDK Build-Tools 36
  build-tools;36.1.0-rc1      | 36.1.0 rc1 | Android SDK Build-Tools 36.1-rc1
  ndk;28.1.13356709           | 28.1.13356709 | NDK
  ndk;29.0.13113456-rc1       | 29.0 rc1 | NDK
"""


def test_latest_stable_ignores_previews_and_rcs():
    assert _latest_stable(LISTING, "platforms;android-", r"(\d+)") == "platforms;android-36"
    assert _latest_stable(LISTING, "build-tools;", r"(\d+)\.(\d+)\.(\d+)") == "build-tools;36.0.0"
    assert _latest_stable(LISTING, "ndk;", r"(\d+)\.(\d+)\.(\d+)") == "ndk;28.1.13356709"
