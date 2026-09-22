# To do

## 1. Remove the fuzzy code matching — after `repair_once.py` has run

`vf_due_sync.py` and `send_digest.py` both match a calendar event to an order
by exact code, then by folding characters the old OCR confused (`O`/`0`,
`I`/`1`, `S`/`5`, `B`/`8`, `Z`/`2`, `G`/`6`), then by same-day-and-client.

Those fallbacks exist only for events the old script wrote with a misread
code. Nothing produces bad codes any more — the API gives exact values — so
once the calendar is clean the fallbacks are dead weight, and they carry a
real risk: matching the wrong event.

Once `repair_once.py --apply` has run, delete from both files:

- `CONFUSABLES`, `canon()`, `_edit_distance_1()`
- the "legacy code" and "near-miss code" tiers in `find_match()`
- the fold and day+client tiers in `find_event()`

Leaving only the exact `vfJobCode` lookup.

## 2. One event that cannot be matched with confidence

`Michele Bayens`, pickup 2026-09-29. The calendar has `61CWR`; Virtual Framer
says `RM6K2`. The two share no characters, so this is not an OCR misreading —
it may be a different artwork entirely.

`repair_once.py` deliberately leaves it alone. Someone who knows the job
should look at it. Until then the sync will treat `RM6K2` as having no event,
which is harmless: the order is delivered and stays out of the digest.

---

Closed: whether Virtual Framer's other delivery states need their own section
in the email. They do not — delivered in Virtual Framer plus a done word on
the calendar are the only two things that matter.
