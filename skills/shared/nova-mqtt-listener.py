# ruff: noqa: S311, S603, T201
# Jitter is not cryptography. The local user explicitly supplies an argv command;
# feed content enters stdin and is never interpreted by a shell.
#!/usr/bin/env python3
"""Local wake companion. No model calls until a feed item wakes the command."""

import argparse
import json
import os
import random
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx


class Companion:
    def __init__(self, origin: str, key: str, state: Path, command: list[str]):
        self.origin = origin.rstrip("/")
        self.state = state
        self.command = command
        self.http = httpx.Client(
            base_url=self.origin, headers={"Authorization": f"Bearer {key}"}, timeout=30
        )
        self.wake = threading.Event()
        saved = json.loads(state.read_text()) if state.exists() else {}
        self.client_id: str | None = saved.get("client_id")
        self.cursor = saved.get("cursor")

    def catch_up(self) -> None:
        """Only commit the cursor after the configured agent accepts the batch."""
        for _ in range(100):
            response = self.http.get(
                "/api/v1/feed",
                params={"since": self.cursor} if self.cursor else {},
                headers={"Accept": "application/feed+json"},
            )
            response.raise_for_status()
            feed: dict[str, Any] = response.json()
            cursor = feed["_nova"]["cursor"]
            if feed.get("items"):
                subprocess.run(
                    self.command,
                    input=json.dumps(feed),
                    text=True,
                    check=True,
                    timeout=600,
                )
            self.cursor = cursor
            self.save_state()
            if len(feed.get("items", [])) < 100:
                return
        self.wake.set()

    def save_state(self) -> None:
        self.state.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state.with_suffix(".tmp")
        saved = {"cursor": self.cursor}
        if self.client_id:
            saved["client_id"] = self.client_id
        temporary.write_text(json.dumps(saved) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.state)

    def connect(self):
        import paho.mqtt.client as mqtt

        response = self.http.post(
            "/api/v1/agents/notify/mqtt", json={"client_id": self.client_id}
        )
        if response.status_code == 404 and self.client_id:
            self.client_id = None
            return self.connect()
        response.raise_for_status()
        credentials = response.json()["data"]
        self.client_id = credentials["client_id"]
        self.save_state()
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=credentials["client_id"],
            protocol=mqtt.MQTTv5,
            transport="websockets",
        )
        client.tls_set()
        client.username_pw_set(credentials["username"], credentials["token"])
        client.ws_set_options(
            path="/mqtt?"
            + urlencode(
                {
                    "x-amz-customauthorizer-name": credentials["username"].split(
                        "=", 1
                    )[1],
                }
            )
        )
        client.reconnect_delay_set(min_delay=1, max_delay=60)

        def connected(client, _userdata, _flags, reason, _properties):
            if reason.is_failure:
                return
            client.subscribe(credentials["topic"], qos=1)
            self.wake.set()  # Catch up after every connection, including dropped wakes.

        def message(_client, _userdata, _message):
            # QoS1 duplicate wake hints coalesce; only Nova's feed advances the cursor.
            self.wake.set()

        client.on_connect = connected
        client.on_message = message
        client.connect(credentials["endpoint"], port=443, keepalive=60)
        client.loop_start()
        return client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument(
        "--state", type=Path, default=Path.home() / ".nova/inbox-cursor.json"
    )
    parser.add_argument("--fallback-seconds", type=int, default=900)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.fallback_seconds < 10:
        parser.error(
            "Supply a command after -- and a fallback interval of at least 10s"
        )
    if not args.origin.startswith("https://") and not args.origin.startswith(
        "http://localhost:"
    ):
        parser.error("Nova origin must use HTTPS")
    companion = Companion(args.origin, os.environ["NOVA_API_KEY"], args.state, command)
    # A process lock prevents two listeners racing the same durable cursor.
    import fcntl

    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        companion.wake.set()
        client = None
        next_connection = 0.0
        connection_failures = 0
        next_fallback = time.monotonic() + args.fallback_seconds
        try:
            while True:
                if time.monotonic() >= next_connection:
                    if client:
                        client.disconnect()
                        client.loop_stop()
                    try:
                        client = companion.connect()
                        connection_failures = 0
                        next_connection = (
                            time.monotonic() + 45 * 60 + random.uniform(0, 60)
                        )
                    except (httpx.HTTPError, OSError):
                        connection_failures += 1
                        next_connection = time.monotonic() + random.uniform(
                            10, min(900, 30 * 2 ** min(connection_failures, 5))
                        )
                signaled = companion.wake.wait(
                    max(1, min(next_fallback, next_connection) - time.monotonic())
                )
                if not signaled and time.monotonic() < next_fallback:
                    continue
                next_fallback = (
                    time.monotonic() + args.fallback_seconds * random.uniform(0.9, 1.1)
                )
                companion.wake.clear()
                try:
                    companion.catch_up()
                except (httpx.HTTPError, subprocess.SubprocessError, OSError):
                    # Leave the cursor untouched on failure. No credentials in logs.
                    print(
                        "Nova wake command or connection failed; retrying", flush=True
                    )
                    companion.wake.wait(random.uniform(10, 30))
                    companion.wake.set()
        except KeyboardInterrupt:
            pass
        finally:
            if client:
                client.disconnect()
                client.loop_stop()
            companion.http.close()


if __name__ == "__main__":
    main()
