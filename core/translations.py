import gettext
import os

from pathlib import Path

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


__all__ = [
    "language",
    "get_translation"
]


language = None


def get_translation(domain):
    translation = gettext.translation(domain, localedir='locale', languages=[language], fallback=True)
    return translation.gettext


if not language:
    try:
        config = yaml.load(Path(os.path.join('config', 'main.yaml')))
        language = config.get('language', 'en_US')
    except FileNotFoundError:
        language = 'en_US'
#    set_language()
