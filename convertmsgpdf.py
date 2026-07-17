import argparse
import hashlib
import html
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import extract_msg
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

# Import Rich components
from rich.console import Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table

from inventory_csv import PdfInventoryCsv


class HTMLToTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(value: str) -> str:
    parser = HTMLToTextParser()
    parser.feed(value or "")
    text = "".join(parser.parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def safe_str(value) -> str:
    return "" if value is None else str(value).strip()


def md5_hash(file_path: Path) -> str:
    hasher = hashlib.md5()
    with open(file_path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def parse_revision_key(stem: str) -> tuple[str, str]:
    match = re.search(r"[\s_]+(rev\d+[a-z]?)$", stem, flags=re.IGNORECASE)
    if match:
        revision = match.group(1)
        group_key = stem[: match.start()].strip(" _")
        return group_key, revision
    return stem, ""


def parse_revision_number(revision: str) -> int:
    match = re.match(r"rev(\d+)", revision or "", flags=re.IGNORECASE)
    if not match:
        return -1
    return int(match.group(1))


def format_creation_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def build_inventory_row(pdf_path: Path, output_root: Path) -> dict[str, str]:
    group_key, revision = parse_revision_key(pdf_path.stem)
    try:
        relative_path = pdf_path.relative_to(output_root.parent.parent).as_posix()
    except ValueError:
        relative_path = pdf_path.as_posix()

    file_stats = pdf_path.stat()
    creation_timestamp = file_stats.st_ctime
    return {
        "filename": pdf_path.name,
        "full_path": str(pdf_path.resolve()),
        "relative_path": relative_path,
        "file_size": str(file_stats.st_size),
        "reduced": "False",
        "group_key": group_key,
        "revision": revision,
        "revision_number": str(parse_revision_number(revision)),
        "creation_time": format_creation_time(creation_timestamp),
        "is_latest": "True",
        "md5_hash": md5_hash(pdf_path),
    }


def get_msg_body(msg) -> str:
    body = safe_str(getattr(msg, "body", ""))
    if body:
        return body

    html_body = safe_str(getattr(msg, "htmlBody", ""))
    if html_body:
        return html_to_text(html_body)

    return ""


def build_pdf(pdf_path: Path, meta: dict, body_text: str) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=meta.get("Subject", "") or pdf_path.stem,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "EmailTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        spaceAfter=10,
    )
    label_style = ParagraphStyle(
        "EmailLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        spaceAfter=2,
    )
    value_style = ParagraphStyle(
        "EmailValue",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "EmailBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        spaceAfter=8,
    )

    story = []

    subject = meta.get("Subject", "") or pdf_path.stem
    story.append(Paragraph(html.escape(subject), title_style))
    story.append(Spacer(1, 0.12 * inch))

    for label in ["From", "To", "Cc", "Date", "Subject"]:
        value = meta.get(label, "")
        if value:
            story.append(Paragraph(f"{html.escape(label)}:", label_style))
            story.append(Paragraph(html.escape(value), value_style))

    if not body_text.strip():
        body_text = "[No body text found]"

    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph("Message Body:", label_style))
    story.append(Spacer(1, 0.06 * inch))

    blocks = re.split(r"\n\s*\n", body_text.strip())
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        block = html.escape(block).replace("\n", "<br/>")
        story.append(Paragraph(block, body_style))
        story.append(Spacer(1, 0.06 * inch))

    doc.build(story)


def convert_msg_to_pdf(msg_path: Path, pdf_path: Path, inventory_csv: Path | None = None, output_root: Path | None = None) -> bool:
    msg = None
    try:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        msg = extract_msg.Message(str(msg_path))

        meta = {
            "From": safe_str(getattr(msg, "sender", "")) or safe_str(getattr(msg, "sender_email", "")),
            "To": safe_str(getattr(msg, "to", "")),
            "Cc": safe_str(getattr(msg, "cc", "")),
            "Date": safe_str(getattr(msg, "date", "")),
            "Subject": safe_str(getattr(msg, "subject", "")) or msg_path.stem,
        }

        body_text = get_msg_body(msg)
        build_pdf(pdf_path, meta, body_text)

        if inventory_csv is not None:
            inventory_store = PdfInventoryCsv(inventory_csv)
            inventory_store.append_row_if_missing(
                build_inventory_row(pdf_path, output_root or pdf_path.parent)
            )
        return True

    except Exception:
        return False

    finally:
        if msg is not None and hasattr(msg, "close"):
            try:
                msg.close()
            except Exception:
                pass


def iter_msg_files(root: Path):
    for path in root.rglob("*.msg"):
        if path.is_file():
            yield path


def main():
    parser = argparse.ArgumentParser(description="Convert .msg emails to lightweight PDF files.")
    parser.add_argument(
        "--input-dir",
        default="data",
        help="Root folder to scan recursively for .msg files (default: ./data).",
    )
    parser.add_argument(
        "--output-dir",
        default="output/emails",
        help="Folder to write PDFs into, preserving subfolder structure (default: ./output/emails).",
    )
    parser.add_argument(
        "--inventory-csv",
        default="output/pdf_inventory.csv",
        help="CSV file to append converted PDF metadata to (default: ./output/pdf_inventory.csv).",
    )

    args = parser.parse_args()

    root = Path(args.input_dir).expanduser().resolve()
    output_root = Path(args.output_dir).expanduser().resolve()
    inventory_csv = Path(args.inventory_csv).expanduser().resolve()

    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Folder does not exist or is not a directory: {root}")

    msg_files = list(iter_msg_files(root))
    total_files = len(msg_files)
    
    if not msg_files:
        print("No .msg files found.")
        return

    converted = 0
    failed = 0

    # 1. Setup the progress bar
    progress = Progress(
        TextColumn("[cyan]Processing PDFs[/cyan]"),
        BarColumn(bar_width=30, complete_style="cyan", finished_style="bright_green"),
        TaskProgressColumn(),
        MofNCompleteColumn(),
    )
    task_id = progress.add_task("converting", total=total_files)

    # 2. Live UI Layout Helper (Active state)
    def make_layout(current_file: str, current_failed: int) -> Table:
        grid = Table.grid(expand=True)
        grid.add_column()
        grid.add_row(progress)
        
        # Colorize active failures warning dynamically
        fail_color = "red" if current_failed > 0 else "dim"
        grid.add_row(f" └── 📩 [dim]{current_file:<40}[/dim]")
        return grid

    current_filename = "Starting..."

    # 3. Live Context Loop
    with Live(make_layout(current_filename, failed), refresh_per_second=12) as live:
        for msg_path in msg_files:
            current_filename = msg_path.name
            live.update(make_layout(current_filename, failed))

            rel_path = msg_path.relative_to(root)
            pdf_path = output_root / rel_path.with_suffix(".pdf")

            ok = convert_msg_to_pdf(msg_path, pdf_path, inventory_csv=inventory_csv, output_root=output_root)

            if ok:
                converted += 1
            else:
                failed += 1

            progress.advance(task_id)
            live.update(make_layout(current_filename, failed))

    # --- Final Detailed Summary Dashboard ---
    from rich.console import Console
    from rich.panel import Panel
    
    console = Console()
    
    # Determine look & feel based on job status
    if failed == 0:
        summary_title = "[white]Conversion Completed Successfully[/white]"
        fail_style = "dim"
    else:
        summary_title = "[red]Conversion Completed with Errors[/red]"
        fail_style = "bold red"

    # Build the dashboard tree contents
    summary_text = (
        f"├── Total Files Detected: [bold cyan]{total_files}[/bold cyan]\n"
        f"├── Successfully Saved:  [bold green]{converted}[/bold green]\n"
        f"└── Failed Processing:   [{fail_style}]{failed}[/{fail_style}]"
    )
    
    # Print the crisp panel break out
    console.print()  # Spacer
    console.print(
        Panel(
            summary_text,
            title=summary_title,
            title_align="left",
            border_style="white" if failed == 0 else "red",
            expand=False,
        )
    )


if __name__ == "__main__":
    main()