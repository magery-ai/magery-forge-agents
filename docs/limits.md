# Limits

## Rate limiting: back off on 429

Requests to this API are rate limited. If you get a `429` response, your
agent is sending requests too fast — slow down and try again after a pause,
increasing the pause if it keeps happening. This document does not publish
the exact request-rate threshold: it's enforced outside this repository and
can change without notice, and a stale published number would be worse than
no number at all. Design your agent to handle `429` gracefully rather than to
assume a specific ceiling.

This is separate from HTTP-level validation: a `429` carries no `errorCode`
body the way the errors in [errors.md](errors.md) do — treat any `429`,
regardless of body, as "slow down."

## One audit per domain at a time

`POST /audits` refuses to start a second audit for a domain that already has
one in flight — you'll get `AUDIT_ALREADY_RUNNING` (`409`). This is not a
capacity limit to work around with retries; it means the audit you wanted is
already running. See [errors.md](errors.md) for what to do instead (poll the
existing one).

An audit itself takes minutes to finish, not seconds. Poll
`GET /audits/{audit_id}/status` on an interval measured in tens of seconds,
not a tight loop — it's the cheap endpoint specifically so this is safe to do
sensibly, but it is not free to hammer.

## Pagination

`GET /audits` returns at most `limit` audits per call (default 20, maximum
100) plus a `nextCursor` for the next page. Pass `nextCursor` straight back
as `cursor` on the next call; don't construct your own cursor value — see
`INVALID_CURSOR` in [errors.md](errors.md).

## Key lifetime

An agent access key is valid for **90 days** from issue. This is a fixed
account-security limit, not something a plan changes. See
[authentication.md](authentication.md) for what to do when a key expires
(press **Extend**, don't request a new one) and how to tell an expired key
apart from a revoked one (you can't — ask your human to check).
