import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "resources", "lib"))

from runner import run_now  # noqa: E402

run_now()
