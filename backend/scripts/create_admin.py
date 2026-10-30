"""Create the first administrator in the NEW database, without a default password."""
import getpass
from sqlalchemy import select
from app.auth import hash_password
from app.db import SessionLocal
from app.models import User


def main():
    username = input("New administrator username: ").strip()
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if len(username) < 2 or len(username) > 30 or not username.isascii() or not all(c.isalnum() or c == "_" for c in username):
        raise SystemExit("Use 2–30 ASCII letters, numbers, or underscores.")
    if len(password) < 6 or password != confirm:
        raise SystemExit("Passwords must match and be at least 6 characters.")
    try:
        encoded = hash_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.role == "admin")) is not None:
            raise SystemExit("An administrator already exists; use account management instead.")
        if db.scalar(select(User.id).where(User.username == username)) is not None:
            raise SystemExit("Username already exists; it will not be elevated.")
        db.add(User(username=username, display_name=username, role="admin", password_hash=encoded))
        db.commit()
    print("Administrator created.")


if __name__ == "__main__":
    main()
