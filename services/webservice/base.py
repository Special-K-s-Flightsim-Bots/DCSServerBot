import os

from fastapi import FastAPI, APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


class WebServiceBase:
    def __init__(self, app: FastAPI, base_path: str, webroot: str = None,
                 has_static: bool = False, has_templates: bool = False):
        self.app = app
        self.base_path = base_path
        self.router = APIRouter(prefix=base_path)
        if has_static:
            self.app.mount('/static', StaticFiles(directory=os.path.join(webroot, 'static')), name="static")
        if has_templates:
            self.templates = Jinja2Templates(directory=os.path.join(webroot, 'templates'))
        self.setup_routes()
        self.app.include_router(self.router)

    def setup_routes(self):
        ...
