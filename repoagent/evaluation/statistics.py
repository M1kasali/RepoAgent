"""Small dependency-free statistics for evaluation reports."""

from __future__ import annotations

import math
import random
import statistics


def wilson_interval(successes: int, total: int, *, z=1.959963984540054):
    if isinstance(successes, bool) or isinstance(total, bool):
        raise TypeError("Wilson counts must be integers")
    if total < 1 or successes < 0 or successes > total:
        raise ValueError("Wilson counts require 0 <= successes <= total and total > 0")
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    return {"estimate": proportion, "low": max(0.0, center - margin), "high": min(1.0, center + margin)}


def paired_win_tie_loss(control, treatment):
    control = tuple(float(value) for value in control)
    treatment = tuple(float(value) for value in treatment)
    if not control or len(control) != len(treatment):
        raise ValueError("paired samples must be non-empty and equal length")
    deltas = [right - left for left, right in zip(control, treatment, strict=True)]
    return {
        "pairs": len(deltas),
        "wins": sum(delta > 0 for delta in deltas),
        "ties": sum(delta == 0 for delta in deltas),
        "losses": sum(delta < 0 for delta in deltas),
        "mean_delta": statistics.fmean(deltas),
        "median_delta": statistics.median(deltas),
    }


def paired_bootstrap_interval(control, treatment, *, samples=2000, seed=0, confidence=0.95):
    control = tuple(float(value) for value in control)
    treatment = tuple(float(value) for value in treatment)
    if not control or len(control) != len(treatment):
        raise ValueError("paired samples must be non-empty and equal length")
    if samples < 100:
        raise ValueError("bootstrap requires at least 100 samples")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")
    deltas = [right - left for left, right in zip(control, treatment, strict=True)]
    randomizer = random.Random(seed)
    means = sorted(
        statistics.fmean(randomizer.choice(deltas) for _ in deltas)
        for _ in range(samples)
    )
    tail = (1 - confidence) / 2
    low_index = max(0, int(math.floor(tail * (samples - 1))))
    high_index = min(samples - 1, int(math.ceil((1 - tail) * (samples - 1))))
    return {
        "estimate": statistics.fmean(deltas),
        "low": means[low_index],
        "high": means[high_index],
        "samples": samples,
        "seed": seed,
        "confidence": confidence,
    }


def exact_mcnemar(control_passed, treatment_passed):
    control = tuple(bool(value) for value in control_passed)
    treatment = tuple(bool(value) for value in treatment_passed)
    if not control or len(control) != len(treatment):
        raise ValueError("McNemar samples must be non-empty and equal length")
    control_only = sum(left and not right for left, right in zip(control, treatment, strict=True))
    treatment_only = sum(right and not left for left, right in zip(control, treatment, strict=True))
    discordant = control_only + treatment_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, value)
            for value in range(0, min(control_only, treatment_only) + 1)
        ) / (2**discordant)
        p_value = min(1.0, 2 * tail)
    return {
        "control_only": control_only,
        "treatment_only": treatment_only,
        "discordant": discordant,
        "p_value_two_sided": p_value,
    }


__all__ = [
    "exact_mcnemar",
    "paired_bootstrap_interval",
    "paired_win_tie_loss",
    "wilson_interval",
]
