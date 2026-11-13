# MCP Service

The MCP service exposes the bot's endpoints to an [MCP](https://modelcontextprotocol.io) client
(Claude Desktop, Claude Code, any Streamable HTTP client) as **tools**, so an agent can read the
fleet and start, stop or restart servers itself. A tool is an endpoint row: its name is the row's
action, its description the row's summary, its parameters the action's signature — the same rows the
REST API and the admin console serve, so a tool can never describe an action differently from the
route behind it.

It runs on the **master node only** and has no port of its own: the surface is a mount,
`POST /mcp`, on the [webservice's](../webservice/README.md) application — the same port as the
website (`config/services/webservice.yaml`, `port`, default `9876`).

---

## Configuration

The file is what switches the service on. Without `config/services/mcpservice.yaml` nothing is
mounted, and the service says so at start:

```
MCP: no config file at <config>/services/mcpservice.yaml - nothing is mounted. Create it with a 'DEFAULT:' block to serve /mcp.
```

```yaml
# config/services/mcpservice.yaml
DEFAULT:
  enabled: true                    # serve the MCP surface (default: false)
  keys:                            # one entry per MCP client
    - label: claude-desktop        # name of the client, unique; tail of its audit token
      key: change-me-long-random   # static bearer token this client presents
      level: control               # read | control
    - label: dashboard
      key: change-me-another-one
      level: read
    - label: cloud                 # a JWT client: no static key
      level: control
      jwt:
        key: https://cloud.example.com/api/v1        # expected signing key / issuer
        jwks_url: https://cloud.example.com/api/v1/.well-known/jwks.json
  transport:
    allowed_hosts:                 # MANDATORY - see below
      - dcs.example.com
      - 100.64.0.5:*
    allowed_origins:               # optional; empty accepts any Origin header
      - https://claude.ai
```

| Key                   | Required | What it does                                                                      |
|-----------------------|----------|-----------------------------------------------------------------------------------|
| `enabled`             | yes      | Serve the surface. `false` (or absent) mounts nothing.                             |
| `keys[].label`        | yes      | Name of the client: 1–64 characters of letters, digits, `.`, `_`, `-`; unique.     |
| `keys[].level`        | yes      | `read` or `control`. Any other value is refused — by the schema and at start.      |
| `keys[].key`          | one of   | Static bearer token, compared in constant time.                                   |
| `keys[].jwt`          | one of   | `key` + `jwks_url`: the bearer token is a JWT, verified against that JWKS endpoint.|
| `transport.allowed_hosts` | yes  | Host header values the surface answers. Every other request is refused with `421`. |
| `transport.allowed_origins` | no | Origin header values accepted; empty accepts any origin.                           |

`key` and `jwt` are **mutually exclusive**: one entry carries exactly one credential. The schema
refuses both, and refuses neither.

`allowed_hosts` is not optional. The MCP SDK enables DNS-rebinding protection by default, and with
an empty allow-list its middleware answers **`421 Invalid Host header` to every request** — a mount
that looks installed and serves nothing. An empty or missing `allowed_hosts` is therefore a startup
error, and the value must be the host (and port, when the client uses one) the client puts in its
Host header.

The file is validated against `schemas/mcpservice_schema.yaml` at load. With
`validation: strict` in `main.yaml` a misspelt key (`allowed_host` for `allowed_hosts`, `key` under
`transport`) stops the service at load and names the fault; with `validation: lazy` (the default) it
is logged and the file is loaded as written.

---

## Key levels

One `MCPServer` is built per level, each with its own tool list — a level's list is a promise the
agent plans against, so a `read` client is never shown a lifecycle tool that would answer "no".

| Level     | Tools                                                                                                           |
|-----------|------------------------------------------------------------------------------------------------------------------|
| `read`    | `list_servers`, `get_server_status`, `get_mission_info`, `get_mission_list`                                       |
| `control` | the four `read` tools plus `start_server`, `startup_server`, `stop_server`, `shutdown_server`, `restart_server`    |

The two DCS-level tools are the pair a SHUTDOWN instance needs: `startup_server` boots it and
`shutdown_server` parks it there, where `start_server` / `stop_server` are the process-level pair
(`STOPPED`). A tool's description states the state it answers for and names the sibling that covers
the other one, and a refusal names the tool that would work — `Server 'SRS-1' is Shutdown - use
startup_server.`

Risk classes map to levels: `read` rows are offered to both levels, `lifecycle`, `moderation` and
`config` rows to `control` only, and `node` and `secret` rows to no level at all. A row can also opt
out of MCP entirely.

---

## Audit

Every audited call records the verified caller as its actor, never the transport:

| Key type | Actor                    |
|----------|--------------------------|
| `key`    | `[mcp:key/<label>]`      |
| `jwt`    | `[mcp:jwt/<subject>]` — the token's subject, with `<label>` as the fallback |

---

## Connecting a client

Point the client at the website's port plus `/mcp` — `http://<host>:<port>/mcp`, or the `https://`
URL when a TLS terminator sits in front — and have it send the configured key as a bearer token:
the `Authorization` header carries `Bearer` and the key. The host in that URL must be one of
`transport.allowed_hosts`, and the path is `/mcp`: no trailing slash and no second `/mcp`. With
Claude Code that is one command:

```bash
claude mcp add --transport http dcs https://dcs.example.com:9876/mcp \
  --header "Authorization: Bearer ${MCP_KEY}"
```

A request with no token, an unknown token or a token for a level that is not served is answered
`401 {"detail": "Not authenticated"}` with `WWW-Authenticate: Bearer`, before any MCP session exists.

---

## Failure modes

* **The config file is absent** — nothing is mounted, the surface does not exist. Logged at INFO
  with the path that was read (`MCP: no config file at <config>/services/mcpservice.yaml - nothing
  is mounted. Create it with a 'DEFAULT:' block to serve /mcp.`).
* **No web application on this node** (no webservice, or it is not started) — the service refuses to
  start with `McpConfigError` and the registry logs
  `Service MCPService NOT started: MCP is enabled but this node serves no web application: ...`; the
  rest of the process keeps running. There is no fallback port: the surface is a mount.
* **No tool is visible to any configured key** — `McpConfigError`, the service is not started
  ("MCP is enabled and configured but serves no level"). A single level with keys but no visible row
  is logged as a warning and that level is not served; the others still are.
* **`allowed_hosts` empty or missing** — `McpConfigError` naming the `421` consequence, at startup
  rather than one refusal per request.
