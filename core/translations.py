import gettext
import os

from core.commandline import COMMAND_LINE_ARGS
from pathlib import Path

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


__all__ = [
    "get_translation"
]


_language = None


def get_translation(domain):
    translation = gettext.translation(domain, localedir='locale', languages=[_language], fallback=True)
    return translation.gettext


if not _language:
    try:
        config = yaml.load(Path(os.path.join(COMMAND_LINE_ARGS.config, 'main.yaml')))
        _language = config.get('language', 'en_US')
    except FileNotFoundError:
        _language = 'en_US'
