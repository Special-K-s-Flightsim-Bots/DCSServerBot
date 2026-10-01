# The action layer (`core/actions.py`)

An **action** is a transport-agnostic domain function that lives in a plugin's `actions.py` and is
decorated with `@action`. The same function is reached from Discord commands, from the MCP service,
from the REST surface, and — through `ActionContext.from_web` — from the admin console's write
controls. This document is the map of that layer: how an action is registered, how a console POST
reaches one, what the seam guarantees, and the two failures that are **silent in the UI**.

The console side (where the buttons live, how a control is decided, the confirm dialogs) is documented
in [`services/webservice/README.md`](../services/webservice/README.md#the-write-surface).

---

## 1. The registry

| Piece | Where | What it is |
|---|---|---|
| `action` | `core/actions.py:31` | The decorator. `@action` on a function in a plugin's `actions.py` registers it and returns the function unchanged. |
| the registry | `core/actions.py:28`, `:47` | `_ACTIONS: dict[str, Callable]`, keyed on **`fn.__qualname__`** (`:47`) — never on `fn.__name__`. |
| `action_registry` | `core/actions.py:51` | Returns a **copy** of the registry. |
| `discover_actions` | `core/actions.py:72` | Imports `plugins.<name>.actions` for each plugin name, which is what **runs the decorators**, then returns the registry. |

**Importing the module is the registration.** There is no hard-coded list of actions anywhere; a
plugin's actions exist in a process only if that process imported its `actions.py`.

The registry is a **bot-wide mechanism** — Discord commands, the REST surface and the MCP service all
read it — so it is populated where the **plugins load**, by `PluginManager._discover_actions()`
(`core/plugin_manager.py`, called from `load_plugins()`) — this is the AUTHORITATIVE call, reached by
the real bot (`services/bot/dcsserverbot.py`) and the headless bot (`services/bot/dummy/bot.py`)
alike, and re-run whenever the plugins load again (a master/agent switch, a reload). It is
idempotent (`importlib` caches each module and `@action` keys on `__qualname__`) and never fatal.

Two FALLBACK callers populate the registry for a process that runs WITHOUT that plugin load (a stub
install in a test, or a service whose plugin list never reached `load_plugins`):

* `services/mcpservice/server.py:48` — the MCP service calls `discover_actions` and wraps each
  function for FastMCP's stdio transport (`:55`).
* `services/webservice/shell.py:130` (`_discover_actions`, called at `:109`) — the admin console
  calls it too, because otherwise the web process would find an **empty** registry and omit every
  control (see §8).

The only `@action` users in the tree today are the **17** action functions in
`plugins/mission/actions.py` (`start_server` `:31`, `stop_server` `:69`, `restart_server` `:112`,
`startup_server` `:168`, `shutdown_server` `:212`, `pause_mission` `:258`, `unpause_mission` `:296`,
`message_player` `:545`, `kick_player` `:720`, `ban_player` `:772`, `mute_player` `:883`,
`unmute_player` `:889`, plus `restart_mission` `:334`, `rotate_mission` `:385`, `load_mission` `:430`,
`list_missions` `:897` and `get_mission_info` `:942`).

> **Naming hazard.** The MCP tool *name* is `fn.__name__` (`services/mcpservice/server.py:120`); the
> registry *key* is `fn.__qualname__` (`core/actions.py:44`). These are not the same vocabulary —
> `__name__` collides across plugins, `__qualname__` does not. A dispatcher (the console's
> `call_action`) must key on `qualname`.

---

## 2. `ActionContext` and its four constructors

`ActionContext` (`core/actions.py:417`) is the shared context one action function receives. It
carries the server resolver (`node`, `bus`) plus, since the web seam landed, an **identity**: the
caller's role names, its per-server scope, and the audit's actor.

| Constructor | Where | Used by | Audit actor |
|---|---|---|---|
| `from_interaction(interaction)` | `core/actions.py:450` | a Discord interaction | the member's display name (`_audit_user`) |
| `from_service(service)` | `core/actions.py:467` | a `Service` — the MCP service | `"MCP"` |
| `from_plugin(plugin)` | `core/actions.py:477` | a plugin instance — the REST surface | `"API"` |
| `from_web(identity, node, bus)` | `core/actions.py:487` | a request of the admin console | `[web:<backend>/<subject>]` |

`from_web` takes an `identity` that is **duck-typed and never imported**: `core` may not import a
service package (`tests/test_layering_direction.py`), so the contract is the four attributes the
console's principal already carries — `roles`, `scope`, `shown_name`, and `backend`/`subject` for the
audit. `node` and `bus` are the process's own; the console passes its scoped cluster as `bus`
(`services/webservice/pages/actions.py:1213`, `_Cluster` at `:1261`), because the seam only ever reads
`bus.servers`.

`resolve_server(name)` (`core/actions.py:527`) stays the plain, transport-agnostic lookup: exact
match, then case-insensitive, then `display_name`. It carries **no** permission step — scope is a
separate method, below.

---

## 3. The web seam, end to end

A console write is a `POST` (never a state-changing `GET`) and travels this path. Steps 1–3 are the
app-level gate chain and run **before** any handler; the order is load-bearing.

```
POST /actions/<…>                       (route registered by services/webservice/pages/actions.py:930)
  ├─ 1. capability_gate        permissions.py:693   → 403 if the route's capability is not held
  ├─ 2. csrf_protect           session.py:300       → 403 if the session's CSRF token is missing/wrong
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
(`permissions.allows`, `permissions.py:146`), so "offered ⊆ authorised" holds by construction.

---

## 4. `resolve_scoped_server` — three answers, not two

`ActionContext.resolve_scoped_server(name)` (`core/actions.py:542`) returns a `ServerResolution`
(`:101`) whose `status` is one of:

| status | meaning | what the caller owes |
|---|---|---|
| `FOUND` | the name resolves **and** the server is in the caller's scope | run the action |
| `NOT_FOUND` | the caller is **unscoped** (sees everything) and the name does not exist | the action's own typed refusal (`ServerResolution.message`, `:133`, rendered inline) |
| `NOT_PERMITTED` | **every other caller** — restricted scope, unresolvable scope, or no scope at all — for anything it cannot see | the route's own refusal (the console answers a bare 403) |

**Why the last two are different answers, not one.** For a caller whose view is restricted, a name
that does not exist and a name outside its scope must produce the **same** answer, or a hoster could
enumerate the fleet by probing names (the design of record, `WRITE-ACTIONS-DESIGN.md` §2.4, kept
outside this repo). So a scoped caller gets
`NOT_PERMITTED` for both. An unscoped caller (Admin, break-glass, a local account that declares no
scope) gets the honest `NOT_FOUND`, because there is nothing to hide. The route applies the scope
again on every request; the confirm page and the rendered control are warnings, never locks.

`_scope_allows` (`:573`) fails **closed**: a missing scope, a scope that raises, or one that cannot be
applied all deny.

---

## 5. `call_action` — what it guarantees

`call_action(qualname, ctx, /, *, audit_result=False, **params)` (`core/actions.py:216`) is the only
way a console write reaches an action. Four guarantees, each a failure mode the console must not have:

* **an absent action** is a typed `ActionResult` refusal — never a 500 and never a silent success
  (`:252`, `"Action '<qualname>' is not available in this installation."`). Availability is answered
  separately by `action_available(qualname)` (`:206`), which the console uses to **omit** a control
  whose action is not registered.
* **a second concurrent call on the same target** is refused with a typed refusal, not queued
  (`:256`). See the in-flight guard, §7.
* **an action that raises** is typed as well (`:264`): the action functions catch their own domain
  errors and return a result; anything escaping them is a bug and still answers with a result rather
  than a stack trace.
* **a missing audit** is visible in the log: if the returned result carries no `data["audit"]`
  marker, the seam logs a **WARNING** (`:277`) — unless the caller passed `audit_result=True`, in
  which case the seam writes the entry itself (`:274`). The flag exists only for actions this console
  may not yet modify (`pause_mission` / `unpause_mission`); the seam's own refusals (absent action,
  in-flight target) ran no action and are not warned about.

---

## 6. The audit

The trail is the core `audit` table plus the audit channel, written by `bot.audit()`. The **action**
calls `audit_action(ctx, result)` (`core/actions.py:376`) once, whatever the transport, so one
operation audits the same way from Discord and from the web. A refused action is audited too (one
entry per attempt).

* `audit_action` sets `result.data["audit"]` to `AUDIT_RECORDED` or `AUDIT_NOT_RECORDED`
  (`core/actions.py:305-306`). A failed or unconfirmable audit is **reported, never silent**, and
  never changes the operation's outcome — a success stays a success.
* The **actor** is the context's `audit_actor` (`:516`): the declared `[web:<backend>/<subject>]`
  prefix for a web caller (greppable with `WHERE event LIKE '[web:%'`), the display-name label
  otherwise. A web caller is **never** recorded by display name into `ucid` and never as a ucid.
* With the **headless** bot (`services.bot.dummy.bot`), `audit()` is a no-op that returns normally,
  so "recorded" cannot be confirmed by the call returning: `_can_confirm_audit` (`:357`) answers
  False and the outcome is `AUDIT_NOT_RECORDED` with a `log.error` (`:400`).
* The event text is **bounded** to `AUDIT_EVENT_MAX_CHARS` = 500 (`:317`) with a visible truncation
  marker, because a refusal can quote a caller-supplied name (`Server '<name>' not found.`).

---

## 7. The in-flight guard

`_in_flight: set[str]` (`core/actions.py:169`) is a **module-level set**, i.e. **per process**. A call
normalises its target once (`_normalise_target`, `:182`): the parameter is stripped, written back into
`params`, and the guard key is that value **case-folded** (so `SRS-1` and `srs-1` are one target,
because `resolve_server` matches case-insensitively). While an action runs, its target key is held; a
second call on the same target is refused with `"Another action on <target> is still running. Wait and
retry."` (`:256`). `reset_in_flight()` (`:177`) clears it for a rebuilt application and for tests.

Because the guard is per process, it serialises **within one process only** — a deployment with
several worker processes, or several nodes, has one guard each.

---

## 8. Deployment, and the two silent failures

**Actions exist only in a process where the plugin's `actions.py` is importable.** That is the whole
deployment story:

* An install that syncs the console's `services/webservice/` but not the plugin's `actions.py` has an
  empty (or partial) registry. Every control whose action is missing is **omitted**, and the UI shows
  **no error** — the list table's chip simply reads `read-only`
  (`services/webservice/templates/_table.html:35`). The only in-band signal is the console's own log
  line at install (`services/webservice/shell.py:173`):
  `Admin web shell: action registry populated from <N> plugin(s) - <M> action(s) available in this
  process: <names>.` A count of `0 action(s)` is the fault.

### Trap 1 — an empty registry looks exactly like a permission problem

The list table prints a chip built from the control DATA a page was handed, not from the caller's
roles: **`actions`** when the page built row controls, **`read-only`** when it built none
(`services/webservice/templates/_table.html:35`). So "I am Admin and it says `read-only`" means *the
console found no action to offer*, not "you are not allowed". Check the registry log line above before
suspecting permissions.

### Trap 2 — a broken `actions.py` import must not look like "this plugin has no actions"

`discover_actions` (`core/actions.py:69`) distinguishes the two cases, because they owe opposite
answers:

* **the actions module is genuinely absent** — `plugins.<name>.actions` (or the `plugins.<name>`
  package) is not there — is **ordinary** and stays quiet: `_actions_module_is_absent` (`:53`) reads
  `ModuleNotFoundError.name` and the loop `continue`s silently (`:88`).
* **the module exists and fails to import** — e.g. it imports a library that is not installed — is a
  **failure**, logged at **WARNING** with the plugin name and the traceback (`:89`):
  `Action discovery: the actions module of plugin '<name>' (plugins.<name>.actions) exists but could
  not be imported - its actions are NOT registered and no console control will be offered for them.
  The remaining plugins are unaffected.`
* any other exception raised while importing the module is logged at **WARNING** the same way (`:94`).

One broken plugin never costs the others their discovery: the failure is per plugin and the loop
continues. The console's own install wraps this so that **no discovery failure is fatal**
(`services/webservice/shell.py:146`): a console that cannot read the plugin list, or whose action
layer cannot be imported, still installs and serves its pages — it simply offers no control, and a
crafted POST gets the seam's typed "not available" refusal (`core/actions.py:252`). Those failures are
logged loudly (`services/webservice/shell.py:153`, `:165`).

---

## 9. Where the console's controls come from

`services/webservice/pages/actions.py` declares the write surface as data — `SERVER_ACTIONS` (`:215`)
and `PLAYER_ACTIONS` (`:354`) — and reads it four ways: the route table (`add_routes`, `:930`), the
capability map (`capabilities`, `:431`), the page's control data (`server_controls` `:470`,
`player_controls` `:586`, `controls_for` `:547`), and the confirm dialogs. No file outside
`services/webservice/` is changed to add a control. The declaration and what it drives are documented
in `services/webservice/README.md`.
