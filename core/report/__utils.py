import asyncio
import importlib
import inspect

from core import utils, Status
from core.report.errors import ValueNotInRange
from psycopg.rows import dict_row
from typing import Any, Mapping, Callable

__all__ = [
    "parse_params",
    "parse_input"
]


def _load_call(call_path: str) -> Callable[..., Any]:
    """
    Import a module and return the callable identified by *call_path*.

    Parameters
    ----------
    call_path : str
        Dotted path – e.g. ``"utils.get_running_campaign"``.

    Returns
    -------
    function
        The callable retrieved from the module.

    Raises
    ------
    ValueError
        If the path does not contain a module component.
    ImportError
        If the module cannot be imported.
    AttributeError
        If the attribute is missing or not callable.
    """
    module_path, _, attr_name = call_path.rpartition('.')
    if not module_path:
        raise ValueError(f"Invalid call path: {call_path!r}")

    module = importlib.import_module(module_path)
    func = getattr(module, attr_name)

    if not callable(func):
        raise TypeError(
            f"Attribute {attr_name!r} of module {module_path!r} is not callable"
        )
    return func


def _filter_kwargs(func: Callable[..., Any], kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """
    Return a new dict containing only the keyword arguments that *func* can accept.

    If *func* accepts **kwargs (VAR_KEYWORD) we simply return *kwargs* unchanged.

    Parameters
    ----------
    func : callable
        Function to inspect.
    kwargs : Mapping
        Candidate keyword arguments.

    Returns
    -------
    dict
        Subset of *kwargs* that matches the function signature.
    """
    sig = inspect.signature(func)

    # 2a.  Accept everything if the function has **kwargs
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return dict(kwargs)

    # 2b.  Otherwise keep only the parameters that are keyword‑able
    allowed_keys = {
        p.name
        for p in sig.parameters.values()
        if p.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    return {k: v for k, v in kwargs.items() if k in allowed_keys}


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
                    new_args[param['name']] = utils.format_string(value, '_ _', **new_args) if isinstance(value, str) else value
            elif 'value' in param:
                value = param['value']
                new_args[param['name']] = utils.format_string(value, '_ _', **new_args) if isinstance(value, str) else value
            elif 'default' in param:
                new_args[param['name']] = param['default']
            elif 'call' in param:
                func = _load_call(param['call'])
                if 'params' in param:
                    for k, v in param['params'].items():
                        new_args[k] = utils.format_string(v, '_ _', **new_args) if isinstance(v, str) else v
                filtered_kwargs = _filter_kwargs(func, new_args)
                try:
                    if inspect.iscoroutinefunction(func):
                        new_args[param['name']] = await func(**filtered_kwargs)
                    else:
                        new_args[param['name']] = await asyncio.to_thread(func, **filtered_kwargs)
                except Exception:
                    new_args[param['name']] = None
        elif 'sql' in param:
            async with self.apool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(utils.format_string(param['sql'], **new_args), new_args)
                    if cursor.rowcount == 1:
                        for name, value in (await cursor.fetchone()).items():
                            new_args[name] = value
        elif 'callback' in param:
            server = new_args['server']
            if server.status not in [Status.PAUSED, Status.RUNNING]:
                new_args[param['callback']] = None
            try:
                data: dict = await new_args['server'].send_to_dcs_sync({
                    "command": "getVariable", "name": param['callback']
                })
                if 'value' in data:
                    new_args[param['callback']] = data['value']
            except (TimeoutError, asyncio.TimeoutError):
                new_args[param['callback']] = None
        elif 'event' in param:
            server = new_args['server']
            if server.status in [Status.PAUSED, Status.RUNNING]:
                try:
                    cmd = {
                        "command": param['event']
                    }
                    if 'params' in param:
                        cmd |= param['params']
                    data: dict = await new_args['server'].send_to_dcs_sync(cmd)
                    if data:
                        new_args[param['event']] = data
                except (TimeoutError, asyncio.TimeoutError):
                    new_args[param['event']] = None
            else:
                new_args[param['event']] = None

    return new_args
