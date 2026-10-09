"""Google Docs uses the existing Google/OpenBao protocol with an isolated mount."""

from dataclasses import dataclass
from typing import ClassVar

from signal_core.gsc_secrets import OpenBaoGscSecrets


@dataclass(frozen=True, repr=False)
class OpenBaoDocsSecrets(OpenBaoGscSecrets):
    provider: ClassVar[str] = "google-docs"
    mount: str = "signal-google-docs"
