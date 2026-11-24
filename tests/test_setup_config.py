"""``core.utils.setup_config`` builds the exact config shapes the installer writes.

The installers two front ends (the CMD installer and the future web tour) must produce the same
dicts, so these pin the shape -- keys AND order, which the YAML round-trip preserves.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.utils.setup_config import (  # noqa: E402
    DEFAULT_NO_DISCORD_ROLES, DEFAULT_ROLE_DEFAULTS, DEFAULT_ROLE_NAMES, SRS_CONFIG_TEMPLATE,
    build_discord_bot, build_instance, build_main, build_no_discord_bot, build_no_discord_main,
    build_node, build_scheduler, build_server_channels)


def test_role_sets_are_the_installer_defaults():
    assert DEFAULT_ROLE_NAMES == ("Admin", "DCS Admin", "DCS")
    assert DEFAULT_ROLE_DEFAULTS == ("Admin", "DCS Admin", "@everyone")
    assert DEFAULT_NO_DISCORD_ROLES == ("Admin", "DCS Admin")
    assert SRS_CONFIG_TEMPLATE == '{instance.home}/Config/SRS.cfg'


def test_build_main_discord_shape_and_order():
    main = build_main(100000000000000001, True)
    assert list(main) == ["guild_id", "autoupdate"]
    assert main == {"guild_id": 100000000000000001, "autoupdate": True}


def test_build_main_adds_language_only_when_given():
    assert "language" not in build_main(1, False)
    assert build_main(1, False, "de") == {"guild_id": 1, "autoupdate": False, "language": "de"}


def test_build_no_discord_main_shape_and_order():
    main = build_no_discord_main("My Group", True, guild_id=123)
    assert list(main) == ["guild_id", "guild_name", "autoupdate"]
    assert main == {"guild_id": 123, "guild_name": "My Group", "autoupdate": True}


def test_build_no_discord_main_draws_a_random_guild_id():
    main = build_no_discord_main("My Group", False)
    assert isinstance(main["guild_id"], int)
    assert 111111111111111111 <= main["guild_id"] <= 999999999999999999


def test_build_discord_bot_without_channels():
    roles = {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["@everyone"]}
    bot = build_discord_bot(42, True, roles)
    assert list(bot) == ["owner", "privileged_intents", "roles"]
    assert bot == {"owner": 42, "privileged_intents": True, "roles": roles}


def test_build_discord_bot_channels_written_only_when_set():
    bot = build_discord_bot(42, True, {}, audit_channel=-1, admin_channel=-1)
    assert "channels" not in bot
    bot = build_discord_bot(42, True, {}, audit_channel=7, admin_channel=9)
    assert bot["channels"] == {"audit": 7, "admin": 9}
    # a lone audit channel is the only key written
    assert build_discord_bot(42, True, {}, audit_channel=7)["channels"] == {"audit": 7}


def test_build_no_discord_bot_shape():
    assert build_no_discord_bot({"Admin": ["x"]}) == {"no_discord": True, "roles": {"Admin": ["x"]}}


def test_build_node_minimal():
    node = build_node(10042, False)
    assert list(node) == ["listen_port", "use_upnp"]
    assert node == {"listen_port": 10042, "use_upnp": False}


def test_build_node_full_order():
    node = build_node(10042, True, public_ip="1.2.3.4", database_url="postgres://x",
                      dcs_installation="/dcs", dcs_autoupdate=True, srs_installation="/srs")
    assert list(node) == ["listen_port", "use_upnp", "public_ip", "database", "DCS",
                          "extensions", "instances"]
    assert node["database"] == {"url": "postgres://x"}
    assert node["DCS"] == {"installation": "/dcs", "autoupdate": True}
    assert node["extensions"] == {"SRS": {"installation": "/srs"}}
    assert node["instances"] == {}


def test_build_node_dcs_blocks_require_a_dcs_installation():
    node = build_node(1, False, srs_installation="/srs")
    assert "DCS" not in node and "extensions" not in node and "instances" not in node


def test_build_node_no_database_when_url_omitted():
    assert "database" not in build_node(1, False, database_url=None)


def test_build_instance_without_srs():
    inst = build_instance(home="/saved/DCS.openbeta", bot_port=6666)
    assert list(inst) == ["bot_port", "home"]
    assert inst == {"bot_port": 6666, "home": "/saved/DCS.openbeta"}


def test_build_instance_with_srs():
    inst = build_instance(home="/saved/X", bot_port=6666, srs_installation="/srs", srs_port=5002)
    assert inst == {"bot_port": 6666, "home": "/saved/X",
                    "extensions": {"SRS": {"config": SRS_CONFIG_TEMPLATE, "port": 5002}}}


def test_build_server_channels_always_has_status_and_chat():
    assert build_server_channels(3) == {"status": 3, "chat": -1}
    assert build_server_channels(3, 4) == {"status": 3, "chat": 4}
    assert build_server_channels(3, 4, 5) == {"status": 3, "chat": 4, "admin": 5}


def test_build_scheduler():
    assert build_scheduler(True) == {"schedule": {"00-24": "YYYYYYY"}}
    assert build_scheduler(False) == {}
