"""
limits.py
=========
Resource budgets for everything that reads untrusted input. One place, so the
HTTP layer, the batch packager and the workbook reader enforce the same numbers
and a reviewer can see every cap at once.

Enforcement points:
  * ingress  -- api.BodySizeLimitMiddleware rejects a request whose declared or
                streamed body exceeds MAX_REQUEST_BYTES before the handler runs;
  * handler  -- each upload field is read in bounded chunks (api._read_limited);
  * service  -- service.process_upload re-checks len(content) as a backstop;
  * archives -- batch._iter_zip and xlsx_locator.load_workbook check member
                counts, declared sizes, compression ratios and elapsed time
                before and during decompression.
"""
from __future__ import annotations

import time

MIB = 1024 * 1024

# One uploaded file.
MAX_UPLOAD_BYTES = 5 * MIB

# Pasted text arrives as a multipart form field; Starlette refuses a form part
# above 1 MiB (HTTP 400) before any handler runs, so this is the real limit for
# pasted text. Larger content must be uploaded as a file.
MAX_PASTE_BYTES = 1 * MIB

# One HTTP request body (multipart overhead plus every file in a batch). Nothing
# larger reaches a handler. A reverse proxy should also cap the body (DEPLOY.md).
MAX_REQUEST_BYTES = 32 * MIB

# ZIP archives submitted to /correct/batch (the archive itself, then its members).
MAX_ARCHIVE_BYTES = 24 * MIB
ZIP_MAX_MEMBERS = 200
ZIP_MAX_MEMBER_BYTES = MAX_UPLOAD_BYTES        # a member is one upload
ZIP_MAX_EXPANDED_BYTES = 64 * MIB              # sum of all members actually read
ZIP_MAX_COMPRESSION_RATIO = 100                # declared size / compressed size

# Excel workbooks (themselves ZIP containers).
XLSX_MAX_MEMBERS = 2000
XLSX_MAX_MEMBER_BYTES = 32 * MIB
XLSX_MAX_EXPANDED_BYTES = 96 * MIB
XLSX_MAX_COMPRESSION_RATIO = 200               # XML compresses hard; still bounded

# Wall-clock budget for one synchronous request's decompression + processing.
PROCESSING_TIME_BUDGET_S = 60.0

READ_CHUNK = 256 * 1024


class BudgetExceeded(ValueError):
    """A resource budget was hit. The message names the budget and the value."""


class Deadline:
    """A wall-clock budget that callers poll between units of work."""

    def __init__(self, seconds: float = PROCESSING_TIME_BUDGET_S):
        self.seconds = seconds
        self.start = time.monotonic()

    def remaining(self) -> float:
        return self.seconds - (time.monotonic() - self.start)

    def check(self, what: str = "processing") -> None:
        if self.remaining() < 0:
            raise BudgetExceeded(
                f"{what} exceeded the time budget of {self.seconds:.0f} s")
