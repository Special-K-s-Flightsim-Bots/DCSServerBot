# Developing for DCSServerBot

DCSServerBot is built to be extended. Four kinds of component exist, and each has its own contract,
its own documentation page and its own README:

| Component | What it is | Documentation |
|:----------|:-----------|:--------------|
| **Plugin** | Provides Discord or in-game commands, handles events from DCS, runs on the master node only. | [Plugins](../plugins/index.md) |
| **Service** | Runs once on each node of the cluster; some services are master-bound. | [Services](../services/index.md) |
| **Extension** | Runs for each server on the node that server runs on, with access to all its resources. | [Extensions](../extensions/index.md) |
| **Report** | The reporting framework: build your own reports or override existing ones. | [Reports](../reports/index.md) |

## Start here

1. Read the component page you want to build — each one describes lifecycle, base class and the
   hooks it expects.
2. Copy the sample: `plugins/sample` is a working plugin skeleton with a README aimed at exactly
   this.
3. Give your component a **schema** under `<kind>/<name>/schemas/<name>_schema.yaml`. The
   documentation site generates the configuration reference of every component from it, so a field
   with a `desc` and an `example` documents itself.
4. Add a `README.md` next to it. That README **is** the component's page on the site.

## Documentation rules

- The site is generated: `docs/scripts/build_docs.py` assembles the pages from the in-repo markdown,
  the schemas, the command decorators and the SQL.
- Reference pages are never written by hand. A missing description in a reference table is a missing
  `desc` in the schema — fix it there.
- The build runs with `mkdocs build --strict`, so a broken internal link fails CI.

## Sharing what you built

If your component might interest other communities, say so in the support Discord (see
[Community](../community/dgsa.md)) or open a pull request. Requirements vary: a plugin that only
works on your setup is fine to keep private.
