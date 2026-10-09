"""Bounded Astro metadata inference; repository JavaScript is never host code."""

import ast
import json
from dataclasses import dataclass
from pathlib import PurePosixPath

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Parser

CONFIGS = frozenset({"astro.config.js", "astro.config.mjs", "astro.config.ts", "astro.config.mts"})
LOCKS = frozenset({"package-lock.json", "npm-shrinkwrap.json"})


class AstroSourceUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class AstroSource:
    config_path: str
    lockfile_path: str
    output_directory: str
    collection_entries: tuple[str, ...]
    collection_configs: tuple[str, ...]
    source_directory: str = "src"
    public_directory: str = "public"


def strict_json(content: bytes, maximum: int = 128 * 1024):
    if not isinstance(content, bytes) or not 0 < len(content) <= maximum:
        raise AstroSourceUnavailable("ASTRO_METADATA_INVALID")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AstroSourceUnavailable("ASTRO_METADATA_INVALID")
            result[key] = value
        return result

    try:

        def invalid_constant(value):
            raise ValueError("Non-JSON number")

        result = json.loads(content, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise AstroSourceUnavailable("ASTRO_METADATA_INVALID") from None
    if not isinstance(result, dict):
        raise AstroSourceUnavailable("ASTRO_METADATA_INVALID")
    return result


def astro_metadata_paths(files: dict[str, bytes]) -> tuple[str, str]:
    configs = sorted(CONFIGS & files.keys())
    locks = sorted(LOCKS & files.keys())
    if len(configs) != 1 or len(locks) != 1:
        raise AstroSourceUnavailable("ASTRO_CONFIG_OR_LOCKFILE_UNAVAILABLE")
    package = strict_json(files.get("package.json"))
    if not any(
        isinstance(package.get(key), dict) and isinstance(package[key].get("astro"), str)
        for key in ("dependencies", "devDependencies")
    ):
        raise AstroSourceUnavailable("ASTRO_DEPENDENCY_UNAVAILABLE")
    lock = strict_json(files[locks[0]])
    packages = lock.get("packages")
    if (
        type(lock.get("lockfileVersion")) is not int
        or lock.get("lockfileVersion") not in (2, 3)
        or not isinstance(packages, dict)
        or not isinstance(packages.get("node_modules/astro"), dict)
    ):
        raise AstroSourceUnavailable("ASTRO_LOCKFILE_UNAVAILABLE")
    return configs[0], locks[0]


def inspect_astro_source(files: dict[str, bytes]) -> AstroSource:
    config, lockfile = astro_metadata_paths(files)
    source = files[config]
    if len(source) > 64 * 1024:
        raise AstroSourceUnavailable("ASTRO_CONFIG_UNAVAILABLE")
    language = Language(
        tree_sitter_typescript.language_typescript()
        if config.endswith((".ts", ".mts"))
        else tree_sitter_javascript.language()
    )
    tree = Parser(language).parse(source)
    if tree.root_node.has_error:
        raise AstroSourceUnavailable("ASTRO_CONFIG_UNAVAILABLE")
    exports = [
        node
        for node in tree.root_node.named_children
        if node.type == "export_statement"
        and any(child.type == "default" for child in node.children)
    ]
    if len(exports) != 1:
        raise AstroSourceUnavailable("ASTRO_CONFIG_UNAVAILABLE")
    value = exports[0].child_by_field_name("value")
    if value is not None and value.type == "call_expression":
        function = value.child_by_field_name("function")
        arguments = value.child_by_field_name("arguments")
        if function.text != b"defineConfig" or len(arguments.named_children) != 1:
            raise AstroSourceUnavailable("ASTRO_CONFIG_UNAVAILABLE")
        value = arguments.named_children[0]
    if value is None or value.type != "object":
        raise AstroSourceUnavailable("ASTRO_CONFIG_UNAVAILABLE")
    settings = {}
    for pair in value.named_children:
        if pair.type == "comment":
            continue
        if pair.type != "pair":
            raise AstroSourceUnavailable("ASTRO_DYNAMIC_CONFIG_UNAVAILABLE")
        key = pair.child_by_field_name("key")
        if key.type == "property_identifier":
            name = key.text.decode("utf8")
        elif key.type == "string":
            name = _literal(key)
        else:
            raise AstroSourceUnavailable("ASTRO_DYNAMIC_CONFIG_UNAVAILABLE")
        if name in settings:
            raise AstroSourceUnavailable("ASTRO_DYNAMIC_CONFIG_UNAVAILABLE")
        settings[name] = pair.child_by_field_name("value")
    if "output" in settings and _literal(settings["output"]) != "static":
        raise AstroSourceUnavailable("ASTRO_SERVER_OUTPUT_UNAVAILABLE")
    output = _directory(_literal(settings["outDir"]) if "outDir" in settings else "./dist")
    src = _directory(_literal(settings["srcDir"]) if "srcDir" in settings else "./src")
    public = _directory(_literal(settings["publicDir"]) if "publicDir" in settings else "./public")
    if (
        output == src
        or output.startswith(src + "/")
        or output == public
        or output.startswith(public + "/")
        or public.startswith(output + "/")
        or any(path == output or path.startswith(output + "/") for path in files)
    ):
        raise AstroSourceUnavailable("ASTRO_OUTPUT_SCOPE_INVALID")
    entries = tuple(
        sorted(
            path
            for path in files
            if path.startswith(src + "/content/")
            and path.endswith((".md", ".mdx", ".json", ".yaml", ".yml"))
        )
    )
    collection_configs = tuple(
        sorted(
            path
            for path in files
            if path
            in {
                src + "/content.config.ts",
                src + "/content.config.js",
                src + "/content/config.ts",
                src + "/content/config.js",
            }
        )
    )
    return AstroSource(config, lockfile, output, entries, collection_configs, src, public)


def _literal(node):
    if node.type != "string" or any(
        child.type == "escape_sequence" for child in node.named_children
    ):
        raise AstroSourceUnavailable("ASTRO_DYNAMIC_CONFIG_UNAVAILABLE")
    try:
        value = ast.literal_eval(node.text.decode("utf8"))
    except (ValueError, SyntaxError, UnicodeError):
        raise AstroSourceUnavailable("ASTRO_DYNAMIC_CONFIG_UNAVAILABLE") from None
    if not isinstance(value, str):
        raise AstroSourceUnavailable("ASTRO_DYNAMIC_CONFIG_UNAVAILABLE")
    return value


def _directory(value):
    value = value.removeprefix("./")
    path = PurePosixPath(value)
    if (
        not value.isascii()
        or not 1 <= len(value) <= 120
        or path.is_absolute()
        or any(
            part in {"", ".", "..", "node_modules"}
            or part.startswith(".")
            or not all(char.isalnum() or char in "_-" for char in part)
            for part in value.split("/")
        )
    ):
        raise AstroSourceUnavailable("ASTRO_OUTPUT_SCOPE_INVALID")
    return value
