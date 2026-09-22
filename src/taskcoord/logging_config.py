from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler

from taskcoord.security import redact_text
from taskcoord.settings import Settings


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            record.args = tuple(redact_text(arg) if isinstance(arg, str) else arg for arg in record.args)
        return True


def configure_logging(settings: Settings) -> None:
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = TimedRotatingFileHandler(
        settings.log_path,
        when="midnight",
        backupCount=settings.log_retention_days,
        encoding="utf-8",
    )
    stream = logging.StreamHandler()
    redact = RedactFilter()
    for handler in (file_handler, stream):
        handler.setFormatter(formatter)
        handler.addFilter(redact)
        root.addHandler(handler)
    logging.getLogger("uvicorn.access").addFilter(redact)
