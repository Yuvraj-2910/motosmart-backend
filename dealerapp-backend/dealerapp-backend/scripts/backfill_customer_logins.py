"""Give a Cognito login to customers converted before invites were on by default.

Lead conversion used to leave `invite` off, so those customers exist only in our
database: Cognito never heard of the email, and asking for a sign-in code
delivered nothing. This provisions the missing users and links each `sub` back to
its row.

Safe to re-run: it only touches customers that still have no `cognito_sub`, and
`provision_customer` reuses an existing Cognito user rather than failing.

    PYTHONPATH=. .venv/bin/python -m scripts.backfill_customer_logins [--apply]

Without `--apply` it reports what it would do and changes nothing.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.org import Customer
from app.services import cognito


async def main(apply: bool) -> int:
    async with SessionLocal() as session:
        customers = list(
            (
                await session.execute(
                    select(Customer)
                    .where(Customer.email.is_not(None), Customer.cognito_sub.is_(None))
                    .order_by(Customer.created_at)
                )
            ).scalars()
        )

        if not customers:
            print("Nothing to do — every customer with an email already has a login.")
            return 0

        print(f"{len(customers)} customer(s) without a login:\n")
        done = failed = 0

        for customer in customers:
            label = f"  {customer.name[:22]:24} {customer.email}"

            # Report an unusable number before calling AWS: the pool requires
            # phone_number, so these would fail anyway and the dealer needs to
            # know which lead to correct.
            if cognito.to_e164(customer.phone) is None:
                print(f"{label}\n      SKIP  unusable mobile {customer.phone!r}")
                failed += 1
                continue

            if not apply:
                print(f"{label}\n      would provision ({cognito.to_e164(customer.phone)})")
                continue

            result = await cognito.provision_customer(
                email=customer.email, phone=customer.phone, name=customer.name
            )
            if result.ok and result.cognito_sub:
                customer.cognito_sub = result.cognito_sub
                done += 1
                print(f"{label}\n      OK    sub={result.cognito_sub}")
            else:
                failed += 1
                print(f"{label}\n      FAIL  {result.error}")

        if apply:
            await session.commit()
            print(f"\nProvisioned {done}, failed {failed}.")
        else:
            print(f"\nDry run. Re-run with --apply to create {len(customers) - failed} login(s).")

        return 1 if (apply and failed) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(apply="--apply" in sys.argv)))
