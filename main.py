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
SPACE_HEIGHT_FACTOR: float = 0.90  # Use 90% of available space height
SPACE_TOP_MARGIN: float = 2.0   # pt — margin at top of space
SPACE_BOTTOM_MARGIN: float = 2.0  # pt — margin at bottom of space
TOP_PADDING: float = 2.0   # pt — jarak atas gambar dari placeholder top
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
    space_rect: Optional["fitz.Rect"],
    name_rect: Optional["fitz.Rect"],
    aspect_ratio: float,
    upper_boundary: Optional[float] = None,
    lower_boundary: Optional[float] = None,
) -> tuple[float, float]:
    """
    Hitung lebar dan tinggi gambar tanda tangan dalam poin PDF.

    Jika upper_boundary dan lower_boundary diberikan, tinggi gambar = total_space - 10
    (5pt margin atas + 5pt margin bawah).

    Args:
        placeholder_rect  : Kotak placeholder ``{{SIGNATURE}}``.
        space_rect        : Rect ruang kosong (di atas atau di bawah placeholder).
        name_rect         : Rect nama penandatangan di bawah (jika ada).
        aspect_ratio      : Rasio lebar / tinggi gambar PNG.
        upper_boundary    : Batas atas ruang (untuk space above).
        lower_boundary    : Batas bawah ruang (untuk space below).

    Returns:
        ``(img_width, img_height)`` dalam poin PDF.
    """
    img_height = FALLBACK_IMG_HEIGHT

    if space_rect is not None:
        space_height = space_rect.y1 - space_rect.y0
        # For space above: image replaces placeholder, use space height directly
        # For space below: image is below placeholder, use space + placeholder
        if space_rect.y0 < placeholder_rect.y0:  # Space above placeholder
            # Image will be placed in space, replacing placeholder
            usable_height = space_height * SPACE_HEIGHT_FACTOR - SPACE_TOP_MARGIN - SPACE_BOTTOM_MARGIN
            if usable_height > 0:
                img_height = usable_height
        else:  # Space below placeholder
            total_height = space_height + placeholder_rect.height
            usable_height = total_height * SPACE_HEIGHT_FACTOR - SPACE_TOP_MARGIN - SPACE_BOTTOM_MARGIN
            if usable_height > 0:
                img_height = usable_height

    # Set image height from fixed 5pt top/bottom margin
    if upper_boundary is not None and lower_boundary is not None:
        total_space = lower_boundary - upper_boundary
        if total_space > 10:  # Need at least 10pt for 5pt margins
            img_height = total_space - 10  # 5pt top + 5pt bottom = 10pt

    min_h = placeholder_rect.height * MIN_HEIGHT_FACTOR
    img_height = max(img_height, min_h)

    img_width = img_height * aspect_ratio

    return img_width, img_height


def compute_signature_position(
    placeholder_rect: "fitz.Rect",
    space_rect: Optional["fitz.Rect"],
    alignment: AlignmentBlock,
    img_width: float,
    img_height: float,
    name_rect: Optional["fitz.Rect"] = None,
    upper_boundary: Optional[float] = None,
    lower_boundary: Optional[float] = None,
) -> "fitz.Rect":
    """
    Hitung kotak ``fitz.Rect`` akhir untuk disisipkan ke halaman PDF.

    Gambar diposisikan di tengah ruang secara vertikal.

    Args:
        placeholder_rect : Kotak placeholder asli.
        space_rect       : Rect ruang kosong (jika ada).
        alignment        : Hasil deteksi alignment dari ``PageLayout``.
        img_width        : Lebar gambar yang sudah dihitung.
        img_height       : Tinggi gambar yang sudah dihitung.
        name_rect        : Rect nama di bawah.
        upper_boundary   : Batas atas ruang untuk penyesuaian margin.
        lower_boundary   : Batas bawah ruang untuk penyesuaian margin.

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

    if space_rect is not None:
        # Final space bounds (space + placeholder)
        if space_rect.y0 >= placeholder_rect.y1:
            # Space below placeholder: center between placeholder.y1 and name.y0
            upper_y = placeholder_rect.y1
            lower_y = name_rect.y0 if name_rect else space_rect.y1
        else:
            # Space above placeholder: image replaces placeholder
            # upper = space.y0 (text above bottom), lower = placeholder.y0 (image top position)
            upper_y = space_rect.y0
            lower_y = placeholder_rect.y0  # placeholder top
        
        # Use provided boundaries for balanced margin calculation if available
        if upper_boundary is not None and lower_boundary is not None:
            upper_y = upper_boundary
            lower_y = lower_boundary

        # Center image vertically in the final space
        center_y = (upper_y + lower_y) / 2
        y0 = center_y - img_height / 2
        
        # Ensure image doesn't overlap with name (for space below)
        if name_rect is not None and y0 + img_height > name_rect.y0:
            y0 = name_rect.y0 - img_height - SPACE_BOTTOM_MARGIN
    else:
        y0 = placeholder_rect.y1 + TOP_PADDING

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
    page.add_redact_annot(placeholder_rect, fill=(1, 1, 1))
    page.apply_redactions()
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

def _find_space_above_placeholder(
    placeholder_rect: "fitz.Rect",
    all_text_rects: List["fitz.Rect"],
) -> Optional["fitz.Rect"]:
    """
    Cari ruang kosong di atas placeholder untuk menempatkan gambar.

    Mencari area kosong di antara text terdekat di atas placeholder.
    Mengabaikan line rect yang sangat sempit (kemungkinan zero-width characters).

    Args:
        placeholder_rect: Kotak placeholder ``{{SIGNATURE}}``.
        all_text_rects: Semua kotak teks di halaman.

    Returns:
        Rect ruang kosong di atas placeholder, atau ``None`` jika tidak ada.
    """
    # Filter out tiny line rects that are likely zero-width characters
    MIN_LINE_WIDTH = 10.0  # pt
    text_above_ph = [
        r for r in all_text_rects
        if r.y1 <= placeholder_rect.y0 and r.width >= MIN_LINE_WIDTH
    ]
    if not text_above_ph:
        return None

    text_above_ph.sort(key=lambda r: r.y1, reverse=True)
    closest_text = text_above_ph[0]

    space_y0 = closest_text.y1
    space_y1 = placeholder_rect.y0
    space_height = space_y1 - space_y0

    if space_height > 0:
        x0 = placeholder_rect.x0
        x1 = placeholder_rect.x1
        return fitz.Rect(x0, space_y0, x1, space_y1)

    return None


def _find_space_below_placeholder(
    placeholder_rect: "fitz.Rect",
    all_text_rects: List["fitz.Rect"],
) -> Optional["fitz.Rect"]:
    """
    Cari ruang kosong di bawah placeholder untuk menempatkan gambar.

    Args:
        placeholder_rect: Kotak placeholder ``{{SIGNATURE}}``.
        all_text_rects: Semua kotak teks di halaman.

    Returns:
        Rect ruang kosong di bawah placeholder, atau ``None`` jika tidak ada.
    """
    # Filter out tiny line rects that are likely zero-width characters
    MIN_LINE_WIDTH = 10.0  # pt
    text_below_ph = [
        r for r in all_text_rects
        if r.y0 >= placeholder_rect.y1 and r.width >= MIN_LINE_WIDTH
    ]
    if not text_below_ph:
        return None

    text_below_ph.sort(key=lambda r: r.y0)
    closest_text = text_below_ph[0]

    space_y0 = placeholder_rect.y1
    space_y1 = closest_text.y0
    space_height = space_y1 - space_y0

    if space_height > 0:
        x0 = placeholder_rect.x0
        x1 = placeholder_rect.x1
        return fitz.Rect(x0, space_y0, x1, space_y1)

    return None


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
    MIN_LINE_WIDTH = 10.0  # pt
    best_rect: Optional["fitz.Rect"] = None
    best_dist: float = float("inf")

    for r in name_rects:
        if r.y0 > placeholder_rect.y0 and r.width >= MIN_LINE_WIDTH:
            dist = r.y0 - placeholder_rect.y0
            if dist < best_dist:
                best_dist = dist
                best_rect = r

    return best_rect


def _find_nearest_rect_above(
    placeholder_rect: "fitz.Rect",
    all_text_rects: List["fitz.Rect"],
) -> Optional["fitz.Rect"]:
    """
    Cari rect teks terdekat yang berada DI ATAS placeholder.

    Args:
        placeholder_rect: Kotak teks ``{{SIGNATURE}}``.
        all_text_rects      : Semua kotak teks di halaman.

    Returns:
        Rect teks terdekat di atas placeholder, atau ``None`` jika tidak ada.
    """
    MIN_LINE_WIDTH = 10.0  # pt
    best_rect: Optional["fitz.Rect"] = None
    best_dist: float = float("inf")

    for r in all_text_rects:
        if r.y1 < placeholder_rect.y0 and r.width >= MIN_LINE_WIDTH:
            dist = placeholder_rect.y0 - r.y1
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
                space_above = _find_space_above_placeholder(ph_rect, all_text_rects)
                space_below = _find_space_below_placeholder(ph_rect, all_text_rects)
                name_rect = _find_nearest_rect_below(ph_rect, name_rects)
                text_above_rect = _find_nearest_rect_above(ph_rect, all_text_rects)

                # Determine best scenario based on available space
                # Scenario 1: Image placed above placeholder (space_above exists)
                # Scenario 2: Image placed below placeholder (space_below exists)
                if space_above is not None and space_below is not None:
                    space_above_height = space_above.y1 - space_above.y0
                    space_below_height = space_below.y1 - space_below.y0
                    if space_below_height > space_above_height:
                        space_rect = space_below
                    else:
                        space_rect = space_above
                elif space_below is not None:
                    space_rect = space_below
                elif space_above is not None:
                    space_rect = space_above
                else:
                    space_rect = None

                # Calculate boundaries for balanced margins
                upper_boundary = None
                lower_boundary = None
                if space_rect is not None:
                    if space_rect.y0 >= ph_rect.y1:
                        # Space below: use text above for upper boundary, name for lower
                        upper_boundary = text_above_rect.y1 if text_above_rect else space_rect.y0
                        lower_boundary = name_rect.y0 if name_rect else space_rect.y1
                    else:
                        # Space above: use text above to name for unified visual balance
                        upper_boundary = text_above_rect.y1 if text_above_rect else space_rect.y0
                        lower_boundary = name_rect.y0 if name_rect else ph_rect.y0

                img_width, img_height = compute_img_dimensions(
                    ph_rect, space_rect, name_rect, aspect_ratio,
                    upper_boundary, lower_boundary
                )

                alignment = layout.get_alignment_block(ph_rect)
                image_rect = compute_signature_position(
                    ph_rect, space_rect, alignment, img_width, img_height, name_rect,
                    upper_boundary, lower_boundary
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
