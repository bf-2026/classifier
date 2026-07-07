import base64
import csv
import json
import os
import sys
import tempfile
import fitz

from dataclasses import dataclass

from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DEFAULT_DOCUMENT_TYPES = {"image_based", "text_based", "unclear"}


@dataclass
class LLMClassificationResult:
    document_type: str
    confidence: float
    reason: str
    raw_json: str


class LLMClassifier:
    """Classify latest PDFs from a scanner CSV using an LLM with page images."""

    def __init__(self, csv_path: str | Path, pdf_root: str | Path, output_csv: str | Path | None = None):
        self.csv_path = Path(csv_path)
        self.pdf_root = Path(pdf_root)
        self.output_csv = Path(output_csv) if output_csv else self.csv_path.with_name(
            f"{self.csv_path.stem}_classified{self.csv_path.suffix}"
        )

        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        self.api_key = os.getenv("AZURE_OPENAI_API_KEY")

        self._validate_configuration()
        self.client = OpenAI(base_url=self.endpoint, api_key=self.api_key)

    def classify_latest_documents(self, show_progress: bool = True) -> Path:
        """Read the CSV, classify latest PDFs, and write a new CSV output file."""
        rows = self._load_rows()

        fieldnames = self._ensure_output_columns(rows)
        total_rows = len(rows)

        self._progress_update(0, total_rows, "Starting", show_progress)
        for index, row in enumerate(rows, start=1):
            if self._is_latest(row):
                if self._has_existing_classification(row):
                    classification = self._classification_from_row(row)
                    self._log_file_result(row, classification, skipped=True)
                else:
                    classification = self._classify_row(row)
                    row["llm_document_type"] = classification.document_type
                    row["llm_confidence"] = f"{classification.confidence:.4f}"
                    row["llm_reason"] = classification.reason
                    row["llm_raw_json"] = classification.raw_json
                    self._log_file_result(row, classification, skipped=False)
            else:
                row.setdefault("llm_document_type", "")
                row.setdefault("llm_confidence", "")
                row.setdefault("llm_reason", "")
                row.setdefault("llm_raw_json", "")
                self._log_file_result(row, None, skipped=True, reason="not latest")

            self._progress_update(index, total_rows, "Processing", show_progress)

        self._progress_finish(total_rows, show_progress)
        self._write_rows_safely(rows, fieldnames)
        return self.output_csv



    def _progress_update(self, current: int, total: int, label: str, show_progress: bool) -> None:
        if not show_progress:
            return

        width = 30
        total = max(total, 1)
        filled = int(width * min(current, total) / total)
        bar = "#" * filled + "-" * (width - filled)

        percent = (min(current, total) / total) * 100
        message = f"\r{label}: |{bar}| {current}/{total} ({percent:5.1f}%)"
        sys.stderr.write(message)
        sys.stderr.flush()

    def _progress_finish(self, total: int, show_progress: bool) -> None:
        if not show_progress:
            return
        self._progress_update(total, total, "Done", show_progress)
        sys.stderr.write("\n")
        sys.stderr.flush()

    def _validate_configuration(self) -> None:
        missing = []
        if not self.endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if not self.deployment_name:
            missing.append("AZURE_OPENAI_DEPLOYMENT")

        if not self.api_key:
            missing.append("AZURE_OPENAI_API_KEY")
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    def _load_rows(self) -> list[dict[str, Any]]:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"CSV file not found: {self.csv_path}")

        with open(self.csv_path, newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            return list(reader)

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
    ) -> None:
        filename = row.get("filename") or row.get("relative_path") or "<unknown>"
        if classification is None:
            message = f"{filename}: skipped ({reason or 'skipped'})"
        elif skipped:
            message = f"{filename}: skipped (already {classification.document_type})"
        else:
            message = f"{filename}: {classification.document_type} ({classification.confidence:.4f})"
        print(message, file=sys.stderr)

    def _classify_row(self, row: dict[str, Any]) -> LLMClassificationResult:

        pdf_path = self._resolve_pdf_path(row.get("relative_path", ""))
        images = self._render_first_pages(pdf_path, max_pages=2)
        raw_response_text = self._call_llm(pdf_path, images)
        parsed = self._parse_llm_json(raw_response_text)

        document_type = str(parsed.get("document_type", "unclear")).strip().lower()
        if document_type not in DEFAULT_DOCUMENT_TYPES:
            document_type = "unclear"

        confidence = parsed.get("confidence", 0.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0
        confidence_value = max(0.0, min(1.0, confidence_value))

        reason = str(parsed.get("reason", "")).strip()
        if not reason:
            reason = "LLM returned no reason."

        return LLMClassificationResult(
            document_type=document_type,
            confidence=confidence_value,
            reason=reason,
            raw_json=json.dumps(parsed, ensure_ascii=False),
        )

    def _resolve_pdf_path(self, relative_path: str) -> Path:
        path = Path(relative_path)
        if path.is_absolute() and path.exists():
            return path

        candidates = [
            self.pdf_root / relative_path,
            self.pdf_root.parent / relative_path,
            self.csv_path.parent / relative_path,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate

        raise FileNotFoundError(
            f"Could not locate PDF for relative_path='{relative_path}'. Searched: {', '.join(str(c) for c in candidates)}"
        )

    def _render_first_pages(self, pdf_path: Path, max_pages: int = 2) -> list[bytes]:
        doc = fitz.open(pdf_path)
        images: list[bytes] = []
        try:
            page_count = min(max_pages, doc.page_count)
            if page_count == 0:
                raise ValueError(f"PDF has no pages: {pdf_path}")

            for index in range(page_count):
                page = doc.load_page(index)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                images.append(pixmap.tobytes("jpeg"))
        finally:
            doc.close()
        return images

    def _call_llm(self, pdf_path: Path, images: list[bytes]) -> str:
        prompt = (
            "You are classifying PDFs by visual appearance for a RAG application. "
            "Analyze the provided first pages of the PDF and return only valid JSON. "
            "Choose a document_type from: image_based, text_based, unclear. "
            "Use image_based when the pages mainly contain photos, diagrams, drawings, or other visuals. "
            "Use text_based when the pages mainly contain readable text. "
            "Use unclear when the evidence is insufficient. "
            "Return this exact JSON shape: "
            '{"document_type":"image_based","confidence":0.93,"reason":"The pages mainly contain photos, diagrams, and very little readable text."}'
        )

        content = [{"type": "input_text", "text": prompt}]
        for image_bytes in images:
            image_b64 = self._bytes_to_base64(image_bytes)
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{image_b64}",
                }
            )

        response = self.client.responses.create(
            model=self.deployment_name,
            input=[{"role": "user", "content": content}],
        )

        text = self._extract_response_text(response)
        if not text:
            raise ValueError(f"LLM returned an empty response for {pdf_path}")
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
            raise ValueError(f"LLM response JSON must be an object: {raw_text}")
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

        try:
            with open(temp_path, mode="w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: row.get(key, "") for key in fieldnames})
            temp_path.replace(self.output_csv)
        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Classify latest PDFs in a scanner CSV with an LLM.")
    parser.add_argument("csv_path", help="Path to the CSV produced by PDFScanner")
    parser.add_argument("pdf_root", help="Root folder used to resolve relative_path entries")
    parser.add_argument(
        "--output-csv",
        help="Optional path for the classified CSV output",
        default=None,
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the progress bar",
    )
    args = parser.parse_args()

    classifier = LLMClassifier(args.csv_path, args.pdf_root, args.output_csv)
    output_path = classifier.classify_latest_documents(show_progress=not args.no_progress)

    print(f"Classified CSV written to: {output_path}")






