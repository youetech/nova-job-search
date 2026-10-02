import json
import os
import sys
import time
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
    if len(sys.argv) != 4:
        print(
            "Usage: wait-for-payment.py <base_url> <api_key> <payment_id>",
            file=sys.stderr,
        )
        return 2

    base_url = sys.argv[1].rstrip("/")
    api_key = sys.argv[2]
    payment_id = sys.argv[3]
    interval = _non_negative_float(os.getenv("NOVA_PAYMENT_POLL_INTERVAL"), 2.0)
    # Bound the wait so the agent's shell can't poll forever if nobody pays.
    timeout = _non_negative_float(os.getenv("NOVA_PAYMENT_POLL_TIMEOUT"), 1200.0)
    # base_url is the room's http(s) URL the agent was given.
    request = Request(  # noqa: S310
        f"{base_url}/api/v1/tools/get_payment",
        data=json.dumps({"payment_id": payment_id}).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    deadline = time.monotonic() + timeout
    while True:
        try:
            with urlopen(request, timeout=10) as response:  # noqa: S310
                payload = json.load(response)
        except HTTPError as error:
            if error.code in {408, 429} or error.code >= 500:
                if time.monotonic() >= deadline:
                    break
                time.sleep(interval)
                continue
            print(f"Payment polling failed (HTTP {error.code}).", file=sys.stderr)
            return 1
        except (TimeoutError, URLError):
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
            continue
        except (json.JSONDecodeError, UnicodeDecodeError):
            print("Payment polling received an unexpected response.", file=sys.stderr)
            return 1

        status = _status(payload)
        if status == "paid":
            print("Payment successful.", flush=True)
            return 0
        if status in {"failed", "expired"}:
            print(f"Payment {status}. Ask your human to try again.", file=sys.stderr)
            return 1
        if status != "pending":
            print("Payment polling received an unexpected response.", file=sys.stderr)
            return 1
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)

    print(
        f"Not paid within {int(timeout)}s. Ask your human to open the checkout link, "
        "then run this again.",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
