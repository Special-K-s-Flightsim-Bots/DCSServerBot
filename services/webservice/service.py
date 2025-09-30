import asyncio
import logging
import os
import uvicorn

from core import Service, ServiceRegistry, NodeImpl, DEFAULT_TAG
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from pathlib import Path
from services.servicebus import ServiceBus
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
                workers=4,
                log_level=logging.WARNING,
                log_config=None,
                use_colors=False
            )
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

    async def start(self):
        if self.app and self.server:
            await super().start()
            self.task = asyncio.create_task(self.server.serve())

    async def stop(self):
        if self.task:
            self.server.should_exit = True
            await self.task
            await super().stop()
