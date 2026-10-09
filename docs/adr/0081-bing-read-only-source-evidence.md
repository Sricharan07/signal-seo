# ADR-0081: Read-Only Bing Binding with Separate Source Evidence

Status: accepted for the local R1 connector boundary, 2026-09-29.

## Context

Bing Webmaster Tools provides performance and inbound-link data, but its numbers
are not interchangeable with Search Console. OAuth read access and JSON/HTTP
methods are documented by Microsoft. SOAP and POX are retired; a connector must
not fall back to them or place an API key in a durable egress URL.

## Decision

Use only the fixed `webmaster.read` OAuth scope and a one-use, hash-stored state.
The owner must have a current session and exact verified origin before the attempt
starts, and must confirm an eligible verified Bing site returned by `GetUserSites`.
The secret and refresh token live in separate OpenBao KV-v2 paths; PostgreSQL
stores only a reference. The refresh-token path uses CAS rotation and permanent
deletion on disconnect. Bing's documented OAuth flow does not specify PKCE, so
the code is exchanged once after the state has been durably consumed; no
unsupported PKCE parameter is sent.

All token and JSON/HTTP calls use the shared egress gateway. Performance,
inbound-link counts, and bounded first-page link details are separate immutable
`bing_webmaster` generations with response digests and egress receipts. Each
generation says `complete=false` and `missing_data=unknown`; it never supplies
zeroes for absent rows. GSC evidence retains its own source and is never silently
merged with Bing evidence (EC-143). Owner revocation and provider reauthorization
requirements append restrictions before the next import can proceed.

## Alternatives

- An account API key in the documented query parameter would enter the durable
  egress URL, so OAuth Bearer authorization is used instead.
- SOAP/POX are retired, so only JSON/HTTP is used.
- Automatically accepting the first verified Bing site would bypass owner
  confirmation, so discovery and binding are separate operations.
- Treating missing days or unreturned link pages as zero would violate INV-018.

## Consequences

The boundary needs an exact owner callback, a prepared shared-egress authority,
and current robots evidence for `www.bing.com`. This slice does not turn on the
dashboard connector or a recurring import schedule. There are no real Bing
credentials in the repository, and live provider success is not yet qualified.

## Verification

The commands and evidence are in [slice 0071](../implementation/0071-bing-binding.md).
The external protocol contracts are [Microsoft's OAuth guide](https://learn.microsoft.com/en-us/bingwebmaster/oauth2),
[JSON/HTTP protocol page](https://learn.microsoft.com/en-us/bingwebmaster/api-protocols),
and the method references for [site discovery](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getusersites?view=bing-webmaster-dotnet),
[performance](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getrankandtrafficstats?view=bing-webmaster-dotnet),
[link counts](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getlinkcounts?view=bing-webmaster-dotnet),
and [link details](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.geturllinks?view=bing-webmaster-dotnet).
