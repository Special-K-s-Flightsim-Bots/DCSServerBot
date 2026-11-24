"""The bot's own rename chain must not steal or destroy a server's identity.

Exercises the hole beneath the config action's collision refusal: ``/server rename`` (``commands.py``)
and the config view's Save (``views.py``) reach ``Server.rename`` directly, where a colliding target
name was silently skipped, the ``servers.yaml`` key move could be dropped, and an exception was
swallowed into a false success; ``ServiceBus.rename_server`` overwrote ``bus.servers[new]`` so the
other server vanished from the fleet.

The real ``ServerImpl`` and ``ServiceBus`` are driven (built without their heavy constructors) with
doubles only where the code reaches a node, a pool or an extension. On a refusal nothing is written
anywhere -- ``servers.yaml``, the database, the registry and the extension hooks are untouched -- and
the refusal is typed (``RenameError``)."""
from __future__ import annotations

import logging
import types

import pytest
from ruamel.yaml import YAML

from core.data.impl.serverimpl import ServerImpl, RenameError
from services.servicebus.service import ServiceBus

OLD = "SRS-1"
OTHER = "SRS-2"


# -------------------------------------------------------------------------------------- doubles

class _Conn:
    """The slice of a psycopg connection the rename touches: one ``execute`` that records."""

    def __init__(self, log):
        self._log = log

    async def execute(self, sql, params=None):
        self._log.append((" ".join(sql.split()), params))


class _Pool:
    """``apool`` with a recording connection; ``fail`` makes the FIRST execute raise (the swallow test)."""

    def __init__(self, log, fail=False):
        self._log = log
        self._fail = fail

    def connection(self):
        return _Ctx(self._log, self._fail)


class _Ctx:
    def __init__(self, log, fail):
        self._log = log
        self._fail = fail

    async def __aenter__(self):
        conn = _Conn(self._log)
        if self._fail:
            async def boom(sql, params=None):
                raise RuntimeError("database is down")
            conn.execute = boom
        return conn

    async def __aexit__(self, *exc):
        return False


class _Bus:
    """A registry double: ``servers`` is the mapping, ``rename_server`` performs the real re-key.

    It mirrors the real ``ServiceBus.rename_server``'s old-key handling: the OLD key is dropped only
    when it differs, so a same-name call keeps its entry instead of deleting it.
    """

    def __init__(self, servers=None):
        self.servers = dict(servers or {})

    def rename_server(self, server, new_name):
        self.servers[new_name] = server
        if server.name != new_name:
            self.servers.pop(server.name, None)


class _Node:
    """A master node double: the re-key the cluster rename performs, plus the RPC it records."""

    def __init__(self, config_dir, bus, *, master=True):
        self.config_dir = str(config_dir)
        self.bus = bus
        self.master = master
        self.rename_calls: list[tuple] = []

    async def rename_server(self, server, new_name):
        self.rename_calls.append((server.name, new_name))
        self.bus.rename_server(server, new_name)


class _Extension:
    def __init__(self):
        self.renamed: list[tuple] = []

    def rename_server(self, old_name, new_name):
        self.renamed.append((old_name, new_name))


def write_servers_yaml(tmp_path, mapping):
    from pathlib import Path

    path = Path(tmp_path) / "servers.yaml"
    with path.open("w", encoding="utf-8") as f:
        YAML().dump(mapping, f)
    return path


def make_server(name, tmp_path, *, servers_yaml=None, bus=None, extensions=None, settings=None,
                instance_name="inst", failing_db=False):
    """A real ``ServerImpl`` with only the attributes ``rename`` reads, and nothing else."""
    if servers_yaml is not None:
        write_servers_yaml(tmp_path, servers_yaml)
    db_log: list[tuple] = []
    srv = object.__new__(ServerImpl)
    srv.name = name
    srv.bus = bus if bus is not None else _Bus()
    srv.node = _Node(tmp_path, srv.bus)
    srv.log = logging.getLogger("tests.rename")
    srv.apool = _Pool(db_log, fail=failing_db)
 # ``settings`` is a read-only property backed by ``_settings``; seed it NON-empty so the getter
 # returns it as-is instead of trying to load a SettingsDict off a (absent) instance home.
    srv._settings = dict(settings) if settings else {"name": name}
    srv.extensions = extensions if extensions is not None else {}
    srv._instance = types.SimpleNamespace(name=instance_name)
    srv.db_log = db_log
    return srv


def read_yaml(tmp_path):
    from pathlib import Path

    return YAML().load((Path(tmp_path) / "servers.yaml").read_text(encoding="utf-8"))


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# a collision is refused, typed, before anything is written or re-keyed
@pytest.mark.asyncio
async def test_a_collision_with_a_live_server_is_refused_with_no_half_state(tmp_path):
    """Renaming onto a live entry would clobber it: the rename refuses before ANY write, and the other
    server keeps its entry, its yaml, its DB row and its hooks.
    """
    other = types.SimpleNamespace(name=OTHER)
    bus = _Bus({OLD: None, OTHER: other})
    ext = _Extension()
    server = make_server(OLD, tmp_path, servers_yaml={OLD: {"dcs_port": 10308}, OTHER: {"dcs_port": 10318}},
                         bus=bus, extensions={"SRS": ext})
    bus.servers[OLD] = server
    before = read_yaml(tmp_path)

    with pytest.raises(RenameError) as exc:
        await server.rename(OTHER, update_settings=True)

    assert OTHER in str(exc.value) and "already used" in str(exc.value), "the colliding server is named"
    assert server.name == OLD, "the object was not renamed"
    assert server.settings["name"] == OLD, "no settings write"
    assert server.db_log == [], "no database rename"
    assert bus.servers[OTHER] is other and bus.servers[OLD] is server, "both registry entries intact"
    assert ext.renamed == [], "no extension hook"
    assert read_yaml(tmp_path) == before, "servers.yaml is untouched"


@pytest.mark.asyncio
async def test_a_collision_with_a_yaml_only_entry_is_refused(tmp_path):
    """A ``servers.yaml`` key held by a DIFFERENT entry is a collision too (it would half-rename).

    Without this, ``update_config`` silently skips the key move (``:530-533``) while the DB and the
    registry ARE renamed — a half-renamed server. It is refused for the same reason as a live one.
    """
    bus = _Bus({OLD: None})
    server = make_server(OLD, tmp_path, servers_yaml={OLD: {}, OTHER: {}}, bus=bus)
    bus.servers[OLD] = server

    with pytest.raises(RenameError) as exc:
        await server.rename(OTHER)

    assert OTHER in str(exc.value) and "already used" in str(exc.value)
    assert server.db_log == [] and server.name == OLD
    assert set(read_yaml(tmp_path)) == {OLD, OTHER}, "servers.yaml untouched"


@pytest.mark.asyncio
async def test_a_collision_is_case_folded(tmp_path):
    """``srs-2`` and ``SRS-2`` name ONE server (matching the console's own guard)."""
    other = types.SimpleNamespace(name=OTHER)
    bus = _Bus({OLD: None, OTHER: other})
    server = make_server(OLD, tmp_path, servers_yaml={OLD: {}}, bus=bus)
    bus.servers[OLD] = server

    with pytest.raises(RenameError):
        await server.rename(OTHER.lower())

    assert bus.servers[OTHER] is other


@pytest.mark.asyncio
async def test_a_collision_against_the_server_itself_is_not_a_collision(tmp_path):
    """A same-name rename is the idempotent no-op it always was (``register_server`` hits it)."""
    bus = _Bus({OLD: None})
    server = make_server(OLD, tmp_path, servers_yaml={OLD: {}}, bus=bus)
    bus.servers[OLD] = server

    await server.rename(OLD)          # must not raise

    assert server.name == OLD
    assert bus.servers[OLD] is server


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# the reserved sentinel ``n/a`` and empty names are refused
@pytest.mark.parametrize("bad", ["n/a", "N/A", "", "   ", None, 42])
@pytest.mark.asyncio
async def test_the_reserved_sentinel_and_empty_names_are_refused(tmp_path, bad):
    """``n/a`` is the code's unnamed marker and renaming to it walked a no-rollback path; an empty name
    wrote an empty ``servers.yaml`` key. Both are a typed refusal before anything is written.
    """
    bus = _Bus({OLD: None})
    server = make_server(OLD, tmp_path, servers_yaml={OLD: {}}, bus=bus)
    bus.servers[OLD] = server

    with pytest.raises(RenameError) as exc:
        await server.rename(bad)

    message = str(exc.value).casefold()
    assert ("n/a" in message and "reserved" in message) or "empty" in message
    assert server.name == OLD and server.db_log == []
    assert set(read_yaml(tmp_path)) == {OLD}


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# a failed rename raises instead of reporting success
@pytest.mark.asyncio
async def test_a_failed_rename_raises_instead_of_swallowing(tmp_path):
    """The outer ``except`` used to log and ``return`` -- the caller saw SUCCESS. The failure is now
    re-raised, so ``/server rename`` cannot report a rename that never landed.
    """
    bus = _Bus({OLD: None})
    server = make_server(OLD, tmp_path, servers_yaml={OLD: {}}, bus=bus, failing_db=True)
    bus.servers[OLD] = server

    with pytest.raises(Exception) as exc:      # noqa: PT011 - the point is that SOMETHING raises
        await server.rename("SRS-9", update_settings=True)

    assert "SRS-9" in str(exc.value) or "database is down" in str(exc.value)
    assert server.name == OLD, "the name did not move, and the caller was told so by the raise"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# a legitimate rename still does all of it (no regression)
@pytest.mark.asyncio
async def test_a_legitimate_rename_rekeys_yaml_registry_db_and_hooks(tmp_path):
    """The behaviour that must NOT change: yaml key move, registry re-key, DB rename, extension hook."""
    new = "SRS-9"
    bus = _Bus({OLD: None})
    ext = _Extension()
    server = make_server(OLD, tmp_path, servers_yaml={OLD: {"dcs_port": 10308}}, bus=bus,
                         extensions={"SRS": ext}, settings={}, instance_name="inst-1")
    bus.servers[OLD] = server

    await server.rename(new, update_settings=True)

    assert server.name == new, "the object was renamed"
    assert server.settings["name"] == new, "the settings name moved with update_settings"
    data = read_yaml(tmp_path)
    assert new in data and OLD not in data, "the servers.yaml key moved"
    assert data[new] == {"dcs_port": 10308}, "the entry travelled"
    assert bus.servers[new] is server and OLD not in bus.servers, "the registry was re-keyed"
    assert server.db_log == [("UPDATE servers SET server_name = %s WHERE server_name = %s", (new, OLD))], \
        "the database was renamed exactly once, old -> new"
    assert ext.renamed == [(OLD, new)], "the extension hook fired with old -> new"
    assert server.node.rename_calls == [(OLD, new)], "the cluster rename ran"


@pytest.mark.asyncio
async def test_a_rename_still_works_when_the_entry_is_absent_from_servers_yaml(tmp_path):
    """The guard looks only at the TARGET; the rename must still work when the server's own
    ``servers.yaml`` entry is missing.
    """
    new = "SRS-9"
    bus = _Bus({OLD: None})
    ext = _Extension()
    server = make_server(OLD, tmp_path, servers_yaml={"somebody-else": {"dcs_port": 1}}, bus=bus,
                         extensions={"SRS": ext})
    bus.servers[OLD] = server

    await server.rename(new, update_settings=True)

    assert server.name == new
    assert server.db_log == [("UPDATE servers SET server_name = %s WHERE server_name = %s", (new, OLD))]
    assert bus.servers[new] is server and OLD not in bus.servers
    assert ext.renamed == [(OLD, new)]
    assert set(read_yaml(tmp_path)) == {"somebody-else"}, "no entry → servers.yaml is left untouched"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# the bus refuses to clobber a live registry entry, whatever the caller
def make_bus(servers):
    """A ``ServiceBus`` built without its heavy constructor, holding exactly ``servers``."""
    bus = object.__new__(ServiceBus)
    bus.servers = dict(servers)
    bus.udp_server = types.SimpleNamespace(message_queue={})
    return bus


def test_the_bus_refuses_to_clobber_a_live_entry():
    """``self.servers[new_name] = server`` used to overwrite the other entry and pop the old key. The bus
    now refuses, typed, and BOTH entries stay intact.
    """
    one = types.SimpleNamespace(name=OLD)
    two = types.SimpleNamespace(name=OTHER)
    bus = make_bus({OLD: one, OTHER: two})

    with pytest.raises(RenameError) as exc:
        bus.rename_server(one, OTHER)

    assert OTHER in str(exc.value) and "already used" in str(exc.value)
    assert bus.servers[OTHER] is two and bus.servers[OLD] is one


@pytest.mark.parametrize("bad", ["n/a", "", None])
def test_the_bus_refuses_a_reserved_or_empty_target(bad):
    one = types.SimpleNamespace(name=OLD)
    bus = make_bus({OLD: one})

    with pytest.raises(RenameError):
        bus.rename_server(one, bad)

    assert bus.servers == {OLD: one}


def test_the_bus_rekeys_a_legitimate_rename():
    new = "SRS-9"
    one = types.SimpleNamespace(name=OLD)
    bus = make_bus({OLD: one})

    bus.rename_server(one, new)

    assert bus.servers == {new: one}


def test_the_bus_allows_a_same_name_call():
    one = types.SimpleNamespace(name=OLD)
    bus = make_bus({OLD: one})

    bus.rename_server(one, OLD)          # must not raise (existing IS the server)

    assert bus.servers == {OLD: one}


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# the bus guard folds case too, so the RPC path cannot create a fold-duplicate
def fold_keys(registry, name):
    """Every key in ``registry`` that folds to ``name`` (asserting no fold-duplicate)."""
    return [key for key in registry if str(key).casefold() == name.casefold()]


def test_the_bus_refuses_a_case_variant_collision():
    """``self.servers.get(new_name)`` is an exact lookup, so a case-variant target (``srs-2`` against a
    live ``SRS-2``) was accepted and keyed a SECOND entry -- and on Windows/WSL the two entries'
    instance/home directories collide. The bus now applies the same folded rule the core guard
    (:meth:`ServerImpl._rename_refusal`) uses.
    """
    one = types.SimpleNamespace(name=OLD)
    two = types.SimpleNamespace(name=OTHER)
    bus = make_bus({OLD: one, OTHER: two})

    with pytest.raises(RenameError) as exc:
        bus.rename_server(one, OTHER.lower())

    assert OTHER in str(exc.value) and "already used" in str(exc.value), "the colliding server is named"
    assert bus.servers == {OLD: one, OTHER: two}, "both registry entries are intact"
    assert fold_keys(bus.servers, OTHER) == [OTHER], "no second fold-key appears"


def test_the_rpc_path_refuses_a_case_variant_rename():
    """``NodeImpl.rename_server`` -> ``ServiceBus.rename_server`` did an exact-match lookup, so a
    case-variant request re-keyed the master registry into fold-duplicates. The bus refuses, the RPC
    raises, and the registry keeps exactly one entry per fold.
    """
    import asyncio

    from core.data.impl.nodeimpl import NodeImpl
    from core.services.registry import ServiceRegistry

    one = types.SimpleNamespace(name=OLD, is_remote=True)
    two = types.SimpleNamespace(name=OTHER, is_remote=False)
    bus = make_bus({OLD: one, OTHER: two})
    node = object.__new__(NodeImpl)
    node._master = True                       # ``master`` is a property backed by ``_master``
    node.log = logging.getLogger("tests.rename.rpc")

    with ServiceRegistry._lock:
        ServiceRegistry._singletons[ServiceBus] = bus
    try:
        with pytest.raises(RenameError):
            asyncio.run(node.rename_server(one, OTHER.lower()))
    finally:
        with ServiceRegistry._lock:
            ServiceRegistry._singletons.pop(ServiceBus, None)

    assert bus.servers == {OLD: one, OTHER: two}, "the registry was not re-keyed"
    assert fold_keys(bus.servers, OTHER) == [OTHER], "no second fold-key appears"
    assert one.name == OLD, "the proxy name was not moved"
