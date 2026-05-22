import subprocess
import sys


def main() -> None:
    subprocess.run(
        [sys.executable, "chat_api.py"],
        check=True,
    )


if __name__ == "__main__":
    main()
