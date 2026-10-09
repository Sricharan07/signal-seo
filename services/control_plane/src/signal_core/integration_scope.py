"""Protected operator configuration for one dedicated integration environment."""

import json
import os
import re
import stat
from dataclasses import dataclass, fields
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from signal_core.json_objects import unique_object


class IntegrationScopeUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Dedicated environment configuration is unavailable.")


@dataclass(frozen=True, repr=False)
class IntegrationScope:
    origin: str
    owner_email: str
    owner_subject: str
    site_id: str
    slack_workspace_id: str
    slack_channel_id: str
    slack_client_id: str
    google_project_id: str
    google_login_client_id: str
    google_gsc_client_id: str
    github_app_id: int
    github_installation_id: int
    github_owner: str
    github_repository: str
    github_base_branch: str
    github_content_path: str
    public_ipv4: str
    public_ipv6: str
    ssh_source_ipv6: str
    ssh_key_path: str
    ssh_known_hosts_path: str
    realm_id: str
    incomplete_user_id: str
    incomplete_created_timestamp: int
    tenant_name: str
    home_region: str

    def __post_init__(self):
        try:
            integers = {"github_app_id", "github_installation_id", "incomplete_created_timestamp"}
            for field in fields(self):
                value = getattr(self, field.name)
                if field.name in integers:
                    if type(value) is not int or not 1 <= value < 2**63:
                        raise ValueError
                elif (
                    not isinstance(value, str)
                    or not 1 <= len(value) <= 2048
                    or any(ord(character) < 32 or ord(character) > 126 for character in value)
                ):
                    raise ValueError
            parsed = urlsplit(self.origin)
            if (
                parsed.scheme != "https"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or not parsed.hostname
                or parsed.netloc != parsed.hostname
                or re.fullmatch(r"[0-9.]+", parsed.hostname) is not None
                or re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)+", parsed.hostname) is None
            ):
                raise ValueError
            for value in (self.site_id, self.realm_id, self.incomplete_user_id):
                identifier = UUID(value)
                if identifier.version != 4 or str(identifier) != value:
                    raise ValueError
            if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.owner_email) is None:
                raise ValueError
            patterns = {
                "slack_workspace_id": r"T[A-Z0-9]{7,63}",
                "slack_channel_id": r"[CG][A-Z0-9]{7,63}",
                "slack_client_id": r"[0-9]{5,32}\.[0-9]{5,32}",
                "google_project_id": r"[a-z][a-z0-9-]{4,62}",
                "google_login_client_id": r"[0-9]+-[a-z0-9-]+\.apps\.googleusercontent\.com",
                "google_gsc_client_id": r"[0-9]+-[a-z0-9-]+\.apps\.googleusercontent\.com",
                "github_owner": r"[A-Za-z0-9][A-Za-z0-9-]{0,38}",
                "github_repository": r"[A-Za-z0-9_.-]{1,100}",
                "github_base_branch": r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}",
                "github_content_path": r"[A-Za-z0-9][A-Za-z0-9_./-]{0,200}",
                "home_region": r"[a-z]{2}-[a-z]+-[0-9]+",
            }
            if any(
                re.fullmatch(pattern, getattr(self, name)) is None
                for name, pattern in patterns.items()
            ):
                raise ValueError
            if ".." in self.github_content_path.split("/"):
                raise ValueError
            if self.google_login_client_id == self.google_gsc_client_id:
                raise ValueError
            if str(IPv4Address(self.public_ipv4)) != self.public_ipv4:
                raise ValueError
            for value in (self.public_ipv6, self.ssh_source_ipv6):
                if str(IPv6Address(value)) != value:
                    raise ValueError
            for value in (self.ssh_key_path, self.ssh_known_hosts_path):
                if not Path(value).is_absolute() or any(part == ".." for part in Path(value).parts):
                    raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise IntegrationScopeUnavailable() from None

    @property
    def host(self):
        return urlsplit(self.origin).hostname

    @property
    def issuer(self):
        return self.origin + "/identity/realms/signal"

    @property
    def site(self):
        return UUID(self.site_id)

    @property
    def gsc_property(self):
        return self.origin + "/"


def load_integration_scope(path=None):
    """No defaults for identities or resources, no import-time I/O or diagnostics values."""
    target = Path(
        path
        or os.environ.get(
            "SIGNAL_INTEGRATION_CONFIG_FILE", "/run/signal-application/environment.json"
        )
    )
    descriptor = None
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or not 1 <= info.st_size <= 16384
        ):
            raise ValueError

        data = json.loads(os.read(descriptor, 16385), object_pairs_hook=unique_object)
        if not isinstance(data, dict) or set(data) != {
            field.name for field in fields(IntegrationScope)
        }:
            raise ValueError
        return IntegrationScope(**data)
    except (OSError, ValueError, TypeError):
        raise IntegrationScopeUnavailable() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
