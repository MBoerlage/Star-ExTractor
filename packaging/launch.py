"""PyInstaller entry point for the frozen app (StarExtractor.exe)."""
import sys

from starex_reduce.app import main

if __name__ == "__main__":
    sys.exit(main())
