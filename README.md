# 📝 Add Signature to PDF

Script Python untuk menyisipkan gambar tanda tangan ke dalam file PDF secara otomatis, berdasarkan posisi placeholder teks `{{SIGNATURE}}`.

---

## ✨ Fitur

- 🔍 **Deteksi placeholder otomatis** — mencari teks `{{SIGNATURE}}` di setiap halaman PDF
- 🖼️ **Background removal** — menghapus latar putih dari gambar tanda tangan menggunakan NumPy (cepat)
- 📐 **Auto-alignment** — mendeteksi apakah tanda tangan harus rata kiri atau rata tengah
- ⚡ **Pemrosesan paralel** — menggunakan `ThreadPoolExecutor` untuk memproses banyak PDF secara bersamaan
- 📏 **Aspect ratio preserved** — ukuran tanda tangan mengikuti jarak ke teks nama di bawahnya
- ⚖️ **Balanced margins** — margin atas dan bawah gambar dijamin 5pt yang sama
- 🪵 **Logging terstruktur** — output yang jelas dengan timestamp

---

## 📦 Requirements

- Python 3.8+
- [PyMuPDF](https://pymupdf.readthedocs.io/) (`fitz`)
- [Pillow](https://python-pillow.org/)
- [NumPy](https://numpy.org/)

### Instalasi dependensi

```bash
python -m venv .venv
source .venv/bin/activate

pip install pymupdf pillow numpy
```

---

## 🚀 Penggunaan

```bash
python main.py \
  --input  ./pdf_input \
  --output ./pdf_output \
  --name   "Nama Penandatangan" \
  --signature ./tanda_tangan.png
```

### Argumen

| Argumen       | Wajib | Default       | Keterangan                                                   |
|---------------|-------|---------------|--------------------------------------------------------------|
| `--input`     | ❌    | `./input`     | Folder yang berisi file PDF sumber                           |
| `--output`    | ❌    | `./output`    | Folder tujuan untuk menyimpan PDF hasil (dibuat otomatis)    |
| `--name`      | ✅    | —             | Teks nama yang muncul di bawah placeholder (sebagai acuan Y) |
| `--signature` | ✅    | —             | Path ke file gambar tanda tangan (PNG/JPG)                   |
| `--workers`   | ❌    | auto          | Jumlah thread paralel (`min(8, cpu_count)`)                  |

### Contoh

```bash
python main.py \
  --input  ./dokumen \
  --output ./dokumen_signed \
  --name   "Budi Santoso" \
  --signature ./ttd_budi.png \
  --workers 4
```

---

## 🗂️ Struktur Direktori

```
add_signature_to_pdf/
├── main.py           # Script utama
├── README.md         # Dokumentasi ini
├── test_main.py      # Smoke test untuk CLI dan helper utama
├── pdf_input/        # Contoh: folder PDF sumber (buat sendiri)
└── pdf_output/       # Contoh: folder output (dibuat otomatis)
```

---

## 🧪 Testing

Smoke test dijalankan dengan:

```bash
python -m unittest test_main.py
```

Test ini memverifikasi:
- `main._get_image_aspect_ratio()`
- `main.make_signature_transparent()`
- behavior CLI ketika folder input kosong

---

## ⚙️ Cara Kerja

```
PDF Input
   │
   ▼
[Buka setiap halaman]
   │
   ├── Cari teks {{SIGNATURE}}
   │       │
   │       ├── Cari ruang di atas dan bawah placeholder
   │       ├── Hitung tinggi agar margin 5pt + 5pt
   │       ├── Deteksi alignment (CENTER / LEFT)
   │       ├── Hapus placeholder (redaction putih)
   │       └── Sisipkan gambar tanda tangan transparan
   │
   └── Simpan ke folder output
```

### Penjelasan Placeholder

Tempatkan teks `{{SIGNATURE}}` di dokumen Word/PDF Anda persis di posisi tanda tangan yang diinginkan. Script akan:

1. Menemukan posisi `{{SIGNATURE}}`
2. Mendeteksi ruang kosong di atas dan di bawah placeholder
3. Menghitung tinggi gambar agar margin atas dan bawah = 5pt
4. Menghapus placeholder (redaction putih)
5. Menyisipkan gambar tanda tangan di lokasi yang dihitung

---

## 🔧 Konfigurasi (di dalam `main.py`)

| Konstanta           | Default | Keterangan                                              |
 |--------------------|---------|---------------------------------------------------------|
 | `PLACEHOLDER`      | `{{SIGNATURE}}` | Teks penanda posisi tanda tangan              |
 | `LEFT_MARGIN`      | `15` pt | Jarak dari kiri saat alignment LEFT                 |
 | `SPACE_HEIGHT_FACTOR`| `0.90`  | Persentase tinggi ruang yang dipakai (90%)          |
 | `SPACE_TOP_MARGIN` | `2` pt  | Margin di atas ruang gambar                        |
 | `SPACE_BOTTOM_MARGIN`| `2` pt  | Margin di bawah ruang gambar                       |
 | `TOP_PADDING`      | `2` pt  | Padding atas gambar jika tidak ada ruang          |
 | `ALIGN_TOLERANCE`  | `8` pt  | Toleransi piksel untuk deteksi alignment           |
 | `MIN_HEIGHT_FACTOR`| `1.0`   | Tinggi minimum gambar = tinggi placeholder × faktor |

---

## 📝 Catatan

- Gambar tanda tangan sebaiknya berlatar putih atau transparan.
- Script akan membuat versi transparan dari gambar tanda tangan secara otomatis di folder temp sistem, dan menghapusnya setelah selesai.
- Jika tidak ada teks nama di bawah placeholder, tinggi gambar akan di-fallback ke **60 pt**.
- Margin atas dan bawah gambar secara otomatis dihitung untuk **5pt yang sama** di kedua sisi.
- Script aman dijalankan berulang kali — folder output dibuat otomatis jika belum ada.

---

## 📄 Lisensi

MIT License — bebas digunakan dan dimodifikasi.
