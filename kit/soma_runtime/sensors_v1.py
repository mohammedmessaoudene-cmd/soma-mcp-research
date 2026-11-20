"""Deterministic measurements, with explicit domains and no universal detector claim.

Python/SQLite research prototype; constant-time claims use a bounded word-RAM
model and fixed configurations. They are not hard real-time guarantees.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Mapping, Sequence


@dataclass(frozen=True)
class InvariantReading:
    conservation_residual: int
    minimum_candidate_balance: int
    destination_label_margin: int


def invariant_reading(before: Sequence[int], delta: Sequence[int],
                      source_label: int, destination_label: int) -> InvariantReading:
    """O(k) time and O(1) auxiliary space for k touched balances."""
    if not before or len(before) != len(delta):
        raise ValueError("A nonempty complete touched-set is required")
    return InvariantReading(
        abs(sum(delta)),
        min(b + d for b, d in zip(before, delta)),
        destination_label - source_label,
    )


def budget_margin(limit: int, spent: int, held: int, candidate_ceiling: int) -> int:
    """O(1) bounded-word arithmetic; UNKNOWN exposures remain in held."""
    return limit - spent - held - candidate_ceiling


class ProgressSensor:
    """Measures a supplied ranking; does not establish that it is valid.

    O(1) expected work for a supplied fixed-size digest; O(w) memory. Computing
    a digest of n bytes upstream costs O(n), and is not included here.
    """
    def __init__(self, epsilon: float = 0.0, patience: int = 3, window: int = 16):
        if epsilon < 0 or not math.isfinite(epsilon) or patience < 1 or window < 1:
            raise ValueError("Invalid progress configuration")
        self.epsilon, self.patience, self.window = epsilon, patience, window
        self.previous = None
        self.stagnant_steps = 0
        self.history = deque()
        self.counts = {}

    def update(self, value: float, now_ns: int, digest: str) -> dict:
        if not math.isfinite(value) or value < 0:
            raise ValueError("Ranking value must be finite and nonnegative")
        if self.previous is not None and now_ns <= self.previous[1]:
            raise ValueError("Monotonic time must increase")
        decrease = None if self.previous is None else self.previous[0] - value
        velocity = None if self.previous is None else decrease / ((now_ns - self.previous[1]) / 1e9)
        if decrease is not None:
            self.stagnant_steps = self.stagnant_steps + 1 if decrease <= self.epsilon else 0
        repeated = self.counts.get(digest, 0) > 0
        self.history.append(digest)
        self.counts[digest] = self.counts.get(digest, 0) + 1
        if len(self.history) > self.window:
            removed = self.history.popleft()
            self.counts[removed] -= 1
            if not self.counts[removed]:
                del self.counts[removed]
        self.previous = (value, now_ns)
        return {"ranking": value, "decrease": decrease, "progress_per_second": velocity,
                "stagnation_alert": self.stagnant_steps >= self.patience,
                "repeated_digest": repeated,
                "interpretation": "diagnostic_only_not_a_termination_proof"}


class HistogramSensor:
    """Fixed alphabet, fixed positive reference q, additive smoothing alpha.

    c_i = count_i + alpha; N = sum c_i; S = sum c_i log(c_i);
    T = sum c_i log(q_i). H = log(N)-S/N; KL = S/N-log(N)-T/N.
    Initialization O(K), each single-bin update/reading O(1), storage O(K).
    Units are nats. Floating-point numerical error is not a security guarantee.
    """
    def __init__(self, reference: Mapping[str, float], alpha: float = 0.5):
        if not reference or alpha <= 0 or not math.isfinite(alpha):
            raise ValueError("Positive smoothing and finite fixed alphabet required")
        if any(not math.isfinite(p) or p <= 0 for p in reference.values()):
            raise ValueError("Reference probabilities must be strictly positive")
        if not math.isclose(sum(reference.values()), 1.0, abs_tol=1e-12):
            raise ValueError("Reference probabilities must sum to one")
        self.alpha = alpha
        self.counts = {key: 0 for key in reference}
        self.logq = {key: math.log(value) for key, value in reference.items()}
        self.n = len(reference) * alpha
        self.s = len(reference) * alpha * math.log(alpha)
        self.t = alpha * sum(self.logq.values())

    def update(self, category: str, change: int = 1) -> dict:
        if type(change) is not int or category not in self.counts:
            raise ValueError("Known category and integer count delta required")
        old = self.counts[category] + self.alpha
        if self.counts[category] + change < 0:
            raise ValueError("Cannot remove absent observations")
        new = old + change
        self.s += new * math.log(new) - old * math.log(old)
        self.t += change * self.logq[category]
        self.n += change
        self.counts[category] += change
        return self.read()

    def read(self) -> dict:
        entropy = math.log(self.n) - self.s / self.n
        divergence = self.s / self.n - math.log(self.n) - self.t / self.n
        return {"entropy_nats": max(0.0, entropy), "kl_nats": max(0.0, divergence),
                "effective_count": self.n, "interpretation": "distribution_only_not_integrity"}


def branching_pressure(frontier: int, generated: int, remaining_budget: int,
                       horizon: int = 4, smoothing: float = 1.0) -> dict:
    """Fixed horizon extrapolation O(1); no asymptotic-complexity classification."""
    if min(frontier, generated, remaining_budget) < 0 or not 0 <= horizon <= 32:
        raise ValueError("Nonnegative counts and bounded horizon required")
    if not math.isfinite(smoothing) or smoothing <= 0:
        raise ValueError("Positive finite smoothing required")
    ratio = (generated + smoothing) / (frontier + smoothing)
    factor = horizon + 1 if abs(ratio - 1) < 1e-12 else (ratio ** (horizon + 1) - 1) / (ratio - 1)
    forecast = frontier * factor
    return {"observed_branch_ratio": ratio, "forecast_work": forecast,
            "budget_pressure": forecast / max(1, remaining_budget),
            "horizon": horizon, "interpretation": "finite_prefix_heuristic"}


@dataclass(frozen=True)
class Observation:
    sequence: int
    state_version: int
    observed_ns: int
    initial_radius: float = 0.0


def freshness_reading(observation: Observation, expected_sequence: int,
                      expected_version: int, now_ns: int, maximum_age_ns: int,
                      drift_per_second: float = 0.0) -> dict:
    """O(1); radius bound is conditional on a valid supplied drift bound."""
    if not math.isfinite(drift_per_second) or drift_per_second < 0:
        raise ValueError("Finite nonnegative drift bound required")
    if not math.isfinite(observation.initial_radius) or observation.initial_radius < 0:
        raise ValueError("Finite nonnegative initial uncertainty required")
    age = now_ns - observation.observed_ns
    sequence_ok = observation.sequence == expected_sequence
    version_ok = observation.state_version == expected_version
    age_ok = 0 <= age <= maximum_age_ns
    radius = observation.initial_radius + drift_per_second * max(0, age) / 1e9
    return {"fresh": sequence_ok and version_ok and age_ok,
            "sequence_ok": sequence_ok, "version_ok": version_ok,
            "age_ns": age, "uncertainty_radius": radius}
