"""Entry point for the frozen sidecar.

Not `splitstep.cli:main` directly: the shell always invokes this as a server,
and hardcoding `serve` here means the Tauri side cannot accidentally run
`init` or `detect` against a library by passing stray argv. It also gives
PyInstaller a single, static module to analyse.
"""

import multiprocessing
import sys

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


if __name__ == "__main__":
    # Required before anything else under PyInstaller: without it a frozen
    # process that forks re-executes the bundle's entry point instead of the
    # child target, which shows up as the app launching itself in a loop.
    multiprocessing.freeze_support()
    sys.argv = [sys.argv[0], *_reorder(sys.argv[1:])]
    sys.exit(main())
