# Security policy

## Supported versions

Two versions are supported:

| Version | Branch | Supported |
|:--|:--|:--|
| Latest release (currently `v3.0.5.2`) | `master` | ✅ |
| Newest code, not released yet | `development` | ✅ |
| Any older release | — | ❌ |

Security fixes are made on `development` and ship with the next release. An issue of high severity is
released as a hotfix on `master` ahead of the normal release cycle, at the maintainers' discretion.
Older releases do not receive backports, so a report is only accepted against the versions above.

- Update to the latest release with `update.cmd` (Windows) or `./update.sh` (Linux).
- Switch between the release and the development branch with `switch-branch.cmd` or `./switch-branch.sh`.

## Reporting a vulnerability

Report it privately — not in a public issue, and not in a normal Discord channel. Two channels are open
for this:

**GitHub advisory** — preferred when you can attach logs, code or a patch:

1. Open <https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/security/advisories/new>
   (or the repository's **Security** tab → **Report a vulnerability**).
2. Describe the problem, how to trigger it, and what an attacker gets out of it.
3. Submit — the report is visible to the maintainers only.

**Discord ticket** — equivalent to a private conversation with the maintainers: open a ticket in the
[Discord server](https://discord.gg/h2zGDH9szZ). A ticket is a private channel between you and the staff,
not a public room. Details belong in the ticket, not in the open server.

Either way, do not open a public issue or pull request for a security problem, and do not post it
publicly before a fix has been released.

## What to include

- The version you run (`version.py`, or the release tag / commit you installed).
- Which parts are involved: the bot itself, the website, a plugin, an extension, or a script.
- Steps to reproduce, ideally from a clean install, and how reliable the reproduction is.
- The impact as you see it: what data or access an attacker can reach.
- Any log output or request that shows it. Remove your Discord token, database password, session
  secret and IP addresses first.

## What happens next

- You get an acknowledgement where you reported it — on the advisory or in the ticket — normally within
  five days.
- The report is reproduced and confirmed against the version named above.
- The fix is prepared on `development`, or released as a hotfix on `master` first if the severity
  warrants it, and coordinated with you. The advisory is published with the release that carries the
  fix, and credit goes to you unless you ask to stay anonymous.
- If the report turns out not to be a security issue, you are told so, with the reason, and pointed
  to the right place.

## Not a security issue

- **Bugs and crashes** — open a [GitHub issue](https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/issues).
- **Setup and configuration questions** — ask in the [Discord server](https://discord.gg/h2zGDH9szZ).
- **Hardening your own installation** (reverse proxy, TLS, ports, file permissions) — see the
  [website guides](https://special-k-s-flightsim-bots.github.io/DCSServerBot/guides/webservice-frontend-linux/).
