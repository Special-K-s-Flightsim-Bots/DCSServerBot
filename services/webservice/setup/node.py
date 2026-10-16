"""Step 4 of the tour — This node: the DCS installation, the listen port, the static-IP switch and
the two DGSA banlist opt-ins.

DETECTION IS READ FROM THE INSTALLER'S OWN HELPERS, NEVER RE-DONE HERE. The DCS candidates come
from :func:`core.utils.dcs.detect_dcs_installation` (the ``win32`` registry walk ``install.py``
offers as a numbered list; empty on every other platform), the public address from
:func:`core.utils.network.get_public_ip` and the UPnP answer from
:func:`core.utils.network.is_upnp_available` — the same three calls ``install.py`` makes. Every
probe is read-only and bounded: a failing probe yields an empty/``None``/``False`` answer rather
than raising into a render.

THE ANSWERS ARE THE INSTALLER'S INPUTS. :class:`NodeAnswers` carries exactly the values
``install.py`` collects in its Node Setup block, and :func:`node_block` / :func:`cloud_block` turn
them into the two shapes the installer writes — ``nodes.yaml``'s node entry (through the shared
:func:`core.utils.setup_config.build_node`) and ``plugins/cloud.yaml``'s ``DEFAULT.dcs-ban`` /
``DEFAULT.discord-ban`` pair. Nothing here writes a file: the tour's draft
(:mod:`services.webservice.setup.state`) is the one destination, and the Review step builds the real
configuration from these answers.

WHAT IS NEVER DONE HERE. No download, no install (an extension's ``do_install`` is not called), no
``MissionScripting.lua`` edit, no database write. A detected path is offered, a typed path is
checked for existence, and nothing else touches the machine.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass
from typing import Mapping

from core import utils
from core.utils import setup_config

__all__ = [
    "BASE_LISTEN_PORT",
    "DEFAULT_LISTEN_PORT",
    "MAX_LISTEN_PORT",
    "Detection",
    "NodeAnswers",
    "detect_candidates",
    "detect_public_ip",
    "detect_upnp",
    "detection_for",
    "cached_detection",
    "parse_listen_port",
    "next_listen_port",
    "next_free_listen_port",
    "port_is_free",
    "answers_from_mapping",
    "non_secret_document",
    "node_block",
    "cloud_block",
]

log = logging.getLogger(__name__)

#: The installer's listen-port derivation: the first free node port is its default, and an
#: existing node's port (or this base plus its position) is the next one's lower bound
#: (``install.py``: ``max(...) + 1 if nodes else 10042``).
BASE_LISTEN_PORT = 10041
DEFAULT_LISTEN_PORT = 10042
MAX_LISTEN_PORT = 65535


@dataclass(frozen=True)
class Detection:
    """What the machine answered, once, and read by every render of the step.

    ``candidates`` are ``(name, path)`` pairs, in the installer's own order; ``public_ip`` is the
    address the static-IP question names, or ``None`` when nothing answered; ``upnp`` is the
    node's ``use_upnp`` fact.
    """

    candidates: tuple[tuple[str, str], ...] = ()
    public_ip: str | None = None
    upnp: bool = False


# --------------------------------------------------------------------------------------- the probes

def detect_candidates() -> list[tuple[str, str]]:
    """The DCS World installations found on this host, as ``(name, path)`` pairs.

    Reuses the installer's own detection; a failure to read the registry answers with an empty list
    so the step falls back to the typed path instead of failing to render.
    """
    try:
        return [(str(name), str(path)) for name, path in utils.detect_dcs_installation()]
    except Exception as ex:  # noqa: BLE001 - a probe never breaks a render
        log.warning("Setup tour: DCS detection failed (%s); offering the typed path only.", ex)
        return []


async def detect_public_ip() -> str | None:
    """The public address, or ``None`` when it could not be retrieved (no network, timeout)."""
    try:
        return await utils.get_public_ip()
    except Exception as ex:  # noqa: BLE001 - an offline host still walks the tour
        log.info("Setup tour: the public IP could not be retrieved (%s); the static-IP question "
                 "is not shown.", ex)
        return None


def detect_upnp() -> bool:
    """Whether a UPnP gateway is available on this network (the node's ``use_upnp`` fact)."""
    try:
        return bool(utils.is_upnp_available())
    except Exception as ex:  # noqa: BLE001 - a probe never breaks a render
        log.info("Setup tour: the UPnP probe failed (%s); use_upnp stays false.", ex)
        return False


async def detection_for(app) -> Detection:
    """The detection, computed ONCE per application and cached on its state.

    The public-IP probe is a network call and the UPnP probe touches the LAN, so both run once and
    every later render reads the same answer. ``app.state.setup_node_detection`` is the cache; a
    test may seed it to keep a render off the network.
    """
    cached = getattr(app.state, "setup_node_detection", None)
    if isinstance(cached, Detection):
        return cached
    detection = Detection(candidates=tuple(detect_candidates()),
                          public_ip=await detect_public_ip(),
                          upnp=detect_upnp())
    app.state.setup_node_detection = detection
    return detection


def cached_detection(app) -> Detection:
    """The cached detection, or an empty one when nothing has been probed yet."""
    cached = getattr(app.state, "setup_node_detection", None)
    return cached if isinstance(cached, Detection) else Detection()


# ------------------------------------------------------------------------------------- the answers

@dataclass
class NodeAnswers:
    """Step 4's answers: the listen port, the DCS decision (its installation and auto-update) and
    the two banlist opt-ins.

    Defaults are the installer's: both banlists on, DCS auto-update on, no DCS decline, UPnP from
    detection.
    """

    listen_port: int = DEFAULT_LISTEN_PORT
    use_upnp: bool = False
    #: the explicit "Continue without a DCS installation" answer
    no_dcs: bool = False
    #: the DCS radio the operator picked: a candidate's index as a digit string, ``"other"`` for
    #: the typed-path row, or ``None`` when nothing has been chosen yet. Stored so the picked row
    #: (and its path field) survives a re-render, on its own and without a path.
    dcs_choice: str | None = None
    #: the chosen DCS World installation path (a detected candidate or a typed one)
    dcs_installation: str | None = None
    #: the DCS auto-update answer (the installer's default is on). Read only when a DCS
    #: installation is configured — ``build_node`` writes no ``DCS`` block without one.
    dcs_autoupdate: bool = True
    #: the detected address, written only when the operator answered the static-IP question yes
    public_ip: str | None = None
    dgsa_dcs: bool = True
    dgsa_discord: bool = True

    @property
    def has_dcs(self) -> bool:
        """Whether a DCS installation was configured (decides whether DCS servers are asked for)."""
        return not self.no_dcs

    @property
    def dcs_path(self) -> str | None:
        """The path the node should use, or ``None`` when the installation was declined."""
        return None if self.no_dcs else self.dcs_installation


def parse_listen_port(value) -> int | None:
    """*value* as a valid TCP port, or ``None`` when it is not one."""
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return port if 0 < port < MAX_LISTEN_PORT + 1 else None


def next_listen_port(existing=None) -> int:
    """The installer's next listen port: one above the highest existing node's, else the default."""
    ports = [int(node.get("listen_port", BASE_LISTEN_PORT + index))
             for index, node in enumerate(existing or ())]
    return max(ports) + 1 if ports else DEFAULT_LISTEN_PORT


def port_is_free(port: int, *, host: str = "127.0.0.1") -> bool:
    """Whether ``port`` can be bound on *host*. A probe failure answers "free" (never a block)."""
    try:
        return bool(utils.is_port_free(host, int(port)))
    except Exception as ex:  # noqa: BLE001 - a probe never breaks a render
        log.info("Setup tour: the port probe failed for %s:%s (%s).", host, port, ex)
        return True


def next_free_listen_port(existing=None, *, attempts: int = 50) -> int:
    """The installer's next port, advanced past any that is already bound on this host."""
    port = next_listen_port(existing)
    for _ in range(max(1, attempts)):
        if port_is_free(port):
            return port
        port += 1
    return port


def answers_from_mapping(data: Mapping | None) -> NodeAnswers:
    """Build the answers from the draft's ``node`` block (missing keys get the installer's
    defaults)."""
    data = data if isinstance(data, Mapping) else {}
    choice = data.get("dcs_choice")
    return NodeAnswers(
        listen_port=parse_listen_port(data.get("listen_port")) or DEFAULT_LISTEN_PORT,
        use_upnp=bool(data.get("use_upnp")),
        no_dcs=bool(data.get("no_dcs")),
        dcs_choice=str(choice) if choice is not None else None,
        dcs_installation=str(data["dcs_installation"]) if data.get("dcs_installation") else None,
        dcs_autoupdate=bool(data.get("dcs_autoupdate", True)),
        public_ip=str(data["public_ip"]) if data.get("public_ip") else None,
        dgsa_dcs=bool(data.get("dgsa_dcs", True)),
        dgsa_discord=bool(data.get("dgsa_discord", True)),
    )


def non_secret_document(answers: NodeAnswers) -> dict:
    """The draft-writable part of the answers (this step holds no secret)."""
    return {
        "listen_port": answers.listen_port,
        "use_upnp": answers.use_upnp,
        "no_dcs": answers.no_dcs,
        "dcs_choice": answers.dcs_choice,
        "dcs_installation": answers.dcs_installation,
        "dcs_autoupdate": answers.dcs_autoupdate,
        "public_ip": answers.public_ip,
        "dgsa_dcs": answers.dgsa_dcs,
        "dgsa_discord": answers.dgsa_discord,
    }


# --------------------------------------------------------------------------------- the config shapes

def node_block(answers: NodeAnswers) -> dict:
    """The ``nodes.yaml`` entry for this node, built by the installer's own builder.

    Only the keys this step owns are passed: the database URL belongs to another step and is
    omitted here exactly as a node without it is. The DCS auto-update answer rides the DCS block
    and is read only when a DCS installation is set — with none, ``build_node`` writes no ``DCS``
    block and the answer is dropped.
    """
    return setup_config.build_node(listen_port=int(answers.listen_port),
                                   use_upnp=bool(answers.use_upnp),
                                   public_ip=answers.public_ip,
                                   dcs_installation=answers.dcs_path,
                                   dcs_autoupdate=bool(answers.dcs_autoupdate))


def cloud_block(answers: NodeAnswers) -> dict | None:
    """The DGSA opt-in pair for ``plugins/cloud.yaml``'s ``DEFAULT`` block, or ``None`` when both
    are off (the installer writes no cloud block for a double no)."""
    if not (answers.dgsa_dcs or answers.dgsa_discord):
        return None
    return {"dcs": bool(answers.dgsa_dcs), "discord": bool(answers.dgsa_discord)}
