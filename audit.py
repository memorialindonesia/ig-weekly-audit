"""
IG Audit — runs in GitHub Actions every Monday 09:00 WIB.

Single trigger, dual report:
- Weekly (7d window) — what happened minggu lalu
- Monthly rolling (30d window) — trend overview 1 bulan terakhir

Pipeline:
1. Fetch profile + 100 latest posts dari Apify Instagram Profile Scraper untuk 13 akun (sekali)
2. Filter dua kali: posts dalam 7d window + 30d window
3. Untuk masing-masing window: scoreboard, top 3, format mix, ER → generate PDF
4. POST keduanya ke Discord webhook dalam satu message (2 PDF attached)
5. Archive snapshots sebagai GitHub Actions artifact

Required env vars:
- APIFY_TOKEN: dari https://console.apify.com/account/integrations
- DISCORD_WEBHOOK: webhook URL channel target

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


def fetch_apify(usernames):
    """Sekali fetch — 100 latest posts per akun, cukup untuk 30d window."""
    url = f"https://api.apify.com/v2/acts/apify~instagram-profile-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
    payload = {"usernames": usernames, "resultsLimit": APIFY_RESULTS_LIMIT}
    r = requests.post(url, json=payload, timeout=900)
    r.raise_for_status()
    return r.json()


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


def build_pdf(data, top3, window_days, label, out_path):
    """Colorful PDF report."""
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

    # Summary
    summary = build_discord_summary((w_data, w_top3, w_fmt), (m_data, m_top3, m_fmt))
    print("\n--- SUMMARY ---")
    print(summary)
    print("---\n")

    # PDFs
    w_pdf = f"ig-weekly-audit-{RUN_DATE}.pdf"
    m_pdf = f"ig-monthly-audit-{RUN_DATE}.pdf"
    build_pdf(w_data, w_top3, 7, "Weekly Audit", w_pdf)
    print(f"Weekly PDF built: {w_pdf}")
    build_pdf(m_data, m_top3, 30, "Monthly Rolling Audit", m_pdf)
    print(f"Monthly PDF built: {m_pdf}")

    # Snapshots
    for label, dat in [("weekly", w_data), ("monthly", m_data)]:
        sp = f"snapshot-{label}-{RUN_DATE}.json"
        with open(sp, "w") as f:
            json.dump({"run_date": RUN_DATE, "window": label, "accounts": dat},
                      f, indent=2, default=str)
        print(f"Snapshot saved: {sp}")

    # Post both PDFs in one Discord message
    post_discord_combined(summary, [w_pdf, m_pdf])


if __name__ == "__main__":
    main()
