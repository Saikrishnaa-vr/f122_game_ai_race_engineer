"""Syntax-check inline dashboard JavaScript with Node.js."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PAGES = ("dashboard.html", "practice.html", "replay_library.html")
SCRIPT = re.compile(r"<script(?:\s[^>]*)?>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)


def main() -> None:
    checked = 0
    with tempfile.TemporaryDirectory(prefix="js-check-", dir=ROOT) as temp_dir:
        for page_name in PAGES:
            page = ROOT / "app" / page_name
            scripts = SCRIPT.findall(page.read_text(encoding="utf-8"))
            if not scripts:
                raise RuntimeError(f"No inline JavaScript found in {page}")
            for index, source in enumerate(scripts, start=1):
                path = Path(temp_dir) / f"{page.stem}-{index}.js"
                path.write_text(source, encoding="utf-8")
                subprocess.run(["node", "--check", str(path)], check=True)
                checked += 1
    print(f"Checked {checked} inline JavaScript blocks in {len(PAGES)} pages")


if __name__ == "__main__":
    main()
