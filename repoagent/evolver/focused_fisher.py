"""Focused wide-pass screening and per-task navigator confirmation.

Promotion is deliberately distinct from statistical credit. This policy owns
probe/confirm ordering; scorer execution and infrastructure reruns are external.
"""

from dataclasses import asdict, dataclass
import math
from statistics import mean, stdev


@dataclass(frozen=True)
class TaskTrialSummary:
    task_id: str
    passes: int
    attempts: int
    infra_attempts: int = 0
    failure: str | None = None

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError("trial summary requires a task id")
        if any(type(n) is not int or n < 0 for n in (self.passes, self.attempts, self.infra_attempts)):
            raise ValueError("trial counts must be nonnegative integers")
        if max(self.passes, self.infra_attempts) > self.attempts:
            raise ValueError("trial counts exceed attempts")
        if self.failure not in {None, "provider", "infrastructure", "inconclusive"}:
            raise ValueError("unknown measurement failure")

    @property
    def pass_rate(self):
        return self.passes / self.attempts if self.attempts else 0.0


def _validity(evals, ids, k):
    result = {name: [] for name in ("missing", "provider_failures", "infrastructure_failures", "inconclusive")}
    for tid in ids:
        row = evals.get(tid)
        if row is None:
            result["missing"].append(tid)
            continue
        if not isinstance(row, TaskTrialSummary) or row.task_id != tid:
            raise ValueError("measurement identity mismatch")
        if row.failure == "provider":
            result["provider_failures"].append(tid)
        if row.failure == "infrastructure" or row.infra_attempts:
            result["infrastructure_failures"].append(tid)
        if row.failure == "inconclusive" or row.attempts < k:
            result["inconclusive"].append(tid)
    result["status"] = (
        "failed" if result["provider_failures"] or result["infrastructure_failures"] else
        "inconclusive" if result["missing"] or result["inconclusive"] else "measured")
    return result


def _valid_pair(candidate, control, ids, k):
    stats = {"candidate_validity": _validity(candidate, ids, k),
             "control_validity": _validity(control, ids, k)}
    statuses = {row["status"] for row in stats.values()}
    return ("failed" if "failed" in statuses else "inconclusive" if "inconclusive" in statuses else None), stats


def fisher_one_sided(cp, cn, vp, vn):
    if any(type(n) is not int or n < 0 for n in (cp, cn, vp, vn)):
        raise ValueError("Fisher counts must be nonnegative integers")
    row1, row2 = cp + cn, vp + vn
    col1, total = cp + vp, cp + cn + vp + vn
    if not row1 or not row2 or col1 in {0, total}:
        return 1.0

    def probability(a):
        b, c, d = row1 - a, col1 - a, total - col1 - (row1 - a)
        if min(b, c, d) < 0:
            return 0.0
        return math.exp(
            math.lgamma(row1 + 1) + math.lgamma(row2 + 1)
            + math.lgamma(col1 + 1) + math.lgamma(total - col1 + 1)
            - math.lgamma(total + 1)
            - sum(math.lgamma(x + 1) for x in (a, b, c, d)))

    return min(1.0, sum(probability(a) for a in range(cp, min(row1, col1) + 1)))


def _mean(rows, ids):
    return sum(rows[tid].pass_rate if tid in rows else 0.0 for tid in ids) / len(ids) if ids else 0.0


def _counts(rows, ids):
    return (sum(rows[t].passes for t in ids if t in rows),
            sum(rows[t].attempts - rows[t].passes for t in ids if t in rows))


@dataclass(frozen=True)
class FocusedFisherGate:
    k: int = 3
    alpha: float = 0.05
    min_confirm_lift: float = 0.0
    z_threshold: float = 2.0

    def __post_init__(self):
        if type(self.k) is not int or self.k < 1:
            raise ValueError("Fisher repetitions must be positive")
        if any(type(x) not in (int, float) or not math.isfinite(x)
               for x in (self.alpha, self.min_confirm_lift, self.z_threshold)):
            raise ValueError("gate thresholds must be finite")
        if not 0 < self.alpha < 1 or self.z_threshold < 0:
            raise ValueError("invalid Fisher thresholds")

    def descriptor(self):
        return {"kind": "focused-fisher/v1", **asdict(self)}

    def decide(self, *, baseline, train_ids, focused_ids, sentinel_ids, evaluate, fired_tasks=None):
        train_ids, focused_ids, sentinel_ids = map(tuple, (train_ids, focused_ids, sentinel_ids))
        if not train_ids or any(len(ids) != len(set(ids)) for ids in (train_ids, focused_ids, sentinel_ids)):
            raise ValueError("gate requires unique training task ids")
        if not set(focused_ids) | set(sentinel_ids) <= set(train_ids):
            raise ValueError("probe tasks must belong to training")
        if fired_tasks is not None and not set(fired_tasks) <= set(train_ids):
            raise ValueError("fired tasks must belong to training")
        baseline = dict(baseline)
        probe_ids = tuple(dict.fromkeys(focused_ids + sentinel_ids))
        probe = evaluate(probe_ids, self.k, "focused") if probe_ids else {}
        invalid, stats = _valid_pair(probe, baseline, probe_ids, self.k)
        if invalid:
            stats["verdict"] = invalid
            return self._result(invalid, "screen", stats)
        stats = {}
        if focused_ids:
            cp, cn = _counts(probe, focused_ids)
            vp, vn = _counts(baseline, focused_ids)
            stats.update(fisher_p=fisher_one_sided(cp, cn, vp, vn),
                         fisher_p_worse=fisher_one_sided(vp, vn, cp, cn),
                         foc_c=cp / (cp + cn) if cp + cn else 0.0,
                         foc_v=vp / (vp + vn) if vp + vn else 0.0)
        if sentinel_ids:
            stable = [tid for tid in sentinel_ids if baseline[tid].pass_rate == 1.0]
            fragile = [tid for tid in sentinel_ids if tid not in stable and baseline[tid].pass_rate]
            stats.update(sent_c=_mean(probe, sentinel_ids), sent_v=_mean(baseline, sentinel_ids))
            if stable:
                guard = 1.5 / (len(stable) * self.k)
                stats["sentinel_guard"] = guard
                if _mean(probe, stable) < _mean(baseline, stable) - guard:
                    stats["sentinel_regression"] = True
                    return self._result("rejected", "screen", stats)
            if fragile:
                cp, cn = _counts(probe, fragile)
                vp, vn = _counts(baseline, fragile)
                worse = fisher_one_sided(vp, vn, cp, cn)
                stats["sent_fragile_p_worse"] = worse
                if cp / (cp + cn) < vp / (vp + vn) and worse < self.alpha:
                    stats["sentinel_regression"] = True
                    return self._result("rejected", "screen", stats)
        if (focused_ids and stats["foc_c"] < stats["foc_v"]
                and stats["fisher_p_worse"] < self.alpha):
            stats["pruned_significantly_worse"] = True
            return self._result("rejected", "screen", stats)

        confirmed = evaluate(train_ids, self.k, "confirm")
        invalid, validity = _valid_pair(confirmed, baseline, train_ids, self.k)
        lift = _mean(confirmed, train_ids) - _mean(baseline, train_ids)
        stats.update(full_lift=lift, **validity)
        if invalid:
            stats["verdict"] = invalid
            return self._result(invalid, "confirm", stats)
        eligible = [tid for tid in train_ids if fired_tasks is None or tid in fired_tasks]
        if not eligible:
            stats["verdict"] = "rejected"
            return self._result("rejected", "confirm", stats)
        diffs = [confirmed[t].pass_rate - baseline[t].pass_rate for t in eligible]
        delta = mean(diffs)
        se = stdev(diffs) / math.sqrt(len(diffs)) if len(diffs) > 1 else 0.0
        z = delta / se if se else (0.0 if not delta else math.copysign(math.inf, delta))
        navigator = mean(confirmed[t].pass_rate for t in eligible) > mean(baseline[t].pass_rate for t in eligible)
        verdict = "accepted" if navigator and lift >= self.min_confirm_lift else "rejected"
        stats["verdict"] = "accepted" if navigator else "rejected"
        paired = {"n_tasks": len(eligible), "candidate_mean": mean(confirmed[t].pass_rate for t in eligible),
                  "control_mean": mean(baseline[t].pass_rate for t in eligible),
                  "mean_lift": delta, "se": se, "z": z if math.isfinite(z) else str(z),
                  "z_threshold": self.z_threshold, "promoted": navigator,
                  "credited_2sigma": navigator and z >= self.z_threshold,
                  "eligible_tasks": eligible}
        return {**self._result(verdict, "confirm", stats), "paired": paired,
                "score": _mean(confirmed, train_ids)}

    @staticmethod
    def _result(verdict, phase, stats):
        return {"verdict": verdict, "phase": phase, "promoted": verdict == "accepted",
                "stats": stats}


class FocusedBenchmarkEvaluator:
    """Opt-in check evaluator; native receipts own replay and interruption fences.

    backend.score(repo_root, identity, task_ids, k, phase) must return immutable
    TaskTrialSummary values. The backend owns sandboxing, per-trial receipts,
    cost admission and bounded infrastructure salvage; candidate code is untrusted.
    """

    def __init__(self, *, target, backend, train_ids, focused_ids, sentinel_ids, gate=None):
        from .contracts import BenchmarkTarget
        if not isinstance(target, BenchmarkTarget):
            raise TypeError("focused evaluation requires a benchmark target")
        self.target, self.backend = target, backend
        self.train_ids, self.focused_ids, self.sentinel_ids = map(tuple, (train_ids, focused_ids, sentinel_ids))
        self.gate = gate or FocusedFisherGate()
        if not isinstance(self.gate, FocusedFisherGate):
            raise TypeError("focused evaluation requires a typed gate")
        if (not self.train_ids or any(len(ids) != len(set(ids)) for ids in
                (self.train_ids, self.focused_ids, self.sentinel_ids))
                or not (set(self.focused_ids) | set(self.sentinel_ids)) <= set(self.train_ids)):
            raise ValueError("focused evaluation requires unique training-only task sets")

    def descriptor(self, repo_root):
        return {"kind": "focused-benchmark-evaluator/v1", "target": self.target.to_dict(),
                "train_ids": list(self.train_ids), "focused_ids": list(self.focused_ids),
                "sentinel_ids": list(self.sentinel_ids), "gate": self.gate.descriptor(),
                "backend": self.backend.descriptor(repo_root)}

    def evaluate(self, repo_root, identity, checks, descriptor):
        from .workspace import _git, _git_bytes, verify_benchmark_target
        checks = tuple(checks)
        if len(checks) != 1 or checks[0].check_id != "focused_fisher":
            raise ValueError("focused evaluator requires the focused_fisher check")
        if identity["base_commit"] != self.target.base_commit or descriptor != self.descriptor(repo_root):
            raise ValueError("focused evaluation configuration changed")
        verify_benchmark_target(repo_root, self.target)
        base = self.target.base_commit
        if _git(repo_root, "show", "-s", "--format=%P", identity["commit_sha"]) != base:
            raise ValueError("focused candidate parent differs from target")
        changed = set(_git_bytes(repo_root, "diff", "--name-only", "-z", base, identity["commit_sha"]).decode().split("\0")) - {""}
        if not changed <= set(self.target.mutable_paths):
            raise ValueError("focused candidate changed protected paths")
        records = []

        def score(arm_identity, ids, k, phase):
            if descriptor != self.descriptor(repo_root):
                raise ValueError("focused evaluator descriptor drift")
            rows = self.backend.score(repo_root, dict(arm_identity), tuple(ids), k, phase)
            if set(rows) - set(ids):
                raise ValueError("scorer returned undeclared tasks")
            records.append({"phase": phase, "identity": dict(arm_identity), "task_ids": list(ids),
                            "k": k, "results": {tid: asdict(row) for tid, row in rows.items()}})
            return rows

        baseline = score({"commit_sha": base}, self.train_ids, self.gate.k, "baseline")
        result = self.gate.decide(
            baseline=baseline, train_ids=self.train_ids, focused_ids=self.focused_ids,
            sentinel_ids=self.sentinel_ids,
            evaluate=lambda ids, k, phase: score(identity, ids, k, phase))
        if descriptor != self.descriptor(repo_root):
            raise ValueError("focused evaluation configuration changed during scoring")
        status = "pass" if result["promoted"] else "error" if result["verdict"] in {"failed", "inconclusive"} else "fail"
        return [{"check_id": "focused_fisher", "status": status,
                 "result": result, "measurements": records}]
