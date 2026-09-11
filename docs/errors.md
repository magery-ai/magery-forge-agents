# Errors

This page is organized by what your agent should **do**, not by what each
code technically means. Getting this wrong is the most likely way an
unattended agent misbehaves against this API — most of these are not bugs to
work around, they're normal states with a specific correct response.

## Two response shapes

An error caused by the request itself — a bad domain id, an audit that
doesn't belong to you, an already-running audit — comes back as:

```json
{"detail": {"errorCode": "SOME_CODE", "message": "human-readable text"}}
```

A `422 Unprocessable Entity` from malformed input (an out-of-range `limit`, a
badly formatted `from`/`to` date, a missing required field) uses the web
framework's own validation shape instead, and has **no `errorCode`**:

```json
{"detail": [{"loc": ["query", "limit"], "msg": "...", "type": "..."}]}
```

Check for `detail.errorCode` first; if it isn't there, you're looking at a
validation error and the fix is in your request's shape, not in retry logic.

## Over MCP: a third shape, and no status code

The MCP server at `https://forge.magery.ai/mcp` runs the same six operations
against the same code, so every error code on this page reaches you there
too — but a tool result is not an HTTP response, and it does not look like
one.

A failed tool call comes back as a normal result with `isError` set to true
and a single text block. Its text is the SDK's own prefix followed by the
same JSON object the HTTP API puts under `detail`:

```
Error executing tool get_audit: {"errorCode": "AUDIT_NOT_FOUND", "message": "audit not found"}
```

**Parse the JSON out of that string; don't match on the whole text.** The
prefix names the tool and comes from the MCP framework, not from Forge, so
treat everything from the first `{` as the payload and `errorCode` inside it
as the thing to branch on — exactly as you would branch on
`detail.errorCode` over HTTP. Some errors carry extra keys alongside
`errorCode` and `message` (`INVALID_FILTER` includes `field` and `allowed`),
the same ones the HTTP API sends.

**There is no status code.** Every "Status:" line on this page describes the
HTTP API. Over MCP the `errorCode` is the whole answer, which also means the
`DOMAIN_NOT_FOUND` 400-vs-404 split below simply does not arise: you get the
same code from `start_audit` and from `get_domain`, and the advice for both
is the one this page already gives — check the id against `list_domains`.

Two failures do **not** carry an `errorCode`, and neither is retryable as-is:

- **An argument your call got wrong** is rejected by the tool's published
  input schema before the tool runs, and the text is the schema validation
  message rather than JSON — e.g. `Error executing tool list_audits: 1
  validation error for list_auditsArguments / limit / Input should be less
  than or equal to 100`. This is the MCP equivalent of the `422` below. Read
  the tool's input schema (`tools/list`) and fix the argument.
- **An unexpected server-side failure** returns the prefix and nothing else
  — `Error executing tool get_audit` — with no JSON and no detail, on
  purpose. Treat it the way you would a `500`: it is not something your
  arguments can fix, and a tight retry loop will not help.

Authentication over MCP fails exactly as it does over HTTP, with
`AGENT_KEY_INVALID` and the same "your agent cannot diagnose this" rule —
see that section below, and [authentication.md](authentication.md).

## `auditStatus` values — when to stop polling

`auditStatus` (returned by `POST /audits`, `GET /audits`,
`GET /audits/{audit_id}` and `GET /audits/{audit_id}/status`) is one of
four values: `new`, `in_progress`, `done`, `error`. `done` and `error` are
terminal — stop polling once you see either. `new` and `in_progress` both
mean the audit is still running; the distinction between them isn't
significant to an agent, only that neither is terminal yet. This is also
why `AUDIT_ALREADY_RUNNING` below only fires while a domain's audit is
`new` or `in_progress` — once it reaches `done` or `error`, `POST /audits`
simply starts a new one.

## `resultIndicator` values

`resultIndicator` (returned by `GET /audits`, `GET /audits/{audit_id}` and
`GET /audits/{audit_id}/status`, and by `POST /audits`) is one of three
values: `green`, `yellow`, `red`. It is `null` while `auditStatus` is `new`
or `in_progress` — nothing has been judged yet — and may also be `null` for
an audit that finished `error`, if nothing was judged before it errored.

## `AUDIT_ALREADY_RUNNING` — normal, not a failure

- **Where:** `POST /audits`
- **Status:** `409 Conflict`

Forge allows one in-flight audit per domain at a time. This code means your
`POST` was well formed and the domain is fine — there is simply already an
audit running for it. **Do not treat this as an error and do not retry the
`POST` in a loop.** Instead, find the running audit with `GET /audits`
(filter by `domain`) and poll its status.

**`domain` is a substring filter, and the list is newest-first — do not just
take the first result.** `GET /audits?domain=example.com` also matches
`staging.example.com`, and if that other domain's audit is newer, it sorts
first even though it isn't the one that just returned `AUDIT_ALREADY_RUNNING`
— and it may already be finished, which would make polling it return
immediately with a stale-looking success. From the returned list, pick the
first entry whose `domainName` equals the domain you requested **exactly**
and whose `auditStatus` is not terminal (`done` or `error`). If nothing in
the page matches, that is a real, reportable problem — don't guess by
falling back to the first row anyway.

## `VERIFICATION_REQUIRED` vs. `NO_CHECKS_AVAILABLE` — different problems, different fixes

Both are returned from `POST /audits` as `400 Bad Request`, and both mean "no
checks would run", but they are not the same problem and send your human to
different places. Collapsing them into "audit creation failed" gives your
human the wrong instruction.

- **`VERIFICATION_REQUIRED`**: the plan includes checks, but this domain
  hasn't proven control of it yet (email or TXT verification). Tell your
  human to verify the domain on the Forge site — nothing to do with billing.
- **`NO_CHECKS_AVAILABLE`**: the account's plan doesn't grant any active
  checks at all, regardless of verification. Tell your human this is a plan
  question, not a verification one.

## `DOMAIN_NOT_FOUND` — one code, two statuses depending on where you hit it

- **Where:** `POST /audits`
- **Status:** `400 Bad Request` (not 404 — the id you sent was an argument to
  a write that cannot succeed, not a lookup on an owned resource)

- **Where:** `GET /domains/{domain_id}`
- **Status:** `404 Not Found` (this route looks the domain up directly, and
  the resource is simply absent)

Both statuses mean the same thing: the `domainId` (or `domain_id`) you used
doesn't exist or doesn't belong to this account — deliberately collapsed
into one answer, the same reasoning as `AUDIT_NOT_FOUND` below, so probing
ids can't be used to tell "never existed" from "not yours" apart. Check it
against `GET /domains` rather than retrying with the same id. **Don't branch
on the status code alone** — an agent that treats `400` and `404` as two
different problems will handle the identical situation two different ways
depending only on which endpoint it called.

## `DOMAIN_NOT_ACTIVE_FOR_CHECK` — won't fix itself with a retry

- **Where:** `POST /audits`
- **Status:** `400 Bad Request`

`DOMAIN_NOT_ACTIVE_FOR_CHECK` means the domain exists but is currently
switched off for checks on the Forge site; that's a setting your human needs
to flip, not something an audit request can work around.

## `AUDIT_NOT_FOUND` — a 404 that may mean "not yours"

- **Where:** `GET /audits/{audit_id}`, `GET /audits/{audit_id}/status`
- **Status:** `404 Not Found`

This code is returned both when an audit id never existed and when it belongs
to a different account — deliberately collapsed into one answer, so that
probing ids can't be used to tell which. **If you get this on an id you
previously created successfully, the audit was not deleted.** Something else
is wrong — most likely you're using the wrong key or the wrong id — not the
audit disappearing.

## `INVALID_CURSOR` / `INVALID_FILTER` — fix the request, don't retry it

- **Where:** `GET /audits`
- **Status:** `400 Bad Request`

`INVALID_CURSOR` means the `cursor` value you sent back wasn't one this API
issued — don't hand-construct a cursor; only pass back `nextCursor` values
you received. `INVALID_FILTER` means `source`, `auditStatus` or `result` was
set to a value outside the fixed set those fields accept; the error message
lists the allowed values. Both are request-shape problems: retrying
unchanged will fail the same way.

## `AGENT_KEY_INVALID` — a 401 your agent cannot diagnose

- **Where:** any endpoint
- **Status:** `401 Unauthorized`

This single code covers an unknown key, a revoked key, an expired key, and an
agent (or its owning account) that has been blocked. That's deliberate — it
keeps a caller who is probing keys from learning which case they hit. The
practical consequence is that **your agent cannot tell these apart either**,
and should not guess. Tell your human to open the My Agents page on the Forge
site and check what it says about this agent's key. See
[authentication.md](authentication.md) for the exact wording to use, and for
why an expired key is fixed with **Extend**, not a new key.

## Generic `422` validation errors

Any endpoint can return `422` for a request that doesn't match its schema —
a `limit` outside `1`–`100`, a date that isn't a valid ISO date, a missing
`domainId` on `POST /audits`, a non-integer `audit_id`. These have no
`errorCode`; see "Two response shapes" above for the payload format, and
[endpoints.md](endpoints.md) or [../openapi.json](../openapi.json) for the
exact parameter types and constraints per endpoint.
