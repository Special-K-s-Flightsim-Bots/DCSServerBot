"""The Nodes page: every node in the cluster, in full.

One of the two CLUSTER lists (see :mod:`services.webservice.pages.lists`): the mockups' role model
gives a DCS member the read-only Operations lists but not the cluster ones, so this page declares
the narrower pair — a DCS member is not offered the link and the route refuses them.

WHO READS IT, IN FULL: ``Admin``/``DCS Admin`` through a role, and a MANAGER through its scope
(``scope_grants``) — scoped to the nodes that carry their servers. **Reading** the list is that
wide; **operating** a node is not: the five writes below are declared with ``NODE_ROLES``
(``Admin`` only) and carry no scope grant, because they take every server on the node out of
service.

WHAT A ROW OFFERS (W4c-revised, W4d, restated by W5b):

* Restart, Shut down and Upgrade — the real node lifecycle (``Node.restart()`` / ``Node.shutdown()``
  / ``Node.upgrade()``, mirrored by ``plugins/admin/actions.py``). **Upgrade is OFFERED ONLY WHILE
  THE NODE REPORTS AN UPDATE PENDING** (W7b): the console polls ``Node.upgrade_pending()`` in the
  background (``services/webservice/upgrade.py``, never on a render) and hides the control once
  there is nothing to upgrade, saying ``"no update check yet"`` while the value is unknown;
* the POWER pair Take servers offline / Bring servers online — ``/node offline`` and ``/node online``,
  which act on the SERVERS the node carries: the first stops the servers that are up (through the
  engine's popup chain) and, unless its box is cleared, marks them as maintenance; the second reverts
  exactly that operation — the flags it set and the servers it stopped. They touch NO service. The
  node's services, including the webservice serving this page, keep running, which is why these two
  can be a browser control at all and why a node taken offline through them is brought back from this
  very page.

Every control is reachable only for ``Admin`` and only for a node the cluster can actually reach,
and it is OMITTED — never rendered disabled — otherwise.

THE PAIR IS GATED ON THE NODE'S OWN STATE, NOT ON THE SERVERS AND NOT ON A FLAG (W7a, restating W5b).
"offline" here means the SERVERS, never the node's own process — that is *Shut down*, the trio's own
third control. Which half is offered is decided by ``pages/actions.node_power_states``: a node that
is heartbeating, carries servers and has NO power-off on record is IN SERVICE and offers *Take servers
offline* — whether or not anything happens to be running — and a node whose power-off IS on record
offers *Bring servers online*. The servers' statuses decide neither (they did until W7a, which is what
put an online control on an online node), and the maintenance flag is not the row's business any more:
its own controls are on the SERVER row (``Maintenance`` / ``End maintenance``), one to a server, which
is what §10.4's "one home per concept" means.

THE TAG AND THE CONTROL NOW AGREE. The ONLINE/OFFLINE tag is the HEARTBEAT's verdict
(``readmodels.nodes``: ``node is not None`` in the master's registry) and the power pair is the node's
own power state, asked of the same reachability first — so a row tagged ONLINE offers the way back only
when a power-off is on record, and never because "some server is down". (The tag can still read ONLINE
with the servers stopped or under maintenance: that is the SERVERS' state, and a server's own flag has
its own control on the Servers page.)

WHAT A ROW STILL CANNOT OFFER: a node whose PROCESS is down is started by the MACHINE, not by a
browser — an OS-level job (a launcher, a service, a terminal) that no page can perform. The pair
above does not contradict that: it changes SERVERS, and it is offered only while the node's own
services answer.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import lists as lists_page

__all__ = ["NODES_PATH", "NODES_CAPABILITY", "NODES_ROLES", "NODES_TABLE", "NODES_TITLE",
           "NODES_LEAD", "NAV_LABEL", "PAGE", "capabilities", "nav_items", "add_routes"]

NODES_PATH = "/nodes"
NODES_CAPABILITY = "nodes.view"
NODES_ROLES: tuple[str, ...] = lists_page.CLUSTER_ROLES
NODES_TABLE = "nodes"
NODES_TITLE = "Nodes"
NODES_LEAD = ("Every node in the cluster, with the instances and servers it carries. An Admin's "
              "row for a node that is heartbeating offers Restart, Shut down and Upgrade — each "
              "takes every server on that node down with it, though Upgrade is offered only while "
              "the node itself reports an update pending — and the power pair: Take servers "
              "offline stops the servers that are up and, unless the box is cleared, marks them as "
              "maintenance; Bring servers online reverts exactly that — it clears the maintenance "
              "flags its own power-off set and starts the servers it stopped, while a flag somebody "
              "set by hand is left alone. Neither touches the node's services — this console keeps "
              "running, and the servers are brought back from this very page. Those two are offered "
              "on the NODE's own state, not on what its servers happen to be doing: a node that is "
              "heartbeating and has no power-off on record is in service and offers Take servers "
              "offline — whether or not anything is running — and a node whose power-off is on "
              "record offers Bring servers online. A server's own maintenance flag has "
              "its own control on the Servers page — one server to a row. An OFFLINE node offers no "
              "control: it cannot be reached at all, and starting it is a job for the machine "
              "itself, never for this browser. A node's ONLINE/OFFLINE tag is the heartbeat's "
              "verdict, so it can read ONLINE while its servers are stopped or under maintenance.")
NAV_LABEL = "Nodes"

PAGE = lists_page.ListPage(table=NODES_TABLE, path=NODES_PATH, capability=NODES_CAPABILITY,
                           roles=NODES_ROLES, title=NODES_TITLE, lead=NODES_LEAD,
                           nav_label=NAV_LABEL, scope_grants=True, writes=True)


def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers."""
    PAGE.declare()
    return {NODES_PATH: NODES_CAPABILITY}


def nav_items():
    """The sidebar entry this page contributes — data, not markup."""
    return (PAGE.nav_item(),)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the Nodes route to the shell's own router and return it."""
    return lists_page.add_list_route(router, PAGE)
