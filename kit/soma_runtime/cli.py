"""JSON command line for a cooperatively instrumented local session."""

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

from .core import apply, create_session, journal, observe, read_json


class _Help(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)

    def print_help(self, file=None):
        raise _Help(self.format_help())


def _argument_json(value):
    if value.lstrip().startswith("{"):
        return read_json(value)
    filename = value[1:] if value.startswith("@") else value
    return read_json(Path(filename).read_text(encoding="utf-8-sig"))


def _parser():
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("session", type=Path)
    init.add_argument("--adapter", required=True)
    init.add_argument("--inputs", required=True)
    init.add_argument("--config", required=True)
    init.add_argument("--max-actions", type=int, required=True)
    read = commands.add_parser("observe")
    read.add_argument("session", type=Path)
    read.add_argument("--masked", action="store_true")
    action = commands.add_parser("apply")
    action.add_argument("session", type=Path)
    action.add_argument("--request", required=True)
    history = commands.add_parser("journal")
    history.add_argument("session", type=Path)
    return parser


def _dispatch(args):
    if args.command == "init":
        return create_session(
            args.session, args.adapter, _argument_json(args.inputs), _argument_json(args.config), args.max_actions
        )
    if args.command == "observe":
        return observe(args.session, args.masked)
    if args.command == "apply":
        return apply(args.session, _argument_json(args.request))
    return journal(args.session)


def main(argv=None):
    try:
        with redirect_stdout(sys.stderr):
            result = _dispatch(_parser().parse_args(argv))
        output = json.dumps(result, allow_nan=False)
    except _Help as exc:
        print(json.dumps({"help": str(exc)}))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, allow_nan=False))
        return 1
    print(output)
    return 0
