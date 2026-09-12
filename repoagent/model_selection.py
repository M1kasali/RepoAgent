"""Connection-local model selection with atomic context budget updates."""

from .config import provider_env
from .compaction import DeterministicHistoryCompactor
from .context_window import ContextWindowBudget
from .providers.profiles import ModelProfile
from .tokenization import resolve_token_counter


class ModelSelection:
    def __init__(self, profiles, client_factory, *, settings=None):
        self.profiles = dict(profiles)
        if not all(
            isinstance(profile, ModelProfile) and name == profile.name
            for name, profile in self.profiles.items()
        ):
            raise ValueError("model selection requires named ModelProfiles")
        self.client_factory = client_factory
        self.selected = None
        self.selected_model = None
        self.settings = settings
        self.known_secrets = set()

    def options(self, agent):
        current = getattr(agent.model_client, "profile", None)
        configured = (
            self.settings.read()["providers"] if self.settings is not None else {}
        )
        for entry in configured.values():
            if entry.get("api_key"):
                self.known_secrets.add(entry["api_key"])
        for value in self.known_secrets:
            agent.register_secret(value)
        return {
            "profile": current.name if current is not None else "",
            "model": current.model
            if current is not None
            else getattr(agent.model_client, "model", ""),
            "provider": current.provider if current is not None else "",
            "profiles": [
                {
                    "name": name,
                    "model": profile.model,
                    "provider": profile.provider,
                    "credential_present": any(
                        bool(provider_env(key)) for key in profile.credential_envs
                    )
                    or bool(configured.get(profile.provider, {}).get("api_key")),
                    "environment_credential_present": any(
                        bool(provider_env(key)) for key in profile.credential_envs
                    ),
                    "saved_credential_present": bool(
                        configured.get(profile.provider, {}).get("api_key")
                    ),
                    "api_base": configured.get(profile.provider, {}).get(
                        "api_base", profile.base_url
                    ),
                    "models": list(
                        dict.fromkeys(
                            [
                                profile.model,
                                *configured.get(profile.provider, {}).get("models", []),
                            ]
                        )
                    ),
                    "requires_credentials": bool(profile.credential_envs),
                    "remote_verified": False,
                }
                for name, profile in self.profiles.items()
            ],
        }

    def select(self, agent, name, model=None):
        if name not in self.profiles:
            raise ValueError("unknown model profile")
        profile = self.profiles[name]
        if self.settings is not None:
            entry = self.settings.entry(profile.provider)
            if model is not None and model not in [
                profile.model,
                *entry.get("models", []),
            ]:
                raise ValueError("model is not in the configured list")
            profile = profile.with_overrides(
                model=model or profile.model,
                base_url=entry.get("api_base", profile.base_url),
            )
        elif model is not None and model != profile.model:
            raise ValueError("model is not in the configured list")
        if (
            getattr(agent.model_client, "profile", None) == profile
            and self.settings is None
        ):
            self.selected = name
            self.selected_model = profile.model
            return {"changed": False, **self.options(agent)}
        # Finish all validation/construction before changing live runtime fields.
        budget = ContextWindowBudget(
            context_window_tokens=profile.context_window_tokens,
            configured_input_tokens=agent.context_window_budget.configured_input_tokens,
            reserved_output_tokens=profile.max_output_tokens,
            window_source=profile.context_window_source,
        )
        client = self.client_factory(profile)
        if getattr(client, "profile", None) != profile:
            raise ValueError("model factory returned a different profile")
        counter = agent.context_manager.explicit_token_counter or resolve_token_counter(
            client
        )
        compactor = DeterministicHistoryCompactor(counter)
        result = {
            **self.options(agent),
            "changed": True,
            "profile": name,
            "model": profile.model,
            "provider": profile.provider,
        }
        agent.model_client = client
        agent.register_secret(getattr(client, "api_key", ""))
        agent.max_new_tokens = profile.max_output_tokens
        agent.context_window_budget = budget
        agent.context_manager.total_token_budget = budget.effective_input_tokens
        agent.context_manager.token_counter = counter
        agent.context_manager.history_compactor = compactor
        self.selected = name
        self.selected_model = profile.model
        return result

    def restore(self, agent):
        if self.selected is not None:
            self.select(agent, self.selected, self.selected_model)

    def configure(self, agent, operation, params):
        if self.settings is None:
            raise ValueError("provider configuration is unavailable")
        name = params.get("slug")
        if name not in self.profiles:
            raise ValueError("unknown provider")
        current = getattr(agent.model_client, "profile", None)
        if (
            operation == "remove_model"
            and current is not None
            and current.provider == self.profiles[name].provider
            and current.model == params.get("model")
            and current.model != self.profiles[name].model
        ):
            raise ValueError("select another model before removing the active model")
        key = params.get("api_key")
        if isinstance(key, str) and key:
            self.known_secrets.add(key)
            agent.register_secret(key)
        self.settings.update(
            self.profiles[name].provider,
            operation,
            api_key=key,
            api_base=params.get("api_base"),
            model=params.get("model"),
        )
        return {
            "saved": True,
            "disconnected": operation == "disconnect",
            "options": self.options(agent),
            "active_client_unchanged": True,
        }
