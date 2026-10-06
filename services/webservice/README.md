# Web service

The `webservice` service runs an HTTP server inside the bot on the master node. It carries two
things on one port:

* the **REST API** used by the [RestAPI plugin](../../plugins/restapi/README.md) — served always;
* the **staff admin console** — an HTML UI with pages, login and session — served only when you
  switch it on (see below).

It runs on the **master node only**, and starts after the service bus
(`services/webservice/service.py:24`).

---

## Turning it on

1. **Create the configuration file** `config/services/webservice.yaml` with a `DEFAULT:` block.
   The service is switched on **by the file**: the loader reads
   `config/services/<service>.yaml`, and a missing file means no configuration and no service
   (`core/services/base.py:177`, `services/webservice/service.py:29-56`). Start from the sample:
   `samples/services/webservice.yaml`.

2. **Set `frontend: true`** if you want the web console. Without it — the default — the service
   serves the REST API only: no pages, no login, no session cookie
   (`schemas/webservice_schema.yaml:17`, `services/webservice/shell.py:75`).

3. **Configure a login** (`auth:`) and a session secret (`session:`) if you use the console.
   Without an `auth:` block every console page answers 403 (deny by default).

With the bot's `validation: strict`, a bad value in this file stops the service at load and names
the problem; with `validation: lazy` (the default) it is logged
(`core/services/base.py:181-186`).

---

## Configuration

All keys live under `DEFAULT:` in `config/services/webservice.yaml`. Nothing here is derived from a
request, so the same file behaves the same behind a reverse proxy as it does on localhost.

| Key | Default | What it does | Source |
|-----|---------|--------------|--------|
| `listen` | `0.0.0.0` | Interface the HTTP server binds to. | `service.py:46` |
| `port` | `9876` | TCP port. Must be 1024–65535 and unique per node. | `schemas/webservice_schema.yaml:10`, `core/utils/validators.py:170` |
| `debug` | `false` | Serves `/openapi.json`, `/docs` and `/redoc` for API testing (loopback and private networks only — see the warning below). | `service.py:92`, `service.py:115-125` |
| `log_level` | `info` | Default level filter of the console's log panel: `info`, `warning` or `all`. The same choice is available per page as `?level=`. | `schemas/webservice_schema.yaml:22` |
| `frontend` | `false` | Install the web console. `false` = REST API only. | `schemas/webservice_schema.yaml:17`, `shell.py:75` |
| `dashboard.live` | `true` | Serve the live update stream (Server-Sent Events) on the dashboard. | `schemas/webservice_schema.yaml:33` |
| `dashboard.refresh_seconds` | `2` | How often changed fragments are re-rendered and pushed, in seconds (1–60). | `schemas/webservice_schema.yaml:36` |
| `dashboard.stream_clients` | `16` | Most concurrent live streams (1–256). | `schemas/webservice_schema.yaml:39` |
| `session.secret` | *(unset → random per start)* | Signs the session cookie. Unset means every restart signs everyone out. | `schemas/webservice_schema.yaml:49`, `session.py:221` |
| `session.cookie_name` | `dcssb_session` | Session cookie name. | `schemas/webservice_schema.yaml:50`, `session.py:48` |
| `session.max_age` | `86400` | Session lifetime in seconds. | `schemas/webservice_schema.yaml:53` |
| `session.https_only` | *(unset → derived from `listen`)* | The `Secure` flag on the session cookie. Unset: `false` on a loopback-only bind, `true` on anything else. | `schemas/webservice_schema.yaml:59`, `session.py:140-169` |
| `session.same_site` | `lax` | Cookie `SameSite`: `lax`, `strict` or `none`. Keep `lax` — `strict` makes every OAuth login fail. | `schemas/webservice_schema.yaml:63`, `session.py:201-212` |
| `auth.public_base_url` | *(none)* | The public origin (scheme + host) the console is reachable at. Redirect URIs are built from this, never from a request. | `schemas/webservice_schema.yaml:73` |
| `auth.local.enabled` | `false` | Enable username/password login. | `schemas/webservice_schema.yaml:80` |
| `auth.local.users[].username` | *(required)* | The account name. | `schemas/webservice_schema.yaml:87` |
| `auth.local.users[].password_hash` | *(required)* | A PBKDF2 hash — never a plaintext password. | `schemas/webservice_schema.yaml:90` |
| `auth.local.users[].roles` | *(required)* | Role **names** of the bot's role model (`Admin`, `DCS Admin`, …). An unknown name is refused at startup. | `schemas/webservice_schema.yaml:93-97` |
| `auth.local.users[].scope` | *(unset = unrestricted)* | The `managed_by` values this account may see and act on (the web-only equivalent of a Discord manager's scope). | `schemas/webservice_schema.yaml:106-110` |
| `auth.discord.enabled` | `false` | Enable Discord OAuth login. | `schemas/webservice_schema.yaml:122` |
| `auth.discord.client_id` | *(none)* | The Discord application's client id. | `schemas/webservice_schema.yaml:123` |
| `auth.discord.client_secret` | *(none)* | Inline client secret. | `schemas/webservice_schema.yaml:126` |
| `auth.discord.client_secret_key` | *(none)* | Name of a key in the bot's secret store. When set it **wins over** `client_secret`. | `schemas/webservice_schema.yaml:130` |
| `auth.discord.member_ttl` | `60` | Seconds a member resolution may be cached when the bot runs without the Server Members Intent (clamped 1–3600). | `schemas/webservice_schema.yaml:136` |
| `auth.breakglass.enabled` | `false` | Enable the single emergency Admin credential, for the day the identity provider is what broke. | `schemas/webservice_schema.yaml:143` |
| `auth.breakglass.username` | *(none)* | The emergency account name. | `schemas/webservice_schema.yaml:144` |
| `auth.breakglass.password_hash` | *(none)* | A PBKDF2 hash, same format as `auth.local`. | `schemas/webservice_schema.yaml:145` |

`Source` paths are relative to `services/webservice/`.

Generate a password hash (never store plaintext):

```
python -m services.webservice.auth.local --hash --username <name> --roles Admin
```

Store the Discord client secret in the bot's secret store rather than inline:

```
python -c "from core.utils.os import set_password; set_password('discord_client_secret', '<the secret>')"
```

then set `auth.discord.client_secret_key: discord_client_secret`.

---

## How to reach the UI

With `frontend: true`, the console is at the `listen`/`port` you set — for the default config,
`http://localhost:9876/`. An anonymous visitor is redirected to the login page; a signed-in visitor
without the needed role gets an "access denied" page.

### Discord OAuth login

1. In the Discord developer portal (your application → **OAuth2 → Redirects**), register **exactly**:

   ```
   <auth.public_base_url>/auth/discord/callback
   ```

   With the default `public_base_url` that is `http://localhost:9876/auth/discord/callback`. The URI
   is built from `auth.public_base_url` and never from a request header, so a proxy cannot move it.

2. The console asks for the scopes **`identify`** (who signed in) and **`guilds.members.read`**
   (one fallback membership lookup when the bot cannot read the guild's members — see the problems
   section).

3. Supply the client secret through `auth.discord.client_secret_key` (preferred) or
   `auth.discord.client_secret`. Enabled with neither — or with a store key that does not exist — the
   service refuses to install the login backend at startup and logs why, rather than starting a login
   that cannot complete.

A signed-in Discord user's **roles** come from the running bot's own role model (`bot.yaml →
roles:`), so the console and the bot's commands can never disagree about who is an Admin.

### Local username/password login

Enable `auth.local`, list users and give each the role names it should hold. This is the only login
door on a headless (`no_discord: true`) install.

---

## Who can see what

Roles are the bot's role names from `bot.yaml` (`roles:`). The two-cluster-role pair is `Admin` and
`DCS Admin` (`services/webservice/scope.py:90`).

| Page or control | Who |
|-----------------|-----|
| Dashboard (`/`), Servers (`/servers`), Players (`/players`), Nodes (`/nodes`), Instances (`/instances`) | `Admin` and `DCS Admin` see everything; a **manager** sees only their own servers (and the nodes/instances that carry them) |
| Logs page (`/logs`) and a node's *Download log* | `Admin` only |
| A server's own page and its **Missions** tab | `Admin`, `DCS Admin`, and any **manager** of that server |
| A server's **Configuration** tab — DCS config and coalition passwords | `Admin`, and a manager for the servers in their scope |
| A server's **Configuration** tab — channels | `Admin` only (a manager may not write channels) |
| Server **and** player controls (start/stop/restart, kick/ban/mute/message, mission add/remove/load/…) | `Admin`, `DCS Admin`, and managers on their own servers |
| Node controls (restart, shut down, upgrade, take servers offline, bring servers online) | `Admin` only |

**Managers and scope.** A *manager* is a signed-in identity whose scope holds a server's
`managed_by` value. A manager sees and acts on **their own servers only** — a server that declares no
`managed_by` is nobody's manager server: it is left out of every page for them, and a crafted request
naming it is refused. For a local user the scope is declared in the config
(`auth.local.users[].scope`); for a Discord user it comes from the roles that map to `managed_by`.
Node controls are deliberately `Admin`-only and unscoped: a manager's scope is a set of *servers*, and
taking a whole node out of service must never widen out of it.

**What is not gated by this model:** the REST API. Its endpoints carry the [RestAPI
plugin](../../plugins/restapi/README.md)'s own authentication (API key / JWT) and are configured in
`config/plugins/restapi.yaml`, not here.

---

## TLS

**This service does not terminate TLS.** Put a reverse proxy (nginx, caddy, …) in front of it and
serve HTTPS there. Two consequences matter:

* Set `session.https_only: true` once the console is reached over TLS, and leave `session.same_site`
  at `lax`.
* If you bind `listen` to anything other than loopback and do **not** set `https_only`, the cookie is
  derived `Secure` (`session.py:140-169`). A browser does not return a `Secure` cookie over plain
  HTTP, so the login will appear to succeed and the very next request will be refused. The service
  logs exactly this warning at startup. The two ways out are the ones you actually have: bind to
  `127.0.0.1`, or terminate TLS in front and set `session.https_only: true`.

---

## Problems people actually hit

| Symptom | Cause and fix |
|---------|---------------|
| "Login seems to work, then the next page is refused" | A `Secure` session cookie served over plain HTTP on a non-loopback bind. Bind to `127.0.0.1`, or terminate TLS and set `session.https_only: true`. (`session.py:140-169`) |
| Discord login "fails the first time, works on retry", or every Discord login fails | `session.same_site` is not `lax`. `strict` drops the cookie on the OAuth callback. Set it back to `lax`. (`session.py:201-212`) |
| Everyone is signed out after every restart | `session.secret` is unset, so a random key is used per start. Set it to make sessions survive a restart. (`session.py:221-224`) |
| A user who lost a role keeps it for a while (or a signed-in page says "console is starting up") | With `privileged_intents: false`, member roles are re-resolved at most every `member_ttl` (default 60 s). After a restart, signed-in visitors get a page that retries itself for up to 60 s — no sign-in is needed, it resolves on its own. (`auth.discord.member_ttl`; `shell.py:419-490`) |
| The console is not there at all | `frontend` is not `true`. The default serves the REST API only. (`shell.py:75`) |
| The port is already in use | Ports must be 1024–65535 and unique per node; the service retries the bind a few times and then logs an error. (`core/utils/validators.py:170`, `service.py:175-188`) |

### A warning about `debug`

`debug: true` turns on the API documentation endpoints (`/docs`, `/redoc`, `/openapi.json`) and
uvicorn's access log. Those endpoints are reachable only from loopback and private networks
(`service.py:115-125`), but they still lay out your API, and the access log prints the OAuth
callback URL. Enable `debug` only while testing a locally reachable install — do not expose the port
to the internet with it on.

---

## Where the bot log lives

The console's log panel, and the node row's *Download log*, read the bot's own rotating log at
`logs/dcssb-<node>.log`, relative to the bot's working directory (`run.py:87`). Rotation size and
count come from the bot's `main.yaml` logging block, not from this service.

---

## More

* [`I18N.md`](I18N.md) — translating the console (adding a language, extracting and compiling
  catalogs).
* [`DEVELOPER.md`](DEVELOPER.md) — the design record: the write surface, the confirm tokens, the
  background-submit path, member resolution, the retryable page. Operator-facing rules are not
  defined there; it explains how the console is built.
* [`core/ACTIONS.md`](../../core/ACTIONS.md) — the action layer the write controls reach.
* [`plugins/restapi/README.md`](../../plugins/restapi/README.md) — the REST API served on the same
  port.
