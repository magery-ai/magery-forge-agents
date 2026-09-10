#!/usr/bin/env bash
# A complete agent session against the Magery Forge agent API.
#
# Requires: curl and jq.
#
# List domains, choose one, start an audit, poll until it finishes, then
# read the finished report.
#
# Requires the MAGERY_FORGE_API_KEY environment variable (see
# ../docs/authentication.md for how to get one). Never pass the key as a
# command-line argument: it would land in your shell history and in the
# process table where any other user on the machine can read it.
#
# See ../AGENTS.md and ../docs/errors.md for the rules this script follows:
# poll the status endpoint, not the detail endpoint, on a sane interval;
# don't retry a 409 (AUDIT_ALREADY_RUNNING); a 401 can't be diagnosed by
# the agent.

set -u -o pipefail

# The published base URL. MAGERY_FORGE_API_BASE_URL overrides it only so
# this script can be pointed at a local mock for testing - the default
# below is the one that ships and the one you should use.
BASE_URL="${MAGERY_FORGE_API_BASE_URL:-https://forge.magery.ai/api/agents/v1}"

# docs/limits.md: "An audit itself takes minutes to finish, not seconds.
# Poll ... on an interval measured in tens of seconds, not a tight loop."
# MAGERY_FORGE_POLL_INTERVAL_SECONDS overrides this only for testing this
# script itself - the default below is the production interval; don't
# lower it against the real API.
# INTEGER seconds only, unlike session.py which accepts a fractional
# value: the arithmetic below ($((wait * 2)) in api_with_backoff, and
# `sleep "$wait"`) is bash integer arithmetic. A fractional value here
# makes that arithmetic produce an empty or malformed number and `sleep`
# fails partway through a backoff.
POLL_INTERVAL_SECONDS="${MAGERY_FORGE_POLL_INTERVAL_SECONDS:-30}"

# Bounded so a stuck audit doesn't poll forever. At the default interval
# this is 20 minutes, well past the "minutes, not seconds" an audit takes.
MAX_POLL_ATTEMPTS=40

# docs/limits.md: "slow down and try again after a pause, increasing the
# pause if it keeps happening." The 429 backoff below doubles each time,
# capped here so it never waits absurdly long between retries.
MAX_BACKOFF_SECONDS=300

# A single call's 429 retries used to reuse MAX_POLL_ATTEMPTS (40) as its
# bound. With the doubling backoff above that made one call's worst case
# roughly three hours - far longer than the "stuck audit" bound
# MAX_POLL_ATTEMPTS exists to enforce for status polling. Rate limiting on
# one call is a different failure than an audit that never finishes, so it
# gets its own, much smaller bound.
MAX_RATE_LIMIT_RETRIES=5

fail() {
    echo "error: $1" >&2
    exit 1
}

command -v curl >/dev/null 2>&1 || fail "curl is required but was not found on PATH."
command -v jq >/dev/null 2>&1 || fail "jq is required but was not found on PATH."

if [ -z "${MAGERY_FORGE_API_KEY:-}" ]; then
    fail "MAGERY_FORGE_API_KEY is not set.
Ask your human to sign in at forge.magery.ai, then: header menu -> User
Profile -> My Agents -> give the agent a name -> Issue access key. Then:
  export MAGERY_FORGE_API_KEY=<the key>
Never pass the key as a command-line argument."
fi

# api METHOD PATH [JSON_BODY]
# Prints the response body to stdout and sets $HTTP_STATUS. Never logs the
# Authorization header.
api() {
    local method="$1" path="$2" body="${3:-}"
    local response status
    if [ -n "$body" ]; then
        response=$(curl -sS -w '\n%{http_code}' -X "$method" \
            -H "Authorization: Bearer ${MAGERY_FORGE_API_KEY}" \
            -H "Content-Type: application/json" \
            -d "$body" \
            "${BASE_URL}${path}")
    else
        response=$(curl -sS -w '\n%{http_code}' -X "$method" \
            -H "Authorization: Bearer ${MAGERY_FORGE_API_KEY}" \
            "${BASE_URL}${path}")
    fi
    status="${response##*$'\n'}"
    HTTP_STATUS="$status"
    HTTP_BODY="${response%$'\n'"$status"}"
}

# api_with_backoff METHOD PATH [JSON_BODY] WHAT
# Retries on 429 per docs/limits.md ("Back off on 429" - treat it as "slow
# down", not a fatal error), up to MAX_RATE_LIMIT_RETRIES. The wait
# doubles on each successive 429 (starting from the poll interval), capped
# at MAX_BACKOFF_SECONDS, per docs/limits.md's "increasing the pause if it
# keeps happening".
api_with_backoff() {
    local method="$1" path="$2" body="$3" what="$4"
    local attempts=0
    local wait="$POLL_INTERVAL_SECONDS"
    while true; do
        api "$method" "$path" "$body"
        if [ "$HTTP_STATUS" = "429" ]; then
            attempts=$((attempts + 1))
            if [ "$attempts" -gt "$MAX_RATE_LIMIT_RETRIES" ]; then
                fail "kept getting 429 (rate limited) while ${what}; giving up"
            fi
            echo "rate limited (429) while ${what}; waiting ${wait}s and retrying" >&2
            sleep "$wait"
            wait=$((wait * 2))
            if [ "$wait" -gt "$MAX_BACKOFF_SECONDS" ]; then
                wait="$MAX_BACKOFF_SECONDS"
            fi
            continue
        fi
        return
    done
}

# fail_auth: AGENT_KEY_INVALID covers an unknown, revoked or expired key,
# and a blocked agent or account - all with the same code, on purpose. This
# script cannot tell these apart, so it does not guess.
fail_auth() {
    fail "authentication failed (401 AGENT_KEY_INVALID). This can mean an
unknown, expired or revoked key, or that this agent or its owning account
has been blocked - there is no way for this script to tell which. Ask your
human to open the My Agents page on forge.magery.ai and check what it says
about this agent's key."
}

# fail_generic ACTION
# Reads $HTTP_STATUS / $HTTP_BODY set by the last api call.
fail_generic() {
    local action="$1"
    local error_code message
    error_code=$(echo "$HTTP_BODY" | jq -r '.detail.errorCode // empty' 2>/dev/null)
    message=$(echo "$HTTP_BODY" | jq -r '.detail.message // empty' 2>/dev/null)
    if [ -n "$error_code" ]; then
        fail "${action} failed: ${HTTP_STATUS} ${error_code} - ${message}"
    fi
    fail "${action} failed: unexpected ${HTTP_STATUS} response: ${HTTP_BODY}"
}

echo "listing domains..."
api_with_backoff GET "/domains" "" "listing domains"
[ "$HTTP_STATUS" = "401" ] && fail_auth
[ "$HTTP_STATUS" = "200" ] || fail_generic "listing domains"

domain_count=$(echo "$HTTP_BODY" | jq '.domains | length')
if [ "$domain_count" -eq 0 ]; then
    fail "no domains are registered for this account yet. Ask your human to add one at forge.magery.ai before running this script."
fi

domain_id=$(echo "$HTTP_BODY" | jq -r '.domains[0].id')
domain_name=$(echo "$HTTP_BODY" | jq -r '.domains[0].name')
echo "chose domain ${domain_name} (id ${domain_id}) out of ${domain_count} available"

echo "starting an audit for ${domain_name} (domainId ${domain_id})..."
api_with_backoff POST "/audits" "{\"domainId\": ${domain_id}}" "starting an audit"
if [ "$HTTP_STATUS" = "201" ]; then
    audit_id=$(echo "$HTTP_BODY" | jq -r '.id')
    audit_status=$(echo "$HTTP_BODY" | jq -r '.auditStatus')
    echo "audit ${audit_id} created, status=${audit_status}"
elif [ "$HTTP_STATUS" = "401" ]; then
    fail_auth
elif [ "$HTTP_STATUS" = "409" ] && [ "$(echo "$HTTP_BODY" | jq -r '.detail.errorCode // empty')" = "AUDIT_ALREADY_RUNNING" ]; then
    # Normal, not a failure - see docs/errors.md. Do not retry POST.
    #
    # `domain` is a SUBSTRING filter server-side, and results are newest
    # first: a request for "example.com" also matches
    # "staging.example.com", so the first (newest) row is not necessarily
    # the audit that hit AUDIT_ALREADY_RUNNING, and it may already be
    # finished. Fetch a page and pick the first entry whose domainName
    # matches exactly and whose auditStatus is not terminal - never trust
    # audits[0].
    echo "an audit is already running for this domain; finding it instead of starting a new one"
    api_with_backoff GET "/audits?domain=$(printf '%s' "$domain_name" | jq -sRr @uri)" "" "looking up the already-running audit"
    [ "$HTTP_STATUS" = "401" ] && fail_auth
    [ "$HTTP_STATUS" = "200" ] || fail_generic "looking up the already-running audit"
    audit_id=$(echo "$HTTP_BODY" | jq -r --arg name "$domain_name" \
        '[.audits[] | select(.domainName == $name and (.auditStatus != "done" and .auditStatus != "error"))][0].id // empty')
    if [ -z "$audit_id" ]; then
        fail "got AUDIT_ALREADY_RUNNING for ${domain_name} but GET /audits found no running audit for exactly that domain (domain is a substring filter, so results can include other domains and already-finished audits); this should not happen"
    fi
else
    fail_generic "starting an audit"
fi

# The API does not publish an enum of auditStatus values; "done" and
# "error" are the two terminal values the service actually returns.
# Anything else ("new", "in_progress", ...) means the audit is still
# running.
attempt=0
reached_terminal=0
while [ "$attempt" -lt "$MAX_POLL_ATTEMPTS" ]; do
    attempt=$((attempt + 1))
    api_with_backoff GET "/audits/${audit_id}/status" "" "polling audit status"
    [ "$HTTP_STATUS" = "401" ] && fail_auth
    [ "$HTTP_STATUS" = "200" ] || fail_generic "polling audit status"
    audit_status=$(echo "$HTTP_BODY" | jq -r '.auditStatus')
    echo "[${attempt}/${MAX_POLL_ATTEMPTS}] audit ${audit_id} status=${audit_status}"
    if [ "$audit_status" = "done" ] || [ "$audit_status" = "error" ]; then
        reached_terminal=1
        break
    fi
    sleep "$POLL_INTERVAL_SECONDS"
done

if [ "$reached_terminal" -ne 1 ]; then
    fail "audit ${audit_id} did not reach a terminal status after ${MAX_POLL_ATTEMPTS} attempts ($((MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS))s at the current interval); giving up. Check the audit later with GET /audits/${audit_id}."
fi

echo "fetching detail for audit ${audit_id}..."
api_with_backoff GET "/audits/${audit_id}" "" "fetching audit detail"
[ "$HTTP_STATUS" = "401" ] && fail_auth
[ "$HTTP_STATUS" = "200" ] || fail_generic "fetching audit detail"

result_indicator=$(echo "$HTTP_BODY" | jq -r '.resultIndicator')
score=$(echo "$HTTP_BODY" | jq -r '.score')
final_status=$(echo "$HTTP_BODY" | jq -r '.auditStatus')
echo "audit ${audit_id} finished: status=${final_status} result=${result_indicator} score=${score}"

recommendations=$(echo "$HTTP_BODY" | jq -r '.recommendations // empty')
if [ -n "$recommendations" ]; then
    echo "recommendations:"
    echo "$recommendations"
fi
