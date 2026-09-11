# AGENTS.md — Magery Forge Agent API

This file is a condensed reference for a coding agent integrating against the
Magery Forge agent API. It states the rules; for prose explanations see
[README.md](README.md), [docs/authentication.md](docs/authentication.md),
[docs/errors.md](docs/errors.md) and [docs/limits.md](docs/limits.md). For the
full machine-readable contract, see [openapi.json](openapi.json).

**This product is in beta.** Forge also exposes an MCP server, over
Streamable HTTP at `https://forge.magery.ai/mcp`. It offers the same six
operations as the HTTP API below, available as MCP tools: `list_domains`,
`get_domain`, `list_audits`, `start_audit`, `get_audit_status`,
`get_audit`. It takes the same `Authorization: Bearer <key>` as the HTTP
API — the same key, not a second one to issue.

## Base URL

```
https://forge.magery.ai/api/agents/v1
```

## The whole API: six endpoints, and no others

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/domains` | List the domains your agent's owner has added |
| `GET` | `/domains/{domain_id}` | One domain by id; 404 if it doesn't exist or isn't yours |
| `GET` | `/audits` | List audits, filterable by domain, date range, source, status, result |
| `POST` | `/audits` | Start an audit on one domain (`{"domainId": <id>}`) |
| `GET` | `/audits/{audit_id}` | Full detail for one audit, including its checks |
| `GET` | `/audits/{audit_id}/status` | Cheap poll: status, result indicator, score only |

There is no endpoint to create a domain or issue a key, and nothing outside
this table is yours to call. If a path you want isn't in this table, it does
not exist. Do not guess at one; a plausible name like `/domains/{id}/verify`
is not a real endpoint.

## Authentication

Every request carries:

```
Authorization: Bearer <key>
```

**Your agent cannot obtain this key itself.** Keys are issued from a
cookie-authenticated page only a signed-in human can reach. The first thing a
fresh agent should do is ask its human, in these words:

> Sign in at forge.magery.ai → the header menu → User Profile → My Agents →
> give the agent a name → **Issue access key** → copy it immediately; it is
> shown once and never again.

Put the key in the `MAGERY_FORGE_API_KEY` environment variable (or a config
file you read from), and **never paste it into the conversation** — a
credential typed into chat ends up in that chat's logs and may reach a model
provider.

Keys expire **90 days** after they're issued. When yours does, ask your human
in these words — pressing **Extend** keeps the existing secret valid, so your
stored key keeps working with nothing to reconfigure, unlike issuing a new
one:

> Ask your human to open My Agents. If your key is listed as **Expired**,
> press **Extend** — nothing else needs changing. If it is not listed at all,
> it was revoked: ask for a new one.

See [docs/authentication.md](docs/authentication.md) for why revoke-and-reissue
is the wrong move here.

## Rules an agent must follow

- **Poll `/audits/{audit_id}/status`, not `/audits/{audit_id}`, in a loop.**
  Status is the field-thin endpoint built for polling; the detail endpoint
  serialises every check and is expensive. An audit takes minutes — poll on a
  sane interval, not a tight loop. `auditStatus` takes one of four values:
  `new`, `in_progress`, `done`, `error`. `done` and `error` are terminal —
  stop polling once you see either and move on to `GET /audits/{audit_id}`.
  `new` and `in_progress` both mean the audit is still running; keep
  polling.
- **Don't retry a 409.** `AUDIT_ALREADY_RUNNING` means the service already has
  an audit in flight for that domain — this is normal, not an error. Poll the
  existing audit instead of retrying the `POST`.
- **A 404 may mean "not yours", not "doesn't exist".** `AUDIT_NOT_FOUND` is
  returned both for an id that never existed and for one that belongs to
  another account, on purpose — an agent cannot use it to enumerate ids. Don't
  conclude the audit "was deleted."
- **A 401 cannot be diagnosed by your agent.** `AGENT_KEY_INVALID` covers an
  unknown, revoked or expired key, and an agent (or its owner's account) that
  has been blocked — all with the same code, on purpose. Don't guess which;
  tell your human to check the My Agents page. See
  [docs/errors.md](docs/errors.md).
- **Back off on 429.** Rate limits are enforced and are not published as
  numbers here because they live outside this repository and can change.
  Treat 429 as "slow down", not as a fatal error.
- **A 404 from `https://forge.magery.ai/mcp` itself means MCP routing is not
  enabled on this deployment.** That is the endpoint URL, not an operation on
  it, so a 404 there is never "the id isn't yours" — it means the MCP server
  is not reachable at that address and nothing you send will change that. Use
  the HTTP API below instead, and tell your human. Every operation is
  available over HTTP regardless.

## Minimal flow

1. `GET /domains` with the Bearer header → pick a `domainId`.
2. `POST /audits` with `{"domainId": <id>}` → get back the new audit's `id`.
   If you get `AUDIT_ALREADY_RUNNING`, use the id from `GET /audits` instead.
3. Poll `GET /audits/{audit_id}/status` on a sane interval until
   `auditStatus` reaches a terminal value.
4. `GET /audits/{audit_id}` → read the finished checks and recommendations.

## Error shape

An error your agent's own request caused looks like:

```json
{"detail": {"errorCode": "SOME_CODE", "message": "human-readable text"}}
```

A `422` from malformed input (a bad query parameter, a missing required
field) uses the web framework's own validation shape instead, with no
`errorCode`:

```json
{"detail": [{"loc": ["query", "limit"], "msg": "...", "type": "..."}]}
```

Over MCP the shape is different again — a result with `isError` set, whose
text prefixes that same JSON object with `Error executing tool <name>: ` and
carries no status code. See [docs/errors.md](docs/errors.md).

Full code list, what each means, and what to do about it: see
[docs/errors.md](docs/errors.md).
