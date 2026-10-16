import pytest

from devos import winapp


def test_msi_is_wine_compatible(tmp_path):
    f = tmp_path / "tool.msi"
    f.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 512 + "Some Tool".encode("utf-16-le"))
    assert winapp.analyze(f).category == winapp.WINE


def test_visual_studio_msi_requires_vm(tmp_path):
    f = tmp_path / "vs.msi"
    f.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + "Microsoft Visual Studio Enterprise".encode("utf-16-le"))
    assert winapp.analyze(f).category == winapp.VM


def test_non_pe_is_rejected(tmp_path):
    f = tmp_path / "x.exe"
    f.write_bytes(b"#!/bin/sh\n")
    with pytest.raises(ValueError):
        winapp.analyze(f)
