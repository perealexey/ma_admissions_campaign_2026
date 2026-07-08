#!/usr/bin/env python
"""
sync_to_drive.py — заливает содержимое локальной data/ в папку на Google
Drive, откуда его читает задеплоенный дашборд (app.py, доступ read-only).

Использует тот же сервисный аккаунт, что и дашборд (.streamlit/secrets.toml),
но запрашивает более широкий scope (read-write) — только для этого
локального скрипта, права самого дашборда не меняются.

Запуск: python3 scripts/sync_to_drive.py
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SECRETS_PATH = Path(__file__).parent.parent / ".streamlit" / "secrets.toml"
DATA_DIR = Path(__file__).parent.parent / "data"
MIME_TYPES = {".parquet": "application/octet-stream", ".csv": "text/csv", ".json": "application/json"}


def get_service():
    with SECRETS_PATH.open("rb") as f:
        secrets = tomllib.load(f)
    creds = service_account.Credentials.from_service_account_info(
        secrets["gdrive_service_account"],
        scopes=["https://www.googleapis.com/auth/drive"],  # read-write, только для этого скрипта
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False), secrets["gdrive_folder_id"]


def list_remote_files(service, folder_id: str) -> dict[str, str]:
    files, page_token = {}, None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name)",
            pageToken=page_token,
        ).execute()
        files.update({f["name"]: f["id"] for f in resp.get("files", [])})
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def main():
    if not SECRETS_PATH.exists():
        raise SystemExit(f"Не найден {SECRETS_PATH} — сначала настройте локальные секреты (см. app.py).")

    service, folder_id = get_service()
    remote_files = list_remote_files(service, folder_id)
    local_files = sorted(p for p in DATA_DIR.iterdir() if p.is_file() and not p.name.startswith("."))

    if not local_files:
        raise SystemExit(f"В {DATA_DIR} нет файлов для заливки.")

    uploaded, updated = 0, 0
    for path in local_files:
        media = MediaFileUpload(str(path), mimetype=MIME_TYPES.get(path.suffix, "application/octet-stream"))
        if path.name in remote_files:
            service.files().update(fileId=remote_files[path.name], media_body=media).execute()
            print(f"обновлён: {path.name}")
            updated += 1
        else:
            service.files().create(
                body={"name": path.name, "parents": [folder_id]}, media_body=media
            ).execute()
            print(f"загружен новый: {path.name}")
            uploaded += 1

    stray = sorted(set(remote_files) - {p.name for p in local_files})
    print(f"\nГотово: обновлено {updated}, загружено новых {uploaded}.")
    if stray:
        print(f"\n⚠ На Drive есть файлы, которых нет локально в data/ (не тронуты, удалите вручную при необходимости):")
        for name in stray:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
