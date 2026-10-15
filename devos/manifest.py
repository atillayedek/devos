"""Loads and validates provisioning/tools.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import paths

VALID_METHODS = {"apt", "apt-repo", "handler", "npm", "uv-tool", "flatpak"}


@dataclass
class Tool:
    id: str
    name: str
    category: str
    stage: str
    required: bool
    redistributable: bool
    license: str
    install: dict[str, Any]
    health: list[dict[str, Any]]
    license_url: str | None = None
    desktop: str | None = None

    @property
    def method(self) -> str:
        return self.install["method"]


@dataclass
class Manifest:
    stages: list[str]
    repos: dict[str, dict[str, Any]]
    tools: list[Tool] = field(default_factory=list)

    def get(self, tool_id: str) -> Tool:
        for tool in self.tools:
            if tool.id == tool_id:
                return tool
        raise KeyError(tool_id)

    def by_stage(self, stage: str) -> list[Tool]:
        return [t for t in self.tools if t.stage == stage]

    def proprietary(self) -> list[Tool]:
        return [t for t in self.tools if not t.redistributable]


class ManifestError(ValueError):
    pass


def load(path: Path | None = None) -> Manifest:
    path = path or paths.MANIFEST
    if not path.exists():
        # Running from a source checkout
        path = Path(__file__).resolve().parent.parent / "provisioning" / "tools.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest = Manifest(stages=data["stages"], repos=data.get("repos", {}))
    seen: set[str] = set()
    for raw in data["tools"]:
        tool = Tool(
            id=raw["id"],
            name=raw["name"],
            category=raw["category"],
            stage=raw["stage"],
            required=bool(raw.get("required", False)),
            redistributable=bool(raw["redistributable"]),
            license=raw.get("license", ""),
            license_url=raw.get("license_url"),
            install=raw["install"],
            health=raw.get("health", []),
            desktop=raw.get("desktop"),
        )
        if tool.id in seen:
            raise ManifestError(f"duplicate tool id {tool.id}")
        seen.add(tool.id)
        if tool.stage not in manifest.stages:
            raise ManifestError(f"{tool.id}: unknown stage {tool.stage}")
        if tool.method not in VALID_METHODS:
            raise ManifestError(f"{tool.id}: unknown install method {tool.method}")
        if tool.method == "apt-repo" and tool.install["repo"] not in manifest.repos:
            raise ManifestError(f"{tool.id}: unknown repo {tool.install['repo']}")
        if not tool.redistributable and not tool.license_url:
            raise ManifestError(f"{tool.id}: proprietary tools need a license_url the user accepts")
        if not tool.health:
            raise ManifestError(f"{tool.id}: every tool needs at least one health check")
        manifest.tools.append(tool)
    return manifest
