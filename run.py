from __future__ import annotations
import asyncio
import os
import platform
import traceback
from install import Install
from migrate import migrate
from node import Node


if __name__ == "__main__":
    if os.path.exists('config/dcsserverbot.ini'):
        migrate()
    elif not os.path.exists('config/main.yaml'):
        print("Please run 'python install.py' first.")
        exit(-1)
    if int(platform.python_version_tuple()[0]) < 3 or int(platform.python_version_tuple()[1]) < 9:
        print("You need Python 3.9 or higher to run DCSServerBot!")
        exit(-1)
    elif int(platform.python_version_tuple()[1]) == 9:
        print("Python 3.9 is outdated, you should consider upgrading it to 3.10 or higher.")
    try:
        #Install.verify()
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()
        )
        asyncio.run(Node().run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        exit(-1)
    except Exception as ex:
        traceback.print_exc()
        exit(-1)
