"""Deterministic, explainable model routing with explicit fallback chains."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Mapping

from .providers import FallbackModelClient, ModelEvent
from .providers.base import generate_model


@dataclass(frozen=True)
class RouteRequest:
    task: str
    role: str = "default"
    category: str = "general"
    required_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutingProfile:
    profile_id: str
    providers: tuple[str, ...]
    priority: int = 0
    roles: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.profile_id or not self.providers:
            raise ValueError("routing profile id and providers must not be empty")
        if len(set(self.providers)) != len(self.providers):
            raise ValueError("routing fallback providers must be unique")


@dataclass(frozen=True)
class RoutingDecision:
    profile_id: str
    providers: tuple[str, ...]
    reasons: tuple[str, ...]
    candidate_scores: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "candidate_scores", MappingProxyType(dict(self.candidate_scores)))

    def to_dict(self):
        return {
            "profile_id": self.profile_id,
            "providers": list(self.providers),
            "selected_provider": self.providers[0],
            "fallback_providers": list(self.providers[1:]),
            "reasons": list(self.reasons),
            "candidate_scores": dict(self.candidate_scores),
        }


class DeterministicRouter:
    def __init__(self, profiles, *, default_profile):
        self.profiles = tuple(profiles)
        if not self.profiles or len({profile.profile_id for profile in self.profiles}) != len(self.profiles):
            raise ValueError("routing profiles must be non-empty with unique IDs")
        self.default_profile = str(default_profile)
        if self.default_profile not in {profile.profile_id for profile in self.profiles}:
            raise ValueError("default routing profile does not exist")

    def route(self, request: RouteRequest):
        task_tokens = set(request.task.lower().replace("_", " ").split())
        required = set(request.required_capabilities)
        scored = []
        reasons_by_id = {}
        for profile in self.profiles:
            reasons = []
            score = profile.priority
            selector_count = 0
            if request.role in profile.roles:
                score += 100
                reasons.append(f"role:{request.role}")
                selector_count += 1
            if request.category in profile.categories:
                score += 50
                reasons.append(f"category:{request.category}")
                selector_count += 1
            keyword_hits = sorted(task_tokens & {word.lower() for word in profile.keywords})
            score += 10 * len(keyword_hits)
            reasons.extend(f"keyword:{word}" for word in keyword_hits)
            selector_count += len(keyword_hits)
            if required:
                if not required.issubset(profile.capabilities):
                    score = -1
                    reasons.append("missing_required_capability")
                else:
                    score += 20 * len(required)
                    reasons.append("required_capabilities_satisfied")
                    selector_count += 1
            has_selectors = bool(profile.roles or profile.categories or profile.keywords)
            if has_selectors and selector_count == 0:
                score = -1
                reasons.append("no_selector_match")
            scored.append((score, profile.priority, profile.profile_id, profile))
            reasons_by_id[profile.profile_id] = reasons
        eligible = [item for item in scored if item[0] >= 0]
        if not eligible:
            selected = next(profile for profile in self.profiles if profile.profile_id == self.default_profile)
            reasons = ("default:no_eligible_profile",)
        else:
            selected = max(eligible, key=lambda item: (item[0], item[1], item[2]))[3]
            reasons = tuple(reasons_by_id[selected.profile_id]) or ("default:no_specific_signal",)
        return RoutingDecision(
            profile_id=selected.profile_id,
            providers=selected.providers,
            reasons=reasons,
            candidate_scores={profile.profile_id: score for score, _priority, _id, profile in scored},
        )


class RoutedModelClient:
    def __init__(self, providers, router, *, role="default", category="general"):
        self.providers = dict(providers)
        self.router = router
        self.role = role
        self.category = category
        self.last_routing_decision = None
        missing = {
            name for profile in router.profiles for name in profile.providers
        } - set(self.providers)
        if missing:
            raise ValueError(f"routing providers are unavailable: {', '.join(sorted(missing))}")

    def _select(self, request):
        decision = self.router.route(
            RouteRequest(request.prompt, role=self.role, category=self.category)
        )
        self.last_routing_decision = decision
        chain = tuple(self.providers[name] for name in decision.providers)
        return decision, chain[0] if len(chain) == 1 else FallbackModelClient(chain)

    @staticmethod
    def _annotate(result, decision):
        return replace(
            result,
            metadata={**dict(result.metadata), "routing_decision": decision.to_dict()},
        )

    def generate(self, request):
        decision, client = self._select(request)
        return self._annotate(generate_model(client, request), decision)

    def stream(self, request):
        decision, client = self._select(request)
        stream = getattr(client, "stream", None)
        if not callable(stream):
            result = self._annotate(generate_model(client, request), decision)
            if result.text:
                yield ModelEvent(kind="text_delta", text=result.text)
            yield ModelEvent(kind="completed", result=result)
            return
        for event in stream(request):
            if event.kind == "completed" and event.result is not None:
                yield replace(event, result=self._annotate(event.result, decision))
            else:
                yield event


__all__ = [
    "DeterministicRouter",
    "RouteRequest",
    "RoutedModelClient",
    "RoutingDecision",
    "RoutingProfile",
]
