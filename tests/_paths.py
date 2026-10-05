"""Import first in every test. Gives the run a private registry (never ~/.labconstrictor) with the example app
`labconstrictor_tools.examples.synthetic` registered against the interpreter running the tests.

Needs: pip install labconstrictor-tools  (+ this package, "napari[pyqt5]", pandas, scipy, imageio). Display: xvfb-run on Linux CI.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)  # screenshots and reports (git-ignored)
sys.path.insert(0, str(ROOT))

HOME = Path(tempfile.mkdtemp(prefix="lc_napari_test_home_"))
os.environ["LC_HOME"] = str(HOME)
subprocess.run(
    [sys.executable, "-m", "labconstrictor_tools", "register", "--name", "synthetic", "--prefix", sys.prefix,
     "--module", "labconstrictor_tools.examples.synthetic", "--version", "0.0"],
    check=True, capture_output=True,
)  # fmt: skip
