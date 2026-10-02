import json
import os
import sys
import time
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LONG_POLL_SECONDS = 45


def _non_negative_float(raw: str | None, default: float) -> float:
    """Parse an env override; fall back to the default on missing/invalid/negative."""
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _status(payload: object) -> object:
    if not isinstance(payload, dict):
        return None
    data = cast(dict[str, object], payload).get("data", {})
    return (
        cast(dict[str, object], data).get("status") if isinstance(data, dict) else None
    )


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: wait-for-claim.py <base_url> <api_key>", file=sys.stderr)
        return 2

    base_url = sys.argv[1].rstrip("/")
    api_key = sys.argv[2]
    interval = _non_negative_float(os.getenv("NOVA_CLAIM_POLL_INTERVAL"), 2.0)
    # Bound the wait so the agent's shell can't poll forever if nobody claims.
    timeout = _non_negative_float(os.getenv("NOVA_CLAIM_POLL_TIMEOUT"), 1200.0)
    status_url = f"{base_url}/api/v1/agents/status"
    headers = {"Authorization": f"Bearer {api_key}"}

    deadline = time.monotonic() + timeout
    while True:
        wait = int(min(LONG_POLL_SECONDS, max(deadline - time.monotonic(), 0)))
        # base_url is the room's http(s) URL the agent was given.
        request = Request(f"{status_url}?wait={wait}", headers=headers)  # noqa: S310
        try:
            with urlopen(request, timeout=LONG_POLL_SECONDS + 15) as response:  # noqa: S310
                payload = json.load(response)
        except HTTPError as error:
            if error.code in {408, 429} or error.code >= 500:
                if time.monotonic() >= deadline:
                    break
                time.sleep(interval)
                continue
            print(f"Claim polling failed (HTTP {error.code}).", file=sys.stderr)
            return 1
        except (TimeoutError, URLError):
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
            continue
        except (json.JSONDecodeError, UnicodeDecodeError):
            print("Claim polling received an unexpected response.", file=sys.stderr)
            return 1

        status = _status(payload)
        if status == "claimed":
            print("Claim successful.", flush=True)
            return 0
        if status != "pending_claim":
            print("Claim polling received an unexpected response.", file=sys.stderr)
            return 1
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)

    print(
        f"Not claimed within {int(timeout)}s. Ask your human to open the claim link, "
        "then run this again.",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
