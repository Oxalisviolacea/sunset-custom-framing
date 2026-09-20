import glob
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pdf2image import convert_from_path
import pytesseract
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TZ = ZoneInfo("America/New_York")

CALENDAR_ID = "YOUR_CALENDAR_ID@group.calendar.google.com"
EVENT_TIME_HOUR = 9
DONE_WORDS = ["done", "paid", "picked up", "complete"]


def gcal_service():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def clean_client_name(name):
    name = re.sub(r"\s*Client\s*#:\s*[A-Z0-9]+\s*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def extract_job_code(text):
    m = re.search(r"([A-Z0-9]{4,6})\s*$", text.strip())
    return m.group(1) if m else ""


def title_looks_done(title):
    t = (title or "").lower()
    return any(word in t for word in DONE_WORDS)


def find_existing_event_by_job_code(service, job_code):
    result = service.events().list(
        calendarId=CALENDAR_ID,
        q=job_code,
        singleEvents=True,
        maxResults=50
    ).execute()

    for event in result.get("items", []):
        summary = event.get("summary", "")
        if extract_job_code(summary) == job_code:
            return event

    return None


def newest_pdf_path():
    pdf_files = glob.glob("summary_ongoing_clients_orders*.pdf")
    if not pdf_files:
        raise FileNotFoundError("No summary_ongoing_clients_orders PDF files found in ~/Desktop/vf_sync")
    return max(pdf_files, key=os.path.getmtime)


def ocr_pdf_text(pdf_path):
    print("Using PDF:", pdf_path)
    images = convert_from_path(pdf_path, dpi=250)
    full_text = ""

    for image in images:
        text = pytesseract.image_to_string(image, config="--psm 6")
        full_text += text + "\n"

    return full_text


def parse_jobs(full_text):
    # split by PROJECT block, not pickup date
    blocks = re.split(r"(?=Project name:)", full_text, flags=re.IGNORECASE)

    seen_codes = set()
    jobs = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        client_match = re.search(r"Client name:\s*(.+?)(?:\s+Client\s*#:|\n)", block, re.IGNORECASE)
        code_match = re.search(r"Artwork:\s*([A-Z0-9]{4,6})\b", block, re.IGNORECASE)
        date_match = re.search(r"Pick-?up date:\s*(\d{2}/\d{2}/\d{4})", block, re.IGNORECASE)

        if not client_match or not code_match or not date_match:
            continue

        client = clean_client_name(client_match.group(1))
        job_code = code_match.group(1).upper()
        pickup_date = date_match.group(1)

        if job_code in seen_codes:
            continue
        seen_codes.add(job_code)

        title = f"PICKUP — {client} — {job_code}"

        jobs.append({
            "pickup_date": pickup_date,
            "job_code": job_code,
            "client": client,
            "title": title,
        })

    jobs.sort(key=lambda j: datetime.strptime(j["pickup_date"], "%m/%d/%Y"))
    return jobs


def upsert_event(service, job):
    due = datetime.strptime(job["pickup_date"], "%m/%d/%Y").replace(
        tzinfo=TZ,
        hour=EVENT_TIME_HOUR,
        minute=0,
        second=0,
        microsecond=0
    )
    end = due + timedelta(minutes=30)

    existing = find_existing_event_by_job_code(service, job["job_code"])

    if existing:
        old_title = (existing.get("summary") or "").strip()
        if title_looks_done(old_title):
            return "skipped_done"

    body = {
        "summary": job["title"],
        "description": (
            f"Client: {job['client']}\n"
            f"Job Code: {job['job_code']}\n"
            f"Pick-up Date: {job['pickup_date']}\n"
            f"Source: VF ongoing clients/orders PDF"
        ),
        "start": {"dateTime": due.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "extendedProperties": {"private": {"vfJobCode": job["job_code"]}},
        "reminders": {"useDefault": True},
    }

    if existing:
        service.events().update(
            calendarId=CALENDAR_ID,
            eventId=existing["id"],
            body=body
        ).execute()
        return "updated"
    else:
        service.events().insert(
            calendarId=CALENDAR_ID,
            body=body
        ).execute()
        return "inserted"


def main():
    pdf_path = newest_pdf_path()
    full_text = ocr_pdf_text(pdf_path)
    jobs = parse_jobs(full_text)

    print(f"Parsed jobs: {len(jobs)}")

    service = gcal_service()

    inserted = 0
    updated = 0
    skipped_done = 0
    errors = 0

    for job in jobs:
        try:
            result = upsert_event(service, job)
            if result == "inserted":
                inserted += 1
            elif result == "updated":
                updated += 1
            elif result == "skipped_done":
                skipped_done += 1
        except Exception as e:
            errors += 1
            print(f"ERROR: {job['title']} -> {e}")

    print(f"\nInserted: {inserted}")
    print(f"Updated: {updated}")
    print(f"Skipped DONE: {skipped_done}")
    print(f"Errors: {errors}")
    print("Sync complete.")


if __name__ == "__main__":
    main()