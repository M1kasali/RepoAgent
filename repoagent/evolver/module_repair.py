"""Frozen training-only, single-module repair messages and bounded parsing."""

import ast
from dataclasses import dataclass
import json
import re

from .contracts import _benchmark_path
from .evaluation import payload_digest


class ModuleRepairError(ValueError):
    pass


@dataclass(frozen=True)
class ModuleRepairProtocol:
    path: str
    public_names: tuple[str, ...]
    why: str
    definition: str
    training_failures_json: str
    max_retries: int = 2
    max_bytes: int = 32768

    def __post_init__(self):
        _benchmark_path(self.path)
        names = tuple(self.public_names)
        if not names or any(not isinstance(name, str) or not name.isidentifier() for name in names):
            raise ValueError("repair requires public function names")
        object.__setattr__(self, "public_names", names)
        if not self.why or not isinstance(self.definition, str):
            raise ValueError("repair requires a failure class and definition")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 2:
            raise ValueError("repair supports at most two parse retries")
        if type(self.max_bytes) is not int or not 0 < self.max_bytes <= 32768:
            raise ValueError("invalid repair module byte cap")
        failures = json.loads(self.training_failures_json)
        if not isinstance(failures, dict) or not failures:
            raise ValueError("repair requires training failure records")
        for task, record in failures.items():
            if (not isinstance(task, str) or not task or not isinstance(record, dict)
                    or set(record) != {"description", "cases"}
                    or not isinstance(record["description"], str)
                    or not isinstance(record["cases"], list)
                    or not record["cases"]
                    or any(not isinstance(case, dict) for case in record["cases"])):
                raise ValueError("invalid training failure record")
        object.__setattr__(self, "training_failures_json", json.dumps(failures, sort_keys=True, allow_nan=False))

    @property
    def task_ids(self):
        return tuple(json.loads(self.training_failures_json))

    def descriptor(self):
        return {"kind": "single-module-repair/v1", "path": self.path,
                "public_names": list(self.public_names), "why": self.why,
                "definition": self.definition, "max_retries": self.max_retries,
                "max_bytes": self.max_bytes, "task_ids": list(self.task_ids),
                "failure_digest": payload_digest(json.loads(self.training_failures_json))}

    def messages(self, source):
        lines = []
        for task, record in sorted(json.loads(self.training_failures_json).items()):
            lines.append(f"- task {task} ({record['description']}):")
            for case in record["cases"]:
                if "fn" not in case:
                    lines.append(f"    {case.get('detail', 'no detail')}")
                    continue
                args = ", ".join(repr(arg) for arg in case.get("args", []))
                lines.append(
                    f"    {case['fn']}({args}) -> {case.get('actual')!r}, expected {case.get('expect')!r}"
                    + (f" [raised: {case['error']}]" if case.get("error") else ""))
        return [
            {"role": "system", "content": (
                "You repair a small Python module. You always answer with the complete corrected "
                "module inside exactly one fenced code block and no other text.")},
            {"role": "user", "content": (
                f"File: {self.path}\n\nCurrent source:\n\n```python\n{source}\n```\n\n"
                f"Failure class under repair: {self.why}\n{self.definition}\n\n"
                "Observed failures:\n" + "\n".join(lines) + "\n\nRules:\n"
                f"- keep these public functions with their current signatures: {', '.join(self.public_names)}\n"
                "- use the Python standard library only\n"
                "- do not import modules; only `from __future__ import annotations` is allowed\n"
                "- other cases currently pass, so do not change their behaviour\n"
                "- reply with the complete corrected file in exactly one fenced code block")},
        ]

    def parse(self, text):
        blocks = re.findall(r"```[A-Za-z0-9_+.-]*\r?\n(.*?)```", text or "", re.DOTALL)
        if len(blocks) != 1:
            raise ModuleRepairError(f"expected exactly one fenced code block, found {len(blocks)}")
        source = blocks[0]
        if not source.strip():
            raise ModuleRepairError("the fenced code block is empty")
        size = len(source.encode("utf-8"))
        if size > self.max_bytes:
            raise ModuleRepairError(f"the fenced code block is {size} bytes, over the {self.max_bytes} byte cap")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ModuleRepairError(f"the fenced code block is not valid Python: {exc}") from exc
        defined = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        missing = sorted(name for name in self.public_names if name not in defined)
        if missing:
            raise ModuleRepairError(f"the module body must still define {missing}")
        return {self.path: source}

    @staticmethod
    def repair_prompt(error):
        return (
            f"That response could not be used: {error}. Answer again with the complete corrected "
            "module and nothing else: exactly one fenced code block, no prose before or after it.")
