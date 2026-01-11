import sys

from .processmanager import ProcessManager
if sys.platform == 'win32':
    from .win32.cpu import *
else:
    from .linux.cpu import *
