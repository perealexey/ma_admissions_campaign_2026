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

import re
import tomllib
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SECRETS_PATH = Path(__file__).parent.parent / ".streamlit" / "secrets.toml"
DATA_DIR = Path(__file__).parent.parent / "data"
MIME_TYPES = {".parquet": "application/octet-stream", ".csv": "text/csv", ".json": "application/json"}

# У сервисных аккаунтов Google нет собственной квоты хранилища — они могут
# ОБНОВЛЯТЬ уже существующие файлы (владелец — человек, расшаривший папку),
# но не могут СОЗДАВАТЬ новые (создание = стать владельцем = нужна квота,
# которой у сервисного аккаунта нет). Датированные файлы вроде
# admissions_long_2026-07-08.parquet каждый день были бы новым именем — на
# Drive кладём последний срез под фиксированным именем, чтобы это всегда
# было "update", а не "create". Полный архив по датам остаётся только
# локально в data/ и raw/ — Drive нужен только дашборду, ему полная история
# ни к чему.
RENAME_FOR_DRIVE = {re.compile(r"^admissions_long_\d{4}-\d{2}-\d{2}\.parquet$"): "admissions_long_latest.parquet"}

# Файлы, которые не нужны дашборду (app.py их не читает) — чисто локальная
# техническая разведка, на Drive заливать незачем.
SKIP_PATTERNS = [re.compile(r"^stage0_targets_.*\.csv$")]


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
    all_files = sorted(p for p in DATA_DIR.iterdir() if p.is_file() and not p.name.startswith("."))

    if not all_files:
        raise SystemExit(f"В {DATA_DIR} нет файлов для заливки.")

    # Локально хранится весь архив admissions_long_*.parquet по датам (это
    # нужно только локально, см. docs/PIPELINE.md) — на Drive заливаем
    # ТОЛЬКО самый свежий, остальные пропускаем целиком, а не перезаписываем
    # одно и то же удалённое имя по три раза подряд.
    dated_long_table = re.compile(r"^admissions_long_\d{4}-\d{2}-\d{2}\.parquet$")
    long_table_files = sorted(p for p in all_files if dated_long_table.match(p.name))
    older_long_tables = set(long_table_files[:-1])  # все, кроме самого свежего по имени/дате
    local_files = [p for p in all_files if p not in older_long_tables]

    uploaded, updated, skipped, failed = 0, 0, 0, []
    synced_names = set()
    for path in local_files:
        if any(pat.match(path.name) for pat in SKIP_PATTERNS):
            skipped += 1
            continue

        remote_name = path.name
        for pattern, fixed_name in RENAME_FOR_DRIVE.items():
            if pattern.match(path.name):
                remote_name = fixed_name
                break
        synced_names.add(remote_name)

        media = MediaFileUpload(str(path), mimetype=MIME_TYPES.get(path.suffix, "application/octet-stream"))
        try:
            if remote_name in remote_files:
                service.files().update(fileId=remote_files[remote_name], media_body=media).execute()
                print(f"обновлён: {remote_name}" + (f" (из {path.name})" if remote_name != path.name else ""))
                updated += 1
            else:
                service.files().create(
                    body={"name": remote_name, "parents": [folder_id]}, media_body=media
                ).execute()
                print(f"загружен новый: {remote_name}")
                uploaded += 1
        except Exception as e:
            print(f"⚠ НЕ УДАЛОСЬ залить {remote_name}: {e}")
            failed.append(remote_name)
            continue

    stray = sorted(set(remote_files) - synced_names)
    print(f"\nГотово: обновлено {updated}, загружено новых {uploaded}, пропущено локально-технических {skipped}.")
    if failed:
        print(f"\n✗ ОШИБКИ при заливке ({len(failed)}): {', '.join(failed)}")
        print("  Если ошибка 'storageQuotaExceeded' — значит, это новое имя файла, которое сервисный")
        print("  аккаунт не может создать сам. Создайте/переименуйте файл с таким именем на Drive")
        print("  вручную один раз (через веб-интерфейс, под своим аккаунтом) — дальше скрипт сможет")
        print("  его обновлять.")
    if stray:
        print(f"\n⚠ На Drive есть файлы, которых нет локально в data/ (не тронуты, удалите вручную при необходимости):")
        for name in stray:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
