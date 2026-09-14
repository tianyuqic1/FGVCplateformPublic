from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_persistence_import_has_no_runtime_side_effects(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from finevision.persistence import MetadataStore; assert MetadataStore",
        ],
        cwd=tmp_path,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".finevision").exists()
