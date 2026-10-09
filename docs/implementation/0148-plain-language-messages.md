# Slice 0148: Plain-Language Sign-In and Status Messages

This is a product slice and presentation only. Sign-in, organization, site and connection notices were written in
system terms such as "tenant authority", "pre-tenant identity", "active scope", "browser proof" and "control
plane". It changes no loader, form, route or authority. Based on main `a4062ce`.

## Implemented

- **Plain words, same meaning:** every notice now says what happened in plain words.
  - A failure still states that nothing was unlocked or changed. Examples: "Nothing was unlocked. Try signing in
    again." and "Signal could not confirm the sign-out, so you are still signed in here."
  - Success notices say what is now true. Examples: "Your access was rechecked. Choose a site to work on." and
    "The new site is selected. Verify you own it before Signal does any work."
- **No duplicate failure card:** an expired, invalid or unavailable organization directory no longer gets a
  second card. The welcome panel's sign-in notice already explains it.
- **Smaller copy and style fixes:**
  - The no-site state reads "You don't have access to any sites here yet."
  - The workspace fallback reads "Workspace …" instead of "Tenant …".
  - The last three page kickers became pills with the same text, and the unused `.section-kicker` style was
    removed.

## Verification

- Dashboard: 271 tests passed. Two assertions now pin the new wording with the same intent: the API-unreachable
  notice, and a failed logout never reported as success.
- Repository: 46 checks passed.
- Typecheck and build pass in `npm test`.
- Error, no-site and sign-in renders are checked.
