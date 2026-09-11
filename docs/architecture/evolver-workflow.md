# Controlled Evolution Workflow

## Scope

The opt-in isolated task Runtime now supports this complete path:

1. Generate bounded candidates from training failures.
2. Check pinned commits, compare both arms and stop bounded search.
3. Select one finalist using training results only.
4. Execute the fixed finalist on separate sealed tasks.
5. Return an explicit human approval request.
6. Run new tasks using the approved commit; rollback restores the previous route.

This does not replace the running Python modules of the normal CLI/TUI. Tasks
execute in isolated scratch directories; user checkout files are not overwritten.
Task answers, source identity and grading evidence are retained in deployment
receipts. This mode uses registered file-based task contracts, not an unrestricted
interactive workspace or automatic application of generated patches to user files.

## Components

- `ModelCandidateProposer`: the default model-backed proposal callback. Supply a
  fresh `BudgetedEvaluationClient`, explicit source paths and training evidence.
  One gateway budget covers generation across all rounds. Its journal directory
  is exclusive and must be outside the source checkout.
- `HostedAgentSnapshotEvaluator`: supply host-mode `AgentSnapshotTask` objects,
  a fresh-gateway factory, frozen gateway descriptor and private call-journal root.
  Task call/output limits must match the gateway. No credentials enter the guest.
- `SnapshotSealedBackend`: wraps a separate hosted evaluator whose registered
  tasks are the sealed set. Use its `grader_digest` when constructing the vault.
- `SnapshotDeployment`: resolves the approved source before each isolated task.
  It supports a single complete snapshot route, not composition of independent
  candidate commits. Skill routes require `enable_skills=True`.

## Invocation

The controller exposes one preparation call. Its arguments are explicit host
objects rather than dynamically imported plugins or untrusted configuration code:

```python
prepared = evolver.prepare_evolution(
    repo_root,
    search_options=search_options,
    select_finalist=lambda state: state["qualified_candidates"][0],
    vault=sealed_vault,
    sealed_backend=sealed_backend,
    sealed_cost_limit_usd=sealed_budget,
)
```

`search_options` contains `run_id`, `base_commit`, `propose`, deterministic checks
and evaluator, paired checks and evaluator, `gate`, `run_budget`,
`max_trial_cost_usd`, and `SearchLimits`. Set `propose` to ModelCandidateProposer.
The first-qualified selector above is deterministic, not a claim to select the
best candidate; applications may supply a training-only ranking policy.

Possible outcomes are `no_qualified_candidate`, `sealed_rejected`, or
`awaiting_human_approval`. Exceptions preserve state/evidence and halt progression.
The pending result contains an approval token; do not put it into public logs.
After a human has reviewed the candidate and evidence, explicitly invoke:

```python
deployment.approve(
    prepared["approval_token"],
    candidate_id=prepared["sealed"]["receipt"]["candidate_id"],
    label="prompt",
    actor="human:reviewer",
)
receipt = deployment.run_task("task-id", cost_limit_usd=task_budget)
deployment.rollback("prompt", actor="human:reviewer")
```

Before approval, and after rollback of the first deployment, tasks use the
configured baseline. With multiple sequential deployments, rollback restores the
previous candidate. An in-flight task retains its already captured commit.
If confirmation was durably recorded but activation was interrupted, explicitly
use `activate_confirmed(candidate_id=..., label=..., actor=...)`. This checks the
existing human confirmation and cannot reuse it after activation/rollback.

## Costs and Recovery

Generation, paired search, sealed validation and deployed tasks have separate
explicit budgets. Supply an accurate full-request token counter and leaf Provider
without hidden retries. Fixture usage/prices are not production accounting data.

`settle_paired_costs(candidate_id)` verifies the full receipt matrix and any host
model journals before releasing unused paired reservations. Preparation settles
qualified candidates; other fully measured candidates can be settled explicitly.
Trial count quotas are not refunded. Unknown/invalid results retain reservations.

`reconcile_trial_cost(candidate_id, index, journal_path=..., actor=...)` is an
explicit accounting-only action. It verifies the frozen host root and journal
attribution, and records observable known usage without inventing a quality score,
releasing uncertain reservations or sending another model request. Ambiguous
attribution and genuinely missing Provider usage are rejected.

`search(..., resume=True)` replays finished state or continues at an anchored,
completed-round boundary with the same plan. An uncertain generation/check/trial
is never silently rerun. Model proposer journal identity is part of the plan;
reconstructing a fresh gateway after a crash is not a way to reset its budget.
Sealed validation is one-shot per search, even if interrupted. Recover existing
approval state with `request_finalist_approval`, not by repeating sealed evaluation.

## Verification

`tests/test_evolver_lifecycle.py` runs the model proposer, search, concrete sealed
backend, settlement, human confirmation, deployed task and rollback in real Docker
with scripted host Providers. It checks actual source markers and changed task
outcomes before/after deployment, plus loading skills from pinned source. This is
functional integration evidence, not model-quality or billable-cost acceptance.

Run only these targeted integration cases with an existing local Docker image:

```bash
REPOAGENT_TEST_DOCKER=/usr/bin/docker .venv/bin/python -m pytest -q tests/test_evolver_lifecycle.py
```
