"""Load calculation modules without starting Home Assistant."""

import importlib
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "smart_pv_test"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, package)


def load(name):
    return importlib.import_module(f"{PACKAGE}.{name}")
