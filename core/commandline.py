import argparse
import platform

__all__ = [
    "COMMAND_LINE_ARGS",
    "set_commandline_args"
]


COMMAND_LINE_ARGS = None


def set_commandline_args(args: argparse.Namespace) -> None:
    global COMMAND_LINE_ARGS

    COMMAND_LINE_ARGS = args


if not COMMAND_LINE_ARGS:
    parser = argparse.ArgumentParser(prog='run.py', description="Welcome to DCSServerBot!",
                                     epilog='If unsure about the parameters, please check the documentation.')
    parser.add_argument('-n', '--node', help='Node name', default=platform.node())
    parser.add_argument('-x', '--noupdate', action='store_true', help='Do not autoupdate')
    parser.add_argument('-c', '--config', help='Path to configuration', default='config')
    parser.add_argument('-s', '--secret', action='store_true', help='Reveal all stored passwords')

    set_commandline_args(parser.parse_args())
