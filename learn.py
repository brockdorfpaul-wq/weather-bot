"""Weekly learning: reads view counts for logged videos, finds which options do best,
and saves the results to learned_settings.json (used by make_video.py) and performance_report.md."""
import os, csv, json, math, statistics
from collections import defaultdict
from datetime import datetime, timezone

# ---------- SETTINGS ----------
MIN_AGE_HOURS = 48      # give each video two days to collect views before judging it
MIN_SAMPLES = 5         # each option needs at least this many videos before it can win
EXPLORE_RATE = 0.25     # share of videos that keep testing the non-winning options
MIN_MARGIN = 0.10       # a winner must beat the runner-up by about 10% in views, or it's a tie
FATIGUE_MIN_SAMPLES = 8     # a category needs at least this many judged videos before fatigue applies to it
FATIGUE_SHARE = 0.40        # flagged as "overused" if it's this share of the last FATIGUE_WINDOW videos
FATIGUE_WINDOW = 20          # how many recent videos count toward that share
FATIGUE_UNDERPERFORM = -0.15   # flagged as "underperforming" at this relative score or worse
FATIGUE_PENALTY = 0.85      # the multiplier applied in make_video.py when both conditions are met
# ------------------------------

from make_video import EXPERIMENTS as OPTIONS, CATEGORY_TAGS    # shared with the video maker
EXPERIMENTS = list(OPTIONS)
STORY_CATEGORIES = list(CATEGORY_TAGS)   # winter, tropical, heat, cold, flood, wind, fire, storm


def parse_date(text):
    try:
        d = datetime.fromisoformat(text)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def load_rows():
    if not os.path.exists("video_log.csv"):
        return []
    with open("video_log.csv", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("video_id")]


def load_weekly_rows():
    if not os.path.exists("weekly_log.csv"):
        return []
    with open("weekly_log.csv", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("video_id")]


def fetch_stats(ids):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"],
                        client_secret=os.environ["YT_CLIENT_SECRET"])
    yt = build("youtube", "v3", credentials=creds)
    stats = {}
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
        for item in resp.get("items", []):
            s = item.get("statistics", {})
            stats[item["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0))}
    return stats


def analyze(rows, stats, previous_best):
    now = datetime.now(timezone.utc)
    videos = []
    for r in rows:
        d = parse_date(r.get("date", ""))
        if d and (now - d).total_seconds() >= MIN_AGE_HOURS * 3600 and r["video_id"] in stats:
            # r["date"] is written in the bot's own local time (see local_now().isoformat() in make_video.py),
            # so the parsed weekday is already the viewer-relevant local day, not UTC.
            videos.append({**r, **stats[r["video_id"]], "weekday": d.strftime("%A")})

    # Compare each video with others of the same kind (a snowstorm day naturally gets more
    # views than a quiet day, and morning, evening and big-event updates get different audiences),
    # so the test measures the option, not the weather or the time of day.
    def kind(v):
        return (v.get("format", ""), v.get("mode", ""), str(v.get("event", "")).lower() == "true")

    by_kind, by_format = defaultdict(list), defaultdict(list)
    for v in videos:
        v["score"] = math.log1p(v["views"])
        by_kind[kind(v)].append(v["score"])
        by_format[v.get("format", "")].append(v["score"])
    for v in videos:
        v["relative"] = v["score"] - statistics.mean(by_kind[kind(v)])

    results, best = {}, dict(previous_best)
    for exp in EXPERIMENTS:
        groups = defaultdict(list)
        for v in videos:
            if exp == "hook" and v.get("script_source") != "gemini":
                continue          # the hook only applies when Gemini wrote the script
            if v.get(exp) in OPTIONS[exp]:         # ignores old rows that logged a fallback voice like "gtts"
                groups[v[exp]].append(v)
        results[exp] = {opt: {"videos": len(vs),
                              "median_views": statistics.median(x["views"] for x in vs),
                              "relative_score": round(statistics.mean(x["relative"] for x in vs), 3)}
                        for opt, vs in groups.items()}
        ready = {o: r for o, r in results[exp].items() if r["videos"] >= MIN_SAMPLES}
        if len(ready) >= 2:
            ranked = sorted(ready, key=lambda o: ready[o]["relative_score"], reverse=True)
            margin = ready[ranked[0]]["relative_score"] - ready[ranked[1]]["relative_score"]
            if margin >= MIN_MARGIN:
                best[exp] = ranked[0]
            else:
                best.pop(exp, None)      # too close to call: keep testing evenly

    formats = {f: {"videos": len(s), "median_views": statistics.median(
                   v["views"] for v in videos if v.get("format", "") == f)}
               for f, s in by_format.items()}
    return videos, results, best, formats


def story_fatigue(rows, videos):
    """Flags a category as 'fatigued' only when BOTH are true: it's dominated recent scheduling
    (>= FATIGUE_SHARE of the last FATIGUE_WINDOW videos) AND it's measurably underperforming
    (relative score <= FATIGUE_UNDERPERFORM) with enough judged samples to trust that number.
    Returns (fatigue multipliers to save, a status dict per category for the report)."""
    recent = [r.get("format", "") for r in rows[-FATIGUE_WINDOW:] if r.get("format") in STORY_CATEGORIES]
    freq = {}
    for cat in STORY_CATEGORIES:
        freq[cat] = recent.count(cat) / len(recent) if recent else 0.0

    # Deliberately NOT v["relative"]: that field compares each video only against others of the
    # same category (so voice/hook tests are apples-to-apples), which nets every category to ~0.
    # Fatigue needs the opposite comparison: this category's videos against everything else.
    overall = statistics.mean(v["score"] for v in videos) if videos else 0.0
    by_cat = defaultdict(list)
    for v in videos:
        if v.get("format") in STORY_CATEGORIES:
            by_cat[v["format"]].append(v["score"])

    fatigue, status = {}, {}
    for cat in STORY_CATEGORIES:
        n = len(by_cat[cat])
        rel = (statistics.mean(by_cat[cat]) - overall) if n else None
        overused = freq[cat] >= FATIGUE_SHARE
        underperforming = n >= FATIGUE_MIN_SAMPLES and rel is not None and rel <= FATIGUE_UNDERPERFORM
        status[cat] = {"share": freq[cat], "videos": n, "relative": rel,
                       "flagged": overused and underperforming}
        if overused and underperforming:
            fatigue[cat] = FATIGUE_PENALTY
    return fatigue, status


def analyze_weekly(rows, stats):
    """Same win-most-of-the-time test as the Shorts, applied to the two thumbnail layouts."""
    from weekly_recap import THUMB_VARIANTS
    now = datetime.now(timezone.utc)
    videos = []
    for r in rows:
        d = parse_date(r.get("date", ""))
        if d and (now - d).total_seconds() >= MIN_AGE_HOURS * 3600 and r["video_id"] in stats:
            videos.append({**r, **stats[r["video_id"]]})
    groups = defaultdict(list)
    for v in videos:
        if v.get("thumb_variant") in THUMB_VARIANTS:
            groups[v["thumb_variant"]].append(math.log1p(v["views"]))
    results = {opt: {"videos": len(vs), "median_views": round(math.expm1(statistics.median(vs)))}
              for opt, vs in groups.items()}
    best = None
    ready = {o: statistics.mean(vs) for o, vs in groups.items() if len(vs) >= MIN_SAMPLES}
    if len(ready) >= 2:
        ranked = sorted(ready, key=ready.get, reverse=True)
        margin = ready[ranked[0]] - ready[ranked[1]]
        if margin >= MIN_MARGIN:
            best = ranked[0]
    return videos, results, best


def write_report(videos, results, best, formats, total_logged, fatigue_status, weekly_results, weekly_best):
    lines = ["# Weather bot performance report", "",
             f"Updated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
             f"{len(videos)} of {total_logged} logged videos are old enough to judge "
             f"(at least {MIN_AGE_HOURS} hours).", ""]
    lines += ["## Current winners", ""]
    for exp in EXPERIMENTS:
        lines.append(f"- {exp}: {best.get(exp, 'still testing (not enough videos yet)')}")
    lines += ["", "## Test results", "",
              "| Test | Option | Videos | Median views | Score vs similar videos |",
              "|---|---|---|---|---|"]
    for exp in EXPERIMENTS:
        for opt, r in sorted(results.get(exp, {}).items()):
            lines.append(f"| {exp} | {opt} | {r['videos']} | {r['median_views']:.0f} | {r['relative_score']:+.2f} |")
    lines += ["", "## Views by video type", "", "| Type | Videos | Median views |", "|---|---|---|"]
    for f, r in sorted(formats.items(), key=lambda x: -x[1]["median_views"]):
        lines.append(f"| {f or 'unknown'} | {r['videos']} | {r['median_views']:.0f} |")

    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    by_day = defaultdict(list)
    for v in videos:
        by_day[v["weekday"]].append(v["views"])
    lines += ["", "## Views by day of week", "", "Informational only; nothing is automated from this yet.", "",
              "| Day | Videos | Median views |", "|---|---|---|"]
    for day in weekdays:
        if by_day[day]:
            lines.append(f"| {day} | {len(by_day[day])} | {statistics.median(by_day[day]):.0f} |")

    lines += ["", "## Story fatigue", "",
              "A category is flagged once it's dominated recent scheduling "
              f"(\u2265{FATIGUE_SHARE:.0%} of the last {FATIGUE_WINDOW} videos) AND is measurably "
              f"underperforming (score \u2264{FATIGUE_UNDERPERFORM:+.2f} vs similar videos, "
              f"with \u2265{FATIGUE_MIN_SAMPLES} judged videos to trust that number). Flagged categories "
              f"are deprioritized ({FATIGUE_PENALTY:.0%} weight) in tomorrow's story picks, never for "
              "genuine emergencies.", "",
              "| Category | Recent share | Judged videos | Score vs similar | Flagged |",
              "|---|---|---|---|---|"]
    for cat, s in sorted(fatigue_status.items(), key=lambda x: -x[1]["share"]):
        rel = f"{s['relative']:+.2f}" if s["relative"] is not None else "\u2014"
        lines.append(f"| {cat} | {s['share']:.0%} | {s['videos']} | {rel} | {'Yes' if s['flagged'] else ''} |")

    lines += ["", "## Weekly recap thumbnail test", ""]
    if weekly_results:
        lines.append(f"Current winner: {weekly_best or 'still testing (not enough weeks yet)'}")
        lines += ["", "| Variant | Weeks | Median views |", "|---|---|---|"]
        for opt, r in sorted(weekly_results.items()):
            lines.append(f"| {opt} | {r['videos']} | {r['median_views']} |")
    else:
        lines.append("Not enough Week in Weather videos judged yet.")

    lines += ["", "## Top 5 videos", "", "| Views | Likes | Title |", "|---|---|---|"]
    for v in sorted(videos, key=lambda v: -v["views"])[:5]:
        lines.append(f"| {v['views']} | {v['likes']} | {v.get('title', '')} |")
    lines += ["", "A score above 0 means that option beat the average of similar videos. "
              "Small differences with few videos are often just luck."]
    with open("performance_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    rows = load_rows()
    weekly_rows = load_weekly_rows()
    try:
        with open("learned_settings.json", encoding="utf-8") as f:
            previous = json.load(f)
    except Exception:
        previous = {}

    all_ids = [r["video_id"] for r in rows] + [r["video_id"] for r in weekly_rows]
    stats = fetch_stats(all_ids) if all_ids else {}

    videos, results, best, formats = analyze(rows, stats, previous.get("best", {}))
    fatigue, fatigue_status = story_fatigue(rows, videos)
    weekly_videos, weekly_results, weekly_best = analyze_weekly(weekly_rows, stats)
    if weekly_best is None:
        weekly_best = (previous.get("weekly_best") or {}).get("thumb_variant")

    settings = {"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                "explore_rate": EXPLORE_RATE, "best": best, "results": results,
                "videos_judged": len(videos), "story_fatigue": fatigue,
                "weekly_best": {"thumb_variant": weekly_best} if weekly_best else {}}
    with open("learned_settings.json", "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    write_report(videos, results, best, formats, len(rows), fatigue_status, weekly_results, weekly_best)
    print(f"Judged {len(videos)} Shorts of {len(rows)} logged, {len(weekly_videos)} weekly of {len(weekly_rows)} logged.")
    print(f"Winners: {best or 'none yet'} | Fatigued categories: {list(fatigue) or 'none'} | "
          f"Thumbnail: {weekly_best or 'still testing'}")


if __name__ == "__main__":
    main()
