import gettext
import os

from core.commandline import COMMAND_LINE_ARGS
from pathlib import Path
from typing import Callable

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


__all__ = [
    "get_translation",
    "get_language",
    "set_language"
]


_language: str | None = None


def get_translation(domain) -> Callable[[str], str]:
    translation = gettext.translation(domain, localedir='locale', languages=[_language], fallback=True)
    return translation.gettext


def get_language() -> str:
    return _language


def set_language(language: str):
    global _language
    _language = language


if not _language:
    try:
        config = yaml.load(Path(os.path.join(COMMAND_LINE_ARGS.config, 'main.yaml')).read_text(encoding='utf-8'))
        _language = config.get('language', 'en_US')
    except FileNotFoundError:
        _language = 'en_US'
