"""Load the engine by file path so the suite runs without the Hermes runtime installed.

The plugin package's ``__init__.py`` imports ``agent.memory_provider``; the retrieval
engine deliberately does not, and stays importable on a bare checkout.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "plugins" / "mdvault" / "vault_engine.py"

spec = importlib.util.spec_from_file_location("vault_engine", ENGINE)
module = importlib.util.module_from_spec(spec)
sys.modules["vault_engine"] = module
spec.loader.exec_module(module)

MIRROR = ROOT / "plugins" / "mdvault" / "mirror.py"
mirror_spec = importlib.util.spec_from_file_location("mirror", MIRROR)
mirror_module = importlib.util.module_from_spec(mirror_spec)
sys.modules["mirror"] = mirror_module
mirror_spec.loader.exec_module(mirror_module)
