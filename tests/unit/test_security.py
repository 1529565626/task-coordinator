import logging

from taskcoord.logging_config import RedactFilter
from taskcoord.security import hash_token, new_token, redact_text, verify_token


def test_claim_token_is_hashed_and_not_reversible():
    token = new_token()
    stored = hash_token(token)
    assert token not in stored
    assert verify_token(token, stored)
    assert not verify_token(token + "x", stored)
    assert not verify_token("", stored)
    assert not verify_token(token, None)


def test_redacts_secrets_in_log_lines():
    message = 'Authorization: Bearer abc.def password=hunter2 claim_token=sekret Cookie: taskcoord_session=cook'
    redacted = redact_text(message)
    assert "abc.def" not in redacted
    assert "hunter2" not in redacted
    assert "sekret" not in redacted
    assert "cook" not in redacted
    assert "[REDACTED]" in redacted


def test_log_filter_redacts_record():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "Bearer abcdef token", (), None)
    assert RedactFilter().filter(record)
    assert "abcdef" not in record.msg
