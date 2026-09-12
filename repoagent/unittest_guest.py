"""Executed inside the selected sandbox, using its Python and unittest."""

import contextlib
import io
import json
import os
import sys
import unittest


def main():
    start, pattern = sys.argv[1:]

    class Result(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.ids = []

        def startTest(self, test):
            if len(self.ids) < 200:
                self.ids.append(str(test.id())[:240])
            super().startTest(test)

    # Test output is not the report protocol. Framework results supply counts.
    with (
        open(os.devnull, "w") as sink,
        contextlib.redirect_stdout(sink),
        contextlib.redirect_stderr(sink),
    ):
        suite = unittest.defaultTestLoader.discover(start, pattern=pattern)
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=Result).run(
            suite
        )
    report = {
        "schema": "repoagent.unittest/v1",
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "test_ids": result.ids,
        "ids_truncated": result.testsRun > len(result.ids),
        "diagnostics": [
            str(error)[-1000:] for _, error in (result.failures + result.errors)[:3]
        ],
    }
    print(json.dumps(report))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
