"""Smoke test for Zoho Desk ticket creation."""

from __future__ import annotations

from datetime import datetime

from zoho_ticket_creation import DEPT_GENERAL, PRIORITY_MEDIUM, create_zoho_ticket


def main() -> None:
    subject = "Dummy ticket from dummy.py"
    description = (
        "This is a test ticket created by dummy.py.\n"
        f"Timestamp: {datetime.now().isoformat()}\n"
    )
    try:
        ticket = create_zoho_ticket(
            subject=subject,
            description=description,
            department_id=DEPT_GENERAL,
            priority=PRIORITY_MEDIUM,
        )
    except Exception as exc:
        print(f"Failed to create ticket: {exc}")
        return

    ticket_id = ticket.get("id") or ticket.get("ticketNumber")
    print(f"Ticket created: {ticket_id}")


if __name__ == "__main__":
    main()
