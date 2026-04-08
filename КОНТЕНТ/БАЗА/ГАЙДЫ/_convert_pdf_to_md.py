# -*- coding: utf-8 -*-
"""Добавь пары (путь к PDF, имя .md) и запусти: py -3 _convert_pdf_to_md.py"""
import re
from pathlib import Path

import fitz

# (исходный PDF, имя файла в этой папке)
PDFS = [
    # пример:
    # (Path(r"c:\...\файл.pdf"), "Имя_файла.md", "Заголовок H1"),
]

HERE = Path(__file__).resolve().parent


def pdf_to_text(path: Path) -> str:
    doc = fitz.open(path)
    try:
        return "\n\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def despace_garbled_line(line: str) -> str:
    stripped = line.strip()
    if len(stripped) < 8:
        return line
    tokens = stripped.split()
    if len(tokens) < 6:
        return line
    if sum(1 for t in tokens if len(t) == 1) / len(tokens) < 0.55:
        return line
    words = []
    buf = []
    for t in tokens:
        if len(t) == 1 and t not in ("-", "–", "—"):
            buf.append(t)
        else:
            if buf:
                words.append("".join(buf))
                buf = []
            words.append(t)
    if buf:
        words.append("".join(buf))
    return "  ".join(words) if words else line


def fix_text(raw: str) -> str:
    lines = [despace_garbled_line(L) for L in raw.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def to_md(title: str, source_name: str, body: str) -> str:
    return (
        f"# {title}\n\n"
        f"*Источник: конвертация из PDF `{source_name}` (PyMuPDF).*\n\n"
        f"{body}\n"
    )


def main():
    if not PDFS:
        print("Добавь PDF в список PDFS в скрипте.")
        return
    for pdf_path, md_name, doc_title in PDFS:
        if not pdf_path.is_file():
            print("SKIP:", pdf_path)
            continue
        body = fix_text(pdf_to_text(pdf_path))
        (HERE / md_name).write_text(to_md(doc_title, pdf_path.name, body), encoding="utf-8")
        print("OK:", HERE / md_name)


if __name__ == "__main__":
    main()
