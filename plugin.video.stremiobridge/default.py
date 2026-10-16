import os
import sys

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib")
if LIB not in sys.path:  # Kodi can reuse the interpreter between calls (reuselanguageinvoker)
    sys.path.insert(0, LIB)

from kodi_ui.router import run  # noqa: E402

run(sys.argv)
