"""Connector lifecycle mechanics; provider adapters and SQL remain the authority."""

import base64
import hashlib
import inspect
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from hmac import compare_digest
from uuid import uuid4

from signal_core.database import _clean_transaction


def connector_row(connection, function, *args, scalar=False, transaction=_clean_transaction):
    with transaction(connection):
        select = "" if scalar else "* FROM "
        row = connection.execute(
            f"SELECT {select}control.{function}({','.join(['%s'] * len(args))})", args
        ).fetchone()
    return row[0] if scalar else row


def state_digest(state, error):
    if not isinstance(state, str) or len(state) != 43 or not state.isascii():
        raise error
    return hashlib.sha256(state.encode()).digest()


def pkce_challenge(verifier):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


async def consume_pkce(store, attempt, challenge, error, **options):
    verifier = await store.consume_verifier(attempt, **options)
    if not compare_digest(pkce_challenge(verifier), challenge):
        raise error
    return verifier


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


@asynccontextmanager
async def retained_secret(store, remove, *args, cleanup_errors=(), **options):
    """Remove newly stored material if its authority record cannot be committed."""
    reference = await store(*args, **options)
    try:
        yield reference
    except Exception:
        try:
            await remove(reference, **options)
        except cleanup_errors:
            pass
        raise


async def begin_pkce(store, attempt, authorization, persist, error, **options):
    await store.store_verifier(attempt, authorization.verifier, **options)
    try:
        if await _resolve(persist()) != "created":
            raise error
    except Exception:
        await store.destroy_verifier(attempt, **options)
        raise


@contextmanager
def refresh_lock(connection, identifier, error):
    key = int.from_bytes(hashlib.sha256(identifier.bytes).digest()[:8], "big", signed=True)
    if not connection.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]:
        raise error
    try:
        yield
    finally:
        connection.execute("SELECT pg_advisory_unlock(%s)", (key,))


def require_restriction(row, accepted, error):
    # Pending means locally denied, not a durable acknowledgement. Never normalize it.
    if row is None or row[0] not in accepted:
        raise error
    return row


def require_outcome(outcome, success, errors, unavailable):
    if outcome != success:
        raise errors.get(outcome, unavailable)


def public_projection(packet, hidden):
    for key in hidden:
        packet.pop(key, None)
    return packet


async def external_revocation(invoke, remove, *, caught=(), failure=False):
    """Run upstream revocation only after the adapter recorded local denial."""
    try:
        return await _resolve(invoke())
    except caught:
        return failure
    finally:
        await remove()


async def refresh_oauth(
    store,
    reference,
    invoke,
    restrict,
    *,
    protocol_error,
    reauth_codes,
    secret_error,
    rotation_error,
    options,
):
    refresh = await store.refresh_token(reference, **options)
    credentials = await store.client_credentials(**options)
    try:
        tokens = await _resolve(invoke(credentials, refresh))
    except protocol_error as error:
        if error.code in reauth_codes:
            restrict(reauth_codes[error.code])
        raise
    if tokens.refresh_token is not None and tokens.refresh_token != refresh:
        try:
            await store.replace_refresh_token(reference, refresh, tokens.refresh_token, **options)
        except secret_error:
            restrict("provider_rotated")
            raise rotation_error from None
    return tokens


@dataclass(frozen=True, repr=False)
class OAuthBinding:
    connection: object
    secrets: object
    provider: str
    error: type
    transaction: object = _clean_transaction
    pkce: bool = False

    def failure(self, suffix):
        return self.error(f"{self.provider.upper()}_{suffix}")

    def row(self, function, *args, scalar=False):
        return connector_row(
            self.connection, function, *args, scalar=scalar, transaction=self.transaction
        )

    async def begin(self, args, authorization, redirect, attempt, **options):
        values = (*args, attempt, authorization.state_sha256, redirect)
        if self.pkce:
            values += (authorization.code_challenge,)

        def persist():
            return self.row(f"begin_{self.provider}_oauth_attempt", *values, scalar=True)

        if self.pkce:
            await begin_pkce(
                self.secrets,
                attempt,
                authorization,
                persist,
                self.failure("OWNER_OR_ORIGIN_DENIED"),
                **options,
            )
        elif persist() != "created":
            raise self.failure("OWNER_OR_ORIGIN_DENIED")

    async def complete(
        self,
        args,
        attempt,
        state,
        redirect,
        exchange,
        discover,
        candidates,
        validate_selection,
        **options,
    ):
        # SQL consumption always precedes secret access and provider I/O.
        digest = state_digest(state, self.failure("STATE_REJECTED"))
        with self.transaction(self.connection):
            columns = "outcome, origin, code_challenge" if self.pkce else "outcome, origin"
            row = self.connection.execute(
                f"SELECT {columns} FROM control.consume_{self.provider}_oauth_attempt("
                "%s, %s, %s, %s, %s, %s)",
                (*args, attempt, digest, redirect),
            ).fetchone()
        if row is None or row[0] != "consumed":
            raise self.failure("ATTEMPT_UNAVAILABLE")
        verifier = None
        if self.pkce:
            verifier = await consume_pkce(
                self.secrets, attempt, row[2], self.failure("VERIFIER_REJECTED"), **options
            )
        credentials = await self.secrets.client_credentials(**options)
        tokens = await _resolve(exchange(credentials, verifier))
        selection = await _resolve(discover(tokens, row[1]))
        selection = validate_selection(tokens, selection)
        async with retained_secret(
            self.secrets.store_refresh_token,
            self.secrets.destroy_refresh_token,
            attempt,
            tokens.refresh_token,
            **options,
        ) as reference:
            outcome = self.row(
                f"stage_{self.provider}_oauth_attempt",
                *args,
                attempt,
                reference,
                candidates(selection),
                scalar=True,
            )
            if outcome != "staged":
                raise self.failure("ATTEMPT_UNAVAILABLE")
        return selection

    def confirm(self, args, attempt, resource):
        binding = uuid4()
        outcome = self.row(
            f"confirm_{self.provider}_binding",
            *args,
            attempt,
            binding,
            uuid4(),
            resource,
            scalar=True,
        )
        if outcome != "bound":
            raise self.failure(outcome.upper())
        return binding
