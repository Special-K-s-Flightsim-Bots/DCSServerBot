import datetime
import logging
import os
import re
import threading

from core.const import DEFAULT_TAG
from core.commandline import COMMAND_LINE_ARGS
from pathlib import Path
from pykwalify import partial_schemas
from pykwalify.core import Core
from pykwalify.errors import SchemaError, CoreError, PyKwalifyException
from pykwalify.rule import Rule
from typing import Any, Type, Union, Optional

logger = logging.getLogger(__name__)

Text = Union[int, str]

_types: dict[Type, str] = {
    str: "str",
    int: "int",
    float: "float",
    bool: "bool",
    datetime.datetime: "timestamp",
    datetime.date: "date",
    Text: "text"
}

__all__ = [
    "file_exists",
    "seq_or_map",
    "bool_or_map",
    "str_or_map",
    "int_or_map",
    "bool_or_list",
    "str_or_list",
    "int_or_list",
    "text_or_list",
    "int_csv_or_list",
    "str_csv_or_list",
    "check_main_structure",
    "is_node",
    "is_server",
    "validate"
]

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

# Node-global unique ports
ports: dict[str, dict[int, str]] = {}


class NodeData:
    _instance: Optional['NodeData'] = None
    _lock = threading.Lock()    # make it thread-safe

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:  # Double-checked locking pattern
                if not cls._instance:
                    cls._instance = super(NodeData, cls).__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        # Only initialize the attributes once
        try:
            config_path = Path(os.path.join(COMMAND_LINE_ARGS.config, 'nodes.yaml'))
            data = yaml.load(config_path.read_text(encoding='utf-8'))
            self._nodes: list[str] = list(data.keys())
            self._instances: dict[str, list[str]] = {
                node: list(data[node]['instances'].keys())
                for node in self._nodes
                if data[node] and data[node].get('instances')
            }
            self._all_instances: dict[str, int] = {}
            for node, instances in self._instances.items():
                for instance in instances:
                    self._all_instances[instance] = self._all_instances.get(instance, 0) + 1
        except Exception:
            raise CoreError(msg="nodes.yaml seems to be corrupt, can't initialize the node/instance-validation!")

        try:
            config_path = Path(os.path.join(COMMAND_LINE_ARGS.config, 'servers.yaml'))
            data = yaml.load(config_path.read_text(encoding='utf-8'))
            self._servers = list(data.keys())
        except Exception:
            raise CoreError(msg="servers.yaml seems to be corrupt, can't initialize the server-validation!")

    @property
    def nodes(self):
        return self._nodes

    @property
    def instances(self):
        return self._instances

    @property
    def servers(self):
        return self._servers

    @property
    def all_instances(self):
        return self._all_instances

def get_node_data() -> NodeData:
    return NodeData()

def file_exists(value, _, path):
    if path and path.split("/")[1] in [DEFAULT_TAG, COMMAND_LINE_ARGS.node]:
        if not os.path.exists(os.path.expandvars(value)):
            raise SchemaError(msg=f'File "{value}" does not exist', path=path)
    return True

def unique_port(value, _, path):
    try:
        value = int(value)
        if value < 1024 or value > 65535:
            raise ValueError
    except ValueError:
        raise SchemaError(msg=f"{value} is not a valid port", path=path)
    node = path.split("/")[1]
    if node not in ports:
        ports[node] = {}
    if value in ports[node]:
        raise SchemaError(msg=f"Port {value} is already in use in {ports[node][value]}", path=path)
    ports[node][value] = path
    return True

def _load_schema(include_name: str, path: str) -> str:
    partial_schema_rule = partial_schemas.get(include_name)
    if not partial_schema_rule:
        existing_schemas = ", ".join(sorted(partial_schemas.keys()))
        raise SchemaError(msg=f"Cannot find partial schema with name '{include_name}'. Existing partial schemas: "
                              f"'{existing_schemas}'. Path: '{path})")
    return partial_schema_rule.schema

def _validate_schema(schema, value, path):
    c = Core(source_data=value, schema_data=schema, extensions=['core/utils/validators.py'])
    try:
        c.validate()
    except SchemaError as ex:
        raise SchemaError(msg=ex.msg, path=path + '/' + ex.path.lstrip('/'))

def any_of(value, rule_obj, path):
    errors = []
    for include_name in rule_obj.schema_str.get('enum', []):
        try:
            _validate_schema(_load_schema(include_name, path), value, path)
            break
        except SchemaError as ex:
            errors.append(ex)
        except CoreError:
            pass
    else:
        msg = []
        new_path = set()
        for error in errors:
            path_part = re.findall(r"Path: '([^']*)'\.", error.msg)
            if path_part and path_part[0]:
                new_path.add(error.path + path_part[0].lstrip('/'))
            else:
                new_path.add(error.path)
            msg.append(re.sub(r"Path: '[^']*'\.", "", error.msg).strip())
        raise SchemaError(msg='\n'.join(msg), path='\n'.join(new_path))
    rule_obj.enum = None
    return True

def seq_or_map(value, rule_obj, path):
    if isinstance(value, list):
        include_name = rule_obj.schema_str.get('enum')[0]
    elif isinstance(value, dict):
        include_name = rule_obj.schema_str.get('enum')[1]
    else:
        raise SchemaError(msg=f"Value {value} is not list or dict.", path=path)
    _validate_schema(_load_schema(include_name, path), value, path)
    rule_obj.enum = None
    return True

def _scalar_or_map(t: Type, value: Any, rule_obj: Rule, path: str):
    if isinstance(value, dict):
        _validate_schema(_load_schema(rule_obj.schema_str.get('enum')[0], path), value, path)
    elif not isinstance(value, t):
        raise SchemaError(msg=f"Value {value} is not {t.__name__} or dict.", path=path)
    rule_obj.enum = None
    return True

def bool_or_map(value, rule_obj, path):
    return _scalar_or_map(bool, value, rule_obj, path)

def str_or_map(value, rule_obj, path):
    return _scalar_or_map(str, value, rule_obj, path)

def int_or_map(value, rule_obj, path):
    return _scalar_or_map(int, value, rule_obj, path)

def _scalar_or_list(t: Type, value: Any, rule_obj: Rule, path: str):
    if isinstance(value, list):
        if rule_obj.enum:
            schema = _load_schema(rule_obj.enum[0], path)
        else:
            _type = {
                'type': _types[t],
            }
            if rule_obj.nullable is not None:
                _type['nullable'] = rule_obj.nullable
            if rule_obj.pattern is not None:
                _type['pattern'] = rule_obj.pattern
            schema = {
                'type': 'seq',
                'nullable': False,
                'sequence': [
                    _type
                ]
            }
        _validate_schema(schema, value, path)
        rule_obj.pattern = None
        rule_obj.patten_regexp = None
    elif not isinstance(value, t):
        raise SchemaError(msg=f"Value {value} is not {t.__name__} or list.", path=path)
    rule_obj.enum = None
    return True

def bool_or_list(value, rule_obj, path):
    return _scalar_or_list(bool, value, rule_obj, path)

def str_or_list(value, rule_obj, path):
    return _scalar_or_list(str, value, rule_obj, path)

def int_or_list(value, rule_obj, path):
    return _scalar_or_list(int, value, rule_obj, path)

def text_or_list(value, rule_obj, path):
    return _scalar_or_list(Text, value, rule_obj, path)

def _csv_or_list(t, value, rule_obj, path):
    if isinstance(value, list):
        if rule_obj.enum:
            schema = _load_schema(rule_obj.enum[0], path)
        else:
            _type = {
                'type': _types[t],
            }
            if rule_obj.nullable is not None:
                _type['nullable'] = rule_obj.nullable
            if rule_obj.pattern is not None:
                _type['pattern'] = rule_obj.pattern
            schema = {
                'type': 'seq',
                'nullable': False,
                'sequence': [
                    _type
                ]
            }
        _validate_schema(schema, value, path)
        rule_obj.pattern = None
        rule_obj.patten_regexp = None
    elif not isinstance(value, str):
        raise SchemaError(msg=f"Value {value} is not string or list.", path=path)
    rule_obj.enum = None
    return True

def int_csv_or_list(value, rule_obj, path):
    rule_obj.pattern = "^(\\d+)(,\\d+)*$"
    return _csv_or_list(int, value, rule_obj, path)

def str_csv_or_list(value, rule_obj, path):
    rule_obj.pattern = r"^\[?[a-zA-Z0-9]+(,[a-zA-Z0-9]+)*\]?$"
    return _csv_or_list(str, value, rule_obj, path)

def is_node(value, rule_obj, path):
    node_data = get_node_data()
    for instance in value.keys():
        if instance not in node_data.all_instances:
            return False
    return True

def is_server(value, rule_obj, path):
    if isinstance(value, list):
        for server in value:
            if not is_server(server, rule_obj, path):
                raise SchemaError(f'No server with name/pattern "{server}" found.')
        return True

    node_data = get_node_data()
    try:
        for server in node_data.servers:
            if re.match(value, server):
                return True
        return False
    except re.error as ex:
        raise SchemaError(f'Invalid regular expression: "{ex.pattern}"', path=path)

def is_element(value, rule_obj, path):
    node_data = get_node_data()
    for instance in value.keys():
        if instance in node_data.all_instances:
            return False
    return True

def check_main_structure(value, rule_obj, path):
    node_data = get_node_data()
    for element in value.keys():
        if element == 'DEFAULT':
            if any(item in value[element].keys() for item in node_data.instances):
                raise SchemaError(msg="The DEFAULT tag must not have any instances!", path=path)
        elif element in node_data.nodes:
            for instance in value[element].keys():
                if instance not in node_data.instances.get(element, {}):
                    raise SchemaError(
                        msg=f"{instance} is not an instance name of node {element}!",
                        path=path + '/' + element
                    )
        elif element in node_data.all_instances:
            if node_data.all_instances[element] > 1:
                raise SchemaError(
                    msg=f"Instance name {element} is ambiguous. You must add a node name to your yaml structure!",
                    path=path
                )
        elif element in ['commands', 'chat_commands']:
            continue
        else:
            raise SchemaError(msg=f"{element} is neither a node nor an instance name!", path=path)
    return True

def validate(source_file: str, schema_files: list[str], *, raise_exception: bool = False):
    c = Core(source_file=source_file, schema_files=schema_files, file_encoding='utf-8',
             extensions=['core/utils/validators.py'])
    try:
        c.validate(raise_exception=True)
    except PyKwalifyException as ex:
        if raise_exception:
            raise
        if isinstance(ex, SchemaError):
            logger.warning(f'Error while parsing {source_file}:\n{ex}')
        else:
            logger.error(f'Error while parsing {source_file}:\n{ex}', exc_info=ex)
