"""Command line for the same pipeline the web workbench uses."""

from __future__ import annotations

import argparse
import os
import sys

from intake.access import AccessContext
from intake.errors import AccessDenied, AuthRequired, GateFailed, IntakeError
from intake.service import IncomingFile, IntakeService


def db_path() -> str:
    return os.environ.get("INTAKE_DB", "data/intake.db")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Source-linked matter intake review")
    parser.add_argument("--db", default=None, help="SQLite file. Defaults to INTAKE_DB or data/intake.db")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create the database and demo users")
    sub.add_parser("serve", help="Run the local web workbench")

    load = sub.add_parser("load-samples", help="Load the three fictional bundles")
    _auth(load)

    listing = sub.add_parser("list", help="List matters for a user")
    _auth(listing)

    show = sub.add_parser("show", help="Show documents, facts, and flags")
    _auth(show)
    show.add_argument("--matter", required=True)

    register = sub.add_parser("register", help="Register a 2-4 file bundle")
    _auth(register)
    register.add_argument("--title", required=True)
    register.add_argument("--file", action="append", required=True, help="Path to a file. Repeat for each file.")
    register.add_argument("--type", action="append", default=[], help="Document type for each file, in order")
    register.add_argument("--channel", action="append", default=[], help="Channel for each file, in order")

    add = sub.add_parser("add-file", help="Add a later file to a matter")
    _auth(add)
    add.add_argument("--matter", required=True)
    add.add_argument("--file", required=True)
    add.add_argument("--type", default=None)
    add.add_argument("--channel", default="upload")

    accept = sub.add_parser("accept", help="Accept one candidate")
    _auth(accept)
    accept.add_argument("--matter", required=True)
    accept.add_argument("--fact", required=True)
    accept.add_argument("--reason", required=True)

    edit = sub.add_parser("edit", help="Correct a candidate and approve the new value")
    _auth(edit)
    edit.add_argument("--matter", required=True)
    edit.add_argument("--fact", required=True)
    edit.add_argument("--value", required=True)
    edit.add_argument("--reason", required=True)

    reject = sub.add_parser("reject", help="Reject a candidate")
    _auth(reject)
    reject.add_argument("--matter", required=True)
    reject.add_argument("--fact", required=True)
    reject.add_argument("--reason", required=True)

    dispose = sub.add_parser("dispose", help="Dispose a flag")
    _auth(dispose)
    dispose.add_argument("--matter", required=True)
    dispose.add_argument("--flag", required=True)
    dispose.add_argument("--disposition", required=True)
    dispose.add_argument("--reason", required=True)

    ready = sub.add_parser("ready", help="Mark ready for attorney if the gate passes")
    _auth(ready)
    ready.add_argument("--matter", required=True)
    ready.add_argument("--reason", required=True)

    gate = sub.add_parser("gate", help="Run the quality gate without changing status")
    _auth(gate)
    gate.add_argument("--matter", required=True)

    report = sub.add_parser("report", help="Print the seven-section report")
    _auth(report)
    report.add_argument("--matter", required=True)
    report.add_argument("--audience", choices=("staff", "attorney"), default="attorney")

    ack = sub.add_parser("acknowledge", help="Attorney acknowledgment. Does not change facts.")
    _auth(ack)
    ack.add_argument("--matter", required=True)

    returned = sub.add_parser("return-report", help="Attorney sends the report back")
    _auth(returned)
    returned.add_argument("--matter", required=True)
    returned.add_argument("--reason", required=True)
    return parser


def _auth(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.db:
        os.environ["INTAKE_DB"] = args.db
    service = IntakeService(db_path())
    service.init_db()
    try:
        return _run(service, args)
    except (AuthRequired, AccessDenied) as exc:
        print(exc, file=sys.stderr)
        return 2
    except GateFailed as exc:
        print("Ready is blocked:", file=sys.stderr)
        for reason in exc.reasons:
            print("- " + reason, file=sys.stderr)
        return 3
    except IntakeError as exc:
        print(exc, file=sys.stderr)
        return 4


def _run(service: IntakeService, args) -> int:
    if args.command == "init":
        print(f"Database ready at {db_path()}")
        return 0
    if args.command == "serve":
        from intake.web import main as serve

        serve()
        return 0

    user = service.authenticate(args.user, args.password)
    if user is None:
        print("Sign-in failed", file=sys.stderr)
        return 2
    ctx = AccessContext(user["user_id"], user["role"], None)

    if args.command == "load-samples":
        for item in service.load_samples(ctx):
            print(f"{item['matter_id']}\t{item['key']}\t{item['title']}")
        return 0
    if args.command == "list":
        for matter in service.list_matters(ctx):
            print(f"{matter['matter_id']}\t{matter['status']}\t{matter['title']}")
        return 0

    ctx = AccessContext(user["user_id"], user["role"], args.matter) if getattr(args, "matter", None) else ctx

    if args.command == "register":
        files = []
        for index, path in enumerate(args.file):
            doc_type = args.type[index] if index < len(args.type) else None
            channel = args.channel[index] if index < len(args.channel) else "upload"
            with open(path, "rb") as handle:
                files.append(IncomingFile(os.path.basename(path), handle.read(), doc_type, channel))
        matter_id = service.register_bundle(ctx, args.title, files, extract_now=True)
        print(matter_id)
        return 0
    if args.command == "add-file":
        with open(args.file, "rb") as handle:
            incoming = IncomingFile(os.path.basename(args.file), handle.read(), args.type, args.channel)
        document_id = service.add_document(ctx, args.matter, incoming)
        print(document_id)
        matter = service.get_matter(ctx, args.matter)
        print(matter["status"] if matter else "")
        return 0
    if args.command == "show":
        matter = service.get_matter(ctx, args.matter)
        if matter is None:
            print("Matter not found", file=sys.stderr)
            return 2
        print(f"{matter['matter_id']}  {matter['status']}  {matter['title']}")
        print("-- documents")
        for doc in service.list_documents(ctx, args.matter):
            print(f"{doc['document_id']} v{doc['version']} {doc['status']} {doc['filename']} {doc['reason'] or ''}")
        print("-- facts")
        for fact in service.list_facts(ctx, args.matter, "working"):
            print(f"{fact['fact_id']} {fact['status']} {fact['fact_type']} {fact['value']}")
        print("-- flags")
        for flag in service.list_flags(ctx, args.matter):
            if not flag["active"]:
                continue
            print(f"{flag['flag_id']} {flag['flag_type']} {flag['fact_type']} disposition={flag['disposition']}")
        return 0
    if args.command == "accept":
        service.accept_fact(ctx, args.matter, args.fact, args.reason)
        print("accepted")
        return 0
    if args.command == "edit":
        service.edit_fact(ctx, args.matter, args.fact, args.value, args.reason)
        print("edited")
        return 0
    if args.command == "reject":
        service.reject_fact(ctx, args.matter, args.fact, args.reason)
        print("rejected")
        return 0
    if args.command == "dispose":
        service.dispose_flag(ctx, args.matter, args.flag, args.disposition, args.reason)
        print("disposed")
        return 0
    if args.command == "gate":
        result = service.evaluate_gate(ctx, args.matter)
        print("pass" if result["passed"] else "fail")
        for reason in result["reasons"]:
            print("- " + reason)
        return 0 if result["passed"] else 3
    if args.command == "ready":
        result = service.mark_ready(ctx, args.matter, args.reason)
        print(result["status"])
        return 0
    if args.command == "report":
        report = service.compose_report(ctx, args.matter, args.audience)
        print(report["text"])
        return 0
    if args.command == "acknowledge":
        print(service.acknowledge(ctx, args.matter))
        return 0
    if args.command == "return-report":
        print(service.return_report(ctx, args.matter, args.reason))
        return 0
    print("Unknown command", file=sys.stderr)
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
