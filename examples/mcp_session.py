#!/usr/bin/env python3
"""A complete agent session against the Magery Forge MCP server.

The same walkthrough session.py does over the HTTP API — list domains,
choose one, start an audit, poll until it finishes, then read the finished
report — but as MCP tool calls over Streamable HTTP instead of HTTP
requests. See ../docs/mcp.md for the full tool reference this script is
driving.

Requires Python 3.11+ (this script's own `except*` in main() is 3.11
syntax; the SDK itself supports 3.10) and the official MCP Python SDK
(`pip install mcp`), plus the MAGERY_FORGE_API_KEY environment variable
(see ../docs/authentication.md for how to get one — it is the same key the
HTTP API uses, not a second one to issue). Never pass the key as a
command-line argument: it would land in your shell history and in the
process table where any other user on the machine can read it.

See ../AGENTS.md and ../docs/errors.md for the rules this script follows:
poll get_audit_status, not get_audit, on a sane interval; don't retry a
start_audit that comes back AUDIT_ALREADY_RUNNING; a failed auth can't be
diagnosed by the agent. docs/errors.md's "Over MCP: a third shape, and no
status code" section explains why this script's error handling looks
different from session.py's despite following the same rules: there is no
status code here, only an `errorCode` inside a failed CallToolResult's text.

TWO THINGS THAT DIFFER FROM THE HTTP API, WORTH KNOWING BEFORE YOU ADAPT
THIS SCRIPT:

- Tool ARGUMENTS are named exactly like the Python parameters they are
  (snake_case: `domain_id`, `audit_id`, `date_from`) — not like the HTTP
  JSON body's camelCase field names (`domainId`). See ../docs/mcp.md's
  argument tables for the exact name of every argument.
- Tool RESULTS are still camelCase, because they're the same response
  models the HTTP API serialises (`domainId`, `auditStatus`, `nextCursor`).
  Only the arguments going in are snake_case; the JSON coming back reads
  exactly like an HTTP response body.
"""

import asyncio
import contextlib
import json
import os
import sys

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# The SDK's own recommended timeouts for an MCP transport's http client (30s
# connect/write/pool, 300s read - a server may hold a response stream open):
# mcp.shared._httpx_utils.MCP_DEFAULT_TIMEOUT / MCP_DEFAULT_SSE_READ_TIMEOUT,
# as of the installed mcp 2.2.0. That module's own create_mcp_http_client()
# would build the client in one call, but it is a PRIVATE module (the
# leading underscore, and no re-export from `mcp` or `mcp.client` - checked
# against both) - `pip install mcp` is unpinned, so importing a private path
# would let an unrelated SDK release silently break every published copy of
# this script with an ImportError nothing here explains. Building the same
# httpx2.AsyncClient by hand, with these two constants inlined, keeps the
# only import this script needs from the SDK's PUBLIC surface.
_MCP_TIMEOUT_SECONDS = 30.0
_MCP_SSE_READ_TIMEOUT_SECONDS = 300.0

# The published MCP endpoint. MAGERY_FORGE_MCP_URL overrides it only so
# this script can be exercised against a local mock in tests — the default
# below is the one that ships and the one you should use.
DEFAULT_MCP_URL = "https://forge.magery.ai/mcp"

# docs/limits.md: "An audit itself takes minutes to finish, not seconds.
# Poll ... on an interval measured in tens of seconds, not a tight loop."
# MAGERY_FORGE_POLL_INTERVAL_SECONDS overrides this only for testing this
# script itself — the default below is the production interval; don't
# lower it against the real API.
DEFAULT_POLL_INTERVAL_SECONDS = 30

# Bounded so a stuck audit doesn't poll forever. At the default interval
# this is 20 minutes, well past the "minutes, not seconds" an audit takes.
MAX_POLL_ATTEMPTS = 40

# The API does not publish an enum of auditStatus values; these are the two
# terminal values the service actually returns. Anything else ("new",
# "in_progress", ...) means the audit is still running.
TERMINAL_AUDIT_STATUSES = {"done", "error"}

# Hints for the request-shape errors docs/errors.md says matter most to
# tell apart, so a human reading this script's output gets the specific
# next step rather than a generic "audit creation failed". Identical to
# session.py's table: the codes and what they mean don't change over MCP,
# only how the error reaches this script.
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
        "check it against list_domains rather than retrying."
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


def mcp_url():
    return os.environ.get("MAGERY_FORGE_MCP_URL", DEFAULT_MCP_URL)


def poll_interval_seconds():
    raw = os.environ.get("MAGERY_FORGE_POLL_INTERVAL_SECONDS")
    if raw is None:
        return DEFAULT_POLL_INTERVAL_SECONDS
    try:
        return float(raw)
    except ValueError:
        fail(f"MAGERY_FORGE_POLL_INTERVAL_SECONDS is not a number: {raw!r}")


def _tool_error_payload(text):
    """Pull the errorCode/message JSON out of a failed tool call's text.

    docs/errors.md: the SDK prefixes a failed call's text with "Error
    executing tool <name>: ", so this is never the whole string — treat
    everything from the first `{` as the payload. Two shapes carry no JSON
    at all (a bad argument, rejected by the tool's schema before it runs;
    an unexpected server-side failure) — this returns None for those and
    the caller falls back to the raw text.
    """
    start = text.find("{")
    if start == -1:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


def fail_auth():
    # AGENT_KEY_INVALID covers an unknown, revoked or expired key, and a
    # blocked agent or account - all with the same code, on purpose. The
    # agent cannot tell these apart, so it should not guess.
    fail(
        "authentication failed (AGENT_KEY_INVALID). This can mean an "
        "unknown, expired or revoked key, or that this agent or its "
        "owning account has been blocked - there is no way for this "
        "script to tell which. Ask your human to open the My Agents page "
        "on forge.magery.ai and check what it says about this agent's key."
    )


def _fail_tool(what, raw_text):
    """The generic fallback for a failed tool call once AGENT_KEY_INVALID
    and (for start_audit) AUDIT_ALREADY_RUNNING have already been checked
    for and handled specially."""
    payload = _tool_error_payload(raw_text)
    if payload is None:
        fail(f"{what} failed: {raw_text}")
    code = payload.get("errorCode")
    if code == "AGENT_KEY_INVALID":
        fail_auth()
    hint = ERROR_HINTS.get(code)
    suffix = f" ({hint})" if hint else ""
    fail(f"{what} failed: {code} - {payload.get('message')}{suffix}")


async def call_tool(session, name, arguments, what):
    """Call one tool and return its parsed JSON result, or fail() with a
    clear message. See docs/errors.md's "Over MCP" section: unlike the HTTP
    API there is no status code to branch on here, only `isError` on the
    result and the errorCode inside its text."""
    result = await session.call_tool(name, arguments)
    if not result.is_error:
        return json.loads(result.content[0].text)
    raw = result.content[0].text if result.content else ""
    _fail_tool(what, raw)


async def list_domains(session):
    print("listing domains...")
    payload = await call_tool(session, "list_domains", {}, what="listing domains")
    domains = payload.get("domains", [])
    if not domains:
        fail(
            "no domains are registered for this account yet. Ask your "
            "human to add one at forge.magery.ai before running this "
            "script."
        )
    return domains


async def find_running_audit(session, domain_name):
    # docs/errors.md: "find the running audit with list_audits (filter by
    # domain) and poll its status" - do not retry start_audit.
    #
    # `domain` is a SUBSTRING filter server-side, and results are newest
    # first: a request for "example.com" also matches "staging.example.com",
    # so the first (newest) row is not necessarily the audit that hit
    # AUDIT_ALREADY_RUNNING, and it may already be finished. Fetch a page
    # and pick the first entry whose domainName matches exactly and whose
    # auditStatus is not terminal - never trust audits[0].
    payload = await call_tool(
        session, "list_audits", {"domain": domain_name},
        what="looking up the already-running audit",
    )
    audits = payload.get("audits", [])
    for audit in audits:
        if audit.get("domainName") == domain_name and audit.get("auditStatus") not in TERMINAL_AUDIT_STATUSES:
            return audit["id"]
    fail(
        f"got AUDIT_ALREADY_RUNNING for {domain_name!r} but list_audits found "
        "no running audit for exactly that domain (domain is a substring "
        "filter, so results can include other domains and already-finished "
        "audits); this should not happen"
    )


async def start_or_find_audit(session, domain):
    domain_id = domain["id"]
    domain_name = domain["name"]
    print(f"starting an audit for {domain_name} (domainId {domain_id})...")
    # start_audit's argument is `domain_id` (the tool's own Python parameter
    # name), not the HTTP body's `domainId` - see this file's module
    # docstring.
    result = await session.call_tool("start_audit", {"domain_id": domain_id})
    if not result.is_error:
        payload = json.loads(result.content[0].text)
        audit_id = payload["id"]
        print(f"audit {audit_id} created, status={payload['auditStatus']}")
        return audit_id
    raw = result.content[0].text if result.content else ""
    payload = _tool_error_payload(raw)
    code = payload.get("errorCode") if payload else None
    if code == "AGENT_KEY_INVALID":
        fail_auth()
    if code == "AUDIT_ALREADY_RUNNING":
        # Normal, not a failure - see docs/errors.md. Do not retry start_audit.
        print("an audit is already running for this domain; finding it instead of starting a new one")
        return await find_running_audit(session, domain_name)
    _fail_tool("starting an audit", raw)


async def poll_status(session, audit_id):
    interval = poll_interval_seconds()
    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        payload = await call_tool(
            session, "get_audit_status", {"audit_id": audit_id},
            what="polling audit status",
        )
        audit_status = payload["auditStatus"]
        print(f"[{attempt}/{MAX_POLL_ATTEMPTS}] audit {audit_id} status={audit_status}")
        if audit_status in TERMINAL_AUDIT_STATUSES:
            return
        await asyncio.sleep(interval)
    fail(
        f"audit {audit_id} did not reach a terminal status after "
        f"{MAX_POLL_ATTEMPTS} attempts ({MAX_POLL_ATTEMPTS * interval:.0f}s "
        "at the current interval); giving up. Check the audit later with "
        "get_audit."
    )


async def fetch_detail(session, audit_id):
    print(f"fetching detail for audit {audit_id}...")
    return await call_tool(
        session, "get_audit", {"audit_id": audit_id}, what="fetching audit detail",
    )


async def _connect_and_initialize(stack, key):
    """Enter the http client, the Streamable HTTP transport and the
    ClientSession (in that order, all on `stack` so they unwind cleanly on
    any failure below) and complete the initialize handshake. Returns the
    live session.

    Wrapped in its own try/except: ../docs/authentication.md is explicit
    that a 404 at this URL is not an authentication problem — "MCP routing
    is not enabled on this deployment... no amount of retrying or
    reissuing will change it" — and that is worth a distinct message here
    rather than a raw traceback the same way an auth failure would print.
    """
    # streamable_http_client is an httpx2 client by contract, not a plain
    # httpx one — passing a bare `httpx.AsyncClient` here would fail at the
    # type boundary, not at the network. See this file's module-level
    # comment on _MCP_TIMEOUT_SECONDS for why this is built by hand instead
    # of via the SDK's own (private) create_mcp_http_client().
    try:
        http_client = await stack.enter_async_context(
            httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {key}"},
                timeout=httpx2.Timeout(_MCP_TIMEOUT_SECONDS, read=_MCP_SSE_READ_TIMEOUT_SECONDS),
            )
        )
        read, write = await stack.enter_async_context(
            streamable_http_client(mcp_url(), http_client=http_client)
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
    except Exception as exc:
        fail(
            f"could not connect to {mcp_url()}: {exc}\n"
            "If this was a 404, MCP routing is not enabled on this "
            "deployment - that is not an authentication problem and no "
            "amount of retrying will change it. Use the HTTP API instead "
            "(see ../docs/endpoints.md and examples/session.py), or ask "
            "your human to check the deployment."
        )
    return session


async def run_session():
    key = api_key()

    async with contextlib.AsyncExitStack() as stack:
        session = await _connect_and_initialize(stack, key)

        # Not required for the flow below, but this is the discovery step a
        # real agent runtime performs before it ever calls a tool: ask the
        # server what it can do rather than hard-coding the six names. See
        # ../docs/mcp.md for what each one takes and returns.
        tools = await session.list_tools()
        print(f"connected; {len(tools.tools)} tools available: " + ", ".join(t.name for t in tools.tools))

        domains = await list_domains(session)
        domain = domains[0]
        print(f"chose domain {domain['name']} (id {domain['id']}) out of {len(domains)} available")

        audit_id = await start_or_find_audit(session, domain)
        await poll_status(session, audit_id)
        detail = await fetch_detail(session, audit_id)

    print(
        f"audit {audit_id} finished: status={detail['auditStatus']} "
        f"result={detail.get('resultIndicator')} score={detail.get('score')}"
    )
    recommendations = detail.get("recommendations")
    if recommendations:
        print("recommendations:")
        print(recommendations)


def main():
    try:
        asyncio.run(run_session())
    except* SystemExit:
        # fail() has already printed the real reason to stderr and called
        # sys.exit(1) by the time we get here. anyio's TaskGroup (inside the
        # SDK's streamable_http_client/ClientSession context managers) wraps
        # that SystemExit in one or more nested exception groups while the
        # transport's background tasks unwind, rather than letting it pass
        # through bare - verified by running this script for real against a
        # stuck audit (see this task's report). `except*` finds it at
        # whatever depth it landed and re-raises anything else in the group
        # unchanged; for the SystemExit itself, this just gives the process
        # the plain exit code the printed message already promised, instead
        # of a nested traceback under it.
        sys.exit(1)


if __name__ == "__main__":
    main()
