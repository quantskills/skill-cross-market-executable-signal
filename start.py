"""无需第三方包即可打开的中文入口。"""
import sys

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    from scripts.guide import main
    raise SystemExit(main())

