"""Make modules in the parent directory importable without a package shim."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
