"""Pure JSON transformation and cursor-path helpers for JSON Viewer."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Literal


KeyStyle = Literal["double", "single", "bare"]
StringQuote = Literal["double", "single"]


def _is_identifier_start(char: str) -> bool:
    """Return whether *char* is a JSON5 / ECMAScript identifier start."""
    return char in "$_" or char.isidentifier()


def _is_identifier_part(char: str) -> bool:
    """Return whether *char* is a JSON5 / ECMAScript identifier part."""
    # Python's ``isidentifier`` also handles Unicode combining marks when
    # they follow a valid start. ECMAScript additionally permits ZWNJ/ZWJ.
    return char in "$_\u200c\u200d" or ("a" + char).isidentifier()


def _read_json5_identifier(text: str, pos: int) -> tuple[str, int] | None:
    """Read an unquoted JSON5 key, decoding its ``\\uXXXX`` escapes.

    JSON5 property names use ECMAScript IdentifierName syntax, rather than
    ASCII-only identifiers. Keeping this scanner separate from tokenization
    lets formatting retain the original spelling while parsing gets the
    actual dictionary key.
    """
    value: list[str] = []
    is_start = True
    while pos < len(text):
        if text[pos] == "\\":
            if pos + 6 > len(text) or text[pos + 1] != "u":
                return None if is_start else ("".join(value), pos)
            digits = text[pos + 2:pos + 6]
            if not re.fullmatch(r"[0-9A-Fa-f]{4}", digits):
                return None if is_start else ("".join(value), pos)
            char = chr(int(digits, 16))
            next_pos = pos + 6
        else:
            char = text[pos]
            next_pos = pos + 1

        valid = _is_identifier_start(char) if is_start else _is_identifier_part(char)
        if not valid:
            break
        value.append(char)
        pos = next_pos
        is_start = False
    return ("".join(value), pos) if value else None


def _is_json5_identifier(value: str) -> bool:
    """Return whether a decoded key can be emitted without quotes in JSON5."""
    if not value or not _is_identifier_start(value[0]):
        return False
    return all(_is_identifier_part(char) for char in value[1:])


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
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in "\"'":
            quote = char
        elif char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        elif char == "/" and index + 1 < len(text) and text[index + 1] == "*":
            closing = text.find("*/", index + 2)
            index = len(text) if closing < 0 else closing + 2
            continue
        elif char in "[{":
            if not stack:
                starts.append(index)
            stack.append(char)
        elif char in "]}" and stack:
            expected = "[" if char == "]" else "{"
            if stack[-1] == expected:
                stack.pop()
        index += 1
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
    """Recursive parser for practical JSON5-style input."""

    _NUMBER = re.compile(
        r"[+-]?(?:0[xX][0-9A-Fa-f]+|(?:(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?))"
    )
    def __init__(self, text: str, start: int):
        self.text = text
        self.pos = start
        self.key_styles: set[KeyStyle] = set()

    def skip_ws(self) -> None:
        while self.pos < len(self.text):
            if self.text[self.pos].isspace():
                self.pos += 1
                continue
            if self.text.startswith("//", self.pos):
                newline = self.text.find("\n", self.pos + 2)
                self.pos = len(self.text) if newline < 0 else newline + 1
                continue
            if self.text.startswith("/*", self.pos):
                closing = self.text.find("*/", self.pos + 2)
                if closing < 0:
                    raise JsonToolError("块注释缺少结束符 */")
                self.pos = closing + 2
                continue
            return

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
        escapes = {
            "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
            "0": "\0", "/": "/", "\\": "\\", '"': '"', "'": "'",
        }
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
            if escaped in "\r\n":
                if escaped == "\r" and self.pos < len(self.text) and self.text[self.pos] == "\n":
                    self.pos += 1
                continue
            if escaped == "u":
                if self.pos < len(self.text) and self.text[self.pos] == "{":
                    closing = self.text.find("}", self.pos + 1)
                    digits = self.text[self.pos + 1:closing] if closing >= 0 else ""
                    if not digits or not re.fullmatch(r"[0-9A-Fa-f]{1,6}", digits):
                        raise JsonToolError("字符串包含无效的 Unicode 转义")
                    chars.append(chr(int(digits, 16)))
                    self.pos = closing + 1
                    continue
                digits = self.text[self.pos:self.pos + 4]
                if len(digits) != 4 or not re.fullmatch(r"[0-9A-Fa-f]{4}", digits):
                    raise JsonToolError("字符串包含无效的 Unicode 转义")
                chars.append(chr(int(digits, 16)))
                self.pos += 4
            elif escaped == "x":
                digits = self.text[self.pos:self.pos + 2]
                if len(digits) != 2 or not re.fullmatch(r"[0-9A-Fa-f]{2}", digits):
                    raise JsonToolError("字符串包含无效的十六进制转义")
                chars.append(chr(int(digits, 16)))
                self.pos += 2
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
        for token, value in (("Infinity", float("inf")), ("NaN", float("nan"))):
            for sign, multiplier in (("", 1), ("+", 1), ("-", -1)):
                shown = sign + token
                if self.text.startswith(shown, self.pos):
                    self.pos += len(shown)
                    return value * multiplier
        match = self._NUMBER.match(self.text, self.pos)
        if match:
            token = match.group()
            self.pos = match.end()
            try:
                return int(token, 0) if "x" in token.lower() else float(token) if any(
                    marker in token.lower() for marker in (".", "e")
                ) else int(token)
            except ValueError as exc:
                raise JsonToolError(f"无效数字：{token}") from exc
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
                identifier = _read_json5_identifier(self.text, self.pos)
                if identifier is None:
                    raise JsonToolError(f"第 {self.pos + 1} 个字符附近不是有效的属性名")
                key, self.pos = identifier
                style = "bare"
            self.key_styles.add(style)
            self.expect(":")
            result[key] = self.parse_value()
            self.skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1
                self.skip_ws()
                if self.pos < len(self.text) and self.text[self.pos] == "}":
                    self.pos += 1
                    return result
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
                self.skip_ws()
                if self.pos < len(self.text) and self.text[self.pos] == "]":
                    self.pos += 1
                    return result
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


@dataclass(frozen=True)
class _Json5Token:
    kind: Literal["punctuation", "string", "atom", "line_comment", "block_comment"]
    raw: str
    leading: str


def _json5_tokens(source: str) -> list[_Json5Token]:
    """Tokenize JSON5 source while retaining every non-whitespace token verbatim."""
    tokens: list[_Json5Token] = []
    pos = 0
    leading_start = 0
    punctuation = "{}[],:"
    while pos < len(source):
        if source[pos].isspace():
            pos += 1
            continue
        leading = source[leading_start:pos]
        if source.startswith("//", pos):
            end = source.find("\n", pos + 2)
            end = len(source) if end < 0 else end
            tokens.append(_Json5Token("line_comment", source[pos:end], leading))
            pos = end
        elif source.startswith("/*", pos):
            end = source.find("*/", pos + 2)
            if end < 0:
                raise JsonToolError("块注释缺少结束符 */")
            end += 2
            tokens.append(_Json5Token("block_comment", source[pos:end], leading))
            pos = end
        elif source[pos] in "\"'":
            quote = source[pos]
            start = pos
            pos += 1
            escaped = False
            while pos < len(source):
                char = source[pos]
                pos += 1
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    break
            if pos > len(source) or source[pos - 1] != quote:
                raise JsonToolError("字符串缺少结束引号")
            tokens.append(_Json5Token("string", source[start:pos], leading))
        elif source[pos] in punctuation:
            tokens.append(_Json5Token("punctuation", source[pos], leading))
            pos += 1
        else:
            start = pos
            while pos < len(source) and not source[pos].isspace() and source[pos] not in punctuation + "\"'":
                if source.startswith("//", pos) or source.startswith("/*", pos):
                    break
                pos += 1
            if start == pos:
                raise JsonToolError(f"第 {pos + 1} 个字符附近不是有效的 JSON5 标记")
            tokens.append(_Json5Token("atom", source[start:pos], leading))
        leading_start = pos
    return tokens


def _format_json5_tokens(tokens: list[_Json5Token]) -> str:
    """Pretty-print tokens without rewriting comments, literals, or key styles."""
    output: list[str] = []
    indent = 0
    line_start = True

    def write(text: str) -> None:
        nonlocal line_start
        if line_start:
            output.append("\t" * indent)
            line_start = False
        output.append(text)

    def newline() -> None:
        nonlocal line_start
        while output and output[-1] == " ":
            output.pop()
        if not line_start:
            output.append("\n")
        line_start = True

    for index, token in enumerate(tokens):
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if token.kind == "punctuation":
            if token.raw in "{[":
                write(token.raw)
                indent += 1
                if next_token is not None and not (
                    next_token.kind == "punctuation" and next_token.raw in "}]"):
                    newline()
            elif token.raw in "}]":
                indent = max(0, indent - 1)
                if not line_start:
                    newline()
                write(token.raw)
            elif token.raw == ",":
                write(",")
                if not (
                    next_token is not None
                    and next_token.kind == "block_comment"
                    and "\n" not in next_token.leading
                ):
                    newline()
            else:  # colon
                write(": ")
            continue

        if token.kind == "line_comment":
            if not line_start:
                write(" ")
            write(token.raw)
            newline()
            continue

        if token.kind == "block_comment":
            inline = not line_start and "\n" not in token.leading
            if inline:
                write(" ")
                write(token.raw)
                if not (
                    next_token is not None
                    and next_token.kind == "punctuation"
                    and next_token.raw == ","
                ):
                    newline()
            else:
                write(token.raw)
                newline()
            continue

        write(token.raw)

    return "".join(output).rstrip()


def format_json_like(text: str, parsed: ParsedJsonLike | None = None) -> tuple[str, ParsedJsonLike]:
    """Format JSON5-style source while preserving comments and literal spelling."""
    parsed = parsed or parse_json_like(text)
    return _format_json5_tokens(_json5_tokens(text[parsed.start:parsed.end])), parsed


def json5_minify_risks(text: str, parsed: ParsedJsonLike | None = None) -> frozenset[str]:
    """Return JSON5 source features that canonical minification would lose.

    Whitespace alone is intentionally not a risk: compacting ordinary,
    pretty-printed JSON is expected. The source span is limited to the parsed
    value so log prefixes and suffixes cannot trigger the confirmation.
    """
    parsed = parsed or parse_json_like(text)
    tokens = _json5_tokens(text[parsed.start:parsed.end])
    risks: set[str] = set()
    strict_number = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")

    def next_code_token(index: int) -> _Json5Token | None:
        return next(
            (token for token in tokens[index + 1:] if token.kind not in ("line_comment", "block_comment")),
            None,
        )

    for index, token in enumerate(tokens):
        following = next_code_token(index)
        if token.kind in ("line_comment", "block_comment"):
            risks.add("comments")
        elif token.kind == "string":
            if token.raw.startswith("'"):
                risks.add("single_quotes")
            if re.search(r"\\(?:\r\n|\r|\n|[vx]|0(?!\d)|u\{)", token.raw):
                risks.add("json5_string_escapes")
        elif token.kind == "atom":
            if following is not None and following.kind == "punctuation" and following.raw == ":":
                risks.add("bare_keys")
            elif token.raw not in ("true", "false", "null") and not strict_number.fullmatch(token.raw):
                risks.add("json5_literals")
        elif (
            token.kind == "punctuation"
            and token.raw == ","
            and following is not None
            and following.kind == "punctuation"
            and following.raw in "]}"
        ):
            risks.add("trailing_commas")
    return frozenset(risks)


def rewrite_json_like_quotes(
    text: str,
    *,
    key_style: KeyStyle | None = None,
    value_quote: StringQuote | None = None,
    parsed: ParsedJsonLike | None = None,
) -> tuple[str, ParsedJsonLike, int]:
    """Rewrite only JSON5 quote tokens while preserving all other source text.

    The returned count is the number of rewritten value strings for which the
    target quote needed an added escape character.
    """
    parsed = parsed or parse_json_like(text)
    tokens = _json5_tokens(text[parsed.start:parsed.end])
    converted: list[_Json5Token] = []
    escaped_value_count = 0
    for index, token in enumerate(tokens):
        is_key = (
            token.kind in ("string", "atom")
            and next(
                (
                    candidate.raw == ":"
                    for candidate in tokens[index + 1:]
                    if candidate.kind not in ("line_comment", "block_comment")
                ),
                False,
            )
        )
        if is_key and key_style is not None:
            key = (
                _JsonLikeValueParser(token.raw, 0).parse_string()[0]
                if token.kind == "string" else token.raw
            )
            if key_style == "bare" and _is_json5_identifier(key):
                raw, kind = key, "atom"
            elif key_style == "bare":
                raw, kind = token.raw, token.kind
            else:
                target_quote = "'" if key_style == "single" else '"'
                raw = token.raw if token.kind == "string" and token.raw.startswith(target_quote) else _json_string(key, target_quote)
                kind = "string"
            token = _Json5Token(kind, raw, token.leading)
        elif token.kind == "string" and not is_key and value_quote is not None:
            target_quote = "'" if value_quote == "single" else '"'
            if not token.raw.startswith(target_quote):
                value, _ = _JsonLikeValueParser(token.raw, 0).parse_string()
                escaped_value_count += int(target_quote in value)
                token = _Json5Token("string", _json_string(value, target_quote), token.leading)
        converted.append(token)
    source = "".join(token.leading + token.raw for token in converted)
    return text[:parsed.start] + source + text[parsed.end:], parsed, escaped_value_count


def _json_string(value: str, quote: str = '"') -> str:
    encoded = json.dumps(value, ensure_ascii=False)
    if quote == '"':
        return encoded
    body = encoded[1:-1].replace("'", "\\'")
    # A single-quoted string does not need escaped double quotes.
    body = body.replace('\\"', '"')
    return f"'{body}'"


def _render(
    value: Any,
    *,
    compact: bool,
    key_style: KeyStyle,
    string_quote: StringQuote,
    level: int = 0,
) -> str:
    if isinstance(value, dict):
        if not value:
            return "{}"
        pieces: list[str] = []
        for key, item in value.items():
            key = str(key)
            if key_style == "bare" and _is_json5_identifier(key):
                shown_key = key
            else:
                shown_key = _json_string(key, "'" if key_style == "single" else '"')
            sep = ":" if compact else ": "
            pieces.append(shown_key + sep + _render(
                item,
                compact=compact,
                key_style=key_style,
                string_quote=string_quote,
                level=level + 1,
            ))
        if compact:
            return "{" + ",".join(pieces) + "}"
        indent = "\t" * (level + 1)
        return "{\n" + indent + (",\n" + indent).join(pieces) + "\n" + "\t" * level + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        pieces = [_render(
            item,
            compact=compact,
            key_style=key_style,
            string_quote=string_quote,
            level=level + 1,
        ) for item in value]
        if compact:
            return "[" + ",".join(pieces) + "]"
        indent = "\t" * (level + 1)
        return "[\n" + indent + (",\n" + indent).join(pieces) + "\n" + "\t" * level + "]"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, str):
        return _json_string(value, "'" if string_quote == "single" else '"')
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def render_json(
    value: Any,
    *,
    compact: bool = False,
    key_style: KeyStyle = "double",
    string_quote: StringQuote = "double",
) -> str:
    """Render an already parsed value without parsing its current presentation again."""
    return _render(value, compact=compact, key_style=key_style, string_quote=string_quote)


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
        elif _is_json5_identifier(part):
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
        identifier = _read_json5_identifier(text, pos)
        if identifier:
            _, end = identifier
            start = pos
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
