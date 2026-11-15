"""The radio's ``music.yaml`` write keeps the operator's file.

``Radio.config`` used to dump the whole document through ``new_yaml()``, which drops every comment
and reorders the keys. These cases write a REAL config file through the config primitive and read it
back, so what is asserted is the file on disk — comments, key order, and the node/instance shape the
service reads its blocks from — rather than the fact that some function was called.
"""
from __future__ import annotations

import types

import pytest

from core import utils
from core.utils.yaml_helper import new_yaml
from services.music.radios import Radio, write_radio_config

CONFIG = """\
# config/services/music.yaml
# The radio definitions of this node.
DEFAULT:
  music_dir: /music   # where the files are
  popup: Now playing {song}
  radios:
    Radio 1:                      # the first radio
      type: SRSRadio
      mode: 1                     # 1 = repeat, 2 = shuffle
      frequency: '30.0'
      modulation: FM
      coalition: 1
      volume: '0.5'
      display_name: Jungle Beats
    Radio 2:
      type: SRSRadio
      mode: 2
      frequency: '32.0'
      modulation: FM
      coalition: 2
      volume: '0.5'
      display_name: Rock & Pop
"""

NODE_CONFIG = """\
# config/services/music.yaml
DEFAULT:
  music_dir: /music
NODE-01:
  SRS-1:
    radios:
      Radio 1:                    # this instance's radio
        type: SRSRadio
        mode: 1
        frequency: '30.0'
"""


class FakeService:
    """The two things the writer needs: the node's config dir and the reload after the write."""

    def __init__(self, config_dir):
        self.node = types.SimpleNamespace(config_dir=str(config_dir))
        self.path = config_dir / "services" / "music.yaml"
        self.locals = new_yaml().load(self.path.read_text(encoding='utf-8'))
        self._config: dict = {}
        self.reloads = 0

    def get_config(self, server=None):
        """The service's own read: the instance block merged over DEFAULT (``Service.get_config``)."""
        if server is None:
            return self.locals.get("DEFAULT", {})
        node_block = self.locals.get(server.node.name, self.locals)
        return utils.deep_merge(self.locals.get("DEFAULT", {}),
                                node_block.get(server.instance.name, {}))

    def reload(self):
        self.reloads += 1
        self.locals = new_yaml().load(self.path.read_text(encoding='utf-8'))
        self._config.clear()


def server():
    return types.SimpleNamespace(
        name="SRS-1",
        node=types.SimpleNamespace(name="NODE-01", config_dir=""),
        instance=types.SimpleNamespace(name="SRS-1"),
    )


def write_config(tmp_path, text=CONFIG):
    config_dir = tmp_path / "config"
    (config_dir / "services").mkdir(parents=True)
    path = config_dir / "services" / "music.yaml"
    path.write_text(text, encoding='utf-8')
    built = FakeService(config_dir)
    target = server()
    target.node.config_dir = str(config_dir)
    return built, target, path


def test_the_write_keeps_the_comments_and_the_key_order(tmp_path):
    built, target, path = write_config(tmp_path)
    before = path.read_text(encoding='utf-8').splitlines()
    write_radio_config(built, target, "Radio 1", {"frequency": "31.5", "volume": "0.25"})
    after = path.read_text(encoding='utf-8').splitlines()
    # every comment is still there, in the same order
    comments = [line for line in before if line.lstrip().startswith("#")]
    assert comments
    assert [line for line in after if line.lstrip().startswith("#")] == comments
    # and so is every key, in the same order — the write adds the INSTANCE block it states, at the
    # end, and nothing else
    keys = [line.split(":")[0] for line in before if line and not line.startswith(" ")]
    written_keys = [line.split(":")[0] for line in after if line and not line.startswith(" ")]
    assert written_keys[:len(keys)] == keys
    assert written_keys[len(keys):] == ["SRS-1"]
    # the two stated values moved, the rest did not
    written = new_yaml().load(path.read_text(encoding='utf-8'))
    radio = written["SRS-1"]["radios"]["Radio 1"]
    assert radio["frequency"] == "31.5"
    assert radio["volume"] == "0.25"
    # only the stated keys are written: everything else is still read from DEFAULT
    merged = built.get_config(target)["radios"]["Radio 1"]
    assert merged["modulation"] == "FM"
    assert merged["display_name"] == "Jungle Beats"
    # the sibling radio is untouched, and so is the block the values are inherited from
    assert "Radio 2" not in written["SRS-1"]["radios"]
    assert written["DEFAULT"]["radios"]["Radio 2"]["frequency"] == "32.0"
    assert written["DEFAULT"]["music_dir"] == "/music"
    # the cache is dropped, so the radio reads the new values
    assert built.reloads == 1
    assert built.get_config(target)["radios"]["Radio 1"]["frequency"] == "31.5"


def test_the_write_states_the_instance_block_when_the_document_carries_a_node_key(tmp_path):
    built, target, path = write_config(tmp_path, NODE_CONFIG)
    write_radio_config(built, target, "Radio 1", {"volume": "0.9"})
    written = new_yaml().load(path.read_text(encoding='utf-8'))
    assert written["NODE-01"]["SRS-1"]["radios"]["Radio 1"]["volume"] == "0.9"
    assert "SRS-1" not in written            # never a second, shadowed instance block
    assert built.get_config(target)["radios"]["Radio 1"]["volume"] == "0.9"


def test_the_write_creates_the_block_of_a_radio_the_file_does_not_carry_yet(tmp_path):
    built, target, path = write_config(tmp_path)
    write_radio_config(built, target, "Radio 9", {"type": "SRSRadio", "mode": 1})
    written = new_yaml().load(path.read_text(encoding='utf-8'))
    assert written["SRS-1"]["radios"]["Radio 9"] == {"type": "SRSRadio", "mode": 1}
    # a node-keyed document gets the NEW radio under the node, never at the top level
    built, target, path = write_config(tmp_path / "keyed", NODE_CONFIG)
    write_radio_config(built, target, "Radio 2", {"mode": 2})
    written = new_yaml().load(path.read_text(encoding='utf-8'))
    assert written["NODE-01"]["SRS-1"]["radios"]["Radio 2"] == {"mode": 2}


def test_the_radio_config_setter_is_the_one_writer(tmp_path):
    """``Radio.config = {...}`` lands in the file — no second spelling of the write."""
    built, target, path = write_config(tmp_path)
    radio = types.SimpleNamespace(service=built, server=target, name="Radio 2")

    def setter(self, config):          # the property's own fset, called on the double
        return Radio.config.fset(self, config)

    setter(radio, {"display_name": "Night Jazz"})
    written = new_yaml().load(path.read_text(encoding='utf-8'))
    assert written["SRS-1"]["radios"]["Radio 2"]["display_name"] == "Night Jazz"
    # the value the write did not state is still the one the radio reads (DEFAULT's)
    assert built.get_config(target)["radios"]["Radio 2"]["frequency"] == "32.0"


def test_a_write_that_cannot_read_the_file_raises_and_leaves_it_alone(tmp_path):
    built, target, path = write_config(tmp_path / "broken")
    before = "DEFAULT: [unclosed\n"
    path.write_text(before, encoding='utf-8')
    with pytest.raises(Exception):
        write_radio_config(built, target, "Radio 1", {"volume": "0.1"})
    assert path.read_text(encoding='utf-8') == before
