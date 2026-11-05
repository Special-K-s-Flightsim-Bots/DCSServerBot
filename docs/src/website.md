# The website

The bot serves a website of its own. It is part of the **WebService**, switched on with
`frontend: true` in `config/services/webservice.yaml`, and it has a login of its own — which is what
makes a non-Discord installation fully operable.

What is behind that login:

- a dashboard with every server, its state, players and uptime
- a server page: start, stop, restart, update, live state, missions
- players and their per-player statistics, bans, and the audit log
- the node's settings, its instances, mods and the music library
- the configuration pages, which write the same YAML files you would edit by hand
- plugin pages: plugins register their own through the WebService, so the menu grows with the
  plugins you enable

## Mission list

![The mission list of one server](assets/images/mission-list.png){ loading=lazy }

Choose the mission that starts next, order the rotation, upload new `.miz` files and remove ones you
no longer fly.

## Scheduler

![The scheduler wizard, step 1](assets/images/scheduler.png){ loading=lazy }

The wizard builds the schedule you would otherwise write by hand in `config/plugins/scheduler.yaml`:
a timezone, a table of hours against days, and for every cell whether the server must run (`Y`), must
be off (`N`) or stays up until the last player leaves (`P`). Five steps, then it saves the file.

## Configuration pages

A save tells you what it costs before you make it: a plugin configuration reloads that plugin in
place and nothing restarts, while a change to `main.yaml`, `nodes.yaml`, `servers.yaml` or a service
configuration restarts the affected node. [Configuration](configure.md) has the full table.

!!! note
    The website listens on the machine the bot runs on. If you reach it from outside, put it behind a
    reverse proxy with TLS — it is an admin interface, not a public page.

## Setting it up

Two step-by-step guides carry it end to end: the session secret, the login you choose (Discord or
local users), and putting it on the internet behind HTTPS with nginx or Caddy.

- [Web frontend on Linux](guides/webservice-frontend-linux.md) — Debian/Ubuntu or Fedora/RHEL
- [Web frontend on Windows](guides/webservice-frontend-windows.md) — nginx with win-acme, or Caddy
