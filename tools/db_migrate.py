from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def run_upgrade(revision: str = "head") -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    command.upgrade(config, revision)


def main() -> None:
    run_upgrade("head")


if __name__ == "__main__":
    main()
