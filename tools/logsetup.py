"""The viewer's log: messages to the console and to view.log in the project folder, rewritten at each start (only
the last run is kept). Modules log with logging.getLogger(__name__). The console shows INFO and up; the file also
gets DEBUG (shaders loaded and such) and the tracebacks of uncaught errors, of the main and other threads.

Per-frame render timings (view.py --stats) are printed to the console only: they would flood the file.

The packed viewer (TripoliRadar.exe) has no console: only the file then, and an error that ends the program is also
shown in a message box, or the window would just vanish."""
import logging
import os
import sys
import threading
from paths import PROJECT_DIR

PATH = os.path.join(PROJECT_DIR, 'view.log')


def start(path=PATH):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    if sys.stdout is not None:                  # None: no console (the packed viewer)
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter('%(message)s'))
        root.addHandler(console)
    try:
        file = logging.FileHandler(path, mode='w', encoding='utf-8')
    except OSError as e:
        root.warning(f"Can't write the log to {path}: {e}")
    else:
        file.setFormatter(logging.Formatter('%(asctime)s %(levelname)-7s %(name)s: %(message)s'))
        root.addHandler(file)
    logging.captureWarnings(True)
    for noisy in ('PIL', 'OpenGL'):
        logging.getLogger(noisy).setLevel(logging.INFO)

    def uncaught(kind, value, tb):
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, tb)
        else:
            logging.getLogger('uncaught').critical("Uncaught error", exc_info=(kind, value, tb))
            alert(f"{value or kind.__name__}\n\nDetails are in {path}")

    def uncaught_in_thread(a):
        if not issubclass(a.exc_type, SystemExit):
            logging.getLogger('uncaught').critical(f"Uncaught error in thread {a.thread.name if a.thread else '?'}",
                                                   exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    sys.excepthook = uncaught
    threading.excepthook = uncaught_in_thread


def alert(text):
    """An error message box, when there is no console to see the log in (the packed viewer, Windows)."""
    if sys.stdout is not None or sys.platform != 'win32':
        return
    pygame = sys.modules.get('pygame')
    if pygame is not None and pygame.display.get_init():
        pygame.display.quit()                   # a fullscreen window would hide the box
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, str(text), "Tripoli Radar", 0x10)     # MB_ICONERROR
