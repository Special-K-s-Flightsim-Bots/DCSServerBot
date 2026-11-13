# The action layer (`core/actions.py`)

An **action** is a transport-agnostic domain function that lives in a plugin's `actions.py` and is
decorated with `@action`. The same function is reached from Discord commands, from the MCP service
(`/mcp`), from `/api/v1`, and — through `ActionContext.from_web` — from the admin console's write
controls. This document is the map of that layer: how an action is registered, what an action's kind
means, how a console POST reaches one, what the seam guarantees, and the failures that are **silent
in the UI**.

The console side (where the buttons live, how a control is decided, the confirm dialogs) is documented
in [`services/webservice/README.md`](../services/webservice/README.md#the-write-surface). The two
machine surfaces are documented in [`plugins/restapi/API.md`](../plugins/restapi/API.md).

---

## 1. The registry

| Piece | Where | What it is |
|---|---|---|
| `action` | `core/actions.py:54` | The decorator: `@action` (a write) or `@action(kind="read")`. It registers the function and returns it unchanged; any other `kind` raises `ValueError` at import. |
| `_ACTIONS` | `core/actions.py:46` | `dict[str, Callable]`, keyed on **`fn.__qualname__`** (`core/actions.py:83`) — never on `fn.__name__`. |
| `_ACTION_KINDS` | `core/actions.py:51` | `qualname` → `read`/`write`, written by the same decorator; a separate map so `action_registry()` keeps returning `dict[str, Callable]`. |
| `kind_of` | `core/actions.py:90` | The kind an action declares. A name that is not registered answers `write` — the fail-safe direction, because a write served as a read would skip both the level floor and the audit. |
| `action_registry` | `core/actions.py:102` | Returns a **copy** of the registry. |
| `discover_actions` | `core/actions.py:123` | Imports `plugins.<name>.actions` for each plugin name, which is what **runs the decorators**, then returns the registry. |

**Importing the module is the registration.** There is no hard-coded list of actions anywhere; a
plugin's actions exist in a process only if that process imported its `actions.py`.

### What is registered today

| plugin module | write | read | total |
|---|---|---|---|
| `admin` | 8 | 0 | 8 |
| `mission` | 29 | 14 | 43 |
| `modmanager` | 4 | 4 | 8 |
| `monitoring` | 0 | 3 | 3 |
| `music` | 9 | 4 | 13 |
| `punishment` | 2 | 0 | 2 |
| `scheduler` | 5 | 2 | 7 |
| **all** | **57** | **27** | **84** |

Seven plugin modules declare every one of the 84 actions, and `plugins/mission/actions.py` holds 43
of them. The counts above are asserted against the live registry by
`tests/test_docs_action_counts.py`: a new `@action` breaks that test instead of drifting away from
this table.

Four places populate the registry:

* `core/plugin_manager.py:76` (`_discover_actions`, called from `load_plugins` at `:73`) — the
  authoritative population: the bot's own plugin load, reached by the real and the headless bot alike,
  so Discord, REST and MCP all read a filled registry. It logs the counts at INFO
  (`core/plugin_manager.py:108`).
* `services/webservice/shell.py:200` (`_discover_actions`, called at `:155`) — the admin console's
  fallback for a shell installed without a plugin load (see §8).
* `services/mcpservice/service.py:111` — the MCP service, when it is mounted into a process whose
  plugin load did not run.
* `plugins/restapi/api_v1.py:338` — the same fallback for `/api/v1`.

> **Naming.** The MCP tool *name* and the registry *key* are the same string: a tool is built from a
> row, and the row names its action by qualname (`services/mcpservice/tools.py:91`,
> `core/action_surface.py:561`). One vocabulary, so a tool cannot dispatch on a name the registry
> does not hold. A `/api/v1` route is named `v1_<qualname>` (`core/action_surface.py:737`).

---

## 2. `ActionContext` and its four constructors

`ActionContext` (`core/actions.py:637`) is the shared context one action function receives. It
carries the server resolver (`node`, `bus`) plus an **identity**: the caller's role names, its
per-server scope, and the audit's actor.

| Constructor | Where | Used by | Audit actor |
|---|---|---|---|
| `from_interaction` | `core/actions.py:676` | a Discord interaction | the member's display name (`_audit_user`) |
| `from_service` | `core/actions.py:694` | a `Service` — the MCP service | `[mcp:key/<label>]` when the caller passes `actor=`, else `"MCP"` |
| `from_plugin` | `core/actions.py:712` | a plugin instance — the legacy REST surface | `"API"` |
| `from_web` | `core/actions.py:723` | a request of the admin console | `[web:<backend>/<subject>]` |

`from_web` takes an `identity` that is **duck-typed and never imported**: `core` may not import a
service package (`tests/test_layering_direction.py`), so the contract is the four attributes the
console's principal already carries — `roles`, `scope`, `shown_name`, and `backend`/`subject` for the
audit. `node` and `bus` are the process's own; the console passes its scoped cluster as `bus`
(`services/webservice/pages/actions.py:9831`, built from `_Cluster` at `:9973`), because the seam only
ever reads `bus.servers`.

`from_service(service, *, actor=None)` also takes the caller's actor token: the MCP service passes
the verified key's `[mcp:key/<label>]` (`services/mcpservice/tools.py:60`), so the trail names the
client and not the transport. `None` keeps the transport label `"MCP"`.

`resolve_server(name)` (`core/actions.py:814`) stays the plain, transport-agnostic lookup: exact
match, then case-insensitive, then `display_name`. It carries **no** permission step — scope is a
separate method, below.

---

## 3. The web seam, end to end

A console write is a `POST` (never a state-changing `GET`) and travels this path. Steps 1–3 are the
app-level gate chain and run **before** any handler; the order is load-bearing.

```
POST /actions/<…>            (route registered by services/webservice/pages/actions.py:5852)
  ├─ 1. capability_gate        services/webservice/permissions.py:1057 → 403 if the capability is not held
  ├─ 2. csrf_protect           services/webservice/session.py:322     → 403 if the CSRF token is missing/wrong
  ├─ 3. AuthManager.authenticate                    → the signed-in Identity, or a refusal (never a 500)
  ├─ 4. ActionContext.from_web identity, node, bus  → the ctx (roles, scope, audit actor)
  ├─ 5. ctx.resolve_scoped_server(name)             → FOUND / NOT_FOUND / NOT_PERMITTED  (§4)
  ├─ 6. call_action(qualname, ctx, **params)        → the action function, guarded (§5)
  ├─ 7. ActionResult                                → core/action_results.py
  ├─ 8. audit                                       → recorded by the action, or by the seam (§6)
  └─ 9. 303 redirect + one-shot notice              → the page the control lives on
```

Authorization is **not** re-implemented in the route or in the action: the route declares its
capability and the gate refuses; the control is rendered from the same predicate the gate runs
(`permissions.allows`, `services/webservice/permissions.py:500`), so "offered ⊆ authorised" holds by
construction.

---

## 4. `resolve_scoped_server` — three answers, not two

`ActionContext.resolve_scoped_server(name)` (`core/actions.py:876`) returns a `ServerResolution`
(`:156`) whose `status` is one of:

| status | meaning | what the caller owes |
|---|---|---|
| `FOUND` | the name resolves **and** the server is in the caller's scope | run the action |
| `NOT_FOUND` | the caller is **unscoped** (sees everything) and the name does not exist | the action's own typed refusal (`ServerResolution.message`, `:187`, rendered inline) |
| `NOT_PERMITTED` | **every other caller** — restricted scope, unresolvable scope, or no scope at all — for anything it cannot see | the route's own refusal (the console answers a bare 403) |

**Why the last two are different answers, not one.** For a caller whose view is restricted, a name
that does not exist and a name outside its scope must produce the **same** answer, or a hoster could
enumerate the fleet by probing names. So a scoped caller gets `NOT_PERMITTED` for both. An unscoped
caller (Admin, break-glass, a local account that declares no scope) gets the honest `NOT_FOUND`,
because there is nothing to hide. The route applies the scope again on every request; the confirm
page and the rendered control are warnings, never locks.

`_scope_allows` (`core/actions.py:907`) fails **closed**: a missing scope, a scope that raises, or
one that cannot be applied all deny.

---

## 5. `call_action` and `read_action` — what they guarantee

`call_action(qualname, ctx, /, *, audit_result=False, **params)` (`core/actions.py:395`) is the only
way a write reaches an action, from any transport. Four guarantees, each a failure mode the surfaces
must not have:

* **an absent action** is a typed `ActionResult` refusal — never a 500 and never a silent success
  (`:429`, `code=action_unavailable`, `"Action '<qualname>' is not available in this installation."`).
  Availability is answered separately by `action_available(qualname)` (`:385`), which the console
  uses to **omit** a control whose action is not registered.
* **a second concurrent call on the same target** is refused with a typed refusal, not queued
  (`:436`, `code=target_busy`). See the in-flight guard, §7.
* **an action that raises** is typed as well (`:447`, `code=action_failed`): the action functions
  catch their own domain errors and return a result; anything escaping them is a bug and still
  answers with a result rather than a stack trace.
* **a missing audit** is visible in the log: if the returned result carries no `data["audit"]`
  marker, the seam logs a **WARNING** (`:459`) — unless the caller passed `audit_result=True`, in
  which case the seam writes the entry itself (`:456`). The flag exists for an action this console
  may not modify (`pause_mission` / `unpause_mission`); the seam's own refusals (absent action,
  in-flight target) ran no action and are not warned about.

`read_action(qualname, ctx, /, **params)` (`core/actions.py:465`) is the **unguarded twin**, and it
keeps only the typing: an absent action is `action_unavailable`, a raised action is `action_failed`.
It takes **no** in-flight guard (a page rendering while a power action runs must still read that
server) and **no** missing-audit warning (a read changes nothing and has no trail to miss). A
transport chooses between the two by the action's kind, never by the row's own risk claim
(`plugins/restapi/api_v1.py:288`).

---

## 6. The audit

The trail is the core `audit` table plus the audit channel, written by `bot.audit()`. The **action**
calls `audit_action(ctx, result)` (`core/actions.py:595`) once, whatever the transport, so one
operation audits the same way from Discord and from the web. A refused action is audited too (one
entry per attempt).

* `audit_action` sets `result.data["audit"]` to `AUDIT_RECORDED` or `AUDIT_NOT_RECORDED`
  (`core/actions.py:521-522`). A failed or unconfirmable audit is **reported, never silent**, and
  never changes the operation's outcome — a success stays a success.
* The **actor** is the context's `audit_actor` (`:803`): the declared `[web:<backend>/<subject>]`
  token for a web caller, the `[api:key/<label>]` / `[mcp:key/<label>]` token for a machine caller,
  the plain transport label (`"API"`, `"MCP"`) for a caller that names none, and the display name for
  Discord. A web caller is **never** recorded by display name into `ucid` and never as a ucid. The
  token sits in the entry's own `actor` column, so clipping the event text cannot lose it.
* With the **headless** bot (`services.bot.dummy.bot`), `audit()` is a no-op that returns normally,
  so "recorded" cannot be confirmed by the call returning: `can_record_audit` (`:574`) answers False
  and the outcome is `AUDIT_NOT_RECORDED` with a `log.error` (`:619`).
* The event text is **bounded** to `AUDIT_EVENT_MAX_CHARS` = 500 (`:533`) with a visible truncation
  marker, because a refusal can quote a caller-supplied name (`Server '<name>' not found.`).

---

## 7. The in-flight guard

`_in_flight: set[str]` (`core/actions.py:307`) is a **module-level set**, i.e. **per process**. A call
normalises its target once (`_normalise_target`, `:354`): the parameter is stripped, written back into
`params`, and the guard key is that value **case-folded** (so `SRS-1` and `srs-1` are one target,
because `resolve_server` matches case-insensitively). While a write runs, its target key is held; a
second call on the same target is refused with `"Another action on <target> is still running. Wait and
retry."` (`:438`). `reset_in_flight()` (`:319`) clears it for a rebuilt application and for tests.

Because the guard is per process, it serialises **within one process only** — a deployment with
several worker processes, or several nodes, has one guard each. `read_action` never touches it.

---

## 8. Deployment, and the two silent failures

**Actions exist only in a process where the plugin's `actions.py` is importable.** That is the whole
deployment story:

* An install that syncs the console's `services/webservice/` but not the plugin's `actions.py` has an
  empty (or partial) registry. Every control whose action is missing is **omitted**, and the UI shows
  **no error** — the list table's chip simply reads `read-only`
  (`services/webservice/templates/_table.html:69`). The in-band signal is the bot's own start line
  (`core/plugin_manager.py:108`):
  `PluginManager: action registry populated from <N> plugin(s) - <M> action(s) available in this
  process.` A count of `0 action(s)` is the fault. The console repeats the fact at DEBUG with the
  whole name list (`services/webservice/shell.py:246`).

### Trap 1 — an empty registry looks exactly like a permission problem

The list table prints a chip built from the control DATA a page was handed, not from the caller's
roles: **`actions`** when the page built row controls, **`read-only`** when it built none
(`services/webservice/templates/_table.html:69`). So "I am Admin and it says `read-only`" means *the
console found no action to offer*, not "you are not allowed". Check the registry log line above before
suspecting permissions.

### Trap 2 — a broken `actions.py` import must not look like "this plugin has no actions"

`discover_actions` (`core/actions.py:123`) distinguishes the two cases, because they owe opposite
answers:

* **the actions module is genuinely absent** — `plugins.<name>.actions` (or the `plugins.<name>`
  package) is not there — is **ordinary** and stays quiet: `_actions_module_is_absent`
  (`core/actions.py:107`) reads `ModuleNotFoundError.name` and the loop `continue`s silently
  (`:142`).
* **the module exists and fails to import** — e.g. it imports a library that is not installed — is a
  **failure**, logged at **WARNING** with the plugin name and the traceback (`:143`):
  `Action discovery: the actions module of plugin '<name>' (plugins.<name>.actions) exists but could
  not be imported - its actions are NOT registered and no console control will be offered for them.
  The remaining plugins are unaffected.`
* any other exception raised while importing the module is logged at **WARNING** the same way
  (`:148`).

One broken plugin never costs the others their discovery: the failure is per plugin and the loop
continues. The console's own install wraps this so that **no discovery failure is fatal**
(`services/webservice/shell.py:200`): a console that cannot read the plugin list, or whose action
layer cannot be imported, still installs and serves its pages — it simply offers no control, and a
crafted POST gets the seam's typed "not available" refusal (`core/actions.py:429`). Those failures are
logged loudly (`services/webservice/shell.py:223`, `:235`, `:241`).

---

## 9. Where the console's controls come from

`services/webservice/pages/actions.py` declares the write surface as data — `SERVER_ACTIONS`
(`:1068`) and `PLAYER_ACTIONS` (`:1683`) — and reads it four ways: the route table (`add_routes`,
`:5852`), the capability map (`capabilities`, `:2591`), the page's control data (`server_controls`
`:3026`, `player_controls` `:4169`, `controls_for` `:3969`), and the confirm dialogs. No file outside
`services/webservice/` is changed to add a control. The declaration and what it drives are documented
in `services/webservice/README.md`.
