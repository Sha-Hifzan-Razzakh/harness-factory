"""A tool server that dies at start (tests of the health probe, ENV-06)."""

import sys

sys.stderr.write("crash_server: cannot open its data\n")
sys.exit(3)
