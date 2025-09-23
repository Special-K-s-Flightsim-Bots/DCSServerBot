import logging
import os

from core import COMMAND_LINE_ARGS, MizFile
from pathlib import Path
from rich import print
from rich.prompt import Prompt

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML(typ='safe')

logger = logging.getLogger(__name__)


if __name__ == '__main__':
    args = COMMAND_LINE_ARGS
    filename = args.mizfile
    presets = args.preset.split(',') if args.preset else []
    presets_file = os.path.join(args.config, args.presets_file)
    data = yaml.load(Path(presets_file).read_text(encoding='utf-8'))
    if not presets:
        presets = [Prompt.ask("Please specify a preset: ", choices=data.keys())]
    print(f"Reading mission {filename}...")
    miz = MizFile(filename)
    for preset in presets:
        print(f"Applying preset {preset} ...")
        try:
            miz.apply_preset(data[preset])
        except KeyError:
            logger.error(f"Preset {preset} not found")
    miz.save()
    print("New mission written.")
