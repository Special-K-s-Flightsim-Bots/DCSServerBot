"""DCSServerBot's own autoupdate: a major version step waits for a person.

``run.py`` applies a pending update unattended, which is right for a hotfix and wrong for a feature release — the
installer and the schema migrations behind it belong to somebody who knows the install. These pin the rule, the
node-side decision the launcher makes, and the ONE wording both messages use.
"""
from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest

from core.data.impl.nodeimpl import NodeImpl
from core.utils.helper import is_major_version_change, upgrade_message
from version import __version__ as BOT_VERSION

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------------------------- the rule

@pytest.mark.parametrize("installed, latest, expected", [
    ("3.0.4.28", "3.0.4.29", False),      # a hotfix: apply it as before
    ("3.0.4.28", "3.0.4.100", False),     # same first three numbers, whatever the fourth
    ("3.0.4.28", "3.0.5.0", True),        # the third number moved: a feature release
    ("3.0.4.28", "3.1.0.0", True),
    ("3.0.4.28", "4.0.0.0", True),
    ("3.0", "3.0.0", False),              # an absent number is a zero, not a change
    ("3.0", "3.0.5", True),
    ("v3.0.4.28", "v3.0.5.0", True),      # a leading v belongs to packaging, not to the rule
    ("nightly", "3.0.5.0", False),        # unreadable: never switch every update off
    ("3.0.4.28", "", False),
])
def test_only_the_first_three_numbers_decide(installed, latest, expected):
    assert is_major_version_change(installed, latest) is expected


def test_the_message_names_both_versions_when_a_major_step_waits():
    text = upgrade_message("3.0.4.28", "3.0.5.0")
    assert "3.0.4.28" in text and "3.0.5.0" in text
    assert "not applied automatically" in text
    assert "/node upgrade" in text and "update.cmd" in text


def test_the_message_without_versions_keeps_the_old_wording():
    assert upgrade_message() == \
        "New update for DCSServerBot available!\nUse /node upgrade or enable autoupdate to apply it."


# ------------------------------------------------------------------- the launcher's decision, on the node

class _Node(NodeImpl):
    """Exactly the node surface the two methods read.

    Subclasses ``NodeImpl`` so the unbound calls below type-check, and bypasses its ``__init__`` (which wants a
    config directory and a database) with the three fields the methods actually touch.
    """

    def __init__(self, pending: bool = True, latest: str | None = None,
                 versions: tuple[str, str] | None = None):
        self.major_upgrade = None
        self.log = types.SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None,
                                         warning=lambda *a, **k: None)
        self._pending = pending
        self._latest = latest
        self._versions = versions

    async def upgrade_pending(self) -> bool:
        return self._pending

    async def _latest_version(self):
        return self._latest

    async def upgrade_versions(self):
        return self._versions


@pytest.mark.asyncio
async def test_a_hotfix_does_not_hold_the_update_back():
    node = _Node(versions=("3.0.4.28", "3.0.4.29"))
    assert await NodeImpl.upgrade_is_major(node) is False
    assert node.major_upgrade is None


@pytest.mark.asyncio
async def test_a_feature_release_is_held_back_and_recorded_for_the_bot_to_announce():
    node = _Node(versions=("3.0.4.28", "3.0.5.0"))
    assert await NodeImpl.upgrade_is_major(node) is True
    assert node.major_upgrade == ("3.0.4.28", "3.0.5.0")


@pytest.mark.asyncio
async def test_an_unreadable_version_never_holds_an_update_back():
    node = _Node(versions=None)                 # upgrade_versions() could not read the number
    assert await NodeImpl.upgrade_is_major(node) is False


@pytest.mark.asyncio
async def test_no_pending_update_means_no_versions():
    assert await NodeImpl.upgrade_versions(_Node(pending=False, latest="3.0.5.0")) is None


@pytest.mark.asyncio
async def test_both_versions_are_reported_when_the_number_is_readable():
    node = _Node(pending=True, latest="3.0.5.0")
    assert await NodeImpl.upgrade_versions(node) == (BOT_VERSION, "3.0.5.0")


@pytest.mark.asyncio
async def test_a_pending_update_without_a_readable_number_reports_nothing():
    assert await NodeImpl.upgrade_versions(_Node(pending=True, latest=None)) is None


# --------------------------------------------------------------------------------------- the wiring

def test_the_launcher_asks_whether_the_update_is_major_before_applying_it():
    """A text pin on purpose: ``Main.run()`` builds a whole node and service stack and cannot run in a test."""
    source = (REPO / "run.py").read_text(encoding="utf-8")
    assert "await self.node.upgrade_is_major()" in source
    assert source.index("upgrade_is_major()") < source.index("await self.node.upgrade()")


def test_the_launcher_sets_the_node_up_for_the_bots_announcement():
    """The bot can only announce what the launcher left on the node."""
    run = (REPO / "run.py").read_text(encoding="utf-8")
    bot = (REPO / "services" / "bot" / "dcsserverbot.py").read_text(encoding="utf-8")
    assert "self.node.major_upgrade" in run
    assert "if self.node.major_upgrade:" in bot
    assert "_announce_major_upgrade" in bot


# ------------------------------- where the NEW version is read from: a branch carries no tags (the reported caveat)

def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def checkout(tmp_path):
    """A branch and a checkout of it — the way a git install is updated, with no tags anywhere.

    GitPython is a shipped dependency (``requirements.in``), but a dev box may not have it installed: the tests
    that need a real checkout then skip rather than pretend the other install kind is the same thing.
    """
    pytest.importorskip('git', reason="GitPython is not installed: the checkout path cannot run here")
    remote = tmp_path / 'remote'
    remote.mkdir()
    _git(remote, 'init', '--bare', '-b', 'development')
    work = tmp_path / 'work'
    _git(tmp_path, 'clone', str(remote), str(work))
    _git(work, 'config', 'user.email', 'test@example.invalid')
    _git(work, 'config', 'user.name', 'test')
    return remote, work


def _push_version(work, version: str, message: str = "bump"):
    (work / 'version.py').write_text(f'__version__ = "{version}"\n', encoding='utf-8')
    _git(work, 'add', 'version.py')
    _git(work, 'commit', '-m', message)
    _git(work, 'push', 'origin', 'development')


@pytest.mark.asyncio
async def test_the_new_version_comes_from_the_branch_even_where_there_are_no_tags(checkout, monkeypatch):
    """A branch carries no release tags, so the number has to be the branch tip's own ``version.py``."""
    _remote, work = checkout
    _push_version(work, "3.0.4.28")
    _push_version(work, "3.0.5.0", "feature release")
    monkeypatch.chdir(work)
    assert await NodeImpl._latest_version(_Node()) == "3.0.5.0"


@pytest.mark.asyncio
async def test_a_failed_branch_read_stays_unknown_and_is_never_a_release_tag(checkout, monkeypatch):
    """On a checkout the release tag answers a different question, so it must not be a fallback."""
    _remote, work = checkout
    _push_version(work, "3.0.4.28")
    _git(work, 'checkout', '--detach')                      # no branch to read
    monkeypatch.chdir(work)

    async def must_not_be_used(self):
        raise AssertionError("a checkout must never fall back to a release tag")

    monkeypatch.setattr(NodeImpl, '_latest_version_release', must_not_be_used)
    assert await NodeImpl._latest_version(_Node()) is None


@pytest.mark.asyncio
async def test_an_install_without_a_checkout_reads_the_release_tag(tmp_path, monkeypatch):
    """The other install kind: it updates to the newest RELEASE, and that tag is the number it would move to."""
    if subprocess.run(['git', 'rev-parse', '--git-dir'], cwd=tmp_path,
                      capture_output=True).returncode == 0:
        pytest.skip("tmp_path sits inside a repository, so there is no checkout-free case to test")
    monkeypatch.chdir(tmp_path)

    async def tag(self):
        return "3.0.5.0"

    monkeypatch.setattr(NodeImpl, '_latest_version_release', tag)
    assert await NodeImpl._latest_version(_Node()) == "3.0.5.0"


# ------------------------------------------------------ the cluster upgrade intent survives a dead master

def test_a_master_dying_mid_upgrade_does_not_cancel_it_for_the_whole_cluster():
    """REPORTED: the master died mid-upgrade and every agent was left on the old version.

    That branch is a nested function inside ``heartbeat()`` (``nodeimpl.py:1323``) and cannot be executed from a
    test without lifting three helpers out of a 300-line method, so this pins the intent in the source instead —
    the live check on a two-node cluster stays the operator's. The intent: taking over after a master died must
    NOT clear ``cluster.update_pending``, because that flag is the instruction every node updates on.
    """
    source = (REPO / "core" / "data" / "impl" / "nodeimpl.py").read_text(encoding="utf-8")
    assert "Master died during an upgrade" in source, "the take-over branch moved — review this pin"
    branch = source.split("Master died during an upgrade", 1)[1].split("return await try_become_master()", 1)[0]
    assert "update_pending = FALSE" not in branch, \
        "taking over must not cancel the upgrade for the cluster: the next master re-announces it"


# ----------------------------------------------- a PINNED (detached) checkout is a state, not an error

def _probe_git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def tagged_clones(tmp_path):
    """Two real clones of one repository, both detached at a release tag — the reported state.

    One was cloned on the default branch and then checked the tag out, so it still HAS a local branch;
    the other was cloned WITH ``--branch <tag>``, which is how one pins a fresh install and leaves no
    local branch at all. Both shapes are real and the code has to answer for both.
    """
    try:
        _probe_git('--version', cwd=tmp_path)
    except Exception:
        pytest.skip("git is not installed")
    bare, seed = tmp_path / 'origin.git', tmp_path / 'seed'
    _probe_git('init', '--bare', '-b', 'master', str(bare), cwd=tmp_path)
    seed.mkdir()
    _probe_git('init', '-b', 'master', '.', cwd=seed)
    _probe_git('config', 'user.email', 't@example.invalid', cwd=seed)
    _probe_git('config', 'user.name', 'tests', cwd=seed)
    (seed / 'version.py').write_text('__version__ = "3.0.4.27"\n', encoding='utf-8')
    _probe_git('add', 'version.py', cwd=seed)
    _probe_git('commit', '-m', 'init', cwd=seed)
    _probe_git('tag', 'v3.0.4.27', cwd=seed)
    _probe_git('remote', 'add', 'origin', str(bare), cwd=seed)
    _probe_git('push', 'origin', 'master', '--tags', cwd=seed)
    (seed / 'version.py').write_text('__version__ = "3.0.5.0"\n', encoding='utf-8')
    _probe_git('commit', '-am', 'later release', cwd=seed)
    _probe_git('push', 'origin', 'master', cwd=seed)

    kept = tmp_path / 'cloned-then-pinned'
    _probe_git('clone', str(bare), str(kept), cwd=tmp_path)
    _probe_git('checkout', 'v3.0.4.27', cwd=kept)

    bare_clone = tmp_path / 'cloned-with-branch-tag'
    _probe_git('clone', '--branch', 'v3.0.4.27', str(bare), str(bare_clone), cwd=tmp_path)
    return kept, bare_clone


def _state(path):
    """``(state, repo)`` for *path* — the repo stays OPEN because callers may run git through it."""
    from contextlib import closing

    import git

    from core.data.impl.nodeimpl import _read_git_state

    repo = git.Repo(path)
    with closing(repo):
        return _read_git_state(repo), repo


def test_a_pinned_checkout_is_read_without_asking_for_a_branch(tagged_clones):
    """REPORTED: DCS Server Bot fails from a detached head (a release tag).

    ``repo.active_branch`` raises there and ``origin.refs[<tag>]`` has nothing to find, because tags
    are not remote branches. Both clone shapes must read cleanly, name the pin, and name the branch an
    unpin would return to.
    """
    for path in tagged_clones:
        state, _repo = _state(path)
        assert state.detached is True
        assert state.pinned == 'v3.0.4.27', "the tag HEAD sits on is how a pinned install names itself"
        assert state.branch is None and state.target is None
        assert state.default_branch == 'master', "the branch an unpin would move onto"


def test_a_branch_checkout_reads_its_branch_tracking_and_target(tagged_clones):
    """The ordinary case is unchanged: a branch, its tracking ref, and the commit it would move to.

    A clone in step with its upstream reads ``target == current`` — which is exactly the "nothing to
    pull" answer the update check turns into "no upgrade available".
    """
    kept, _bare_clone = tagged_clones
    _probe_git('checkout', 'master', cwd=kept)
    state, _repo = _state(kept)
    assert state.detached is False
    assert state.branch == 'master'
    assert state.tracking == 'origin/master'
    assert state.target and state.target == state.current


def test_the_unpin_arguments_match_the_shape_of_the_clone(tagged_clones):
    """A clone that kept its branch only needs a checkout; one that never had one must create it."""
    from core.data.impl.nodeimpl import _unpin_argv
    for path, expected in zip(tagged_clones, (['checkout', 'master'],
                                              ['checkout', '-b', 'master', '--track', 'origin/master'])):
        state, repo = _state(path)
        assert state.detached is True
        assert _unpin_argv(repo, state) == expected


def test_the_unpin_arguments_actually_leave_the_detached_state(tagged_clones):
    """Run them for real: after the unpin the checkout is on a branch that tracks origin."""
    from core.data.impl.nodeimpl import _unpin_argv
    for path in tagged_clones:
        state, repo = _state(path)
        argv = _unpin_argv(repo, state)
        assert argv is not None, "a pinned checkout must have an unpin"
        repo.git.execute(['git', *argv])
        repo.close()
        after, _repo = _state(path)
        assert after.detached is False and after.branch == 'master'
        assert after.tracking == 'origin/master'