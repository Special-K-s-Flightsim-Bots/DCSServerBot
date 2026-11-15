"""``set_password`` must create the secret directory of the config dir IT was given.

The store is ``<config_dir>/.secret/<key>.pkl``. ``create_secret_dir`` takes that path, but
``set_password`` called it with no argument, so a non-default config dir got the directory created
under the process's own working directory and the write then failed on the real one.
"""

from __future__ import annotations

from core.utils.os import get_password, set_password


def test_set_password_creates_the_secret_dir_of_the_given_config_dir(tmp_path, monkeypatch):
    config_dir = tmp_path / "elsewhere"
    config_dir.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    set_password("probe_key", "probe_value", str(config_dir))

    assert (config_dir / ".secret" / "probe_key.pkl").exists(), \
        "the secret was not written under the config dir it was given"
    assert not (cwd / "config").exists(), \
        "a stray ./config/.secret was created in the working directory"
    assert get_password("probe_key", str(config_dir)) == "probe_value"


def test_set_password_default_config_dir_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    set_password("probe_key", "probe_value")

    assert (tmp_path / "config" / ".secret" / "probe_key.pkl").exists()
    assert get_password("probe_key") == "probe_value"
