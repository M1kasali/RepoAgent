from pathlib import Path

import pytest

from repoagent.evaluation.polyglot import POLYGLOT_TEST_COMMANDS
from scripts.run_polyglot_image_smoke import _reference_mapping, build_arg_parser
from scripts.run_polyglot_campaign import main as campaign_main


IMAGE_ROOT = Path("benchmarks/polyglot-image")


def test_polyglot_image_defines_all_frozen_toolchains_and_offline_java():
    dockerfile = (IMAGE_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.startswith(
        "FROM eclipse-temurin:21-jdk-jammy@sha256:"
    )
    for build_arg in (
        "GO_VERSION=1.21.5",
        "RUST_VERSION=1.83.0",
        "NODE_VERSION=20.18.1",
        "GRADLE_VERSION=8.7",
    ):
        assert build_arg in dockerfile
    assert "libboost-date-time-dev" in dockerfile
    assert "libboost-all-dev" not in dockerfile
    assert POLYGLOT_TEST_COMMANDS["java"] == (
        "gradle",
        "test",
        "--offline",
        "--no-daemon",
    )
    assert "GRADLE_USER_HOME=/opt/gradle-cache gradle test" in dockerfile


def test_polyglot_image_build_context_is_allowlisted():
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8").splitlines()

    assert dockerignore[0] == "*"
    assert "!benchmarks/polyglot-image/**" in dockerignore
    assert not any(".env" in line and line.startswith("!") for line in dockerignore)


def test_polyglot_image_runtime_scripts_are_fail_closed_and_network_independent():
    entrypoint = (IMAGE_ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    cpp = (IMAGE_ROOT / "cpp-test.sh").read_text(encoding="utf-8")
    npm = (IMAGE_ROOT / "npm-test.sh").read_text(encoding="utf-8")

    assert "set -eu" in entrypoint
    assert "set -eu" in cpp
    assert "set -eu" in npm
    assert "cp -a /opt/gradle-cache/." in entrypoint
    assert 'chmod -R u+w "${GRADLE_USER_HOME}"' in entrypoint
    assert 'HOME="${PWD}/.repoagent-home"' in entrypoint
    assert 'GOTMPDIR="${HOME}/tmp/go"' in entrypoint
    assert "export TMPDIR=/tmp" in entrypoint
    assert "/npm-install/node_modules" in npm


def test_known_good_reference_mapping_handles_multi_file_cpp():
    class Runner:
        task_id = "cpp/example"
        solution_files = ("example.cpp", "example.h")

    class Instance:
        runner = Runner()
        example_files = (".meta/example.cpp", ".meta/example.h")

    assert _reference_mapping(Instance()) == {
        "example.cpp": ".meta/example.cpp",
        "example.h": ".meta/example.h",
    }


def test_image_smoke_accepts_explicit_windows_backed_staging_root():
    args = build_arg_parser().parse_args(
        [
            "--dataset",
            "dataset",
            "--output",
            "output",
            "--image",
            "image",
            "--staging-root",
            "staging",
        ]
    )

    assert args.staging_root == "staging"


def test_formal_campaign_rejects_mutable_image_before_loading_dataset():
    with pytest.raises(ValueError, match="image@sha256"):
        campaign_main(
            [
                "--dataset",
                "missing",
                "--output",
                "output",
                "--image",
                "repoagent-polyglot:latest",
                "--max-input-tokens-per-call",
                "12000",
                "--hard-cost-cap-usd",
                "1",
                "--input-cost-per-1m-usd",
                "1",
                "--output-cost-per-1m-usd",
                "1",
                "--pricing-source",
                "test",
            ]
        )
