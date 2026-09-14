"""
Layer analisa untuk IG Audit — dipisah dari audit.py supaya audit.py tetap ramping.

Isi:
1. Agregasi VIEWS per akun.
   CATATAN PENTING: apify/instagram-profile-scraper hanya mengembalikan
   videoViewCount untuk post bertipe Video. Carousel (Sidecar) dan Image
   TIDAK punya field views sama sekali. Jadi semua angka views di sini
   adalah REEL-ONLY, dan dilabeli demikian. Jangan pernah tampilkan
   sebagai "Views" polos.

2. Pemisahan COLLAB POST — post yang muncul di grid sebuah akun tapi
   ownerUsername-nya akun lain. Itu audience pinjaman, bukan performa
   akun tsb. Dihitung terpisah supaya scoreboard bisa dibandingkan
   antar-minggu.

3. Analisa Top 3: sinyal kuantitatif (reach multiplier, rasio terhadap
   median akun sendiri, jam posting, panjang caption, dst) + penjelasan
   naratif dari Claude API.

Env:
- ANTHROPIC_API_KEY : wajib untuk layer naratif. Kalau kosong, modul tetap
                      jalan dan hanya menghasilkan sinyal kuantitatif.
- ANTHROPIC_MODEL   : opsional. Kalau kosong, model dipilih otomatis dari
                      /v1/models (ambil sonnet terbaru yang tersedia).
"""

import os
import re
import json
import statistics

import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL_ENV = os.environ.get("ANTHROPIC_MODEL", "").strip()
ANTHROPIC_VERSION = "2023-06-01"

_MODEL_CACHE = {}


# ----------------------------------------------------------------------
# 1. COLLAB + VIEWS
# ----------------------------------------------------------------------

def split_own_vs_collab(parsed_posts, username):
    """Pisahkan post milik akun sendiri vs collab post milik akun lain.

    Apify mengembalikan ownerUsername. Kalau kosong (field tidak ada),
    post dianggap milik akun sendiri — konservatif, tidak membuang data.
    """
    own, collab = [], []
    for p in parsed_posts:
        owner = (p.get("owner") or "").lower()
        if owner and owner != username.lower():
            collab.append(p)
        else:
            own.append(p)
    return own, collab


def views_metrics(posts, followers):
    """Agregasi views. REEL-ONLY — lihat catatan di header modul."""
    reels = [p for p in posts if p.get("format") == "reel"]
    views_total = sum(p.get("views", 0) or 0 for p in reels)
    n = len(reels)
    return {
        "reel_views": views_total,
        "reels_count": n,
        "views_per_reel": (views_total / n) if n else 0.0,
        "reach_multiplier": (views_total / followers) if followers else 0.0,
    }


# ----------------------------------------------------------------------
# 2. SINYAL PER POST
# ----------------------------------------------------------------------

_CTA_PAT = re.compile(
    r"(hubungi|konsultasi|kontak|chat|dm|klik|link|daftar|kunjungi|follow|save|simpan|komen)",
    re.I,
)


def _median(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else 0.0


def post_signals(post, account_row):
    """Sinyal kuantitatif satu post, relatif terhadap akunnya sendiri."""
    followers = account_row.get("followers", 0) or 0
    own_posts = account_row.get("posts_window", []) or []

    eng = (post.get("likes", 0) or 0) + (post.get("comments", 0) or 0)
    med_eng = _median([(p.get("likes", 0) or 0) + (p.get("comments", 0) or 0)
                       for p in own_posts])
    reel_views = [p.get("views", 0) or 0 for p in own_posts
                  if p.get("format") == "reel" and (p.get("views") or 0) > 0]
    med_views = _median(reel_views)

    views = post.get("views", 0) or 0
    dt = post.get("dt")

    return {
        "eng": eng,
        "views": views,
        "is_reel": post.get("format") == "reel",
        "is_collab": bool(post.get("is_collab")),
        "owner": post.get("owner") or "",
        "likes_hidden": bool(post.get("likes_hidden")),
        "followers": followers,
        # Berapa kali lipat dibanding follower sendiri. >1 artinya IG
        # mendorong post ini keluar dari lingkaran follower.
        "reach_multiplier": (views / followers) if followers else 0.0,
        # Berapa kali lipat dibanding post rata-rata akun itu sendiri.
        "eng_vs_median": (eng / med_eng) if med_eng else 0.0,
        "views_vs_median": (views / med_views) if med_views else 0.0,
        "median_eng_akun": med_eng,
        "median_views_reel_akun": med_views,
        "hour_wib": dt.hour if dt is not None else None,
        "weekday": dt.strftime("%A") if dt is not None else None,
        "caption_len": len(post.get("caption_full") or ""),
        "hashtag_count": len(post.get("hashtags") or []),
        "has_question": "?" in (post.get("caption_full") or ""),
        "has_cta": bool(_CTA_PAT.search(post.get("caption_full") or "")),
        "product_type": post.get("product_type") or "",
    }


def pick_top(rows, n=3):
    """Top-n post lintas akun dalam satu scope, diurut by engagement."""
    pool = []
    by_user = {}
    for d in rows:
        by_user[d["username"]] = d
        for p in d.get("posts_window", []) or []:
            pool.append({**p, "account": d["username"], "status": d["status"]})
    pool.sort(key=lambda p: (p.get("likes", 0) or 0) + (p.get("comments", 0) or 0),
              reverse=True)
    out = []
    for p in pool[:n]:
        item = dict(p)
        item["signals"] = post_signals(p, by_user[p["account"]])
        out.append(item)
    return out


# ----------------------------------------------------------------------
# 3. PENJELASAN NARATIF (Claude API)
# ----------------------------------------------------------------------

def _pick_model():
    """Ambil model yang benar-benar tersedia di akun, jangan menebak nama."""
    if ANTHROPIC_MODEL_ENV:
        return ANTHROPIC_MODEL_ENV
    if "id" in _MODEL_CACHE:
        return _MODEL_CACHE["id"]
    try:
        r = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": ANTHROPIC_API_KEY,
                     "anthropic-version": ANTHROPIC_VERSION},
            timeout=30,
        )
        r.raise_for_status()
        ids = [m.get("id", "") for m in r.json().get("data", [])]
        for pref in ("sonnet", "opus", "haiku"):
            for mid in ids:
                if pref in mid:
                    _MODEL_CACHE["id"] = mid
                    return mid
        if ids:
            _MODEL_CACHE["id"] = ids[0]
            return ids[0]
    except Exception as e:
        print(f"[analysis] gagal ambil daftar model: {e}")
    return ""


_SYSTEM = """Kamu analis konten Instagram untuk industri memorial park (pemakaman pre-need) di Indonesia.
Pembacamu seorang CEO — operator, bukan pemula. Tulis padat, tanpa basa-basi, bahasa Indonesia.

ATURAN KERAS:
- Data yang tersedia hanya: views (khusus Reels), likes, comments, caption, jam posting, hashtag, follower akun, dan rasio terhadap median akun itu sendiri.
- Retention, watch time, saves, shares, reach breakdown follower vs non-follower TIDAK tersedia. JANGAN mengarang atau menyiratkan angka itu.
- Bedakan tegas mana yang TERBUKTI dari angka dan mana HIPOTESIS. Tandai hipotesis dengan kata "Hipotesis:".
- Kalau sebuah post adalah collab (ownerUsername berbeda dari akun), sebut terang-terangan bahwa performanya sebagian besar berasal dari audiens pinjaman, bukan kekuatan konten akun tsb.
- Kalau reach_multiplier di bawah 1, post itu TIDAK viral — ia bahkan belum menjangkau seluruh follower. Katakan apa adanya, jangan dipoles.
- Kategori duka/religi: jangan usulkan taktik yang eksploitatif, sensasional, atau menjanjikan hal yang tidak bisa dibuktikan.

Untuk SETIAP post, tulis 3 bagian pendek:
CARA: apa yang secara teknis dilakukan post ini (format, struktur hook, panjang caption, CTA, jam tayang).
KENAPA: mekanisme yang paling mungkin menjelaskan angkanya — distribusi algoritmik vs audiens pinjaman vs resonansi emosional. Sandarkan pada angka yang ada.
TIRU: satu aksi konkret yang bisa dieksekusi tim minggu depan. Spesifik, bukan nasihat umum.

Maksimal 110 kata per post. Tanpa heading markdown, tanpa emoji."""


def explain_top(items, scope_label, window_days):
    """Minta Claude menjelaskan Top 3. Satu panggilan untuk ketiganya."""
    if not items:
        return
    if not ANTHROPIC_API_KEY:
        for it in items:
            it["explain"] = ("[LLM tidak aktif — ANTHROPIC_API_KEY belum diset. "
                             "Hanya sinyal kuantitatif yang tersedia.]")
        return

    model = _pick_model()
    if not model:
        for it in items:
            it["explain"] = "[LLM tidak aktif — tidak ada model yang tersedia di akun API.]"
        return

    payload_posts = []
    for i, it in enumerate(items, 1):
        s = it["signals"]
        payload_posts.append({
            "no": i,
            "akun": it["account"],
            "status_akun": it["status"],
            "format": it["format"],
            "tanggal": it.get("date"),
            "caption": (it.get("caption_full") or it.get("caption") or "")[:700],
            "likes": it.get("likes"),
            "comments": it.get("comments"),
            "views_reel_only": s["views"],
            "follower_akun": s["followers"],
            "reach_multiplier_vs_follower": round(s["reach_multiplier"], 2),
            "engagement_vs_median_akun": round(s["eng_vs_median"], 2),
            "views_vs_median_reel_akun": round(s["views_vs_median"], 2),
            "jam_posting_wib": s["hour_wib"],
            "hari": s["weekday"],
            "panjang_caption": s["caption_len"],
            "jumlah_hashtag": s["hashtag_count"],
            "ada_pertanyaan_di_caption": s["has_question"],
            "ada_cta": s["has_cta"],
            "collab_post": s["is_collab"],
            "pemilik_asli_post": s["owner"],
            "likes_disembunyikan": s["likes_hidden"],
        })

    prompt = (
        f"Scope: {scope_label}. Window: {window_days} hari terakhir.\n\n"
        f"Berikut {len(payload_posts)} post dengan engagement tertinggi:\n\n"
        + json.dumps(payload_posts, ensure_ascii=False, indent=2)
        + "\n\nBalas HANYA sebagai JSON array, satu objek per post, "
          'format: [{"no":1,"cara":"...","kenapa":"...","tiru":"..."}]. '
          "Tanpa teks lain di luar JSON."
    )

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY,
                     "anthropic-version": ANTHROPIC_VERSION,
                     "content-type": "application/json"},
            json={"model": model, "max_tokens": 2000, "system": _SYSTEM,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=180,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        m = re.search(r"\[.*\]", text, re.S)
        parsed = json.loads(m.group(0)) if m else []
        by_no = {int(x.get("no", 0)): x for x in parsed}
        for i, it in enumerate(items, 1):
            x = by_no.get(i)
            if x:
                it["explain"] = (
                    f"CARA: {x.get('cara','-')}\n"
                    f"KENAPA: {x.get('kenapa','-')}\n"
                    f"TIRU: {x.get('tiru','-')}"
                )
            else:
                it["explain"] = "[LLM tidak mengembalikan analisa untuk post ini.]"
        print(f"[analysis] explain OK ({scope_label}, {window_days}d, model={model})")
    except Exception as e:
        print(f"[analysis] explain GAGAL ({scope_label}, {window_days}d): {e}")
        for it in items:
            it["explain"] = f"[Analisa LLM gagal: {e}]"


def analyze_scope(rows, scope_label, window_days, n=3):
    """Entry point: ambil Top-n dalam satu scope + jelaskan."""
    items = pick_top(rows, n=n)
    explain_top(items, scope_label, window_days)
    return items


def signal_line(item):
    """Ringkasan sinyal 1 baris, dipakai di PDF & Discord."""
    s = item["signals"]
    bits = []
    if s["is_reel"]:
        bits.append(f"{s['views']:,} views")
        if s["followers"]:
            bits.append(f"reach {s['reach_multiplier']:.1f}x follower")
    if s["eng_vs_median"]:
        bits.append(f"eng {s['eng_vs_median']:.1f}x median akun")
    if s["hour_wib"] is not None:
        bits.append(f"posting {s['hour_wib']:02d}:00 WIB")
    if s["is_collab"]:
        bits.append(f"COLLAB (pemilik: @{s['owner']})")
    if s["likes_hidden"]:
        bits.append("likes disembunyikan")
    return " · ".join(bits)
