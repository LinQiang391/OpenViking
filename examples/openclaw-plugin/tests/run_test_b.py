"""
B-group test runner — convenience wrapper for run_test_a.py with config-B.json.

B-group uses memory-core (no OpenViking dependency).

Usage:
  python run_test_b.py                    # uses config-B.json
  python run_test_b.py --skip-cleanup     # keep existing env
  python run_test_b.py --skip-ingest      # skip ingest
  python run_test_b.py --skip-qa          # skip QA
"""

import sys
from pathlib import Path

sys.argv.insert(1, str(Path(__file__).resolve().parent / "config-B.json"))

from run_test_a import main  # noqa: E402

main()
