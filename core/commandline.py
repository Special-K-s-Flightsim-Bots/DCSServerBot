import argparse
import platform

__all__ = [
    "COMMAND_LINE_ARGS"
]


COMMAND_LINE_ARGS = None

if not COMMAND_LINE_ARGS:
    parser = argparse.ArgumentParser(prog='run.py', description="Welcome to DCSServerBot!",
                                     epilog='If unsure about the parameters, please check the documentation.')
    parser.add_argument('-n', '--node', help='Node name', default=platform.node())
    parser.add_argument('-x', '--noupdate', action='store_true', help='Do not autoupdate')
    parser.add_argument('-c', '--config', help='Path to configuration', default='config')

    COMMAND_LINE_ARGS = parser.parse_args()
