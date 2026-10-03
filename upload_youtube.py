import os
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
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
res = yt.videos().insert(part="snippet,status", body=body,
        media_body=MediaFileUpload("out.mp4", mimetype="video/mp4", resumable=True)).execute()
print("Uploaded:", res["id"])