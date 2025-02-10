import datetime
import logging
import os
import threading

from core import DEFAULT_TAG, COMMAND_LINE_ARGS
from pathlib import Path
from pykwalify import partial_schemas
from pykwalify.core import Core
from pykwalify.errors import SchemaError, CoreError
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

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

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
            self._data = yaml.load(config_path.read_text(encoding='utf-8'))
            self._nodes: list[str] = list(self._data.keys())
            self._instances: dict[str, list[str]] = {
                node: list(self._data[node].get('instances', {}).keys()) for node in self._nodes
            }
            self._all_instances: dict[str, int] = {}
            for node, instances in self._instances.items():
                for instance in instances:
                    self._all_instances[instance] = self._all_instances.get(instance, 0) + 1
        except Exception:
            raise CoreError(msg="nodes.yaml seems to be corrupt, can't initialize the node/instance-validation!")

    @property
    def nodes(self):
        return self._nodes

    @property
    def instances(self):
        return self._instances

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
        raise SchemaError(msg=ex.msg, path=path)

def seq_or_map(value, rule_obj, path):
    if isinstance(value, list):
        include_name = rule_obj.schema_str.get('enum')[0]
    elif isinstance(value, dict):
        include_name = rule_obj.schema_str.get('enum')[1]
    else:
        raise SchemaError(msg=f'Value is not a list or dict', path=path)
    _validate_schema(_load_schema(include_name, path), value, path)
    rule_obj.enum = None
    return True

def _scalar_or_map(t: Type, value: Any, rule_obj: Rule, path: str):
    if isinstance(value, dict):
        _validate_schema(_load_schema(rule_obj.schema_str.get('enum')[0], path), value, path)
    elif not isinstance(value, t):
        raise SchemaError(msg=f'Value is not a {t.__name__} or dict', path=path)
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
            schema = {
                'type': 'seq',
                'nullable': False,
                'sequence': [
                    {'type': _types[t], 'nullable': False}
                ]
            }
        _validate_schema(schema, value, path)
    elif not isinstance(value, t):
        raise SchemaError(msg=f'Value is not a {t.__name__} or list', path=path)
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

def is_node_or_instance(value, rule_obj, path):
    elements = path.split("/")
    if not elements:
        return True
    node_data = get_node_data()
    elements = elements[1:]
    if elements[0] == DEFAULT_TAG and len(elements) > 1:
        raise SchemaError(msg=f"The DEFAULT tag must not have any instances!", path=path)
    elif len(elements) == 1:
        if elements[0] != DEFAULT_TAG and elements[0] not in node_data.nodes:
            if elements[0] in node_data.all_instances:
                if node_data.all_instances[elements[0]] > 1:
                    raise SchemaError(
                        msg=f"Instance name {elements[0]} is ambiguous. You must add a node name to your yaml structure!",
                        path=path
                    )
            else:
                raise SchemaError(msg=f"{elements[0]} is neither a node nor an instance name!", path=path)
    elif len(elements) == 2:
        if elements[0] not in node_data.nodes:
            raise SchemaError(msg=f"{elements[0]} is not a node name!", path=path)
        if elements[1] not in node_data.instances[elements[0]]:
            raise SchemaError(msg=f"{elements[1]} is not an instance name of node {elements[0]}!", path=path)
    else:
        raise SchemaError(msg=f"Path {path} is not a valid instance representation!", path=path)
    return True
