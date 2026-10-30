import os

# Tests never start the development worker or use the local migrated database.
os.environ["LOCAL_MODE"] = "false"
os.environ["LOCAL_AUTO_TASKS"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
