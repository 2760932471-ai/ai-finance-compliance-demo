"""Build the public mirror. The old no-argument entry point remains supported."""
from pathlib import Path
import sys

from build_public_preview.build import main


if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append(str(Path(__file__).resolve().parents[2] / "finance-web-preview"))
    main()
