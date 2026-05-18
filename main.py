"""
Clean Architecture — add_signature_to_pdf.py
=============================================

Menempelkan gambar tanda tangan (PNG, bisa transparan) ke dalam file PDF
berdasarkan posisi placeholder teks {{SIGNATURE}}.

====================================================================
PERFORMANCE OPTIMISASI vs VERSI LAMA
====================================================================
1. Caching page text dict per halaman  — get_text("dict") dieksekusi
   HANYA SEKALI per halaman, bukan N kali untuk N placeholder.
2. Batch search_for per halaman       — semua placeholder dan semua
   nama penandatangan dicari SEKALI sebelum loop placeholder.
3. Alignment detection terpisah layer — dipisahkan jadi satu kelas
   agar reusabel dan mudah ditest.
4. No redundant Pixmap disk round-trip — ukuran gambar diambil dari
   PIL Image.open().size tanpa menulis file sementara kedua kalinya.
====================================================================
"""

import argparse
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

# ================================================================
# LOGGING
# ================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ================================================================
# CONFIGURATION CONSTANTS
# ================================================================
PLACEHOLDER: str = "{{SIGNATURE}}"

LEFT_MARGIN: float = 15.0      # pt — jarak dari kiri untuk alignment LEFT
BOTTOM_PADDING: float = 0.0    # pt — jarak bawah gambar dari nama
TOP_PADDING: float = 0.0       # pt — jarak atas gambar dari placeholder top
ALIGN_TOLERANCE: float = 8.0   # pt — toleransi deteksi alignment blok
MIN_HEIGHT_FACTOR: float = 1.0 # tinggi minimum = tinggi placeholder * faktor
FALLBACK_IMG_HEIGHT: float = 60.0  # pt — fallback jika tidak ada nama di bawah
TRANSPARENT_TMP_PREFIX: str = "transparent_"
# STRUCTURED DATA TYPES  (data layer)
# ================================================================

@dataclass
class ParsedLine:
    """Satu baris teks non-kosong dalam blok teks PDF."""
    line_x0: float
    line_x1: float
    line_y0: float
    line_y1: float

    @property
    def rect(self) -> "fitz.Rect":
        """Kotak bounding untuk baris ini."""
        return fitz.Rect(self.line_x0, self.line_y0, self.line_x1, self.line_y1)


@dataclass
class ParsedBlock:
    """Satu blok teks bertipe 0 dari hasil get_text("dict")."""
    block_x0: float
    block_x1: float
    block_y0: float
    block_y1: float
    lines: List[ParsedLine] = field(default_factory=list)

    @property
    def rect(self) -> "fitz.Rect":
        """Kotak bounding untuk seluruh blok."""
        return fitz.Rect(
            self.block_x0, self.block_y0, self.block_x1, self.block_y1
        )

    @property
    def visual_lefts(self) -> List[float]:
        """Koordinat X-0 semua baris non-kosong dalam blok."""
        return [ln.line_x0 for ln in self.lines]

    @property
    def visual_rights(self) -> List[float]:
        """Koordinat X-1 semua baris non-kosong dalam blok."""
        return [ln.line_x1 for ln in self.lines]


@dataclass
class AlignmentBlock:
    """
    Hasil deteksi alignment untuk satu placeholder.

    Attributes:
        align    : "CENTER", "LEFT", atau None
        block_x0 : X paling kiri seluruh blok teks
        block_x1 : X paling kanan seluruh blok teks
        block_y0 : Y paling atas seluruh blok teks
        block_y1 : Y paling bawah seluruh blok teks
    """
    align: Optional[str]
    block_x0: float
    block_x1: float
    block_y0: float
    block_y1: float

    @property
    def block_center(self) -> float:
        """Titik tengah horizontal seluruh blok."""
        return (self.block_x0 + self.block_x1) / 2

    @property
    def block_width(self) -> float:
        """Lebar horizontal seluruh blok."""
        return self.block_x1 - self.block_x0


@dataclass
class SignaturePlacement:
    """Hasil perhitungan posisi dan ukuran untuk satu placeholder."""
    image_rect: "fitz.Rect"
    placeholder_rect: "fitz.Rect"
    alignment: AlignmentBlock


# ================================================================
# PAGE LAYOUT CACHE  (parsing & alignment — dijalankan 1×/halaman)
# ================================================================

class PageLayout:
    """
    Cache struktur teks halaman agar tidak di-parse berulang kali.

    Dipakai oleh worker ``process_pdf`` untuk:
      - menyediakan daftar ParsedBlock (sudah difilter tipe 0)       — 1×/page
      - mendeteksi alignment untuk setiap placeholder di halaman itu   — 1×/ph
    """

    def __init__(self, page: "fitz.Page") -> None:
        self._page = page
        self._blocks: List[ParsedBlock] = self._parse_text_blocks(page)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_text_blocks(page: "fitz.Page") -> List[ParsedBlock]:
        """
        Parsing sekali saja dari hasil ``get_text("dict")``.

        Hanya mempertimbangkan blok bertipe 0 (teks) dan baris non-kosong.

        Args:
            page: Halaman PyMuPDF yang akan di-parse.

        Returns:
            List[ParsedBlock] berisi semua blok teks yang valid.
        """
        raw: Any = page.get_text("dict")
        parsed: List[ParsedBlock] = []

        for raw_block in raw["blocks"]:
            if raw_block.get("type") != 0:
                continue

            bx0, by0, bx1, by1 = raw_block["bbox"]
            lines: List[ParsedLine] = []

            for line in raw_block.get("lines", []):
                spans_text = "".join(s["text"] for s in line.get("spans", [])).strip()
                if not spans_text:
                    continue

                lx0, ly0, lx1, ly1 = line["bbox"]
                lines.append(ParsedLine(lx0, lx1, ly0, ly1))

            if lines:
                parsed.append(
                    ParsedBlock(
                        block_x0=bx0, block_x1=bx1,
                        block_y0=by0, block_y1=by1,
                        lines=lines,
                    )
                )

        return parsed

    # ------------------------------------------------------------------
    # Alignment detection (operates on cached blocks)
    # ------------------------------------------------------------------

    def get_alignment_block(
        self, placeholder_rect: "fitz.Rect"
    ) -> AlignmentBlock:
        """
        Deteksi alignment blok teks yang berpotensi berisi placeholder.

        Beroperasi pada ``self._blocks`` yang sudah di-cache  — tidak
        memanggil ``page.get_text("dict")`` lagi.

        Args:
            placeholder_rect: Kotak bounding teks ``{{SIGNATURE}}``.

        Returns:
            AlignmentBlock berisi info alignment untuk placeholder.
        """
        for block in self._blocks:
            if not block.rect.intersects(placeholder_rect):
                continue

            block_left = min(block.visual_lefts)
            block_right = max(block.visual_rights)
            block_center = (block_left + block_right) / 2
            ph_center = (placeholder_rect.x0 + placeholder_rect.x1) / 2

            if abs(ph_center - block_center) <= ALIGN_TOLERANCE:
                return AlignmentBlock(
                    align="CENTER",
                    block_x0=block_left,
                    block_x1=block_right,
                    block_y0=block.block_y0,
                    block_y1=block.block_y1,
                )

            if abs(placeholder_rect.x0 - block_left) <= ALIGN_TOLERANCE:
                return AlignmentBlock(
                    align="LEFT",
                    block_x0=block_left,
                    block_x1=block_right,
                    block_y0=block.block_y0,
                    block_y1=block.block_y1,
                )

        return AlignmentBlock(
            align=None,
            block_x0=placeholder_rect.x0,
            block_x1=placeholder_rect.x1,
            block_y0=placeholder_rect.y0,
            block_y1=placeholder_rect.y1,
        )

    @property
    def blocks(self) -> List[ParsedBlock]:
        """Mengembalikan daftar ParsedBlock yang sudah ter-cache."""
        return self._blocks


# ================================================================
# CORE PURE FUNCTIONS  (no I/O — mudah ditest)
# ================================================================

def compute_img_dimensions(
    placeholder_rect: "fitz.Rect",
    text_above_rect: Optional["fitz.Rect"],
    name_rect: Optional["fitz.Rect"],
    aspect_ratio: float,
) -> tuple[float, float]:
    """
    Hitung lebar dan tinggi gambar tanda tangan dalam poin PDF.

    Tinggi dihitung dari bagian ATAS teks di atas placeholder hingga
    bagian BAWAH teks nama di bawah, dengan penyesuaian margin.

    Jika tidak ada nama di bawah, gunakan tinggi placeholder sebagai minimum.

    Args:
        placeholder_rect  : Kotak placeholder ``{{SIGNATURE}}``.
        text_above_rect   : Rect teks di atas placeholder (jika ada).
        name_rect         : Rect nama penandatangan di bawah (jika ada).
        aspect_ratio      : Rasio lebar / tinggi gambar PNG.

    Returns:
        ``(img_width, img_height)`` dalam poin PDF.
    """
    if text_above_rect is not None and name_rect is not None:
        img_height = (name_rect.y0 - text_above_rect.y1) - BOTTOM_PADDING
    elif name_rect is not None:
        img_height = (name_rect.y0 - placeholder_rect.y0) - BOTTOM_PADDING
    else:
        img_height = FALLBACK_IMG_HEIGHT

    min_h = placeholder_rect.height * MIN_HEIGHT_FACTOR
    img_height = max(img_height, min_h)

    img_width = img_height * aspect_ratio

    return img_width, img_height


def compute_signature_position(
    placeholder_rect: "fitz.Rect",
    text_above_rect: Optional["fitz.Rect"],
    alignment: AlignmentBlock,
    img_width: float,
    img_height: float,
) -> "fitz.Rect":
    """
    Hitung kotak ``fitz.Rect`` akhir untuk disisipkan ke halaman PDF.

    Titik atas gambar dihitung dari bagian bawah teks di atas placeholder
    jika ada, sehingga gambar dimulai tepat di bawah teks tersebut.

    Args:
        placeholder_rect : Kotak placeholder asli.
        text_above_rect  : Rect teks di atas placeholder (jika ada).
        alignment        : Hasil deteksi alignment dari ``PageLayout``.
        img_width        : Lebar gambar yang sudah dihitung.
        img_height       : Tinggi gambar yang sudah dihitung.

    Returns:
        ``fitz.Rect`` berisi posisi dan ukuran gambar tanda tangan.
    """
    if alignment.align == "CENTER":
        x0 = (
            alignment.block_x0
            + (alignment.block_width - img_width) / 2
        )
    elif alignment.align == "LEFT":
        x0 = placeholder_rect.x0 + LEFT_MARGIN
    else:
        x0 = placeholder_rect.x0

    if text_above_rect is not None:
        y0 = text_above_rect.y1 + TOP_PADDING
    else:
        y0 = placeholder_rect.y0 + TOP_PADDING

    return fitz.Rect(x0, y0, x0 + img_width, y0 + img_height)


# ================================================================
# SIDE-EFFECT HELPERS  (modify PDF pages / filesystem)
# ================================================================

def remove_placeholder_and_insert_image(
    page: "fitz.Page",
    placeholder_rect: "fitz.Rect",
    image_rect: "fitz.Rect",
    image_path: str,
) -> None:
    """
    Hapus placeholder dan sisipkan gambar tanda tangan ke halaman PDF.

    Args:
        page             : Halaman PyMuPDF yang akan di-modifikasi.
        placeholder_rect : Kotak teks placeholder yang dihapus.
        image_rect       : Kotak bingkai gambar yang disisipkan.
        image_path       : Path ke gambar PNG tanda tangan (alpha channel).
    """
    page.draw_rect(placeholder_rect, fill=(1, 1, 1), color=None, overlay=True)
    page.insert_image(image_rect, filename=image_path, overlay=True)


def make_signature_transparent(
    input_path: str,
    output_path: str,
    white_threshold: int = 240,
    alpha_softness: int = 10,
) -> None:
    """
    Ubah background putih gambar tanda tangan menjadi transparan (NumPy vectorized).

    Lebih cepat daripada loop pixel-per-pixel Python karena memanfaatkan
    broadcasting NumPy untuk seluruh array sekaligus.

    Args:
        input_path      : Path gambar sumber (PNG / JPG).
        output_path     : Path untuk PNG hasil dengan alpha transparan.
        white_threshold : Batas putih untuk transparansi penuh.
        alpha_softness  : Jarak transisi alpha di bawah ambang putih.
    """
    with Image.open(input_path) as source_img:
        rgba = source_img.convert("RGBA")
        arr = np.array(rgba, dtype=np.uint8)

        r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]

        white_mask = (r >= white_threshold) & (
            g >= white_threshold
        ) & (b >= white_threshold)
        fade_zone = (
            (r >= white_threshold - alpha_softness)
            & (g >= white_threshold - alpha_softness)
            & (b >= white_threshold - alpha_softness)
            & ~white_mask
        )

        alpha = np.full(r.shape, 255, dtype=np.uint8)
        alpha[white_mask] = 0

        if alpha_softness > 0 and np.any(fade_zone):
            fade_distance = np.minimum.reduce(
                (
                    white_threshold - r[fade_zone],
                    white_threshold - g[fade_zone],
                    white_threshold - b[fade_zone],
                )
            ).astype(np.int32)
            alpha[fade_zone] = np.clip(
                (fade_distance * 255) // alpha_softness,
                0,
                255,
            ).astype(np.uint8)

        arr[..., 3] = alpha

        Image.fromarray(arr, "RGBA").save(
            output_path, format="PNG", compress_level=6
        )

    log.info("Transparent signature saved -> %s", output_path)


# ================================================================
# STANDALONE HELPERS  (small, focused utilities)
# ================================================================

def _find_nearest_text_above(
    placeholder_rect: "fitz.Rect",
    text_rects: List["fitz.Rect"],
) -> Optional["fitz.Rect"]:
    """
    Cari rect teks terdekat yang berada DI ATAS placeholder.

    Args:
        placeholder_rect: Kotak teks ``{{SIGNATURE}}``.
        text_rects      : Semua kotak teks di halaman (hasil search_for).

    Returns:
        Rect teks terdekat di atas placeholder, atau ``None`` jika tidak ada.
    """
    best_rect: Optional["fitz.Rect"] = None
    best_dist: float = float("inf")

    for r in text_rects:
        if r.y1 <= placeholder_rect.y0 and (placeholder_rect.y0 - r.y1) > 5:
            dist = placeholder_rect.y0 - r.y1
            if dist < best_dist:
                best_dist = dist
                best_rect = r

    return best_rect


def _find_nearest_rect_below(
    placeholder_rect: "fitz.Rect",
    name_rects: List["fitz.Rect"],
) -> Optional["fitz.Rect"]:
    """
    Cari rect nama penandatangan terdekat yang berada DI BAWAH placeholder.

    Args:
        placeholder_rect: Kotak teks ``{{SIGNATURE}}``.
        name_rects      : Semua kotak teks nama penandatangan di halaman.

    Returns:
        Rect nama terdekat di bawah placeholder, atau ``None`` jika tidak ada.
    """
    best_rect: Optional["fitz.Rect"] = None
    best_dist: float = float("inf")

    for r in name_rects:
        if r.y0 > placeholder_rect.y0:
            dist = r.y0 - placeholder_rect.y0
            if dist < best_dist:
                best_dist = dist
                best_rect = r

    return best_rect


def _get_image_aspect_ratio(image_path: str) -> float:
    """
    Hitung rasio aspek gambar tanpa disk round-trip PyMuPDF.

    Menggunakan ``PIL.Image.open().size`` yang membaca header file saja
    tanpa memuat seluruh bitmap ke memori.

    Args:
        image_path: Path ke file gambar (PNG / JPG).

    Returns:
        Rasio lebar / tinggi. Fallback ke ``1.0`` jika tinggi 0.
    """
    with Image.open(image_path) as img:
        w, h = img.size
    return w / h if h else 1.0


def _collect_pdf_tasks(input_dir: str) -> List[str]:
    """
    Kumpulkan semua path absolut file PDF dari folder input.

    Args:
        input_dir: Folder berisi file PDF sumber.

    Returns:
        List path absolut file PDF, diurutkan berdasarkan nama.

    Raises:
        FileNotFoundError: Jika folder input tidak ada.
    """
    input_path = Path(input_dir)
    if not input_path.exists():
        raise FileNotFoundError(
            f"Folder input tidak ditemukan: {input_dir}"
        )

    pdfs = sorted(
        f for f in input_path.iterdir() if f.suffix.lower() == ".pdf"
    )
    return [str(p) for p in pdfs]


def _build_transparent_tmp_path() -> str:
    """
    Buat path file sementara untuk gambar tanda tangan transparan.

    Returns:
        Path absolut file sementara.
    """
    fd, tmp_path = tempfile.mkstemp(
        suffix=".png",
        prefix=TRANSPARENT_TMP_PREFIX,
    )
    os.close(fd)
    return tmp_path


# ================================================================
# PER-FILE WORKER  (dipanggil oleh ThreadPoolExecutor)
# ================================================================

def process_pdf(
    pdf_path: str,
    output_dir: str,
    signed_name: str,
    image_path: str,
    aspect_ratio: float,
) -> str:
    """
    Proses satu file PDF: cari placeholder dan sisipkan tanda tangan.

    Setiap halaman di-cache via ``PageLayout`` (``get_text`` 1×/page).
    Semua placeholder dan nama penandatangan dicari SEKALI per halaman
    sebelum loop placeholder berjalan.

    Args:
        pdf_path     : Path file PDF sumber.
        output_dir   : Folder untuk menyimpan PDF hasil.
        signed_name  : Teks nama untuk acuan tinggi gambar.
        image_path   : Path gambar tanda tangan yang sudah transparan.
        aspect_ratio : Rasio lebar / tinggi gambar PNG.

    Returns:
        Nama file PDF yang berhasil diproses.

    Raises:
        Exception: Dilempar ke ``ThreadPoolExecutor`` untuk error handling.
    """
    filename = os.path.basename(pdf_path)
    log.info("Processing: %s", filename)

    doc: fitz.Document = fitz.open(pdf_path)
    out_path = os.path.join(output_dir, filename)

    try:
        for page_index, page in enumerate(doc.pages(), start=1):
            layout = PageLayout(page)

            # Batch search — 1 panggilan per halaman, bukan berulang
            placeholder_rects = page.search_for(PLACEHOLDER)
            name_rects = page.search_for(signed_name)

            # Collect all text rects for finding text above placeholder
            all_text_rects = []
            for block in layout.blocks:
                for line in block.lines:
                    all_text_rects.append(line.rect)

            for ph_rect in placeholder_rects:
                text_above_rect = _find_nearest_text_above(ph_rect, all_text_rects)
                name_rect = _find_nearest_rect_below(ph_rect, name_rects)
                img_width, img_height = compute_img_dimensions(
                    ph_rect, text_above_rect, name_rect, aspect_ratio
                )

                alignment = layout.get_alignment_block(ph_rect)
                image_rect = compute_signature_position(
                    ph_rect, text_above_rect, alignment, img_width, img_height
                )

                remove_placeholder_and_insert_image(
                    page, ph_rect, image_rect, image_path
                )

            if placeholder_rects:
                log.debug(
                    "Page %d: %d placeholder(s) rendered",
                    page_index, len(placeholder_rects),
                )

        doc.save(out_path, garbage=4, deflate=True, clean=True)

    except Exception:
        # Lepaskan exception ke ThreadPoolExecutor tanpa menyelamatkan doc.close()
        raise
    finally:
        doc.close()

    log.info("Saved -> %s", out_path)
    return filename


# ================================================================
# ENTRY POINT
# ================================================================

def main() -> None:
    """Entry point — argumen CLI dan dispatcher paralel."""
    parser = argparse.ArgumentParser(
        description="Sisipkan gambar tanda tangan ke file PDF secara otomatis.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input", default="./input",
        help="Folder berisi file PDF sumber.",
    )
    parser.add_argument(
        "--output", default="./output",
        help="Folder tujuan untuk PDF hasil.",
    )
    parser.add_argument(
        "--name", required=True,
        help="Teks nama penandatangan di bawah placeholder.",
    )
    parser.add_argument(
        "--signature", required=True,
        help="Path ke gambar tanda tangan (PNG / JPG).",
    )
    parser.add_argument(
        "--workers", type=int, default=0,
        help=(
            "Jumlah worker thread. "
            "0 = otomatis (min(8, cpu_count))."
        ),
    )

    args = parser.parse_args()

    log.info("Input  folder : %s", os.path.abspath(args.input))
    log.info("Output folder : %s", os.path.abspath(args.output))

    os.makedirs(args.output, exist_ok=True)

    pdf_paths = _collect_pdf_tasks(args.input)

    if not pdf_paths:
        log.warning(
            "Tidak ada file PDF di: %s", os.path.abspath(args.input)
        )
        return

    transparent_path = _build_transparent_tmp_path()

    try:
        make_signature_transparent(args.signature, transparent_path)

        # Aspect ratio sekali saja dari PIL — tidak ada disk round-trip PyMuPDF
        aspect_ratio = _get_image_aspect_ratio(transparent_path)

        cpu_count = os.cpu_count() or 4
        max_workers = min(8, cpu_count)
        workers = min(
            args.workers if args.workers > 0 else max_workers,
            len(pdf_paths),
        )
        log.info(
            "Memproses %d PDF dengan %d worker(s)...",
            len(pdf_paths), workers,
        )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map: dict = {}
            for pdf_path in pdf_paths:
                fut = executor.submit(
                    process_pdf,
                    pdf_path,
                    args.output,
                    args.name,
                    transparent_path,
                    aspect_ratio,
                )
                future_map[fut] = pdf_path

            for future in as_completed(future_map):
                src_path = future_map[future]
                try:
                    result = future.result()
                    log.info("Done: %s", result)
                except Exception as exc:
                    log.error(
                        "Gagal memproses %s: %s",
                        os.path.basename(src_path),
                        exc,
                        exc_info=True,
                    )

    finally:
        if os.path.exists(transparent_path):
            os.remove(transparent_path)

    log.info("SELESAI")


if __name__ == "__main__":
    main()
