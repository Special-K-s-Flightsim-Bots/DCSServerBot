import asyncio
import ipaddress
import logging
import os
import uvicorn

from contextlib import suppress
from core import Service, ServiceRegistry, NodeImpl, DEFAULT_TAG, Port, PortType
from fastapi import FastAPI, HTTPException, Depends
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from pathlib import Path
from services.servicebus import ServiceBus
from services.webservice.shell import create_app, frontend_enabled, install_shell
from starlette.requests import Request
from typing_extensions import override
from uvicorn import Config

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


@ServiceRegistry.register(master_only=True, depends_on=[ServiceBus])
class WebService(Service):

    def __init__(self, node: NodeImpl):
        super().__init__(node)
        cfg = self.get_config()
        if not cfg:
            old_config = os.path.join(self.node.config_dir, 'plugins', 'restapi.yaml')
            if os.path.exists(old_config):
                self.install()
                self.locals = self.read_locals()
                cfg = self.get_config()

        self.task = None
        self.server = None
        self.ui = None
        #: the upgrade poll's task — created in ``start()``, cancelled in ``stop()``
        self.upgrade_task = None
        if cfg:
            self.app = self._create_app()
            self.config = Config(
                app=self.app,
                host=cfg.get('listen', '0.0.0.0'),
                port=cfg.get('port', 9876),
                workers=1,
                log_level=logging.WARNING,
                log_config=None,
                use_colors=False,
                lifespan="off"
            )
            self.config.extra_kwargs = {"backlog": 2048}
            self.server = uvicorn.Server(config=self.config)
        else:
            self.app = None

    def install(self):
        old_config = os.path.join(self.node.config_dir, 'plugins', 'restapi.yaml')
        new_config = os.path.join(self.node.config_dir, 'services', 'webservice.yaml')
        if os.path.exists(old_config) and not os.path.exists(new_config):
            old = yaml.load(Path(old_config).read_text(encoding='utf-8'))
            new = old.copy()
            if 'prefix' in old.get(DEFAULT_TAG, {}):
                old[DEFAULT_TAG] = {
                    'prefix': new[DEFAULT_TAG].pop('prefix')
                }
            else:
                old = {}

            if old:
                with open(old_config, mode='w', encoding='utf-8') as old_out:
                    yaml.dump(old, old_out)
            else:
                os.remove(old_config)
            if new:
                with open(new_config, mode='w', encoding='utf-8') as new_out:
                    yaml.dump(new, new_out)

    def _create_app(self) -> FastAPI:
        """Create the FastAPI app and install the admin web shell on it.

        Called from both app-creation paths: __init__ (config exists) and start() (after stop()
        dropped the app, e.g. across a master/agent switch). The shell install is idempotent, the
        app object is not — anything registered in __init__ only would be gone after a takeover.
        """
        app = create_app()
        # debug endpoints first: they are created through the app, so they carry the access gate,
        # and they keep their own local-networks-only dependency (which the gate recognises as a
        # route that guards itself)
        if self.get_config().get('debug', False):
            self.add_debug_routes(app)
        if frontend_enabled(self.get_config()):
            self.ui = install_shell(app, self.node, self.get_config())
        else:
            # ``frontend: false`` — install NO shell at all: no page route, no ``/auth/*``, no
            # session middleware, no shell assets and no UI background work. The ``auth:`` block is
            # not evaluated (the whole install is skipped), so a stale login block cannot hold an
            # API-only operator hostage, and the REST API is untouched. The refusal handlers, the
            # access gate and the debug endpoints come from ``create_app`` and stay as they are.
            self.ui = None
            self.log.info(f"{self.name}: The admin frontend is disabled (frontend: false); the "
                          f"auth block was skipped and this service runs as a REST API only.")
        return app

    def add_debug_routes(self, app: FastAPI):
        self.log.warning("WebService: Debug is enabled, you might expose your API functions!")

        # enable debug logging for FastAPI
        logging.getLogger("fastapi").setLevel(logging.DEBUG)
        logging.getLogger("uvicorn").setLevel(logging.DEBUG)
        logging.getLogger("uvicorn.access").setLevel(logging.DEBUG)

        def local_networks_only(request: Request):
            if not request.client:
                raise HTTPException(status_code=404, detail="Not found.")

            try:
                client_ip = ipaddress.ip_address(request.client.host)
            except ValueError:
                raise HTTPException(status_code=404, detail="Not found.")

            if not client_ip.is_loopback and not client_ip.is_private:
                raise HTTPException(status_code=404, detail="Not found.")

        # Enable OpenAPI schema
        app.add_api_route(
            "/openapi.json",
            lambda: get_openapi(
                title="DCSServerBot REST API",
                version=f"{self.node.bot_version}.{self.node.sub_version}",
                description="REST functions to be used for DCSServerBot.",
                routes=app.routes,
            ),
            include_in_schema=False,
            dependencies=[Depends(local_networks_only)]
        )

        # Enable Swagger UI
        app.add_api_route(
            "/docs",
            lambda: get_swagger_ui_html(
                openapi_url="/openapi.json",
                title="DCSServerBot REST API - Swagger UI",
            ),
            include_in_schema=False,
            dependencies=[Depends(local_networks_only)]
        )

        # Enable ReDoc
        app.add_api_route(
            "/redoc",
            lambda: get_redoc_html(
                openapi_url="/openapi.json",
                title="DCSServerBot REST API - ReDoc",
            ),
            include_in_schema=False,
            dependencies=[Depends(local_networks_only)]
        )

    @override
    async def start(self):
        if not self.server:
            return

        await super().start()

        if not self.app:
            self.app = self._create_app()
            self.config.app = self.app

        # run server in the background but guard against SystemExit
        async def run_server():
            for i in range(5):
                try:
                    await self.server.serve()
                    break
                except (SystemExit, OSError) as ex:
                    if ((isinstance(ex, OSError) and ex.errno in [10013, 10048]) or
                            (isinstance(ex, SystemExit) and ex.code == 1)):
                        if i < 4:
                            await asyncio.sleep(1)
                            continue
                        self.log.error(f"{self.name}: Could not bind to port {self.config.port} after {i} retries.")
                    else:
                        self.log.exception(f"{self.name}: Uvicorn crashed: {ex}")
                    break
                except asyncio.CancelledError:
                    break
                except Exception as ex:
                    self.log.exception(f"{self.name}: Uvicorn crashed: {ex}")
                    break

        self.task = asyncio.create_task(run_server())
        # THE UPGRADE POLL: a background check of every node the console knows, from the
        # node's OWN API (``Node.upgrade_pending()``) — never from a page render. The four triggers
        # live in ``services/webservice/upgrade.py`` (start = the poller's first, full sweep);
        # "immediately after an Upgrade is accepted" is the node route's own call to
        # ``upgrade.check_node``, and a node coming online is a newcomer the sampler notices.
        #
        # It is UI BACKGROUND WORK, so with the frontend off it must not run at all: no node is
        # ever polled for a console nobody serves (``frontend_enabled`` is the one rule the app
        # factory reads too).
        if frontend_enabled(self.get_config()):
            self.upgrade_task = asyncio.create_task(self._poll_upgrades())

    async def _poll_upgrades(self) -> None:
        """Sample the cluster for pending upgrades and refresh the cache the render reads.

        The task lives for the service's lifetime and does NOTHING but the poll: each tick asks
        :func:`services.webservice.upgrade.console_nodes` for the current registry and lets the
        poller decide which nodes to check (newcomers, and every node on a due sweep). Every failure
        is contained — a poll that raises must never take the webservice down with it, and the
        cache simply keeps its last honest value.
        """
        from services.webservice import upgrade as upgrade_signal

        poller = upgrade_signal.UpgradePoller()
        while True:
            try:
                await poller.tick(upgrade_signal.console_nodes())
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.exception("WebService: the upgrade check sweep failed.")
            await asyncio.sleep(poller.sample_seconds)

    @override
    async def stop(self):
        if self.upgrade_task:
            # the poll runs for the service's lifetime: end it with the service, and wait for the
            # cancellation so a master/agent switch cannot leave a poller sampling a stopped app.
            self.upgrade_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.upgrade_task
            self.upgrade_task = None
        if self.task:
            self.server.should_exit = True
            # Explicitly trigger the shutdown of the uvicorn server
            if hasattr(self.server, 'force_exit'):
                self.server.force_exit = True

            # Give uvicorn a moment to shut down gracefully, then cancel if it hangs
            try:
                await asyncio.wait_for(self.task, timeout=5.0)
            except asyncio.TimeoutError:
                self.log.warning(f"{self.name}: Uvicorn did not stop gracefully, cancelling task.")
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
            finally:
                # Ensure sockets are closed to free the port
                if self.server.started:
                    for server in self.server.servers:
                        server.close()
                self.server = uvicorn.Server(config=self.config)
                self.task = None
                self.app = None
                self.ui = None
        await super().stop()

    @override
    def get_ports(self) -> dict[str, Port]:
        return {"WebService": Port(self.get_config().get('port', 9876), PortType.TCP, public=True)}
