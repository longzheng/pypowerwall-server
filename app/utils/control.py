"""Control helpers shared by the HTTP API, the MQTT controls and the gateway
manager (kept here so none of them imports another's module for them)."""

import logging
import threading
from typing import Any, Callable, Optional


class _BatteryDispatchRejection(logging.Handler):
    """Capture only the current worker's explicit v1r dispatch rejection."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.worker_thread = threading.get_ident()
        self.reason: Optional[str] = None

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self.worker_thread:
            message = record.getMessage()
            if message.startswith("Battery dispatch rejected:"):
                self.reason = message


def call_battery_dispatch(method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Preserve gateway rejections that pypowerwall 0.22.1 only logs.

    Run inside the executor worker, under the existing write lock. A temporary
    handler captures only explicit rejections from this thread; unrelated poll
    errors and transport diagnostics stay in the logs. Remove it when the
    worker finishes, including after an HTTP timeout or a library exception.
    """
    library_logger = logging.getLogger("pypowerwall.tedapi.tedapi_v1r")
    rejection = _BatteryDispatchRejection()
    library_logger.addHandler(rejection)
    try:
        result = method(*args, **kwargs)
    finally:
        library_logger.removeHandler(rejection)
        rejection.close()
    if result is None and rejection.reason:
        return {"error": rejection.reason}
    return result


def is_error_result(result: object) -> bool:
    """True when a library response reports an error instead of a value."""
    return isinstance(result, dict) and ("error" in result or "ERROR" in result)


def is_v1r_gateway(gateway: Any, data: Any) -> bool:
    """True when a gateway uses the v1r transport (signed-command capable).

    Fail-closed like the Console gate (``tedapi_mode === 'v1r'``): the RSA
    key marks a v1r connection, but an unknown/unresolved mode (cold start,
    cloud failover) must NOT pass — otherwise islanding commands could be
    dispatched on a transport that cannot sign them. The library's signed
    islanding command works on Powerwall 2 and 3 alike, so hardware must
    NOT gate it — otherwise PW2 v1r users lose a control they have today.
    Shared by discovery (which buttons to announce) and the control loop
    (which islanding commands to accept).
    """
    try:
        if gateway is None or not getattr(gateway, "rsa_key_configured", False):
            return False
        if data is None:
            return False
        return getattr(data, "tedapi_mode", None) == "v1r"
    except Exception:
        return False
