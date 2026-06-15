# IG Audit Automation — Weekly + Monthly via 1 Cron, dengan detail layer

Otomatis fetch data IG **14 akun** setiap Senin 09:00 WIB, generate **2 PDF dalam 1 trigger** (analisis 7 hari + 30 hari) **+ 3 detail layer**, kirim ke Discord.

**Konfigurasi:**
- **1 cron** (Senin 09:00 WIB)
- **2 analisis output:** weekly (7d) + monthly rolling (30d)
- **3 detail layers:**
  - 🎬 Reels deep metrics (plays, plays-to-likes ratio, audio trends) — setiap minggu
  - 💬 Comments + sentiment (5 top competitor posts, lead detection) — setiap minggu
  - 🏷️ Hashtag market tracking (5 key hashtags) — **hanya 1× per bulan (first Monday)**
- Setiap Senin pagi Discord dapat 1 message dengan 2 PDF attached

## Roster (14 akun)
- **Corporate (2):** @alazharmemorialgarden, @lestari.memorialpark
- **Agent Al Azhar Memorial group (4):** @alazharmemorialpark, @alazharpemakamanmuslim, @alazhar.memorial, @alazhar_memorial
- **Competitor (8):** @insiramemorialpark, @firdausmemorialpark, @baqimemorialpark.bogor, @dt.memorialpark, @sandiegohills, @marketing_sandiegohills, @sandiegohillsgallery, @graha.sentosa

## Setup (15 menit, sekali saja)

### 1. Daftar Apify
- https://console.apify.com/sign-up → free trial $5 credit
- Settings → Integrations → API tokens → copy "Personal API token"
- Cost: ~$0.05/profile × 14 akun = $0.70 per run
- 4 run/bulan = **~$2.80/bulan**

### 2. Bikin repo GitHub (private)
```bash
gh repo create ig-audit --private
```

### 3. Upload 4 file ke repo
```
ig-audit/
├── audit.py                          (main script — generate 2 windows)
├── requirements.txt                  (deps)
├── README.md
└── .github/workflows/
    └── weekly.yml                    (cron Senin, run audit.py)
```

**Catatan:** kalau di folder kamu masih ada `monthly.yml`, kamu boleh hapus — file itu di-deprecate (cron tidak akan trigger).

### 4. Set GitHub Secrets
Di repo: **Settings → Secrets and variables → Actions**
- `APIFY_TOKEN`
- `DISCORD_WEBHOOK`

### 5. Test manual run
- Tab **Actions** → "IG Audit (Weekly + Monthly Rolling)" → **Run workflow** → Run
- Tunggu ~3-5 menit, cek Discord — harus dapat 1 message dengan 2 PDF attached
- Filename:
  - `ig-weekly-audit-YYYY-MM-DD.pdf` (7d detail)
  - `ig-monthly-audit-YYYY-MM-DD.pdf` (30d detail)

### 6. Done — Senin pagi otomatis jalan

## Cost estimasi
| Item | Cost/bulan |
|---|---|
| GitHub Actions (private repo) | $0 (free tier) |
| Profile scraper (14 akun × 4 run) | ~$2.80 |
| Reels deep metrics (8 competitor × 5 reels × 4 run) | ~$8 |
| Comments + sentiment (5 top posts × 4 run) | ~$6 |
| Hashtag tracking (5 hashtag × 1 run/month, first Monday only) | ~$4 |
| **Total** | **~$17–20/bulan** |

Apify trial $5 cukup untuk ~1 minggu pertama. Setelah itu pay-as-you-go (top up $20 = 1 bulan).

**Cost control:**
- Reels + Comments hanya untuk **competitor (8 akun)**, bukan corporate/agent
- Hashtag hanya jalan **first Monday of month** — script auto-skip kalau bukan tanggal 1-7 yang Senin
- Edit `HASHTAGS = [...]` di audit.py untuk ubah list hashtag yang di-track

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

### Ubah jadwal cron
Edit field `cron:` di `weekly.yml`. Format: `'menit jam tgl bulan hari'` (UTC).
- Senin 09:00 WIB: `'0 2 * * 1'` (default)
- Setiap hari 09:00 WIB: `'0 2 * * *'`

### Ubah window analisis
Kalau mau weekly + quarterly (90d) bukan monthly, edit `audit.py` line yang panggil `build_window_data(raw, 30)` jadi `build_window_data(raw, 90)`, dan ubah label "Monthly" jadi "Quarterly" di `build_pdf` + `build_discord_summary`.

## Output struktur

Discord message setiap Senin:
```
📊 IG Audit — YYYY-MM-DD · Weekly + Monthly Rolling
14 akun (2 corp · 4 agent · 8 competitor)

📅 7 HARI TERAKHIR
🔴 @insiramemorialpark — X eng / Y posts (ER Z%)
🏢 @alazharmemorialgarden — ...
🏆 Top: ...

🗓️ 30 HARI TERAKHIR
[sama, tapi window 30d]
🏆 Top: ...

📈 Format 30d: Reel avg X vs Static avg Y (Z× lebih efektif)
⚠ Dormant 30d (N akun): @...

📎 2 PDF attached
```

## Troubleshooting

**Apify return data kosong / akun tidak ditemukan**
- Username case-sensitive. Apify expect lowercase.
- Akun private tidak bisa di-scrape.

**Monthly window kelihatan ada post yang hilang**
- `APIFY_RESULTS_LIMIT = 100` (hardcoded di audit.py). Untuk akun yang post &gt;100×/30d, increase ke 200.

**Discord 401**
- Webhook URL expired. Generate ulang.

**GitHub Action gagal "out of Apify credit"**
- Trial $5 habis. Top up $10 via Apify console, jalan 3+ bulan.

## Hidden tradeoff

Setiap Senin kamu dapat 2 PDF: weekly + monthly. **Lebih banyak data, lebih banyak waktu baca.** Risk: jadi malas baca yang kedua. Saran: baca weekly setiap Senin (5 menit), baca monthly cuma di Senin pertama setiap bulan (10-15 menit). Sisanya monthly PDF di-archive aja — datanya tetap ter-store sebagai GitHub Actions artifact (retention 365 hari), bisa diakses anytime kalau perlu compare.
