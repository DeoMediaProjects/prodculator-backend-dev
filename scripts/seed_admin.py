#!/usr/bin/env python3
"""Seed an admin into the admins table.

Usage:
    python scripts/seed_admin.py --email admin@example.com --name "Admin Name"
    python scripts/seed_admin.py  # prompts interactively
"""
import argparse
import getpass
import sys
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Allow running from the project root
sys.path.insert(0, ".")

from app.core.config import get_settings
from app.core.security import hash_password


def seed_admin(email: str, password: str, name: str | None = None) -> None:
    settings = get_settings()
    engine = create_engine(settings.DB_URL)

    with Session(engine) as session:
        existing = session.execute(
            text("SELECT id FROM admins WHERE lower(email) = lower(:email)"),
            {"email": email},
        ).first()

        if existing:
            print(f"Admin with email '{email}' already exists.")
            sys.exit(1)

        admin_id = str(uuid4())
        session.execute(
            text(
                "INSERT INTO admins (id, email, password_hash, name, created_at) "
                "VALUES (:id, :email, :password_hash, :name, :created_at)"
            ),
            {
                "id": admin_id,
                "email": email.strip().lower(),
                "password_hash": hash_password(password),
                "name": name or None,
                "created_at": datetime.now(timezone.utc),
            },
        )
        session.commit()

    print(f"Admin seeded successfully.")
    print(f"  ID:    {admin_id}")
    print(f"  Email: {email.strip().lower()}")
    if name:
        print(f"  Name:  {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed an admin user")
    parser.add_argument("--email", help="Admin email address")
    parser.add_argument("--name", help="Admin display name (optional)")
    args = parser.parse_args()

    email = args.email or input("Email: ").strip()
    if not email:
        print("Error: email is required.")
        sys.exit(1)

    password = getpass.getpass("Password: ")
    if not password:
        print("Error: password is required.")
        sys.exit(1)

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Error: passwords do not match.")
        sys.exit(1)

    name = args.name or input("Name (optional, press Enter to skip): ").strip() or None

    seed_admin(email=email, password=password, name=name)


if __name__ == "__main__":
    main()
