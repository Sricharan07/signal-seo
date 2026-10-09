"""Owner health projection and a loopback-only operational route."""

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from signal_core.health_monitoring import CHECKS, REASONS, HealthStore

from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserRequestRejected, exact_cookie


@dataclass(frozen=True, repr=False)
class ComposedHealthGateway:
    store: HealthStore
    recovery_source: object

    async def read(self, token: str, site: UUID) -> dict:
        generation = (await self.recovery_source.current_generation()).value
        result = await asyncio.to_thread(self.store.read, token, site, generation)
        for item in result["checks"]:
            if item["check"] not in CHECKS or item["reason"] not in REASONS:
                raise ValueError("Health projection rejected.")
            item["remediation"] = CHECKS[item["check"]]
        return result


def mount_health_http(application: FastAPI, gateway=None, monitor=None) -> None:
    application.state.browser_health = gateway
    application.state.health_monitor = monitor

    @application.get("/v1/sites/{site_id}/health", tags=["health"])
    async def owner_health(request: Request, site_id: UUID):
        current = application.state.browser_health
        if current is None:
            return JSONResponse({"availability": "unavailable"}, status_code=503)
        try:
            result = await current.read(exact_cookie(request, SESSION_COOKIE_NAME), site_id)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (PermissionError, ValueError, BrowserRequestRejected):
            return JSONResponse({"code": "HEALTH_AUTHORITY_DENIED"}, status_code=403)
        except Exception:
            return JSONResponse({"availability": "unavailable"}, status_code=503)

    @application.get("/health", include_in_schema=False)
    async def private_health(request: Request):
        # Never trust forwarded headers. Public reverse proxies must additionally deny /health*.
        if (
            request.client is None
            or request.client.host not in {"127.0.0.1", "::1"}
            or "x-forwarded-for" in request.headers
            or "forwarded" in request.headers
        ):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        current = application.state.health_monitor
        if current is None or not current.latest:
            return JSONResponse({"state": "unknown"}, status_code=503)
        checks = [asdict(r) for r in current.latest]
        now = datetime.now(UTC)
        for item in checks:
            if now - datetime.fromisoformat(item["checked_at"]) > timedelta(minutes=5):
                item.update(state="unknown", reason="stale_evidence")
        status = (
            200
            if (
                getattr(current, "persisted", False)
                and len(checks) == len(CHECKS)
                and all(r["state"] == "ok" for r in checks)
            )
            else 503
        )
        return JSONResponse(
            {"checks": checks}, status_code=status, headers={"Cache-Control": "no-store"}
        )
