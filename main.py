import fitz
import os
import argparse
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from PIL import Image

# ================= CONFIG =================
PLACEHOLDER = "{{SIGNATURE}}"

LEFT_MARGIN    = 15   # pt
BOTTOM_PADDING = 0    # pt
TOP_PADDING    = 0    # pt
ALIGN_TOLERANCE = 8   # pt
MIN_HEIGHT_FACTOR = 1.0
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ------------------------------------------------
# Buat image transparan (NumPy-vectorized)
# ------------------------------------------------
def make_signature_transparent(
    input_path: str,
    output_path: str,
    white_threshold: int = 240,
    alpha_softness: int = 10,
) -> None:
    """
    Mengubah background putih menjadi transparan menggunakan NumPy
    (jauh lebih cepat daripada loop pixel-per-pixel).

    Args:
        input_path:      Path gambar sumber.
        output_path:     Path output PNG transparan.
        white_threshold: Pixel >= threshold dianggap "putih" → alpha 0.
        alpha_softness:  Pengali transisi alpha untuk tepi yang halus.
    """
    img = Image.open(input_path).convert("RGBA")
    arr = np.array(img, dtype=np.int32)  # shape: (H, W, 4)

    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]

    # Mask pixel putih / hampir putih
    white_mask = (r >= white_threshold) & (g >= white_threshold) & (b >= white_threshold)

    # Hitung alpha berdasarkan rata-rata kecerahan
    avg = (r + g + b) // 3
    soft_alpha = np.clip((white_threshold - avg) * alpha_softness, 0, 255)

    # Terapkan: putih → transparan penuh, lainnya → alpha halus
    alpha = np.where(white_mask, 0, soft_alpha).astype(np.uint8)
    arr[..., 3] = alpha

    Image.fromarray(arr.astype(np.uint8), "RGBA").save(output_path, "PNG")
    log.info("Transparent signature saved → %s", output_path)


# ------------------------------------------------
# Deteksi alignment (X) berdasarkan blok teks
# ------------------------------------------------
def detect_alignment(page: fitz.Page, placeholder_rect: fitz.Rect):
    """
    Mendeteksi apakah placeholder berada di CENTER atau LEFT
    relatif terhadap blok teks di sekitarnya.

    Returns:
        (align, block_left, block_right)  –  align ∈ {"CENTER", "LEFT", None}
    """
    text = page.get_text("dict")

    for block in text["blocks"]:
        if block["type"] != 0:
            continue

        xs0, xs1 = [], []
        ph_rect = None

        for line in block["lines"]:
            txt = "".join(span["text"] for span in line["spans"]).strip()
            if not txt:
                continue

            r = fitz.Rect(line["bbox"])
            xs0.append(r.x0)
            xs1.append(r.x1)

            if r.intersects(placeholder_rect):
                ph_rect = r

        if not ph_rect or len(xs0) < 2:
            continue

        block_left  = min(xs0)
        block_right = max(xs1)

        block_center = (block_left + block_right) / 2
        ph_center    = (ph_rect.x0 + ph_rect.x1) / 2

        if abs(ph_center - block_center) <= ALIGN_TOLERANCE:
            return "CENTER", block_left, block_right
        elif abs(ph_rect.x0 - block_left) <= ALIGN_TOLERANCE:
            return "LEFT", block_left, block_right

    return None, None, None


# ------------------------------------------------
# Worker: proses SATU file PDF
# ------------------------------------------------
def process_pdf(
    pdf_path: str,
    output_dir: str,
    signed_name: str,
    image_path: str,   # ← path gambar transparan
    aspect_ratio: float,
) -> str:
    """
    Menyisipkan gambar tanda tangan ke setiap placeholder dalam satu PDF.

    Returns:
        Nama file PDF yang diproses.

    Raises:
        Exception: diteruskan ke caller (ThreadPoolExecutor).
    """
    filename = os.path.basename(pdf_path)
    log.info("Processing: %s", filename)

    doc = fitz.open(pdf_path)

    for page in doc:
        signed_name_list = page.search_for(signed_name)

        for rect in page.search_for(PLACEHOLDER):

            # ── cari teks pertama DI BAWAH placeholder ──
            candidates = sorted(
                (r for r in signed_name_list if r.y0 > rect.y0),
                key=lambda r: r.y0,
            )
            next_y = candidates[0].y0 if candidates else None

            if next_y:
                img_height = next_y - rect.y0 - BOTTOM_PADDING
            else:
                img_height = 60  # fallback

            # clamp minimum agar tidak terlalu kecil
            min_h = rect.height * MIN_HEIGHT_FACTOR
            if img_height < min_h:
                img_height = min_h

            # pertahankan aspect ratio
            img_width = img_height * aspect_ratio

            # ── posisi X berdasarkan alignment ──
            align, block_left, block_right = detect_alignment(page, rect)

            if align == "CENTER":
                x0 = block_left + (block_right - block_left - img_width) / 2
            elif align == "LEFT":
                x0 = rect.x0 + LEFT_MARGIN
            else:
                x0 = rect.x0

            y0 = rect.y0
            image_rect = fitz.Rect(
                x0,
                y0 + TOP_PADDING,
                x0 + img_width,
                y0 + img_height,
            )

            # hapus placeholder lalu sisipkan gambar
            page.draw_rect(rect, fill=(1, 1, 1), color=None, overlay=True)
            page.insert_image(image_rect, filename=image_path, overlay=True)

    out_path = os.path.join(output_dir, filename)
    doc.save(out_path)
    doc.close()

    log.info("Saved: %s", out_path)
    return filename


# ========================= MAIN =========================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Insert a signature image into PDFs based on placeholder position."
    )
    parser.add_argument("--input",  default="./input",
                        help="Folder berisi file PDF sumber (default: ./input)")
    parser.add_argument("--output", default="./output",
                        help="Folder output PDF bertanda tangan (default: ./output)")
    parser.add_argument("--name",      required=True, help="Teks nama di bawah placeholder")
    parser.add_argument("--signature", required=True, help="Path gambar tanda tangan")
    parser.add_argument("--workers",   type=int, default=0,
                        help="Jumlah worker thread (0 = auto)")
    args = parser.parse_args()

    log.info("Input  folder : %s", os.path.abspath(args.input))
    log.info("Output folder : %s", os.path.abspath(args.output))
    os.makedirs(args.output, exist_ok=True)

    # Buat versi transparan dari gambar tanda tangan (sekali saja)
    ext = os.path.splitext(args.signature)[1]
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        transparent_path = tmp.name

    try:
        make_signature_transparent(args.signature, transparent_path)

        # Hitung aspect ratio SEKALI (reused di semua worker)
        pix = fitz.Pixmap(transparent_path)
        aspect_ratio = pix.width / pix.height
        pix = None  # bebaskan memori

        # Kumpulkan semua PDF
        tasks = [
            (
                os.path.join(args.input, f),
                args.output,
                args.name,
                transparent_path,   # ← gunakan path transparan
                aspect_ratio,
            )
            for f in os.listdir(args.input)
            if f.lower().endswith(".pdf")
        ]

        if not tasks:
            log.warning("Tidak ada file PDF ditemukan di: %s", args.input)
            return

        workers = args.workers or min(8, os.cpu_count() or 4)
        log.info("Memproses %d PDF dengan %d worker(s)...", len(tasks), workers)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(process_pdf, *t): t[0]
                for t in tasks
            }
            for future in as_completed(futures):
                pdf_path = futures[future]
                try:
                    result = future.result()
                    log.info("✅ Done: %s", result)
                except Exception as exc:
                    log.error("❌ Gagal memproses %s: %s", os.path.basename(pdf_path), exc)

    finally:
        # Hapus file transparan sementara
        if os.path.exists(transparent_path):
            os.remove(transparent_path)

    log.info("SELESAI ✅")


if __name__ == "__main__":
    main()
