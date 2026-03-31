#!/usr/bin/env python3
"""
Birthday SMS System
Checks Loopy Loyalty for customers whose birthday is today,
adds 12 stamps to their loyalty card (triggering a free coffee reward),
and sends them a happy birthday SMS via Twilio.

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

# Number of stamps for a free coffee reward
BIRTHDAY_STAMPS = 12

# SMS message template - {name} will be replaced with the customer's first name
BIRTHDAY_MESSAGE = (
    "Happy Birthday, {name}! 🎂🎉 "
    "To celebrate your special day, we've added a FREE coffee reward to your "
    "Bird Coffee loyalty card! Just open your card in your wallet and show it "
    "at any Bird Coffee location to redeem. "
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


def add_birthday_stamps(card_id):
    """Add 12 stamps to a card, triggering the free coffee reward."""
    headers = get_loopy_headers()
    url = f"{LOOPY_BASE_URL}/card/cid/{card_id}/addStamps/{BIRTHDAY_STAMPS}"

    resp = requests.post(url, headers=headers, json={}, timeout=30)
    resp.raise_for_status()

    log.info("Added %d stamps to card %s", BIRTHDAY_STAMPS, card_id)
    return resp.json()


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
            # Send birthday SMS
            send_birthday_sms(phone, name)
            sent += 1
        except Exception as e:
            log.error("Failed to process birthday for %s (%s): %s", phone, name, e)
            failed += 1

    log.info("=== Done: %d sent, %d failed, %d total birthdays ===", sent, failed, len(birthday_cards))


if __name__ == "__main__":
    main()
