# IG Audit Automation — Weekly + Monthly via GitHub Actions

Otomatis fetch data IG 13 akun, generate PDF colorful, post ke Discord. Tidak perlu buka laptop, tidak perlu maintain server.

**Dua jadwal:**
- **Weekly (7d)** — setiap Senin 08:00 WIB
- **Monthly (30d)** — setiap tanggal 1 jam 08:00 WIB

Sama-sama post ke Discord channel yang sama, dengan label "📅 Weekly" vs "🗓️ Monthly".

## Setup (15 menit, sekali saja)

### 1. Daftar Apify
- https://console.apify.com/sign-up → free trial $5 credit
- Settings → Integrations → API tokens → copy "Personal API token"
- Cost: ~$0.05/profile × 13 akun = $0.65 per run
- Weekly (4×/bulan) + Monthly (1×/bulan) = 5 run/bulan = **~$3.25/bulan**

### 2. Bikin repo GitHub (private)
```bash
gh repo create ig-audit --private
cd ig-audit
```
Atau via UI github.com → New repository → Private.

### 3. Upload 5 file ini ke repo
```
ig-audit/
├── audit.py                          (main script, dual-mode)
├── requirements.txt                  (deps: requests + reportlab)
├── README.md                         (this file)
└── .github/workflows/
    ├── weekly.yml                    (cron Senin 09:00 WIB)
    └── monthly.yml                   (cron tgl 1 jam 09:00 WIB)
```

```bash
git add .
git commit -m "Initial setup: weekly + monthly automation"
git push
```

### 4. Set GitHub Secrets
Di repo: **Settings → Secrets and variables → Actions → New repository secret**

Tambahkan 2 secret (sama dipakai oleh kedua workflow):
- `APIFY_TOKEN` — token dari step 1
- `DISCORD_WEBHOOK` — webhook URL Discord channel target

### 5. Test manual
- Tab **Actions** di repo GitHub
- Pilih workflow "IG Weekly Audit (7d)" → klik **Run workflow** → Run
- Tunggu ~2-3 menit, cek Discord
- Ulangi untuk "IG Monthly Audit (30d)" untuk verify monthly juga jalan

### 6. Done — otomatis jalan
- Weekly: Senin pagi WIB
- Monthly: tanggal 1 setiap bulan

## Cara modifikasi

### Tambah/ubah akun
Edit `ROSTER` dict di `audit.py`:
```python
ROSTER = {
    "alazharmemorialgarden": "corporate",
    "akun_baru": "competitor",
    ...
}
```
Status valid: `corporate` / `agent` / `competitor`. Push, otomatis pakai di run berikutnya.

### Ubah jadwal
Edit field `cron:` di workflow yml. Format: `'menit jam tgl bulan hari'` (UTC).
- Setiap hari 09:00 WIB: `'0 2 * * *'`
- Jumat 17:00 WIB: `'0 10 * * 5'`
- Tgl 15 setiap bulan: `'0 2 15 * *'`

### Ubah window berapa hari
Edit `WINDOW_DAYS` di workflow yml env section. Misal untuk fortnightly (14d), bikin file workflow baru `fortnightly.yml` dengan `WINDOW_DAYS: '14'` dan cron tiap 2 minggu.

### Disable monthly (cuma mau weekly)
Hapus `.github/workflows/monthly.yml`, atau ubah cron jadi `'0 0 31 2 *'` (tidak pernah jalan — 31 Februari).

## Cost estimasi
| Item | Cost |
|---|---|
| GitHub Actions (private repo) | $0 (2000 min/bulan free; kita pakai ~5 min/run × 5 run = 25 min) |
| Apify Instagram Profile Scraper | ~$3.25/bulan (13 akun × $0.05 × 5 run) |
| **Total** | **~$3.25/bulan** |

Apify trial $5 cukup untuk ~1.5 bulan pertama.

## Output

### Discord channel akan terima 5 message/bulan:
- 4× weekly (Senin) → "📅 IG Weekly Audit"
- 1× monthly (tgl 1) → "🗓️ IG Monthly Audit"

Setiap message: summary text (top 3 akun, top post, format winner, dormant flag) + PDF attached.

### Filename pattern:
- Weekly: `ig-weekly-audit-YYYY-MM-DD.pdf`
- Monthly: `ig-monthly-audit-YYYY-MM-DD.pdf`

### Snapshot JSON (per run, archived sebagai GitHub Actions artifact):
- Weekly: retention 90 hari
- Monthly: retention 365 hari (untuk YoY comparison nanti)

## Troubleshooting

**Apify return data kosong**
- Cek username case-sensitive. Apify expect lowercase.
- Akun private tidak bisa di-scrape.

**Discord 401**
- Webhook URL expired. Generate ulang di Discord channel settings.

**GitHub Action gagal "rate limited" / "out of credit"**
- Apify free trial habis. Upgrade ke Starter plan ($49/mo, way over-spec) atau pay-as-you-go.
- Pay-as-you-go realistic untuk use case ini: top-up $10 dan jalan 3+ bulan.

**Monthly report angka aneh untuk akun aktif**
- Akun yang post lebih dari 100×/30d kemungkinan ada post yang tidak ke-grab. Increase `APIFY_RESULTS_LIMIT` di `audit.py` (line 33) dari 100 → 200.

**Engagement (likes/views) null**
- IG mulai sembunyikan likes untuk beberapa akun. Apify return null untuk yang hidden — script handle dengan `or 0`.

## Hidden tradeoff

Automation generate Tier 1+2 dari report framework (scoreboard + per-post table) + Tier 3 basic (top 3 + format winner).

**Yang TIDAK di-generate otomatis:**
- Tier 3 advanced — hook pattern analysis, competitor moves narrative
- Tier 4 — strategic context (SoV, sentiment, conversion proxy)
- Tier 5 — action items spesifik per situasi

Untuk full LLM-grade analysis weekly/monthly, integrasikan Anthropic API call setelah data fetch (tambah ~$0.50/run kalau pakai Claude Haiku, ~$1.50/run pakai Claude Sonnet).

Versi sekarang sudah cukup untuk data collection + signal detection. Action items strategis tetap kamu/tim yang generate dari baca PDF tiap Senin (5-10 menit). Jangan ekspektasikan automation = autopilot total.
