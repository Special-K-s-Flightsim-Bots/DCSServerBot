from core import Port, PortType
from typing import Iterable

__all__ = ["generate_firewall_rules"]


def fw_rule(port: int, protocol: str, name: str, description: str) -> str:
    """
    Returns a single New-NetFirewallRule command string.
    """
    cmd = (
        f'New-NetFirewallRule '
        f'-DisplayName "{name}" '
        f'-Direction Inbound '
        f'-Action Allow '
        f'-Protocol {protocol} '
        f'-LocalPort {port} '
        f'-Profile Any '
        f'-Description "{description}"'
    )
    return cmd

def generate_firewall_rules(ports: Iterable[Port]) -> str:
    """
    Write a PowerShell script that adds inbound rules for the given ports.
    """
    lines = [
        "# ------------------------------------------------------------",
        "# Auto‑generated PowerShell script to add inbound firewall rules",
        "# Run this script **as Administrator** in PowerShell.",
        "# ------------------------------------------------------------",
        "",
        "Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process -Force",
        ""
    ]

    for p in ports:
        if p.typ is PortType.BOTH:
            # Create two separate rules
            for proto in (PortType.TCP, PortType.UDP):
                name = f"Allow {p.port}/{proto.value.lower()}"
                desc = f"Auto‑generated rule for inbound {p.port}/{proto.value.lower()}"
                lines.append(fw_rule(p.port, proto.value, name, desc))
                lines.append("")  # blank line for readability
        else:
            name = f"Allow {p.port}/{p.typ.value.lower()}"
            desc = f"Auto‑generated rule for inbound {p.port}/{p.typ.value.lower()}"
            lines.append(fw_rule(p.port, p.typ.value, name, desc))
            lines.append("")

    return "\n".join(lines)
