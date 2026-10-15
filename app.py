import os
import sys

if getattr(sys, "frozen", False):
    # Les rutes relatives (data/, output/) han de quedar al costat de l'executable.
    os.chdir(os.path.dirname(os.path.abspath(sys.executable)))

from src.app import main


if __name__ == "__main__":
    main()
