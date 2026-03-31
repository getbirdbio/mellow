#!/usr/bin/env python3
"""
Birthday SMS System
Checks Loopy Loyalty for customers whose birthday is today,
sends them a happy birthday SMS via Twilio with a free coffee offer.

Runs daily at 6 AM SAST via GitHub Actions.
"""

import os
import jwt
import time
import requests
import logging
from datetime import datetime
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
TWILIO_FROM_NUMBER = os.environ["TWILIO_FROM_NUMBER"]

# SMS message template - {name} will be replaced with the customer's first name
BIRTHDAY_MESSAGE = (
    "Happy Birthday, {name}! 🎂🎉 "
    "To celebrate your special day, we'd love to treat you to a FREE coffee on us! "
    "Just show this message at any Bird Coffee location today. "
    "Enjoy your day! ☕ - The Bird Coffee Team"
)

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


def fetch_all_cards():
    """Fetch all cards from the campaign, paginated."""
    token = get_loopy_token()
    headers = {"Authorization": token, "Content-Type": "application/json"}
    url = f"{LOOPY_BASE_URL}/card/cid/{LOOPY_CAMPAIGN_ID}"

    all_cards = []
    start = 0

    while True:
        body = {"dt": {"start": start, "length": PAGE_SIZE}}
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        cards = data.get("data", [])
        all_cards.extend(cards)

        total = data.get("recordsTotal", 0)
        start += PAGE_SIZE

        if start >= total or not cards:
            break

    log.info("Fetched %d cards (total: %d)", len(all_cards), total)
    return all_cards


def get_birthday_customers(cards):
    """Filter cards to those whose birthday is today (matching month and day)."""
    today = datetime.now()
    today_month = today.month
    today_day = today.day

    birthday_cards = []
    for card in cards:
        details = card.get("customerDetails") or {}
        birthday_str = details.get("Birthday")
        if not birthday_str:
            continue

        try:
            bday = datetime.fromisoformat(birthday_str.replace("Z", "+00:00"))
            if bday.month == today_month and bday.day == today_day:
                birthday_cards.append(card)
        except (ValueError, TypeError):
            log.warning("Could not parse birthday '%s' for card %s", birthday_str, card.get("id"))

    log.info("Found %d customers with birthdays today (%02d/%02d)", len(birthday_cards), today_month, today_day)
    return birthday_cards


# --------------- Twilio SMS ---------------


def send_birthday_sms(phone_number, customer_name):
    """Send a birthday SMS via Twilio."""
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    first_name = customer_name.split()[0] if customer_name else "there"
    message_body = BIRTHDAY_MESSAGE.format(name=first_name)

    message = client.messages.create(
        body=message_body,
        from_=TWILIO_FROM_NUMBER,
        to=phone_number,
    )

    log.info("Sent SMS to %s (SID: %s)", phone_number, message.sid)
    return message.sid


# --------------- Main ---------------


def main():
    log.info("=== Birthday SMS check started ===")

    cards = fetch_all_cards()
    birthday_cards = get_birthday_customers(cards)

    sent = 0
    failed = 0

    for card in birthday_cards:
        details = card.get("customerDetails", {})
        name = details.get("Name", "")
        phone = details.get("Contact Number", "")

        if not phone:
            log.warning("No phone number for card %s (%s), skipping", card.get("id"), name)
            failed += 1
            continue

        try:
            send_birthday_sms(phone, name)
            sent += 1
        except Exception as e:
            log.error("Failed to send SMS to %s (%s): %s", phone, name, e)
            failed += 1

    log.info("=== Done: %d sent, %d failed, %d total birthdays ===", sent, failed, len(birthday_cards))


if __name__ == "__main__":
    main()
