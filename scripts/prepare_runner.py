"""Create private local directories and the shared Router/runner control secret."""

import os
import secrets
from pathlib import Path

os.umask(0o077)
root = Path(__file__).resolve().parent.parent
for directory in (
    ".local/data/datasets",
    ".local/data/tokenizers",
    ".local/guidellm/runs",
    ".local/guidellm/cache",
):
    (root / directory).mkdir(parents=True, exist_ok=True)
key = root / ".local/runner.key"
if not key.exists():
    with key.open("x") as file:
        file.write(secrets.token_urlsafe(48) + "\n")
key.chmod(0o600)
print("GuideLLM runnerの保存先・接続キーを準備しました")
