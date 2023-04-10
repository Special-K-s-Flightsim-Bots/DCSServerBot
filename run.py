from __future__ import annotations
import asyncio
import os
import platform
from install import Install
from main import Main


if __name__ == "__main__":
    if not os.path.exists('config/dcsserverbot.ini'):
        print("Please run 'python install.py' first.")
        exit(-1)
    if int(platform.python_version_tuple()[0]) < 3 or int(platform.python_version_tuple()[1]) < 9:
        print("You need Python 3.9 or higher to run DCSServerBot!")
        exit(-1)
    elif int(platform.python_version_tuple()[1]) == 9:
        print("Python 3.9 is outdated, you should consider upgrading it to 3.10 or higher.")
    try:
        Install.verify()
        asyncio.run(Main().run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        exit(-1)
    except Exception as ex:
        print(f"{ex.__class__.__name__}: {ex}")
        exit(-1)
