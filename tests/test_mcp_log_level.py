"""The SDK transport's logging level: its per-connection teardown INFO is silenced, WARNING is not.

``services/mcpservice/__init__.py`` sets ``mcp.server.streamable_http`` to WARNING at import, so the
line the SDK logs on every client disconnect (``Terminating session: None``) never reaches the bot's
root handler, while a real SDK fault (mount, session manager) still does.
"""
from __future__ import annotations

import logging

LOGGER_NAME = "mcp.server.streamable_http"


def _sdk_logger() -> logging.Logger:
    """Import the owning module and return the SDK's transport logger it configures."""
    import services.mcpservice  # noqa: F401  the import applies the level

    return logging.getLogger(LOGGER_NAME)


def test_level_is_warning_at_import() -> None:
    logger = _sdk_logger()
    assert logger.level == logging.WARNING
    assert logger.getEffectiveLevel() == logging.WARNING
    assert logger.isEnabledFor(logging.WARNING)
    assert not logger.isEnabledFor(logging.INFO)
    assert logger.propagate


def test_info_on_the_sdk_logger_does_not_reach_the_root_log(caplog) -> None:
    logger = _sdk_logger()
    with caplog.at_level(logging.DEBUG):
        logger.info("Terminating session: None")
    assert [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME] == []


def test_warning_on_the_sdk_logger_reaches_the_root_log(caplog) -> None:
    logger = _sdk_logger()
    with caplog.at_level(logging.DEBUG):
        logger.warning("Session manager failed to start")
    assert [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME] == [
        "Session manager failed to start"
    ]