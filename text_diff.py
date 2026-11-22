"""Text-only diff model, independent of Qt and document presentation."""
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import zip_longest
from enum import Enum
import re


class IgnoreMode(str, Enum):
    NONE = "none"
    EDGES = "edges"
    EDGES_AND_EMPTY = "edges_and_empty"


@dataclass(frozen=True)
class DiffRow:
    left: int | None
    right: int | None
    changed: bool = False
    left_spans: tuple[tuple[int, int], ...] = ()
    right_spans: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class TextDiff:
    left_lines: tuple[str, ...]
    right_lines: tuple[str, ...]
    rows: tuple[DiffRow, ...]
    hunks: tuple[int, ...]


def line_content(line: str) -> str:
    return line[:-1].removesuffix("\r") if line.endswith("\n") else line


def _line_opcodes(left, right):
    # Trim shared edges before matching: large JSON documents often have
    # thousands of repeated lines and only a small changed region.
    prefix = 0
    limit = min(len(left), len(right))
    while prefix < limit and left[prefix] == right[prefix]:
        prefix += 1
    left_end, right_end = len(left), len(right)
    while left_end > prefix and right_end > prefix and left[left_end - 1] == right[right_end - 1]:
        left_end -= 1
        right_end -= 1
    if prefix:
        yield "equal", 0, prefix, 0, prefix
    matcher = SequenceMatcher(None, left[prefix:left_end], right[prefix:right_end], autojunk=False)
    for kind, a, b, c, d in matcher.get_opcodes():
        yield kind, a + prefix, b + prefix, c + prefix, d + prefix
    if left_end < len(left):
        yield "equal", left_end, len(left), right_end, len(right)


def inline_changes(left, right, highlight):
    if highlight == "lines":
        return
    if highlight == "words":
        tokens = [list(re.finditer(r"\w+|\s+|[^\w\s]", text)) for text in (left, right)]
        sequences = [[m.group() for m in matches] for matches in tokens]
        offsets = [[m.start() for m in matches] + [len(text)] for matches, text in zip(tokens, (left, right))]
    else:
        sequences = (left, right)
        offsets = (range(len(left) + 1), range(len(right) + 1))
    for kind, a, b, c, d in SequenceMatcher(None, *sequences, autojunk=False).get_opcodes():
        yield kind, offsets[0][a], offsets[0][b], offsets[1][c], offsets[1][d]


def _compare_exact(left: str, right: str, cancelled, highlight) -> TextDiff:
    # Split only at LF: Unicode paragraph separators remain part of the text.
    def lines(text):
        parts = text.split("\n")
        return tuple(part + "\n" for part in parts[:-1]) + (parts[-1],)

    left_lines, right_lines = lines(left), lines(right)
    rows, hunks = [], []
    if cancelled():
        return TextDiff(left_lines, right_lines, (), ())
    for kind, a, b, c, d in _line_opcodes(left_lines, right_lines):
        if cancelled():
            return TextDiff(left_lines, right_lines, (), ())
        if kind != "equal":
            hunks.append(len(rows))
        for li, ri in zip_longest(range(a, b), range(c, d)):
            if len(rows) % 1024 == 0 and cancelled():
                return TextDiff(left_lines, right_lines, (), ())
            ls, rs = [], []
            if kind == "replace" and li is not None and ri is not None:
                # Very long minified lines receive whole-line highlighting;
                # avoid quadratic character matching on megabyte strings.
                lt, rt = line_content(left_lines[li]), line_content(right_lines[ri])
                if max(len(lt), len(rt)) <= 4000:
                    for op, x, y, u, v in inline_changes(lt, rt, highlight):
                        if op != "equal":
                            if x != y:
                                ls.append((x, y))
                            if u != v:
                                rs.append((u, v))
            rows.append(DiffRow(li, ri, kind != "equal", tuple(ls), tuple(rs)))
    return TextDiff(left_lines, right_lines, tuple(rows), tuple(hunks))


def compare_text(left: str, right: str, cancelled=lambda: False,
                 mode: IgnoreMode = IgnoreMode.NONE, highlight="characters") -> TextDiff:
    mode = IgnoreMode(mode)
    if mode == IgnoreMode.NONE:
        return _compare_exact(left, right, cancelled, highlight)

    def prepare(text):
        parts = text.split("\n")
        raw = tuple(part + "\n" for part in parts[:-1]) + (parts[-1],)
        keys, indices, offsets = [], [], []
        for i, line in enumerate(raw):
            content = line_content(line)
            trimmed = content.strip(" \t")
            if mode == IgnoreMode.EDGES_AND_EMPTY and not trimmed:
                continue
            ending = ("\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "")
            keys.append(trimmed if mode == IgnoreMode.EDGES_AND_EMPTY else trimmed + ending)
            indices.append(i)
            offsets.append(len(content) - len(content.lstrip(" \t")))
        return raw, keys, indices, offsets

    ll, lk, lm, lo = prepare(left)
    rl, rk, rm, ro = prepare(right)
    # Compare keys directly: joining keys into text would lose empty terminal
    # line identity and line-ending distinctions.
    rows, hunks = [], []
    lp = rp = 0

    def padding(le, re):
        nonlocal lp, rp
        for li, ri in zip_longest(range(lp, le), range(rp, re)):
            rows.append(DiffRow(li, ri))
        lp, rp = le, re

    for kind, a, b, c, d in _line_opcodes(lk, rk):
        if cancelled():
            return TextDiff(ll, rl, (), ())
        first_change = True
        for li, ri in zip_longest(range(a, b), range(c, d)):
            left_index = lm[li] if li is not None else None
            right_index = rm[ri] if ri is not None else None
            padding(left_index if left_index is not None else lp,
                    right_index if right_index is not None else rp)
            if kind != "equal" and first_change:
                hunks.append(len(rows))
                first_change = False
            ls, rs = [], []
            if kind == "replace" and li is not None and ri is not None:
                lt, rt = line_content(lk[li]), line_content(rk[ri])
                if max(len(lt), len(rt)) <= 4000:
                    for op, x, y, u, v in inline_changes(lt, rt, highlight):
                        if op != "equal":
                            if x != y:
                                ls.append((x + lo[li], y + lo[li]))
                            if u != v:
                                rs.append((u + ro[ri], v + ro[ri]))
            rows.append(DiffRow(left_index, right_index, kind != "equal", tuple(ls), tuple(rs)))
            if left_index is not None:
                lp = left_index + 1
            if right_index is not None:
                rp = right_index + 1
    padding(len(ll), len(rl))
    return TextDiff(ll, rl, tuple(rows), tuple(hunks))


def utf16_offset(text: str, offset: int) -> int:
    return len(text[:offset].encode("utf-16-le")) // 2
