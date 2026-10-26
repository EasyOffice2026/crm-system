import argparse
import getpass
import json
import os
import sys

from pydantic import ValidationError
from sqlalchemy import select

from .control import Workspace, control_sessions
from .provisioning import (
    ErpPlatform,
    ProvisionError,
    ProvisionRequest,
    reissue_invite,
    rotate_credential,
    run_steps,
    suspend_workspace,
)


def operator() -> str:
    return f"cli:{os.environ.get('CRM_PROVIDER_OPERATOR') or getpass.getuser()}"


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("CRM_TENANCY") != "hosted":
        print("Provider commands require CRM_TENANCY=hosted", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="python -m app.provider")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision", help="Create or resume a client workspace")
    provision.add_argument("--request-id", required=True)
    provision.add_argument("--slug", required=True)
    provision.add_argument("--company-name", required=True)
    provision.add_argument("--company-name-ar", default="")
    provision.add_argument("--legal-name", required=True)
    provision.add_argument("--cr-number", required=True)
    provision.add_argument("--fy-start", required=True)
    provision.add_argument("--fy-end-month", type=int, default=12)
    provision.add_argument("--admin-email", required=True)
    provision.add_argument("--admin-name", default="Workspace Admin")
    commands.add_parser("process-pending", help="Resume workspaces requested through the provider API")
    rotate = commands.add_parser("rotate-credential", help="Rotate the ERP service credential")
    rotate.add_argument("workspace")
    suspend = commands.add_parser("suspend", help="Suspend a workspace in CRM and ERP")
    suspend.add_argument("workspace")
    invite = commands.add_parser("invite", help="Issue a new one-time invitation link")
    invite.add_argument("workspace")
    invite.add_argument("email")
    commands.add_parser("list", help="List workspaces")
    args = parser.parse_args(argv)
    actor = operator()
    erp = ErpPlatform()
    try:
        if args.command == "provision":
            from .provisioning import provision_workspace

            request = ProvisionRequest(
                request_id=args.request_id,
                slug=args.slug,
                company_name=args.company_name,
                company_name_ar=args.company_name_ar,
                legal_name=args.legal_name,
                cr_number=args.cr_number,
                fy_start=args.fy_start,
                fiscal_year_end_month=args.fy_end_month,
                admin_email=args.admin_email,
                admin_name=args.admin_name,
            )
            result = provision_workspace(request, actor, erp, os.environ.get("CRM_ADMIN_PASSWORD") or None)
            print(json.dumps(result, indent=2))
        elif args.command == "process-pending":
            with control_sessions()() as control:
                ids = list(
                    control.scalars(
                        select(Workspace.id).where(Workspace.status.in_(("provisioning", "failed")))
                    )
                )
            failures = 0
            for identifier in ids:
                try:
                    print(json.dumps(run_steps(identifier, actor, erp), indent=2))
                except ProvisionError as exc:
                    failures += 1
                    print(f"{identifier}: {exc.code} {exc.message}", file=sys.stderr)
            return 1 if failures else 0
        elif args.command == "rotate-credential":
            print(json.dumps(rotate_credential(args.workspace, actor, erp), indent=2))
        elif args.command == "suspend":
            print(json.dumps(suspend_workspace(args.workspace, actor, erp), indent=2))
        elif args.command == "invite":
            print(reissue_invite(args.workspace, args.email, actor))
        else:
            with control_sessions()() as control:
                for workspace in control.scalars(select(Workspace).order_by(Workspace.slug)):
                    print(f"{workspace.slug}\t{workspace.status}\t{workspace.provision_step}\t{workspace.id}")
    except ValidationError as exc:
        print(f"Invalid request: {exc.errors(include_url=False, include_input=False)}", file=sys.stderr)
        return 2
    except ProvisionError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
