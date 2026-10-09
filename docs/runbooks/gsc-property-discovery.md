# GSC Property Discovery Runbook

This runbook covers the internal read-only Search Console property-discovery
boundary from Slice 0050. It is not an owner OAuth or production connector guide.

## Qualification

Run the deterministic protocol suite:

```sh
.venv/bin/python -m pytest tests/connectors/test_gsc_properties.py -q
```

Run the real-provider negative boundary check:

```sh
.venv/bin/python scripts/check-gsc-provider-boundary.py
```

The second command intentionally uses a synthetic credential against Google's
fixed `sites.list` endpoint. Success means Google rejected that credential as an
authorization failure. It does not prove an authorized account or property.

## Result Interpretation

| Code | Meaning | Operator action |
| --- | --- | --- |
| `GSC_ACCESS_TOKEN_REJECTED` | Local bearer shape is invalid | Stop before provider I/O; inspect credential acquisition without logging the bearer |
| `GSC_SITE_ORIGIN_REJECTED` | The selected site is not one canonical public HTTPS origin | Correct the server-owned site configuration and reverify ownership |
| `GSC_TLS_CONFIGURATION_REJECTED` | TLS verification was disabled or replaced by an invalid value | Restore the reviewed trust configuration; do not bypass TLS |
| `GSC_AUTHORIZATION_REJECTED` | Google returned 401 or 403 | Reauthorize with the exact read-only scope or inspect account/property access |
| `GSC_PROVIDER_UNAVAILABLE` | Transport, quota, or provider availability failed | Retry under a bounded later connector policy; do not convert this to empty data |
| `GSC_PROVIDER_RESPONSE_REJECTED` | Status, media type, size, or schema was outside the contract | Preserve the failure, inspect sanitized diagnostics, and update only through review |

An empty tuple is a valid successful provider response with no visible properties.
It must not be replaced with sample data. A returned property is eligible only when
its current permission is readable and its exact resource identity matches the
verified origin.

## Security Rules

- Never pass a caller-controlled URL to the provider client.
- Never disable TLS verification or enable redirects/environment proxies.
- Never print the bearer, request headers, provider body, or customer property list
  in ordinary logs or support bundles.
- Never persist a reusable token in Signal business tables.
- Never treat discovery as durable binding or analytics authority.

## Production No-Go Boundary

Do not expose this module to owners until reviewed OAuth attempt state, OpenBao
secret storage, callback validation, exact tenant/site authority, durable binding,
revoke/reconnect behavior, health projection, and one successful dedicated real-
account qualification exist.
