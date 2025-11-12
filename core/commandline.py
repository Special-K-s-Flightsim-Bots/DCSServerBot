import argparse
import os
import platform
import sys

__all__ = [
    "COMMAND_LINE_ARGS"
]


COMMAND_LINE_ARGS = None

if not COMMAND_LINE_ARGS:
    program = os.path.basename(sys.argv[0])
    parser = argparse.ArgumentParser(prog=program, description="Welcome to DCSServerBot!",
                                     epilog='If unsure about the parameters, please check the documentation.')
    parser.add_argument('-c', '--config', help='Path to configuration', default='config')
    if program == 'run.py':
        parser.add_argument('-n', '--node', help='Node name', default=platform.node())
        parser.add_argument('-x', '--noupdate', action='store_true', help='Do not autoupdate')
        parser.add_argument('-s', '--secret', action='store_true', help='Reveal all stored passwords')
        parser.add_argument('-r', '--restarted', action='store_true', help='Indicates if the bot was restarted')
    elif program == 'update.py':
        parser.add_argument('-n', '--node', help='Node name', default=platform.node())
        parser.add_argument('-r', '--no-restart', action='store_true', default=False,
                            help="don't start DCSServerBot after the update")
        parser.add_argument('-i', '--install', action='store_true', default=False,
                            help='Install requirements.txt only')
        parser.add_argument('-r', '--restarted', action='store_true', help='Indicates if the bot was restarted')
    elif program == 'install.py':
        parser.add_argument('-n', '--node', help='Node name', default=platform.node())
        parser.add_argument('-u', '--user', help='Database username', default='dcsserverbot')
        parser.add_argument('-d', '--database', help='Database name', default='dcsserverbot')
    elif program == 'mizedit.py':
        parser.add_argument('-m', '--mizfile', help='Mission to patch', required=True)
        parser.add_argument('-p', '--preset', help='Preset to use, can be comma-separated')
        parser.add_argument('-f', '--presets-file', help='Presets file', default='presets.yaml')
    elif program == 'recover.py':
        parser.add_argument('-n', '--node', help='Node name', default=platform.node())
    elif program == 'testdriver.py':
        parser.add_argument('-n', '--node', help='Node name', default='TestNode')
    COMMAND_LINE_ARGS = parser.parse_args()
