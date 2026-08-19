import sys
from pathlib import Path

# Ensure the repo root is in sys.path for imports
repo_root = Path(__file__).parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
