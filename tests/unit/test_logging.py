import json
import logging

from backend.app.core.logging import JsonFormatter, bind_log_context, reset_log_context


def test_json_formatter_includes_bound_and_event_context() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="event",
        args=(),
        exc_info=None,
    )
    record.event_fields = {"project_id": "project-1"}
    token = bind_log_context(request_id="request-1", correlation_id="correlation-1")

    try:
        payload = json.loads(formatter.format(record))
    finally:
        reset_log_context(token)

    assert payload["message"] == "event"
    assert payload["request_id"] == "request-1"
    assert payload["correlation_id"] == "correlation-1"
    assert payload["project_id"] == "project-1"
