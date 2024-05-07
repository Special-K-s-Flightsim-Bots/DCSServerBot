from __future__ import annotations

import asyncio
import json

from abc import ABC, abstractmethod
from asyncio import DatagramProtocol
from asyncpg import connect, Connection

from copy import deepcopy
from core import Status
from typing import AsyncGenerator, TYPE_CHECKING

if TYPE_CHECKING:
    from services import ServiceBus
    from core import Server


class Receiver(ABC):
    def __init__(self, name: str):
        self.name = name
        self.queue = asyncio.Queue()

    @abstractmethod
    async def start(self):
        pass

    @abstractmethod
    async def stop(self):
        pass

    async def receive(self) -> AsyncGenerator[dict, None]:
        while True:
            message = await self.queue.get()
            if message is None:  # We use "None" message as signal to stop the generator
                break
            yield message


class UDPReceiver(Receiver, DatagramProtocol):
    def __init__(self, name: str, host: str, port: int):
        super().__init__(name)
        self.host = host
        self.port = port
        self.transport = None
        self.loop = asyncio.get_event_loop()

    async def start(self):
        await self.loop.create_datagram_endpoint(lambda: self, local_addr=(self.host, self.port))

    def connection_made(self, transport):
        self.transport = transport

    async def stop(self):
        self.transport.close()
        await self.queue.put(None)

    def datagram_received(self, data, addr):
        try:
            self.queue.put_nowait(json.loads(data))
        except Exception as ex:
            print(ex)


class DBReceiver(Receiver):
    def __init__(self, name: str, url: str):
        super().__init__(name)
        self.url = url
        self.connection = None

    async def start(self):
        self.connection: Connection = await connect(self.url)
        await self.connection.set_type_codec('json', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
        await self.connection.add_listener(self.name, self.notify_event)

    async def stop(self):
        await self.connection.remove_listener(self.name, self.notify_event)
        await self.connection.close()
        await self.queue.put(None)

    async def notify_event(self, connection, pid, channel, payload):
        await self.queue.put(json.loads(payload))


class DCSBus:
    def __init__(self, bus: ServiceBus):
        self.receivers = {}
        self.futures = {}
        self.bus = bus
        self.log = self.bus.log

    async def register_receiver(self, receiver):
        self.receivers[receiver.name] = receiver
        future = asyncio.create_task(self._read_receiver(receiver))
        await receiver.start()
        self.futures[receiver.name] = future

    async def unregister_receiver(self, receiver_name: str):
        receiver = self.receivers.pop(receiver_name, None)
        if receiver:
            await receiver.stop()
        future = self.futures.pop(receiver_name, None)
        if future:
            future.cancel()

    async def _read_receiver(self, receiver):
        async for data in receiver.receive():
            try:
                if 'server_name' not in data:
                    self.log.warning('Message without server_name received: {}'.format(data))
                    return
                server_name = data['server_name']
                self.log.debug('{}->HOST: {}'.format(server_name, json.dumps(data)))
                server: Server = self.bus.servers.get(server_name)
                if not server:
                    self.log.debug(
                        f"Command {data['command']} received for unregistered server {server_name}, ignoring.")
                    return
                if 'channel' in data and data['channel'].startswith('sync-'):
                    if data['channel'] in server.listeners:
                        f = server.listeners.get(data['channel'])
                        if f and not f.done():
                            await asyncio.to_thread(f.set_result, data)
                        if data['command'] not in ['registerDCSServer', 'getMissionUpdate']:
                            return
                command = data['command']
                if command == 'registerDCSServer':
                    if not server.is_remote:
                        if not self.bus.register_server(data):
                            self.log.error(f"Error while registering server {server.name}.")
                            return
                        if not self.bus.master:
                            self.log.debug(f"Registering server {server.name} on Master node ...")
                elif server.status == Status.UNREGISTERED:
                    self.log.debug(
                        f"Command {command} received for unregistered server {server.name}, ignoring.")
                    continue
                if self.bus.master:
                    tasks = [
                        listener.processEvent(command, server, deepcopy(data))
                        for listener in self.bus.eventListeners
                        if listener.has_event(command)
                    ]
                    await asyncio.gather(*tasks)
                else:
                    self.bus.send_to_node(data)
            except Exception as ex:
                self.log.exception(ex)
            finally:
                receiver.queue.task_done()
