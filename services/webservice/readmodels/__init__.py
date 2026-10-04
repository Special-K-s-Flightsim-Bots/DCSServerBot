"""Pure read models for the read-only dashboard.

Layout, one module per read model:

======================  ======================================================================
``model.py``            the view records and the status vocabulary (pure data)
``source.py``           where state comes from — ``LiveSource`` (in-process) / ``EmptySource``
``access.py``           tolerant attribute access, shared
``nodes.py``            the cluster registry + the servers each node carries
``instances.py``        every node's instance registry (cluster-wide)
``servers.py``          one row per server: status, port, mission, player counts
``players.py``          the players online right now
``logtail.py``          the bot's log file, tailed
``dcslog.py``           a SERVER's own ``dcs.log``: the config-driven path, the line format, the
                        byte-window arithmetic (the windowed read itself lives in the page, which
                        may RPC a node)
``overview.py``         one call that builds everything the page renders
``views.py``            the validated view layer: ``?sort=``/``?dir=``/``?q=`` (sorting + search)
======================  ======================================================================

NO HTTP, NO DISCORD, NOTHING AWAITED WHILE RENDERING. The page must render with ``bot = None``
(early start, after a master/agent takeover) and the models read plain duck-typed objects, which is
what makes "the read models are pure" a checked property rather than a claim.

THIS PACKAGE DOES REACH THE BOT'S ``core``, and deliberately: ``source.py`` imports
``server_managed_by`` from ``services.webservice.scope``, which consumes the shared ``managed_by``
rule that LIVES in ``core/utils/discord.py`` (one rule, pinned by
``tests/test_layering_direction.py``). It is therefore NOT importable by an interpreter without
psycopg — the console runs in the bot's own environment and may use the bot's libraries. Only the
LIVE registry's ``core`` import is kept function-local, in
:func:`services.webservice.readmodels.source.resolve_source`.
"""
from __future__ import annotations

from .access import as_int, port_number, safe, text
from .instances import instances
from .logtail import DEFAULT_LINES, log_tail, tail
from .model import (LOG_EMPTY_MESSAGE, LOG_MISSING_MESSAGE, NO_INSTANCES_MESSAGE,
                    NO_LIVE_REASON, NO_NODES_MESSAGE, NO_NODE_INSTANCES_MESSAGE,
                    NO_PLAYERS_MESSAGE, NO_SERVERS_MESSAGE, SCOPED_EMPTY_MESSAGE,
                    STATUS_WORDS, InstanceView, LogLine, LogTail, NodeView, Overview, PlayerView,
                    ServerView, SourceStatus, StatusView, format_duration, status_view)
from .nodes import nodes
from .overview import overview
from .players import players_online
from .serverconfig import (EDITABLE_FIELDS, READONLY_FIELDS, ConfigField,
                           ConfigGroup, FieldView, ServerConfigView, server_config_view,
                           bound_text, editable_keys, readonly_keys, STOP_FIRST_SENTENCE,
                           CHANNEL_KEYS, CHANNEL_ADMIN_KEY, CHANNEL_UNSET, CHANNEL_UNSET_VALUE,
                           CHANNEL_APPLY_NOTE, ChannelOption, ChannelGroup, ChannelView,
                           channel_value_text, live_bot, central_admin_channel,
                           guild_channel_groups,
                           COALITION_FIELDS, COALITION_TOKENS, COALITION_OF_KEY, CoalitionView,
                           coalition_views, coalition_keys, COALITION_APPLY_NOTE,
                           COALITION_UNKNOWN_NOTE, COALITION_DB_ONLY_NOTE,
                           COALITION_REFUSED_SENTENCE, WRITABLE_COALITION_RAW)
from .servers import SERVERS_HREF_PREFIX, server_url, servers
from .source import EmptySource, LiveSource, ScopedSource, Source, console_source, \
    declared_node_instances, log_path_for, resolve_source, scoped_source
from .views import (Column, ColumnView, PlayerSearch, TableView, ViewParams, ViewState,
                    column_link, sort_rows, table_view, view_params, view_state)

__all__ = [
    # records + vocabulary
    "SourceStatus", "StatusView", "NodeView", "InstanceView", "ServerView", "PlayerView",
    "LogLine", "LogTail", "Overview", "STATUS_WORDS", "status_view", "format_duration",
    # empty-state copy
    "NO_LIVE_REASON", "NO_SERVERS_MESSAGE", "NO_PLAYERS_MESSAGE", "NO_NODES_MESSAGE",
    "NO_INSTANCES_MESSAGE", "NO_NODE_INSTANCES_MESSAGE", "LOG_MISSING_MESSAGE",
    "LOG_EMPTY_MESSAGE", "SCOPED_EMPTY_MESSAGE",
    # sources (the scope wrap is the ONE place the hoster view is applied; console_source is the
    # ONE place the application's source seam is resolved)
    "Source", "EmptySource", "LiveSource", "ScopedSource", "scoped_source", "resolve_source",
    "console_source", "log_path_for", "declared_node_instances",
    # read models
    "nodes", "instances", "servers", "players_online", "log_tail", "tail", "overview",
    "DEFAULT_LINES",
    # a server's read model AND the ONE encoder of its page URL (the row link and the redirect back)
    "server_url", "SERVERS_HREF_PREFIX",
    # the DCS configuration read model (the Configuration tab): its declaration and its view
    "EDITABLE_FIELDS", "READONLY_FIELDS", "ConfigField", "ConfigGroup",
    "FieldView", "ServerConfigView", "server_config_view", "bound_text", "editable_keys",
    "readonly_keys", "STOP_FIRST_SENTENCE",
    # the channels face: the per-server rows, the guild's options and the central rule
    "CHANNEL_KEYS", "CHANNEL_ADMIN_KEY", "CHANNEL_UNSET", "CHANNEL_UNSET_VALUE", "CHANNEL_APPLY_NOTE",
    "ChannelOption", "ChannelGroup", "ChannelView", "channel_value_text",
    "live_bot", "central_admin_channel", "guild_channel_groups",
    # the coalition face: the two plaintext rows and the states they render
    "COALITION_FIELDS", "COALITION_TOKENS", "COALITION_OF_KEY", "CoalitionView", "coalition_views",
    "coalition_keys", "COALITION_APPLY_NOTE", "COALITION_UNKNOWN_NOTE", "COALITION_DB_ONLY_NOTE",
    "COALITION_REFUSED_SENTENCE", "WRITABLE_COALITION_RAW",
    # the validated view layer (sorting + players search)
    "Column", "ColumnView", "TableView", "PlayerSearch", "ViewParams", "ViewState",
    "column_link", "sort_rows", "table_view", "view_params", "view_state",
    # helpers
    "text", "as_int", "port_number", "safe",
]