#!/usr/bin/env python3
"""
One-off WhatsApp blast: Radio 947 "Johannesburg's Best Pizza" voting campaign.

Sends the approved marketing content template to every customer with a phone
number on the Loopy Loyalty campaign. Manual trigger only (no schedule).

Modes (via env vars):
  DRY_RUN=true (default)  -> report recipient count, send nothing
  TEST_PHONE=+27...       -> send a single test message to that number only
  DRY_RUN=false           -> send to all customers (respecting OFFSET/LIMIT)

OFFSET / LIMIT allow sending in batches to stay under WhatsApp's 24-hour
business-initiated conversation limits (new numbers start at 250/day).
"""

import os
import time

from twilio.rest import Client

from birthday_sms import (
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_NUMBER,
    fetch_all_cards,
    log,
)

TWILIO_BLAST_CONTENT_SID = os.environ["TWILIO_BLAST_CONTENT_SID"]

# Pause between sends to stay well under Twilio's throughput limits
SEND_DELAY_SECONDS = 0.5


def send_blast_message(client, phone_number):
    """Send the pizza vote template to a single number."""
    message = client.messages.create(
        content_sid=TWILIO_BLAST_CONTENT_SID,
        from_=f"whatsapp:{TWILIO_WHATSAPP_NUMBER}",
        to=f"whatsapp:{phone_number}",
    )
    log.info("Sent blast to %s (SID: %s)", phone_number, message.sid)
    return message.sid


def get_recipients():
    """Fetch all cards and return a sorted list of unique phone numbers."""
    cards = fetch_all_cards()
    phones = set()
    skipped = 0
    for card in cards:
        details = card.get("customerDetails") or {}
        phone = (details.get("Contact Number") or "").strip()
        if phone:
            phones.add(phone)
        else:
            skipped += 1

    log.info("Found %d unique phone numbers (%d cards without a number)", len(phones), skipped)
    # Sorted so OFFSET/LIMIT batches are stable across runs
    return sorted(phones)


def main():
    dry_run = os.environ.get("DRY_RUN", "true").lower() != "false"
    test_phone = os.environ.get("TEST_PHONE", "").strip()
    offset = int(os.environ.get("OFFSET", "0") or "0")
    limit = int(os.environ.get("LIMIT", "0") or "0")  # 0 = no limit

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    if test_phone:
        log.info("=== TEST MODE: sending pizza vote blast to %s ===", test_phone)
        send_blast_message(client, test_phone)
        log.info("=== Test message sent ===")
        return

    recipients = get_recipients()
    batch = recipients[offset:offset + limit] if limit else recipients[offset:]
    log.info(
        "Batch: offset=%d limit=%s -> %d recipients (of %d total)",
        offset, limit or "none", len(batch), len(recipients),
    )

    if dry_run:
        log.info("=== DRY RUN: no messages sent. Set DRY_RUN=false to send. ===")
        return

    log.info("=== Sending pizza vote blast to %d customers ===", len(batch))
    sent = 0
    failed = 0
    for phone in batch:
        try:
            send_blast_message(client, phone)
            sent += 1
        except Exception as e:
            log.error("Failed to send to %s: %s", phone, e)
            failed += 1
        time.sleep(SEND_DELAY_SECONDS)

    log.info("=== Done: %d sent, %d failed, %d in batch ===", sent, failed, len(batch))


if __name__ == "__main__":
    main()
