"""
IG Audit — runs in GitHub Actions every Monday 09:00 WIB.

Single trigger, dual report + detail layers:
- Weekly (7d window) — what happened minggu lalu
- Monthly rolling (30d window) — trend overview 1 bulan terakhir
- DETAIL LAYERS (competitor only): Reels deep metrics + Comments sentiment
- HASHTAG TRACKING — only first Monday of month (cost control)

Pipeline:
1. Fetch profile + 100 posts (apify/instagram-profile-scraper) — 14 akun
2. Fetch reels deep metrics (apify/instagram-reel-scraper) — 8 competitor only, top 5 reels each
3. Fetch comments for top 5 cross-competitor posts (apify/instagram-post-scraper)
4. (Monthly only) Fetch hashtag stats (apify/instagram-hashtag-scraper) — 5 key hashtags
5. Filter dua kali: posts dalam 7d + 30d window
6. Generate 2 PDF (weekly + monthly), POST ke Discord webhook
7. Kirim ringkasan 1 row ke Notion database
8. Archive snapshots sebagai GitHub Actions artifact

Cost: ~$17/bulan (4 weekly runs + 1 hashtag run)

Required env vars:
- APIFY_TOKEN: dari https://console.apify.com/account/integrations
- DISCORD_WEBHOOK: webhook URL channel target

Optional env vars (kalau diset, ringkasan dikirim ke Notion):
- NOTION_TOKEN: Internal Integration Secret dari notion.so/my-integrations
- NOTION_DATABASE_ID: 32-char ID database "IG Audit Log"

Run locally: python audit.py
"""

import os
import json
import sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import requests

APIFY_TOKEN = os.environ["APIFY_TOKEN"]
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"]
APIFY_RESULTS_LIMIT = 100  # cover 30d untuk akun aktif
REELS_PER_COMPETITOR = 5  # top reels per competitor
TOP_POSTS_FOR_COMMENTS = 5  # cross-competitor top posts to fetch comments
COMMENTS_PER_POST = 30  # comments per post sample

# Hashtag list untuk monthly tracking — edit sesuai strategy
HASHTAGS = [
    "pemakamanmuslim",
    "pemakamansyariah",
    "wakaf",
    "akhiryangbaik",
    "memorialpark",
]

# Account roster — edit sesuai kebutuhan
# Agent al azhar memorial tampilkan terpisah, jangan di-sum
ROSTER = {
    # Corporate (2)
    "alazharmemorialgarden": "corporate",
    "lestari.memorialpark": "corporate",
    # Agent Al Azhar Memorial group (4) — tetap terpisah, jangan di-sum
    "alazhar_memorial": "agent",
    "alazharpemakamanmuslim": "agent",
    "alazhar.memorial": "agent",
    "alazharmemorialpark": "agent",
    # Competitor (8)
    "insiramemorialpark": "competitor",
    "firdausmemorialpark": "competitor",
    "baqimemorialpark.bogor": "competitor",
    "dt.memorialpark": "competitor",
    "sandiegohills": "competitor",
    "marketing_sandiegohills": "competitor",
    "sandiegohillsgallery": "competitor",
    "graha.sentosa": "competitor",
}

# WIB timezone (UTC+7)
WIB = timezone(timedelta(hours=7))
NOW = datetime.now(WIB)
RUN_DATE = NOW.strftime("%Y-%m-%d")


def call_apify_actor(actor_id, payload, timeout=900):
    """Generic Apify actor caller."""
    url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items?token={APIFY_TOKEN}"
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_apify(usernames):
    """Profile scraper — 100 latest posts per akun, cukup untuk 30d window."""
    return call_apify_actor("apify~instagram-profile-scraper", {
        "usernames": usernames,
        "resultsLimit": APIFY_RESULTS_LIMIT,
    })


def fetch_reels_metrics(competitor_usernames):
    """Reel deep metrics — top reels per competitor account.
    Returns: dict {username: [reel_data, ...]}
    Cost: ~$0.05 per reel × 5 × 8 = $2/run."""
    print(f"Fetching reels deep metrics for {len(competitor_usernames)} competitors...")
    try:
        raw = call_apify_actor("apify~instagram-reel-scraper", {
            "username": competitor_usernames,
            "resultsLimit": REELS_PER_COMPETITOR,
        })
    except Exception as e:
        print(f"⚠ Reels fetch failed: {e}")
        return {}

    grouped = defaultdict(list)
    for r in raw:
        owner = r.get("ownerUsername") or r.get("username")
        if not owner:
            continue
        plays = r.get("videoPlayCount") or r.get("videoViewCount") or 0
        likes = r.get("likesCount", 0) or 0
        grouped[owner].append({
            "shortcode": r.get("shortCode"),
            "caption": (r.get("caption") or "")[:120],
            "plays": plays,
            "likes": likes,
            "comments": r.get("commentsCount", 0) or 0,
            "duration": r.get("videoDuration", 0) or 0,
            "audio_title": r.get("musicInfo", {}).get("song_name") if r.get("musicInfo") else None,
            "audio_artist": r.get("musicInfo", {}).get("artist_name") if r.get("musicInfo") else None,
            "plays_to_likes": (likes / plays * 100) if plays else 0,
        })
    return dict(grouped)


def fetch_post_comments(top_post_urls):
    """Comments + basic sentiment untuk top posts cross-competitor.
    Returns: dict {post_url: [comment_data, ...]}
    Cost: ~$0.30 per post × 5 = $1.50/run."""
    if not top_post_urls:
        return {}
    print(f"Fetching comments for {len(top_post_urls)} top posts...")
    try:
        raw = call_apify_actor("apify~instagram-comment-scraper", {
            "directUrls": top_post_urls,
            "resultsLimit": COMMENTS_PER_POST,
        })
    except Exception as e:
        print(f"⚠ Comments fetch failed: {e}")
        return {}

    grouped = defaultdict(list)
    for c in raw:
        post_url = c.get("postUrl") or c.get("ownerUsername")
        text = (c.get("text") or "").strip()
        if not text:
            continue
        grouped[post_url].append({
            "text": text[:200],
            "author": c.get("ownerUsername"),
            "likes": c.get("likesCount", 0) or 0,
            "sentiment": classify_sentiment(text),
        })
    return dict(grouped)


def classify_sentiment(text):
    """Simple keyword-based sentiment. Bukan LLM — fast + free.
    Untuk full LLM sentiment, integrate Anthropic API call."""
    t = text.lower()
    POSITIVE = ["bagus", "indah", "amin", "barakallah", "terima kasih", "mantap",
                "🥰", "❤", "🤍", "💚", "🙏", "amazing", "love", "👍"]
    NEGATIVE = ["mahal", "ga jelas", "kurang", "kecewa", "nggak", "tidak",
                "😢", "😡", "buruk", "scam"]
    LEAD = ["berapa", "harga", "dm", "wa", "info", "kontak", "pesan",
            "konsultasi", "kavling", "tipe"]
    SPAM = ["follow back", "follback", "fb", "spam", "promo", "shopee", "tokped"]

    if any(k in t for k in SPAM):
        return "spam"
    if any(k in t for k in LEAD):
        return "lead"
    if any(k in t for k in POSITIVE):
        return "positive"
    if any(k in t for k in NEGATIVE):
        return "negative"
    return "neutral"


def is_first_monday_of_month():
    """Check if today is first Monday of the month (untuk hashtag tracking)."""
    return NOW.weekday() == 0 and NOW.day <= 7


def fetch_hashtag_stats(hashtags):
    """Hashtag tracking — top posts + reach per hashtag.
    Only runs first Monday of month untuk cost control.
    Cost: ~$0.80/hashtag × 5 = $4/month."""
    if not is_first_monday_of_month():
        print("Skipping hashtag tracking — not first Monday of month")
        return {}
    print(f"Fetching hashtag stats for {len(hashtags)} hashtags...")
    try:
        raw = call_apify_actor("apify~instagram-hashtag-scraper", {
            "hashtags": hashtags,
            "resultsLimit": 20,  # top 20 posts per hashtag
        })
    except Exception as e:
        print(f"⚠ Hashtag fetch failed: {e}")
        return {}

    grouped = defaultdict(list)
    for p in raw:
        tag = p.get("hashtag") or p.get("query")
        if not tag:
            continue
        grouped[tag].append({
            "shortcode": p.get("shortCode"),
            "owner": p.get("ownerUsername"),
            "likes": p.get("likesCount", 0) or 0,
            "comments": p.get("commentsCount", 0) or 0,
            "caption": (p.get("caption") or "")[:100],
        })
    return dict(grouped)


def parse_post(p):
    """Normalize raw Apify post dict."""
    ts = p.get("timestamp")
    if not ts:
        return None
    post_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(WIB)
    fmt = (p.get("type") or "").lower()
    format_str = {"video": "reel", "sidecar": "carousel", "image": "static"}.get(fmt, "static")
    return {
        "shortcode": p.get("shortCode") or p.get("shortcode"),
        "format": format_str,
        "caption": (p.get("caption") or "")[:140],
        "likes": p.get("likesCount", 0) or 0,
        "comments": p.get("commentsCount", 0) or 0,
        "views": p.get("videoViewCount") or p.get("videoPlayCount") or 0,
        "dt": post_dt,
        "date": post_dt.strftime("%m/%d"),
        "url": f"https://www.instagram.com/p/{p.get('shortCode') or p.get('shortcode')}/",
    }


def build_window_data(raw_profiles, window_days):
    """Filter posts ke window, hitung scoreboard per akun."""
    window_start = NOW - timedelta(days=window_days)
    out = []
    for profile in raw_profiles:
        username = profile.get("username", "")
        if username not in ROSTER:
            continue
        status = ROSTER[username]
        follower_count = profile.get("followersCount", 0) or 0
        posts_total = profile.get("postsCount", 0) or 0

        all_parsed = [parse_post(p) for p in profile.get("latestPosts", [])]
        in_window = [p for p in all_parsed if p and p["dt"] >= window_start]

        eng_total = sum(p["likes"] + p["comments"] for p in in_window)
        er = (eng_total / follower_count * 100) if follower_count else 0
        out.append({
            "username": username,
            "status": status,
            "followers": follower_count,
            "posts_total": posts_total,
            "posts_window": in_window,
            "eng_window": eng_total,
            "er": er,
        })
    return out


def compute_insights(data):
    """Top 3 cross-account + format winners."""
    all_posts = []
    for d in data:
        for p in d["posts_window"]:
            all_posts.append({**p, "account": d["username"], "status": d["status"]})
    top3 = sorted(all_posts, key=lambda p: p["likes"] + p["comments"], reverse=True)[:3]

    fmt_stats = defaultdict(lambda: {"n": 0, "likes_sum": 0})
    for p in all_posts:
        fmt_stats[p["format"]]["n"] += 1
        fmt_stats[p["format"]]["likes_sum"] += p["likes"]
    fmt_winners = {f: {"n": s["n"], "avg": s["likes_sum"] / s["n"] if s["n"] else 0}
                   for f, s in fmt_stats.items()}
    return top3, fmt_winners, all_posts


def build_discord_summary(weekly, monthly):
    """Combined summary untuk Discord — both windows in one message."""
    w_data, w_top3, w_fmt = weekly
    m_data, m_top3, m_fmt = monthly

    lines = [f"**📊 IG Audit — {RUN_DATE}** · Weekly + Monthly Rolling"]
    lines.append(f"13 akun (2 corp · 3 agent · 8 competitor)")
    lines.append("")

    # Weekly section
    lines.append("**📅 7 HARI TERAKHIR**")
    w_sorted = sorted(w_data, key=lambda d: d["eng_window"], reverse=True)
    for d in w_sorted[:3]:
        emoji = {"corporate": "🏢", "agent": "👥", "competitor": "🔴"}[d["status"]]
        lines.append(f"{emoji} @{d['username']} — {d['eng_window']} eng / {len(d['posts_window'])} posts (ER {d['er']:.2f}%)")
    if w_top3:
        t = w_top3[0]
        lines.append(f"🏆 Top: @{t['account']} {t['format'].upper()} — **{t['likes']}♥/{t['comments']}💬**")
        lines.append(f"_{t['caption'][:100]}_")

    lines.append("")
    # Monthly section
    lines.append("**🗓️ 30 HARI TERAKHIR**")
    m_sorted = sorted(m_data, key=lambda d: d["eng_window"], reverse=True)
    for d in m_sorted[:3]:
        emoji = {"corporate": "🏢", "agent": "👥", "competitor": "🔴"}[d["status"]]
        lines.append(f"{emoji} @{d['username']} — {d['eng_window']} eng / {len(d['posts_window'])} posts (ER {d['er']:.2f}%)")
    if m_top3:
        t = m_top3[0]
        lines.append(f"🏆 Top: @{t['account']} {t['format'].upper()} — **{t['likes']}♥/{t['comments']}💬**")
        lines.append(f"_{t['caption'][:100]}_")

    # Format winner cross-window
    lines.append("")
    if m_fmt.get("reel") and m_fmt.get("static"):
        r_avg = m_fmt["reel"]["avg"]
        s_avg = m_fmt["static"]["avg"]
        ratio = r_avg / s_avg if s_avg else 0
        lines.append(f"📈 Format 30d: Reel avg {r_avg:.1f} vs Static avg {s_avg:.1f} ({ratio:.1f}× lebih efektif)")

    # Dormant flag (30d)
    dormant = [d for d in m_data if len(d["posts_window"]) == 0]
    if dormant:
        names = ", ".join("@" + d["username"] for d in dormant[:5])
        suffix = f" (+{len(dormant)-5} lain)" if len(dormant) > 5 else ""
        lines.append(f"\n⚠ Dormant 30d ({len(dormant)} akun): {names}{suffix}")

    lines.append("\n📎 2 PDF attached — Weekly detail + Monthly rolling")
    return "\n".join(lines)[:1900]


def build_pdf(data, top3, window_days, label, out_path,
              reels_detail=None, comments_detail=None, hashtag_detail=None):
    """Colorful PDF report. Detail sections rendered jika data tersedia."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    )

    NAVY = colors.HexColor("#0F172A")
    INK = colors.HexColor("#1E293B")
    MUTED = colors.HexColor("#64748B")
    HAIR = colors.HexColor("#CBD5E1")
    LIGHT_BG = colors.HexColor("#F8FAFC")
    CORP = colors.HexColor("#059669")
    AGENT = colors.HexColor("#7C3AED")
    COMP = colors.HexColor("#E11D48")
    HEAT_HOT = colors.HexColor("#DC2626")

    body = ParagraphStyle("Body", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK)
    small = ParagraphStyle("Small", fontName="Helvetica", fontSize=8, leading=11, textColor=MUTED)
    h1 = ParagraphStyle("H1", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=NAVY)
    h2 = ParagraphStyle("H2", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=NAVY,
                        spaceBefore=10, spaceAfter=4)
    tcell = ParagraphStyle("TC", fontName="Helvetica", fontSize=8, leading=10, textColor=INK)
    thdr = ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=colors.white)

    sc = {"corporate": CORP, "agent": AGENT, "competitor": COMP, "unknown": MUTED}

    window_start = NOW - timedelta(days=window_days)
    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    story = [Paragraph(f"IG {label} — {RUN_DATE}", h1)]
    story.append(Paragraph(
        f"Window {window_start.strftime('%Y-%m-%d')} → {NOW.strftime('%Y-%m-%d')} "
        f"({window_days} hari) · 13 akun · automated", small))
    story.append(Spacer(1, 8))

    # Scoreboard
    story.append(Paragraph("Scoreboard", h2))
    rows = [["Akun", "Status", "Followers", f"Posts {window_days}d", f"Eng. {window_days}d", "ER %"]]
    sorted_data = sorted(data, key=lambda d: d["followers"], reverse=True)
    for d in sorted_data:
        c = sc.get(d["status"], MUTED)
        rows.append([
            Paragraph(f"@{d['username']}", tcell),
            Paragraph(f"<font color='{c.hexval()}'><b>● {d['status']}</b></font>", tcell),
            Paragraph(f"{d['followers']:,}", tcell),
            Paragraph(str(len(d['posts_window'])), tcell),
            Paragraph(str(d['eng_window']), tcell),
            Paragraph(f"{d['er']:.2f}%", tcell),
        ])
    t = Table([[Paragraph(x, thdr) if i == 0 and isinstance(x, str) else x for x in r]
               for i, r in enumerate(rows)],
              colWidths=[5 * cm, 3.5 * cm, 2.5 * cm, 2 * cm, 2 * cm, 2 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.3, HAIR),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
    ]))
    story.append(t)

    # Top 3
    story.append(Paragraph(f"🏆 Top 3 Posts {window_days}d", h2))
    for i, p in enumerate(top3, 1):
        story.append(Paragraph(
            f"<b>#{i}</b> @{p['account']} · {p['format'].upper()} · "
            f"<b>{p['likes']}♥ / {p['comments']}💬</b> · {p['date']}",
            ParagraphStyle("tp", fontName="Helvetica", fontSize=9.5, leading=12,
                          textColor=HEAT_HOT if i == 1 else INK, spaceAfter=2)))
        story.append(Paragraph(p["caption"], small))
        story.append(Spacer(1, 4))

    # Per-akun detail
    story.append(Paragraph(f"Per-akun detail ({window_days} hari)", h2))
    for d in sorted_data:
        if not d["posts_window"]:
            continue
        c = sc.get(d["status"], MUTED)
        story.append(Paragraph(
            f"<font color='{c.hexval()}'><b>@{d['username']}</b></font> · "
            f"{d['status']} · {len(d['posts_window'])} posts · {d['eng_window']} eng · ER {d['er']:.2f}%",
            ParagraphStyle("ah", fontName="Helvetica-Bold", fontSize=9.5, leading=12,
                          textColor=INK, spaceBefore=6, spaceAfter=3)))
        post_rows = [["Tgl", "Format", "Caption", "♥", "💬"]]
        for p in d["posts_window"]:
            post_rows.append([p["date"], p["format"], p["caption"][:80],
                              str(p["likes"]), str(p["comments"])])
        t = Table([[Paragraph(x, thdr) if i == 0 and isinstance(x, str) else Paragraph(str(x), tcell)
                    for x in r] for i, r in enumerate(post_rows)],
                  colWidths=[1.2 * cm, 1.8 * cm, 11 * cm, 1.5 * cm, 1.5 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), c),
            ("GRID", (0, 0), (-1, -1), 0.2, HAIR),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ]))
        story.append(t)

    dormant = [d for d in data if not d["posts_window"]]
    if dormant:
        story.append(Paragraph(
            f"<b>Dormant {window_days}d ({len(dormant)} akun):</b> " +
            ", ".join(f"@{d['username']}" for d in dormant), small))

    # ─── DETAIL LAYER 1: Reels deep metrics ──────────────────────────
    if reels_detail:
        story.append(Paragraph("🎬 Reels Deep Metrics (competitor)", h2))
        rd_rows = [["Akun", "Reel snippet", "Plays", "Likes", "P:L %", "Audio"]]
        for username, reels in reels_detail.items():
            # Sort by plays desc, take top 3 per akun
            top_reels = sorted(reels, key=lambda r: r["plays"], reverse=True)[:3]
            for r in top_reels:
                audio = f"{r['audio_title']} – {r['audio_artist']}" if r['audio_title'] else "Original"
                rd_rows.append([
                    f"@{username}",
                    r["caption"][:60],
                    f"{r['plays']:,}",
                    str(r["likes"]),
                    f"{r['plays_to_likes']:.1f}%",
                    audio[:35]
                ])
        t = Table([[Paragraph(x, thdr) if i == 0 and isinstance(x, str) else Paragraph(str(x), tcell)
                    for x in row] for i, row in enumerate(rd_rows)],
                  colWidths=[3 * cm, 5.5 * cm, 2 * cm, 1.5 * cm, 1.5 * cm, 4 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COMP),
            ("GRID", (0, 0), (-1, -1), 0.2, HAIR),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ]))
        story.append(t)

    # ─── DETAIL LAYER 2: Comments + sentiment ──────────────────────
    if comments_detail:
        story.append(Paragraph("💬 Top Posts — Comments + Sentiment", h2))
        for post_url, comments in comments_detail.items():
            sent_count = defaultdict(int)
            for c in comments:
                sent_count[c["sentiment"]] += 1
            leads = [c for c in comments if c["sentiment"] == "lead"]
            negative = [c for c in comments if c["sentiment"] == "negative"]

            story.append(Paragraph(
                f"<b>{post_url[-30:]}</b> · "
                f"<font color='#16A34A'>+{sent_count['positive']}</font> · "
                f"<font color='#DC2626'>-{sent_count['negative']}</font> · "
                f"<font color='#0891B2'>leads {sent_count['lead']}</font> · "
                f"neutral {sent_count['neutral']} · spam {sent_count['spam']}",
                ParagraphStyle("ch", fontName="Helvetica-Bold", fontSize=8.5,
                              leading=11, textColor=INK, spaceBefore=4, spaceAfter=2)))
            if leads:
                story.append(Paragraph("<b>Lead signals:</b>", small))
                for c in leads[:3]:
                    story.append(Paragraph(
                        f"• @{c['author']}: <i>{c['text'][:120]}</i>",
                        ParagraphStyle("cl", fontName="Helvetica", fontSize=7.5,
                                      leading=10, textColor=INK, leftIndent=6)))
            if negative:
                story.append(Paragraph("<b>Negative:</b>", small))
                for c in negative[:2]:
                    story.append(Paragraph(
                        f"• @{c['author']}: <i>{c['text'][:120]}</i>",
                        ParagraphStyle("cn", fontName="Helvetica", fontSize=7.5,
                                      leading=10, textColor=colors.HexColor("#B45309"),
                                      leftIndent=6)))

    # ─── DETAIL LAYER 3: Hashtag tracking (monthly only) ───────────
    if hashtag_detail:
        story.append(Paragraph("🏷️ Hashtag Market — Top Posts per Tag", h2))
        ht_rows = [["Hashtag", "Owner", "Likes", "Cmt", "Caption snippet"]]
        for tag, posts in hashtag_detail.items():
            top_posts = sorted(posts, key=lambda p: p["likes"] + p["comments"],
                              reverse=True)[:3]
            for p in top_posts:
                is_competitor = p["owner"] in ROSTER and ROSTER[p["owner"]] == "competitor"
                marker = " 🔴" if is_competitor else ""
                ht_rows.append([
                    f"#{tag}",
                    f"@{p['owner']}{marker}",
                    str(p["likes"]),
                    str(p["comments"]),
                    p["caption"][:70]
                ])
        t = Table([[Paragraph(x, thdr) if i == 0 and isinstance(x, str) else Paragraph(str(x), tcell)
                    for x in row] for i, row in enumerate(ht_rows)],
                  colWidths=[3.5 * cm, 4 * cm, 1.5 * cm, 1.5 * cm, 7 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#CA8A04")),
            ("GRID", (0, 0), (-1, -1), 0.2, HAIR),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ]))
        story.append(t)

    doc.build(story)


def post_discord_combined(summary, pdf_paths):
    """POST summary + multiple PDF attachments dalam satu Discord message."""
    payload = {"username": "IG Audit Bot", "content": summary}
    files = {}
    handles = []
    for idx, p in enumerate(pdf_paths, 1):
        h = open(p, "rb")
        handles.append(h)
        files[f"file{idx}"] = (os.path.basename(p), h, "application/pdf")
    try:
        r = requests.post(DISCORD_WEBHOOK,
                          data={"payload_json": json.dumps(payload)},
                          files=files, timeout=120)
        r.raise_for_status()
        print(f"Discord OK: status {r.status_code}, attached {len(pdf_paths)} PDF")
    finally:
        for h in handles:
            h.close()


def post_to_notion(w_data, w_top3, m_data, m_top3, m_fmt,
                   reels_detail, comments_detail, hashtag_detail, summary):
    """Kirim 1 row ringkasan per run ke Notion database.
    No-op (skip aman) kalau NOTION_TOKEN / NOTION_DATABASE_ID tidak diset —
    audit & Discord tetap jalan normal."""
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not token or not db_id:
        print("Notion skip — NOTION_TOKEN / NOTION_DATABASE_ID tidak diset")
        return

    def rt(s):
        return {"rich_text": [{"text": {"content": (s or "-")[:1900]}}]}

    w_top = max(w_data, key=lambda d: d["eng_window"], default=None)
    m_top = max(m_data, key=lambda d: d["eng_window"], default=None)
    top_akun_7d = f"@{w_top['username']} ({w_top['eng_window']} eng)" if w_top else "-"
    top_akun_30d = f"@{m_top['username']} ({m_top['eng_window']} eng)" if m_top else "-"

    if w_top3:
        t = w_top3[0]
        top_post_7d = f"@{t['account']} {t['format'].upper()} — {t['likes']}/{t['comments']}"
    else:
        top_post_7d = "-"

    fmt_winner = "-"
    if m_fmt.get("reel") and m_fmt.get("static"):
        r_avg, s_avg = m_fmt["reel"]["avg"], m_fmt["static"]["avg"]
        ratio = r_avg / s_avg if s_avg else 0
        fmt_winner = f"Reel {r_avg:.1f} vs Static {s_avg:.1f} ({ratio:.1f}x)"

    dormant = [d for d in m_data if not d["posts_window"]]
    dormant_names = ", ".join("@" + d["username"] for d in dormant) or "-"
    reels_n = sum(len(v) for v in reels_detail.values()) if reels_detail else 0
    leads_n = sum(1 for v in comments_detail.values() for c in v
                  if c["sentiment"] == "lead") if comments_detail else 0

    props = {
        "Run":               {"title": [{"text": {"content": f"IG Audit {RUN_DATE}"}}]},
        "Run Date":          {"date": {"start": RUN_DATE}},
        "Top Akun 7d":       rt(top_akun_7d),
        "Top Post 7d":       rt(top_post_7d),
        "Top Akun 30d":      rt(top_akun_30d),
        "Format Winner 30d": rt(fmt_winner),
        "Dormant Count":     {"number": len(dormant)},
        "Dormant Akun":      rt(dormant_names),
        "Reels Analyzed":    {"number": reels_n},
        "Lead Signals":      {"number": leads_n},
        "Hashtag Run":       {"checkbox": bool(hashtag_detail)},
        "Summary":           rt(summary),
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post("https://api.notion.com/v1/pages", headers=headers,
                          json={"parent": {"database_id": db_id}, "properties": props},
                          timeout=60)
        r.raise_for_status()
        print(f"Notion OK: row dibuat untuk {RUN_DATE}")
    except Exception as e:
        resp = getattr(e, "response", None)
        print(f"⚠ Notion gagal: {e}" + (f" — {resp.text}" if resp is not None else ""))


def main():
    print(f"Run date: {RUN_DATE} WIB")
    print(f"Fetching {len(ROSTER)} profiles from Apify (resultsLimit={APIFY_RESULTS_LIMIT})...")

    raw = fetch_apify(list(ROSTER.keys()))
    print(f"Got {len(raw)} profile records")

    # Build both windows
    w_data = build_window_data(raw, 7)
    m_data = build_window_data(raw, 30)
    w_top3, w_fmt, _ = compute_insights(w_data)
    m_top3, m_fmt, _ = compute_insights(m_data)

    # Detail layers (competitor only untuk cost control)
    competitors = [u for u, s in ROSTER.items() if s == "competitor"]
    reels_detail = fetch_reels_metrics(competitors)

    # Top 5 post URL cross-competitor untuk comments fetch
    competitor_posts_30d = []
    for d in m_data:
        if d["status"] == "competitor":
            for p in d["posts_window"]:
                competitor_posts_30d.append({**p, "username": d["username"]})
    top_post_urls = [p["url"] for p in
                     sorted(competitor_posts_30d,
                            key=lambda p: p["likes"] + p["comments"],
                            reverse=True)[:TOP_POSTS_FOR_COMMENTS]]
    comments_detail = fetch_post_comments(top_post_urls)

    # Hashtag (monthly only)
    hashtag_detail = fetch_hashtag_stats(HASHTAGS)

    # Summary
    summary = build_discord_summary((w_data, w_top3, w_fmt), (m_data, m_top3, m_fmt))
    if reels_detail:
        summary += f"\n🎬 Reels analyzed: {sum(len(v) for v in reels_detail.values())} reels across {len(reels_detail)} competitors"
    if comments_detail:
        total_comments = sum(len(v) for v in comments_detail.values())
        leads = sum(1 for v in comments_detail.values() for c in v if c["sentiment"] == "lead")
        summary += f"\n💬 Comments: {total_comments} analyzed · {leads} potential lead signals detected"
    if hashtag_detail:
        summary += f"\n🏷️ Hashtag stats: {len(hashtag_detail)} hashtag tracked (monthly)"

    print("\n--- SUMMARY ---")
    print(summary)
    print("---\n")

    # PDFs
    w_pdf = f"ig-weekly-audit-{RUN_DATE}.pdf"
    m_pdf = f"ig-monthly-audit-{RUN_DATE}.pdf"
    build_pdf(w_data, w_top3, 7, "Weekly Audit", w_pdf,
              reels_detail=reels_detail, comments_detail=comments_detail)
    print(f"Weekly PDF built: {w_pdf}")
    build_pdf(m_data, m_top3, 30, "Monthly Rolling Audit", m_pdf,
              reels_detail=reels_detail, comments_detail=comments_detail,
              hashtag_detail=hashtag_detail)
    print(f"Monthly PDF built: {m_pdf}")

    # Snapshots — include detail layers
    for label, dat in [("weekly", w_data), ("monthly", m_data)]:
        sp = f"snapshot-{label}-{RUN_DATE}.json"
        with open(sp, "w") as f:
            json.dump({
                "run_date": RUN_DATE, "window": label,
                "accounts": dat,
                "reels_detail": reels_detail,
                "comments_detail": comments_detail,
                "hashtag_detail": hashtag_detail if label == "monthly" else {},
            }, f, indent=2, default=str)
        print(f"Snapshot saved: {sp}")

    # Kirim ringkasan ke Notion (no-op kalau env var kosong)
    post_to_notion(w_data, w_top3, m_data, m_top3, m_fmt,
                   reels_detail, comments_detail, hashtag_detail, summary)

    # Post both PDFs in one Discord message
    post_discord_combined(summary, [w_pdf, m_pdf])


if __name__ == "__main__":
    main()
