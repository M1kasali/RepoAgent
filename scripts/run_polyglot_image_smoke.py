#!/usr/bin/env python3
"""Grade one known-good Aider Polyglot exercise per language offline."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from repoagent.evaluation.container import DockerContainerRunner, wsl_windows_path
from repoagent.evaluation.polyglot import (
    POLYGLOT_LANGUAGES,
    PolyglotAdapter,
    PolyglotContainerGrader,
    prepare_polyglot_runner_workspace,
)
from repoagent.evaluation.schema import digest_path


SMOKE_SCHEMA = "repoagent.polyglot-image-smoke/v1"


def build_arg_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--staging-root", default=None)
    parser.add_argument("--wsl-windows-path", action="store_true")
    return parser


def _reference_mapping(instance):
    solutions = tuple(instance.runner.solution_files)
    examples = tuple(instance.example_files)
    if len(solutions) == 1 and len(examples) == 1:
        return {solutions[0]: examples[0]}
    by_suffix = {
        Path(example).name.removeprefix("example"): example for example in examples
    }
    mapping = {}
    for solution in solutions:
        suffix = Path(solution).suffix
        example = by_suffix.get(suffix)
        if example is None:
            raise ValueError(
                f"cannot map known-good reference for {instance.runner.task_id}: {solution}"
            )
        mapping[solution] = example
    return mapping


def install_known_good_reference(instance, runner_workspace):
    mapping = _reference_mapping(instance)
    for solution, example in mapping.items():
        source = instance.exercise_root / example
        target = Path(runner_workspace) / solution
        shutil.copy2(source, target)
    return mapping


def _image_identity(docker, image):
    result = subprocess.run(
        [docker, "image", "inspect", image],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Docker image inspection failed")
    records = json.loads(result.stdout)
    if len(records) != 1:
        raise RuntimeError("Docker image inspection returned an invalid record count")
    record = records[0]
    return {
        "requested": image,
        "id": str(record.get("Id", "")),
        "repo_digests": list(record.get("RepoDigests") or []),
        "size_bytes": int(record.get("Size", 0)),
    }


def run_smoke(
    *,
    dataset,
    output,
    image,
    docker="docker",
    staging_root=None,
    convert_wsl=False,
):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Polyglot image smoke output already exists: {output}")
    output.mkdir(parents=True)
    loaded = PolyglotAdapter().load(
        dataset,
        languages=POLYGLOT_LANGUAGES,
        limit=len(POLYGLOT_LANGUAGES),
    )
    if staging_root:
        Path(staging_root).mkdir(parents=True, exist_ok=True)
    container = DockerContainerRunner(
        docker_executable=docker,
        image=image,
        staging_root=staging_root,
        path_converter=wsl_windows_path if convert_wsl else None,
        memory="1g",
        cpus=1.0,
        pids_limit=128,
    )
    grader = PolyglotContainerGrader(container)
    rows = []
    for instance in loaded["instances"]:
        workspace = prepare_polyglot_runner_workspace(
            instance,
            output / "workspaces" / instance.runner.language,
        )
        mapping = install_known_good_reference(instance, workspace)
        grade = grader.grade(instance, workspace)
        rows.append(
            {
                "task_id": instance.runner.task_id,
                "language": instance.runner.language,
                "passed": bool(grade["passed"]),
                "status": grade["status"],
                "exit_code": grade["exit_code"],
                "duration_seconds": grade["duration_seconds"],
                "fixture_digest": grade["fixture_digest"],
                "reference_mapping": mapping,
                "stdout": grade["stdout"],
                "stderr": grade["stderr"],
            }
        )
    payload = {
        "schema": SMOKE_SCHEMA,
        "benchmark": loaded["benchmark"],
        "image": _image_identity(docker, image),
        "network": "none",
        "known_good_reference_only": True,
        "run_n": len(rows),
        "passes": sum(row["passed"] for row in rows),
        "rows": rows,
    }
    payload["passed"] = payload["passes"] == payload["run_n"]
    result_path = output / "results.json"
    result_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    payload["output_digest"] = digest_path(output)
    return payload


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    payload = run_smoke(
        dataset=args.dataset,
        output=args.output,
        image=args.image,
        docker=args.docker,
        staging_root=args.staging_root,
        convert_wsl=args.wsl_windows_path,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
