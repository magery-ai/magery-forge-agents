#!/usr/bin/env python3
"""A complete agent session against the Magery Forge agent API.

List domains, choose one, start an audit, poll until it finishes, then read
the finished report. Uses only the Python standard library — no dependency
to install first.

Requires the MAGERY_FORGE_API_KEY environment variable (see
../docs/authentication.md for how to get one). Never pass the key as a
command-line argument: it would land in your shell history and in the
process table where any other user on the machine can read it.

See ../AGENTS.md and ../docs/errors.md for the rules this script follows:
poll the status endpoint, not the detail endpoint, on a sane interval; don't
retry a 409 (AUDIT_ALREADY_RUNNING); a 401 can't be diagnosed by the agent.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# The published base URL. MAGERY_FORGE_API_BASE_URL overrides it only so
# this script can be exercised against a local mock in tests — the default
# below is the one that ships and the one you should use.
DEFAULT_BASE_URL = "https://forge.magery.ai/api/agents/v1"

# docs/limits.md: "An audit itself takes minutes to finish, not seconds.
# Poll ... on an interval measured in tens of seconds, not a tight loop."
# MAGERY_FORGE_POLL_INTERVAL_SECONDS overrides this only for testing this
# script itself — the default below is the production interval; don't
# lower it against the real API.
DEFAULT_POLL_INTERVAL_SECONDS = 30

# Bounded so a stuck audit doesn't poll forever. At the default interval
# this is 20 minutes, well past the "minutes, not seconds" an audit takes.
MAX_POLL_ATTEMPTS = 40

# docs/limits.md: "slow down and try again after a pause, increasing the
# pause if it keeps happening." The 429 backoff below doubles each time,
# capped here so it never waits absurdly long between retries.
MAX_BACKOFF_SECONDS = 300

# A single call's 429 retries used to reuse MAX_POLL_ATTEMPTS (40) as its
# bound. With the doubling backoff above that made one call's worst case
# roughly three hours - far longer than the "stuck audit" bound
# MAX_POLL_ATTEMPTS exists to enforce for status polling. Rate limiting on
# one call is a different failure than an audit that never finishes, so it
# gets its own, much smaller bound.
MAX_RATE_LIMIT_RETRIES = 5

# The API does not publish an enum of auditStatus values (it's a plain
# string field in openapi.json); these are the two terminal values the
# service actually returns. Anything else ("new", "in_progress", ...) means
# the audit is still running.
TERMINAL_AUDIT_STATUSES = {"done", "error"}

# Hints for the request-shape errors docs/errors.md says matter most to
# tell apart, so a human reading this script's output gets the specific
# next step rather than a generic "audit creation failed".
ERROR_HINTS = {
    "VERIFICATION_REQUIRED": (
        "this domain hasn't proven control of it yet (email or TXT "
        "verification) - verify it on the Forge site."
    ),
    "NO_CHECKS_AVAILABLE": (
        "the account's plan doesn't grant any active checks - this is a "
        "plan question, not a verification one."
    ),
    "DOMAIN_NOT_FOUND": (
        "that domainId doesn't exist or doesn't belong to this account - "
        "check it against GET /domains rather than retrying."
    ),
    "DOMAIN_NOT_ACTIVE_FOR_CHECK": (
        "the domain exists but checks are switched off for it on the "
        "Forge site - a human needs to flip that setting."
    ),
}


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def api_key():
    key = os.environ.get("MAGERY_FORGE_API_KEY")
    if not key:
        fail(
            "MAGERY_FORGE_API_KEY is not set.\n"
            "Ask your human to sign in at forge.magery.ai, then: header "
            "menu -> User Profile -> My Agents -> give the agent a name -> "
            "Issue access key. Then:\n"
            "  export MAGERY_FORGE_API_KEY=<the key>\n"
            "Never pass the key as a command-line argument."
        )
    return key


def base_url():
    return os.environ.get("MAGERY_FORGE_API_BASE_URL", DEFAULT_BASE_URL)


def poll_interval_seconds():
    raw = os.environ.get("MAGERY_FORGE_POLL_INTERVAL_SECONDS")
    if raw is None:
        return DEFAULT_POLL_INTERVAL_SECONDS
    try:
        return float(raw)
    except ValueError:
        fail(f"MAGERY_FORGE_POLL_INTERVAL_SECONDS is not a number: {raw!r}")


def request(method, path, key, body=None):
    """Call one endpoint and return (status_code, parsed_json_or_None)."""
    url = base_url() + path
    data = None
    headers = {"Authorization": f"Bearer {key}"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = None
        return e.code, payload
    except urllib.error.URLError as e:
        fail(f"could not reach {url}: {e.reason}")


def request_with_backoff(method, path, key, body=None, what=""):
    """Call one endpoint, backing off and retrying on 429 per
    docs/limits.md ("Back off on 429" - treat it as "slow down", not a
    fatal error), up to MAX_RATE_LIMIT_RETRIES. The wait doubles on each
    successive 429 (starting from the poll interval), capped at
    MAX_BACKOFF_SECONDS, per docs/limits.md's "increasing the pause if it
    keeps happening"."""
    attempts = 0
    wait = poll_interval_seconds()
    while True:
        status, payload = request(method, path, key, body=body)
        if status == 429:
            attempts += 1
            if attempts > MAX_RATE_LIMIT_RETRIES:
                fail(f"kept getting 429 (rate limited) while {what}; giving up")
            print(f"rate limited (429) while {what}; waiting {wait}s and retrying", file=sys.stderr)
            time.sleep(wait)
            wait = min(wait * 2, MAX_BACKOFF_SECONDS)
            continue
        return status, payload


def error_code_and_message(payload):
    detail = (payload or {}).get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        return detail.get("errorCode"), detail.get("message")
    return None, None


def fail_auth():
    # AGENT_KEY_INVALID covers an unknown, revoked or expired key, and a
    # blocked agent or account - all with the same code, on purpose. The
    # agent cannot tell these apart, so it should not guess.
    fail(
        "authentication failed (401 AGENT_KEY_INVALID). This can mean an "
        "unknown, expired or revoked key, or that this agent or its "
        "owning account has been blocked - there is no way for this "
        "script to tell which. Ask your human to open the My Agents page "
        "on forge.magery.ai and check what it says about this agent's key."
    )


def fail_generic(status, payload, action):
    detail = (payload or {}).get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, list):
        errors = "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg')}" for e in detail
        )
        fail(f"{action} failed: {status} validation error - {errors}")
    code, message = error_code_and_message(payload)
    if code:
        hint = ERROR_HINTS.get(code)
        suffix = f" ({hint})" if hint else ""
        fail(f"{action} failed: {status} {code} - {message}{suffix}")
    fail(f"{action} failed: unexpected {status} response: {payload}")


def list_domains(key):
    print("listing domains...")
    status, payload = request_with_backoff("GET", "/domains", key, what="listing domains")
    if status == 401:
        fail_auth()
    if status != 200:
        fail_generic(status, payload, "listing domains")
    domains = payload.get("domains", [])
    if not domains:
        fail(
            "no domains are registered for this account yet. Ask your "
            "human to add one at forge.magery.ai before running this "
            "script."
        )
    return domains


def find_running_audit(key, domain_name):
    # docs/errors.md: "find the running audit with GET /audits (filter by
    # domain) and poll its status" - do not retry the POST.
    #
    # `domain` is a SUBSTRING filter server-side, and results are newest
    # first: a request for "example.com" also matches "staging.example.com",
    # so the first (newest) row is not necessarily the audit that hit
    # AUDIT_ALREADY_RUNNING, and it may already be finished. Fetch a page
    # and pick the first entry whose domainName matches exactly and whose
    # auditStatus is not terminal - never trust audits[0].
    query = urllib.parse.urlencode({"domain": domain_name})
    status, payload = request_with_backoff(
        "GET", f"/audits?{query}", key, what="looking up the already-running audit"
    )
    if status == 401:
        fail_auth()
    if status != 200:
        fail_generic(status, payload, "looking up the already-running audit")
    audits = payload.get("audits", [])
    for audit in audits:
        if audit.get("domainName") == domain_name and audit.get("auditStatus") not in TERMINAL_AUDIT_STATUSES:
            return audit["id"]
    fail(
        f"got AUDIT_ALREADY_RUNNING for {domain_name!r} but GET /audits found "
        "no running audit for exactly that domain (domain is a substring "
        "filter, so results can include other domains and already-finished "
        "audits); this should not happen"
    )


def start_or_find_audit(key, domain):
    domain_id = domain["id"]
    domain_name = domain["name"]
    print(f"starting an audit for {domain_name} (domainId {domain_id})...")
    status, payload = request_with_backoff(
        "POST", "/audits", key, body={"domainId": domain_id}, what="starting an audit"
    )
    if status == 201:
        audit_id = payload["id"]
        print(f"audit {audit_id} created, status={payload['auditStatus']}")
        return audit_id
    if status == 401:
        fail_auth()
    code, _ = error_code_and_message(payload)
    if status == 409 and code == "AUDIT_ALREADY_RUNNING":
        # Normal, not a failure - see docs/errors.md. Do not retry POST.
        print("an audit is already running for this domain; finding it instead of starting a new one")
        return find_running_audit(key, domain_name)
    fail_generic(status, payload, "starting an audit")


def poll_status(key, audit_id):
    interval = poll_interval_seconds()
    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        status, payload = request_with_backoff(
            "GET", f"/audits/{audit_id}/status", key, what="polling audit status"
        )
        if status == 401:
            fail_auth()
        if status != 200:
            fail_generic(status, payload, "polling audit status")
        audit_status = payload["auditStatus"]
        print(f"[{attempt}/{MAX_POLL_ATTEMPTS}] audit {audit_id} status={audit_status}")
        if audit_status in TERMINAL_AUDIT_STATUSES:
            return
        time.sleep(interval)
    fail(
        f"audit {audit_id} did not reach a terminal status after "
        f"{MAX_POLL_ATTEMPTS} attempts ({MAX_POLL_ATTEMPTS * interval:.0f}s "
        "at the current interval); giving up. Check the audit later with "
        f"GET /audits/{audit_id}."
    )


def fetch_detail(key, audit_id):
    print(f"fetching detail for audit {audit_id}...")
    status, payload = request_with_backoff(
        "GET", f"/audits/{audit_id}", key, what="fetching audit detail"
    )
    if status == 401:
        fail_auth()
    if status != 200:
        fail_generic(status, payload, "fetching audit detail")
    return payload


def main():
    key = api_key()
    domains = list_domains(key)
    domain = domains[0]
    print(f"chose domain {domain['name']} (id {domain['id']}) out of {len(domains)} available")

    audit_id = start_or_find_audit(key, domain)
    poll_status(key, audit_id)
    detail = fetch_detail(key, audit_id)

    print(
        f"audit {audit_id} finished: status={detail['auditStatus']} "
        f"result={detail.get('resultIndicator')} score={detail.get('score')}"
    )
    recommendations = detail.get("recommendations")
    if recommendations:
        print("recommendations:")
        print(recommendations)


if __name__ == "__main__":
    main()
