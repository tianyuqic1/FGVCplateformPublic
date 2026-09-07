from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_store_import_does_not_boot_legacy_fastapi(tmp_path: Path) -> None:
    environment = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-c", "from finevision.api.store import MetadataStore; assert MetadataStore"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".finevision-api").exists()
