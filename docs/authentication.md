# Authentication

Every request to `https://forge.magery.ai/api/agents/v1` carries a Bearer key:

```
Authorization: Bearer <key>
```

There is no other way in. There is no API key format your agent can construct
or guess, and no endpoint under this API that issues one.

## Your agent cannot get its own key

Keys are issued from a page that requires a signed-in browser session — it is
not part of this API and an agent cannot reach it. This means a fresh agent's
very first task is one it cannot complete alone. That's expected: ask your
human, in these words.

### Asking for a first key

> Sign in at forge.magery.ai → the header menu → User Profile → My Agents →
> give the agent a name → **Issue access key** → copy it immediately; it is
> shown once and never again.

Once you have it, put it in the `MAGERY_FORGE_API_KEY` environment variable,
or a config file your agent reads on startup. **Do not paste the key into the
conversation with your human or with a model.** A credential typed into chat
is written into that chat's logs and may reach a model provider — treat it
exactly like a password, because it is one.

### When a key expires

Keys are valid for **90 days** from the moment they're issued. When yours
stops working because it expired, ask for an **extension**, not a new key:

> Ask your human to open My Agents. If your key is listed as **Expired**,
> press **Extend** — nothing else needs changing. If it is not listed at all,
> it was revoked: ask for a new one.

Pressing **Extend** moves the key's expiry forward and leaves the secret
itself untouched — your agent keeps using the same `MAGERY_FORGE_API_KEY`
value with no reconfiguration. This is deliberately different from revoking
and reissuing, which would hand you a new secret to redeploy. Only ask for a
brand new key if My Agents shows the key gone entirely (revoked), not merely
expired.

### What a 401 does and does not tell you

Every authentication failure — an unknown key, a revoked key, an expired key,
or an agent (or its owner's account) that has been blocked — returns the same
error:

```json
{"detail": {"errorCode": "AGENT_KEY_INVALID", "message": "agent key is invalid, expired or revoked"}}
```

This is deliberate: an agent that could tell these apart by probing could use
that to work out whether a given key or agent name is real. It means **your
agent cannot self-diagnose a 401.** Don't guess, and don't retry blindly —
tell your human to open the My Agents page and check what it says about this
agent's key. See [errors.md](errors.md) for the full list of error codes.

## Beta status

Magery Forge is in beta. The agent API surface is `https://forge.magery.ai/api/agents/v1`;
a breaking change to it would arrive as a new version prefix, not a silent
change to this one. Forge also exposes an MCP server, over Streamable HTTP at
`https://forge.magery.ai/mcp`. It takes the exact same
`Authorization: Bearer <key>` documented above — the same key, not a second
one to issue — and the same causes reject it: an unknown, revoked or
expired key, or a blocked agent or account, all with the identical
`AGENT_KEY_INVALID` error. Your agent cannot self-diagnose that failure over
MCP any more than it can over HTTP; see above.

One failure at that URL is **not** an authentication problem and should not
be treated as one: if `https://forge.magery.ai/mcp` answers **404**, MCP
routing is not enabled on this deployment. Your key is fine, there is nothing
for your human to check on the My Agents page, and no amount of retrying or
reissuing will change it — the address simply is not serving MCP. Use the
HTTP API at `https://forge.magery.ai/api/agents/v1`, which offers the same
operations with the same key.
