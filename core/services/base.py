import json
import os
from abc import ABC


class Service(ABC):
    def __init__(self, node, name: str):
        self.name = name
        self.running: bool = False
        self.node = node
        self.log = node.log
        self.pool = node.pool
        self.config = node.config
        self.locals = self.read_locals()

    async def start(self, *args, **kwargs):
        self.log.debug(f'- Starting service {self.name} ...')
        self.running = True

    async def stop(self, *args, **kwargs):
        self.running = False
        self.log.debug(f'- Service {self.name} stopped.')

    async def is_running(self) -> bool:
        return self.running

    def read_locals(self) -> dict:
        if os.path.exists(f'./config/{self.name}.json'):
            filename = f'./config/{self.name}.json'
        else:
            return {}
        self.log.debug(f'  => Reading service configuration from {filename} ...')
        with open(filename, encoding='utf-8') as file:
            return json.load(file)


class ServiceInstallationError(Exception):
    def __init__(self, service: str, reason: str):
        super().__init__(f'Service "{service.title()}" could not be installed: {reason}')
