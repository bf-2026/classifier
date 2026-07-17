"""Utilities for reducing the size of PDF files with PyMuPDF."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable

import fitz
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn


class PDFReducer:
    """Reduce PDF size by removing unused objects and compressing PDF streams.

    Embedded files can be removed as well. This is useful for PDFs created from
    emails where the original email attachments were embedded in the PDF.
    """

    def __init__(
        self,
        remove_embedded_files: bool = True,
        text_only: bool = True,
    ) -> None:
        self.remove_embedded_files = remove_embedded_files
        self.text_only = text_only

    @staticmethod
    def _has_reduced_suffix(path: str | Path) -> bool:
        """Return whether *path* already has the reducer's output suffix."""
        return Path(path).stem.lower().endswith("-reduced")

    @staticmethod
    def _is_true(value: object) -> bool:
        return str(value).strip().lower() in {"true", "1", "yes", "y"}

    @staticmethod
    def _save_options() -> dict[str, object]:
        return {
            "garbage": 4,
            "clean": True,
            "deflate": True,
            "deflate_images": True,
            "deflate_fonts": True,
            "use_objstms": True,
        }

    @staticmethod
    def _remove_embedded_files(document: fitz.Document) -> int:
        names = tuple(document.embfile_names())
        for name in names:
            document.embfile_del(name)
        return len(names)

    @staticmethod
    def _create_text_only_document(source: fitz.Document) -> fitz.Document:
        """Rebuild *source* with text only.

        Reconstructing pages instead of deleting objects in-place is deliberate:
        it drops annotation objects (including file-attachment annotations),
        associated files, embedded files, images, links, and custom metadata
        objects that garbage collection may leave behind.
        """
        text_document = fitz.open()
        for source_page in source:
            page = text_document.new_page(
                width=source_page.rect.width,
                height=source_page.rect.height,
            )
            page.set_rotation(source_page.rotation)
            page_dict = source_page.get_text("dict")
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:  # Skip image and other non-text blocks.
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        value = span.get("text", "")
                        if not value.strip():
                            continue
                        x0, y0, _x1, y1 = span["bbox"]
                        size = max(float(span.get("size", 8)), 1.0)
                        # A span's bbox is in page coordinates; insert_text uses
                        # the lower-left point as its baseline.
                        baseline = (x0, y1 - max(size * 0.15, 0.5))
                        color = span.get("color", 0)
                        red = ((color >> 16) & 255) / 255
                        green = ((color >> 8) & 255) / 255
                        blue = (color & 255) / 255
                        page.insert_text(
                            baseline,
                            value,
                            fontsize=size,
                            fontname="helv",
                            color=(red, green, blue),
                        )
        # Do not copy source metadata. A newly created document has no custom
        # vendor metadata, associated-file entries, or attachment namespace.
        return text_document

    @staticmethod
    def _recompress_images(document: fitz.Document) -> int:
        """Re-encode raster images when normal PDF cleanup was insufficient."""
        replaced: set[int] = set()
        for page in document:
            for image in page.get_images(full=True):
                xref = image[0]
                if xref in replaced:
                    continue
                replaced.add(xref)
                try:
                    pixmap = fitz.Pixmap(document, xref)
                    if pixmap.alpha or pixmap.width < 80 or pixmap.height < 80:
                        continue
                    encoded = pixmap.tobytes("jpeg", jpg_quality=75)
                    if len(encoded) < len(pixmap.tobytes("png")):
                        document.update_stream(xref, encoded)
                        document.xref_set_key(xref, "Filter", "/DCTDecode")
                        document.xref_set_key(xref, "ColorSpace", "/DeviceRGB")
                        document.xref_set_key(xref, "BitsPerComponent", "8")
                        document.xref_set_key(xref, "SMask", "null")
                except (RuntimeError, ValueError):
                    # Unsupported image formats should not prevent other PDFs
                    # from being reduced.
                    continue
        return len(replaced)

    def reduce(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        *,
        remove_embedded_files: bool | None = None,
    ) -> Path:
        """Create a smaller copy of *input_path* and return its path.

        By default, pages are rebuilt as text-only pages. This removes file
        attachment annotations, associated files, embedded files, images, links,
        and custom vendor metadata objects. Set ``text_only=False`` to retain
        the original page objects and use normal PDF cleanup instead.
        """
        source = Path(input_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"PDF not found: {source}")
        if source.suffix.lower() != ".pdf":
            raise ValueError(f"Expected a PDF file: {source}")
        if self._has_reduced_suffix(source):
            return source

        destination = (
            Path(output_path).expanduser()
            if output_path is not None
            else source.with_name(f"{source.stem}-reduced{source.suffix}")
        ).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        remove_files = self.remove_embedded_files if remove_embedded_files is None else remove_embedded_files

        with NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.stem}-", suffix=".pdf", delete=False) as temporary:
            temporary_path = Path(temporary.name)

        try:
            with fitz.open(source) as document:
                if self.text_only:
                    text_document = self._create_text_only_document(document)
                    try:
                        text_document.save(temporary_path, **self._save_options())
                    finally:
                        text_document.close()
                else:
                    if remove_files:
                        self._remove_embedded_files(document)
                    document.save(temporary_path, **self._save_options())

            # Deflation cannot shrink JPEG streams. Reopen the original and
            # explicitly recompress images only when the first pass failed.
            if not self.text_only and temporary_path.stat().st_size >= source.stat().st_size:
                temporary_path.unlink()
                with fitz.open(source) as document:
                    if remove_files:
                        self._remove_embedded_files(document)
                    self._recompress_images(document)
                    document.save(temporary_path, **self._save_options())

            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)

        return destination

    def reduce_directory(
        self,
        input_directory: str | Path,
        output_directory: str | Path | None = None,
    ) -> Iterable[Path]:
        """Reduce every PDF below *input_directory* recursively."""
        source_directory = Path(input_directory).expanduser().resolve()
        if not source_directory.is_dir():
            raise NotADirectoryError(f"Directory not found: {source_directory}")

        target_directory = (
            Path(output_directory).expanduser().resolve()
            if output_directory is not None
            else None
        )

        sources = sorted(source_directory.rglob("*.pdf"))
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Reducing PDFs", total=len(sources))
            for source in sources:
                if self._has_reduced_suffix(source):
                    progress.advance(task)
                    yield source
                    continue
                if target_directory is None:
                    destination = None
                else:
                    destination = target_directory / source.relative_to(source_directory)
                    destination = destination.with_name(
                        f"{destination.stem}-reduced{destination.suffix}"
                    )
                result = self.reduce(source, destination)
                progress.advance(task)
                yield result

    def reduce_inventory(
        self,
        csv_path: str | Path = "output/pdf_inventory_email.csv",
        output_directory: str | Path | None = None,
        *,
        replace_original: bool = True,
        update_inventory: bool = True,
    ) -> list[Path]:
        """Reduce every PDF listed in an inventory CSV.

        The PDF path is read from ``full_path``. If that field is unavailable,
        ``relative_path`` is resolved relative to the CSV directory. When
        ``replace_original=True`` and no ``output_directory`` is provided, the
        original is replaced with the reduced PDF. Set ``replace_original=False``
        to write ``-reduced.pdf`` beside each original instead. Pass
        ``output_directory`` to keep the reduced files in a separate directory.
        """
        inventory_path = Path(csv_path).expanduser().resolve()
        if not inventory_path.is_file():
            raise FileNotFoundError(f"Inventory CSV not found: {inventory_path}")

        with inventory_path.open(newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)

        if "full_path" not in fieldnames and "relative_path" not in fieldnames:
            raise ValueError("Inventory must contain full_path or relative_path")
        if "reduced" not in fieldnames:
            fieldnames.append("reduced")
            for row in rows:
                row["reduced"] = "False"

        reduced_paths: list[Path] = []
        total_original_size = 0
        total_reduced_size = 0
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Reducing inventory PDFs", total=len(rows))
            for row in rows:
                raw_source = row.get("full_path") or row.get("relative_path")
                if not raw_source:
                    progress.advance(task)
                    continue

                source = Path(raw_source).expanduser()
                if not source.is_absolute():
                    source = inventory_path.parent / source

                already_reduced = self._is_true(row.get("reduced", "")) or self._has_reduced_suffix(source)
                if already_reduced:
                    row["reduced"] = "True"
                    reduced_paths.append(source)
                    progress.advance(task)
                    continue

                if output_directory is not None:
                    destination_root = Path(output_directory).expanduser().resolve()
                    relative = Path(row.get("relative_path") or source.name)
                    destination = destination_root / relative
                    destination = destination.with_name(
                        f"{destination.stem}-reduced{destination.suffix}"
                    )
                elif replace_original:
                    # Reduce to a sibling temporary name first. This makes the
                    # size comparison safe and avoids corrupting the original.
                    destination = source.with_name(f".{source.name}.reduced-temp")
                else:
                    destination = None

                original_size = source.stat().st_size
                reduced = self.reduce(source, destination)
                reduced_size = reduced.stat().st_size
                total_original_size += original_size
                total_reduced_size += reduced_size

                if replace_original and output_directory is None:
                    # Replace the original whenever requested, even when the
                    # reduced PDF happens to be larger. The temporary output
                    # keeps the source safe until reduction has completed.
                    # ``os.replace`` is atomic and replaces an existing file.
                    try:
                        os.replace(reduced, source)
                    except PermissionError:
                        # Windows can reject replacement while another process
                        # holds the source. Fall back to an explicit removal.
                        source.unlink()
                        reduced.replace(source)
                    reduced = source

                reduced_paths.append(reduced)

                if update_inventory:
                    row["reduced"] = "True"
                    row["full_path"] = str(reduced)
                    if "filename" in fieldnames:
                        row["filename"] = reduced.name
                    if "file_size" in fieldnames:
                        row["file_size"] = str(reduced.stat().st_size)

                progress.advance(task)

        if update_inventory:
            with inventory_path.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

        saved_space = total_original_size - total_reduced_size
        Console().print(
            f"[bold green]Total saved space: "
            f"{saved_space / (1024 ** 2):,.2f} MiB "
            f"({saved_space / (1024 ** 3):,.2f} GiB)[/bold green]"
        )

        return reduced_paths


# Backwards-compatible lowercase name for callers that requested `pdfreducer`.
pdfreducer = PDFReducer


__all__ = ["PDFReducer", "pdfreducer"]

if __name__ == "__main__":
    PDFReducer().reduce_inventory(
        "output/pdf_inventory_emails.csv",
        replace_original=True,
    )