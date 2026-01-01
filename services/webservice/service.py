import asyncio
import logging
import os
import uvicorn

from contextlib import suppress
from core import Service, ServiceRegistry, NodeImpl, DEFAULT_TAG, Port, PortType
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from pathlib import Path
from services.servicebus import ServiceBus
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
        if cfg:
            self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
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
            self.server: uvicorn.Server = uvicorn.Server(config=self.config)

            # add debug endpoints
            if cfg.get('debug', False):
                self.add_debug_routes()
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

    def add_debug_routes(self):
        self.log.warning("WebService: Debug is enabled, you might expose your API functions!")

        # enable debug logging for FastAPI
        logging.getLogger("fastapi").setLevel(logging.DEBUG)
        logging.getLogger("uvicorn").setLevel(logging.DEBUG)
        logging.getLogger("uvicorn.access").setLevel(logging.DEBUG)

        # Enable OpenAPI schema
        self.app.add_api_route("/openapi.json",
                               lambda: get_openapi(
                                   title="DCSServerBot REST API",
                                   version=f"{self.node.bot_version}.{self.node.sub_version}",
                                   description="REST functions to be used for DCSServerBot.",
                                   routes=self.app.routes,
                               ),
                               include_in_schema=False
                               )

        # Enable Swagger UI
        self.app.add_api_route("/docs",
                               lambda: get_swagger_ui_html(
                                   openapi_url="/openapi.json",
                                   title="DCSServerBot REST API - Swagger UI",
                               ),
                               include_in_schema=False
                               )

        # Enable ReDoc
        self.app.add_api_route("/redoc",
                               lambda: get_redoc_html(
                                   openapi_url="/openapi.json",
                                   title="DCSServerBot REST API - ReDoc",
                               ),
                               include_in_schema=False
                               )

    @override
    async def start(self):
        if not self.server:
            return

        await super().start()

        if not self.app:
            self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
            self.config.app = self.app

            if self.get_config().get('debug', False):
                self.add_debug_routes()

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
                        self.log.error(f"{self.name}: Could not bind to port {self.config.port} after retries. {ex}")
                    else:
                        self.log.exception(f"{self.name}: Uvicorn crashed: {ex}")
                    break
                except asyncio.CancelledError:
                    break
                except Exception as ex:
                    self.log.exception(f"{self.name}: Uvicorn crashed: {ex}")
                    break

        self.task = asyncio.create_task(run_server())

    @override
    async def stop(self):
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
        await super().stop()

    @override
    def get_ports(self) -> dict[str, Port]:
        return {"WebService": Port(self.get_config().get('port', 9876), PortType.TCP, public=True)}
