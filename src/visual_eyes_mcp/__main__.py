"""Console entry point."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys

from . import __version__
from .config import load_settings
from .errors import ConfigError
from .server import create_server
from .tools import App

_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")


class RedactingFormatter(logging.Formatter):
    """Scrub the API key and key-shaped strings from every log line."""

    def __init__(self, fmt: str | None = None, *, secret: str | None = None) -> None:
        super().__init__(fmt)
        self._secrets = [secret] if secret else []

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            if secret:
                text = text.replace(secret, "***")
        return _KEY_RE.sub("***", text)


def configure_logging(secret: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", secret=secret)
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="visual-eyes-mcp",
        description="Local stdio MCP server for video understanding and frame extraction.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="path to config.toml (overrides VISUAL_EYES_CONFIG)",
    )
    args = parser.parse_args(argv)

    if args.config:
        os.environ["VISUAL_EYES_CONFIG"] = args.config

    configure_logging()
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"visual-eyes-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    secret = settings.api_key.get_secret_value() if settings.api_key else None
    configure_logging(secret)
    logging.getLogger("visual_eyes_mcp").info(settings.startup_summary())

    app = App.from_settings(settings)
    create_server(app).run("stdio")


if __name__ == "__main__":
    main()
