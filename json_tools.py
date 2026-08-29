"""Pure JSON transformation and cursor-path helpers for JSON Viewer."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal


KeyStyle = Literal["double", "single", "bare"]
_IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*$", re.UNICODE)


class JsonToolError(ValueError):
    """A user-facing error raised when no complete JSON value can be found."""


@dataclass(frozen=True)
class ParsedJsonLike:
    value: Any
    start: int
    end: int
    key_styles: frozenset[KeyStyle]

    @property
    def mixed(self) -> bool:
        return len(self.key_styles) > 1


def _outer_starts(text: str) -> list[int]:
    """Find object/array starts that are not nested inside another container."""
    starts: list[int] = []
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char in "[{":
            if not stack:
                starts.append(index)
            stack.append(char)
        elif char in "]}" and stack:
            expected = "[" if char == "]" else "{"
            if stack[-1] == expected:
                stack.pop()
    return starts


def extract_json(text: str) -> tuple[Any, int, int]:
    """Return the first complete object/array in *text* and its source span.

    Prefixes and suffixes are deliberately ignored.  Objects and arrays are
    preferred because matching a stray number in a log prefix is surprising.
    """
    decoder = json.JSONDecoder()
    best_error: json.JSONDecodeError | None = None
    # Only try structural values that begin at the outermost level.  Without
    # this guard an invalid outer object containing a valid array could be
    # silently replaced by that inner array on the next transformation.
    for start in _outer_starts(text):
        try:
            value, length = decoder.raw_decode(text[start:])
        except json.JSONDecodeError as exc:
            best_error = exc
            continue
        if isinstance(value, (dict, list)):
            return value, start, start + length

    detail = ""
    if best_error:
        detail = f"（附近第 {best_error.lineno} 行、第 {best_error.colno} 列）"
    raise JsonToolError(f"没有找到完整、有效的 JSON 对象或数组{detail}")


class _JsonLikeValueParser:
    """Recursive parser for JSON with single-quoted strings and bare keys."""

    _NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
    _BARE_KEY = re.compile(r"[A-Za-z_$][\w$]*", re.UNICODE)

    def __init__(self, text: str, start: int):
        self.text = text
        self.pos = start
        self.key_styles: set[KeyStyle] = set()

    def skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def expect(self, char: str) -> None:
        self.skip_ws()
        if self.pos >= len(self.text) or self.text[self.pos] != char:
            raise JsonToolError(f"第 {self.pos + 1} 个字符附近缺少 {char}")
        self.pos += 1

    def parse_string(self) -> tuple[str, KeyStyle]:
        quote = self.text[self.pos]
        style: KeyStyle = "double" if quote == '"' else "single"
        self.pos += 1
        chars: list[str] = []
        escapes = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "/": "/", "\\": "\\", '"': '"', "'": "'"}
        while self.pos < len(self.text):
            char = self.text[self.pos]
            self.pos += 1
            if char == quote:
                return "".join(chars), style
            if char != "\\":
                chars.append(char)
                continue
            if self.pos >= len(self.text):
                break
            escaped = self.text[self.pos]
            self.pos += 1
            if escaped == "u":
                digits = self.text[self.pos:self.pos + 4]
                if len(digits) != 4 or not re.fullmatch(r"[0-9A-Fa-f]{4}", digits):
                    raise JsonToolError("字符串包含无效的 Unicode 转义")
                chars.append(chr(int(digits, 16)))
                self.pos += 4
            elif escaped in escapes:
                chars.append(escapes[escaped])
            else:
                raise JsonToolError(f"字符串包含无效转义：\\{escaped}")
        raise JsonToolError("字符串缺少结束引号")

    def parse_value(self) -> Any:
        self.skip_ws()
        if self.pos >= len(self.text):
            raise JsonToolError("缺少 JSON 值")
        char = self.text[self.pos]
        if char == "{":
            return self.parse_object()
        if char == "[":
            return self.parse_array()
        if char in "\"'":
            value, _ = self.parse_string()
            return value
        for token, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(token, self.pos):
                self.pos += len(token)
                return value
        match = self._NUMBER.match(self.text, self.pos)
        if match:
            token = match.group()
            self.pos = match.end()
            return json.loads(token)
        raise JsonToolError(f"第 {self.pos + 1} 个字符附近不是有效的 JSON 值")

    def parse_object(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        self.pos += 1
        self.skip_ws()
        if self.pos < len(self.text) and self.text[self.pos] == "}":
            self.pos += 1
            return result
        while True:
            self.skip_ws()
            if self.pos >= len(self.text):
                raise JsonToolError("对象缺少结束大括号")
            if self.text[self.pos] in "\"'":
                key, style = self.parse_string()
            else:
                match = self._BARE_KEY.match(self.text, self.pos)
                if not match:
                    raise JsonToolError(f"第 {self.pos + 1} 个字符附近不是有效的属性名")
                key = match.group()
                self.pos = match.end()
                style = "bare"
            self.key_styles.add(style)
            self.expect(":")
            result[key] = self.parse_value()
            self.skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1
                continue
            if self.pos < len(self.text) and self.text[self.pos] == "}":
                self.pos += 1
                return result
            raise JsonToolError(f"第 {self.pos + 1} 个字符附近缺少逗号或结束大括号")

    def parse_array(self) -> list[Any]:
        result: list[Any] = []
        self.pos += 1
        self.skip_ws()
        if self.pos < len(self.text) and self.text[self.pos] == "]":
            self.pos += 1
            return result
        while True:
            result.append(self.parse_value())
            self.skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1
                continue
            if self.pos < len(self.text) and self.text[self.pos] == "]":
                self.pos += 1
                return result
            raise JsonToolError(f"第 {self.pos + 1} 个字符附近缺少逗号或结束方括号")


def parse_json_like(text: str) -> ParsedJsonLike:
    """Parse JSON-like input and report property-name styles used by it."""
    last_error: JsonToolError | None = None
    for start in _outer_starts(text):
        parser = _JsonLikeValueParser(text, start)
        try:
            value = parser.parse_value()
        except JsonToolError as exc:
            last_error = exc
            continue
        if isinstance(value, (dict, list)):
            return ParsedJsonLike(value, start, parser.pos, frozenset(parser.key_styles))
    if last_error:
        raise last_error
    raise JsonToolError("没有找到完整、有效的 JSON 对象或数组")


def _json_string(value: str, quote: str = '"') -> str:
    encoded = json.dumps(value, ensure_ascii=False)
    if quote == '"':
        return encoded
    body = encoded[1:-1].replace("'", "\\'")
    # A single-quoted string does not need escaped double quotes.
    body = body.replace('\\"', '"')
    return f"'{body}'"


def _render(value: Any, *, compact: bool, key_style: KeyStyle, level: int = 0) -> str:
    if isinstance(value, dict):
        if not value:
            return "{}"
        pieces: list[str] = []
        for key, item in value.items():
            key = str(key)
            if key_style == "bare" and _IDENTIFIER.match(key):
                shown_key = key
            else:
                shown_key = _json_string(key, "'" if key_style == "single" else '"')
            sep = ":" if compact else ": "
            pieces.append(shown_key + sep + _render(item, compact=compact, key_style=key_style, level=level + 1))
        if compact:
            return "{" + ",".join(pieces) + "}"
        indent = "  " * (level + 1)
        return "{\n" + indent + (",\n" + indent).join(pieces) + "\n" + "  " * level + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        pieces = [_render(item, compact=compact, key_style=key_style, level=level + 1) for item in value]
        if compact:
            return "[" + ",".join(pieces) + "]"
        indent = "  " * (level + 1)
        return "[\n" + indent + (",\n" + indent).join(pieces) + "\n" + "  " * level + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def render_json(value: Any, *, compact: bool = False, key_style: KeyStyle = "double") -> str:
    """Render an already parsed value without parsing its current presentation again."""
    return _render(value, compact=compact, key_style=key_style)


def transform(text: str, *, compact: bool = False, key_style: KeyStyle = "double") -> tuple[str, int, int, Any]:
    """Extract and render JSON; return output, removed prefix/suffix sizes, value."""
    value, start, end = extract_json(text)
    output = render_json(value, compact=compact, key_style=key_style)
    return output, start, len(text) - end, value


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    parts: tuple[str | int, ...]


class _PathParser:
    """Small tolerant parser for JSON plus the key styles emitted above."""

    def __init__(self, text: str):
        self.text = text
        self.n = len(text)
        self.spans: list[_Span] = []

    def ws(self, pos: int) -> int:
        while pos < self.n and self.text[pos].isspace():
            pos += 1
        return pos

    def string(self, pos: int) -> tuple[str, int]:
        quote = self.text[pos]
        start = pos
        pos += 1
        chars: list[str] = []
        while pos < self.n:
            char = self.text[pos]
            if char == quote:
                raw = "".join(chars)
                if quote == '"':
                    try:
                        return json.loads(self.text[start:pos + 1]), pos + 1
                    except json.JSONDecodeError:
                        return raw, pos + 1
                return raw.replace("\\'", "'").replace("\\\\", "\\"), pos + 1
            if char == "\\" and pos + 1 < self.n:
                chars.extend((char, self.text[pos + 1]))
                pos += 2
            else:
                chars.append(char)
                pos += 1
        raise ValueError("unterminated string")

    def value(self, pos: int, parts: tuple[str | int, ...]) -> int:
        pos = self.ws(pos)
        start = pos
        if pos >= self.n:
            raise ValueError("value expected")
        char = self.text[pos]
        if char == "{":
            pos = self.obj(pos, parts)
        elif char == "[":
            pos = self.array(pos, parts)
        elif char in "\"'":
            _, pos = self.string(pos)
        else:
            while pos < self.n and self.text[pos] not in ",]}\r\n":
                pos += 1
            pos = len(self.text[:pos].rstrip())
        self.spans.append(_Span(start, max(start + 1, pos), parts))
        return pos

    def obj(self, pos: int, parts: tuple[str | int, ...]) -> int:
        pos = self.ws(pos + 1)
        if pos < self.n and self.text[pos] == "}":
            return pos + 1
        while pos < self.n:
            key_start = pos
            if self.text[pos] in "\"'":
                key, pos = self.string(pos)
            else:
                colon = self.text.find(":", pos)
                if colon < 0:
                    raise ValueError("colon expected")
                key = self.text[pos:colon].strip()
                pos = colon
            pos = self.ws(pos)
            if pos >= self.n or self.text[pos] != ":":
                raise ValueError("colon expected")
            child = parts + (key,)
            pos = self.value(pos + 1, child)
            # The key itself should resolve to the value's path too.
            self.spans.append(_Span(key_start, pos, child))
            pos = self.ws(pos)
            if pos < self.n and self.text[pos] == ",":
                pos = self.ws(pos + 1)
                continue
            if pos < self.n and self.text[pos] == "}":
                return pos + 1
            raise ValueError("object delimiter expected")
        raise ValueError("unterminated object")

    def array(self, pos: int, parts: tuple[str | int, ...]) -> int:
        pos = self.ws(pos + 1)
        if pos < self.n and self.text[pos] == "]":
            return pos + 1
        index = 0
        while pos < self.n:
            pos = self.value(pos, parts + (index,))
            index += 1
            pos = self.ws(pos)
            if pos < self.n and self.text[pos] == ",":
                pos = self.ws(pos + 1)
                continue
            if pos < self.n and self.text[pos] == "]":
                return pos + 1
            raise ValueError("array delimiter expected")
        raise ValueError("unterminated array")


def format_path(parts: tuple[str | int, ...], include_root: bool = True) -> str:
    path = "$" if include_root else ""
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif _IDENTIFIER.match(part):
            path += ("." if path else "") + part
        else:
            escaped = part.replace("\\", "\\\\").replace("'", "\\'")
            path += f"['{escaped}']"
    return path or ("$" if include_root else "")


def path_at_position(text: str, position: int, include_root: bool = True) -> str:
    parser = _PathParser(text)
    try:
        parser.value(0, ())
    except (ValueError, IndexError):
        return "$" if include_root else ""
    candidates = [span for span in parser.spans if span.start <= position <= span.end]
    if not candidates:
        return "$" if include_root else ""
    # Most deeply nested span is the most useful cursor context.
    chosen = max(candidates, key=lambda span: (len(span.parts), -(span.end - span.start)))
    return format_path(chosen.parts, include_root)


def value_stats(value: Any) -> tuple[int, int]:
    """Return recursive container/key count and maximum depth."""
    count = 0
    max_depth = 0

    def walk(item: Any, depth: int) -> None:
        nonlocal count, max_depth
        max_depth = max(max_depth, depth)
        if isinstance(item, dict):
            count += len(item)
            for child in item.values():
                walk(child, depth + 1)
        elif isinstance(item, list):
            count += len(item)
            for child in item:
                walk(child, depth + 1)

    walk(value, 0)
    return count, max_depth


def searchable_spans(text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return lexical spans for property names and scalar property values.

    The scanner supports the same double-quoted, single-quoted and bare-key
    forms as the editor. Quotes are included in spans so selected source text
    can still be found when a scoped search is active.
    """
    keys: list[tuple[int, int]] = []
    values: list[tuple[int, int]] = []
    pos = 0
    length = len(text)
    number = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
    identifier = re.compile(r"[A-Za-z_$][\w$]*", re.UNICODE)

    while pos < length:
        char = text[pos]
        if char in "\"'":
            start = pos
            quote = char
            pos += 1
            escaped = False
            while pos < length:
                current = text[pos]
                pos += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    break
            end = pos
            lookahead = end
            while lookahead < length and text[lookahead].isspace():
                lookahead += 1
            (keys if lookahead < length and text[lookahead] == ":" else values).append((start, end))
            continue
        word = identifier.match(text, pos)
        if word:
            start, end = word.span()
            lookahead = end
            while lookahead < length and text[lookahead].isspace():
                lookahead += 1
            (keys if lookahead < length and text[lookahead] == ":" else values).append((start, end))
            pos = end
            continue
        numeric = number.match(text, pos)
        if numeric:
            values.append(numeric.span())
            pos = numeric.end()
            continue
        pos += 1
    return keys, values
