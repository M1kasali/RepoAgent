"""Read-only operational commands for the RepoAgent product CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import sys

from .config import load_project_env, load_user_env, provider_env
from .cron import CronService
from .evolver import ActivationRegistry, EvolutionLabel, EvolutionLedger
from .gateway import GatewayLease
from .paths import workspace_state_root
from .providers.profiles import BUILTIN_MODEL_PROFILES
from .session_store import SessionStore
from .skills import SkillCatalog
from .workspace import WorkspaceContext


def _workspace(cwd):
    workspace = WorkspaceContext.build(cwd)
    load_user_env()
    load_project_env(workspace.repo_root)
    return workspace


def doctor_report(cwd="."):
    workspace = _workspace(cwd)
    state_root = workspace_state_root(workspace.repo_root)
    checks = [
        {
            "id": "workspace",
            "status": "pass" if Path(workspace.repo_root).is_dir() else "fail",
            "detail": workspace.repo_root,
        },
        {
            "id": "state_root",
            "status": (
                "pass"
                if os.access(state_root.parent, os.W_OK)
                else "fail"
            ),
            "detail": str(state_root),
        },
        {
            "id": "python",
            "status": "pass" if sys.version_info >= (3, 10) else "fail",
            "detail": platform.python_version(),
        },
        {
            "id": "git",
            "status": "pass" if shutil.which("git") else "fail",
            "detail": shutil.which("git") or "not found",
        },
    ]
    return {
        "schema": "repoagent.doctor/v1",
        "status": "pass" if all(row["status"] == "pass" for row in checks) else "fail",
        "checks": checks,
    }


def provider_report(provider=None):
    names = (provider,) if provider else tuple(BUILTIN_MODEL_PROFILES)
    rows = []
    for name in names:
        if name not in BUILTIN_MODEL_PROFILES:
            raise ValueError(f"unknown provider profile: {name}")
        profile = BUILTIN_MODEL_PROFILES[name]
        rows.append(
            {
                **profile.to_dict(),
                "credential_configured": (
                    True
                    if not profile.credential_envs
                    else any(provider_env(env_name) for env_name in profile.credential_envs)
                ),
            }
        )
    return {"schema": "repoagent.providers/v1", "providers": rows}


def _session_summary(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            "id": str(payload.get("id", path.stem)),
            "created_at": str(payload.get("created_at", "")),
            "workspace_root": str(payload.get("workspace_root", "")),
            "history_count": len(payload.get("history", [])),
            "revision": int(payload.get("_revision", 0)),
            "status": "valid",
        }
    except (OSError, ValueError, TypeError):
        return {"id": path.stem, "status": "corrupt"}


def session_report(cwd=".", session_id=None):
    workspace = _workspace(cwd)
    store = SessionStore(workspace_state_root(workspace.repo_root) / "sessions")
    paths = (
        (store.path(session_id),)
        if session_id
        else tuple(sorted(store.root.glob("*.json"), key=lambda item: item.name))
    )
    if session_id and not paths[0].is_file():
        raise ValueError(f"unknown session: {session_id}")
    return {
        "schema": "repoagent.sessions/v1",
        "workspace_root": workspace.repo_root,
        "sessions": [_session_summary(path) for path in paths],
    }


def sandbox_report(*, require_isolation=False):
    isolated = False
    return {
        "schema": "repoagent.sandbox/v1",
        "identity": "direct_host",
        "is_isolated": isolated,
        "isolation_required": bool(require_isolation),
        "status": "pass" if not require_isolation or isolated else "fail",
    }


def gateway_report(cwd="."):
    workspace = _workspace(cwd)
    status = GatewayLease(workspace_state_root(workspace.repo_root)).status()
    return {
        "schema": "repoagent.gateway-health/v1",
        "status": "healthy" if status["running"] else "stopped",
        "lease": status,
    }


def directory_channel_report(root):
    root = Path(root).resolve()
    return {
        "schema": "repoagent.channel-status/v1",
        "channel": "directory",
        "root": str(root),
        "inbox_pending": len(tuple((root / "inbox").glob("*.json"))),
        "outbox_messages": len(tuple((root / "outbox").glob("*.json"))),
        "processed_messages": len(tuple((root / "processed").glob("*.json"))),
    }


def cron_report(cwd="."):
    workspace = _workspace(cwd)
    path = workspace_state_root(workspace.repo_root) / "cron" / "jobs.json"
    jobs = CronService(path).list()
    return {
        "schema": "repoagent.cron-status/v1",
        "path": str(path),
        "jobs": [job.to_dict() for job in jobs],
    }


def skill_report(cwd=".", skill_id=None):
    workspace = _workspace(cwd)
    state_root = workspace_state_root(workspace.repo_root)
    catalog = SkillCatalog(
        {
            "workspace": Path(workspace.repo_root) / "skills",
            "local": state_root / "skills",
        }
    )
    manifests = catalog.list()
    if skill_id:
        manifests = tuple(
            item
            for item in manifests
            if item.qualified_id == skill_id or item.skill_id == skill_id
        )
        if not manifests:
            raise ValueError(f"unknown skill: {skill_id}")
    return {
        "schema": "repoagent.skills/v1",
        "skills": [
            {
                "id": item.qualified_id,
                "name": item.name,
                "description": item.description,
                "version": item.version,
                "always": item.always,
                "digest": item.digest,
                **catalog.availability(item),
            }
            for item in manifests
        ],
    }


def evolver_report(cwd="."):
    workspace = _workspace(cwd)
    ledger = EvolutionLedger(
        workspace_state_root(workspace.repo_root) / "evolver" / "ledger.jsonl"
    )
    events = ledger.events()
    registry = ActivationRegistry(ledger)
    routes = {}
    for label in EvolutionLabel:
        active = registry.resolve(label)
        routes[label.value] = (
            {
                "candidate_id": active.candidate_id,
                "commit_sha": active.commit_sha,
                "evidence_digest": active.evidence_digest,
                "activation_event_id": active.activation_event_id,
            }
            if active
            else None
        )
    return {
        "schema": "repoagent.evolver-status/v1",
        "ledger": str(ledger.path),
        "ledger_valid": True,
        "event_count": len(events),
        "routes": routes,
    }


def print_json(payload):
    print(json.dumps(payload, indent=2, sort_keys=True))


__all__ = [
    "doctor_report",
    "directory_channel_report",
    "evolver_report",
    "cron_report",
    "gateway_report",
    "print_json",
    "provider_report",
    "sandbox_report",
    "session_report",
    "skill_report",
]
