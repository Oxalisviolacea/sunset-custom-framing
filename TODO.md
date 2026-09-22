# To do

## 1. Tell "delivered" apart from "done" in Virtual Framer

Virtual Framer records **delivered**, which is not the same as the work being
**done**. A job can be finished in the workshop but not yet collected, and the
digest cannot currently tell those apart — it only reads `isDelivered`.

That probably wants its own section in the email, something like *finished,
waiting for pickup*, separate from past due and upcoming.

Needs deciding first:

- What each `isDelivered` value actually means. `1, 4, 6` are treated as
  finished and `2, 3` as open, copied from the app's own "ongoing" filter.
  Nobody has confirmed what the numbers mean.
- Whether a state for "made but not collected" exists at all, or whether it
  has to come from somewhere else in the record.
- What to call the new section.

The digest is correct as it stands, just coarser than the real workflow.

## 2. Repair mis-OCR'd codes on delivered orders

Nine calendar events store a code the old OCR script misread — `OF3KZ` where
Virtual Framer says `0F3KZ`, and similar. Nothing is broken: the digest and
the sync both fall back to fuzzy matching and find them. But they resolve by
guesswork on every run instead of matching exactly, which is a thinner margin
than it needs to be.

`fix_legacy_codes.py` did exactly this repair and was deleted once the open
orders were clean. It would need restoring and pointing at delivered orders.

Low priority. Every one of these is a finished order.

## 3. One order has never been on the calendar

`John Limitone 4NR4W`, project `QLA25`, pickup 2026-07-22 — a two-day glass
replacement, no framing, $95.36. Placed and delivered inside a single week,
which is likely why the old script never created an event for it. Nobody at
the shop recognises the name.

It is delivered and out of the digest. Listed only so it is not mistaken for
a gap in the sync later.

Orders older than 90 days with no calendar event are deliberately not being
fixed.
