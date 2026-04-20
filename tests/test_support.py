from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = REPO_ROOT / ".tmp_tests"
TMP_ROOT.mkdir(exist_ok=True)


def make_config_dir(tmp_path: Path, *, target: str, cases: str, limits: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES_DIR / target, config_dir / "target_response.yaml")
    shutil.copyfile(FIXTURES_DIR / cases, config_dir / "plant_cases.yaml")
    shutil.copyfile(FIXTURES_DIR / limits, config_dir / "limits.yaml")
    return config_dir


@contextmanager
def workspace_temp_dir():
    path = TMP_ROOT / f"tmp_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
