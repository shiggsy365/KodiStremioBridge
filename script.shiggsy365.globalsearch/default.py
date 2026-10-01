import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "resources", "lib"))

from globalsearch.runner import run  # noqa: E402

run(sys.argv)
