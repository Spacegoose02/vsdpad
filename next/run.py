"""Start VSD Pad 2:  python run.py --gui      (see README.md)"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vsdpad.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
