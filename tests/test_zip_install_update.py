"""An installation that came from an ARCHIVE: what it updates from, and what it records.

The reported defects these pin:

* a failed dependency install recorded the update as SUCCESSFUL, so every later run answered
  "no update found" and the install could never repair itself;
* a refused GitHub check (rate limit, 5xx, proxy) printed the same "no update found" as an
  installation that is already current;
* the node's own upgrade gate asked for the newest RELEASE, so ``autoupdate`` and ``/node upgrade``
  did nothing for an archive install even while ``update.py`` would have updated it.

The rule they pin: an archive installation follows the MAIN branch, compares its tip with the commit
the last SUCCESSFUL update recorded (``version.sha``), and never reports a check it could not make as
"nothing to do".
"""
from __future__ import annotations

import importlib.util
import io
import logging
import subprocess
import types
import zipfile
from pathlib import Path

import pytest

from core.data.impl import nodeimpl
from core.data.impl.nodeimpl import NodeImpl
from core.utils.os import read_version_marker

REPO = Path(__file__).resolve().parents[1]

SHA_OLD = 'a' * 40
SHA_NEW = 'b' * 40


# --------------------------------------------------------------------------------------------- helpers

@pytest.fixture
def updater():
    """``update.py`` itself, loaded from the repository root as the script the launcher runs."""
    spec = importlib.util.spec_from_file_location('dcssb_update', REPO / 'update.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    """Just enough of ``requests.Response`` for the two things the updater reads."""

    def __init__(self, payload=None, content: bytes = b'', status_code: int = 200, reason: str = 'OK'):
        self._payload = payload
        self.content = content
        self.status_code = status_code
        self.reason = reason

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _router(routes: dict[str, _Response]):
    """A ``requests.get`` that answers by URL fragment and fails loudly on an unexpected URL."""
    def get(url, **kwargs):
        for fragment, response in routes.items():
            if fragment in url:
                return response
        raise AssertionError(f'unexpected URL: {url}')
    return get


def _archive(members: dict[str, str]) -> bytes:
    """A branch archive the way GitHub serves one: a single top-level folder."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, text in members.items():
            archive.writestr(f'Special-K-s-Flightsim-Bots-DCSServerBot-abcdef1/{name}', text)
    return buffer.getvalue()


def _install(tmp_path: Path) -> None:
    (tmp_path / 'version.py').write_text('__version__ = "3.0.5.3"\n', encoding='utf-8')


class _Node(NodeImpl):
    """The three attributes the update checks touch, with a logger ``caplog`` can read.

    Subclasses ``NodeImpl`` so the class-level monkeypatches below are visible through ``self``,
    and bypasses its ``__init__`` (which wants a config directory and a database).
    """

    def __init__(self):
        # ``proxy`` / ``proxy_auth`` are properties over ``locals`` on the real node, so the double
        # answers them the way an unconfigured node does instead of assigning to the properties.
        self.locals = {'proxy': {}, 'proxy_auth': None}
        self.log = logging.getLogger('test.zip_update')


# --------------------------------------------------------------------------- what update.py records

def test_a_failed_dependency_install_does_not_record_the_update(updater, tmp_path, monkeypatch, capsys):
    """REPORTED: a zip install answered "no update found" forever after one bad run."""
    monkeypatch.chdir(tmp_path)
    _install(tmp_path)
    archive = _archive({'version.py': '__version__ = "3.0.5.3"\n', 'new_file.txt': 'new'})
    monkeypatch.setattr(updater.requests, 'get', _router({
        '/releases': _Response([]),
        '/branches/': _Response({'commit': {'sha': SHA_NEW}}),
        '/zipball/': _Response(content=archive),
    }))
    monkeypatch.setattr(updater, 'install_requirements',
                        lambda: types.SimpleNamespace(returncode=1))

    assert updater.do_update_github() == -1
    assert (tmp_path / 'new_file.txt').exists(), 'the files are replaced before the environment is'
    assert read_version_marker() is None, 'a run that failed must NOT claim the install is current'
    out = capsys.readouterr().out
    assert 'stays pending' in out


def test_the_next_run_retries_and_records_the_update(updater, tmp_path, monkeypatch):
    """The retry the operator is told to make has to actually find the update again."""
    monkeypatch.chdir(tmp_path)
    _install(tmp_path)
    archive = _archive({'version.py': '__version__ = "3.0.5.3"\n', 'new_file.txt': 'new'})
    monkeypatch.setattr(updater.requests, 'get', _router({
        '/releases': _Response([]),
        '/branches/': _Response({'commit': {'sha': SHA_NEW}}),
        '/zipball/': _Response(content=archive),
    }))
    monkeypatch.setattr(updater, 'install_requirements', lambda: types.SimpleNamespace(returncode=0))

    assert updater.do_update_github() == 0
    assert read_version_marker() == SHA_NEW


def test_a_refused_check_is_not_reported_as_no_update(updater, tmp_path, monkeypatch, capsys):
    """REPORTED: a rate-limited install looked exactly like an up-to-date one."""
    monkeypatch.chdir(tmp_path)
    _install(tmp_path)
    refused = _Response(status_code=403, reason='rate limit exceeded')
    monkeypatch.setattr(updater.requests, 'get', _router({'/releases': refused, '/branches/': refused}))

    assert updater.do_update_github() == -1
    out = capsys.readouterr().out
    assert 'No update found' not in out
    assert 'HTTP 403' in out and 'rate limit' in out


def test_an_unreachable_github_is_a_failure_and_not_a_traceback(updater, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _install(tmp_path)

    def boom(url, **kwargs):
        raise updater.requests.exceptions.ConnectionError('Max retries exceeded')

    monkeypatch.setattr(updater.requests, 'get', boom)
    assert updater.do_update_github() == -1
    assert 'Could not reach GitHub' in capsys.readouterr().out


# ------------------------------------------------------------------- what the NODE's gate answers

@pytest.mark.asyncio
async def test_a_zip_install_follows_the_main_branch(tmp_path, monkeypatch):
    """The gate ``autoupdate`` and ``/node upgrade`` both go through, on an archive install."""
    monkeypatch.chdir(tmp_path)

    async def commit(self):
        return SHA_NEW

    monkeypatch.setattr(NodeImpl, '_main_branch_commit', commit)
    node = _Node()

    assert await NodeImpl._upgrade_pending_zip(node) is True, 'no marker: nothing has been applied yet'
    (tmp_path / 'version.sha').write_text(SHA_NEW, encoding='utf-8')
    assert await NodeImpl._upgrade_pending_zip(node) is False, 'the recorded commit IS the branch tip'
    (tmp_path / 'version.sha').write_text(SHA_OLD, encoding='utf-8')
    assert await NodeImpl._upgrade_pending_zip(node) is True, 'the branch moved past the recorded commit'


@pytest.mark.asyncio
async def test_an_unreadable_branch_is_unknown_rather_than_no_update(tmp_path, monkeypatch, caplog):
    """A gate that cannot check must not answer "nothing waiting" silently."""
    monkeypatch.chdir(tmp_path)

    async def unreadable(self):
        return None

    monkeypatch.setattr(NodeImpl, '_main_branch_commit', unreadable)
    assert await NodeImpl._upgrade_pending_zip(_Node()) is False


class _FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def raise_for_status(self):
        # the 403 case is handled before this is reached; everything else in these pins is a 200
        return None

    async def json(self, content_type=None):
        return self._payload


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, url, **kwargs):
        return self._response


@pytest.mark.asyncio
async def test_a_refused_branch_check_says_why_in_the_log(monkeypatch, caplog):
    monkeypatch.setattr(nodeimpl.aiohttp, 'ClientSession',
                        lambda *args, **kwargs: _FakeSession(_FakeResponse(403, {'message': 'rate limit'})))
    with caplog.at_level(logging.WARNING, logger='test.zip_update'):
        result = await NodeImpl._github_json(_Node(), 'https://api.github.com/x', 'the master branch')
    assert result is None
    assert any('rate limit' in record.getMessage() for record in caplog.records), \
        'a refused check has to be visible, or it reads as "no update"'


# ------------------------------------------------------- a PINNED checkout is never moved off its tag

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(['git', *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.mark.asyncio
async def test_a_pinned_checkout_is_never_offered_the_main_branch(tmp_path, monkeypatch):
    """A release tag is a decision: a new commit on master must not turn into an offered upgrade."""
    pytest.importorskip('git', reason="GitPython is not installed: the checkout path cannot run here")
    try:
        _git(tmp_path, '--version')
    except Exception:
        pytest.skip('git is not installed')

    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-b', 'master', '.')
    _git(repo, 'config', 'user.email', 't@example.invalid')
    _git(repo, 'config', 'user.name', 'tests')
    (repo / 'version.py').write_text('__version__ = "3.0.5.3"\n', encoding='utf-8')
    _git(repo, 'add', 'version.py')
    _git(repo, 'commit', '-m', 'init')
    _git(repo, 'checkout', '--detach')
    monkeypatch.chdir(repo)

    async def release(self):
        return True

    async def must_not_be_used(self):
        raise AssertionError('a pinned checkout must never be offered the main branch')

    monkeypatch.setattr(NodeImpl, '_upgrade_pending_release', release)
    monkeypatch.setattr(NodeImpl, '_upgrade_pending_zip', must_not_be_used)
    assert await NodeImpl._upgrade_pending_git(_Node()) is True


# --------------------------------------------------- one question, asked by the updater and the node

def test_the_updater_and_the_node_ask_about_the_same_branch(updater):
    """Two spellings of one URL is how the two answers drifted apart in the first place."""
    assert updater.MAIN_BRANCH == nodeimpl.MAIN_BRANCH
    assert updater.MAIN_BRANCH_URL == nodeimpl.BRANCH_URL.format(nodeimpl.MAIN_BRANCH)
    assert updater.MAIN_BRANCH_ZIP_URL.endswith(f'/zipball/{nodeimpl.MAIN_BRANCH}')
    assert nodeimpl.BRANCH_VERSION_URL.endswith(f'/{nodeimpl.MAIN_BRANCH}/version.py')
