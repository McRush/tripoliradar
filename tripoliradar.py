"""TripoliRadar: the F-19 Stealth Fighter viewer, starting at its main menu (tools/view.py takes the same arguments)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))

from view import main   # noqa: E402

if __name__ == '__main__':
    main(sys.argv[1:])
