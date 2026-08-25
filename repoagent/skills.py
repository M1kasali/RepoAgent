"""Local SKILL.md discovery, activation, lazy loading, and change watching."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import shutil
import threading
from types import MappingProxyType
from typing import Mapping


_SKILL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SkillManifestError(ValueError):
    pass


def _tokens(value):
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]+", str(value))
    }


def _split_values(value):
    return tuple(
        item.strip()
        for item in str(value).split(",")
        if item.strip()
    )


def _safe_relative(value):
    path = Path(str(value))
    if path.is_absolute() or ".." in path.parts:
        raise SkillManifestError("skill reference must stay inside its directory")
    return path.as_posix()


@dataclass(frozen=True)
class SkillManifest:
    skill_id: str
    name: str
    description: str
    version: str
    source: str
    path: Path
    always: bool = False
    references: tuple[str, ...] = ()
    requires: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    digest: str = ""

    def __post_init__(self):
        if not _SKILL_ID.fullmatch(self.skill_id):
            raise SkillManifestError(f"invalid skill id: {self.skill_id}")
        if not self.name.strip() or not self.description.strip():
            raise SkillManifestError("skill name and description are required")
        if not _SKILL_ID.fullmatch(self.source):
            raise SkillManifestError(f"invalid skill source: {self.source}")
        object.__setattr__(self, "path", Path(self.path).resolve())
        object.__setattr__(
            self, "references", tuple(_safe_relative(item) for item in self.references)
        )
        object.__setattr__(
            self,
            "requires",
            MappingProxyType(
                {
                    str(key): tuple(str(item) for item in value)
                    for key, value in dict(self.requires).items()
                }
            ),
        )

    @property
    def qualified_id(self):
        return f"{self.source}/{self.skill_id}"


@dataclass(frozen=True)
class ActivatedSkill:
    manifest: SkillManifest
    content: str
    score: float

    @property
    def qualified_id(self):
        return self.manifest.qualified_id


class SkillCatalog:
    def __init__(self, roots=None, *, env=None):
        self.roots = {
            str(source): Path(root).resolve()
            for source, root in dict(roots or {}).items()
        }
        self.env = os.environ if env is None else env
        self._manifests: dict[str, SkillManifest] = {}
        self._lock = threading.Lock()
        self.refresh()

    def refresh(self):
        discovered = {}
        for source, root in sorted(self.roots.items()):
            if not root.exists():
                continue
            for path in sorted(root.glob("*/SKILL.md")):
                manifest = self._parse_manifest(path, source)
                if manifest.qualified_id in discovered:
                    raise SkillManifestError(
                        f"duplicate skill: {manifest.qualified_id}"
                    )
                discovered[manifest.qualified_id] = manifest
        with self._lock:
            self._manifests = discovered
        return tuple(discovered[key] for key in sorted(discovered))

    def list(self):
        with self._lock:
            return tuple(self._manifests[key] for key in sorted(self._manifests))

    def get(self, qualified_id):
        with self._lock:
            return self._manifests.get(str(qualified_id))

    def availability(self, manifest):
        missing_bins = [
            item for item in manifest.requires.get("bins", ()) if shutil.which(item) is None
        ]
        missing_env = [
            item for item in manifest.requires.get("env", ()) if not self.env.get(item)
        ]
        return {
            "available": not missing_bins and not missing_env,
            "missing_bins": missing_bins,
            "missing_env": missing_env,
        }

    def activate(self, query, limit=3):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("skill activation limit must be a non-negative integer")
        query_tokens = _tokens(query)
        ranked = []
        for manifest in self.list():
            availability = self.availability(manifest)
            if not availability["available"]:
                continue
            searchable = _tokens(
                f"{manifest.skill_id} {manifest.name} {manifest.description}"
            )
            overlap = len(query_tokens & searchable)
            if not manifest.always and overlap == 0:
                continue
            score = 1_000_000.0 if manifest.always else float(overlap)
            ranked.append((score, manifest.qualified_id, manifest))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return tuple(
            ActivatedSkill(
                manifest=manifest,
                content=self.load_body(manifest),
                score=score,
            )
            for score, _, manifest in ranked[:limit]
        )

    def load_body(self, manifest_or_id):
        manifest = self._resolve(manifest_or_id)
        text = manifest.path.read_text(encoding="utf-8")
        _, body = self._frontmatter(text)
        return body.strip()

    def load_reference(self, manifest_or_id, reference):
        manifest = self._resolve(manifest_or_id)
        reference = _safe_relative(reference)
        if reference not in manifest.references:
            raise SkillManifestError("skill reference is not declared")
        path = (manifest.path.parent / reference).resolve()
        try:
            path.relative_to(manifest.path.parent)
        except ValueError as exc:
            raise SkillManifestError("skill reference escapes its directory") from exc
        return path.read_text(encoding="utf-8")

    def _resolve(self, manifest_or_id):
        if isinstance(manifest_or_id, SkillManifest):
            return manifest_or_id
        manifest = self.get(manifest_or_id)
        if manifest is None:
            raise KeyError(f"unknown skill: {manifest_or_id}")
        return manifest

    def _parse_manifest(self, path, source):
        raw = path.read_text(encoding="utf-8")
        values, _ = self._frontmatter(raw)
        skill_id = str(values.get("id") or path.parent.name).strip()
        references = _split_values(values.get("references", ""))
        requires = {
            "bins": _split_values(values.get("requires_bins", "")),
            "env": _split_values(values.get("requires_env", "")),
        }
        always_value = str(values.get("always", "false")).strip().lower()
        if always_value not in {"true", "false"}:
            raise SkillManifestError("skill always must be true or false")
        for reference in references:
            ref_path = (path.parent / _safe_relative(reference)).resolve()
            if not ref_path.is_file():
                raise SkillManifestError(f"missing skill reference: {reference}")
        return SkillManifest(
            skill_id=skill_id,
            name=str(values.get("name", "")).strip(),
            description=str(values.get("description", "")).strip(),
            version=str(values.get("version", "1")).strip() or "1",
            source=source,
            path=path,
            always=always_value == "true",
            references=references,
            requires=requires,
            digest="sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _frontmatter(raw):
        lines = str(raw).splitlines()
        if not lines or lines[0].strip() != "---":
            raise SkillManifestError("SKILL.md requires frontmatter")
        values = {}
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                return values, "\n".join(lines[index + 1 :])
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if ":" not in line:
                raise SkillManifestError("invalid skill frontmatter line")
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        raise SkillManifestError("SKILL.md frontmatter is not closed")


class LocalSkillPool:
    def __init__(self, catalog):
        if not isinstance(catalog, SkillCatalog):
            raise TypeError("local skill pool requires a SkillCatalog")
        self.catalog = catalog

    def search(self, query, top_k=3):
        return self.catalog.activate(query, limit=top_k)

    def invalidate(self):
        return self.catalog.refresh()


class SkillChangeWatcher:
    def __init__(self, catalog, *, interval=1.0):
        if not isinstance(catalog, SkillCatalog):
            raise TypeError("skill watcher requires a SkillCatalog")
        self.catalog = catalog
        self.interval = max(0.05, float(interval))
        self._snapshot = self._scan()
        self._stop = threading.Event()
        self._thread = None

    def poll(self):
        current = self._scan()
        if current == self._snapshot:
            return False
        self._snapshot = current
        self.catalog.refresh()
        return True

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="repoagent-skill-watcher", daemon=True
        )
        self._thread.start()
        return True

    def stop(self, timeout=1.0):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
        self._thread = None

    def _run(self):
        while not self._stop.wait(self.interval):
            self.poll()

    def _scan(self):
        snapshot = {}
        for source, root in self.catalog.roots.items():
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                stat = path.stat()
                snapshot[(source, relative)] = (
                    stat.st_mtime_ns,
                    stat.st_size,
                    hashlib.sha256(path.read_bytes()).digest(),
                )
        return snapshot


__all__ = [
    "ActivatedSkill",
    "LocalSkillPool",
    "SkillCatalog",
    "SkillChangeWatcher",
    "SkillManifest",
    "SkillManifestError",
]
