import os, csv, json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=os.environ["YT_CLIENT_ID"],
                    client_secret=os.environ["YT_CLIENT_SECRET"])
yt = build("youtube", "v3", credentials=creds)
title, desc = open("caption.txt", encoding="utf-8").read().split("\n", 1)
body = {"snippet": {"title": title[:95], "description": desc, "categoryId": "25"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}
res = yt.videos().insert(part="snippet,status", body=body,
        media_body=MediaFileUpload("out.mp4", mimetype="video/mp4", resumable=True)).execute()
video_id = res["id"]
print("Uploaded:", video_id)

# Record what this video tested, so learn.py can compare views later.
try:
    with open("run_info.json", encoding="utf-8") as f:
        info = json.load(f)
except Exception:
    info = {}
FIELDS = ["date", "video_id", "mode", "format", "location", "voice", "voice_used", "hook",
          "title_style", "script_source", "title", "story", "score", "event"]
rows = []
if os.path.exists("video_log.csv"):
    with open("video_log.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
rows.append({**info, "video_id": video_id, "title": title[:95]})
with open("video_log.csv", "w", newline="", encoding="utf-8") as f:   # rewriting keeps the columns up to date
    w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
print("Logged in video_log.csv")
