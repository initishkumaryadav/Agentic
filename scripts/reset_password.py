
import sys
from pathlib import Path

projectRoot = Path(__file__).resolve().parent.parent
if str(projectRoot) not in sys.path:
    sys.path.insert(0, str(projectRoot))

from dotenv import load_dotenv

load_dotenv(projectRoot / ".env")

from src.auth import AuthError, config, init_db, passwords, users  # noqa: E402  (after .env is loaded)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 2
    init_db()
    try:
        code = passwords.issue_reset_code(sys.argv[1])
    except AuthError as e:
        print(f"Error: {e}")
        return 1
    print(f"Reset code for '{users.normalize(sys.argv[1])}': {passwords.format_code(code)}")
    print(f"It works once and expires in {config.RESET_CODE_MINUTES} minutes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
