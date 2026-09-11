<!-- GENERATED FILE — DO NOT EDIT.
Produced by `python -m app.scripts.build_agents_docs` from backend/.
Edits here are overwritten and lost; change the route or the model instead. -->

# Endpoints

Every endpoint below is under `https://forge.magery.ai/api/agents/v1`, takes
`Authorization: Bearer <key>`, and returns JSON. Full parameter types,
defaults and schema fields are in `../openapi.json`; error codes and what to
do about them are in `errors.md`.


## `GET /audits`

List Audits

**Parameters**

- `domain` (query, optional)
- `from` (query, optional)
- `to` (query, optional)
- `source` (query, optional)
- `auditStatus` (query, optional)
- `result` (query, optional)
- `limit` (query, optional)
- `cursor` (query, optional)

**Responses:** `200`, `422`

## `POST /audits`

Create Audit

**Request body:** `CreateAuditRequest` (required) — see `../openapi.json` for its fields.

**Responses:** `201`, `422`

## `GET /audits/{audit_id}`

Get Audit

**Parameters**

- `audit_id` (path, required)

**Responses:** `200`, `422`

## `GET /audits/{audit_id}/status`

Get Audit Status

**Parameters**

- `audit_id` (path, required)

**Responses:** `200`, `422`

## `GET /domains`

List Domains

**Responses:** `200`, `422`

## `GET /domains/{domain_id}`

Get Domain

**Parameters**

- `domain_id` (path, required)

**Responses:** `200`, `422`
