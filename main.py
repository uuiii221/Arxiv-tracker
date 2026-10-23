# 便捷入口：python main.py 等价于 python -m arxiv_tracker.cli run --config config.yaml
import subprocess
import sys
from pathlib import Path


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    result = subprocess.run(
        [sys.executable, "-m", "arxiv_tracker.cli", "run", "--config", "config.yaml", *sys.argv[1:]],
        cwd=root,
        check=False,
    )
    sys.exit(result.returncode)
