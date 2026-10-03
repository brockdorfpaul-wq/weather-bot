import os, re, asyncio, subprocess, textwrap, requests
from PIL import Image, ImageDraw, ImageFont
import edge_tts

# ---------- SETTINGS: edit these ----------
LAT, LON = 41.87, -87.63            # Chicago (verify on latlong.net)
REGION = "Chicago"
CONTACT = "brockdorf.paul@gmail.com"         # NWS asks for an identifying User-Agent; use your email
RADAR_URL = "https://radar.weather.gov/ridge/standard/KLOT_loop.gif"  # Chicago (Romeoville) radar
VOICE = "en-US-AriaNeural"          # try en-US-AndrewNeural for a male news-style voice
# ------------------------------------------

HDR = {"User-Agent": f"(weatherbot, {CONTACT})"}
W, H = 1080, 1920


def get_data():
    p = requests.get(f"https://api.weather.gov/points/{LAT},{LON}", headers=HDR, timeout=30).json()["properties"]
    periods = requests.get(p["forecast"], headers=HDR, timeout=30).json()["properties"]["periods"][:4]
    alerts = requests.get(f"https://api.weather.gov/alerts/active?point={LAT},{LON}",
                          headers=HDR, timeout=30).json()["features"]
    return periods, [a["properties"]["event"] for a in alerts]


def get_radar():
    """Download the looping radar GIF. Returns True if it worked."""
    try:
        r = requests.get(RADAR_URL, headers=HDR, timeout=60)
        if r.status_code == 200 and len(r.content) > 10000 and r.content[:3] == b"GIF":
            with open("radar.gif", "wb") as f:
                f.write(r.content)
            return True
        print("Radar download looked wrong, skipping radar")
    except Exception as e:
        print("Radar error, skipping radar:", e)
    return False


def facts_text(periods, alerts):
    lines = []
    if alerts:
        lines.append("Active alerts (mention first): " + ", ".join(alerts))
    for x in periods:
        lines.append(f"{x['name']}: {x['temperature']} degrees, {x['shortForecast']}, wind {x['windSpeed']}")
    return "\n".join(lines)


def template_script(periods, alerts):
    a, b = periods[0], periods[1]
    out = []
    if alerts:
        out.append(f"Heads up: a {alerts[0]} is in effect for {REGION}. Check weather.gov for details.")
    out.append(f"Here's your forecast for {REGION}.")
    out.append(f"{a['name']}: {a['shortForecast']}, around {a['temperature']} degrees.")
    out.append(f"{b['name']}: {b['shortForecast']}, near {b['temperature']}.")
    out.append("Follow for your daily forecast.")
    return out


def gemini_script(facts):
    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = f"""Write a 30-second voiceover (about 70 words) for a weather short for {REGION}.
Friendly, energetic. One sentence per line, 5 to 7 lines, no emojis, no numbering.
First line is a hook. If there are active alerts, mention them in the first two lines.
Last line is exactly: Follow for your daily forecast.
Use ONLY these facts, add no other numbers or claims:
{facts}"""
    # Model names change. Check aistudio.google.com for the current fast/free model.
    r = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    return [l.strip() for l in r.text.splitlines() if l.strip()]


def numbers_ok(lines, facts):
    allowed = set(re.findall(r"\d+", facts))
    return all(n in allowed for n in re.findall(r"\d+", " ".join(lines)))


def make_script(periods, alerts):
    facts = facts_text(periods, alerts)
    try:
        lines = gemini_script(facts)
        if 4 <= len(lines) <= 9 and numbers_ok(lines, facts):
            return lines
        print("Gemini script failed checks, using template")
    except Exception as e:
        print("Gemini error, using template:", e)
    return template_script(periods, alerts)


def colors_for(text):
    t = text.lower()
    if any(k in t for k in ("storm", "thunder", "rain", "shower")): return (30, 40, 70), (80, 90, 120)
    if any(k in t for k in ("snow", "flurr", "ice")): return (120, 150, 190), (220, 230, 245)
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


def make_frame(path, big, sub, caption, cols, has_radar):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(cols[0][i] * (1 - t) + cols[1][i] * t) for i in range(3)))
    d.text((W / 2, 170), REGION, font=font(70), fill="white", anchor="mm")
    d.text((W / 2, 350), big, font=font(220), fill="white", anchor="mm")
    d.text((W / 2, 540), sub, font=font(60), fill="white", anchor="mm")
    if has_radar:  # dark frame behind the radar loop
        d.rounded_rectangle([90, 620, 990, 1470], radius=30, fill=(15, 15, 25))
    y = 1540
    for line in textwrap.wrap(caption, 26)[:3]:
        d.text((W / 2, y), line, font=font(64), fill="white", anchor="mm", stroke_width=4, stroke_fill="black")
        y += 88
    img.save(path)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(r.stdout.strip())


def main():
    periods, alerts = get_data()
    has_radar = get_radar()
    lines = make_script(periods, alerts)
    print("\n".join(lines))

    asyncio.run(edge_tts.Communicate(" ".join(lines), VOICE).save("voice.mp3"))
    total = duration("voice.mp3")
    words = [len(l.split()) for l in lines]
    cols = colors_for(periods[0]["shortForecast"])
    big = f"{periods[0]['temperature']}\u00b0"

    with open("list.txt", "w", encoding="utf-8") as f:
        for i, l in enumerate(lines):
            make_frame(f"f{i}.png", big, periods[0]["shortForecast"], l, cols, has_radar)
            f.write(f"file 'f{i}.png'\nduration {total * words[i] / sum(words):.3f}\n")
        f.write(f"file 'f{len(lines) - 1}.png'\n")

    # Step 1: background slides with captions (silent)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
                    "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "bg.mp4"], check=True)

    # Step 2: add radar loop on top (if available) and the voiceover
    if has_radar:
        subprocess.run(["ffmpeg", "-y", "-i", "bg.mp4", "-stream_loop", "-1", "-i", "radar.gif",
                        "-i", "voice.mp3", "-filter_complex",
                        "[1:v]scale=860:-2[r];[0:v][r]overlay=(W-w)/2:645:shortest=1,format=yuv420p[v]",
                        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac",
                        "-shortest", "out.mp4"], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-i", "bg.mp4", "-i", "voice.mp3", "-c:v", "copy",
                        "-c:a", "aac", "-shortest", "out.mp4"], check=True)

    with open("caption.txt", "w", encoding="utf-8") as f:
        f.write(f"{REGION} forecast #weather #forecast #shorts\nData: National Weather Service\n")
    print("Done: out.mp4")


main()
