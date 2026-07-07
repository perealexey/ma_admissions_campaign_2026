#!/usr/bin/env python
"""
parse_toc.py — разведка Этапа 0.

Парсит сохранённую (через веб-инспектор, Cocoa HTML Writer) копию
https://priem47.hse.ru/magstats.html и извлекает список целевых
xlsx-отчётов: ID программы, её название, кампус, тип места.

Формат входного файла — HTML-обёртка Cocoa, где реальная разметка страницы
лежит внутри <p>/<span> как экранированный текст (&lt;div&gt; и т.п.).
Стратегия: вырезать <body>...</body>, снять теги-обёртки <p>/<span>,
затем html.unescape() — и работать с получившимся текстом как с обычным HTML.
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path
from typing import NamedTuple

KEEP_CAMPUSES = {"Москва", "НИУ ВШЭ - Санкт-Петербург"}
KEEP_TYPES = {"Budget", "Commercial"}  # ForeignCommercial исключаем

RE_STRIP_WRAPPER = re.compile(r"</?(?:p|span)[^>]*>")
RE_CAMPUS_START = re.compile(r'class="ms-campus" id="mill_local_AppCampus_\d+"')
RE_CAMPUS_NAME = re.compile(r'ms-campus__name[^>]*>([^<]+)<')
RE_ROW_START = re.compile(r'<div class="ms-comp-table__row"')
RE_ROW_NAME = re.compile(r'ms-comp-table__cell--name">([^<]*)<')
RE_LINK = re.compile(
    r'href="/ABITREPORTS/MAGREPORTS/FullTime/(\d+)_(\w+)\.xlsx"'
)


class Target(NamedTuple):
    campus: str
    program_id: str
    program_name: str
    place_type: str  # Budget | Commercial

    @property
    def url(self) -> str:
        return (
            f"https://priem47.hse.ru/ABITREPORTS/MAGREPORTS/FullTime/"
            f"{self.program_id}_{self.place_type}.xlsx"
        )

    @property
    def filename(self) -> str:
        return f"{self.program_id}_{self.place_type}.xlsx"


def load_unescaped_body(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    body_start = raw.find("<body>")
    body_end = raw.find("</body>")
    if body_start == -1 or body_end == -1:
        raise ValueError(f"{path}: не найден <body>...</body> — формат файла неожиданный")
    body = raw[body_start:body_end]
    clean = RE_STRIP_WRAPPER.sub("", body)
    return html.unescape(clean)


def parse_toc(path: Path) -> list[Target]:
    text = load_unescaped_body(path)

    campus_bounds = [m.start() for m in RE_CAMPUS_START.finditer(text)]
    campus_bounds.append(len(text))

    targets: list[Target] = []
    for i in range(len(campus_bounds) - 1):
        segment = text[campus_bounds[i]:campus_bounds[i + 1]]
        name_m = RE_CAMPUS_NAME.search(segment)
        campus = name_m.group(1).strip() if name_m else "???"

        row_starts = [m.start() for m in RE_ROW_START.finditer(segment)]
        row_starts.append(len(segment))
        for j in range(len(row_starts) - 1):
            row = segment[row_starts[j]:row_starts[j + 1]]
            pname_m = RE_ROW_NAME.search(row)
            program_name = pname_m.group(1).strip() if pname_m else "???"

            for program_id, place_type in RE_LINK.findall(row):
                if campus in KEEP_CAMPUSES and place_type in KEEP_TYPES:
                    targets.append(Target(campus, program_id, program_name, place_type))

    return targets


def main():
    p = argparse.ArgumentParser(description="Этап 0: разведка списка целевых отчётов")
    p.add_argument("toc_html", type=Path, help="сохранённая копия magstats.html")
    p.add_argument("-o", "--out", type=Path, default=None, help="куда сохранить CSV со списком")
    args = p.parse_args()

    targets = parse_toc(args.toc_html)

    by_campus: dict[str, int] = {}
    by_type: dict[str, int] = {}
    program_ids = set()
    for t in targets:
        by_campus[t.campus] = by_campus.get(t.campus, 0) + 1
        by_type[t.place_type] = by_type.get(t.place_type, 0) + 1
        program_ids.add(t.program_id)

    print(f"Файл: {args.toc_html.name}")
    print(f"Целевых файлов к скачиванию: {len(targets)}")
    print(f"Уникальных программ (ID): {len(program_ids)}")
    print("По кампусам:", by_campus)
    print("По типам мест:", by_type)

    if args.out:
        import csv

        with args.out.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["campus", "program_id", "program_name", "place_type", "filename", "url"])
            for t in sorted(targets, key=lambda x: (x.campus, x.program_name, x.place_type)):
                writer.writerow([t.campus, t.program_id, t.program_name, t.place_type, t.filename, t.url])
        print(f"Список сохранён: {args.out}")


if __name__ == "__main__":
    main()
