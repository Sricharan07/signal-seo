# Slice 0163: Ask Signal (dashboard)

This is a product slice. It adds the owner side of Ask Signal on top of the 0162 assistant API, following the
Direction C prototype drawer. The API contract is shared with 0162: conversations, messages and memories are
kept by the API per site and per person, so they survive reloads, devices and sign-ins.

## Implemented

- **Route:** `/actions/assistant` relays four owner commands (start a conversation, send a question, remember,
  forget) and three reads (overview, one conversation, memories). Every command needs the browser mutation proof
  and the tenant CSRF token, and has exact fields. Questions are capped at 2,000 characters and memories at 500.
  Responses are capped at 256 KiB and validated field by field. Answer links must be same-origin dashboard paths.
- **Replay safety:** each question carries a request id. A failed send keeps its ids, so sending the same text
  again replays it and is never answered or charged twice.
- **Drawer:** the top-bar command "Ask Signal about your site…" (and any `#ask` link, such as the Home header
  button) opens a native modal dialog. On open it resumes the latest conversation. It shows citations as links
  to the records an answer came from, and action cards that link into the existing owner flows. Nothing is
  executed from a chat. It also shows "Remembered:" notes, suggestions from the API and the typing state.
- **Page:** `/chat` lists past conversations, shows the open one and manages what Signal remembers: add a
  preference or context, and forget with a confirm step. The local walkthrough stays below it for local pilots.
- **Honest states:** the composer is closed, with the reason, when no model is set up, the budget is used up or
  the API cannot be reached. A reply that is not an answer says why.

## Verification

- **End to end on the local pilot:** `scripts/ask_signal_pilot_check.py` runs against a running
  `npm run pilot`. It signs in through Keycloak with the pilot's local-only account, selects
  the organization and a site, and then drives every Ask Signal route through the dashboard BFF
  to the API and PostgreSQL:
  - overview;
  - idempotent start;
  - a question and its exact replay;
  - stored history;
  - adding a memory, and refusing a business statement as memory;
  - listing and forgetting a memory;
  - the conversation list, the Chat page and the top-bar command;
  - rejecting a cross-site command.

  It passed 17/17 on a fresh pilot with no model key, so replies took the honest "model is not
  configured" path. The answering path with a model is covered by the 0162 database lab, which
  uses a synthetic gateway.
- **Refused memories:** the API keeps only recognisable personal preferences and plans. The
  memory form guides that input, and a refused entry explains that business facts belong in
  Business facts.

- `tests/ask-signal.test.tsx` covers the same-origin link check, exact commands, CSRF relay, contract validation,
  rejected cross-site and malformed requests, and the page and unavailable renderings.
- Root `npm test` passes.
