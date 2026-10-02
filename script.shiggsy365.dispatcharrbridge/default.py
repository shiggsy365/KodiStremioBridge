import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "resources", "lib"))

from dispatcharrbridge.ui import run  # noqa: E402

run("next" if "next" in sys.argv[1:] else "playing")
