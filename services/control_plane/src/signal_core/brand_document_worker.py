"""Offline, bounded document text extraction in a disposable process."""

import re
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

MAX_TEXT_BYTES = 512 * 1024
MAX_DOCX_MEMBERS = 128
MAX_DOCX_UNCOMPRESSED = 8 * 1024 * 1024


def extract(path: Path, kind: str) -> str:
    if kind in {"text", "markdown"}:
        raw = path.read_bytes()
        if b"\x00" in raw:
            raise ValueError("binary_text")
        text = raw.decode("utf-8", errors="strict")
    elif kind == "pdf":
        raw = path.read_bytes()
        if not raw.startswith(b"%PDF-") or b"%%EOF" not in raw[-1024:]:
            raise ValueError("malformed_pdf")
        if re.search(rb"/(JavaScript|JS|Launch|EmbeddedFile|XFA)\b", raw):
            raise ValueError("active_pdf")
        info = subprocess.run(["pdfinfo", str(path)], capture_output=True, timeout=8, check=False)
        pages = re.search(rb"^Pages:\s*(\d+)\s*$", info.stdout, re.MULTILINE)
        if info.returncode or pages is None or not 1 <= int(pages.group(1)) <= 50:
            raise ValueError("pdf_page_limit")
        completed = subprocess.run(
            ["pdftotext", "-layout", "-nopgbrk", str(path), "-"],
            capture_output=True,
            timeout=8,
            check=False,
        )
        if completed.returncode or len(completed.stdout) > MAX_TEXT_BYTES:
            raise ValueError("pdf_extraction_failed")
        text = completed.stdout.decode("utf-8", errors="strict")
    elif kind == "docx":
        if not zipfile.is_zipfile(path):
            raise ValueError("malformed_docx")
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if (
                len(members) > MAX_DOCX_MEMBERS
                or sum(m.file_size for m in members) > MAX_DOCX_UNCOMPRESSED
            ):
                raise ValueError("docx_expansion_limit")
            names = {member.filename for member in members}
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise ValueError("malformed_docx")
            for member in members:
                name = member.filename.lower()
                if (
                    member.is_dir()
                    or name.startswith("/")
                    or ".." in Path(name).parts
                    or name.endswith((".bin", ".exe", ".dll", ".js", ".vbs"))
                    or "vba" in name
                    or "activex" in name
                    or "embeddings/" in name
                    or member.flag_bits & 1
                ):
                    raise ValueError("active_or_unsafe_docx")
            content_types = archive.read("[Content_Types].xml")
            if b"macroEnabled" in content_types or b"vbaProject" in content_types:
                raise ValueError("macro_docx")
            xml = archive.read("word/document.xml")
            if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
                raise ValueError("unsafe_xml")
            root = ElementTree.fromstring(xml)
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            text = "\n".join(
                "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
                for paragraph in root.iter(namespace + "p")
            )
    else:
        raise ValueError("unsupported_type")
    if not text.strip() or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("empty_or_oversized_text")
    return text


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    try:
        text = extract(Path(sys.argv[1]), sys.argv[2])
    except (
        OSError,
        ValueError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
        subprocess.TimeoutExpired,
    ) as error:
        reason = str(error) if isinstance(error, ValueError) else "extraction_failed"
        sys.stderr.write(reason[:64])
        return 3
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
