# IG Weekly Audit — GitHub Actions Automation

Otomatis fetch data IG 8 akun setiap Senin 09:00 WIB, generate PDF report colorful, post ke Discord channel.

Tidak perlu buka laptop, tidak perlu maintain server.

## Setup (15 menit, sekali saja)

### 1. Daftar Apify
- Buka https://console.apify.com/sign-up (free tier $5 trial credit, cukup untuk ~5-10 minggu)
- Setelah login: **Settings → Integrations → API token** — copy token-nya
- Pricing setelah trial: ~$0.40 per run untuk 8 akun = ~$1.60/bulan

### 2. Bikin repo GitHub (private)
```bash
gh repo create ig-weekly-audit --private
cd ig-weekly-audit
```
Atau bikin via UI: github.com → New repository → Private → "ig-weekly-audit"

### 3. Upload 4 file ini ke repo
- `audit.py` (main script)
- `requirements.txt`
- `.github/workflows/weekly.yml`
- `README.md` (file ini)

```bash
git add .
git commit -m "Initial setup"
git push
```

### 4. Set GitHub Secrets
Di repo: **Settings → Secrets and variables → Actions → New repository secret**

Tambahkan 2 secret:
- `APIFY_TOKEN` — token dari step 1
- `DISCORD_WEBHOOK` — webhook URL Discord channel target (yang sudah kamu pakai sebelumnya)

### 5. Test manual run
- Buka tab **Actions** di repo GitHub
- Pilih workflow "IG Weekly Audit" → klik **Run workflow** → Run
- Tunggu ~2-3 menit, cek Discord channel untuk hasilnya
- Kalau success: artifact PDF tersimpan di run page (retention 90 hari)

### 6. Done — Senin depan otomatis jalan
Cron sudah set ke `0 2 * * 1` (Senin 02:00 UTC = 09:00 WIB).

## Cara modifikasi

**Tambah/ubah akun yang di-track:**
Edit `ROSTER` dict di `audit.py`:
```python
ROSTER = {
    "alazharmemorialgarden": "corporate",
    "akun_baru": "competitor",
    ...
}
```
Status valid: `corporate` / `agent` / `competitor`. Push, otomatis pakai di run berikutnya.

**Ubah jadwal:**
Edit `.github/workflows/weekly.yml` line `cron:`. Format: `'menit jam tgl bulan hari'` (UTC).
- Setiap hari 09:00 WIB: `'0 2 * * *'`
- Jumat sore 17:00 WIB: `'0 10 * * 5'`

**Tambah analisis:**
Edit fungsi `compute_insights()` atau `build_summary()` di `audit.py`.

## Cost estimasi
| Item | Cost |
|---|---|
| GitHub Actions (private repo) | $0 (2000 min/bulan free, kita pakai ~3 min/run × 4 run/bulan = 12 min) |
| Apify Instagram Profile Scraper | ~$1.60/bulan (8 akun × 4 run × ~$0.05) |
| **Total** | **~$2/bulan** |

## Troubleshooting

**Apify return data kosong / akun tidak ditemukan**
- Cek username case-sensitive. Apify expect lowercase.
- Akun private tidak bisa di-scrape, akan return null.

**Discord 401**
- Webhook URL expired atau salah. Generate ulang di Discord channel settings.

**GitHub Action gagal "rate limited"**
- Apify free tier limit. Upgrade ke pay-as-you-go ($0.40/1000 results) atau pakai actor lain.

**Engagement (likes/views) angka aneh**
- IG mulai sembunyikan likes untuk beberapa akun. Apify return null untuk yang hidden — script handle dengan `or 0`.

## Hidden tradeoff
Automation ini cuma generate Tier 1+2 dari laporan (scoreboard + per-post table). Tier 3 (patterns) cuma basic format winner. Action items (Tier 5) **tidak di-generate otomatis** — itu butuh judgment yang LLM-grade. Kalau mau full Claude analysis weekly, integrasikan Anthropic API call setelah data fetch. Tambah ~$0.50/run kalau pakai Claude Haiku.
