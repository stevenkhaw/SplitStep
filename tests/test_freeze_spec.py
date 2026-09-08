"""The freeze's exclude list, checked against what ultralytics actually imports.

`excludes` in `packaging/splitstep.spec` is the cheapest way to keep a 10 GB
bundle from growing, and it is also the only PyInstaller setting that can
make a module vanish with no build-time complaint at all. Excluding
matplotlib once produced a freeze that started, served, ingested and built
proxies correctly, then died inside `iter_person_boxes` fifteen minutes into
the first detect job.

The trap is that ultralytics scopes nearly every matplotlib import inside a
function -- each one carrying the comment "scope for faster 'import
ultralytics'" -- so grepping the package makes the exclusion look safe.
`models/yolo/semantic/train.py` imports `matplotlib.pyplot` at module level,
under the `ultralytics.models` subpackage that `ultralytics/__init__.py`
resolves lazily on first attribute access. Nothing before the detector's
first frame touches it.

So this asserts on the shape that actually breaks: a MODULE-LEVEL,
unconditional import of an excluded top-level package, anywhere in
ultralytics. A function-scoped or try-wrapped import is deliberately not
flagged -- those are the ones ultralytics guards, and flagging them would
make the test noise.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SPEC = Path(__file__).resolve().parent.parent / "packaging" / "splitstep.spec"


def _spec_excludes() -> list[str]:
    """Read the exclude list out of the spec without importing PyInstaller.

    The spec is not an importable module -- it runs with `SPECPATH` injected
    by PyInstaller -- so it is parsed, not executed.
    """
    source = SPEC.read_text()
    match = re.search(r"excludes=(\[[^\]]*\])", source)
    assert match, "no excludes= list in splitstep.spec"
    return ast.literal_eval(match.group(1))


def _module_level_imports(path: Path) -> list[tuple[int, str]]:
    """Top-level `import x` / `from x import y`, unconditional ones only."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    found = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return found


def test_no_excluded_module_is_imported_at_ultralytics_module_level() -> None:
    ultralytics = pytest.importorskip("ultralytics")
    excluded = set(_spec_excludes())
    root = Path(ultralytics.__file__).parent

    offenders = [
        f"{path.relative_to(root)}:{lineno} imports {name}"
        for path in sorted(root.rglob("*.py"))
        for lineno, name in _module_level_imports(path)
        if name.split(".")[0] in excluded
    ]
    assert not offenders, (
        "packaging/splitstep.spec excludes a module ultralytics imports at "
        "module level. The freeze will build cleanly and fail at detect time:\n  "
        + "\n  ".join(offenders)
    )


def test_matplotlib_is_not_excluded() -> None:
    """Pinned separately from the scan above, which only sees this ultralytics.

    The scan is only as good as the installed version; a future ultralytics
    that moves the offending import into a function would let matplotlib back
    onto the list, and the failure would return the next time it moved back.
    """
    assert "matplotlib" not in _spec_excludes()
