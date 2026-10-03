import os, re, json, random, asyncio, subprocess, textwrap, requests
from collections import Counter
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import edge_tts

# ---------- SETTINGS: edit these ----------
CONTACT = "brockdorf.paul@gmail.com"         # NWS/SPC ask for an identifying User-Agent; use your email
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
W, H = 1080, 1920
CLOSING = "Follow for your daily forecast."
REGION = "United States"            # reset in main() to the chosen location
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
    r = requests.get(url, headers=HDR, timeout=60)
    r.raise_for_status()
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
        r = requests.get("https://api.weather.gov/alerts/active", params={"status": "actual"},
                         headers=HDR, timeout=90)
        r.raise_for_status()
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
    r = requests.get(zones[len(zones) // 2], headers=HDR, timeout=30)
    r.raise_for_status()
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
    r = requests.get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", headers=HDR, timeout=30)
    r.raise_for_status()
    p = r.json()["properties"]
    rl = p["relativeLocation"]["properties"]
    return {"lat": round(lat, 4), "lon": round(lon, 4), "city": rl["city"], "state": rl["state"],
            "radar": p.get("radarStation") or FALLBACK_RADAR, "forecast": p["forecast"], "story": None}


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
    cands.sort(key=lambda c: (c["score"], c["size"]), reverse=True)
    for c in cands[:5]:
        try:
            lon, lat = c["point"]()
            loc = locate(lat, lon)
            loc["story"] = c["story"]
            return loc, spc_checked
        except Exception as e:
            print(f"Could not use {c['story']['title']}:", e)
    print("No big weather story found")
    return None, spc_checked


# ---------- weather data ----------
def get_data(loc):
    periods = requests.get(loc["forecast"], headers=HDR, timeout=30).json()["properties"]["periods"][:4]
    alerts = requests.get(f"https://api.weather.gov/alerts/active?point={loc['lat']},{loc['lon']}",
                          headers=HDR, timeout=30).json()["features"]
    return periods, [a["properties"]["event"] for a in alerts]


def station_radar_url(station):
    return f"https://radar.weather.gov/ridge/standard/{station}_loop.gif"


def get_radar(url):
    """Download a looping radar GIF. Returns True if it worked."""
    try:
        r = requests.get(url, headers=HDR, timeout=60)
        if r.status_code == 200 and len(r.content) > 10000 and r.content[:3] == b"GIF":
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
            p = requests.get(f"https://api.weather.gov/points/{lat},{lon}", headers=HDR, timeout=30).json()["properties"]
            periods = requests.get(p["forecast"], headers=HDR, timeout=30).json()["properties"]["periods"][:4]
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
        lines.append(f"Storm Prediction Center: no notable severe thunderstorm risk areas are forecast {day_word}")
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
    t = event.lower()
    if any(k in t for k in ("winter", "snow", "ice", "freez", "blizzard", "frost", "wind chill")):
        return (120, 150, 190), (220, 230, 245)
    if any(k in t for k in ("heat", "fire", "red flag")): return (200, 70, 30), (250, 160, 60)
    if any(k in t for k in ("tornado", "thunder", "flood", "storm", "hurricane")): return (30, 40, 70), (80, 90, 120)
    return (60, 110, 180), (150, 190, 230)


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
    return base + " #shorts" if len(base) <= 84 else base[:92]


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
def gemini_script(facts, style):
    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = f"""Write a 30-second voiceover (about 70 words) for a weather short about {REGION}.
{style}
Friendly, energetic. One sentence per line, 5 to 7 lines, no emojis, no numbering.
Last line is exactly: {CLOSING}
Use ONLY these facts, add no other numbers or claims:
{facts}"""
    # Model names change. Check aistudio.google.com for the current fast/free model.
    r = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    return [l.strip() for l in r.text.splitlines() if l.strip()]


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


# ---------- drawing ----------
def colors_for(text):
    t = text.lower()
    if any(k in t for k in ("snow", "flurr", "ice", "blizzard")): return (120, 150, 190), (220, 230, 245)
    if any(k in t for k in ("storm", "thunder", "rain", "shower")): return (30, 40, 70), (80, 90, 120)
    if "sunny" in t or "clear" in t: return (255, 140, 40), (60, 140, 220)
    return (60, 110, 180), (150, 190, 230)


FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",                             # Windows
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",        # Mac
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",     # Linux / GitHub Actions
]


def font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def fit_font(d, text, max_w, size):
    while size > 24 and d.textlength(text, font=font(size)) > max_w:
        size -= 2
    return font(size)


def gradient(d, cols):
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(cols[0][i] * (1 - t) + cols[1][i] * t) for i in range(3)))


def draw_caption(d, caption):
    y = 1540
    for line in textwrap.wrap(caption, 26)[:3]:
        d.text((W / 2, y), line, font=font(64), fill="white", anchor="mm", stroke_width=4, stroke_fill="black")
        y += 88


def draw_label(d, text, color, y, size):
    if text:
        d.text((W / 2, y), text, font=fit_font(d, text, 980, size), fill=color, anchor="mm",
               stroke_width=3, stroke_fill="black")


def draw_chips(d, chips, top, step, height):
    for i, (text, outline) in enumerate(chips):
        y0 = top + i * step
        d.rounded_rectangle([90, y0, 990, y0 + height], radius=24, fill=(18, 18, 44), outline=outline, width=4)
        d.text((W / 2, y0 + height / 2), text, font=fit_font(d, text, 840, 46), fill="white", anchor="mm")


def make_frame(path, big, sub, caption, cols, has_radar, label, label_color):
    """Morning / overview look: location, story label, big number, radar below."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    gradient(d, cols)
    d.text((W / 2, 130), REGION, font=fit_font(d, REGION, 980, 66), fill="white", anchor="mm")
    draw_label(d, label, label_color, 220, 46)
    d.text((W / 2, 395), big, font=font(210), fill="white", anchor="mm")
    d.text((W / 2, 550), sub, font=fit_font(d, sub, 980, 56), fill="white", anchor="mm")
    if has_radar:
        d.rounded_rectangle([90, 620, 990, 1470], radius=30, fill=(15, 15, 25))
    draw_caption(d, caption)
    img.save(path)


def make_frame_evening(path, info, caption, has_radar, label, label_color):
    """Evening look: night colors, story label, tomorrow's high, two info chips, smaller radar."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    gradient(d, ((20, 24, 62), (78, 52, 120)))
    m = info["tomorrow"]
    d.text((W / 2, 105), REGION, font=fit_font(d, REGION, 980, 62), fill="white", anchor="mm")
    draw_label(d, label, label_color, 175, 42)
    d.text((W / 2, 250), "TOMORROW'S HIGH", font=font(40), fill=(255, 215, 120), anchor="mm")
    d.text((W / 2, 395), f"{m['temperature']}\u00b0", font=font(190), fill="white", anchor="mm")
    sub = textwrap.shorten(m["shortForecast"], 34, placeholder="...")
    d.text((W / 2, 530), sub, font=fit_font(d, sub, 980, 50), fill="white", anchor="mm")
    draw_chips(d, [(info["chip1"], (255, 215, 0)), (info["chip2"], (120, 200, 255))], 620, 100, 82)
    if has_radar:
        d.rounded_rectangle([90, 830, 990, 1470], radius=30, fill=(12, 12, 28))
    draw_caption(d, caption)
    img.save(path)


def make_frame_showdown(path, sd, caption, has_radar, label, label_color, day_word):
    """Quiet-day look: hottest vs coldest big city, rain/snow cities, national radar."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    gradient(d, ((28, 36, 84), (120, 70, 140)))
    h, c = sd["hot"], sd["cold"]
    d.text((W / 2, 105), "United States", font=font(62), fill="white", anchor="mm")
    draw_label(d, label, label_color, 175, 42)
    d.text((W / 2, 250), f"HOTTEST VS COLDEST {day_word.upper()}", font=fit_font(d, "HOTTEST VS COLDEST TOMORROW", 980, 40),
           fill=(255, 215, 120), anchor="mm")
    d.text((W / 2, 395), f"{sd['gap']}\u00b0", font=font(190), fill="white", anchor="mm")
    sub = f"{h['city']} vs {c['city']}"
    d.text((W / 2, 530), sub, font=fit_font(d, sub, 980, 50), fill="white", anchor="mm")
    if sd["wet"]:
        wet = "RAIN/SNOW: " + ", ".join(f"{r['city']} {r['pop']}%" for r in sd["wet"][:2])
    else:
        wet = "DRY: no big-city rain or snow likely"
    draw_chips(d, [(f"HOTTEST: {h['city']} {h['temp']}\u00b0", (255, 150, 60)),
                   (f"COLDEST: {c['city']} {c['temp']}\u00b0", (120, 200, 255)),
                   (wet, (170, 255, 170))], 620, 95, 80)
    if has_radar:
        d.rounded_rectangle([90, 915, 990, 1470], radius=30, fill=(12, 12, 28))
    draw_caption(d, caption)
    img.save(path)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(r.stdout.strip())


# ---------- main ----------
def main():
    global REGION, CURRENT_HOOK
    variants = choose_variants()
    CURRENT_HOOK = HOOK_TEXT[variants["hook"]]
    print("Testing:", variants)
    mode = pick_mode()
    day = 2 if mode == "evening" else 1            # evening video uses tomorrow's (Day 2) SPC outlook
    day_word = "tomorrow" if mode == "evening" else "today"
    print("Mode:", mode)

    features = fetch_alerts()
    loc, spc_checked = choose_location(day, day_word, features)
    spc_label = f"SPC: no severe storm risk {day_word}" if spc_checked else ""
    green = (150, 235, 150)

    if loc:
        # ---- a local story: storms, winter storm, hurricane, heat, flood, etc. ----
        REGION = f"{loc['city']}, {loc['state']}"
        story = loc["story"]
        print("Location:", REGION, f"({loc['lat']}, {loc['lon']})", "radar", loc["radar"], "|", story["title"])
        periods, alerts = get_data(loc)
        has_radar = get_radar(station_radar_url(loc["radar"]))
        if mode == "evening":
            info = evening_info(periods)
            facts = evening_facts(info, alerts, story)
            lines = make_script(facts, template_evening(info, alerts, story), EVENING_STYLE)
            draw = lambda path, cap: make_frame_evening(path, info, cap, has_radar, story["label"], story["color"])
            radar_filter = ("[1:v]scale=680:600:force_original_aspect_ratio=decrease[r];"
                            "[0:v][r]overlay=(W-w)/2:1150-h/2:shortest=1,format=yuv420p[v]")
        else:
            facts = facts_text(periods, alerts, story)
            lines = make_script(facts, template_script(periods, alerts, story), MORNING_STYLE)
            cols = colors_for(periods[0]["shortForecast"])
            big = f"{periods[0]['temperature']}\u00b0"
            draw = lambda path, cap: make_frame(path, big, periods[0]["shortForecast"], cap, cols,
                                                has_radar, story["label"], story["color"])
            radar_filter = ("[1:v]scale=860:-2[r];[0:v][r]overlay=(W-w)/2:645:shortest=1,format=yuv420p[v]")
        title = make_title(mode, loc, story, periods)
        tags = " ".join(make_hashtags(loc["city"], loc["state"], story))
        fmt = story["category"]
    else:
        REGION = "United States"
        has_radar = get_radar(NATIONAL_RADAR_URL)
        try:
            # ---- quiet day: big-city showdown ----
            sd = get_showdown(mode)
            print("Showdown:", sd["hot"]["city"], sd["hot"]["temp"], "vs", sd["cold"]["city"], sd["cold"]["temp"])
            facts = showdown_facts(sd, spc_checked, day_word)
            lines = make_script(facts, showdown_template(sd, day_word), SHOWDOWN_STYLE)
            draw = lambda path, cap: make_frame_showdown(path, sd, cap, has_radar, spc_label, green, day_word)
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
            draw = lambda path, cap: make_frame(path, big, sub, cap, cols, has_radar, spc_label, green)
            radar_filter = ("[1:v]scale=860:-2[r];[0:v][r]overlay=(W-w)/2:1045-h/2:shortest=1,format=yuv420p[v]")
            title = overview_title(total)
            tags = "#usweather #nationalweather #weather #forecast #shorts"
            fmt = "overview"

    print("\n".join(lines))
    asyncio.run(edge_tts.Communicate(" ".join(lines), variants["voice"]).save("voice.mp3"))
    clip_len = duration("voice.mp3")
    words = [len(l.split()) for l in lines]

    with open("list.txt", "w", encoding="utf-8") as f:
        for i, l in enumerate(lines):
            draw(f"f{i}.png", l)
            f.write(f"file 'f{i}.png'\nduration {clip_len * words[i] / sum(words):.3f}\n")
        f.write(f"file 'f{len(lines) - 1}.png'\n")

    # Step 1: background slides with captions (silent)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
                    "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "bg.mp4"], check=True)

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
    with open("run_info.json", "w", encoding="utf-8") as f:
        json.dump({"date": local_now().isoformat(timespec="minutes"), "mode": mode, "format": fmt,
                   "location": REGION, "voice": variants["voice"], "hook": variants["hook"],
                   "title_style": variants["title_style"], "script_source": SCRIPT_SOURCE}, f)
    with open("caption.txt", "w", encoding="utf-8") as f:
        f.write(f"{title}\n{tags}\n\nData: National Weather Service and NOAA Storm Prediction Center. "
                f"Outlooks and watches are not warnings; check weather.gov for alerts in your area.\n")
    print("Title:", title)
    print("Tags:", tags)
    print("Done: out.mp4")


main()
