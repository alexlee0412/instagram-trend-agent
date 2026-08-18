import os

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

SHEET_NAME = "raw_reels"

HEADERS = [
    "scraped_at",
    "discovery_rank",
    "reel_url",
    "username",
    "posted_at",
    "caption",
    "audio_name",
    "view_count",
    "like_count",
    "comment_count",
    "visual_description",
    "aesthetic_tags",
    "scrape_status",
]

WRAP_COLUMNS = {"caption", "visual_description", "aesthetic_tags"}


def _write_header(sheet: Worksheet):
    for col_index, header in enumerate(HEADERS, start=1):
        cell = sheet.cell(row=1, column=col_index, value=header)
        cell.font = Font(bold=True)
        if header in WRAP_COLUMNS:
            sheet.column_dimensions[cell.column_letter].width = 40
        else:
            sheet.column_dimensions[cell.column_letter].width = 18

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{sheet.cell(row=1, column=len(HEADERS)).column_letter}1"


def create_workbook_if_missing(path: str):
    if os.path.exists(path):
        return

    os.makedirs(os.path.dirname(path), exist_ok=True)

    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    sheet = workbook.create_sheet(SHEET_NAME)
    _write_header(sheet)

    workbook.save(path)


def append_row(path: str, row: dict):
    create_workbook_if_missing(path)

    workbook = load_workbook(path)
    sheet = workbook[SHEET_NAME]

    next_row = sheet.max_row + 1
    for col_index, header in enumerate(HEADERS, start=1):
        cell = sheet.cell(row=next_row, column=col_index, value=row.get(header))
        if header in WRAP_COLUMNS:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    workbook.save(path)
