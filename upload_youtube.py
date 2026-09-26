import argparse
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

TOKEN_FILE = "token.json"
CLIENT_SECRET_FILE = "client_secret.json"


def get_credentials():
    credentials = None

    # 保存済みの認証情報があれば読み込む
    if os.path.exists(TOKEN_FILE):
        credentials = Credentials.from_authorized_user_file(
            TOKEN_FILE,
            SCOPES,
        )

    # 認証情報が無効な場合
    if not credentials or not credentials.valid:

        # アクセストークンが期限切れなら自動更新
        if (
            credentials
            and credentials.expired
            and credentials.refresh_token
        ):
            credentials.refresh(Request())

        # 初回、またはrefreshできない場合のみブラウザ認証
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE,
                SCOPES,
            )

            credentials = flow.run_local_server(port=0)

        # 認証情報を保存
        with open(TOKEN_FILE, "w") as f:
            f.write(credentials.to_json())

    return credentials


def main():
    parser = argparse.ArgumentParser(
        description="Upload a video to YouTube"
    )

    parser.add_argument(
        "video",
        help="アップロードする動画ファイルのパス",
    )
    parser.add_argument(
        "thumbnail",
        help="サムネイル画像のパス",
    )
    parser.add_argument(
        "title",
        help="動画タイトル",
    )

    args = parser.parse_args()

    # 認証
    credentials = get_credentials()

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
    )

    # 動画アップロード
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": args.title,
                "description": "YouTube Data API upload",
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": "private",
            },
        },
        media_body=MediaFileUpload(
            args.video,
            chunksize=-1,
            resumable=True,
        ),
    )

    response = request.execute()
    video_id = response["id"]

    print("Upload completed")
    print("Video ID:", video_id)

    # サムネイル設定
    thumbnail_request = youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(args.thumbnail),
    )

    thumbnail_request.execute()

    print("Thumbnail uploaded")


if __name__ == "__main__":
    main()
