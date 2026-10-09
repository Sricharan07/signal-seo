"""GA4's isolated OpenBao namespace uses the existing Google secret lifecycle."""

from dataclasses import dataclass
from typing import ClassVar

from signal_core.gsc_secrets import OpenBaoGscSecrets


@dataclass(frozen=True, repr=False)
class OpenBaoGa4Secrets(OpenBaoGscSecrets):
    mount: str = "signal-ga4"
    provider: ClassVar[str] = "ga4"
