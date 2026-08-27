"""Entry point for the frozen sidecar.

Not `splitstep.cli:main` directly: the shell always invokes this as a server,
and hardcoding `serve` here means the Tauri side cannot accidentally run
`init` or `detect` against a library by passing stray argv. It also gives
PyInstaller a single, static module to analyse.
"""

import multiprocessing
import os
import signal
import sys
import threading
import time

from splitstep.cli import main

# `--library` is declared on the TOP-LEVEL parser, not on the `serve`
# subparser, so argparse only accepts it BEFORE the subcommand name. The
# shell passes flags as one flat list and should not have to know that, so
# the reordering happens here -- one place, next to the argv rewrite that
# creates the problem.
GLOBAL_OPTS = {"--library"}


def _reorder(args: list[str]) -> list[str]:
    globals_: list[str] = []
    rest: list[str] = []
    iterator = iter(args)
    for arg in iterator:
        if arg in GLOBAL_OPTS:
            globals_.append(arg)
            value = next(iterator, None)
            if value is not None:
                globals_.append(value)
        elif any(arg.startswith(f"{opt}=") for opt in GLOBAL_OPTS):
            globals_.append(arg)
        else:
            rest.append(arg)
    return [*globals_, "serve", *rest]


def _die_with_parent() -> None:
    """Exit when the shell that spawned us does.

    Measured, not assumed: quitting the installed .app left this process
    alive and still LISTENing, because neither RunEvent::ExitRequested nor
    RunEvent::Exit reliably reaches the shell's shutdown handler on macOS --
    Tauri exits the process from inside its own run loop. Waiting for a
    parent-side event to be delivered is the wrong shape for the problem;
    noticing the parent is gone is not.

    macOS has no PDEATHSIG, so this polls getppid() instead. Reparenting to
    launchd (pid 1) is the signal. SIGTERM to self rather than os._exit, so
    uvicorn runs its graceful shutdown and sqlite closes cleanly.

    Only the FROZEN entry point does this. `splitstep serve` from a terminal
    is untouched -- there the parent is a shell the user may well outlive,
    and dying with it would be wrong.
    """
    parent = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(2)
            if os.getppid() != parent:
                os.kill(os.getpid(), signal.SIGTERM)
                return

    threading.Thread(target=watch, daemon=True).start()


if __name__ == "__main__":
    # Required before anything else under PyInstaller: without it a frozen
    # process that forks re-executes the bundle's entry point instead of the
    # child target, which shows up as the app launching itself in a loop.
    multiprocessing.freeze_support()
    _die_with_parent()
    sys.argv = [sys.argv[0], *_reorder(sys.argv[1:])]
    sys.exit(main())
