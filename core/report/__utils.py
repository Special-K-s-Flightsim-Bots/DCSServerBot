import asyncio
from core import utils, Status
from core.report.errors import ValueNotInRange
from psycopg.rows import dict_row
from typing import Any

__all__ = [
    "parse_params",
    "parse_input"
]


def parse_params(kwargs: dict, params: tuple[dict, list]):
    new_args = kwargs.copy()
    if isinstance(params, dict):
        for key, value in params.items():
            new_args[key] = value
    else:
        new_args['params'] = params
    return new_args


async def parse_input(self, kwargs: dict, params: list[Any]):
    new_args = kwargs.copy()
    for param in params:
        if 'name' in param:
            if param['name'] in new_args and new_args[param['name']]:
                if 'range' in param:
                    value = new_args[param['name']] or ''
                    if value not in param['range']:
                        raise ValueNotInRange(param['name'], value, param['range'])
                elif 'value' in param:
                    value = param['value']
                    new_args[param['name']] = utils.format_string(value, '_ _', **kwargs) if isinstance(value, str) else value
            elif 'value' in param:
                value = param['value']
                new_args[param['name']] = utils.format_string(value, '_ _', **kwargs) if isinstance(value, str) else value
            elif 'default' in param:
                new_args[param['name']] = param['default']
        elif 'sql' in param:
            async with self.apool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(utils.format_string(param['sql'], **kwargs), kwargs)
                    if cursor.rowcount == 1:
                        for name, value in (await cursor.fetchone()).items():
                            new_args[name] = value
        elif 'callback' in param:
            server = kwargs['server']
            if server.status not in [Status.PAUSED, Status.RUNNING]:
                new_args[param['callback']] = None
            try:
                data: dict = await kwargs['server'].send_to_dcs_sync({
                    "command": "getVariable", "name": param['callback']
                })
                if 'value' in data:
                    new_args[param['callback']] = data['value']
            except (TimeoutError, asyncio.TimeoutError):
                new_args[param['callback']] = None
        elif 'event' in param:
            server = kwargs['server']
            if server.status in [Status.PAUSED, Status.RUNNING]:
                try:
                    cmd = {
                        "command": param['event']
                    }
                    if 'params' in param:
                        cmd |= param['params']
                    data: dict = await kwargs['server'].send_to_dcs_sync(cmd)
                    if data:
                        new_args[param['event']] = data
                except (TimeoutError, asyncio.TimeoutError):
                    new_args[param['event']] = None
            else:
                new_args[param['event']] = None
        return new_args
