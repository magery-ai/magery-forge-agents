# Magery Forge — Agent API

Magery Forge runs security audits against a person's domains and reports what
it finds. This repository documents the HTTP API an AI agent uses to act on
its owner's behalf: listing the domains they've added, starting an audit,
watching it run, and reading the result.

**This product is in beta.** The API may change; a breaking change would move
to a new version prefix rather than silently changing this one. Forge also
exposes an MCP server, over Streamable HTTP at `https://forge.magery.ai/mcp`,
with the same six operations available as MCP tools and authenticated with
the same Bearer key as the HTTP API — see [AGENTS.md](AGENTS.md) for the
tool names.

## Quick facts

- Base URL: `https://forge.magery.ai/api/agents/v1`
- Auth: a Bearer key, issued for your agent by a human Forge user from their
  own logged-in session (see [docs/authentication.md](docs/authentication.md))
- Six endpoints, and only six — this is the whole surface:
  - `GET /domains` — the domains your agent's owner has added
  - `GET /domains/{domain_id}` — one domain by id; 404 if it isn't yours
  - `GET /audits` — past and in-progress audits, filterable
  - `POST /audits` — start an audit on a domain
  - `GET /audits/{audit_id}` — full detail for one audit, including its checks
  - `GET /audits/{audit_id}/status` — the cheap poll: status, result, score
- MCP: the same six operations, at `https://forge.magery.ai/mcp` (Streamable
  HTTP) as MCP tools — `list_domains`, `get_domain`, `list_audits`,
  `start_audit`, `get_audit_status`, `get_audit` — with the same Bearer key,
  not a second one to issue. If that URL returns 404, MCP routing is not
  enabled on this deployment: use the HTTP API instead, which offers every
  one of those operations
- Full machine-readable spec: [openapi.json](openapi.json)

## Documents

- [AGENTS.md](AGENTS.md) — condensed reference for a coding agent: the rules,
  not the prose
- [docs/authentication.md](docs/authentication.md) — how your agent gets a
  Bearer key, the header it sends, and what to do when a key stops working
- [docs/endpoints.md](docs/endpoints.md) — generated endpoint reference
  (paths, parameters, status codes)
- [docs/errors.md](docs/errors.md) — every error code the API returns, what
  it means, and what your agent should do about it
- [docs/limits.md](docs/limits.md) — key expiry, one audit per domain at a
  time, and backing off on 429
- [openapi.json](openapi.json) — generated OpenAPI 3 spec for the six
  endpoints above, nothing else
- [llms.txt](llms.txt) — machine-readable summary of this repository, per the
  llms.txt convention

## Quick start

1. Ask your human for a Bearer key (see
   [docs/authentication.md](docs/authentication.md)) and put it in the
   `MAGERY_FORGE_API_KEY` environment variable. Never paste it into a
   conversation.
2. `GET /domains` to see what your agent's owner has added.
3. `POST /audits` with `{"domainId": <id>}` to start an audit. If one is
   already running for that domain you'll get `AUDIT_ALREADY_RUNNING` — that
   is normal, not a failure; poll the existing one instead. See
   [docs/errors.md](docs/errors.md).
4. Poll `GET /audits/{audit_id}/status` until `auditStatus` is a terminal
   value — it's the cheap endpoint, built for this. Don't poll the full
   detail endpoint in a loop.
5. `GET /audits/{audit_id}` for the finished report: checks, severities,
   recommendations.

## License

MIT — see [LICENSE](LICENSE).
