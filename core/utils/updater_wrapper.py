import argparse
import logging
import os
import psutil
import sys
import time
import win32api

from pathlib import Path
from pywinauto import Application, findwindows, Desktop

log_dir = Path("logs")
log_dir.mkdir(parents=True, exist_ok=True)

LOG_FILE = log_dir / "dcs_repair.log"

logging.basicConfig(
    level=logging.DEBUG,
    format=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
    ]
)

logger = logging.getLogger(__name__)


def _find_window(process: psutil.Process, timeout: int = 30):
    for i in range(0, timeout):
        windows = findwindows.find_elements(title_re="DCS Updater *", top_level_only=False)
        window = next((window for window in windows if len(window.children()) > 6), None)
        if window:
            return window
        elif len(windows) >= 1 and any(window.children()[0].name == 'OK' for window in windows):
            window = next(window for window in windows if window.children()[0].name == 'OK')
            return window
        elif not process.is_running():
            sys.exit(2)
        time.sleep(1)
    else:
        raise TimeoutError()


def terminate_process(process: psutil.Process | None):
    if process is not None and process.is_running():
        process.terminate()
        try:
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def ensure_foreground(win):
    try:
        win32api.SetForegroundWindow(win.handle)
    except Exception as e:
        logger.warning("Could not activate window: %s", e)


def do_update(installation: str, slow: bool | None = False, check_extra_files: bool | None = False):
    app = None
    try:
        cmd = os.path.join(installation, 'bin', 'dcs_updater.exe')
        app = Application(backend="win32").start(f'"{cmd}" repair')
        pid = app.process
        process = psutil.Process(pid)

        window = _find_window(process)

        dlg = app.window(handle=window.handle)
        dlg.wait('ready', timeout=15)

        def uia_wrapper_from_handle(handle):
            """Return a UIA WindowSpecification for the given HWND."""
            return Desktop(backend='uia').window(handle=handle)

        radio_slow = chk_search = repair_btn = ok_btn = None
        for child in dlg.children():
            uia_child = uia_wrapper_from_handle(child.handle)
#            if uia_child.element_info.automation_id == '1025':
#                radio_default = uia_wrapper_from_handle(child.handle)
            if uia_child.element_info.automation_id == '1026':
                radio_slow = uia_wrapper_from_handle(child.handle)
            elif uia_child.element_info.automation_id == '1010':
                chk_search = uia_wrapper_from_handle(child.handle)
            elif uia_child.element_info.automation_id == '1':
                repair_btn = uia_wrapper_from_handle(child.handle)
            elif uia_child.element_info.name == 'OK':
                ok_btn = uia_wrapper_from_handle(child.handle)

        #dlg.set_focus()
        ensure_foreground(dlg)
        # if the process was canceled with an error, we see an OK button
        if ok_btn:
            # close the repair option
            ok_btn.invoke()
        # else, tick the correct switches and run the repair
        else:
            if slow:
                radio_slow.click_input()

            if check_extra_files:
                chk_search.click_input()

            # run the repair
            repair_btn.invoke()

            # close the OK message
            window = _find_window(process, timeout=1800 if slow else 180)
            dlg = app.window(handle=window.handle)
            dlg.wait('ready', timeout=15)
            ok_btn = uia_wrapper_from_handle(dlg.children()[0].handle)
            ok_btn.invoke()

        p = psutil.Process(app.process)
        return p.wait()
    except RuntimeError as ex:
        logger.exception(ex)
        if app:
            p = psutil.Process(app.process)
            terminate_process(p)
        return -2
    except Exception as ex:
        logger.exception(ex)
        return -1


def close_autoupdate_templog():
    try:
        app = Application(backend="uia").connect(
            title_re=r".*autoupdate_templog\.txt.*", timeout=10
        )
        dlg = app.window(title_re=r".*autoupdate_templog\.txt.*")
        dlg.close()
        return True
    except Exception as e:
        print("Could not find or close editor:", e)
        return False


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description="DCS Updater wrapper for unattended updates")
    ap.add_argument("-d", "--dcs_home", default=r"C:\Program Files\Eagle Dynamics\DCS World", help="DCS home directory")
    ap.add_argument("-s", "--slow", action='store_true', default=False, help="Run a slow repair")
    ap.add_argument("-c", "--check_extra_files", action='store_true', default=False, help="Check for extra files")
    args = ap.parse_args()
    try:
        rc = do_update(args.dcs_home, args.slow, args.check_extra_files)
        if rc == 1:
            close_autoupdate_templog()
        sys.exit(rc)
    except Exception as ex:
        logger.exception(ex)
        exit(-1)
