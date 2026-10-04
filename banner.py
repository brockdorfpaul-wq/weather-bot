"""Seasonal AtmosSquall channel banner, drawn in the same style as the videos and thumbnails.

Seasons:  winter storms (Dec-Feb), severe storm season (Mar-May), hurricane season (Jun-Nov).
banner.yml runs this on the 1st of each month; it only uploads when the season has changed
(remembered in banner_state.json), so the banner changes three times a year.

Testing on your PC:  $env:BANNER_DRY_RUN="1"  draws banner.jpg without uploading.
                     $env:BANNER_SEASON="hurricane"  forces a season (winter, spring or hurricane).
                     $env:FORCE_BANNER="1"  uploads even if the season hasn't changed."""
import os, json, math, random
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFilter
import make_video as mv            # shares the AtmosSquall look: clouds, glows, logo, fonts and colors

BW, BH = 2560, 1440                # YouTube's recommended banner size
SAFE = (507, 508, 2053, 931)       # the middle strip every device shows (phones crop the rest)
mv.W, mv.H = BW, BH
CUR = mv.CUR

SEASONS = {
    "winter": {"name": "Winter storm season", "tagline": "Tracking winter storms coast to coast"},
    "spring": {"name": "Severe storm season", "tagline": "Tracking severe storms across the country"},
    "hurricane": {"name": "Hurricane season", "tagline": "Tracking the tropics all season long"},
}


def season_for(month):
    if month in (12, 1, 2):
        return "winter"
    if 6 <= month <= 11:
        return "hurricane"
    return "spring"


# ---------- shared drawing helpers ----------
def gradient(img, top, bottom, y0=0, y1=BH):
    d = ImageDraw.Draw(img)
    for y in range(y0, y1):
        d.line([(0, y), (BW, y)], fill=mv.mix(top, bottom, (y - y0) / max(1, y1 - y0)))


def bolt(d, x, y0, y1, seed, width=10):
    """A forked lightning bolt with a soft glow."""
    rnd = random.Random(seed)
    pts, y = [(x, y0)], y0
    while y < y1:
        x += rnd.randint(-70, 70)
        y += rnd.randint(45, 80)
        pts.append((x, min(y, y1)))
    mv.paste(mv.glow_sprite(300, (225, 220, 255), 150), pts[len(pts) // 2][0] - 300, pts[len(pts) // 2][1] - 300)
    d.line(pts, fill=(230, 225, 255), width=width + 8, joint="curve")
    d.line(pts, fill=(255, 255, 255), width=width, joint="curve")
    branch = pts[len(pts) // 3]
    bx, by = branch
    fork = [branch]
    for _ in range(4):
        bx += rnd.randint(20, 70) * rnd.choice((-1, 1))
        by += rnd.randint(40, 70)
        fork.append((bx, by))
    d.line(fork, fill=(240, 235, 255), width=max(3, width // 2), joint="curve")


def cloud_bank(y, count, scale, fill, seed, spread=(-200, BW + 200)):
    rnd = random.Random(seed)
    for _ in range(count):
        mv.cloud(rnd.uniform(*spread), y + rnd.uniform(-60, 60), scale * rnd.uniform(0.8, 1.25),
                 mv.shade(fill, rnd.uniform(0.9, 1.06)))


def brand_scrim():
    """A soft dark glow behind the logo and name so they read clearly over busy art."""
    cx, cy = (SAFE[0] + SAFE[2]) / 2, (SAFE[1] + SAFE[3]) / 2
    g = mv.glow_sprite(820, (8, 12, 34), 190)
    mv.paste(g.resize((1900, 900)), cx - 950, cy - 450)


def draw_brand(img, d, season):
    brand_scrim()
    lg = mv.logo_sprite(360)
    mv.paste(mv.glow_sprite(250, mv.BRAND_CYAN, 150), 760 - 250, 720 - 250)
    if lg is not None:
        mv.paste(lg, 760 - 180, 720 - 180)
    x = 985
    name_font = mv.fit_font(d, "AtmosSquall", 1060, 170)
    d.text((x, 660), "AtmosSquall", font=name_font, fill="white", anchor="lm", stroke_width=7, stroke_fill=mv.BRAND_NAVY)
    w = d.textlength("AtmosSquall", font=name_font)
    d.line([(x + 6, 752), (x + w, 752)], fill=mv.BRAND_PURPLE, width=10)
    d.line([(x + 6, 752), (x + w * 0.55, 752)], fill=mv.BRAND_CYAN, width=10)
    tag = SEASONS[season]["tagline"]
    d.text((x + 4, 812), tag, font=mv.fit_font(d, tag, 1060, 56), fill=mv.BRAND_CYAN, anchor="lm",
           stroke_width=4, stroke_fill=mv.BRAND_NAVY)
    sub = "Daily weather Shorts  \u2022  Week in Weather every Sunday"
    d.text((x + 4, 884), sub, font=mv.fit_font(d, sub, 1060, 40), fill=(225, 230, 245), anchor="lm",
           stroke_width=3, stroke_fill=mv.BRAND_NAVY)


# ---------- the three seasonal scenes ----------
def winter(img, d):
    gradient(img, (12, 18, 46), (96, 112, 152))
    rnd = random.Random(1)
    for layer, (base, amp, col) in enumerate([(1020, 170, (120, 138, 175)), (1110, 120, (90, 108, 148))]):
        pts = [(x, base - amp * abs(math.sin(x / (430 - layer * 90) + layer)) - 40 * math.sin(x / 97)) for x in range(0, BW + 41, 40)]
        d.polygon([(0, BH)] + pts + [(BW, BH)], fill=col)
        d.line(pts, fill=mv.mix(col, (240, 244, 252), 0.55), width=10, joint="curve")   # snow along the ridgeline
    gradient(img, (226, 233, 245), (190, 202, 224), 1210, BH)
    for _ in range(14):
        x, w = rnd.uniform(0, BW), rnd.uniform(250, 520)
        d.ellipse([x - w / 2, 1195, x + w / 2, 1255], fill=(238, 243, 251))
    for x in list(range(40, 460, 95)) + list(range(2140, 2560, 95)):
        h = rnd.uniform(240, 360)
        y = 1250
        for k in range(4):
            ww = (4 - k) * h / 9
            yy = y - k * h / 4.4
            d.polygon([(x - ww, yy), (x, yy - h / 3.2), (x + ww, yy)], fill=(22, 40, 58))
            d.polygon([(x - ww * 0.9, yy - 6), (x - ww * 0.35, yy - h / 9), (x - ww * 0.1, yy - 6)], fill=(235, 240, 250))
    cloud_bank(150, 16, 3.0, (150, 160, 188), 2)
    cloud_bank(330, 10, 2.2, (124, 134, 166), 3)
    for _ in range(140):
        x, y, ln = rnd.uniform(-200, BW), rnd.uniform(0, BH), rnd.uniform(60, 160)
        d.line([(x, y), (x + ln, y + ln * 0.18)], fill=(205, 215, 235), width=2)
    for _ in range(800):
        x, y, z = rnd.uniform(0, BW), rnd.uniform(0, BH), rnd.uniform(0.25, 1.0)
        r = 2.5 + 8 * z
        d.ellipse([x - r, y - r, x + r, y + r], fill=mv.mix((180, 192, 220), (255, 255, 255), z))


def spring(img, d):
    gradient(img, (14, 24, 56), (88, 104, 116))
    gradient(img, (92, 120, 66), (52, 76, 40), 1150, BH)
    rnd = random.Random(5)
    for x in range(0, BW, 70):
        h = rnd.uniform(18, 45)
        d.line([(x, 1150), (x + rnd.uniform(-8, 8), 1150 - h)], fill=(60, 90, 48), width=6)
    for k, x in enumerate([180, 420, 2180, 2420]):           # distant wind turbines on the plains
        y = 1150 - 40 * k % 3
        d.line([(x, y), (x, y - 330)], fill=(205, 212, 222), width=9)
        for a in (90, 210, 330):
            ang = math.radians(a + 18 * k)
            d.line([(x, y - 330), (x + 150 * math.cos(ang), y - 330 - 150 * math.sin(ang))], fill=(215, 222, 232), width=7)
    for k in range(8):                                   # the towering supercell
        cx = 1280 + math.sin(k * 1.3) * 90
        mv.cloud(cx, 800 - k * 82, 3.7 - k * 0.2, mv.shade((180, 186, 204), 0.92 + k * 0.02))
    for j in range(-7, 8):                               # its spreading anvil, uneven like a real one
        mv.cloud(1280 + j * 180 + rnd.uniform(-40, 40), 175 + abs(j) * 16 + rnd.uniform(-25, 25),
                 rnd.uniform(1.5, 2.3) * (1 - abs(j) * 0.04), mv.shade((198, 202, 216), rnd.uniform(0.9, 1.04)))
    cloud_bank(870, 7, 2.4, (98, 104, 124), 6, spread=(820, 1740))   # dark rain-free base
    for _ in range(520):                                 # rain shaft from the cloud base to the ground
        x, off, z = rnd.uniform(1060, 1560), rnd.random(), rnd.uniform(0.3, 1.0)
        y = 920 + off * 200
        d.line([(x, y), (x - 12, y + 30 + 30 * z)], fill=mv.mix((120, 135, 160), (196, 210, 236), z), width=int(2 + 3 * z))
    bolt(d, 330, 120, 1150, 11, 12)
    bolt(d, 2230, 160, 1150, 12, 10)
    bolt(d, 1560, 1000, 1150, 13, 7)


def hurricane(img, d):
    gradient(img, (18, 14, 50), (88, 58, 138), 0, 860)
    gradient(img, (34, 92, 160), (10, 30, 70), 860, BH)
    rnd = random.Random(9)
    for _ in range(260):                                 # ocean glints, longer and thicker up close
        y = rnd.uniform(870, BH)
        depth = (y - 860) / (BH - 860)
        x, ln = rnd.uniform(0, BW), 30 + 120 * depth
        d.line([(x, y), (x + ln, y)], fill=mv.mix((60, 120, 190), (205, 228, 250), 0.4 + 0.4 * depth), width=2 + int(4 * depth))
    cx, cy, size = 2140, 330, 1400                       # upper right, clear of the channel name
    layer = Image.new("RGBA", (size, size), (240, 244, 250, 0))
    ld = ImageDraw.Draw(layer)
    c = size / 2
    steps, growth, turns = 320, 200, 1.15                # bands spaced widely enough to read as a spiral
    arms = []
    for arm in range(3):
        pts = []
        for i in range(steps + 1):
            th = i / steps * turns * 2 * math.pi
            r = 70 + growth * th / math.pi
            a = th + arm * 2 * math.pi / 3
            pts.append((c + r * math.cos(a), c - r * math.sin(a)))
        arms.append(pts)
    width = lambda i: int(104 - 82 * i / steps)            # thick near the eye, thinning outward
    for pts in arms:                                     # shadow side first, as one smooth band
        for i in range(steps):
            w = width(i) + 14
            x, y = pts[i]
            ld.ellipse([x - w / 2, y + 8 - w / 2, x + w / 2, y + 8 + w / 2], fill=(132, 142, 182, 255))
    for pts in arms:                                     # then the bright cloud tops
        for i in range(steps):
            w = width(i)
            x, y = pts[i]
            ld.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=(243, 246, 252, 255))
    ld.ellipse([c - 120, c - 120, c + 120, c + 120], fill=(236, 240, 250, 255))   # eyewall
    ld.ellipse([c - 52, c - 52, c + 52, c + 52], fill=(28, 78, 150, 255))         # eye
    tilted = layer.filter(ImageFilter.GaussianBlur(3)).resize((1100, 560))
    shadow = mv.glow_sprite(600, (2, 8, 24), 150).resize((1150, 420))
    mv.paste(shadow, cx - 560, cy - 150)
    mv.paste(tilted, cx - 550, cy - 280)
    for _ in range(320):                                 # wind-driven rain on the left
        x, y, z = rnd.uniform(0, 900), rnd.uniform(0, 1100), rnd.uniform(0.3, 1.0)
        d.line([(x, y), (x - 26 * z, y + 60 * z)], fill=mv.mix((120, 130, 175), (205, 215, 245), z), width=int(2 + 3 * z))
    cloud_bank(120, 12, 2.6, (92, 84, 140), 10, spread=(-200, 1300))
    bolt(d, 260, 160, 880, 21, 11)
    bolt(d, 620, 220, 880, 22, 8)
    for _ in range(18):                                  # whitecaps
        x, y = rnd.uniform(0, BW), rnd.uniform(1000, BH)
        d.arc([x - 60, y - 14, x + 60, y + 14], 200, 340, fill=(230, 240, 252), width=4)


SCENES = {"winter": winter, "spring": spring, "hurricane": hurricane}


def make_banner(season, path="banner.jpg"):
    img = Image.new("RGB", (BW, BH))
    d = ImageDraw.Draw(img)
    CUR["img"], CUR["flash"] = img, False
    SCENES[season](img, d)
    draw_brand(img, d, season)
    img.save(path, quality=90)
    return path


# ---------- upload ----------
def upload(path):
    """YouTube's three steps: upload the image, get its URL, then set it in the channel's branding."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"], token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"])
    yt = build("youtube", "v3", credentials=creds)
    url = yt.channelBanners().insert(media_body=MediaFileUpload(path, mimetype="image/jpeg")).execute()["url"]
    channel = yt.channels().list(part="brandingSettings", mine=True).execute()["items"][0]
    # Keep the channel's existing title, description and keywords exactly as they are; only the banner changes.
    branding = {"channel": channel.get("brandingSettings", {}).get("channel", {}), "image": {"bannerExternalUrl": url}}
    yt.channels().update(part="brandingSettings", body={"id": channel["id"], "brandingSettings": branding}).execute()
    print("Banner updated")


def main():
    season = os.environ.get("BANNER_SEASON") or season_for(mv.local_now().month)
    if season not in SEASONS:
        raise ValueError(f"Unknown season {season!r}; use winter, spring or hurricane")
    try:
        with open("banner_state.json", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}
    if state.get("season") == season and not os.environ.get("FORCE_BANNER") and not os.environ.get("BANNER_DRY_RUN"):
        print(f"Banner is already set for {SEASONS[season]['name'].lower()}; nothing to do.")
        return
    path = make_banner(season)
    print(f"Drew the {SEASONS[season]['name'].lower()} banner: {path}")
    if os.environ.get("BANNER_DRY_RUN"):
        print("Dry run: not uploading")
        return
    upload(path)
    with open("banner_state.json", "w", encoding="utf-8") as f:
        json.dump({"season": season, "updated": datetime.now(timezone.utc).isoformat(timespec="minutes")}, f, indent=2)


if __name__ == "__main__":
    main()
