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
EXPERIMENTS = ["voice", "hook", "title_style"]
# ------------------------------


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
            videos.append({**r, **stats[r["video_id"]]})

    # Compare each video with others of the same kind (a snowstorm day naturally gets more
    # views than a quiet day), so the test measures the option, not the weather.
    by_format = defaultdict(list)
    for v in videos:
        v["score"] = math.log1p(v["views"])
        by_format[v.get("format", "")].append(v["score"])
    for v in videos:
        v["relative"] = v["score"] - statistics.mean(by_format[v.get("format", "")])

    results, best = {}, dict(previous_best)
    for exp in EXPERIMENTS:
        groups = defaultdict(list)
        for v in videos:
            if exp == "hook" and v.get("script_source") != "gemini":
                continue          # the hook only applies when Gemini wrote the script
            if v.get(exp):
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


def write_report(videos, results, best, formats, total_logged):
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
    lines += ["", "## Top 5 videos", "", "| Views | Likes | Title |", "|---|---|---|"]
    for v in sorted(videos, key=lambda v: -v["views"])[:5]:
        lines.append(f"| {v['views']} | {v['likes']} | {v.get('title', '')} |")
    lines += ["", "A score above 0 means that option beat the average of similar videos. "
              "Small differences with few videos are often just luck."]
    with open("performance_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    rows = load_rows()
    try:
        with open("learned_settings.json", encoding="utf-8") as f:
            previous = json.load(f)
    except Exception:
        previous = {}
    stats = fetch_stats([r["video_id"] for r in rows]) if rows else {}
    videos, results, best, formats = analyze(rows, stats, previous.get("best", {}))
    settings = {"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                "explore_rate": EXPLORE_RATE, "best": best, "results": results,
                "videos_judged": len(videos)}
    with open("learned_settings.json", "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    write_report(videos, results, best, formats, len(rows))
    print(f"Judged {len(videos)} of {len(rows)} logged videos. Winners: {best or 'none yet'}")


if __name__ == "__main__":
    main()
