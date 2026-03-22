import argparse
import ctypes
import logging
import os
import psutil
import pywintypes
import sys
import time
import win32gui

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

OK_MESSAGE = 'OK'
DELETE_MESSAGE = 'Delete'
CLEANUP_MESSAGE = 'Cleanup has found some extra files not belonging to the vanilla install:'


def _find_updater_window(process: psutil.Process, timeout: int = 30):
    for i in range(0, timeout):
        windows = findwindows.find_elements(title_re="DCS Updater *", top_level_only=False)
        window = next((window for window in windows if len(window.children()) > 6), None)
        if window:
            return window
        elif not process.is_running():
            sys.exit(2)
        time.sleep(1)
    else:
        raise TimeoutError()


def _find_next_window(process: psutil.Process, timeout: int = 30):
    for i in range(0, timeout):
        windows = findwindows.find_elements(title_re="DCS Updater *", top_level_only=False)
        if len(windows) >= 1:
            window = next((w for w in windows if w.children()[0].name == OK_MESSAGE), None)
            if window:
                return window
            window = next((w for w in windows if len(w.children()) > 5 and w.children()[5].name == CLEANUP_MESSAGE), None)
            if window:
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
        win32gui.SetForegroundWindow(win.handle)
    except Exception as e:
        logger.warning("Could not activate window: %s", e)


def click_no_mouse(ctrl):
    """
    Click a control *without* moving the mouse.

    Parameters
    ----------
    ctrl: pywinauto wrapper
        The control you want to click.
    """
    # 1️⃣  Prefer the UIA invoke / check API
    try:
        # UIA CheckBox has .check()/.uncheck()/.toggle()
        if hasattr(ctrl, "check"):
            ctrl.check()          # idempotent – checks the box if not already checked
            return
        # UIA Button or other: invoke()
        ctrl.invoke()
        return
    except Exception as exc:
        logger.debug("UIA invoke failed: %s", exc)

    # 2️⃣  If that fails, try pure Win32 PostMessage (no mouse)
    try:
        import win32gui, win32con
        win32gui.PostMessage(ctrl.handle, win32con.WM_LBUTTONDOWN, 0, 0)
        win32gui.PostMessage(ctrl.handle, win32con.WM_LBUTTONUP, 0, 0)
        return
    except Exception as exc:
        logger.debug("Win32 PostMessage failed: %s", exc)

    # 3️⃣  Final fallback – use SendInput (works even without a cursor)
    try:
        rect = ctrl.rectangle()
        x = rect.left + (rect.right - rect.left) // 2
        y = rect.top + (rect.bottom - rect.top) // 2

        # Move the cursor to the center
        # noinspection PyUnresolvedReferences
        ctypes.windll.user32.SetCursorPos(x, y)

        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004

        # Send a left‑click
        # noinspection PyUnresolvedReferences
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        # noinspection PyUnresolvedReferences
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP,   0, 0, 0, 0)
        return
    except Exception as exc:
        logger.error("SendInput fallback failed: %s", exc)
        raise


def do_repair(installation: str, slow: bool | None = False, check_extra_files: bool | None = False):
    app = None
    try:
        cmd = os.path.join(installation, 'bin', 'dcs_updater.exe')
        app = Application(backend="win32").start(f'"{cmd}" repair')
        pid = app.process
        process = psutil.Process(pid)

        window = _find_updater_window(process)

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
            elif uia_child.element_info.name == OK_MESSAGE:
                ok_btn = uia_wrapper_from_handle(child.handle)

        ensure_foreground(dlg)
        # if the process was canceled with an error, we see an OK button
        if ok_btn:
            # close the repair option
            ok_btn.invoke()
        # else, tick the correct switches and run the repair
        else:
            if slow:
                radio_slow.invoke()

            if check_extra_files:
                try:
                    chk_search.click_input()
                except pywintypes.error:
                    click_no_mouse(chk_search)

            # run the repair
            repair_btn.invoke()

            finished = False
            while not finished:
                # get the next window to process
                window = _find_next_window(process, timeout=1800 if slow else 180)
                dlg = app.window(handle=window.handle)
                dlg.wait('ready', timeout=15)

                # Only the finish message has an OK button
                if window.children()[0].name == OK_MESSAGE:
                    ok_btn = uia_wrapper_from_handle(dlg.children()[0].handle)
                    ok_btn.invoke()
                    finished = True
                # otherwise we need to press the delete for check extra files
                else:
                    delete_btn = uia_wrapper_from_handle(dlg.children()[0].handle)
                    delete_btn.invoke()

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


def do_update(installation: str, version: str | None = None, branch: str | None = None):
    app = None
    try:
        cmd = os.path.join(installation, 'bin', 'dcs_updater.exe')
        options = ""
        if version:
            options = version
        if branch:
            options += f"@{branch}"
        app = Application(backend="win32").start(f'"{cmd}" --quiet update {options}')
        pid = app.process
        process = psutil.Process(pid)

        try:
            window = _find_updater_window(process)

            dlg = app.window(handle=window.handle)
            dlg.wait('ready', timeout=15)

            def uia_wrapper_from_handle(handle):
                """Return a UIA WindowSpecification for the given HWND."""
                return Desktop(backend='uia').window(handle=handle)

            delete_btn = None
            for child in dlg.children():
                uia_child = uia_wrapper_from_handle(child.handle)
                if uia_child.element_info.name == DELETE_MESSAGE:
                    delete_btn = uia_wrapper_from_handle(child.handle)

            ensure_foreground(dlg)
            # if there are files to be deleted, we need to invoke the Delete button
            if delete_btn:
                delete_btn.invoke()

        except TimeoutError:
            pass

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


def get_parser() -> argparse.ArgumentParser:
    """
    Builds the top‑level parser and two sub‑parsers.
    """
    ap = argparse.ArgumentParser(
        description="DCS Updater wrapper for unattended updates / repairs"
    )
    ap.add_argument(
        "-d",
        "--dcs_home",
        default=r"C:\Program Files\Eagle Dynamics\DCS World",
        help="DCS home directory",
    )

    subparsers = ap.add_subparsers(
        dest="command",
        required=True,
        help="sub‑command to execute",
    )

    # ---------- repair ----------
    repair_parser = subparsers.add_parser(
        "repair",
        help="Run a repair of the DCS installation",
    )
    repair_parser.add_argument(
        "-s",
        "--slow",
        action="store_true",
        default=False,
        help="Run a slow repair (or update)",
    )
    repair_parser.add_argument(
        "-c",
        "--check_extra_files",
        action="store_true",
        default=False,
        help="Check for extra files",
    )

    # ---------- update ----------
    update_parser = subparsers.add_parser(
        "update",
        help="Run an unattended update of the DCS installation",
    )
    update_parser.add_argument(
        "-v",
        "--version",
        default="",
        help="Specific version to update to (default: %(default)s)",
    )
    update_parser.add_argument(
        "-b",
        "--branch",
        default="release",
        help="Branch to update (default: %(default)s)",
    )
    # update_parser inherits the global arguments

    return ap


if __name__ == '__main__':
    parser = get_parser()
    args = parser.parse_args()

    try:
        if args.command == "repair":
            rc = do_repair(
                installation=args.dcs_home,
                slow=args.slow,
                check_extra_files=args.check_extra_files,
            )
            if rc == 1:
                close_autoupdate_templog()
        else:  # args.command == "update"
            rc = do_update(
                installation=args.dcs_home,
                branch=args.branch,
                version=args.version,
            )

        sys.exit(rc)
    except Exception as ex:
        logger.exception(ex)
        exit(-1)
