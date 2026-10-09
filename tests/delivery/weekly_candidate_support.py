"""Legacy qualification ports, not composed product capabilities."""

from signal_core.autonomy_gate import AutonomyCandidate
from signal_core.weekly_loop import WeeklyStage


class UnavailableCandidatePort:
    """Explicit empty candidate port for record-only qualification scenarios."""

    async def prepare(
        self, stage: WeeklyStage, due_revisions: tuple[str, ...]
    ) -> tuple[AutonomyCandidate, ...]:
        return ()
