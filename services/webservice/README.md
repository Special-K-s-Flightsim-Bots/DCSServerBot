# Service WebService
This service provides a simple web service. It is WIP, and it will be enhanced in the future.
Currently, you can use it with the [RestAPI plugin](/plugins/restapi/README.md).

## Configuration
The webservice can be configured in its config/services/webservice.yaml file:
```yaml
# config/services/webservice.yaml
DEFAULT:
  listen: 0.0.0.0   # the interface to bind the internal webserver to
  port: 9876        # the port the webservice is listening on
  debug: false      # Enable /openapi.json, /docs and /redoc endpoints to test the API (default: false)
```

## Admin web UI (staff console)

The service also serves the staff admin console (`/`). The page shows nodes, instances,
servers, players and the bot's log tail — and it updates on its own: the log tail and the
servers/KPI figures are pushed to the browser as HTML fragments over Server-Sent Events, so a
reader does not have to refresh the page. This is a progressive enhancement: with JavaScript
disabled the page still renders in full, it just is not live. If the stream cannot be used (for
example a reverse proxy that strips `text/event-stream`), the page falls back to polling the
fragments every 10 seconds.

### The write surface

Only three tables carry a write — **servers**, **players** and **nodes**; the last one (instances) is
read-only. The controls appear in **two screens that render the same declaration**: the dashboard's
**Servers** / **Players** / **Nodes** tabs, and the standalone **/servers** / **/players** / **/nodes**
pages. There is ONE declaration per surface — `SERVER_ACTIONS`, `PLAYER_ACTIONS` and `NODE_ACTIONS` in
`services/webservice/pages/actions.py` — and the route table, the capability map, the page's control
data and the confirm dialog are all readings of it, so a control cannot exist without a declared
capability and a declared action.

| Row    | Control              | Capability                  | Action (`__qualname__`) | Confirms |
|--------|----------------------|-----------------------------|-------------------------|----------|
| server | Startup              | `servers.startup`           | `startup_server`        | no       |
| server | Start                | `servers.start`             | `start_server`          | no       |
| server | Pause                | `missions.pause`            | `pause_mission`         | no       |
| server | Unpause              | `missions.unpause`          | `unpause_mission`       | no       |
| server | Restart              | `servers.restart`           | `restart_server`        | **yes**  |
| server | Shutdown             | `servers.shutdown`          | `shutdown_server`       | **yes**  |
| server | Stop                 | `servers.stop`              | `stop_server`           | **yes**  |
| server | Maintenance          | `servers.maintenance`       | `set_maintenance`       | no       |
| server | End maintenance      | `servers.clear_maintenance` | `clear_maintenance`     | no       |
| player | Kick                 | `players.kick`              | `kick_player`           | **yes**  |
| player | Ban                  | `players.ban`               | `ban_player`            | **yes**  |
| player | Chat / Popup         | `players.message`           | `message_player`        | no       |
| player | Mute                 | `players.mute`              | `mute_player`           | no       |
| player | Unmute               | `players.mute`              | `unmute_player`         | no       |
| node   | Restart              | `nodes.restart`             | `restart_node`          | **yes**  |
| node   | Shut down            | `nodes.shutdown`            | `shutdown_node`         | **yes**  |
| node   | Upgrade              | `nodes.upgrade`             | `upgrade_node`          | **yes**  |
| node   | Take servers offline | `nodes.offline`             | `take_node_offline`     | **yes**  |
| node   | Bring servers online | `nodes.online`              | `bring_node_online`     | no       |

**Two server controls open a DIALOG without confirming** (card W5d): *Shutdown* and *Startup* each
carry a `maintenance` **option** — a checkbox with its `off` companion (the `NodeOption` shape the
node row's *Take servers offline* uses), default ON — that mirrors Discord's `/server shutdown|startup`
flag defaults: Shutdown **sets** the maintenance flag, Startup **clears** it. Because the checkbox is a
form field with nowhere else to live, both post to their `<path>/confirm` dialog, where the box is
chosen; *Startup* is the non-destructive half, so its dialog is an **options** form and mints **no**
one-shot token. *Start* and *Stop* carry **no** option: the process-level pair touches no flag.

The write roles are `Admin` and `DCS Admin`, and every **server** and **player** capability above is
declared `scope_grants`: a **manager** (an identity whose scope holds a server's `managed_by`) reaches
them **on their own servers** and on nobody else's. The **node** row is the exception and stays so:
its five are declared `Admin`-only and **without** a scope grant
(`NODE_ROLES`, `pages/actions.py`), because a manager's scope is a set of *servers* and taking a whole
node — or every server on it — out of service must never widen out of it.

**Two things that look like one.** The server row's *Maintenance* / *End maintenance* is the
MAINTENANCE FLAG (`servers.maintenance`, a persisted switch that keeps a server out of service and is
the scheduler's to honour — the same operation as Discord's `/scheduler maintenance|clear`), and the
node row's *Take servers offline* / *Bring servers online* is POWER: it stops the servers that are up
and starts the ones it stopped. They are independent — a node can be powered off with every flag
clear, and a server can be flagged while it is running — and the words never cross: "offline" means
the SERVERS, never the node's own process, which is *Shut down* (`MAINTENANCE.md`).

**How a control is decided.** A control is built only when *all* of these are true, each read from the
one place that decides it:

* **capability** — `permissions.allows` (`services/webservice/permissions.py:146`), the same predicate
  the route's capability gate runs (`:693`), so "offered ⊆ authorised" holds by construction;
* **scope** — the row comes from the *scoped* source every page renders from, so a server (or a
  player's server) outside the caller's scope is not in the source at all and no control is built;
* **action availability** — `action_available(qualname)`, so an installation whose `mission` plugin is
  not loaded offers no button that could only answer a refusal (see
  [`core/ACTIONS.md`](../../core/ACTIONS.md#8-deployment-and-the-two-silent-failures));
* **the row's state** — `statuses` on a server control (Startup only on `SHUTDOWN`, Pause only on
  `RUNNING`, …; **empty means every state**, which is what the maintenance flag pair declares, since a
  flag is not a power state), `when_muted` on the mute pair and `when_maintenance` on the flag pair
  (so exactly one half of each pair is drawn), and `states` on a node control — where the power pair's
  gate is the node's OWN state (`node_power_states`: is it heartbeating and carrying servers? is a
  power-off on record?), never the servers' statuses and never the maintenance flag it no longer
  manipulates.

**An unusable control is ABSENT, never rendered disabled.** When no row of a table offers a control,
the Actions column is not rendered at all — no header, no empty cell — and the table's chip reads
`read-only` instead of `actions` (`services/webservice/templates/_table.html:35`). A `read-only` chip
therefore means *the console found no action to offer*, **not** "you are not allowed" — see the
empty-registry trap in `core/ACTIONS.md`.

**Every WRITE is a POST form** carrying the session's CSRF token and the **target in the body** —
never a state-changing GET, and never a target in a URL, so an action cannot be pointed at another
server or another player by editing a link (see the action layer's seam, `core/ACTIONS.md` §3). The
one control that is not a write is the node row's *Download log* (below): a **GET** link with its
target in the URL, because that is what a download is — and it is a READ, so there is nothing for a
CSRF token to protect.

### The node row's log download (card B3)

The node row carries ONE **read** control beside its write strip: *Download log*. It hands over THAT
node's own bot log file — agent and master alike — as a file download, on demand. It is a route
(`GET /nodes/{node}/log`, `pages/nodes.py`), never a render step, so the console's no-RPC-on-render
rule is intact: rendering a page asks no node for anything.

* **The gate is the log PANEL's own capability** — `logs.view` (`Admin` only, no scope grant),
  named through `pages/logs` as `LOG_DOWNLOAD_CAPABILITY` rather than re-spelled. No new capability
  exists; a caller who is not offered the control meets the SAME 403 the `/logs` panel answers with.
* **The path is resolved ON THE NODE.** The console sends the RELATIVE `logs/dcssb-<node>.log` — the
  very path `run.py:85` writes the node's own rotating log at — to `Node.read_file`, which resolves
  it in the working directory of the node that answers (the master for its own row, the agent's own
  process for an agent's row). A master-local absolute path would be wrong on any other machine
  (this project runs on WSL and Windows), so none is ever built; `plugins/admin/commands.py:515`
  reads a node-side file the same relative way.
* **The payload is bounded.** A log AT OR BELOW `LOG_DOWNLOAD_MAX_BYTES` (10 MiB — `run.py`'s own
  default `logrotate_size`) is served WHOLE; a log ABOVE it is **refused** with its own sentence and
  served not at all — never a truncated file that downloads like a complete one. The bytes are read
  before the check because `read_file` is a whole-file contract, so the limit bounds what the console
  HANDS BACK, and the ruling is stated on the constant.
* **Every failure is its own sentence**, in plain text (never a zero-byte download that looks like an
  empty log): the node is offline or unknown (404), there is no log file yet (404), the node cannot
  read its own file (403), it does not answer in time (504), it answers with a failure CODE instead
  of bytes (502 — `read_file` is `bytes | int` and an `int` is never content), or the log is above
  the limit (413).
* **What it does NOT do:** it does not tail, stream, merge or live-update an agent's log. The console's
  log PANEL stays master-only; a node's log is here only ever ONE complete file somebody asked for.

**A control submits in the background — the strip AND the dialog** (cards W4h, W4m). Every write is
submitted with `fetch` by the console's ONE interceptor
(`services/webservice/static/submit.js`, loaded by `base.html` on every page) and the page is handed
over to a fresh render at once, so a plain form POST no longer holds the browser while the action
runs and the row can show that something is running (cards W4g/W4k/W4l). A control that opens a
**dialog** (a confirmation, or Startup's options form, card W5d) is itself a plain form — its answer
IS the dialog page — but the dialog's OWN action form takes the background path too, and since card
W4m it hands the page back **the instant it is pressed**, closing the modal rather than waiting for a
shutdown to finish. A refusal is no longer shown in a line of the dialog's own (that line is gone):
it rides the one-shot notice like every other write's outcome.

That shows as TWO signals, with two scopes:

* **the pulse, on the control you pressed** — a control is `busy` (`aria-busy` + the animating class)
  while a write **through that control** has been issued and **the observable that action moves** has
  not reached the state its action **settles** at (card W4n). The expectation record names the
  submitted action's key, the observable it watches and the action's `settled` states
  (`pages/actions.remember_awaiting_change`), so pressing *Maintenance* pulses *Maintenance* and
  leaves *Startup* alone, and vice versa. For every power action the observable is the row's status,
  and the pulse ends only at the action's INTENDED end state — `RUNNING`/`PAUSED` for a
  startup/start/restart, `SHUTDOWN`/`STOPPED` for a shutdown/stop — NOT at "the status moved". A real
  DCS boot reads `LOADING` for most of the minute it takes (a state no control is gated on), and card
  W4n exists because the old "any change" rule spent the pulse one or two seconds in, so it was gone
  before anyone saw it; `LOADING` (and a shutdown's `SHUTTING_DOWN`) never ends it. A `restart` is
  submitted IN a settled state (`RUNNING`), so the read also requires the row to have LEFT that state
  first (the `departed` latch), which is what stops it re-arming. The flag pair watches the
  `server.maintenance` flag instead (a flag is not a power state: pressing the flag pair moves no
  status at all, and it lands before the response returns, so its expectation is spent on the next
  render rather than pulsing to the ceiling). It is bounded to `AWAIT_CHANGE_SECONDS`, so a start that
  never comes up cannot pulse forever; a failed or refused write drops the expectation at once, and a
  spent expectation is dropped the moment it is read (so it can never re-arm).
  A CONTROL WHOSE OPERATION IS PENDING STAYS ON THE ROW (card W4n): while the row is transitional
  (`LOADING`/`SHUTTING_DOWN`) and a start/restart is pending, `server_controls` renders that pending
  control's own glyph — `busy` and `DISABLED`, a statement rather than an invitation — where the row
  would otherwise render NOTHING (there is no control gated on `LOADING`). That is what gives the
  pulse a glyph to run on through the boot.
  IT IS PROCESS-SIDE AND KEYED BY TARGET (card W4m), held by `pages/actions` exactly like the seam's
  in-flight set — NOT in the session, whose cookie the route writes only on its reply: `submit.js`
  navigates the moment it fires the POST, so the render that is meant to show the pulse usually
  happened BEFORE that `Set-Cookie` landed, and the signal trailed the render (nothing at all, or a
  late pulse). It is read ONLY for a row the caller is already rendering (never listed, never
  counted), so it can say nothing about a server the caller cannot see.
* **the row marker, on the row** — the action seam's in-flight set says an action is running on this
  target right now (`core.actions.in_flight_targets`), but it keys the TARGET and cannot say WHICH
  one, so it is rendered as a class on the row's strip container (`busy` + `data-busy`) and **never
  on a glyph**. Stamping it on every control of the row is what made the wrong glyph blink; the fact
  belongs to the row, where it is honest, and the marker carries no animation.

The request is the form's own, unchanged; `keepalive` lets it outlive the navigation, and the action
seam does not cancel an abandoned handler.

The write's OUTCOME (a success, or a refusal) is carried back to the person by the **live path**:
`services/webservice/pages/live.py` renders the one-shot notice as a fragment target (`notice`),
delivered by the polling fallback on its next poll and by the stream's snapshot for an outcome that
is already stored when the stream connects (a refused write is decided in milliseconds, so the fresh
render's stream sees it) — so a refusal is seen without a reload. This holds for EVERY write, the
dialog's included, and for the refusals the ROUTE itself makes (no signed-in identity, a spent/absent
confirm token, an out-of-scope target — `pages/actions._refusal_notice`, card W4m), which used to be
a bare 403 nobody read. With JavaScript off every form is a plain POST, exactly as before.

**The confirm rule.** `restart`, `shutdown`, `stop`, `kick` and `ban` do not POST their action: they
POST to `<path>/confirm`, which renders the dialog (`services/webservice/templates/confirm.html`), and
the dialog's form carries a **one-shot confirm token** minted for that target. The action's own route
**refuses a POST that did not come through the dialog** (`services/webservice/pages/actions.py:709`):
a confirmation a `curl` can skip is decoration, so the check is server-side. The dialog's wording
names the **real consequence** in the user's terms — the players currently flying on a restart, the
count on a shutdown — assembled from the **resolved** object, never from a field the browser sent.
`WriteAction.dialog` is what decides "posts to a dialog": an action that **confirms** *or* **carries an
option** does (W5d, so Startup's maintenance box reaches its options form); only `confirm` mints the
token.

**EVERY node control posts to its dialog's path**, and the token is what differs: the lifecycle trio
and *Take servers offline* are confirm-required (`action.confirm`), so their own routes demand the
one-shot token; *Bring servers online* has **no option and no token** — its dialog is a one-button
form that runs the operation directly, because bringing the servers back *is* the operation. On the
**server** row it is the same shape for *Shutdown* (confirms, and carries the flag box) and *Startup*
(the box only, no token): the option's `field` is the ACTION's own parameter name, parsed from the body
and handed to `call_action`, with the `off` companion saying "off" for a cleared box.

The **maintenance flag pair** on the server row confirms nothing either: it is a reversible state
change, so both halves submit directly and have no dialog at all (the same rule the mute pair
follows).

**A NODE power action pulses the SERVER rows it moves** (card W7c). *Take servers offline* and *Bring
servers online* start or stop their servers INSIDE the bot's action, so no per-server write is
submitted from the browser and their rows would carry no expectation — the node row would behave and
the server rows would not. After such a write is **accepted**, `pages/actions.node_moved_names` seeds
the SAME expectation store on the servers the operation WILL move, describing each through the
SERVER_ACTIONS record (`shutdown` for *offline*, `startup` for *online*), so their rows pulse on the
servers page and the dashboard and END by the identical rules (settled state / failure / ceiling). The
servers are enumerated from the caller's own **scoped** source, matched on the node's name, so an
expectation is only ever seeded on a row the caller already sees (the non-disclosure rule, unchanged).
The set is a **prediction**: *offline* names the in-service servers (not `SHUTDOWN`/`UNREGISTERED`;
the engine's own `in_service` test), *online* names the servers the power-off RECORD stopped — read
BEFORE the action runs, because the action clears the record as it reverts it — or, with no record,
the servers that are down and unflagged; a server already in the settled state is not seeded, a
refused or failed write seeds nothing, and a second node action replaces (never stacks) an entry.

**The two server-side tokens that make a confirm real:**

* the **one-shot confirm token** — minted per dialog render, keyed on the action path and the resolved
  target, held in the session (bounded to 8 pending), compared in constant time and **spent on use**,
  so a replayed dialog form is refused rather than re-running the action
  (`mint_confirm_token` `:688`, `consume_confirm_token` `:709`);
* the **CSRF token** — one token per session, rendered into every control's form as `_csrf_token`
  (`services/webservice/session.py:51`) and re-checked by the route's `csrf_protect` dependency
  (`:300`). It is accepted, not spent, and there is **no CSRF middleware**: a blanket "unsafe method
  needs a token" check would refuse the RestAPI plugin's Bearer-token POSTs, which carry no cookie and
  no CSRF token.

The layer that a control ultimately reaches — the registry, `@action`, the `ActionContext`
constructors, `resolve_scoped_server`, `call_action` and the audit — is documented in
[`core/ACTIONS.md`](../../core/ACTIONS.md).

```yaml
# config/services/webservice.yaml
DEFAULT:
  # The log panel's default level filter: info (INFO and up, DEBUG hidden; the default) | warning | all.
  # The panel offers the same choice in the URL (?level=all) so a refresh keeps it.
  log_level: info
  # Live updates of the read-only dashboard.
  dashboard:
    live: true          # Serve the live stream at all (default: true). Off = the page is not live.
    refresh_seconds: 2  # how often the fragments are re-rendered and pushed, in seconds (default: 2).
                        #   Nothing is sent when nothing changed, so this is a poll interval, not a traffic rate.
    stream_clients: 16  # the most concurrent live streams (default: 16). Past the cap a client still gets
                        #   its snapshot and the stream closes cleanly.
  # Session cookie and login of the console: see the sample config (samples/services/webservice.yaml)
  # for the full `session:` and `auth:` blocks.
```

## Login

Two doors lead into the console, and both feed the SAME capability map, so who may see what has
one declaration:

* **Local username/password** (`auth.local`, see the sample) — the fallback, and the only door on a
  headless (`no_discord: true`) install. Password hashes are generated with
  `python -m services.webservice.auth.local --hash`.
* **Discord OAuth** (`auth.discord`, below) — a signed-in Discord user's roles drive the console,
  resolved through the **running bot's own role model** (`bot.yaml -> roles:` in its Discord
  meaning: a role **name** -> the Discord roles that grant it). The console and the bot's commands
  therefore cannot disagree about who is an Admin. A user who is not a member of the bot's guild is
  refused (with the reason in the log); a member with no mapped role signs in with an empty role
  set, and the ordinary deny-by-default rules apply.

### Member resolution, and the Server Members Intent

discord.py only caches guild members when the bot has the **Server Members Intent**. With
`privileged_intents: false` in `config/services/bot.yaml` — what this repo itself recommends for
guilds over 10.000 members — that cache is **empty for everyone**, the guild owner included, so the
console resolves the member itself instead of concluding that nobody is a member:

1. **Intents ON** (`privileged_intents: true`): the bot's **live member list** is used, exactly like
   the rest of the bot. A role change is effective on the very next request, and no extra Discord
   call happens.
2. **Intents OFF**: the login asks Discord for that ONE member (`Guild.fetch_member`, a single-member
   REST call — the *bulk* member list is what needs the intent), falling back to asking **as the
   user** (`GET /users/@me/guilds/<id>/member`, which is why the flow requests the
   `guilds.members.read` scope alongside `identify`). The per-request path then answers from a short
   in-memory cache (`auth.discord.member_ttl`, default **60 s**, clamped to 1..3600) and refreshes it
   in the background as soon as an entry is used after its TTL; a request never fails just because
   the entry aged out. A subject with **no** entry is refused while the refresh runs, and a refresh
   that **fails** drops the entry, so a stale answer is never kept alive by a failure.
   **Trade-off, stated plainly:** with the intent ON a removed member or role is effective
   immediately; with it OFF that takes effect within `member_ttl` plus one refresh — never at cookie
   expiry, and never at all while Discord is unreachable.

Each login logs **which path resolved the member** (`the bot member cache`, `the bot REST lookup` or
`the user-side members.read lookup`), so an operator can see what their intents setting exercises. A
lookup that fails never becomes a 500 and never becomes an open door: the login is refused and the
failure is logged.

> **Deliberate divergence from the bot's own behaviour.** The bot's player auto-matching *declines*
> when there is no member list (`services/bot/dcsserverbot.py:643`). The console must not copy that —
> refusing on an empty cache would lock the operator out of the admin UI — so this path degrades
> (one REST lookup per login, at most one refresh per `member_ttl`) instead of refusing. The
> corollary: **"is not a member of the bot's guild" is only ever logged when the bot can actually
> see members.** Without the intent the refusal names the intent/cache problem and the way out.

```yaml
# config/services/webservice.yaml
DEFAULT:
  auth:
    # The public origin this console is reachable at (scheme + host). Redirect URIs are built from
    # this and NEVER from a request header, so a reverse proxy cannot move them.
    public_base_url: http://localhost:9876
    discord:
      enabled: false                 # nothing is enabled unless this is `true`
      client_id: 123456789012345678  # the Discord application's client id
      # The client secret. `client_secret_key` NAMES a key in the bot's secret store
      # (config/.secret/<key>.pkl) and WINS when it is set; `client_secret` is the inline fallback.
      client_secret_key: discord_client_secret
      # client_secret: "..."         # inline alternative, used only when no key is named
      # member_ttl: 60               # per-request resolution cache, intents OFF only (1..3600)
```

**Register this EXACT redirect URI** in the Discord developer portal (your application →
OAuth2 → Redirects):

```
<auth.public_base_url>/auth/discord/callback
```

so, with the value above: `http://localhost:9876/auth/discord/callback`. The required **scopes are
`identify` and `guilds.members.read`**: `identify` learns *who* signed in, and `guilds.members.read`
is used for the one user-side membership lookup described above (only when the bot cannot read the
guild's members itself). The member's roles are still expanded through the bot's role map — that
lookup returns role IDs, not a second role model.

**Supplying the secret.** Preferred: put it in the bot's secret store and name the key from the
config, so the secret never sits in a YAML file:

```
python -c "from core.utils.os import set_password; set_password('discord_client_secret', '<the secret>')"
```

writes `config/.secret/discord_client_secret.pkl`, which is what `client_secret_key:
discord_client_secret` reads. Precedence, in order: `enabled` gates everything; `client_id` is
required when enabled; the secret comes from `client_secret_key` **when that key is set**,
otherwise from the inline `client_secret`. Enabled with neither — or with a store key that does not
exist — the service **refuses the backend at startup and logs why** rather than starting a login
that cannot complete.

> [!NOTE]
> `GET /auth/discord/callback?code=...` carries the one-time authorization code in its URL. Uvicorn
> is started at `WARNING` (see `services/webservice/service.py`), so its access log — which would
> print that line — is off. With `debug: true` it is on: enable debugging only while testing.


> [!NOTE]
> To access the API documentation, you can enable debug and access the documentation with these links: 
> http://localhost:9876/docs
> http://localhost:9876/redoc
> Please refer to the [OpenAPI specification](https://swagger.io/specification/) for more information.

> [!WARNING]
> Do NOT enable debug for normal operations, especially if you expose the port to the outside world.

> [!IMPORTANT]
> It is advisable to use a reverse proxy like nginx or caddy and maybe SSL encryption to secure the webservice. 
