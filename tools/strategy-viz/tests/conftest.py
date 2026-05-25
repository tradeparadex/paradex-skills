"""Put tools/strategy-viz/ on sys.path so the `strategy_viz` package is
importable when tests run without `pip install -e .`. Once the package is
installed, this conftest is a no-op."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
