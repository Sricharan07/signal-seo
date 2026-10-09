#!/usr/bin/env python3
"""Run the isolated real-network crawler boundary qualification."""

from crawler_network_lab import LabError, main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LabError as error:
        print(str(error))
        raise SystemExit(1) from None
