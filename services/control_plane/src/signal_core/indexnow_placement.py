"""Exact static key-file paths; no repository configuration is executed."""

from signal_core.astro_source import inspect_astro_source
from signal_core.indexnow_protocol import valid_indexnow_key


def indexnow_key_placement(framework, files, key):
    if not valid_indexnow_key(key):
        raise ValueError("Invalid IndexNow key.")
    filename = key + ".txt"
    if framework == "astro":
        astro = inspect_astro_source(files)
        return astro.public_directory + "/" + filename, astro.output_directory + "/" + filename
    if framework == "eleventy":
        return filename, "_site/" + filename
    if framework == "nextjs":
        return filename, ".next/" + filename
    raise ValueError("IndexNow key build format unavailable.")
