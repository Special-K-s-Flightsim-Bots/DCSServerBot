import asyncio
import logging

import uvicorn
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from uvicorn import Config

from core import Service, ServiceRegistry, NodeImpl
from services.servicebus import ServiceBus


@ServiceRegistry.register(master_only=True, depends_on=[ServiceBus])
class WebService(Service):

    def __init__(self, node: NodeImpl):
        super().__init__(node)
        cfg = self.get_config()
        if cfg:
            self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
            self.config = Config(app=self.app, host=cfg.get('listen', '0.0.0.0'), port=cfg.get('port', 9876),
                                 log_level=logging.ERROR, use_colors=False)
            self.server: uvicorn.Server = uvicorn.Server(config=self.config)
            self.task = None

            # add debug endpoints
            if cfg.get('debug', False):
                self.add_debug_routes()
        else:
            self.app = None

    def add_debug_routes(self):
        self.log.warning("WebService: Debug is enabled, you might expose your API functions!")

        # Enable OpenAPI schema
        self.app.add_api_route("/openapi.json",
                               lambda: get_openapi(
                                   title="DCSServerBot REST API",
                                   version=self.node.bot_version,
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
