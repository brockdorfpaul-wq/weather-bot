"""AtmosSquall Week in Weather: a weekly long-form (landscape) video, built and posted automatically.

Segments: intro, this week's biggest stories (from video_log.csv), the Climate Prediction Center's
6-10 day outlook maps, a 7-day outlook for big cities (animated 3D bar charts), and an outro.
Every number spoken comes from the NWS forecasts; Gemini only words it, and is checked like the Shorts.

Testing on your PC:  $env:RECAP_DRY_RUN="1"  makes recap.mp4 and recap_thumb.png without uploading."""
import os, re, csv, json, math, random, subprocess, textwrap, requests
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFilter
import make_video as mv           # shares the AtmosSquall look, voices, data helpers and settings

# ---------- SETTINGS ----------
W, H = 1920, 1080
FPS = 24
SEG_PAD = 0.7                      # pause after each segment's narration (seconds)
RECAP_CITIES = [("New York", 40.71, -74.01), ("Chicago", 41.88, -87.63), ("Los Angeles", 34.05, -118.24),
                ("Houston", 29.76, -95.37), ("Phoenix", 33.45, -112.07), ("Miami", 25.76, -80.19),
                ("Denver", 39.74, -104.99), ("Seattle", 47.61, -122.33)]
OUTLOOK_MAPS = [
    ("temp", "6-10 Day Temperature Outlook",
     "https://www.cpc.ncep.noaa.gov/products/predictions/610day/610temp.new.gif"),
    ("prcp", "6-10 Day Precipitation Outlook",
     "https://www.cpc.ncep.noaa.gov/products/predictions/610day/610prcp.new.gif"),
]
# ------------------------------

mv.W, mv.H = W, H                  # the shared sky and cloud drawing reads these
mv.CLOUD_YS = [90, 230, 380, 620, 780, 900]      # landscape frame: keep clouds on screen
HDR = mv.HDR
CUR = mv.CUR
CAT_COLORS = dict(mv.CATEGORY_COLORS, storm=(255, 200, 80))


# ---------- data ----------
def parse_date(text):
    try:
        d = datetime.fromisoformat(text)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def spoken_place(location):
    """'Duluth, MN' -> 'Duluth, Minnesota' so the voice reads it naturally."""
    if ", " in location:
        city, st = location.rsplit(", ", 1)
        return f"{city}, {mv.STATE_NAMES.get(st, st)}"
    return location


def story_from_title(title, location):
    t = re.sub(r"\s*#shorts$", "", title or "").replace("UPDATE: ", "").replace(" + What to Wear", "")
    parts = [p for p in t.split(": ") if p.strip() and p.strip() != location]
    return parts[0] if parts else t


def story_score(row, story):
    """How big a story was. Newer log rows store the score; older ones are looked up from the story name."""
    try:
        return float(row["score"])
    except (KeyError, TypeError, ValueError):
        pass
    if story in mv.ALERT_STORIES:
        return mv.ALERT_STORIES[story][0]
    for level, name in mv.SPC_NAMES.items():
        if story.startswith(name + " Storm Risk"):
            return mv.SPC_SCORES[level]
    return 0


def week_stories():
    """The week's five biggest stories from the Shorts log (one per place and story), shown oldest first."""
    if not os.path.exists("video_log.csv"):
        return []
    with open("video_log.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    now = datetime.now(timezone.utc)
    best = {}
    for order, r in enumerate(rows):
        d = parse_date(r.get("date", ""))
        if not d or (now - d).days >= 7 or r.get("format") in ("showdown", "overview", "", None):
            continue
        story = r.get("story") or story_from_title(r.get("title", ""), r.get("location", ""))
        story = re.sub(r"\s+(Today|Tomorrow)$", "", story)       # "Enhanced Storm Risk Today" -> "Enhanced Storm Risk"
        key = (r.get("location"), story)
        if key in best:                                          # keep the first day it was covered
            continue
        best[key] = {"day": d.astimezone(mv.local_now().tzinfo).strftime("%A"), "location": r.get("location", ""),
                     "story": story, "category": r.get("format", ""), "score": story_score(r, story), "order": order}
    top = sorted(best.values(), key=lambda s: (s["score"], s["order"]), reverse=True)[:5]
    return sorted(top, key=lambda s: s["order"])


def on_day(name):
    """'on Monday', but 'today' and 'tonight' without 'on'."""
    n = name.lower()
    return n if n in ("today", "tonight", "this afternoon") else f"on {name}"


def short_day(name):
    n = name.lower()
    if n in ("today", "this afternoon", "tonight"):
        return "Today"
    return name[:3]


def city_week(name, lat, lon):
    p = mv.http_get(f"https://api.weather.gov/points/{lat},{lon}").json()["properties"]
    fc = mv.http_get(p["forecast"]).json()["properties"]
    days = [x for x in fc["periods"] if x.get("isDaytime")][:7]
    if len(days) < 5:
        raise ValueError("not enough forecast days")
    return {"city": name, "updated": fc.get("updateTime") or fc.get("generatedAt"),
            "days": [{"name": x["name"], "label": short_day(x["name"]), "temp": x["temperature"],
                      "pop": mv.pop(x), "kind": mv.precip_kind(x), "short": x["shortForecast"]} for x in days]}


def get_map(url, path):
    try:
        r = mv.http_get(url, timeout=60)
        if len(r.content) > 5000:
            with open(path, "wb") as f:
                f.write(r.content)
            Image.open(path).convert("RGB")      # make sure it's a real image
            return path
        print("Outlook map download looked wrong:", url)
    except Exception as e:
        print("Outlook map error:", e)
    return None


# ---------- segments: facts (for Gemini) and fallback narration ----------
def build_segments():
    today = mv.local_now()
    week_of = f"{today.strftime('%B')} {today.day}"
    segs = [{"id": "intro", "kind": "intro", "week_of": week_of,
             "facts": f"This is the AtmosSquall Week in Weather for the week of {week_of}.",
             "template": (f"Welcome to the AtmosSquall Week in Weather for the week of {week_of}. "
                          f"We'll look back at this week's biggest weather stories, then look ahead at the week to come.")}]
    stories = week_stories()
    if stories:
        facts = "Stories this channel covered this week:\n" + "\n".join(
            f"- {s['day']}: {s['story']} for {spoken_place(s['location'])}" for s in stories)
        tmpl = "Here are the stories that topped our coverage this week. " + " ".join(
            f"On {s['day']}, we tracked {mv.art(s['story'])} {s['story']} for {spoken_place(s['location'])}." for s in stories)
    else:
        facts = "It was a quiet week: no major storms or alerts topped this channel's coverage."
        tmpl = "It was a fairly quiet week, with no major storms or alerts topping our daily coverage."
    segs.append({"id": "stories", "kind": "stories", "stories": stories, "facts": facts, "template": tmpl})
    for key, title, url in OUTLOOK_MAPS:
        path = get_map(url, f"map_{key}.gif")
        if not path:
            continue
        legend = ("orange and red favor warmer than normal, blue favors colder than normal, and white or gray "
                  "means near normal" if key == "temp" else
                  "green favors wetter than normal, brown favors drier than normal, and white or gray means near normal")
        segs.append({"id": f"map_{key}", "kind": "map", "title": title, "image": path,
                     "facts": (f"The Climate Prediction Center's {title.lower().replace('6-10', 'six to ten')} map. "
                               f"How to read it: {legend}; deeper colors mean more confidence. "
                               f"Do NOT say which regions are which; only explain how to read the map."),
                     "template": (f"Here's the Climate Prediction Center's {title.lower().replace('6-10', 'six to ten')}. "
                                  f"On this map, {legend}. The deeper the color, the more confident the forecast. "
                                  f"Find your area on the map to see which way your week is leaning.")})
    updated = None
    for name, lat, lon in RECAP_CITIES:
        try:
            cw = city_week(name, lat, lon)
        except Exception as e:
            print(f"Skipping {name}:", e)
            continue
        updated = updated or cw["updated"]
        days = cw["days"]
        hi = max(days, key=lambda x: x["temp"])
        lo = min(days, key=lambda x: x["temp"])
        wet = [x for x in days if x["pop"] >= 50]
        facts = (f"{name} daytime highs for the next seven days: " +
                 ", ".join(f"{x['name']} {x['temp']} degrees ({x['short']})" for x in days) +
                 f". Warmest: {hi['name']} at {hi['temp']}. Coolest: {lo['name']} at {lo['temp']}. " +
                 ("Days with a 50 percent or higher chance of rain or snow: " +
                  ", ".join(f"{x['name']} ({x['kind'].lower()}, {x['pop']} percent)" for x in wet)
                  if wet else "No day has a 50 percent or higher chance of rain or snow."))
        tmpl = (f"In {name}, highs this week range from {lo['temp']} degrees {on_day(lo['name'])} "
                f"to {hi['temp']} {on_day(hi['name'])}. " +
                (f"The best chance of {wet[0]['kind'].lower()} comes {on_day(wet[0]['name'])}, at {wet[0]['pop']} percent."
                 if wet else "It looks mostly dry, with no day above a 50 percent chance of rain or snow."))
        segs.append({"id": f"city_{name}", "kind": "city", "city": cw, "facts": facts, "template": tmpl})
    segs.append({"id": "outro", "kind": "outro", "facts": "Closing: ask viewers to subscribe to AtmosSquall for daily forecasts.",
                 "template": ("That's your week in weather. Subscribe to AtmosSquall for daily forecasts, "
                              "and check weather.gov for the latest alerts where you live. See you next week.")})
    return segs, stories, updated


WORDS = {"intro": (25, 70), "stories": (30, 170), "map": (45, 110), "city": (35, 100), "outro": (20, 60)}


def write_narration(segs):
    """One Gemini call writes every segment; each is checked, and the built-in wording is used if a check fails."""
    for s in segs:
        s["narration"] = s["template"]
    if not os.environ.get("GEMINI_API_KEY"):
        print("No Gemini key; using built-in narration")
        return
    brief = "\n\n".join(f'id "{s["id"]}" ({WORDS[s["kind"]][0]} to {WORDS[s["kind"]][1]} words):\n{s["facts"]}'
                        for s in segs)
    prompt = f"""You are the narrator of "AtmosSquall Week in Weather", a weekly YouTube weather show.
Tone: confident, energetic news-anchor style for adults. Not childish. Short, vivid sentences.
Write narration for each segment below, using ONLY the facts given for that segment. Never invent numbers,
places, dates or events. Spell out no new numbers; only use numbers that appear in that segment's facts.
Keep each segment within its word range. Segments are read back to back, so add smooth transitions.

{brief}

Return only JSON: {{"segments": [{{"id": "...", "narration": "..."}}]}}"""
    try:
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        data, last = None, None
        for model in mv.GEMINI_MODELS:
            try:
                r = client.models.generate_content(model=model, contents=prompt,
                                                   config={"response_mime_type": "application/json"})
                data = json.loads(re.sub(r"^```(?:json)?|```$", "", r.text.strip(), flags=re.M).strip())
                break
            except Exception as e:
                last = e
                print(f"Gemini model {model} failed:", e)
        if data is None:
            raise last
    except Exception as e:
        print("Gemini error, using built-in narration:", e)
        return
    by_id = {str(x.get("id")): re.sub(r"\s+", " ", str(x.get("narration", ""))).strip() for x in data.get("segments", [])}
    for s in segs:
        text = by_id.get(s["id"], "")
        lo, hi = WORDS[s["kind"]]
        if text and lo <= len(text.split()) <= hi and mv.numbers_ok([text], s["facts"] + " " + s["template"]):
            s["narration"] = text
        else:
            print(f"Using built-in narration for {s['id']}")


# ---------- drawing ----------
def font(size):
    return mv.font(size)


def fit(d, text, max_w, size):
    return mv.fit_font(d, text, max_w, size)


def txt(d, xy, s, size, fill="white", stroke=5, max_w=1700, anchor="mm"):
    d.text(xy, s, font=fit(d, s, max_w, size), fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=(8, 12, 30))


def base_frame(t, kind="cloudy", brand=True):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    CUR["img"], CUR["flash"] = img, False
    mv.draw_sky(img, d, t, kind, brand=brand)
    return img, d


def draw_caption(d, text):
    d.rectangle([0, 930, W, H], fill=(12, 16, 38))
    d.line([(0, 930), (W, 930)], fill=mv.BRAND_CYAN, width=3)
    lines = textwrap.wrap(text, 70)[:2]
    y = 1005 - (len(lines) - 1) * 32
    for line in lines:
        d.text((W / 2, y), line, font=font(44), fill="white", anchor="mm")
        y += 64


def draw_intro(t, p, seg):
    img, d = base_frame(t, brand=True)
    s = mv.ease_out_back(t / 0.8)
    size = max(40, int(380 * (0.5 + 0.5 * s)))
    lg = mv.logo_sprite(size)
    mv.paste(mv.glow_sprite(int(size * 0.75), mv.BRAND_CYAN, 140), 520 - size * 0.75, 470 - size * 0.75)
    if lg is not None:
        mv.paste(lg, 520 - size / 2, 470 - size / 2)
    a = mv.ease((t - 0.4) / 0.6)
    dx = (1 - a) * 300
    txt(d, (1250 + dx, 400), "WEEK IN WEATHER", 120, max_w=1100)
    txt(d, (1250 + dx, 530), f"Week of {seg['week_of']}", 60, fill=mv.BRAND_CYAN, max_w=1100)
    d.line([(800 + dx, 600), (800 + dx + 900 * mv.ease((t - 0.8) / 0.8), 600)], fill=mv.BRAND_PURPLE, width=8)
    return img, d


def draw_stories(t, p, seg):
    img, d = base_frame(t, brand=True)
    mv.draw_brand(img, d, t)
    txt(d, (W / 2, 180), "THIS WEEK'S BIGGEST STORIES", 72, fill=mv.BRAND_GOLD)
    stories = seg["stories"]
    if not stories:
        txt(d, (W / 2, 520), "A quiet week across the country", 70)
        return img, d
    current = min(len(stories) - 1, int(p * len(stories)))
    for i, s in enumerate(stories):
        a = mv.ease((t - 0.3 - i * 0.35) / 0.4)
        if a <= 0:
            continue
        y = 300 + i * 120
        dx = (1 - a) * 900
        col = CAT_COLORS.get(s["category"], mv.BRAND_CYAN)
        lit = i == current
        d.rounded_rectangle([248 + dx, y - 40, 1688 + dx, y + 58], radius=26, fill=(4, 6, 18))
        d.rounded_rectangle([240 + dx, y - 48, 1680 + dx, y + 48], radius=26,
                            fill=(30, 36, 86) if lit else (18, 22, 54), outline=col, width=6 if lit else 3)
        d.rounded_rectangle([262 + dx, y - 30, 470 + dx, y + 30], radius=20, fill=col)
        d.text((366 + dx, y), s["day"][:9].upper(), font=fit(d, s["day"].upper(), 190, 34), fill=(10, 14, 34), anchor="mm")
        d.text((500 + dx, y), f"{s['story']}  \u2022  {s['location']}", font=fit(d, f"{s['story']}  \u2022  {s['location']}", 1150, 44),
               fill="white", anchor="lm")
    return img, d


def draw_map(t, p, seg):
    img, d = base_frame(t, brand=True)
    mv.draw_brand(img, d, t)
    txt(d, (W / 2, 165), seg["title"].upper(), 64, fill=mv.BRAND_GOLD)
    m = mv.cached(("map", seg["image"]), lambda: Image.open(seg["image"]).convert("RGB"))
    box_w, box_h = 1440, 700
    scale = min(box_w / m.width, box_h / m.height)
    fw, fh = int(m.width * scale), int(m.height * scale)
    z = 1.0 + 0.10 * mv.ease(p)                                # slow push-in
    cw, ch = m.width / z, m.height / z
    cx = m.width / 2 + math.sin(t * 0.2) * m.width * 0.02
    x0 = max(0, min(m.width - cw, cx - cw / 2))
    y0 = max(0, min(m.height - ch, m.height / 2 - ch / 2))
    view = m.resize((fw, fh), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))
    left, top = (W - fw) // 2, 220 + (box_h - fh) // 2
    mv.radar_frame(d, t, (left, top, left + fw, top + fh))
    img.paste(view, (left, top))
    txt(d, (W / 2, 900), "Source: NOAA Climate Prediction Center", 30, fill=(220, 226, 240), stroke=3)
    return img, d


def bar(d, x, base, w, h, color):
    """A 3D bar: front face, darker side face and a lighter top."""
    depth = 26
    d.polygon([(x + w, base - h), (x + w + depth, base - h - depth * 0.6), (x + w + depth, base - depth * 0.6), (x + w, base)],
              fill=mv.shade(color, 0.6))
    d.polygon([(x, base - h), (x + depth, base - h - depth * 0.6), (x + w + depth, base - h - depth * 0.6), (x + w, base - h)],
              fill=mv.mix(color, (255, 255, 255), 0.35))
    d.rectangle([x, base - h, x + w, base], fill=color)
    d.line([(x + 6, base - h + 6), (x + 6, base - 4)], fill=mv.mix(color, (255, 255, 255), 0.45), width=5)


def temp_color(tf):
    stops = [(0, (120, 170, 255)), (32, (90, 200, 255)), (55, (120, 220, 170)), (70, (255, 215, 90)),
             (85, (255, 150, 60)), (100, (240, 70, 60))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if tf <= t1:
            return mv.mix(c0, c1, (tf - t0) / (t1 - t0))
    return stops[-1][1]


def draw_city(t, p, seg):
    cw = seg["city"]
    days = cw["days"]
    kinds = [mv.weather_kind(x["short"]) for x in days]
    kind = max(set(kinds), key=kinds.count)
    img, d = base_frame(t, kind=kind, brand=False)
    mv.draw_brand(img, d, t)
    txt(d, (W / 2, 150), cw["city"].upper(), 92)
    txt(d, (W / 2, 235), "7-DAY OUTLOOK  \u2022  DAYTIME HIGHS", 40, fill=mv.BRAND_GOLD, stroke=4)
    temps = [x["temp"] for x in days]
    lo_t, hi_t = min(temps) - 12, max(temps) + 4
    base, max_h = 830, 400
    n = len(days)
    slot = 1500 / n
    for i, x in enumerate(days):
        g = mv.ease(t * 1.4 - i * 0.18)
        h = max(8, (x["temp"] - lo_t) / max(1, hi_t - lo_t) * max_h * g)
        bx = 210 + i * slot + slot * 0.18
        bw = slot * 0.5
        bar(d, bx, base, bw, h, temp_color(x["temp"]))
        if g > 0.05:
            d.text((bx + bw / 2, base - h - 50), f"{int(round(x['temp'] * g))}\u00b0", font=font(52), fill="white",
                   anchor="mm", stroke_width=5, stroke_fill=(8, 12, 30))
        d.text((bx + bw / 2, base + 42), x["label"], font=font(40), fill="white", anchor="mm", stroke_width=4,
               stroke_fill=(8, 12, 30))
        if x["pop"] >= 30 and g > 0.9:
            yy = base - h - 120
            snow = x["kind"] == "Snow"
            col = (235, 240, 255) if snow else (90, 170, 255)
            mv.paste(mv.sphere_sprite(24, mv.mix(col, (255, 255, 255), 0.4), mv.shade(col, 0.6)), bx + bw / 2 - 60, yy - 24)
            d.text((bx + bw / 2 + 12, yy), f"{x['pop']}%", font=font(34), fill="white", anchor="lm", stroke_width=4,
                   stroke_fill=(8, 12, 30))
    d.line([(190, base), (1730, base)], fill=(230, 235, 250), width=4)
    return img, d


def draw_outro(t, p, seg):
    img, d = base_frame(t, brand=True)
    s = mv.ease_out_back(t / 0.8)
    size = max(40, int(330 * (0.5 + 0.5 * s)))
    mv.paste(mv.glow_sprite(int(size * 0.8), mv.BRAND_PURPLE, 150), W / 2 - size * 0.8, 380 - size * 0.8)
    lg = mv.logo_sprite(size)
    if lg is not None:
        mv.paste(lg, W / 2 - size / 2, 380 - size / 2)
    txt(d, (W / 2, 640), "SUBSCRIBE FOR DAILY FORECASTS", 76)
    txt(d, (W / 2, 740), "New Shorts every morning and evening  \u2022  Week in Weather every Sunday", 40,
        fill=mv.BRAND_CYAN, stroke=4)
    return img, d


DRAW = {"intro": draw_intro, "stories": draw_stories, "map": draw_map, "city": draw_city, "outro": draw_outro}


def caption_chunks(narration, speak):
    chunks = []
    for sentence in re.split(r"(?<=[.!?])\s+", narration.strip()):
        lines = textwrap.wrap(sentence, 70)
        for i in range(0, len(lines), 2):
            chunks.append(" ".join(lines[i:i + 2]))
    weights = [max(1, len(c.split())) for c in chunks] or [1]
    out, start = [], 0.0
    for c, n in zip(chunks, weights):
        dur = speak * n / sum(weights)
        out.append((start, start + dur, c))
        start += dur
    return out


def frame(seg, t, p, caption, as_of):
    img, d = DRAW[seg["kind"]](t, p, seg)
    if seg["kind"] == "city" and as_of:
        d.text((W - 30, 905), as_of, font=font(26), fill=(228, 232, 245), anchor="rm", stroke_width=3,
               stroke_fill=(8, 12, 30))
    if seg["kind"] not in ("intro", "outro") or caption:
        draw_caption(d, caption)
    if CUR.get("flash"):
        img.paste(Image.blend(img, Image.new("RGB", (W, H), (255, 255, 255)), 0.18))
    return img


def render(segs, durations, audio, out_path, as_of):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-"]
    if audio:
        cmd += ["-i", audio]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-shortest"]
    proc = subprocess.Popen(cmd + [out_path], stdin=subprocess.PIPE)
    fade, previous = int(0.45 * FPS), None
    for i, (seg, dur) in enumerate(zip(segs, durations)):
        speak = max(0.5, dur - SEG_PAD)
        chunks = caption_chunks(seg["narration"], speak)
        img = None
        for f in range(max(1, int(round(dur * FPS)))):
            t = f / FPS
            cap = next((c for a, b, c in chunks if a <= t < b), chunks[-1][2] if chunks else "")
            img = frame(seg, t, min(1.0, t / speak), cap, as_of)
            if previous is not None and f < fade:
                img = Image.blend(previous, img, mv.ease((f + 1) / fade))
            proc.stdin.write(img.tobytes())
        previous = img
        print(f"Rendered {i + 1}/{len(segs)}: {seg['id']}")
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed while making the recap")


def narrate(segs, voice):
    def speak_all(min_level):
        durations, parts, used = [], [], set()
        for i, seg in enumerate(segs):
            mp3, wav = f"seg{i}.mp3", f"seg{i}.wav"
            used.add(mv.make_voice(seg["narration"], voice, mp3, min_level))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-af", f"apad=pad_dur={SEG_PAD}",
                            "-ar", "44100", "-ac", "2", wav], check=True)
            durations.append(mv.duration(wav))
            parts.append(wav)
        return durations, parts, used

    durations, parts, used = speak_all(0)
    if len(used) > 1:       # a backup voice kicked in partway: redo everything so the voice doesn't change mid-video
        level = max(mv.voice_level(u) for u in used)
        print("Voice changed partway; re-recording with one voice:", sorted(used))
        durations, parts, used = speak_all(level)
    with open("recap_parts.txt", "w", encoding="utf-8") as f:
        f.writelines(f"file '{p}'\n" for p in parts)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "recap_parts.txt",
                    "-c", "copy", "recap_voice.wav"], check=True)
    return durations, used


THUMB_VARIANTS = ["standard", "alt"]


def choose_thumb_variant():
    """Reuses the same win-most-of-the-time / keep-testing pattern as the Shorts' voice/hook/title tests."""
    s = mv.load_settings()
    best = (s.get("weekly_best") or {}).get("thumb_variant")
    explore = s.get("explore_rate", 0.3)
    if best in THUMB_VARIANTS and random.random() > explore:
        return best
    return random.choice(THUMB_VARIANTS)


def make_thumbnail(stories, week_of, path, variant="standard"):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    CUR["img"], CUR["flash"] = img, False
    sub = stories[-1]["story"].upper() if stories else "THE WEEK AHEAD"
    if variant == "alt":
        # Big-number, stat-card style: leads with a number instead of the "WEEK IN WEATHER" wordmark.
        mv.draw_sky(img, d, 2.0, "storm" if stories else "cloudy", brand=True)
        txt(d, (960, 150), "THIS WEEK", 64, fill=mv.BRAND_GOLD, stroke=5)
        big = str(len(stories)) if stories else "0"
        txt(d, (960, 420), big, 320, stroke=10)
        txt(d, (960, 620), ("MAJOR STORIES" if stories else "QUIET WEEK"), 70, fill=mv.BRAND_CYAN, stroke=6)
        txt(d, (960, 690), sub, 46, max_w=1700, stroke=4)
        lg = mv.logo_sprite(170)
        if lg is not None:
            mv.paste(lg, 1920 - 220, 1080 - 220)
    else:
        mv.draw_sky(img, d, 2.0, "storm" if stories else "cloudy", brand=not stories)
        lg = mv.logo_sprite(560)
        mv.paste(mv.glow_sprite(420, mv.BRAND_CYAN, 160), 430 - 420, 540 - 420)
        if lg is not None:
            mv.paste(lg, 430 - 280, 540 - 280)
        txt(d, (1300, 330), "WEEK IN", 170, max_w=1000, stroke=8)
        txt(d, (1300, 500), "WEATHER", 170, max_w=1000, stroke=8, fill=mv.BRAND_GOLD)
        txt(d, (1300, 660), sub, 70, max_w=1000, fill=mv.BRAND_CYAN, stroke=6)
        txt(d, (1300, 770), f"WEEK OF {week_of.upper()}", 56, max_w=1000, stroke=5)
    img.resize((1280, 720), Image.LANCZOS).save(path)


def upload(title, desc, path, thumb):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"], token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"])
    yt = build("youtube", "v3", credentials=creds)
    body = {"snippet": {"title": title, "description": desc, "categoryId": "25",
                        "tags": ["weather", "weather forecast", "week ahead", "AtmosSquall", "severe weather"]},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}
    res = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(path, mimetype="video/mp4", resumable=True)).execute()
    vid = res["id"]
    print("Uploaded:", vid)
    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumb, mimetype="image/png")).execute()
        print("Thumbnail set")
    except Exception as e:
        print("Could not set the thumbnail (is the channel phone-verified?):", e)
    return vid


def main():
    segs, stories, updated = build_segments()
    if len([s for s in segs if s["kind"] == "city"]) < 4:
        raise RuntimeError("Too few city forecasts loaded; skipping this week's recap instead of posting a thin video.")
    write_narration(segs)
    words = sum(len(s["narration"].split()) for s in segs)
    print(f"{len(segs)} segments, {words} words")
    voice = (mv.load_settings().get("best", {}) or {}).get("voice") or "en-US-AndrewNeural"
    durations, used = narrate(segs, voice)
    print(f"Narration: {sum(durations) / 60:.1f} minutes (voice: {', '.join(sorted(used))})")
    as_of = mv.as_of_text(updated, mv.TIMEZONE).replace("NWS data", "NWS forecasts")
    render(segs, durations, "recap_voice.wav", "recap.mp4", as_of)
    week_of = segs[0]["week_of"]
    thumb_variant = choose_thumb_variant()
    make_thumbnail(stories, week_of, "recap_thumb.png", thumb_variant)
    headline = stories[-1]["story"] if stories else "Hottest & Coldest Cities"
    title = f"Week in Weather: {headline} + the Week Ahead ({week_of})"[:100]
    story_lines = "\n".join(f"\u2022 {s['day']}: {s['story']} ({s['location']})" for s in stories) or "\u2022 A quiet week"
    desc = (f"The AtmosSquall Week in Weather for the week of {week_of}.\n\nThis week's biggest stories:\n{story_lines}\n\n"
            f"Then: the Climate Prediction Center's six-to-ten day outlook and a seven-day look at big-city highs.\n\n"
            f"Data: National Weather Service and NOAA Climate Prediction Center. Narrated with a synthetic voice. "
            f"For alerts where you live, visit weather.gov.\n\n#weather #weatherforecast #AtmosSquall")
    if os.environ.get("RECAP_DRY_RUN"):
        print("Dry run: made recap.mp4 and recap_thumb.png without uploading")
        return
    vid = upload(title, desc, "recap.mp4", "recap_thumb.png")
    fields = ["date", "video_id", "title", "thumb_variant"]
    rows = []
    if os.path.exists("weekly_log.csv"):
        with open("weekly_log.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    rows.append({"date": datetime.now(timezone.utc).isoformat(timespec="minutes"), "video_id": vid,
                "title": title, "thumb_variant": thumb_variant})
    with open("weekly_log.csv", "w", newline="", encoding="utf-8") as f:   # rewriting keeps the columns up to date
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
