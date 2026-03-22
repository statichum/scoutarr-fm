from pathlib import Path
import glob
from typing import List

CONFIG_DIR = Path("/config")
FALLBACK_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def list_config_files() -> List[Path]:
    if CONFIG_DIR.exists():
        files = sorted(Path(p) for p in glob.glob(str(CONFIG_DIR / "*.y*ml")))
        if files:
            return files

    if FALLBACK_CONFIG_DIR.exists():
        files = sorted(Path(p) for p in glob.glob(str(FALLBACK_CONFIG_DIR / "*.y*ml")))
        if files:
            return files

    return []
