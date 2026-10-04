import os, re, csv, json, math, random, asyncio, subprocess, textwrap, requests
from collections import Counter
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import edge_tts
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------- SETTINGS: edit these ----------
CONTACT = os.environ.get("NWS_CONTACT_EMAIL")         # NWS/SPC ask for an identifying User-Agent; use your email
if not CONTACT:
    print("Warning: NWS_CONTACT_EMAIL is not set; using a placeholder contact. "
          "Set the secret so the NWS has a real way to reach you.")
    CONTACT = "bot@example.com"
# Things the bot tests automatically. learn.py measures which option gets more views and
# saves the winner in learned_settings.json; make_video.py then uses the winner most of the time.
EXPERIMENTS = {
    "voice": ["en-US-AriaNeural", "en-US-AndrewNeural"],
    "hook": ["question", "bold"],
    "title_style": ["standard", "swapped"],
}
TIMEZONE = "America/Chicago"
EVENING_START_HOUR = 14             # runs at or after 2 PM local time make the evening video
MIN_STORY_SCORE = 40                # how big a weather story must be to get its own location (see STORY SCORES)
EVENT_SCORE = 85                    # stories this big (hurricanes, blizzards, ice storms, High risk) get extra updates
REPEAT_HOURS = 30                   # avoid featuring the same story in the same state this soon, if another is available
EVENT_COOLDOWN_HOURS = 4            # an extra "big event" update is skipped if that story was posted this recently
# Gemini models to try in order; if one is retired or busy, the next is used. Check aistudio.google.com for names.
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
NATIONAL_RADAR_URL = "https://radar.weather.gov/ridge/standard/CONUS_loop.gif"
FALLBACK_RADAR = "KLOT"             # only used if a chosen location somehow has no radar station
# Big cities compared on quiet days (name, latitude, longitude). Add or remove freely.
BIG_CITIES = [
    ("New York", 40.71, -74.01), ("Los Angeles", 34.05, -118.24), ("Chicago", 41.88, -87.63),
    ("Houston", 29.76, -95.37), ("Phoenix", 33.45, -112.07), ("Philadelphia", 39.95, -75.17),
    ("Dallas", 32.78, -96.80), ("Miami", 25.76, -80.19), ("Atlanta", 33.75, -84.39),
    ("Boston", 42.36, -71.06), ("Seattle", 47.61, -122.33), ("Denver", 39.74, -104.99),
    ("Minneapolis", 44.98, -93.27), ("Detroit", 42.33, -83.05), ("Las Vegas", 36.17, -115.14),
    ("Salt Lake City", 40.76, -111.89),
]
# ------------------------------------------

HDR = {"User-Agent": f"(weatherbot, {CONTACT})"}
_session = None


def http_get(url, **kw):
    """GET with automatic retries (api.weather.gov often returns brief 5xx errors) and a status check."""
    global _session
    if _session is None:
        _session = requests.Session()
        retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
        _session.mount("https://", HTTPAdapter(max_retries=retry))
        _session.headers.update(HDR)
    kw.setdefault("timeout", 30)
    r = _session.get(url, **kw)
    r.raise_for_status()
    return r


W, H = 1080, 1920
CLOSING = "Follow for your daily forecast."
REGION = "United States"            # reset in main() to the chosen location
AS_OF = ""                          # "NWS data as of ..." line, set in main()
HOOK_TEXT = {"question": "Make the very first line a short question that sparks curiosity.",
             "bold": "Make the very first line a short, bold, surprising statement."}
CURRENT_HOOK = ""                    # set in main() from the chosen hook style
SCRIPT_SOURCE = "template"           # "gemini" or "template", recorded for learning

# ---------- STORY SCORES: how the biggest weather story of the day is chosen ----------
SPC_LEVELS = {"TSTM": 0, "MRGL": 1, "SLGT": 2, "ENH": 3, "MDT": 4, "HIGH": 5}
SPC_DN = {2: "TSTM", 3: "MRGL", 4: "SLGT", 5: "ENH", 6: "MDT", 8: "HIGH"}   # backup if LABEL is missing
SPC_NAMES = {1: "Marginal", 2: "Slight", 3: "Enhanced", 4: "Moderate", 5: "High"}
SPC_SCORES = {1: 30, 2: 45, 3: 60, 4: 78, 5: 95}
SPC_COLORS = {1: (120, 230, 120), 2: (255, 235, 60), 3: (255, 165, 40), 4: (255, 90, 90), 5: (255, 110, 255)}
# NWS alert type -> (score, category). Winter storms, hurricanes, extreme heat/cold, floods, wind and fire.
ALERT_STORIES = {
    "Hurricane Warning": (95, "tropical"), "Storm Surge Warning": (92, "tropical"),
    "Extreme Wind Warning": (90, "wind"), "Blizzard Warning": (90, "winter"),
    "Ice Storm Warning": (86, "winter"), "Tropical Storm Warning": (82, "tropical"),
    "Hurricane Watch": (80, "tropical"), "Winter Storm Warning": (76, "winter"),
    "Lake Effect Snow Warning": (62, "winter"), "Extreme Cold Warning": (60, "cold"),
    "Excessive Heat Warning": (60, "heat"), "Extreme Heat Warning": (60, "heat"),
    "Winter Storm Watch": (58, "winter"), "Excessive Heat Watch": (50, "heat"),
    "Extreme Heat Watch": (50, "heat"), "Flash Flood Warning": (44, "flood"),
    "High Wind Warning": (46, "wind"), "Red Flag Warning": (41, "fire"),
}
CATEGORY_COLORS = {"winter": (160, 220, 255), "tropical": (255, 130, 210), "heat": (255, 150, 70),
                   "cold": (175, 205, 255), "flood": (120, 210, 130), "wind": (255, 230, 110),
                   "fire": (255, 120, 70)}
CATEGORY_TAGS = {
    "storm": ["#severeweather", "#stormwatch"], "winter": ["#winterweather", "#snowstorm", "#snow"],
    "tropical": ["#hurricane", "#tropicalstorm"], "heat": ["#heatwave", "#extremeheat"],
    "cold": ["#coldwave", "#winterweather"], "flood": ["#flooding", "#flashflood"],
    "wind": ["#highwinds", "#windstorm"], "fire": ["#redflagwarning", "#wildfire"],
}

STORY_STYLE = ("If a storm or alert line is given, lead with it, say whether it is an outlook, watch or warning "
               "in plain words, and tell viewers to check weather.gov for warnings in their area. "
               "For snow or ice, mention expected amounts only if they appear in the facts.")
MORNING_STYLE = "First line is a hook. If there are active alerts, mention them in the first two lines. " + STORY_STYLE
EVENING_STYLE = ("This is the EVENING video about tonight and tomorrow. First line is a hook about what to wear "
                 "tomorrow. Cover: tonight's conditions, tomorrow's high and conditions, whether to expect rain "
                 "or snow, and what to wear using the clothing suggestion. If there are active alerts, mention "
                 "them in the first two lines. " + STORY_STYLE)
SHOWDOWN_STYLE = ("This is a US-wide 'big city showdown', not about one city. First line is a curiosity-sparking "
                  "question or hook. Compare the hottest and coldest big city and the size of the gap, mention "
                  "which cities have rain or snow likely, and mention the Storm Prediction Center line if given. "
                  "One line should tell viewers to check weather.gov for their own local forecast.")
OVERVIEW_STYLE = ("This is a US-wide weather overview, not about one city. First line is a hook. Mention the alert "
                  "totals, then tell viewers to check weather.gov for alerts in their area.")

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "Washington DC", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
REGIONS = {
    "northeast": {"CT", "ME", "MA", "NH", "RI", "VT", "NJ", "NY", "PA"},
    "midwest": {"IL", "IN", "MI", "OH", "WI", "IA", "MN", "MO"},
    "plains": {"KS", "NE", "ND", "SD", "OK", "TX"},
    "southeast": {"AL", "AR", "DE", "DC", "FL", "GA", "KY", "LA", "MD", "MS", "NC", "SC", "TN", "VA", "WV"},
    "west": {"AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY", "CA", "OR", "WA", "AK", "HI"},
}


# ---------- learning: pick which options to use for this video ----------
def load_settings():
    try:
        with open("learned_settings.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def choose_variants():
    """Use the best-performing option most of the time, and keep testing the others."""
    s = load_settings()
    best = s.get("best", {})
    explore = s.get("explore_rate", 0.3)
    out = {}
    for name, options in EXPERIMENTS.items():
        b = best.get(name)
        out[name] = b if (b in options and random.random() > explore) else random.choice(options)
    return out


def apply_title_style(title, style):
    """'swapped' turns 'Duluth, MN: Winter Storm Warning' into 'Winter Storm Warning: Duluth, MN'."""
    if style != "swapped":
        return title
    base = title[:-len(" #shorts")] if title.endswith(" #shorts") else title
    if ": " not in base:
        return title
    left, right = base.split(": ", 1)
    return fit_title(f"{right}: {left}")


# ---------- mode ----------
def local_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        return datetime.now()


def pick_mode():
    """VIDEO_MODE=morning/evening forces a mode (handy for testing). Otherwise use local time of day."""
    m = os.environ.get("VIDEO_MODE", "").strip().lower()
    if m in ("morning", "evening"):
        return m
    return "evening" if local_now().hour >= EVENING_START_HOUR else "morning"


def art(word):
    return "an" if word[:1].upper() in "AEIOU" else "a"


# ---------- geometry helpers (for picking a spot inside a risk area) ----------
def clean_ring(ring):
    return [(float(c[0]), float(c[1])) for c in ring]


def geom_rings(geom):
    """Outer rings of a GeoJSON Polygon or MultiPolygon."""
    geom = geom or {}
    if geom.get("type") == "MultiPolygon":
        polys = geom["coordinates"]
    elif geom.get("type") == "Polygon":
        polys = [geom["coordinates"]]
    else:
        return []
    return [clean_ring(p[0]) for p in polys if p and len(p[0]) >= 4]


def ring_stats(ring):
    """Returns (area, centroid_x, centroid_y) of a polygon ring in lon/lat degrees."""
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-12:
        return 0.0, sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
    return abs(a) / 2, cx / (3 * a), cy / (3 * a)


def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def inside_point(ring):
    """A point guaranteed to be inside the ring, as close to its center as possible."""
    _, cx, cy = ring_stats(ring)
    if point_in_ring(cx, cy, ring):
        return cx, cy
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    best = None
    for i in range(1, 25):
        for j in range(1, 25):
            x = min(xs) + (max(xs) - min(xs)) * i / 25
            y = min(ys) + (max(ys) - min(ys)) * j / 25
            if point_in_ring(x, y, ring):
                d = (x - cx) ** 2 + (y - cy) ** 2
                if best is None or d < best[0]:
                    best = (d, x, y)
    return (best[1], best[2]) if best else ring[0]


def biggest_ring_point(geom):
    rings = geom_rings(geom)
    if not rings:
        return None
    return inside_point(max(rings, key=lambda r: ring_stats(r)[0]))


# ---------- the day's weather stories ----------
def spc_story(level, day_word):
    name = SPC_NAMES[level]
    return {"kind": "spc", "category": "storm", "score": SPC_SCORES[level], "level": level,
            "label": f"SPC: {name} storm risk {day_word}",
            "title": f"{name} Storm Risk {day_word.title()}",
            "fact": (f"Storm Prediction Center severe thunderstorm risk {day_word} for this area: "
                     f"{name} (level {level} on a scale of 1 to 5)"),
            "sentence": (f"The Storm Prediction Center has {art(name)} {name} risk of severe storms here "
                         f"{day_word}. That is an outlook, not a warning."),
            "color": SPC_COLORS[level]}


def alert_story(event, category, score):
    watch = event.endswith("Watch")
    extra = " A watch means conditions are possible, not certain." if watch else ""
    return {"kind": "alert", "category": category, "score": score, "level": None,
            "label": f"NWS: {event} in effect", "title": event,
            "fact": f"The National Weather Service has {art(event)} {event} in effect for this area.{extra}",
            "sentence": f"The National Weather Service has {art(event)} {event} in effect here.{extra}",
            "color": CATEGORY_COLORS.get(category, (255, 255, 255))}


def spc_candidates(day, day_word):
    """Risk areas from the SPC categorical outlook as story candidates."""
    url = f"https://www.spc.noaa.gov/products/outlook/day{day}otlk_cat.lyr.geojson"
    r = http_get(url, timeout=60)
    out = []
    for f in r.json().get("features", []):
        props = f.get("properties") or {}
        label = str(props.get("LABEL", "")).strip().upper()
        if label not in SPC_LEVELS:
            label = SPC_DN.get(props.get("DN"), "")
        level = SPC_LEVELS.get(label, -1)
        if level < 1 or SPC_SCORES[level] < MIN_STORY_SCORE:
            continue
        for ring in geom_rings(f.get("geometry")):
            out.append({"score": SPC_SCORES[level], "size": ring_stats(ring)[0],
                        "story": spc_story(level, day_word), "point": (lambda ring=ring: inside_point(ring))})
    out.sort(key=lambda c: (c["score"], c["size"]), reverse=True)
    return out[:3]


def fetch_alerts():
    """All active NWS alerts, or None if the request failed."""
    try:
        r = http_get("https://api.weather.gov/alerts/active", params={"status": "actual"}, timeout=90)
        return r.json()["features"]
    except Exception as e:
        print("Alert download failed:", e)
        return None


def alert_point(feature):
    """A point inside an alert's area (uses its polygon, or one of its forecast zones)."""
    pt = biggest_ring_point(feature.get("geometry"))
    if pt:
        return pt
    zones = (feature.get("properties") or {}).get("affectedZones") or []
    if not zones:
        raise ValueError("alert has no area")
    r = http_get(zones[len(zones) // 2])
    pt = biggest_ring_point(r.json().get("geometry"))
    if not pt:
        raise ValueError("zone has no shape")
    return pt


def alert_candidates(features):
    """One candidate per notable alert type: the largest alert of that type."""
    best = {}
    for f in features or []:
        props = f.get("properties") or {}
        ev = props.get("event")
        if ev in ALERT_STORIES and ALERT_STORIES[ev][0] >= MIN_STORY_SCORE:
            n = len(props.get("affectedZones") or [])
            if ev not in best or n > best[ev][0]:
                best[ev] = (n, f)
    out = []
    for ev, (n, f) in best.items():
        score, cat = ALERT_STORIES[ev]
        out.append({"score": score, "size": n, "story": alert_story(ev, cat, score),
                    "point": (lambda f=f: alert_point(f))})
    return out


def locate(lat, lon):
    """Ask the NWS which city, state, forecast and radar belong to a point."""
    r = http_get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
    p = r.json()["properties"]
    rl = p["relativeLocation"]["properties"]
    return {"lat": round(lat, 4), "lon": round(lon, 4), "city": rl["city"], "state": rl["state"],
            "radar": p.get("radarStation") or FALLBACK_RADAR, "forecast": p["forecast"], "story": None,
            "tz": p.get("timeZone")}


def choose_location(day, day_word, features):
    """Returns (location, spc_checked). The location is the center of the day's biggest weather story,
    or None on quiet days (the video then becomes a US-wide showdown).
    spc_checked is True only if SPC data loaded, so we never claim 'no risk' after an error."""
    cands, spc_checked = [], False
    try:
        cands += spc_candidates(day, day_word)
        spc_checked = True
    except Exception as e:
        print("SPC error:", e)
    cands += alert_candidates(features)
    fatigue = load_settings().get("story_fatigue", {})
    for c in cands:
        # Only nudges ordinary-story tie-breaking; never touches genuine emergencies (score >= EVENT_SCORE).
        mult = fatigue.get(c["story"]["category"], 1.0) if c["score"] < EVENT_SCORE else 1.0
        c["adjusted"] = c["score"] * mult
    cands.sort(key=lambda c: (c["adjusted"], c["size"]), reverse=True)
    event_only = os.environ.get("EVENT_ONLY") == "1"
    recent = recent_stories(REPEAT_HOURS)
    cooling = recent_stories(EVENT_COOLDOWN_HOURS) if event_only else set()
    repeat = None
    for c in cands[:6]:
        try:
            lon, lat = c["point"]()
            loc = locate(lat, lon)
            loc["story"] = c["story"]
            key = (loc["state"], c["story"]["title"])      # same storm, even if the nearest city name shifts
            if key in cooling:
                print(f"{key[1]} in {key[0]} was posted in the last {EVENT_COOLDOWN_HOURS} hours; skipping this update")
                continue
            if key in recent and c["score"] < EVENT_SCORE and not event_only:
                print(f"Recently featured {key[1]} in {key[0]}; looking for a different story first")
                repeat = repeat or loc
                continue
            return loc, spc_checked
        except Exception as e:
            print(f"Could not use {c['story']['title']}:", e)
    if repeat:
        print("No other story available, so repeating", repeat["city"])
        return repeat, spc_checked
    print("No big weather story found")
    return None, spc_checked


def recent_stories(hours):
    """(state, story title) pairs featured in the last `hours` hours, from video_log.csv."""
    out = set()
    if not os.path.exists("video_log.csv"):
        return out
    now = datetime.now(timezone.utc)
    with open("video_log.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                d = datetime.fromisoformat(r.get("date", ""))
                d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            loc, story = r.get("location") or "", r.get("story") or ""
            if (now - d).total_seconds() < hours * 3600 and ", " in loc and story:
                out.add((loc.rsplit(", ", 1)[1], story))
    return out


# ---------- weather data ----------
def get_data(loc):
    fc = http_get(loc["forecast"]).json()["properties"]
    periods = fc["periods"][:4]
    loc["updated"] = fc.get("updateTime") or fc.get("generatedAt")
    alerts = http_get(f"https://api.weather.gov/alerts/active?point={loc['lat']},{loc['lon']}").json()["features"]
    return periods, [a["properties"]["event"] for a in alerts]


def station_radar_url(station):
    return f"https://radar.weather.gov/ridge/standard/{station}_loop.gif"


def get_radar(url):
    """Download a looping radar GIF. Returns True if it worked."""
    try:
        r = http_get(url, timeout=60)
        if len(r.content) > 10000 and r.content[:3] == b"GIF":
            with open("radar.gif", "wb") as f:
                f.write(r.content)
            return True
        print("Radar download looked wrong, skipping radar:", url)
    except Exception as e:
        print("Radar error, skipping radar:", e)
    return False


def pop(p):
    """Chance of precipitation (percent) for a forecast period."""
    v = (p.get("probabilityOfPrecipitation") or {}).get("value")
    return int(v) if v else 0


def wind_max(p):
    nums = [int(n) for n in re.findall(r"\d+", p.get("windSpeed", ""))]
    return max(nums) if nums else 0


def precip_kind(p):
    t = (p.get("shortForecast", "") + " " + p.get("detailedForecast", "")).lower()
    snow = any(k in t for k in ("snow", "flurr", "blizzard"))
    rain = any(k in t for k in ("rain", "shower", "thunder", "drizzle", "storm"))
    if "sleet" in t or "freezing rain" in t:
        return "Wintry mix"
    if snow and rain:
        return "Rain or snow"
    if snow:
        return "Snow"
    if rain:
        return "Rain"
    return "Rain or snow"


def detail_lines(periods, story):
    """For winter stories, include the NWS written forecast so snow/ice amounts can be reported accurately."""
    if not story or story["category"] not in ("winter", "cold"):
        return []
    return [f"Forecast detail for {p['name']}: {p.get('detailedForecast', '')}" for p in periods[:2]
            if p.get("detailedForecast")]


# ---------- local morning content ----------
def facts_text(periods, alerts, story):
    lines = []
    if alerts:
        lines.append("Active alerts (mention first): " + ", ".join(alerts))
    if story:
        lines.append(story["fact"])
    for x in periods:
        lines.append(f"{x['name']}: {x['temperature']} degrees, {x['shortForecast']}, wind {x['windSpeed']}")
    lines += detail_lines(periods, story)
    return "\n".join(lines)


def template_script(periods, alerts, story):
    a, b = periods[0], periods[1]
    out = []
    if alerts:
        out.append(f"Heads up: a {alerts[0]} is in effect for {REGION}. Check weather.gov for details.")
    out.append(f"Here's your forecast for {REGION}.")
    if story:
        out.append(story["sentence"])
    out.append(f"{a['name']}: {a['shortForecast']}, around {a['temperature']} degrees.")
    out.append(f"{b['name']}: {b['shortForecast']}, near {b['temperature']}.")
    out.append(CLOSING)
    return out


# ---------- local evening content (what to wear + rain or snow) ----------
def outfit_for(p):
    """Rule-based clothing suggestion, so it never depends on AI guesses. Returns (short, long)."""
    t = p["temperature"]
    if t < 20: base = "Heavy coat, hat, gloves"
    elif t < 32: base = "Winter coat, hat, gloves"
    elif t < 45: base = "Warm coat and layers"
    elif t < 60: base = "Light jacket or sweater"
    elif t < 75: base = "T-shirt plus a light layer"
    elif t < 85: base = "T-shirt and shorts"
    else: base = "Light, breathable clothes"
    extras = []
    if wind_max(p) >= 20:
        extras.append("a windproof layer")
    if pop(p) >= 40:
        kind = precip_kind(p)
        if kind == "Rain":
            extras.append("an umbrella or rain jacket")
        elif kind == "Snow":
            extras.append("waterproof boots")
        else:
            extras.append("waterproof shoes")
    long_ = base + (" plus " + " and ".join(extras) if extras else "")
    return base, long_


def when_word(name):
    return name.lower() if name.lower() in ("tonight", "today", "this afternoon", "overnight") else name


def evening_info(periods):
    tonight = periods[0]
    tomorrow = next((p for p in periods[1:] if p.get("isDaytime")), periods[1])
    short, long_ = outfit_for(tomorrow)
    cand = max((tonight, tomorrow), key=pop)   # whichever period has the higher precip chance
    pp = pop(cand)
    kind = precip_kind(cand)
    if pp >= 60: word = "likely"
    elif pp >= 30: word = "possible"
    else: word = "a slight chance"
    if pp >= 20:
        chip2 = f"{kind}: {pp}% ({cand['name']})"
        sentence = f"{kind} is {word}: the chance is {pp} percent {when_word(cand['name'])}."
    else:
        chip2 = "Rain or snow: unlikely"
        sentence = "Rain or snow looks unlikely."
    return {"tonight": tonight, "tomorrow": tomorrow, "outfit_short": short, "outfit_long": long_,
            "chip1": f"WEAR: {short}", "chip2": chip2, "precip_sentence": sentence}


def evening_facts(info, alerts, story):
    t, m = info["tonight"], info["tomorrow"]
    lines = []
    if alerts:
        lines.append("Active alerts (mention first): " + ", ".join(alerts))
    if story:
        lines.append(story["fact"])
    lines.append(f"{t['name']}: {t['temperature']} degrees, {t['shortForecast']}, wind {t['windSpeed']}, "
                 f"precipitation chance {pop(t)} percent")
    lines.append(f"{m['name']} (tomorrow): high {m['temperature']} degrees, {m['shortForecast']}, "
                 f"wind {m['windSpeed']}, precipitation chance {pop(m)} percent")
    lines.append(f"Clothing suggestion for tomorrow: {info['outfit_long']}")
    lines.append(f"Rain or snow outlook: {info['precip_sentence']}")
    lines += detail_lines([t, m], story)
    return "\n".join(lines)


def template_evening(info, alerts, story):
    t, m = info["tonight"], info["tomorrow"]
    out = []
    if alerts:
        out.append(f"Heads up: a {alerts[0]} is in effect for {REGION}. Check weather.gov for details.")
    out.append(f"Here's your evening outlook for {REGION}.")
    if story:
        out.append(story["sentence"])
    out.append(f"{t['name']}: {t['shortForecast']}, around {t['temperature']} degrees.")
    out.append(f"{m['name']}: {m['shortForecast']}, with a high near {m['temperature']}.")
    out.append(info["precip_sentence"])
    out.append(f"What to wear tomorrow: {info['outfit_long']}.")
    out.append(CLOSING)
    return out


# ---------- quiet days: the big-city showdown ----------
def pick_day_period(periods, mode):
    """Morning: the next daytime period. Evening: tomorrow's daytime period."""
    for p in periods:
        if p.get("isDaytime") and (mode == "morning" or p["name"].lower() not in ("today", "this afternoon")):
            return p
    return periods[0]


def get_showdown(mode):
    rows = []
    for name, lat, lon in BIG_CITIES:
        try:
            p = http_get(f"https://api.weather.gov/points/{lat},{lon}").json()["properties"]
            periods = http_get(p["forecast"]).json()["properties"]["periods"][:4]
            day = pick_day_period(periods, mode)
            rows.append({"city": name, "temp": day["temperature"], "pop": pop(day),
                         "kind": precip_kind(day), "short": day["shortForecast"]})
        except Exception as e:
            print(f"Skipping {name}:", e)
    if len(rows) < 8:
        raise RuntimeError("Not enough big-city forecasts")
    hot = max(rows, key=lambda r: r["temp"])
    cold = min(rows, key=lambda r: r["temp"])
    wet = sorted([r for r in rows if r["pop"] >= 50], key=lambda r: r["pop"], reverse=True)[:3]
    return {"hot": hot, "cold": cold, "gap": hot["temp"] - cold["temp"], "wet": wet}


def showdown_facts(sd, spc_checked, day_word):
    lines = []
    if spc_checked:
        lines.append(f"Storm Prediction Center: no significant severe thunderstorm risk areas are forecast {day_word}")
    lines.append(f"Hottest big city {day_word}: {sd['hot']['city']} at {sd['hot']['temp']} degrees")
    lines.append(f"Coldest big city {day_word}: {sd['cold']['city']} at {sd['cold']['temp']} degrees")
    lines.append(f"Temperature gap between them: {sd['gap']} degrees")
    if sd["wet"]:
        lines.append("Big cities with a 50 percent or higher chance of rain or snow: " + "; ".join(
            f"{r['city']} ({r['kind'].lower()}, {r['pop']} percent)" for r in sd["wet"]))
    else:
        lines.append("None of the big cities has a 50 percent or higher chance of rain or snow")
    return "\n".join(lines)


def showdown_template(sd, day_word):
    out = [f"Which big US city is hottest {day_word}, and which is coldest?",
           f"{sd['hot']['city']} tops the list at {sd['hot']['temp']} degrees.",
           f"{sd['cold']['city']} is the chilliest at just {sd['cold']['temp']}.",
           f"That is a {sd['gap']} degree gap across the country."]
    if sd["wet"]:
        names = " and ".join(r["city"] for r in sd["wet"][:2])
        out.append(f"Rain or snow looks most likely in {names}.")
    else:
        out.append("No big city has a high chance of rain or snow.")
    out.append("Check weather.gov for your own local forecast.")
    out.append(CLOSING)
    return out


# ---------- last resort: US-wide alert overview ----------
def national_counts(features):
    sev = [f for f in features if (f.get("properties") or {}).get("severity") in ("Severe", "Extreme")]
    counts = Counter(f["properties"]["event"] for f in sev)
    return len(sev), counts.most_common(3)


def overview_facts(total, top):
    if total == 0:
        return "No severe or extreme weather alerts are active anywhere in the US right now."
    lines = [f"Severe or extreme weather alerts active across the US right now: {total}"]
    lines += [f"{ev}: {n} active" for ev, n in top]
    return "\n".join(lines)


def overview_template(total, top):
    out = ["Here's your national weather snapshot."]
    if total == 0:
        out.append("There are no severe weather alerts active anywhere in the country right now.")
    else:
        out.append(f"Right now there are {total} severe weather alerts active across the country.")
        out += [f"{ev}: {n} active." for ev, n in top[:2]]
    out.append("Check weather.gov for alerts in your area.")
    out.append(CLOSING)
    return out


def overview_colors(event):
    return weather_kind(event)


# ---------- titles and hashtags ----------
def slug(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def make_hashtags(city, st, story):
    """Local hashtags first (YouTube shows the first three above the title), then topic tags, then general."""
    state_name = STATE_NAMES.get(st, st)
    tags = [f"#{slug(city)}weather", f"#{slug(state_name)}weather", f"#{st.lower()}wx"]
    for region, states in REGIONS.items():
        if st in states:
            tags.append(f"#{region}weather")
    if story:
        tags += CATEGORY_TAGS.get(story["category"], [])
    tags += ["#weather", "#forecast", "#shorts"]
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:12]


def fit_title(base):
    return (base if len(base) <= 84 else base[:83].rstrip() + "…") + " #shorts"


def make_title(mode, loc, story, periods):
    where = f"{loc['city']}, {loc['state']}"
    if story:
        base = f"{where}: {story['title']}" + (" + What to Wear" if mode == "evening" else "")
    elif mode == "evening":
        base = f"{where} Tomorrow: What to Wear + Rain or Snow?"
    else:
        base = f"{where} {periods[0]['name']} Forecast"
    return fit_title(base)


def showdown_title(sd, day_word):
    h, c = sd["hot"], sd["cold"]
    full = f"Hottest vs Coldest Big City {day_word.title()}: {h['city']} {h['temp']}\u00b0 vs {c['city']} {c['temp']}\u00b0"
    return fit_title(full if len(full) <= 84 else f"Hottest vs Coldest Big City {day_word.title()}")


def overview_title(total):
    today = local_now().strftime("%b %d")
    return fit_title(f"US Weather {today}: {total} Severe Alerts Active" if total
                     else f"US Weather {today}: Quiet Skies Nationwide")


# ---------- script writing ----------
def voice_level(name):
    """0 = a Microsoft edge-tts voice, 1 = gTTS."""
    return {"gtts": 1}.get(name, 0)


def make_voice(text, voice, path="voice.mp3", min_level=0):
    """Microsoft's edge-tts first; if it fails, Google's gTTS. No further fallback: an offline
    robotic voice would hurt watch time more than skipping a video, so this fails loudly instead,
    which fails the GitHub Actions run and emails you, rather than posting low-quality audio.
    min_level skips the better engine (used to keep one voice for a whole video).
    Returns the name of the voice that worked."""
    if min_level < 1:
        try:
            asyncio.run(edge_tts.Communicate(text, voice).save(path))
            if os.path.getsize(path) > 1000:
                return voice
        except Exception as e:
            print("edge-tts failed, trying a backup voice:", e)
    try:
        from gtts import gTTS
        gTTS(text, lang="en", tld="us").save(path)
        return "gtts"
    except Exception as e:
        print("gTTS failed:", e)
    raise RuntimeError("Both edge-tts and gTTS failed to generate audio; not posting a low-quality voice.")


def as_of_text(iso=None, tz=None):
    """'NWS data as of 7:02 AM CDT Oct 3', in the featured place's own time zone."""
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz or TIMEZONE)
        when = datetime.fromisoformat(iso).astimezone(zone) if iso else datetime.now(zone)
    except Exception:
        when = local_now()
    clock = when.strftime("%I:%M %p").lstrip("0")
    return f"NWS data as of {clock} {when.strftime('%Z')} {when.strftime('%b')} {when.day}  \u2022  weather.gov for the latest"


def gemini_script(facts, style):
    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = f"""Write a 30-second voiceover (about 70 words) for a weather short about {REGION}.
{style}
Friendly, energetic. One sentence per line, 5 to 7 lines, no emojis, no numbering.
Last line is exactly: {CLOSING}
Use ONLY these facts, add no other numbers or claims:
{facts}"""
    last = None
    for model in GEMINI_MODELS:              # if one model is retired or busy, try the next
        try:
            r = client.models.generate_content(model=model, contents=prompt)
            return [l.strip() for l in r.text.splitlines() if l.strip()]
        except Exception as e:
            last = e
            print(f"Gemini model {model} failed:", e)
    raise last


def numbers_ok(lines, facts):
    allowed = set(re.findall(r"\d+", facts))
    return all(n in allowed for n in re.findall(r"\d+", " ".join(lines)))


def make_script(facts, fallback, style):
    global SCRIPT_SOURCE
    SCRIPT_SOURCE = "template"
    try:
        lines = gemini_script(facts, style + " " + CURRENT_HOOK)
        if 4 <= len(lines) <= 9 and numbers_ok(lines, facts):
            SCRIPT_SOURCE = "gemini"
            return lines
        print("Gemini script failed checks, using template")
    except Exception as e:
        print("Gemini error, using template:", e)
    return fallback


# ---------- drawing: animated, branded AtmosSquall style ----------
FPS = 30
BRAND_NAVY = (14, 22, 52)
BRAND_CYAN = (70, 200, 255)
BRAND_BLUE = (45, 120, 225)
BRAND_PURPLE = (125, 70, 185)
BRAND_GOLD = (255, 215, 120)
LOGO_PATH = "logo.png"


def weather_kind(text):
    """Turns forecast or alert wording into one of a few animated sky styles."""
    t = (text or "").lower()
    if any(k in t for k in ("thunder", "tornado", "storm", "hurricane", "severe", "tropical")):
        return "storm"
    if any(k in t for k in ("snow", "flurr", "blizzard", "sleet", "ice", "freez", "winter", "cold", "frost")):
        return "snow"
    if any(k in t for k in ("rain", "shower", "drizzle", "flood")):
        return "rain"
    if any(k in t for k in ("fog", "haze", "smoke", "mist")):
        return "fog"
    if any(k in t for k in ("sunny", "clear", "heat", "fire", "red flag", "hot")):
        return "clear"
    return "cloudy"


def colors_for(text):
    return weather_kind(text)


def lerp(a, b, t):
    return a + (b - a) * t


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def ease_out_back(t):
    t = max(0.0, min(1.0, t))
    c = 1.7
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def mix(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(3))


def shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c)


FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",                             # Windows
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",        # Mac
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",     # Linux / GitHub Actions
]
_font_cache, _sprite_cache, _bg_cache = {}, {}, {}
CUR = {"img": None}


def font(size):
    if size not in _font_cache:
        f = None
        for p in FONT_CANDIDATES:
            if os.path.exists(p):
                f = ImageFont.truetype(p, size)
                break
        _font_cache[size] = f or ImageFont.load_default()
    return _font_cache[size]


def fit_font(d, text, max_w, size):
    while size > 24 and d.textlength(text, font=font(size)) > max_w:
        size -= 2
    return font(size)


def cached(key, make):
    if key not in _sprite_cache:
        if len(_sprite_cache) > 500:
            _sprite_cache.clear()
        _sprite_cache[key] = make()
    return _sprite_cache[key]


def paste(sprite, x, y):
    if sprite is not None:
        CUR["img"].paste(sprite, (int(x), int(y)), sprite)


def gradient_bg(top, bottom):
    key = (top, bottom)
    if key not in _bg_cache:
        img = Image.new("RGB", (W, H))
        d = ImageDraw.Draw(img)
        for y in range(H):
            d.line([(0, y), (W, y)], fill=mix(top, bottom, y / H))
        _bg_cache[key] = img
    return _bg_cache[key].copy()


def sphere_sprite(r, light, dark):
    r = max(3, int(r))

    def make():
        size = 2 * r
        g = Image.radial_gradient("L").resize((int(size * 1.6), int(size * 1.6)))
        x0, y0 = int(size * 0.45), int(size * 0.5)
        g = g.crop((x0, y0, x0 + size, y0 + size))
        col = Image.composite(Image.new("RGB", (size, size), dark), Image.new("RGB", (size, size), light), g)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse([1, 1, size - 2, size - 2], fill=255)
        out = col.convert("RGBA")
        out.putalpha(mask.filter(ImageFilter.GaussianBlur(1.2)))
        return out
    return cached(("sphere", r, light, dark), make)


def glow_sprite(r, color, strength=170):
    r = max(4, int(r))

    def make():
        g = Image.radial_gradient("L").resize((2 * r, 2 * r))
        out = Image.new("RGBA", (2 * r, 2 * r), color + (0,))
        out.putalpha(g.point(lambda v: int(strength * max(0.0, 1 - v / 255) ** 2)))
        return out
    return cached(("glow", r, color, strength), make)


def soft_band(w, h, color, alpha, blur):
    def make():
        pad = int(blur * 3)
        m = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
        ImageDraw.Draw(m).ellipse([pad, pad, pad + w, pad + h], fill=alpha)
        out = Image.new("RGBA", m.size, color + (0,))
        out.putalpha(m.filter(ImageFilter.GaussianBlur(blur)))
        return out
    return cached(("band", w, h, color, alpha, blur), make)


PUFFS = [(5, 30, 58), (-70, 18, 50), (85, 18, 45), (40, -12, 60), (-25, -25, 65)]


def cloud_sprite(s, fill):
    s = max(0.05, round(s * 20) / 20)

    def make():
        dark = shade(fill, 0.64)
        w, h = int(360 * s) + 24, int(230 * s) + 24
        canvas = Image.new("RGBA", (w, h), fill + (0,))
        ox, oy = w / 2, h / 2 + 10 * s
        for dx, dy, r in PUFFS:
            sp = sphere_sprite(r * s, fill, dark)
            canvas.alpha_composite(sp, (int(ox + dx * s - sp.width / 2), int(oy + dy * s - sp.height / 2)))
        return canvas.filter(ImageFilter.GaussianBlur(max(0.6, 1.6 * s)))
    return cached(("cloud", s, fill), make)


def cloud(x, y, s, fill):
    sp = cloud_sprite(s, fill)
    paste(sp, x - sp.width / 2, y - sp.height / 2)


def logo_sprite(size):
    def make():
        try:
            return Image.open(LOGO_PATH).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception:
            return None
    return cached(("logo", size), make)


# ---------- animated skies ----------
DAY_SKIES = {"clear": ((60, 140, 230), (185, 222, 250)), "cloudy": ((100, 130, 172), (196, 208, 226)),
             "rain": ((52, 68, 100), (132, 146, 170)), "storm": ((24, 26, 56), (88, 70, 128)),
             "snow": ((130, 158, 198), (226, 233, 244)), "fog": ((140, 150, 166), (212, 217, 224))}
NIGHT_SKIES = {"clear": ((6, 12, 36), (38, 50, 98)), "cloudy": ((16, 22, 46), (60, 66, 98)),
               "rain": ((12, 16, 34), (50, 56, 82)), "storm": ((10, 8, 28), (62, 40, 100)),
               "snow": ((26, 36, 66), (92, 104, 138)), "fog": ((26, 30, 46), (80, 86, 102))}
BRAND_SKY = ((10, 16, 44), (74, 40, 122))
CLOUD_SETUPS = {"clear": 2, "cloudy": 6, "rain": 7, "storm": 7, "snow": 6, "fog": 4, "brand": 4}
CLOUD_YS = [150, 330, 520, 640, 1520, 1700, 1820]    # vertical spots for clouds; weekly_recap.py swaps in landscape ones


def draw_sky(img, d, t, kind, night=False, brand=False):
    if brand:
        img.paste(gradient_bg(*BRAND_SKY))
    else:
        img.paste(gradient_bg(*(NIGHT_SKIES if night else DAY_SKIES)[kind]))
    rnd = random.Random(42)
    if night or brand:
        for _ in range(90):
            x, y, z = rnd.randint(0, W), rnd.randint(0, min(1200, H)), rnd.random()
            r = 1 + 2.2 * z * (0.6 + 0.4 * math.sin(t * 3 + x))
            d.ellipse([x - r, y - r, x + r, y + r], fill=(225, 230, 255))
    if kind == "clear" and not brand:
        cx, cy = (860, 300) if not night else (880, 280)
        g = glow_sprite(330, (255, 225, 140) if not night else (220, 225, 255), 150 if not night else 90)
        paste(g, cx - 330, cy - 330)
        if not night:
            for k in range(12):
                a = k * math.pi / 6 + t * 0.35
                d.line([(cx + math.cos(a) * 125, cy + math.sin(a) * 125),
                        (cx + math.cos(a) * 175, cy + math.sin(a) * 175)], fill=(255, 210, 70), width=10)
            paste(sphere_sprite(105, (255, 246, 175), (250, 165, 30)), cx - 105, cy - 105)
        else:
            paste(sphere_sprite(80, (250, 250, 238), (175, 178, 165)), cx - 80, cy - 80)
    n = CLOUD_SETUPS["brand" if brand else kind]
    if brand:
        fill = (70, 64, 128)
    elif night:
        fill = {"storm": (70, 66, 100), "rain": (82, 88, 112)}.get(kind, (104, 112, 140))
    else:
        fill = {"storm": (112, 112, 140), "rain": (150, 158, 176), "snow": (226, 232, 242),
                "fog": (205, 210, 218)}.get(kind, (246, 248, 252))
    for k in range(n):
        depth = rnd.uniform(0.6, 1.4)
        y = rnd.choice(CLOUD_YS) + rnd.uniform(-50, 50)
        speed = 22 * depth
        x = (rnd.uniform(0, W + 600) + t * speed) % (W + 600) - 300
        cloud(x, y, 1.15 * depth, shade(fill, lerp(0.92, 1.05, depth - 0.6)))
    if kind in ("rain", "storm") and not brand:
        for _ in range(170 if kind == "storm" else 120):
            x, off, z = rnd.uniform(-100, W), rnd.random(), rnd.uniform(0.35, 1.0)
            y = ((off + t * (0.8 + 0.9 * z)) % 1) * H
            ln = 26 + 44 * z
            d.line([(x, y), (x - 0.25 * ln, y + ln)], fill=mix((120, 135, 165), (195, 215, 245), z), width=int(2 + 3 * z))
    if kind == "snow" and not brand:
        for _ in range(140):
            x, off, z = rnd.uniform(0, W), rnd.random(), rnd.uniform(0.3, 1.0)
            y = ((off + t * (0.07 + 0.12 * z)) % 1) * H
            x += math.sin(t * 1.8 + off * 9) * 18 * z
            r = 3 + 7 * z
            d.ellipse([x - r, y - r, x + r, y + r], fill=mix((205, 214, 232), (255, 255, 255), z))
    if kind == "fog" and not brand:
        for k in range(5):
            band = soft_band(1500, 130, (225, 228, 235), 150, 22)
            x = ((k * 420 + t * (20 + 6 * k)) % (W + 1500)) - 1500
            for y in (520 + k * 40, 1500 + k * 50):
                paste(band, x, y)
    if kind == "storm" and not brand and (t + 0.6) % 3.4 < 0.16:
        bx = 180 + int(t * 37) % max(1, W - 380)
        pts, x, y = [], bx, 0
        r2 = random.Random(int(t * 3))
        while y < 620:
            pts.append((x, y))
            x += r2.randint(-60, 60)
            y += 55
        paste(glow_sprite(260, (230, 220, 255), 160), bx - 260, 60)
        d.line(pts, fill=(235, 225, 255), width=14)
        d.line(pts, fill=(255, 255, 255), width=5)
        CUR["flash"] = True


def finish_frame(img):
    if AS_OF:
        ImageDraw.Draw(img).text((W / 2, 1510), AS_OF, font=fit_font(ImageDraw.Draw(img), AS_OF, 1000, 30),
                                 fill=(228, 232, 245), anchor="mm", stroke_width=3, stroke_fill=(8, 12, 30))
    if CUR.get("flash"):
        img.paste(Image.blend(img, Image.new("RGB", (W, H), (255, 255, 255)), 0.22))
        CUR["flash"] = False


# ---------- branded overlay pieces ----------
def draw_brand(img, d, t):
    """Logo and channel name across the top, with a sliding accent line."""
    lg = logo_sprite(104)
    x = 30
    if lg is not None:
        paste(glow_sprite(80, BRAND_CYAN, 110), 26 + 52 - 80, 14 + 52 - 80)
        paste(lg, 26, 14)
        x = 146
    d.text((x, 66), "AtmosSquall", font=font(46), fill="white", anchor="lm", stroke_width=4, stroke_fill=BRAND_NAVY)
    w = d.textlength("AtmosSquall", font=font(46))
    s = ease(t / 0.8)
    d.line([(x, 98), (x + w * s, 98)], fill=mix(BRAND_CYAN, BRAND_PURPLE, (math.sin(t * 1.5) + 1) / 2), width=5)


def text(d, xy, s, size, fill="white", stroke=5, max_w=980, anchor="mm"):
    d.text(xy, s, font=fit_font(d, s, max_w, size), fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=(8, 12, 30))


def count_up(big, t):
    """Big numbers count up from zero during the first second; '72°' becomes 0°, 14°, ... 72°."""
    m = re.match(r"^(-?\d+)(.*)$", big)
    if not m:
        return big
    target, rest = int(m.group(1)), m.group(2)
    return f"{int(round(target * ease(t / 0.9)))}{rest}"


def big_number(d, x, y, big, t, size):
    s = ease_out_back(t / 0.6)
    size = max(24, int(size * (0.55 + 0.45 * s)))
    d.text((x, y), count_up(big, t), font=font(size), fill="white", anchor="mm", stroke_width=7, stroke_fill=(8, 12, 30))


def radar_frame(d, t, box):
    x0, y0, x1, y1 = box
    glow = mix(BRAND_CYAN, BRAND_PURPLE, (math.sin(t * 1.6) + 1) / 2)
    d.rounded_rectangle([x0 - 10, y0 - 10, x1 + 10, y1 + 10], radius=40, fill=shade(glow, 0.45))
    d.rounded_rectangle([x0 - 5, y0 - 5, x1 + 5, y1 + 5], radius=36, fill=glow)
    d.rounded_rectangle([x0, y0, x1, y1], radius=30, fill=(10, 12, 26))


def chips(d, t, items, top, step, height):
    for i, (label, outline) in enumerate(items):
        a = ease((t - 0.6 - i * 0.35) / 0.4)
        if a <= 0:
            continue
        y0 = top + i * step
        dx = (1 - a) * 700
        d.rounded_rectangle([98 + dx, y0 + 8, 998 + dx, y0 + height + 8], radius=24, fill=(4, 6, 16))
        d.rounded_rectangle([90 + dx, y0, 990 + dx, y0 + height], radius=24, fill=(16, 18, 44), outline=outline, width=5)
        d.text((W / 2 + dx, y0 + height / 2), label, font=fit_font(d, label, 840, 46), fill="white", anchor="mm")


def draw_caption(d, caption, ct):
    """Captions pop up line by line as they're spoken."""
    a = ease(ct / 0.25)
    for width, size, gap, most in ((24, 66, 92, 3), (28, 58, 80, 4), (33, 50, 68, 5)):
        lines = textwrap.wrap(caption, width)
        if len(lines) <= most:
            break
    y = 1590 + (1 - a) * 40
    for line in lines[:most]:
        d.text((W / 2, y), line, font=font(size), fill="white" if a > 0.5 else (200, 205, 220), anchor="mm",
               stroke_width=6, stroke_fill=(8, 12, 30))
        y += gap


# ---------- the three video looks ----------
def make_frame(t, ct, big, sub, caption, kind, has_radar, label, label_color, night=False):
    """Morning / overview look: location, story label, big number, radar below."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    CUR["img"], CUR["flash"] = img, False
    draw_sky(img, d, t, kind, night=night)
    draw_brand(img, d, t)
    text(d, (W / 2, 175), REGION, 64)
    if label:
        pulse = 0.5 + 0.5 * math.sin(t * 3)
        lw = d.textlength(label, font=fit_font(d, label, 980, 44))
        paste(glow_sprite(int(lw / 2 + 60), label_color, int(70 + 60 * pulse)), W / 2 - (lw / 2 + 60), 250 - (lw / 2 + 60))
        text(d, (W / 2, 250), label, 44, fill=label_color, stroke=4)
    big_number(d, W / 2, 405, big, t, 200)
    text(d, (W / 2, 560), sub, 54)
    if has_radar:
        radar_frame(d, t, (90, 620, 990, 1470))
    draw_caption(d, caption, ct)
    finish_frame(img)
    return img


def make_frame_evening(t, ct, info, caption, has_radar, label, label_color):
    """Evening look: night sky, story label, tomorrow's high, two info chips, smaller radar."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    CUR["img"], CUR["flash"] = img, False
    m = info["tomorrow"]
    draw_sky(img, d, t, weather_kind(m["shortForecast"]), night=True)
    draw_brand(img, d, t)
    text(d, (W / 2, 160), REGION, 58)
    if label:
        text(d, (W / 2, 220), label, 40, fill=label_color, stroke=4)
    text(d, (W / 2, 272), "TOMORROW'S HIGH", 38, fill=BRAND_GOLD, stroke=3)
    big_number(d, W / 2, 400, f"{m['temperature']}\u00b0", t, 175)
    text(d, (W / 2, 535), textwrap.shorten(m["shortForecast"], 34, placeholder="..."), 48)
    chips(d, t, [(info["chip1"], BRAND_GOLD), (info["chip2"], BRAND_CYAN)], 620, 100, 82)
    if has_radar:
        radar_frame(d, t, (90, 830, 990, 1470))
    draw_caption(d, caption, ct)
    finish_frame(img)
    return img


def make_frame_showdown(t, ct, sd, caption, has_radar, label, label_color, day_word):
    """Quiet-day look: brand-colored sky, hottest vs coldest big city, rain/snow cities, national radar."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    CUR["img"], CUR["flash"] = img, False
    draw_sky(img, d, t, "cloudy", brand=True)
    draw_brand(img, d, t)
    h, c = sd["hot"], sd["cold"]
    text(d, (W / 2, 160), "United States", 58)
    if label:
        text(d, (W / 2, 220), label, 40, fill=label_color, stroke=4)
    text(d, (W / 2, 272), f"HOTTEST VS COLDEST {day_word.upper()}", 38, fill=BRAND_GOLD, stroke=3)
    big_number(d, W / 2, 400, f"{sd['gap']}\u00b0", t, 175)
    text(d, (W / 2, 535), f"{h['city']} vs {c['city']}", 48)
    if sd["wet"]:
        wet = "RAIN/SNOW: " + ", ".join(f"{r['city']} {r['pop']}%" for r in sd["wet"][:2])
    else:
        wet = "DRY: no big-city rain or snow likely"
    chips(d, t, [(f"HOTTEST: {h['city']} {h['temp']}\u00b0", (255, 150, 60)),
                 (f"COLDEST: {c['city']} {c['temp']}\u00b0", BRAND_CYAN),
                 (wet, (170, 255, 170))], 620, 95, 80)
    if has_radar:
        radar_frame(d, t, (90, 915, 990, 1470))
    draw_caption(d, caption, ct)
    finish_frame(img)
    return img


def render_video(frame_at, seconds, out_path):
    """Draws every frame and streams it straight into FFmpeg."""
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                             "-crf", "20", "-pix_fmt", "yuv420p", out_path], stdin=subprocess.PIPE)
    for f in range(max(1, int(round(seconds * FPS)))):
        proc.stdin.write(frame_at(f / FPS).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed while drawing the video")


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(r.stdout.strip())


# ---------- main ----------
def main():
    global REGION, CURRENT_HOOK, AS_OF
    variants = choose_variants()
    CURRENT_HOOK = HOOK_TEXT[variants["hook"]]
    print("Testing:", variants)
    mode = pick_mode()
    day = 2 if mode == "evening" else 1            # evening video uses tomorrow's (Day 2) SPC outlook
    day_word = "tomorrow" if mode == "evening" else "today"
    print("Mode:", mode)

    features = fetch_alerts()
    loc, spc_checked = choose_location(day, day_word, features)
    event_only = os.environ.get("EVENT_ONLY") == "1"
    if event_only and (not loc or loc["story"]["score"] < EVENT_SCORE):
        print("Big-event check: nothing big enough right now, so no extra video.")
        return
    # Marginal risks are too small to feature, so only claim "no significant" risk, never "none".
    spc_label = f"SPC: no significant storm risk {day_word}" if spc_checked else ""
    green = (150, 235, 150)

    if loc:
        # ---- a local story: storms, winter storm, hurricane, heat, flood, etc. ----
        REGION = f"{loc['city']}, {loc['state']}"
        story = loc["story"]
        if event_only:
            story = dict(story, label="LIVE UPDATE \u2022 " + story["label"])
        print("Location:", REGION, f"({loc['lat']}, {loc['lon']})", "radar", loc["radar"], "|", story["title"])
        periods, alerts = get_data(loc)
        AS_OF = as_of_text(loc.get("updated"), loc.get("tz"))
        has_radar = get_radar(station_radar_url(loc["radar"]))
        if mode == "evening":
            info = evening_info(periods)
            facts = evening_facts(info, alerts, story)
            lines = make_script(facts, template_evening(info, alerts, story), EVENING_STYLE)
            draw = lambda t, ct, cap: make_frame_evening(t, ct, info, cap, has_radar, story["label"], story["color"])
            radar_filter = ("[1:v]scale=680:600:force_original_aspect_ratio=decrease[r];"
                            "[0:v][r]overlay=(W-w)/2:1150-h/2:shortest=1,format=yuv420p[v]")
        else:
            facts = facts_text(periods, alerts, story)
            lines = make_script(facts, template_script(periods, alerts, story), MORNING_STYLE)
            cols = colors_for(periods[0]["shortForecast"])
            big = f"{periods[0]['temperature']}\u00b0"
            draw = lambda t, ct, cap: make_frame(t, ct, big, periods[0]["shortForecast"], cap, cols,
                                                has_radar, story["label"], story["color"])
            radar_filter = ("[1:v]scale=860:-2[r];[0:v][r]overlay=(W-w)/2:645:shortest=1,format=yuv420p[v]")
        title = make_title(mode, loc, story, periods)
        tags = " ".join(make_hashtags(loc["city"], loc["state"], story))
        fmt = story["category"]
    else:
        REGION = "United States"
        AS_OF = as_of_text()
        has_radar = get_radar(NATIONAL_RADAR_URL)
        try:
            # ---- quiet day: big-city showdown ----
            sd = get_showdown(mode)
            print("Showdown:", sd["hot"]["city"], sd["hot"]["temp"], "vs", sd["cold"]["city"], sd["cold"]["temp"])
            facts = showdown_facts(sd, spc_checked, day_word)
            lines = make_script(facts, showdown_template(sd, day_word), SHOWDOWN_STYLE)
            draw = lambda t, ct, cap: make_frame_showdown(t, ct, sd, cap, has_radar, spc_label, green, day_word)
            radar_filter = ("[1:v]scale=860:520:force_original_aspect_ratio=decrease[r];"
                            "[0:v][r]overlay=(W-w)/2:1192-h/2:shortest=1,format=yuv420p[v]")
            title = showdown_title(sd, day_word)
            tags = "#usweather #nationalweather #weather #forecast #shorts"
            fmt = "showdown"
        except Exception as e:
            # ---- last resort: US-wide alert overview ----
            print("Showdown failed, making an alert overview instead:", e)
            if features is None:
                raise RuntimeError("No weather data could be loaded; skipping today's video.")
            total, top = national_counts(features)
            facts = overview_facts(total, top)
            lines = make_script(facts, overview_template(total, top), OVERVIEW_STYLE)
            cols = overview_colors(top[0][0] if top else "")
            big = str(total) if total else "Calm"
            sub = "severe alerts active nationwide" if total else "no severe alerts nationwide"
            draw = lambda t, ct, cap: make_frame(t, ct, big, sub, cap, cols, has_radar, spc_label, green)
            radar_filter = ("[1:v]scale=860:-2[r];[0:v][r]overlay=(W-w)/2:1045-h/2:shortest=1,format=yuv420p[v]")
            title = overview_title(total)
            tags = "#usweather #nationalweather #weather #forecast #shorts"
            fmt = "overview"

    print("\n".join(lines))
    voice_used = make_voice(" ".join(lines), variants["voice"])
    clip_len = duration("voice.mp3")
    words = [len(l.split()) for l in lines]

    # Step 1: animated sky, branding, numbers and captions (silent)
    starts, acc = [], 0.0
    for n in words:
        starts.append(acc)
        acc += clip_len * n / sum(words)

    def frame_at(t):
        i = max(k for k in range(len(lines)) if starts[k] <= t + 1e-6)
        return draw(t, t - starts[i], lines[i])
    render_video(frame_at, clip_len, "bg.mp4")

    # Step 2: add radar loop (if available) and the voiceover
    if has_radar:
        subprocess.run(["ffmpeg", "-y", "-i", "bg.mp4", "-stream_loop", "-1", "-i", "radar.gif",
                        "-i", "voice.mp3", "-filter_complex", radar_filter,
                        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac",
                        "-shortest", "out.mp4"], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-i", "bg.mp4", "-i", "voice.mp3", "-c:v", "copy",
                        "-c:a", "aac", "-shortest", "out.mp4"], check=True)

    title = apply_title_style(title, variants["title_style"])
    if event_only:
        title = fit_title("UPDATE: " + (title[:-len(" #shorts")] if title.endswith(" #shorts") else title))
    with open("run_info.json", "w", encoding="utf-8") as f:
        json.dump({"date": local_now().isoformat(timespec="minutes"), "mode": mode, "format": fmt,
                   "location": REGION, "voice": variants["voice"], "voice_used": voice_used,
                   "hook": variants["hook"], "title_style": variants["title_style"],
                   "script_source": SCRIPT_SOURCE, "story": loc["story"]["title"] if loc else fmt,
                   "score": loc["story"]["score"] if loc else 0, "event": event_only}, f)
    with open("caption.txt", "w", encoding="utf-8") as f:
        f.write(f"{title}\n{tags}\n\nData: National Weather Service and NOAA Storm Prediction Center. "
                f"Outlooks and watches are not warnings; check weather.gov for alerts in your area. "
                f"Narrated with a synthetic voice.\n")
    print("Title:", title)
    print("Tags:", tags)
    print("Done: out.mp4")


if __name__ == "__main__":
    main()
