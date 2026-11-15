"""``Install.rename_node`` rewrites a node name in every table, new-in-SET / old-in-WHERE.

The migration is a no-op-free rewrite: for EVERY ``UPDATE`` the method issues, the NEW name must
land in the ``SET`` position and the OLD name in the ``WHERE`` position. A test that only asserted
the statement text would stay green on a reversed parameter tuple, so this one records the
``(statement, parameters)`` pair the connection is called with and pins the ORDER — the bug this
guards was exactly two tuples passed the wrong way round
(``UPDATE nodestats SET node = %s WHERE node = %s`` with ``(old, new)``), a silent no-op.

The database half runs against a fake connection. No live PostgreSQL is exercised here.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


OLD = "OLDNODE"
NEW = "NEWNODE"


class RecordingConn:
    """A psycopg-shaped connection that records every ``execute`` call verbatim."""

    def __init__(self):
        self.calls = []          # list of (query, params)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self.calls.append((query, params))
        return None


def _load_install_module():
    spec = importlib.util.spec_from_file_location('install_rename_mod', REPO_ROOT / 'install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config_dir(tmp_path: Path) -> Path:
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'main.yaml').write_text(
        "database:\n  url: postgresql://dcssb:SECRET@127.0.0.1:5432/dcssb\n", encoding='utf-8')
    (config / 'nodes.yaml').write_text("{}\n", encoding='utf-8')
    return config


def _run_rename(monkeypatch, tmp_path):
    module = _load_install_module()
    module._ = lambda s: s                      # the translator Install.install would install
    monkeypatch.setattr(module.utils, 'get_password', lambda *a, **k: 'pw')
    conn = RecordingConn()
    monkeypatch.setattr(module.psycopg, 'connect', lambda url, **kw: conn)
    module.Install.rename_node(str(_config_dir(tmp_path)), OLD, NEW, database_only=True)
    return conn


def _rewrites(conn):
    """Yield ``(set_name, where_name)`` for every ``UPDATE`` the connection saw.

    A plain statement carries its values in the parameter tuple (SET first, WHERE second); the
    ``serverstats`` rewrite is a ``sql.Composed`` with the values inlined as ``Literal``s, so both
    forms are unwrapped to the same ``(set, where)`` pair.
    """
    found = []
    for query, params in conn.calls:
        if isinstance(query, str):
            if query.strip().startswith('UPDATE'):
                found.append((query, params[0], params[1]))
        else:
            parts = getattr(query, '_obj', None)            # psycopg sql.Composed
            if not parts:
                continue
            head = str(parts[0])
            if 'UPDATE' in head:
                literals = [p._obj for p in parts if type(p).__name__ == 'Literal']
                found.append((head, literals[0], literals[1]))
    return found


def test_every_update_sets_new_where_old(monkeypatch, tmp_path):
    conn = _run_rename(monkeypatch, tmp_path)
    rewrites = _rewrites(conn)
    # nodestats + audit (parameterised) + serverstats (inlined): all three must be present.
    assert len(rewrites) == 3, f'expected 3 UPDATE rewrites, got {rewrites!r}'
    for statement, set_name, where_name in rewrites:
        assert set_name == NEW, f'SET position holds {set_name!r}, not the new name: {statement!r}'
        assert where_name == OLD, f'WHERE position holds {where_name!r}, not the old name: {statement!r}'


def test_nodestats_update_parameters_are_in_order(monkeypatch, tmp_path):
    conn = _run_rename(monkeypatch, tmp_path)
    calls = [(q, p) for q, p in conn.calls
             if isinstance(q, str) and 'UPDATE nodestats' in q]
    assert calls == [("UPDATE nodestats SET node = %s WHERE node = %s", (NEW, OLD))]


def test_audit_update_parameters_are_in_order(monkeypatch, tmp_path):
    conn = _run_rename(monkeypatch, tmp_path)
    calls = [(q, p) for q, p in conn.calls
             if isinstance(q, str) and 'UPDATE audit' in q]
    assert calls == [("UPDATE audit SET node = %s WHERE node = %s", (NEW, OLD))]


def test_instances_row_is_deleted_by_the_old_name(monkeypatch, tmp_path):
    conn = _run_rename(monkeypatch, tmp_path)
    calls = [(q, p) for q, p in conn.calls
             if isinstance(q, str) and q.startswith('DELETE FROM instances')]
    assert calls == [("DELETE FROM instances WHERE node = %s", (OLD,))]
