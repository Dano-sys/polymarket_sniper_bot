#!/usr/bin/env python3
"""Serve the sniper dashboard and JSON state files."""
from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from dashboard_snapshot import build_full_snapshot, write_snapshot

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8765


def _data_dir() -> Path:
    raw = (os.getenv("SNIPER_DATA_DIR") or os.getenv("DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return ROOT


class DashboardHandler(SimpleHTTPRequestHandler):
    server_version = "PolymarketSniperDashboard/1.0"

    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=directory or str(ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self.path = "/bot_dashboard.html"
        if self.path == "/api/refresh-pools":
            self._handle_refresh_pools()
            return
        if self.path == "/api/status":
            self._handle_status()
            return
        super().do_GET()

    def log_message(self, format: str, *args) -> None:
        if self.path.endswith((".json", "/api/status", "/api/refresh-pools")):
            return
        super().log_message(format, *args)

    def _read_json(self, name: str) -> Optional[object]:
        path = _data_dir() / name
        if not path.is_file():
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def _handle_refresh_pools(self) -> None:
        try:
            path = write_snapshot(build_full_snapshot())
            payload = {"ok": True, "path": str(path)}
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200 if payload.get("ok") else 500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_status(self) -> None:
        payload = {
            "pool_snapshot": self._read_json("pool_snapshot.json"),
            "positions": self._read_json("sniper_positions.json"),
            "trade_log": self._read_json("trade_log.json"),
            "paper_account": self._read_json("sniper_paper_account.json"),
            "kill_state": self._read_json("sniper_kill_state.json"),
            "open_orders": self._read_json("sniper_open_orders.json"),
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _maybe_refresh_pools() -> None:
    snapshot_path = _data_dir() / "pool_snapshot.json"
    if snapshot_path.is_file():
        return
    try:
        write_snapshot(build_full_snapshot(), path=snapshot_path)
        print(f"Wrote initial {snapshot_path.name}")
    except Exception as exc:
        print(f"Warning: could not build initial pool snapshot: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Polymarket sniper dashboard.")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", DEFAULT_PORT)))
    parser.add_argument(
        "--host",
        default=os.getenv("DASHBOARD_HOST", "127.0.0.1"),
        help="Bind address (use 0.0.0.0 on Fly)",
    )
    parser.add_argument(
        "--refresh-pools",
        action="store_true",
        help="Build a full pool snapshot before serving (slow; live CLOB checks)",
    )
    args = parser.parse_args()

    if args.refresh_pools:
        path = write_snapshot(build_full_snapshot())
        print(f"Wrote full pool snapshot to {path}")

    threading.Thread(target=_maybe_refresh_pools, daemon=True).start()

    server = ThreadingHTTPServer(
        (args.host, args.port),
        lambda *handler_args, **handler_kwargs: DashboardHandler(
            *handler_args, directory=str(ROOT), **handler_kwargs
        ),
    )
    print(f"Dashboard: http://{args.host}:{args.port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
