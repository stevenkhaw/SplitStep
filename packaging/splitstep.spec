# PyInstaller spec for the frozen `splitstep serve` sidecar.
#
# onedir, not onefile: onefile unpacks ~2 GB to a temp directory on every
# launch, which is both slow and a second copy of the bundle on a disk the
# user did not agree to fill. onedir also lets `sys._MEIPASS` be a real
# directory the app reads from directly, which is what resources.py already
# assumes.
#
# The payload (ffmpeg, weights, font, web_dist) lands in a second commit, so
# a hook failure and a missing binary cannot be confused for each other.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

REPO = Path(SPECPATH).parent

# ultralytics loads model YAMLs (yolo11n.yaml and the cfg/ tree) by relative
# path at runtime, and torch's dynamo/inductor subpackages are imported
# lazily by name. Static analysis sees neither, so both are collected
# wholesale. This is the "budget a day of hook-fighting" the distribution
# spec warned about; collecting broadly is the cheap answer.
VENDOR = REPO / "packaging" / "vendor"
WEB_DIST = REPO / "web" / "dist"

# Flat at the bundle root, because resources.py's _bundled() looks for bare
# names there. web_dist/ is the one nested entry, matching spa_dist().
# splitstep's OWN data files, and this line is load-bearing. It carries
# both the migrations and assets/font.ttf, which is why neither is listed
# explicitly below. The migrations
# are .sql files found at runtime via `Path(__file__).parent / "migrations"`,
# and collect_submodules() gathers .py modules only -- so without this the
# frozen app shipped with zero migrations, created a library.db with
# user_version 0 and no tables in it, and 500ed on the first route that
# touched the database. /api/config kept answering because it never does,
# which is exactly what made the failure look like a working app.
datas = collect_data_files("splitstep") + collect_data_files("ultralytics") + [
    (str(VENDOR / "yolo11n.pt"), "."),
    (str(WEB_DIST), "web_dist"),
]
hiddenimports = (
    collect_submodules("ultralytics")
    + collect_submodules("splitstep")
    # uvicorn resolves its protocol implementations through a string
    # registry, so none of them appear as imports anywhere.
    + [
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ]
)

a = Analysis(
    [str(REPO / "packaging" / "entry.py")],
    pathex=[str(REPO)],
    # ffmpeg/ffprobe go in binaries, not datas: PyInstaller runs macholib
    # over binaries and will not mangle a static executable, whereas a data
    # file loses its executable bit on some COLLECT paths.
    binaries=[
        (str(VENDOR / "ffmpeg"), "."),
        (str(VENDOR / "ffprobe"), "."),
    ],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # torchvision is NOT excluded despite never being imported by our code:
    # ultralytics reaches for it on several paths (NMS ops, dataset
    # transforms) and a missing-module error at detect time would surface
    # fifteen minutes into a job rather than at startup. The others are
    # genuinely unreachable here and are frequent sources of PyInstaller
    # import failures.
    excludes=["matplotlib", "tkinter", "PyQt5", "PySide2", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="splitstep-server",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    target_arch="arm64",
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name="splitstep-server",
)
