"""
Entry point for the Azure Backup Agent.

Usage::

    python main.py
    python main.py --config path/to/custom-config.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.agent import BackupAgent
from src.config import AppConfig
from src.logger_setup import setup_logging


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backup-agent",
        description=(
            "Monitors a local folder and keeps it mirrored in Azure Blob Storage."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        metavar="FILE",
        help="Path to the YAML configuration file (default: config/config.yaml)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if not args.config.exists():
        # Use basicConfig for this early error – the rotating logger is not
        # yet set up at this point.
        logging.basicConfig(level=logging.ERROR)
        logging.error("Config file not found: %s", args.config)
        sys.exit(1)

    try:
        config = AppConfig.from_yaml(args.config)
    except Exception as exc:
        logging.basicConfig(level=logging.ERROR)
        logging.error("Failed to load config: %s", exc)
        sys.exit(1)

    logger = setup_logging(config.logging)
    logger.info("Loaded config from: %s", args.config)

    agent = BackupAgent(config)
    agent.run()


if __name__ == "__main__":
    main()
