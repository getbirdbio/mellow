#!/usr/bin/env python3
"""
Birthday WhatsApp System
Checks Loopy Loyalty for customers whose birthday is today (SAST / UTC+2),
adds 12 stamps to their loyalty card (triggering a free coffee reward),
and sends them a happy birthday WhatsApp message via Twilio.

Runs daily at 6 AM SAST via GitHub Actions.
"""

import os
import json
import jwt
import time
import requests
import logging
from datetime import datetime, timezone, timedelta
from twilio.rest import Client

# --------------- Configuration ---------------

# Loopy Loyalty
LOOPY_API_KEY = os.environ["LOOPY_API_KEY"]
LOOPY_API_SECRET = os.environ["LOOPY_API_SECRET"]
LOOPY_CAMPAIGN_ID = os.environ["LOOPY_CAMPAIGN_ID"]
LOOPY_BASE_URL = "https://api.loopyloyalty.com/v1"

# Twilio
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_WHATSAPP_NUMBER = os.environ["TWILIO_WHATSAPP_NUMBER"]
TWILIO_CONTENT_SID = os.environ["TWILIO_CONTENT_SID"]

# Timezone: South Africa Standard Time (UTC+2)
SAST = timezone(timedelta(hours=2))

# Number of stamps for a free coffee reward
BIRTHDAY_STAMPS = 12

# Page size for fetching cards from Loopy Loyalty
PAGE_SIZE = 100

# --------------- Logging ---------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# --------------- Loopy Loyalty ---------------


def get_loopy_token():
    """Generate a JWT for Loopy Loyalty API authentication."""
    now = int(time.time())
    payload = {
        "uid": LOOPY_API_KEY,
        "iat": now - 10,
        "exp": now + 3600,
        "username": "",
        "pid": LOOPY_API_KEY,
    }
    return jwt.encode(payload, LOOPY_API_SECRET, algorithm="HS256")


def get_loopy_headers():
    """Return authenticated headers for Loopy Loyalty API."""
    return {"Authorization": get_loopy_token(), "Content-Type": "application/json"}


def fetch_all_cards():
    """Fetch all cards from the campaign, paginated."""
    headers = get_loopy_headers()
    url = f"{LOOPY_BASE_URL}/card/cid/{LOOPY_CAMPAIGN_ID}"

    all_cards = []
    start = 0

    while True:
        body = {"dt": {"start": start, "length": PAGE_SIZE}}

        last_error = None
        for attempt in range(3):
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=30)
                resp.raise_for_status()
                break
            except requests.exceptions.RequestException as e:
                last_error = e
                log.warning("fetch_all_cards attempt %d failed (start=%d): %s", attempt + 1, start, e)
                time.sleep(5 * (attempt + 1))
        else:
            raise last_error

        data = resp.json()

        cards = data.get("data", [])
        all_cards.extend(cards)

        total = data.get("recordsTotal", 0)
        start += PAGE_SIZE

        if start >= total or not cards:
            break

    log.info("Fetched %d cards (total: %d)", len(all_cards), total)
    return all_cards


def get_card_stamps(card_id):
    """Fetch the current stamp count for a specific card."""
    headers = get_loopy_headers()
    url = f"{LOOPY_BASE_URL}/card/{card_id}"

    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            last_error = e
            log.warning("get_card_stamps attempt %d failed for card %s: %s", attempt + 1, card_id, e)
            time.sleep(5 * (attempt + 1))
    else:
        raise last_error

    data = resp.json()

    card = data.get("card", {})
    stamps = int(card.get("totalStampsEarned", 0))
    return stamps


def get_birthday_customers(cards):
    """Filter cards to those whose birthday is today in SAST (UTC+2)."""
    today_sast = datetime.now(SAST)
    today_month = today_sast.month
    today_day = today_sast.day

    log.info("Today's date in SAST: %s", today_sast.strftime("%Y-%m-%d"))

    birthday_cards = []
    for card in cards:
        details = card.get("customerDetails") or {}
        birthday_str = details.get("Birthday")
        if not birthday_str:
            continue

        try:
            # Parse birthday as UTC, then convert to SAST before comparing
            bday_utc = datetime.fromisoformat(birthday_str.replace("Z", "+00:00"))
            bday_sast = bday_utc.astimezone(SAST)
            if bday_sast.month == today_month and bday_sast.day == today_day:
                birthday_cards.append(card)
        except (ValueError, TypeError):
            log.warning("Could not parse birthday '%s' for card %s", birthday_str, card.get("id"))

    log.info("Found %d customers with birthdays today (%02d/%02d)", len(birthday_cards), today_month, today_day)
    return birthday_cards


def add_birthday_stamps(card_id):
    """Add 12 stamps to a card, triggering the free coffee reward.

    After a timeout, checks whether stamps were actually applied before
    retrying, to prevent duplicate stamps.
    """
    headers = get_loopy_headers()
    url = f"{LOOPY_BASE_URL}/card/cid/{card_id}/addStamps/{BIRTHDAY_STAMPS}"

    # Record stamp count before we start
    try:
        stamps_before = get_card_stamps(card_id)
        log.info("Card %s has %d total stamps before birthday stamps", card_id, stamps_before)
    except Exception as e:
        log.warning("Could not read stamps before for card %s: %s", card_id, e)
        stamps_before = None

    last_error = None
    for attempt in range(3):
        try:
            timeout = 180 * (attempt + 1)  # 60s, 120s, 180s
            resp = requests.post(url, headers=headers, json={}, timeout=timeout)
            resp.raise_for_status()
            log.info("Added %d stamps to card %s", BIRTHDAY_STAMPS, card_id)
            return resp.json()
        except requests.exceptions.RequestException as e:
            last_error = e
            log.warning("Attempt %d failed for card %s: %s", attempt + 1, card_id, e)

            # Check if the stamps were actually applied despite the timeout
            if stamps_before is not None:
                try:
                    time.sleep(5)
                    stamps_now = get_card_stamps(card_id)
                    if stamps_now >= stamps_before + BIRTHDAY_STAMPS:
                        log.info(
                            "Stamps were applied despite timeout (before=%d, now=%d). Skipping retry.",
                            stamps_before, stamps_now,
                        )
                        return {"success": True}
                except Exception as check_err:
                    log.warning("Could not verify stamps for card %s: %s", card_id, check_err)

            time.sleep(5 * (attempt + 1))

    raise last_error


# --------------- Twilio WhatsApp ---------------


def send_birthday_sms(phone_number, customer_name):
    """Send a birthday WhatsApp message via Twilio approved content template."""
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    first_name = customer_name.split()[0] if customer_name else "there"

    message = client.messages.create(
        content_sid=TWILIO_CONTENT_SID,
        content_variables=json.dumps({"1": first_name}),
        from_=f"whatsapp:{TWILIO_WHATSAPP_NUMBER}",
        to=f"whatsapp:{phone_number}",
    )

    log.info("Sent WhatsApp message to %s (SID: %s)", phone_number, message.sid)
    return message.sid


# --------------- Main ---------------


def main():
    test_phone = os.environ.get("TEST_PHONE")
    if test_phone:
        log.info("=== TEST MODE: sending to %s ===", test_phone)
        send_birthday_sms(test_phone, "Test User")
        log.info("=== Test message sent ===")
        return

    log.info("=== Birthday WhatsApp check started ===")

    cards = fetch_all_cards()
    birthday_cards = get_birthday_customers(cards)

    sent = 0
    failed = 0

    for card in birthday_cards:
        details = card.get("customerDetails") or {}
        name = details.get("Name", "")
        phone = details.get("Contact Number", "")
        card_id = card.get("id")

        if not phone:
            log.warning("No phone number for card %s (%s), skipping", card_id, name)
            failed += 1
            continue

        try:
            # Add 12 stamps to trigger free coffee reward
            add_birthday_stamps(card_id)
            # Send birthday WhatsApp message
            send_birthday_sms(phone, name)
            sent += 1
        except Exception as e:
            log.error("Failed to process birthday for %s (%s): %s", phone, name, e)
            failed += 1

    log.info("=== Done: %d sent, %d failed, %d total birthdays ===", sent, failed, len(birthday_cards))


if __name__ == "__main__":
    main()
