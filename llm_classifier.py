import base64
import csv
import json
import logging
import os
import sys
import tempfile

import fitz


from dataclasses import dataclass

from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

load_dotenv(override=True)

DEFAULT_CSV_PATH = Path("./output/pdf_inventory.csv")
DEFAULT_DOCUMENT_TYPES = {"image", "text"}
LOGGER = logging.getLogger(__name__)


@dataclass
class LLMClassificationResult:
    document_type: str
    confidence: float
    reason: str
    raw_json: str


class LLMClassifier:
    """Classify latest PDFs from a scanner CSV using an LLM with page images."""

    def __init__(self, csv_path: str | Path = DEFAULT_CSV_PATH):
        self.csv_path = Path(csv_path)
        self.output_csv = self.csv_path

        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        self.api_key = os.getenv("AZURE_OPENAI_API_KEY")

        self._validate_configuration()
        LOGGER.info("Azure OpenAI: '%s'", self.deployment_name)
        self.client = OpenAI(base_url=self.endpoint, api_key=self.api_key)
        

    def classify_latest_documents(self, show_progress: bool = True) -> Path:
        """Read the CSV, classify latest PDFs, and write results back in place."""

        console = Console(stderr=True)
        self._configure_rich_logging(console)
        LOGGER.info("Starting classification workflow")
        rows = self._load_rows()
        fieldnames = self._ensure_output_columns(rows)
        total_latest = sum(1 for row in rows if self._is_latest(row))
        LOGGER.info("Loaded %d inventory rows; %d latest rows require review", len(rows), total_latest)

        progress_context = (
            Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=console,
            )
            if show_progress and total_latest
            else None
        )

        if progress_context is None:
            for row in rows:
                self._process_row(row, total_latest=total_latest, progress=None, console=console)
        else:
            with progress_context as progress:
                task_id = progress.add_task("Classifying latest PDFs", total=total_latest)
                for row in rows:
                    self._process_row(row, total_latest=total_latest, progress=progress, console=console, task_id=task_id)

        self._write_rows_safely(rows, fieldnames)
        LOGGER.info("Classification workflow completed: %s", self.output_csv)
        return self.output_csv

    def _configure_rich_logging(self, console: Console) -> None:
        """Send classifier logs through the progress bar's console."""
        for handler in LOGGER.handlers[:]:
            LOGGER.removeHandler(handler)
            handler.close()

        handler = RichHandler(
            console=console,
            show_path=False,
            markup=False,
            rich_tracebacks=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
        LOGGER.propagate = False

    def _process_row(
        self,
        row: dict[str, Any],
        *,
        total_latest: int,
        progress: Progress | None,
        console: Console,
        task_id: int | None = None,
    ) -> None:
        filename = row.get("filename") or row.get("relative_path") or "<unknown>"
        LOGGER.info("Starting row: %s", filename)
        if not self._is_latest(row):
            row.setdefault("llm_document_type", "")
            row.setdefault("llm_confidence", "")
            row.setdefault("llm_reason", "")
            row.setdefault("llm_raw_json", "")
            self._log_file_result(row, None, skipped=True, reason="not latest", console=console)
            return

        filename = row.get("filename") or row.get("relative_path") or "<unknown>"
        if progress is not None and task_id is not None:
            progress.update(task_id, description=f"Processing {filename}")

        if self._has_existing_classification(row):
            classification = self._classification_from_row(row)
            self._log_file_result(row, classification, skipped=True, console=console)
        else:
            classification = self._classify_row(row)
            row["llm_document_type"] = classification.document_type
            row["llm_confidence"] = f"{classification.confidence:.4f}"
            row["llm_reason"] = classification.reason
            row["llm_raw_json"] = classification.raw_json
            self._log_file_result(row, classification, skipped=False, console=console)

        if progress is not None and task_id is not None:
            progress.advance(task_id)


    def _validate_configuration(self) -> None:

        missing = []
        if not self.endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if not self.deployment_name:
            missing.append("AZURE_OPENAI_DEPLOYMENT")

        if not self.api_key:
            missing.append("AZURE_OPENAI_API_KEY")
        if missing:
            LOGGER.error("Missing required environment variables: %s", ", ".join(missing))
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    def _load_rows(self) -> list[dict[str, Any]]:
        LOGGER.info("Loading inventory CSV: %s", self.csv_path)
        if not self.csv_path.exists():
            LOGGER.error("CSV file not found: %s", self.csv_path)
            raise FileNotFoundError(f"CSV file not found: {self.csv_path}")

        with open(self.csv_path, newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            rows = list(reader)
        LOGGER.info("Inventory CSV loaded successfully")
        return rows

    def _ensure_output_columns(self, rows: list[dict[str, Any]]) -> list[str]:
        desired_columns = ["llm_document_type", "llm_confidence", "llm_reason", "llm_raw_json"]
        fieldnames = list(rows[0].keys()) if rows else []

        for column in desired_columns:
            if column not in fieldnames:
                fieldnames.append(column)
            for row in rows:
                row.setdefault(column, "")
        return fieldnames

    def _is_latest(self, row: dict[str, Any]) -> bool:
        value = str(row.get("is_latest", "")).strip().lower()
        return value in {"true", "1", "yes", "y"}

    def _has_existing_classification(self, row: dict[str, Any]) -> bool:
        return bool(str(row.get("llm_document_type", "")).strip())

    def _classification_from_row(self, row: dict[str, Any]) -> LLMClassificationResult:
        return LLMClassificationResult(
            document_type=str(row.get("llm_document_type", "")).strip() or "unclear",
            confidence=self._safe_float(row.get("llm_confidence", 0.0)),
            reason=str(row.get("llm_reason", "")).strip() or "Existing classification.",
            raw_json=str(row.get("llm_raw_json", "")).strip() or "{}",
        )

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _log_file_result(
        self,
        row: dict[str, Any],
        classification: LLMClassificationResult | None,
        *,
        skipped: bool,
        reason: str | None = None,
        console: Console | None = None,
    ) -> None:
        filename = row.get("filename") or row.get("relative_path") or "<unknown>"
        if classification is None:
            message = f"{filename}: skipped ({reason or 'skipped'})"
        elif skipped:
            message = f"{filename}: skipped (already {classification.document_type})"
        else:
            message = f"{filename}: {classification.document_type} ({classification.confidence:.4f})"

        if console is not None:
            console.print(message)
        else:
            print(message, file=sys.stderr)


    def _classify_row(self, row: dict[str, Any]) -> LLMClassificationResult:
        pdf_path = self._resolve_pdf_path(row)

        images = self._render_first_pages(pdf_path, max_pages=2)
        raw_response_text = self._call_llm(pdf_path, images)
        parsed = self._parse_llm_json(raw_response_text)

        document_type = str(parsed.get("asset_type", parsed.get("document_type", ""))).strip().lower()
        document_type = document_type.replace("-", "_").replace(" ", "_")
        if document_type in {"text_based", "textdocument", "text_document"}:
            document_type = "text"
        elif document_type in {"image_based", "imagedocument", "image_document"}:
            document_type = "image"

        if document_type not in DEFAULT_DOCUMENT_TYPES:
            document_type = "error"

        confidence = parsed.get("confidence", 0.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0
        confidence_value = max(0.0, min(1.0, confidence_value))

        reason = str(parsed.get("reasoning", parsed.get("reason", ""))).strip()
        if not reason:
            reason = "LLM returned no reason."

        classification = LLMClassificationResult(
            document_type=document_type,
            confidence=confidence_value,
            reason=reason,
            raw_json=json.dumps(parsed, ensure_ascii=False),
        )
        LOGGER.info("Classification produced for %s: type=%s confidence=%.4f", pdf_path, document_type, confidence_value)
        return classification

    def _resolve_pdf_path(self, row: dict[str, Any]) -> Path:
        LOGGER.info("Resolving PDF path for %s", row.get("filename") or row.get("relative_path") or "<unknown>")
        relative_path = str(row.get("relative_path", "")).strip()
        full_path = str(row.get("full_path", "")).strip()
        candidates: list[Path] = []

        for raw_path in (full_path, relative_path):
            if not raw_path:
                continue
            path = Path(raw_path)
            if path.is_absolute():
                candidates.append(path)
            else:
                candidates.extend((self.csv_path.parent / path, self.csv_path.parent.parent / path))

        for candidate in candidates:
            if candidate.exists():
                LOGGER.info("Resolved PDF path: %s", candidate)
                return candidate

        requested_path = relative_path or full_path or "<empty>"
        LOGGER.error("Could not resolve PDF path: %s", requested_path)
        raise FileNotFoundError(
            f"Could not locate PDF for path='{requested_path}'. "
            f"Searched: {', '.join(str(candidate) for candidate in candidates) or '<none>'}"
        )

    def _render_first_pages(self, pdf_path: Path, max_pages: int = 2) -> list[bytes]:
        LOGGER.info("Rendering up to %d page(s) from %s", max_pages, pdf_path)
        doc = fitz.open(pdf_path)
        images: list[bytes] = []
        try:
            page_count = min(max_pages, doc.page_count)
            if page_count == 0:
                raise ValueError(f"PDF has no pages: {pdf_path}")

            for index in range(page_count):
                page = doc.load_page(index)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
                images.append(pixmap.tobytes("jpeg"))
        finally:
            doc.close()
        LOGGER.info("Rendered %d page image(s) from %s", len(images), pdf_path)
        return images

    def _call_llm(self, pdf_path: Path, images: list[bytes]) -> str:
        prompt = (
            "You are a document layout analysis engine. Analyze the visual layout and text density of the provided image "
            "of a PDF's first page to determine its primary asset type. "
            "\n\n### Asset Definitions\n"
            "- 'text': The page primarily consists of readable text, structured documents, tables, data listings, or written correspondence (like letters and emails).\n"
            "- 'image': The page is primarily a technical drawing, engineering blueprint, schematic, map, chart, or photographic image with little to no paragraphs of readable text.\n"
            "\n\n### Classification Logic\n"
            "1. If the page contains mostly text, lists, or an email layout, classify as 'text'.\n"
            "2. If the page contains more than ~50% visual diagrams, technical drawings, or shapes, classify as 'image'.\n"
            "\n\n### Output Format\n"
            "Return ONLY a valid JSON object. You MUST generate the 'reasoning' field first to describe what visual components you see. "
            "Follow this exact JSON shape:\n"
            "{\n"
            '  "reasoning": "Brief description of the visual layout (e.g., Contains a table and 3 paragraphs of text / Mostly an engineering schematic with a title block).",\n'
            '  "asset_type": "text",\n'
            '  "confidence": 0.95\n'
            "}"
        )

        LOGGER.info("Calling LLM for %s with %d image(s)", pdf_path, len(images))
        content = [{"type": "input_text", "text": prompt}]
        for image_bytes in images:
            image_b64 = self._bytes_to_base64(image_bytes)
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{image_b64}",
                }
            )
        LOGGER.info("Sending response request")
        response = self.client.responses.create(
            model=self.deployment_name,
            input=[{"role": "user", "content": content}],
        )
        LOGGER.info("LLM raw response: %s", getattr(response, "output_text", "<no output_text>"))
        text = self._extract_response_text(response)
        if not text:
            LOGGER.error("LLM returned an empty response for %s", pdf_path)
            raise ValueError(f"LLM returned an empty response for {pdf_path}")
        LOGGER.info("LLM response received for %s", pdf_path)
        return text

    def _extract_response_text(self, response: Any) -> str:
        text = getattr(response, "output_text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()

        output = getattr(response, "output", None)
        if not output:
            return ""

        pieces: list[str] = []
        for item in output:
            content = getattr(item, "content", None)
            if not content:
                continue
            for part in content:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text.strip():
                    pieces.append(part_text.strip())
        return "\n".join(pieces).strip()

    def _parse_llm_json(self, raw_text: str) -> dict[str, Any]:
        LOGGER.info("Parsing LLM JSON response")
        cleaned = self._strip_code_fences(raw_text.strip())
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise ValueError(f"LLM response was not valid JSON: {raw_text}")
            parsed = json.loads(cleaned[start : end + 1])

        if not isinstance(parsed, dict):
            LOGGER.error("LLM response JSON was not an object")
            raise ValueError(f"LLM response JSON must be an object: {raw_text}")
        LOGGER.info("LLM JSON response parsed successfully")
        return parsed

    def _strip_code_fences(self, text: str) -> str:
        stripped = text.strip()
        if not stripped.startswith("```"):
            return stripped

        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    def _bytes_to_base64(self, data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")

    def _write_rows_safely(self, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
        self.output_csv.parent.mkdir(parents=True, exist_ok=True)

        temp_fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.output_csv.stem}.",
            suffix=".tmp",
            dir=str(self.output_csv.parent),
        )
        os.close(temp_fd)
        temp_path = Path(temp_name)

        LOGGER.info("Writing %d row(s) to temporary CSV: %s", len(rows), temp_path)
        try:
            with open(temp_path, mode="w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: row.get(key, "") for key in fieldnames})
            temp_path.replace(self.output_csv)
            LOGGER.info("CSV replaced successfully: %s", self.output_csv)
            print(f"Classified CSV written: {self.output_csv}")
        except Exception:
            LOGGER.exception("Failed to write classified CSV: %s", self.output_csv)
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise



def main(argv: list[str] | None = None) -> int:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="Classify latest PDFs in a scanner CSV with an LLM.")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=DEFAULT_CSV_PATH,
        help=f"Path to the inventory CSV (default: {DEFAULT_CSV_PATH})",
    )
    args = parser.parse_args(argv)

    classifier = LLMClassifier(args.csv_path)
    output_path = classifier.classify_latest_documents()
    print(f"Classified CSV written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())






