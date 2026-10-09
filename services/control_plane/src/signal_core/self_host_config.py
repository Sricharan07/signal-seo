"""Non-secret self-host installation contract; configuration grants no capability."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

CLIENT = "signal-dashboard"
PROVIDERS = {
    "openai": ("signal-model", "openai/default", ("api_key",)),
    "jev": ("signal-decision", "typesafe/default", ("api_key",)),
    "dataforseo": ("signal-research", "dataforseo/default", ("login", "password")),
    "google": ("signal-gsc", "oauth-client", ("client_id", "client_secret")),
    "github": ("signal-github", "github/app", ("app_id", "private_key_pem")),
    "slack": ("signal-slack", "client", ("client_id", "client_secret", "signing_secret")),
    "telegram": ("signal-telegram", "bot/default", ("bot_token", "webhook_secret")),
    "smtp": (
        "signal-email",
        "smtp/default",
        ("host", "port", "username", "password", "from_address"),
    ),
    "bing": ("signal-bing", "oauth-client", ("client_id", "client_secret")),
}
CALLBACKS = {
    "google": ("/auth/gsc/callback",),
    "slack": ("/auth/slack/callback",),
    "github": ("/webhooks/github",),
    "telegram": ("/webhooks/telegram",),
}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration field.")
        result[key] = value
    return result


@dataclass(frozen=True)
class SelfHostConfig:
    project: str
    origin: str
    owner_username: str
    workspace_name: str
    home_region: str
    images: dict[str, str]

    @property
    def issuer(self):
        return self.origin + "/identity/realms/signal"

    @classmethod
    def parse(cls, data):
        fields = {
            "schema_version",
            "project",
            "origin",
            "owner_username",
            "workspace_name",
            "home_region",
            "images",
        }
        if (
            not isinstance(data, dict)
            or set(data) != fields
            or type(data["schema_version"]) is not int
            or data["schema_version"] != 1
        ):
            raise ValueError("Self-host configuration schema rejected.")
        origin = data["origin"]
        if not isinstance(origin, str):
            raise ValueError("Exact HTTPS origin required.")
        url = urlsplit(origin)
        if (
            url.scheme != "https"
            or url.netloc != url.hostname
            or url.path
            or url.query
            or url.fragment
            or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)+", url.hostname or "")
            or re.fullmatch(r"[0-9.]+", url.hostname or "")
        ):
            raise ValueError("Exact DNS HTTPS origin required.")
        for field in ("project", "owner_username", "home_region"):
            if not isinstance(data[field], str) or not re.fullmatch(
                r"[a-z][a-z0-9_-]{0,62}", data[field]
            ):
                raise ValueError("Bounded installation identifiers required.")
        if data["project"] != "signal-self-host" and not data["project"].startswith(
            "signal-self-host-"
        ):
            raise ValueError("Use a dedicated self-host project namespace.")
        if (
            not isinstance(data["workspace_name"], str)
            or not 1 <= len(data["workspace_name"]) <= 120
            or any(ord(c) < 32 for c in data["workspace_name"])
        ):
            raise ValueError("Workspace name rejected.")
        images = data["images"]
        if (
            not isinstance(images, dict)
            or set(images) != {"api", "dashboard", "identity", "ingress", "workflow"}
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]{0,240}@sha256:[a-f0-9]{64}", value)
                for value in images.values()
            )
        ):
            raise ValueError("Every application image requires a registry SHA-256 digest.")
        return cls(**{key: value for key, value in data.items() if key != "schema_version"})


def provider_configuration(provider, data):
    if (
        provider not in PROVIDERS
        or not isinstance(data, dict)
        or set(data) != set(PROVIDERS[provider][2])
    ):
        raise ValueError("Exact provider fields required.")
    for name, value in data.items():
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 16384
            or "\x00" in value
            or (name != "private_key_pem" and any(ord(c) < 32 for c in value))
        ):
            raise ValueError("Provider value rejected.")
    if provider == "github":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

        key = serialization.load_pem_private_key(data["private_key_pem"].encode(), password=None)
        if (
            not isinstance(key, RSAPrivateKey)
            or key.key_size < 2048
            or not data["app_id"].isdigit()
        ):
            raise ValueError("GitHub App key rejected.")
    if provider == "smtp" and (not data["port"].isdigit() or not 1 <= int(data["port"]) <= 65535):
        raise ValueError("SMTP port rejected.")
    return dict(data)


def provider_projection(configured):
    if (
        not isinstance(configured, (list, tuple, set))
        or len(set(configured)) != len(configured)
        or any(value not in PROVIDERS for value in configured)
    ):
        raise ValueError("Provider projection rejected.")
    return [
        {
            "provider": provider,
            "configuration": "configured" if provider in configured else "not_configured",
            "availability": "disabled",
            "reason": "self_host_runtime_not_qualified"
            if provider in configured
            else "not_configured",
        }
        for provider in PROVIDERS
    ]
