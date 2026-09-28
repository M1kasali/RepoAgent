"""Product entry point; the previous runtime is available only as ``compat``."""

import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "compat":
        from .cli import main as compatibility_main

        return compatibility_main(args[1:])
    if args and args[0] == "issue":
        # The maintainer application retains its frozen Case/worker protocol.
        from .cli import run_product_command

        return run_product_command(args)
    from .harness.cli.commands import app, run

    app.info.epilog = (
        "Compatibility: repoagent compat --help (previous runtime); "
        "repoagent issue --help (existing maintainer Case workflow)."
    )

    previous = sys.argv
    try:
        sys.argv = ["repoagent", *args]
        run()
    finally:
        sys.argv = previous
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
