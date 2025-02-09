import logging
import os

from core import DEFAULT_TAG, COMMAND_LINE_ARGS
from pykwalify.errors import SchemaError

logger = logging.getLogger(__name__)


def file_exists(value, rule_obj, path):
    if path and path.split("/")[1] in [DEFAULT_TAG, COMMAND_LINE_ARGS.node]:
        if not os.path.exists(os.path.expandvars(value)):
            raise SchemaError(msg=f'File "{value}" does not exist', path=path)
    return True
