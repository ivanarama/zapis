"""One Windows executable for the desktop UI and the agent CLI."""
import multiprocessing
import runpy
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if args == ["--serve-video"]:
        from backend.marketing.launch import main as serve

        sys.argv = [sys.argv[0], "--no-browser"]
        serve()
        return 0
    if args and args != ["--gui"]:
        from backend.cli.__main__ import main as cli

        return cli(args)
    runpy.run_module("main", run_name="__main__")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
