# Polyglot Input Contract Audit

Date: 2026-09-10. Status: **24-task audit complete; input-policy correction not implemented**.

## Scope and Method

This audit covers the exact ordered 24-task canary used by the historical
`c42e2bb` live run, not a newly selected favorable subset. Dataset commit is
`7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f`. The current adapter/workspace code is
from `2f310fa`; no production Python code changed during this audit.

The offline inventory uses `PolyglotAdapter.load(..., limit=24)` and the real
`prepare_polyglot_runner_workspace()` for every task. It checks dataset identity,
task order, all task/grader pairing identities, copied-file hashes and separation
from declared tests/examples. It also verifies all 144 files in the historical
24 Agent evidence bundles, plus their manifests and grade hashes. It neither
executes generated code nor calls Docker or a model.

Manual review compares the assembled instructions, actual visible source files,
metadata, withheld public repository tests and recorded grading failures. Seeing
tests as a reviewer is distinct from exposing them to the Agent. All reviewer-only
artifacts remain local/ignored and are not connected to the runtime prompt path.

This is an input-boundary audit, not proof that every instruction survived later
context admission or that a different input would have produced a passing answer.
No old result, denominator, grader, completion rule or price record was changed.

## Findings

- **20/24 tasks omit source build metadata:** all four tasks in each of C++, Go,
  Java, JavaScript and Rust. The explicit inventory includes CMake, Go modules,
  Gradle/wrappers, package/Babel configuration and Cargo manifests. No matching
  build files were found for the four Python tasks. This is an observed omission,
  not proof that every omitted file is necessary or that standalone compilation
  is impossible.
- **Two Java tasks omit public support candidates:** the skeletons reference
  `UnsolvablePuzzleException` and `BankAccountActionInvalidException`, but their
  metadata-declared `files.editor` sources are not copied.
- **Do not copy all `files.editor`:** Go alphametics and book-store also declare
  `cases_test.go` under that role. Those files contain test inputs and expected
  answers, so withholding them is correct under the existing hidden-test policy.
  There are four tasks with omitted editor files, but only two are identified
  support-source gaps; the other two are intentionally hidden test data.
- **Four C++ tasks have empty or almost empty public declarations.** Function,
  class, accessor and iterator contracts must be guessed from domain prose while
  the grader expects a particular C++ interface.
- **Language-specific append instructions are already included.** Go book-store
  explicitly states cents; Python affine-cipher explicitly states its exception
  type/message. Their visibility is a useful counterexample to the hypothesis
  that the adapter universally loses append documents.

The manual contract classification is 17 `gap`, four `clarify`, and three
`no_specific_gap`. These labels are review judgments, not a measured Agent failure
rate or a semantic completeness theorem. Build/support flags are independent and
overlap these categories. A task that passed can still have an ambiguous contract.

## Per-Task Checklist

All 24 rows have been reviewed. `Gap` means an identifiable public interface,
representation, support-source or error-contract requirement is missing.
`Clarify` means a concrete convention needs specification review. `No gap found`
is narrowly scoped to the reviewed interface/behavior, not build readiness.
Historical observations below refer to `c42e2bb` unless explicitly marked later.

| Task | Contract review | Build metadata omitted | Finding and historical interpretation |
| --- | --- | --- | --- |
| `cpp/all-your-base` | Gap | CMake | Empty declarations omit conversion API. Tests also impose zero/empty normalization and exception policy; historical code compiled but failed these edge cases. |
| `go/alphametics` | No gap found | go.mod | Appendix provides signature, `==` grammar, mapping and no-solution error. Code passed; turn did not converge. Editor test cases must remain hidden. |
| `java/affine-cipher` | Gap | Gradle/wrapper | Public methods exist; exact exception message is absent. Fourteen tests passed, two rejected different error wording. |
| `javascript/affine-cipher` | Gap | package/Babel | Untyped `key` lacks an explicit object-shape/error contract. Historical decryption also had a separate modulo implementation error. |
| `python/affine-cipher` | No gap found | None found | Appendix explicitly specifies ValueError and message; historical task passed. |
| `rust/accumulate` | Gap | Cargo | Instructions require consulting withheld tests for the signature; skeleton fixes integer vectors and an unspecified closure type, tests require generics/mutable closures. Historical code passed without convergence. |
| `cpp/allergies` | Gap | CMake | Empty API; appendix mentions a set but not string-based arguments. Historical enum-versus-string mismatch prevented compilation. |
| `go/beer-song` | Clarify | go.mod | Typed string APIs exist; exact trailing-newline framing and invalid ranges need a public convention. Historical multi-verse output lacked a trailing blank line. |
| `java/all-your-base` | Gap | Gradle/wrapper | Typed API exists; empty-input representation and exact validation messages are absent. Historical failures concern empty digits and an error message. |
| `javascript/alphametics` | Gap | package/Babel | Parameterless skeleton does not declare string grammar, object result or null-on-failure convention. Solvable inputs also failed; no sole-cause attribution. |
| `python/beer-song` | Gap | None found | Return shape is unspecified. Historical lists contained whole verses rather than individual lines and separator entries. |
| `rust/acronym` | Clarify | Cargo | Typed API/punctuation rules exist; internal-capital behavior is not explicit. Historical code passed; later paired code failed camel-case handling. |
| `cpp/bank-account` | Gap | CMake | Empty class omits methods/types/exceptions, although concurrency is explicit. Historical runtime/thread abort is not automatically explained by this gap. |
| `go/book-store` | No gap found | go.mod | Appendix explicitly supplies signature and integer cents. Historical task passed; editor test cases must remain hidden. |
| `java/alphametics` | Gap | Gradle/wrapper | Referenced exception source is omitted; accepted equality syntax needs clarification. Historical solvable cases failed in parsing. |
| `javascript/beer-song` | Gap | package/Babel | Array-versus-string and per-line framing are unstated. Historical task passed; later paired treatment failed on return shape. |
| `python/book-store` | Gap | None found | Dollar prose does not state cents as the API unit. Historical outputs were in dollars while grading expected cents. |
| `rust/alphametics` | Clarify | Cargo | Typed Option/mapping API exists; `=` versus `==` serialization needs clarification. Historical code independently failed Rust type/ownership checks. |
| `cpp/binary-search-tree` | Gap | CMake | Empty header omits template/class/accessor/iterator API. Historical tests could not compile the expected tree class. |
| `go/bottle-song` | Clarify | go.mod | Return type is a string slice; line granularity and separator entries need clarification. Historical target remained an unimplemented panic. |
| `java/bank-account` | Gap | Gradle/wrapper | Referenced exception source is omitted; exact messages/state edges are unspecified. Historical methods remained unimplemented. |
| `javascript/binary` | Gap | package/Babel | Invalid-input handling is requested but the null return convention is absent. Historical code passed nine tests and returned zero on the invalid case. |
| `python/bottle-song` | Gap | None found | Untyped recite API lacks list-of-lines/blank-separator contract. Historical task passed by choosing the expected representation. |
| `rust/book-store` | Gap | Cargo | Integer return type alone does not specify cents or numeric title encoding. Historical code passed without convergence; later scratch-only work is still unfinished implementation. |

The exact source paths/hashes and detailed interpretation for each row are in
the local `report.json` and `reviews.json`. For a task `language/exercise`, source
paths resolve under `../polyglot-benchmark/language/exercises/practice/exercise/`.

## Interpretation

The earlier low scores combine implementation errors, budget/convergence failures,
environment mistakes and incomplete interface specifications. They cannot all be
treated as evidence of weak coding ability, nor can every failure be excused as
an evaluation defect. In particular:

- Java affine-cipher's message mismatch, Python book-store's monetary unit, and
  JavaScript binary's invalid-result convention directly match absent contracts.
- Rust type/ownership errors and untouched Go/Java stubs remain actual unfinished
  or invalid implementations. No improved counterfactual score is calculated.
- The prior eight-pair result stays 2/8 versus 2/8 end-to-end and 1W/6T/1L.
  Identical inputs support comparison within that protocol, not equivalence to
  an official Aider benchmark result or broad coding competence.

## Next Implementation

Keep the current v1 results immutable. The next slice should correct input policy
as a separately versioned experiment before more paid tuning:

1. Define explicit per-file roles for editable solutions, reviewed public support,
   build context and grader-only assets. Do not infer trust from `files.editor`,
   file suffixes, or a blanket repository copy. Reject path escapes, symlinks and
   role conflicts; preserve tests/examples/answer tables on the grader side.
2. Specify the public interface contracts consistently across the whole canary:
   names/types, serialization, units, invalid-input behavior and error conventions.
   Do not generate task solutions or paste hidden test inputs/expected outputs into
   prompts. Any contract clarification derived during review must be declared as
   new task input, not a performance optimization under the unchanged old protocol.
3. Review build files before exposure. CMake here creates test targets and executes
   them on builds; package files include grading scripts. Extract or supply the
   required public toolchain/dependency context deliberately. Copying all original
   build files neither creates a standalone hidden-test-free workspace nor
   preserves the existing no-grader-command input boundary automatically.
4. Bind visible file contents, roles and instructions to a new runner contract and
   input digest. Reject v1/v2 pairing. Keep scoring, model settings and budget fixed
   in the new experiment, with clean source provenance and a new spending gate.
5. Verify role isolation, source-byte preservation, support availability and
   language-specific build smoke fixtures without a model. Only then freeze fresh
   paired trials. A same-canary rerun is development confirmation, not an untouched
   holdout. Do not silently recalculate old scores or launch 225 tasks.

Go snapshot/startup/command timing remains a separate model-free experiment.
It should not be mixed into the input-policy treatment or inferred from metadata
omission counts. No runtime, timeout, sandbox, grader or prompt changes were made
by this audit.

## Evidence and Checks

Local root: `artifacts/audits/polyglot-input-contract-20260910/`.

- `audit.py`: materializes real runner inputs, binds historical identities and
  emits the inventory/report/checksum manifest; no live-agent factory is used.
- `inventory.json`: visible inputs plus separately labeled reviewer-only evidence.
- `reviews.json`: manual assessment for all 24 tasks, including editor-file roles.
- `report.json`: aggregate labels and per-row source hashes. Audit completion and
  readiness for further paid tuning are distinct fields; readiness remains false.
- `test_audit.py`: local tests for complete/unique review coverage, explicit editor
  roles, safe evidence paths, real workspace exclusions/omissions and append
  visibility. Their passing status verifies the audit, not a production fix.
- `manifest.json`: hashes local scripts, inventory, review and report artifacts.

```bash
.venv/bin/python artifacts/audits/polyglot-input-contract-20260910/audit.py inventory
.venv/bin/python artifacts/audits/polyglot-input-contract-20260910/audit.py report
.venv/bin/python -m unittest discover -s artifacts/audits/polyglot-input-contract-20260910 -p test_audit.py -v
.venv/bin/python artifacts/audits/polyglot-input-contract-20260910/audit.py check-build-inputs
```

The eight local tests pass. `check-build-inputs` deliberately returns **exit 1**
on the current observed 20 build-metadata omissions and two public-support
candidates. The check is red-capable: it checks observed inventory fields rather
than declaring the known protocol ready because the audit command completed.
It does not establish semantic completeness even if the structural check later
passes. No model requests or generated-code executions occurred in this audit.

Separate repository regression verification passed: **637 tests**, six existing
`datetime.utcnow()` deprecation warnings, 126.85 seconds. Ruff, diff whitespace,
evaluation CLI help and the 24-task Polyglot plan also passed. The first restricted
run was interrupted after it remained incomplete; the completed run used the
approved unrestricted environment. This is local regression evidence, not a new
live benchmark result.

Verification bundles are retained at
`artifacts/verifications/input-contract-audit-local-20260910/` (eight audit tests
and the expected failing structural check) and
`artifacts/verifications/input-contract-audit-20260910/` (repository checks).
All 25 listed files across the audit and two verification manifests were
hash-verified. These artifacts remain local; no commit or push was performed.
