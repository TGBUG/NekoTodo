from __future__ import annotations

import argparse

import uvicorn

from nekotodo.config import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="NekoTodo backend")
    parser.add_argument("--config", default=None, help="path to config file")
    args = parser.parse_args()
    settings = load_settings(args.config)
    uvicorn.run("nekotodo.app:app", host=settings.server.host, port=settings.server.port)


if __name__ == "__main__":
    main()
