<!-- GENERATED FILE — DO NOT EDIT.
Produced by `python -m app.scripts.build_agents_docs` from backend/.
Edits here are overwritten and lost; change the tool instead. -->

# MCP

Forge exposes the same six operations as an MCP server, over **Streamable
HTTP**, at:

```
https://forge.magery.ai/mcp
```

It takes the same header every request to the HTTP API takes — the same
key, not a second one to issue:

```
Authorization: Bearer <key>
```

Each tool below is named, described, and given an argument table — name,
type, allowed values where the schema declares an enum, bounds where it
declares them, whether it is required, and its default — taken from the
tool's own published input schema (what a client sees from `tools/list`),
not retyped by hand. A failed tool call does not look like a failed HTTP
request; see [errors.md](errors.md) for that shape, including the SDK's own
`Error executing tool <name>: ` prefix, rather than this page repeating it.

Argument names below are the tool's own Python parameter names — snake_case
(`audit_status`, `date_from`), not [endpoints.md](endpoints.md)'s camelCase
query parameters (`auditStatus`, `from`). Results are unaffected: a tool's
JSON result is the same camelCase response shape the HTTP API returns.

## `list_domains`

List every domain registered on your account.

Takes no arguments. For each domain, returns its id, its name, when it
was added, whether it has proven ownership by email verification or by
publishing a DNS TXT record (each a timestamp, or absent if not yet
proven), and whether it is currently active for automated security
checks. Also returns the maximum number of domains your plan allows to
be active for checks at once.

**Arguments:** none.

## `get_domain`

Look up one domain on your account by its id.

Requires domain_id, the id of a domain previously returned by
list_domains. Returns the same fields list_domains does, for that one
domain. An id that does not exist, or that belongs to a different
account, is reported the same way: not found.

**Arguments**

| Name | Type | Allowed values | Bounds | Required | Default |
|---|---|---|---|---|---|
| `domain_id` | integer | — | — | yes | — |

## `list_audits`

List security audits run on your domains, newest first.

Every argument is optional. domain matches domains whose name contains
the given text. date_from and date_to (each a calendar date) bound the
range an audit was started in, inclusive of both ends. source is how
the audit was started: "on_demand" for one requested directly, or
"autopilot" for one the account's automated schedule started.
audit_status is the audit's current stage: "new", "in_progress",
"done", or "error". result is its overall verdict once finished:
"green", "yellow", or "red". limit caps how many audits are returned in
one call (default 20, maximum 100). cursor requests the next page and
is the nextCursor value a previous call to this tool returned — omit it
for the first page.

Returns a page of audit summaries — each with the audit's id, when it
started, the domain it ran against, its status, result and numeric
score — plus nextCursor, which is present when another page follows and
absent on the last page.

**Arguments**

| Name | Type | Allowed values | Bounds | Required | Default |
|---|---|---|---|---|---|
| `domain` | string | — | — | no | none |
| `date_from` | date (`YYYY-MM-DD`) | — | — | no | none |
| `date_to` | date (`YYYY-MM-DD`) | — | — | no | none |
| `source` | string | `on_demand`, `autopilot` | — | no | none |
| `audit_status` | string | `new`, `in_progress`, `done`, `error` | — | no | none |
| `result` | string | `green`, `yellow`, `red` | — | no | none |
| `limit` | integer | — | 1–100 | no | `20` |
| `cursor` | string | — | — | no | none |

## `start_audit`

Start a new security audit on one of your domains.

Requires domain_id, the id of a domain on your account that is active
for checks. Runs every check your plan grants for whatever the domain
has verified so far — email verification and DNS TXT verification
unlock different checks, so verifying more of a domain can make a run
more thorough. Fails if the domain does not exist or is not active for
checks, if your plan currently grants no checks for it, or if an audit
is already running on that domain.

Returns the id of the new audit, its starting status, and how many
checks were queued for it. Poll get_audit_status with the returned id
to follow its progress.

**Arguments**

| Name | Type | Allowed values | Bounds | Required | Default |
|---|---|---|---|---|---|
| `domain_id` | integer | — | — | yes | — |

## `get_audit_status`

Check the progress of an audit, cheaply.

Requires audit_id, the id returned by start_audit or list_audits.
Returns just the audit's current status ("new", "in_progress", "done",
or "error"), its overall result once finished ("green", "yellow", or
"red"; absent while still in progress), and its numeric score out of
100 once at least one check has a result. This is the tool to call
repeatedly while waiting for an audit to finish; get_audit returns the
same audit in much more detail, with a much larger response, and is
better suited to reading the results once the audit is done.

**Arguments**

| Name | Type | Allowed values | Bounds | Required | Default |
|---|---|---|---|---|---|
| `audit_id` | integer | — | — | yes | — |

## `get_audit`

Get the full results of one audit.

Requires audit_id, the id returned by start_audit or list_audits.
Returns the audit's status, overall result and score, an overview
description and top-level recommendations, its history of status
changes over time, and every check that ran: each check's slug, name,
category, status, result severity, what this run found, what the check
looks for in general, and what to do about a failure.

A check's name and category are localized to the owning account's
locale and change when that account changes language. Match a check on
its slug or its checkId, never on its name or category.

**Arguments**

| Name | Type | Allowed values | Bounds | Required | Default |
|---|---|---|---|---|---|
| `audit_id` | integer | — | — | yes | — |
