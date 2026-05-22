import os
import subprocess
import sys


def main() -> None:
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    app_path = os.path.join(base_dir, "streamlit_chat.py")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", app_path],
        check=True,
    )


if __name__ == "__main__":
    main()
