import datetime
import logging
import os

from core import DEFAULT_TAG, COMMAND_LINE_ARGS
from pykwalify import partial_schemas
from pykwalify.core import Core
from pykwalify.errors import SchemaError
from pykwalify.rule import Rule
from typing import Any, Type, Union

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
