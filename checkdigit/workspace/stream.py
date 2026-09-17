"""
stream.py
=========
Chunked readers for large files. Bytes are read in fixed chunks and decoded
with an incremental decoder so multibyte sequences split across a chunk
boundary are never corrupted. Records are yielded with the absolute character
offset (in the decoded text) of their raw span, so a later pass can splice
edits in place while streaming the same file again.

Readers:
  * text_chunks      : bytes -> decoded text chunks with a chosen codec
  * csv_records      : RFC 4180 records across quoted newlines and chunk boundaries
  * line_records     : one record per physical line (plain text, fixed-width)
  * edifact_segments : segments across escaped delimiters and chunk boundaries

The codec is chosen once from the first chunk: strict UTF-8 if it decodes,
otherwise ISO-8859-1 for the whole file (the same rule as service.py).
"""
from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

CHUNK_BYTES = 1024 * 1024


def choose_codec(head: bytes) -> str:
    """Codec guess from a prefix; only reliable when `head` is the whole file."""
    try:
        codecs.getincrementaldecoder("utf-8")(errors="strict").decode(head, final=True)
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


def sniff_codec(path: str, chunk_bytes: int = CHUNK_BYTES) -> str:
    """Strict UTF-8 over the whole file, else ISO-8859-1. One sequential read."""
    dec = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(chunk_bytes)
                if not chunk:
                    dec.decode(b"", final=True)
                    return "utf-8"
                dec.decode(chunk, final=False)
    except UnicodeDecodeError:
        return "latin-1"


def text_chunks(path: str, codec: Optional[str] = None, chunk_bytes: int = CHUNK_BYTES
                ) -> Iterator[Tuple[str, str]]:
    """Yield (codec, text_chunk). Every item carries the codec in use.

    Pass the codec recorded for the source when it is known; otherwise the
    whole file is sniffed first, because a prefix cannot prove UTF-8.
    """
    codec = codec or sniff_codec(path, chunk_bytes)
    with open(path, "rb") as fh:
        head = fh.read(chunk_bytes)
        dec = codecs.getincrementaldecoder(codec)(errors="strict")
        text = dec.decode(head, final=not head)
        if text:
            yield codec, text
        if not head:
            return
        while True:
            chunk = fh.read(chunk_bytes)
            if not chunk:
                tail = dec.decode(b"", final=True)
                if tail:
                    yield codec, tail
                return
            text = dec.decode(chunk, final=False)
            if text:
                yield codec, text


def texts(path: str, codec: Optional[str] = None, chunk_bytes: int = CHUNK_BYTES) -> Iterator[str]:
    for _codec, text in text_chunks(path, codec, chunk_bytes):
        yield text


@dataclass
class Field:
    col: int
    value: str
    start: int        # absolute char offset of the raw field text (including quotes)
    end: int
    quoted: bool


@dataclass
class Record:
    no: int           # 0-based record index (a header row counts)
    start: int        # absolute char offset of the record start
    end: int          # absolute char offset just past the record text (before the line break)
    fields: List[Field]


def csv_records(chunks: Iterator[str], delimiter: str) -> Iterator[Record]:
    """RFC 4180 state machine over a stream of text chunks.

    States: plain field, quoted field, and "after a quote inside a quoted
    field" (the next character decides between an escaped quote and the end
    of the quoted section). The state survives chunk boundaries, as does a
    trailing CR that may be the first half of CRLF. Runs of ordinary
    characters are copied with a regex scan rather than one character at a
    time.
    """
    special = re.compile("[" + re.escape(delimiter) + "\"\r\n]")
    row: List[Field] = []
    buf: List[str] = []
    in_quotes = False
    after_quote = False
    quoted_field = False
    field_start = 0
    record_start = 0
    pos = 0
    rec_no = 0
    pending_cr = False
    saw_any = False

    for chunk in chunks:
        i = 0
        n = len(chunk)
        if n:
            saw_any = True
        if pending_cr:
            pending_cr = False
            if n and chunk[0] == "\n":
                i = 1
                pos += 1
                field_start = pos
                record_start = pos
        while i < n:
            if in_quotes:
                if after_quote:
                    after_quote = False
                    if chunk[i] == '"':
                        buf.append('"')
                        i += 1
                        pos += 1
                        continue
                    in_quotes = False
                    # the character at i is handled below as plain text
                else:
                    j = chunk.find('"', i)
                    if j < 0:
                        buf.append(chunk[i:])
                        pos += n - i
                        i = n
                        continue
                    buf.append(chunk[i:j])
                    pos += j - i + 1
                    i = j + 1
                    after_quote = True
                    continue
            ch = chunk[i]
            if ch == '"' and pos == field_start:
                in_quotes = True
                quoted_field = True
                i += 1
                pos += 1
                continue
            if ch == delimiter:
                row.append(Field(len(row), "".join(buf), field_start, pos, quoted_field))
                buf = []
                quoted_field = False
                i += 1
                pos += 1
                field_start = pos
                continue
            if ch == "\r" or ch == "\n":
                row.append(Field(len(row), "".join(buf), field_start, pos, quoted_field))
                buf = []
                quoted_field = False
                rec = Record(rec_no, record_start, pos, row)
                row = []
                rec_no += 1
                if ch == "\r":
                    if i + 1 < n:
                        step = 2 if chunk[i + 1] == "\n" else 1
                    else:
                        pending_cr = True
                        step = 1
                else:
                    step = 1
                i += step
                pos += step
                field_start = pos
                record_start = pos
                yield rec
                continue
            m = special.search(chunk, i)
            j = m.start() if m else n
            buf.append(chunk[i:j])
            pos += j - i
            i = j
    if saw_any and (buf or row or quoted_field or pos > record_start):
        row.append(Field(len(row), "".join(buf), field_start, pos, quoted_field))
        yield Record(rec_no, record_start, pos, row)


def line_records(chunks: Iterator[str]) -> Iterator[Tuple[int, int, str]]:
    """Yield (line_no_1based, absolute_start, line_text_without_break)."""
    carry = ""
    pos = 0
    line_no = 0
    pending_cr = False
    breaks = re.compile("[\r\n]")
    for chunk in chunks:
        text = carry + chunk
        carry = ""
        start = 0
        n = len(text)
        if pending_cr:
            pending_cr = False
            if n and text[0] == "\n":
                start = 1
        i = start
        while i < n:
            m = breaks.search(text, i)
            if m is None:
                break
            i = m.start()
            line_no += 1
            yield line_no, pos + start, text[start:i]
            if text[i] == "\r":
                if i + 1 < n:
                    i += 2 if text[i + 1] == "\n" else 1
                else:
                    pending_cr = True
                    i += 1
            else:
                i += 1
            start = i
        carry = text[start:]
        pos += start
    if carry:
        line_no += 1
        yield line_no, pos, carry


def edifact_segments(chunks: Iterator[str], release: str, terminator: str) -> Iterator[Tuple[int, str]]:
    """Yield (absolute_start, raw_segment) honouring the release character.

    The raw segment keeps release characters so the caller can splice it back
    verbatim; a UNA header (9 characters) is skipped, not yielded.
    """
    buf: List[str] = []
    pos = 0
    start = 0
    escaped = False
    skipping_ws = True
    head = ""
    chunks = iter(chunks)
    # The UNA service string advice is nine characters and may span chunks.
    for chunk in chunks:
        head += chunk
        if len(head) >= 9 or not head.startswith("UNA"[:len(head)]):
            break
    if head.startswith("UNA") and len(head) >= 9:
        pos = start = 9
        head = head[9:]

    def with_head():
        if head:
            yield head
        yield from chunks

    for chunk in with_head():
        i = 0
        n = len(chunk)
        while i < n:
            ch = chunk[i]
            if skipping_ws:
                if ch in "\r\n \t":
                    i += 1
                    pos += 1
                    start = pos
                    continue
                skipping_ws = False
            if escaped:
                buf.append(ch)
                escaped = False
            elif ch == release:
                buf.append(ch)
                escaped = True
            elif ch == terminator:
                seg = "".join(buf)
                if seg.strip():
                    yield start, seg
                buf = []
                skipping_ws = True
                i += 1
                pos += 1
                start = pos
                continue
            else:
                buf.append(ch)
            i += 1
            pos += 1
    tail = "".join(buf)
    if tail.strip():
        yield start, tail
