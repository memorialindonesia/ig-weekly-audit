"""
IG Weekly Audit — runs in GitHub Actions every Monday 09:00 WIB.

Pipeline:
1. Fetch profile + recent posts dari Apify Instagram Profile Scraper untuk 8 akun
2. Filter post yang ada di window 7 hari terakhir
3. Hitung scoreboard, top 3, format mix, ER
4. Generate PDF (colorful, mirip versi manual)
5. POST summary + PDF ke Discord webhook

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

# Account roster — edit sesuai kebutuhan
ROSTER = {
    "alazharmemorialgarden": "corporate",
    "lestari.memorialpark": "corporate",
    "alazharmemorialpark": "agent",
    "alazharpemakamanmuslim": "agent",
    "insiramemorialpark": "competitor",
    "firdausmemorialpark": "competitor",
    "baqimemorialpark.bogor": "competitor",
    "dt.memorialpark": "competitor",
}

# WIB timezone (UTC+7)
WIB = timezone(timedelta(hours=7))
NOW = datetime.now(WIB)
WINDOW_START = NOW - timedelta(days=7)
RUN_DATE = NOW.strftime("%Y-%m-%d")


def fetch_apify(usernames):
    """Call Apify Instagram Profile Scraper actor. Returns list of profile dicts."""
    url = f"https://api.apify.com/v2/acts/apify~instagram-profile-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
    payload = {
        "usernames": usernames,
        "resultsLimit": 30,  # posts per profile
    }
    r = requests.post(url, json=payload, timeout=600)
    r.raise_for_status()
    return r.json()


def classify(profile):
    """Group profile data + filter posts to 7-day window."""
    username = profile.get("username", "")
    status = ROSTER.get(username, "unknown")
    follower_count = profile.get("followersCount", 0)
    posts_total = profile.get("postsCount", 0)

    recent_posts = []
    for p in profile.get("latestPosts", []):
        ts = p.get("timestamp")
        if not ts:
            continue
        post_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(WIB)
        if post_dt < WINDOW_START:
            continue
        fmt = p.get("type", "").lower()  # "Image", "Video", "Sidecar"
        format_str = {"video": "reel", "sidecar": "carousel", "image": "static"}.get(fmt, "static")
        recent_posts.append({
            "shortcode": p.get("shortCode") or p.get("shortcode"),
            "format": format_str,
            "caption": (p.get("caption") or "")[:120],
            "likes": p.get("likesCount", 0) or 0,
            "comments": p.get("commentsCount", 0) or 0,
            "views": p.get("videoViewCount") or p.get("videoPlayCount") or 0,
            "date": post_dt.strftime("%m/%d"),
            "url": f"https://www.instagram.com/p/{p.get('shortCode') or p.get('shortcode')}/",
        })

    eng_total = sum(p["likes"] + p["comments"] for p in recent_posts)
    er = (eng_total / follower_count * 100) if follower_count else 0
    return {
        "username": username,
        "status": status,
        "followers": follower_count,
        "posts_total": posts_total,
        "posts_7d": recent_posts,
        "eng_7d": eng_total,
        "er": er,
    }


def compute_insights(data):
    """Top 3 cross-account + format winners."""
    all_posts = []
    for d in data:
        for p in d["posts_7d"]:
            all_posts.append({**p, "account": d["username"], "status": d["status"]})

    all_posts_sorted = sorted(all_posts, key=lambda p: p["likes"] + p["comments"], reverse=True)
    top3 = all_posts_sorted[:3]

    fmt_stats = defaultdict(lambda: {"n": 0, "likes_sum": 0})
    for p in all_posts:
        fmt_stats[p["format"]]["n"] += 1
        fmt_stats[p["format"]]["likes_sum"] += p["likes"]

    fmt_winners = {
        f: {"n": s["n"], "avg": s["likes_sum"] / s["n"] if s["n"] else 0}
        for f, s in fmt_stats.items()
    }
    return top3, fmt_winners, all_posts


def build_summary(data, top3, fmt_winners):
    """Discord message — markdown, ≤2000 chars."""
    by_status = defaultdict(list)
    for d in data:
        by_status[d["status"]].append(d)

    lines = [f"**📊 IG Weekly Audit — {RUN_DATE}**"]
    lines.append(f"Window: {WINDOW_START.strftime('%Y-%m-%d')} → {NOW.strftime('%Y-%m-%d')} · 2 corporate · 2 agent · 4 competitor")
    lines.append("")
    lines.append("**🎯 KEY SIGNALS**")
    sorted_data = sorted(data, key=lambda d: d["eng_7d"], reverse=True)
    for d in sorted_data[:3]:
        emoji = {"corporate": "🏢", "agent": "👥", "competitor": "🔴"}[d["status"]]
        lines.append(f"{emoji} @{d['username']} — {d['eng_7d']} eng / {len(d['posts_7d'])} posts (ER {d['er']:.2f}%)")
    dormant = [d for d in data if len(d["posts_7d"]) == 0]
    if dormant:
        lines.append(f"⚠ Dormant 7d: {', '.join('@' + d['username'] for d in dormant)}")

    lines.append("")
    lines.append("**🏆 TOP POST MINGGU INI**")
    if top3:
        t = top3[0]
        lines.append(f"@{t['account']} — {t['format'].upper()} — **{t['likes']}♥ / {t['comments']}💬**")
        lines.append(f"_{t['caption'][:140]}_")

    lines.append("")
    if fmt_winners.get("reel") and fmt_winners.get("static"):
        r_avg = fmt_winners["reel"]["avg"]
        s_avg = fmt_winners["static"]["avg"]
        ratio = r_avg / s_avg if s_avg else 0
        lines.append(f"📈 Format: Reel avg {r_avg:.1f} vs Static avg {s_avg:.1f} ({ratio:.1f}× lebih efektif)")

    lines.append("\n📎 Detail lengkap di PDF attached.")
    return "\n".join(lines)[:1900]


def build_pdf(data, top3, all_posts, out_path):
    """Colorful PDF report — same style as manual version."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    )

    NAVY = colors.HexColor("#0F172A")
    INK = colors.HexColor("#1E293B")
    MUTED = colors.HexColor("#64748B")
    HAIR = colors.HexColor("#CBD5E1")
    LIGHT_BG = colors.HexColor("#F8FAFC")
    CORP = colors.HexColor("#059669")
    AGENT = colors.HexColor("#7C3AED")
    COMP = colors.HexColor("#E11D48")
    REEL = colors.HexColor("#0891B2")
    HEAT_HOT = colors.HexColor("#DC2626")

    body = ParagraphStyle("Body", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK)
    small = ParagraphStyle("Small", fontName="Helvetica", fontSize=8, leading=11, textColor=MUTED)
    h1 = ParagraphStyle("H1", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=NAVY)
    h2 = ParagraphStyle("H2", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=NAVY,
                        spaceBefore=10, spaceAfter=4)
    tcell = ParagraphStyle("TC", fontName="Helvetica", fontSize=8, leading=10, textColor=INK)
    thdr = ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=8, leading=10,
                          textColor=colors.white)

    status_color = {"corporate": CORP, "agent": AGENT, "competitor": COMP}

    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    story = [Paragraph(f"IG Weekly Audit — {RUN_DATE}", h1)]
    story.append(Paragraph(
        f"Window {WINDOW_START.strftime('%Y-%m-%d')} → {NOW.strftime('%Y-%m-%d')} · automated", small))
    story.append(Spacer(1, 8))

    # Scoreboard
    story.append(Paragraph("Scoreboard", h2))
    rows = [["Akun", "Status", "Followers", "Posts 7d", "Eng. 7d", "ER %"]]
    sorted_data = sorted(data, key=lambda d: d["followers"], reverse=True)
    for d in sorted_data:
        c = status_color.get(d["status"], MUTED)
        rows.append([
            Paragraph(f"@{d['username']}", tcell),
            Paragraph(f"<font color='{c.hexval()}'><b>● {d['status']}</b></font>", tcell),
            Paragraph(f"{d['followers']:,}", tcell),
            Paragraph(str(len(d['posts_7d'])), tcell),
            Paragraph(str(d['eng_7d']), tcell),
            Paragraph(f"{d['er']:.2f}%", tcell),
        ])
    t = Table([[Paragraph(c, thdr) if i == 0 and isinstance(c, str) else c for c in r]
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
    story.append(Paragraph("🏆 Top 3 Posts 7d (cross-account)", h2))
    for i, p in enumerate(top3, 1):
        eng = p["likes"] + p["comments"]
        story.append(Paragraph(
            f"<b>#{i}</b> @{p['account']} · {p['format'].upper()} · "
            f"<b>{p['likes']}♥ / {p['comments']}💬</b> · {p['date']}",
            ParagraphStyle("tp", fontName="Helvetica", fontSize=9.5, leading=12,
                          textColor=HEAT_HOT if i == 1 else INK, spaceAfter=2)))
        story.append(Paragraph(p["caption"], small))
        story.append(Spacer(1, 4))

    # Per-akun detail
    story.append(Paragraph("Per-akun detail (7 hari)", h2))
    for d in sorted_data:
        if not d["posts_7d"]:
            continue
        c = status_color.get(d["status"], MUTED)
        story.append(Paragraph(
            f"<font color='{c.hexval()}'><b>@{d['username']}</b></font> · "
            f"{d['status']} · {len(d['posts_7d'])} posts · {d['eng_7d']} eng · ER {d['er']:.2f}%",
            ParagraphStyle("ah", fontName="Helvetica-Bold", fontSize=9.5, leading=12,
                          textColor=INK, spaceBefore=6, spaceAfter=3)))
        post_rows = [["Tgl", "Format", "Caption", "♥", "💬"]]
        for p in d["posts_7d"]:
            post_rows.append([
                p["date"], p["format"], p["caption"][:80],
                str(p["likes"]), str(p["comments"])
            ])
        t = Table([[Paragraph(c, thdr) if i == 0 and isinstance(c, str) else Paragraph(str(c), tcell)
                    for c in r] for i, r in enumerate(post_rows)],
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

    dormant = [d for d in data if not d["posts_7d"]]
    if dormant:
        story.append(Paragraph(
            f"<b>Dormant 7d:</b> " + ", ".join(f"@{d['username']}" for d in dormant), small))

    doc.build(story)


def post_discord(summary, pdf_path):
    payload = {"username": "IG Weekly Audit Bot", "content": summary}
    with open(pdf_path, "rb") as f:
        r = requests.post(
            DISCORD_WEBHOOK,
            data={"payload_json": json.dumps(payload)},
            files={"file1": (os.path.basename(pdf_path), f, "application/pdf")},
            timeout=60,
        )
    r.raise_for_status()
    print(f"Discord OK: status {r.status_code}")


def main():
    print(f"Run date: {RUN_DATE} WIB")
    print(f"Window: {WINDOW_START} → {NOW}")
    print(f"Fetching {len(ROSTER)} profiles from Apify...")

    raw = fetch_apify(list(ROSTER.keys()))
    print(f"Got {len(raw)} profile records")

    data = [classify(p) for p in raw if p.get("username") in ROSTER]
    top3, fmt_winners, all_posts = compute_insights(data)

    summary = build_summary(data, top3, fmt_winners)
    print("\n--- SUMMARY ---")
    print(summary)
    print("---\n")

    pdf_path = f"ig-weekly-audit-{RUN_DATE}.pdf"
    build_pdf(data, top3, all_posts, pdf_path)
    print(f"PDF built: {pdf_path}")

    # Snapshot JSON untuk historical record
    snap_path = f"snapshot-{RUN_DATE}.json"
    with open(snap_path, "w") as f:
        json.dump({"run_date": RUN_DATE, "accounts": data}, f, indent=2, default=str)
    print(f"Snapshot saved: {snap_path}")

    post_discord(summary, pdf_path)


if __name__ == "__main__":
    main()
