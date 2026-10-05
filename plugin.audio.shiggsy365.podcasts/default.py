import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from podcasts_ui.router import run  # noqa: E402

run(sys.argv)
