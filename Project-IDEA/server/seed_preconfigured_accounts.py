import json
import os

from platform_auth import PlatformStore


def main() -> None:
    raw_seed = os.environ.get("IDEA_PRECONFIGURED_ACCOUNTS_JSON")
    if not raw_seed:
        raise RuntimeError("IDEA_PRECONFIGURED_ACCOUNTS_JSON is required")
    seed = json.loads(raw_seed)
    if not isinstance(seed, list):
        raise ValueError("IDEA_PRECONFIGURED_ACCOUNTS_JSON must be a JSON list")
    db_path = os.environ.get("IDEA_PLATFORM_DB_PATH", os.path.join(os.path.dirname(__file__), "memory", "platform.db"))
    PlatformStore(db_path).seed_preconfigured_accounts(seed)


if __name__ == "__main__":
    main()
