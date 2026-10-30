"""Read-only schema and row-count inventory; never changes the source database.

Set LEGACY_DATABASE_URL explicitly in the environment. Do not print the URL.
"""
import os
from sqlalchemy import create_engine, inspect, text

TABLES = ("sys_user", "sys_role", "sys_user_role", "ticket_info", "ticket_status_log", "ticket_image", "ticket_ai_result", "game_category", "knowledge_base", "chat_message", "hot_keyword_daily")


def main():
    url = os.getenv("LEGACY_DATABASE_URL")
    if not url:
        raise SystemExit("Set LEGACY_DATABASE_URL to the existing MySQL database.")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        if conn.dialect.name != "mysql":
            raise SystemExit("Only MySQL source databases are supported.")
        conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
        inspector = inspect(conn)
        names = set(inspector.get_table_names())
        for name in TABLES:
            if name not in names:
                print(f"{name}: absent")
                continue
            count = conn.execute(text(f"SELECT COUNT(*) FROM `{name}`")).scalar_one()
            fields = ", ".join(column["name"] for column in inspector.get_columns(name))
            print(f"{name}: {count} rows; columns: {fields}")
    engine.dispose()


if __name__ == "__main__":
    main()
