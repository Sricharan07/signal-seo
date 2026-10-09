"""Parse content as data and locate one existing top-level metadata scalar."""

import json
import re
import tomllib
from dataclasses import dataclass

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken, DirectiveToken, TagToken

from signal_core.astro_source import strict_json


class FrontMatterUnavailable(ValueError):
    """Ambiguous or unsupported data cannot supply an editable scalar."""


@dataclass(frozen=True)
class FrontMatter:
    format: str
    fields: dict
    spans: dict[str, tuple[int, int, str]]


def _reject():
    raise FrontMatterUnavailable("FRONT_MATTER_PARSE_UNAVAILABLE")


def parse_front_matter(raw: bytes) -> FrontMatter:
    """Exactly one leading block. Bodies, including later fences, are opaque data."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 128 * 1024:
        _reject()
    try:
        source = raw.decode("utf8")
        if "\x00" in source or re.search(r"\r(?!\n)", source):
            _reject()
        start = int(source.startswith("\ufeff"))
        opening = re.match(r"(---|\+\+\+|;;;)(\r?\n)", source[start:])
        if opening:
            delimiter, newline = opening.groups()
            left = start + opening.end()
            close = re.search(r"^" + re.escape(delimiter) + r"(?:\r?\n|$)", source[left:], re.M)
            if close is None:
                _reject()
            body = source[left : left + close.start()]
            kind = {"---": "yaml", "+++": "toml", ";;;": "json"}[delimiter]
            # JSON delimited with --- is also used by content adapters.
            if kind == "yaml" and body.lstrip().startswith("{"):
                kind = "json"
        elif source[start:].startswith("{"):
            _, end = json.JSONDecoder().raw_decode(source, start)
            left, body, kind = start, source[start:end], "json"
            if source[end:] and not source[end:].startswith(("\n", "\r\n")):
                _reject()
            newline = "\r\n" if "\r\n" in source else "\n"
        else:
            _reject()
        if (newline == "\r\n" and re.search(r"(?<!\r)\n", body)) or (
            newline == "\n" and "\r" in body
        ):
            _reject()
        if kind == "yaml":
            fields, spans = _yaml(body)
        elif kind == "toml":
            fields, spans = _toml(body)
        else:
            fields, spans = _json(body)
        return FrontMatter(
            kind, fields, {k: (a + left, b + left, s) for k, (a, b, s) in spans.items()}
        )
    except (UnicodeError, ValueError, yaml.YAMLError, RecursionError, OverflowError, IndexError):
        _reject()


def _yaml(body):
    tokens = list(yaml.scan(body, Loader=yaml.SafeLoader))
    if len(tokens) > 8192 or any(
        isinstance(t, (AliasToken, AnchorToken, DirectiveToken, TagToken)) for t in tokens
    ):
        _reject()
    root = yaml.compose(body, Loader=yaml.SafeLoader)
    if not isinstance(root, MappingNode):
        _reject()

    def data(node, depth=0):
        if depth > 32:
            _reject()
        if isinstance(node, MappingNode):
            result = {}
            for key, value in node.value:
                if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                    _reject()
                if key.value in result or key.value == "<<":
                    _reject()
                result[key.value] = data(value, depth + 1)
            return (node.tag, result)
        if isinstance(node, SequenceNode):
            return (node.tag, [data(value, depth + 1) for value in node.value])
        return (node.tag, node.value)

    fields = data(root)[1]
    spans = {
        key.value: (value.start_mark.index, value.end_mark.index, value.style or "plain")
        for key, value in root.value
        if isinstance(value, ScalarNode) and value.tag == "tag:yaml.org,2002:str"
    }
    return fields, spans


def _json(body):
    fields = strict_json(body.encode("utf8"), 128 * 1024)
    decoder = json.JSONDecoder()
    pos, spans = body.index("{") + 1, {}
    while True:
        pos = _space(body, pos)
        if body[pos] == "}":
            break
        key, pos = decoder.raw_decode(body, pos)
        pos = _space(body, pos)
        if body[pos] != ":":
            _reject()
        start = _space(body, pos + 1)
        value, pos = decoder.raw_decode(body, start)
        if isinstance(value, str):
            spans[key] = (start, pos, '"')
        pos = _space(body, pos)
        if body[pos] == "}":
            break
        if body[pos] != ",":
            _reject()
        pos += 1
    return fields, spans


def _space(body, pos):
    while pos < len(body) and body[pos] in " \t\r\n":
        pos += 1
    return pos


def _toml(body):
    fields = tomllib.loads(body)
    spans = {}
    # Locate only direct bare keys before any table. tomllib proves the complete
    # document and the replacement semantics, including duplicate-key rejection.
    pos = 0
    while pos < len(body):
        end = body.find("\n", pos)
        end = len(body) if end < 0 else end + 1
        line = body[pos:end]
        if line.lstrip().startswith("["):
            break
        match = re.match(r"[ \t]*(title|description)[ \t]*=[ \t]*", line)
        if match:
            start = pos + match.end()
            if body[start : start + 1] not in {'"', "'"}:
                _reject()
            quote = body[start]
            style = quote * (3 if body.startswith(quote * 3, start) else 1)
            cursor = start + len(style)
            while cursor < len(body):
                if body.startswith(style, cursor):
                    stop = cursor + len(style)
                    if len(style) == 3:
                        while stop < len(body) and body[stop] == quote:
                            stop += 1
                    spans[match[1]] = (start, stop, style)
                    end = body.find("\n", stop)
                    end = len(body) if end < 0 else end + 1
                    break
                if quote == '"' and body[cursor] == "\\":
                    cursor += 2
                else:
                    cursor += 1
            else:
                _reject()
        elif line.strip() and not line.lstrip().startswith("#"):
            # An unrelated multiline/table value can obscure later root keys.
            # Refuse that mapping instead of guessing lexical context.
            if any(marker in line for marker in ('"""', "'''", "[", "{")):
                break
        pos = end
    return fields, spans


def prove_front_matter_edit(
    raw: bytes, *, field: str, offset: int, before: str, after: str
) -> bytes:
    if field not in {"title", "description"} or type(offset) is not int or offset < 0:
        _reject()
    if not all(
        isinstance(value, str) and 0 < len(value.encode("utf8")) <= 4096
        for value in (before, after)
    ):
        _reject()
    old = parse_front_matter(raw)
    source = raw.decode("utf8")
    span = old.spans.get(field)
    if (
        span is None
        or span[:2] != (offset, offset + len(before))
        or source[offset : span[1]] != before
    ):
        _reject()
    result = source[:offset] + after + source[span[1] :]
    new = parse_front_matter(result.encode("utf8"))
    other_old, other_new = dict(old.fields), dict(new.fields)
    old_value, new_value = other_old.pop(field, None), other_new.pop(field, None)
    if (
        old.format != new.format
        or other_old != other_new
        or old_value == new_value
        or field not in new.spans
        or new.spans[field] != (offset, offset + len(after), span[2])
        or _layout(before, span[2]) != _layout(after, span[2])
    ):
        _reject()
    return result.encode("utf8")


def _layout(value, style):
    lines = value.splitlines(keepends=True)
    layout = [
        (
            re.match(r"[ \t]*", line)[0] if i else "",
            "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "",
        )
        for i, line in enumerate(lines)
    ]
    if style in {"|", ">"}:
        layout[0] = (lines[0], layout[0][1])
    return layout
