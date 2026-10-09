"""A1 candidate-only adapter for the existing standing-authorized skill registry."""

from dataclasses import dataclass, replace
from types import SimpleNamespace
from uuid import UUID

from signal_core.github_read_binding import GitHubSharedEgressTransport
from signal_core.internal_link_service import seal_internal_link_revision
from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable
from signal_core.weekly_skill_ports import GuardedFetcher, WeeklySkillConnection
from signal_core.weekly_skills import SkillResult


class _Credential:
    def __init__(self, credential, guard):
        self.credential, self.guard = credential, guard

    async def credentials(self, **kwargs):
        self.guard()
        return await self.credential.credentials(**kwargs)


class _Runner:
    def __init__(self, runner, guard):
        self.runner, self.guard = runner, guard

    def run(self, plan):
        self.guard()
        result = self.runner.run(plan)
        self.guard()
        return result


def internal_link_skill_io(permit, guard, *, credential, provider, runner, openbao_transport=None):
    if (provider.run.tenant_id, provider.run.site_id) != (permit.tenant_id, permit.site_id):
        raise PermissionError("Internal-link egress scope mismatch.")

    class ReadTransport(GitHubSharedEgressTransport):
        async def handle_async_request(self, request):
            guard()
            return await super().handle_async_request(request)

    gateway = replace(provider, fetcher=GuardedFetcher(provider.fetcher, guard))
    return SimpleNamespace(
        credential=_Credential(credential, guard),
        github_read_transport=ReadTransport(gateway),
        runner=_Runner(runner, guard),
        openbao_transport=openbao_transport,
    )


@dataclass(frozen=True)
class InternalLinkSkillPort:
    connection_factory: object
    release_connection_factory: object
    io_factory: object
    configured: bool = True

    async def run(self, permit, guard):
        refs, unavailable = [], False
        for unit in permit.plan:
            guard()
            io = self.io_factory(permit, guard)
            try:
                with (
                    self.connection_factory() as raw,
                    self.release_connection_factory() as releases,
                ):
                    candidate = await seal_internal_link_revision(
                        WeeklySkillConnection(raw, permit),
                        releases,
                        session_token=permit.handle,
                        current_recovery_generation=permit.generation,
                        site_id=permit.site_id,
                        extension_id=UUID(unit["extension_id"]),
                        source_id=UUID(unit["source_id"]),
                        target_id=UUID(unit["target_id"]),
                        recipe_release_id=UUID(unit["release_id"]),
                        idempotency_key=UUID(unit["revision_key"]),
                        build_idempotency_key=UUID(unit["build_key"]),
                        baseline_idempotency_key=UUID(unit["baseline_key"]),
                        credential=io.credential,
                        github_transport=io.github_read_transport,
                        runner=io.runner,
                        openbao_transport=io.openbao_transport,
                    )
                refs.append(f"revision:{candidate.id}")
            except TechnicalRecipeUnavailable:
                unavailable = True
        return SkillResult(
            "completed" if refs else "unavailable",
            "INTERNAL_LINK_OWNER_REVIEW"
            if refs and not unavailable
            else "INTERNAL_LINK_PARTIAL_OWNER_REVIEW"
            if refs
            else "INTERNAL_LINK_CANDIDATE_UNAVAILABLE",
            tuple(refs),
        )
