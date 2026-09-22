# To do

## Distinguish "delivered" from "done" in Virtual Framer

Virtual Framer's state is **delivered**, which is not the same thing as the
work being **done**. An order can be finished in the workshop but not yet
collected, and right now the digest cannot tell those apart — it only sees
`isDelivered`.

That probably wants a third category in the email, something like *finished,
waiting for pickup*, separate from *past due* and *upcoming*.

Needs deciding first:

- Which `isDelivered` values mean what. Currently `1, 4, 6` are treated as
  finished and `2, 3` as open, copied from the app's own "ongoing" filter.
  Nobody has confirmed what each number actually means.
- Whether a fourth state exists for "made but not collected", or whether that
  has to come from somewhere else in the record.
- What the new email section should be called and where it sits.

Raised 2026-09-22. Not urgent — the digest is correct as it stands, just
coarser than the shop's actual workflow.

## Align the digest's lookback with the sync's

The sync looks back 90 days (`DAYS_BACK`). The digest looks back 365
(`LOOKBACK_DAYS`). So the digest can list an order the sync would never
create an event for. Probably both should be 90.
