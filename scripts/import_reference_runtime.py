"""Reproduce the retained runtime import from a pinned local source checkout."""

import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess


COMMIT = "c3a7a1d9032b539ca7a7cc52e46c9c0e29d5cdc3"
ROOT = Path(__file__).resolve().parents[1]
TEST_PREFIXES = (
    "test_call_efficiency", "test_spine_", "test_subagent", "test_turn_evidence",
    "test_evolver_", "test_tui_rpc", "test_cli_tui", "test_tui_turn",
)


def adapt(text, source):
    # Keep protocol identifiers, plugin metadata and attribution unchanged.
    text = re.sub(r"(?<![\w./])pico\.", "repoagent.harness.", text)
    # Move source-path contracts too, including Evolver's protected kernel.
    text = text.replace('"pico/', '"repoagent/harness/')
    text = text.replace("'pico/", "'repoagent/harness/")
    text = text.replace('"PICO/', '"REPOAGENT/HARNESS/')
    text = re.sub(r"(?m)^(\s*)import pico$", r"\1import repoagent.harness", text)
    text = text.replace("from pico import", "from repoagent.harness import")
    text = text.replace('pkg_files("pico")', 'pkg_files("repoagent.harness")')
    text = text.replace("PICO_", "REPOAGENT_HARNESS_")
    text = text.replace('".pico"', '".repoagent-harness"')
    text = text.replace("'.pico'", "'.repoagent-harness'")
    text = text.replace("~/.pico", "~/.repoagent-harness")
    text = text.replace('"~/.pico/workspace"', '"~/.repoagent-harness/workspace"')
    text = text.replace('version("pico-harness")', 'version("repoagent")')
    text = text.replace('logger.enable("pico")', 'logger.enable("repoagent.harness")')
    text = text.replace('logger.disable("pico")', 'logger.disable("repoagent.harness")')
    if source.startswith(("pico/cli/", "ui-tui/", "tests/")):
        text = re.sub(r"\bpico (?=(?:run|gateway|channels|provider|onboard|doctor|sessions|skills|evolve|status|sandbox|plugins|tracing)\b)", "repoagent ", text)
        text = text.replace("starting pico", "starting repoagent")
    if source.startswith(("pico/cli/", "ui-tui/src/", "tests/")):
        text = re.sub(r"\bPico\b", "RepoAgent", text)
    if source == "pico/cli/onboard_commands.py":
        text = text.replace('table.add_row("pico",', 'table.add_row("repoagent",')
    if source.startswith("tests/") and "/integration/" not in source:
        text = text.replace('Path(__file__).resolve().parents[1]', 'Path(__file__).resolve().parents[2]')
        text = text.replace('Path(__file__).resolve().parent.parent', 'Path(__file__).resolve().parents[2]')
        text = text.replace('Path(__file__).parent.parent', 'Path(__file__).parents[2]')
        if source == "tests/test_tui_rpc_session_init_bundle.py":
            text = text.replace('/ "pico" /', '/ "repoagent" / "harness" /')
        if source == "tests/test_evolver_candidate_manifest.py":
            text = text.replace('/ "pico"', '/ "repoagent/harness"')
            text = text.replace('(tmp_path / "repoagent/harness").mkdir()', '(tmp_path / "repoagent/harness").mkdir(parents=True)')
        text = text.replace('"pico evolve"', '"repoagent evolve"')
    if source == "pico/__init__.py":
        text = text.replace('__version__ = "0.0.0+unknown"', '__version__ = "0.1.7"')
    if source == "pico/product.py":
        text = text.replace('PRODUCT_NAME = "Pico"', 'PRODUCT_NAME = "RepoAgent"')
        text = text.replace('CLI_NAME = "pico"', 'CLI_NAME = "repoagent"')
        text = text.replace('DISTRIBUTION_NAME = "pico-harness"', 'DISTRIBUTION_NAME = "repoagent"')
        text = text.replace('".pico"', '".repoagent-harness"')
        text = text.replace('"~/.pico/workspace"', '"~/.repoagent-harness/workspace"')
    if source == "pico/cli/commands.py":
        text = text.replace('name="pico"', 'name="repoagent"').replace("Pico -", "RepoAgent -")
        text = text.replace("        app()", '        app(prog_name="repoagent")')
    if source == "pico/cli/tui_commands.py":
        text = text.replace('Path(__file__).resolve().parent.parent.parent / "ui-tui"',
                            'Path(__file__).resolve().parents[3] / "ui-tui"')
    if source == "pico/sandbox/boxlite_executor.py":
        text = text.replace('extra_kwargs["network"] = "none"',
                            'extra_kwargs["network"] = boxlite.NetworkSpec(mode="disabled")')
        text = text.replace('extra_kwargs["allow_net"] = self._allow_net',
                            'extra_kwargs["network"] = boxlite.NetworkSpec(mode="enabled", allow_net=self._allow_net)')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    ref = args.reference.resolve()
    head = subprocess.check_output(["git", "-C", str(ref), "rev-parse", "HEAD"], text=True).strip()
    if head != COMMIT:
        parser.error(f"expected {COMMIT}, got {head}")
    files = subprocess.check_output(["git", "-C", str(ref), "ls-files", "pico", "ui-tui", "tests", "benchmarks", "scripts/setup_small_real_subject.py", "docs/examples/evolve_appworld.yaml"], text=True).splitlines()
    rows = []
    for source in files:
        if source.startswith("pico/"):
            target = "repoagent/harness/" + source[5:]
        elif source.startswith("ui-tui/"):
            target = source
        elif source == "tests/conftest.py" or Path(source).name.startswith(TEST_PREFIXES) or source == "tests/integration/_evolver_process_bench.py":
            target = "tests/harness_reference/" + source[6:]
        elif source.startswith(("benchmarks/appworld/", "benchmarks/picobench/", "benchmarks/evolver/")) or source in {"benchmarks/__init__.py", "docs/examples/evolve_appworld.yaml", "scripts/setup_small_real_subject.py"}:
            target = source
        else:
            continue
        # Read committed blobs, never local generated files or credentials.
        raw = subprocess.check_output(["git", "-C", str(ref), "show", f"{COMMIT}:{source}"])
        try:
            result = adapt(raw.decode("utf-8"), source).encode("utf-8")
        except UnicodeDecodeError:
            result = raw
        dest = ROOT / target
        if args.check:
            if not dest.is_file() or dest.read_bytes() != result:
                raise SystemExit(f"Source mapping mismatch: {target}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(result)
        rows.append({"source": source, "destination": target,
                     "source_sha256": hashlib.sha256(raw).hexdigest(),
                     "destination_sha256": hashlib.sha256(result).hexdigest()})
    manifest = {"reference_commit": COMMIT, "transformation": "scripts/import_reference_runtime.py:adapt", "files": rows}
    if not args.check:
        (ROOT / "LICENSES/pico-runtime-source.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{'Verified' if args.check else 'Imported'} {len(rows)} retained source files")


if __name__ == "__main__":
    main()
