import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, select

ROOT = Path(__file__).resolve().parent.parent / "migrations"
BASELINE = "0001_baseline"


def config(kind: str) -> Config:
    result = Config()
    result.set_main_option("script_location", str(ROOT / kind))
    return result


def upgrade(engine: Engine, kind: str = "workspace") -> None:
    alembic = config(kind)
    with engine.begin() as connection:
        alembic.attributes["connection"] = connection
        command.upgrade(alembic, "head")


def stamp_existing(engine: Engine) -> bool:
    tables = set(inspect(engine).get_table_names())
    if "alembic_version" in tables or "crm_settings" not in tables:
        return False
    alembic = config("workspace")
    with engine.begin() as connection:
        alembic.attributes["connection"] = connection
        command.stamp(alembic, BASELINE)
    return True


def prepare_single(engine: Engine) -> None:
    stamp_existing(engine)
    upgrade(engine)


def all_workspaces() -> int:
    from .control import Workspace, control_sessions
    from .tenancy import dispose_workspace, make_workspace_engine, workspace_database_url

    with control_sessions()() as control:
        workspaces = list(
            control.scalars(select(Workspace).where(Workspace.status == "active").order_by(Workspace.slug))
        )
    for workspace in workspaces:
        engine = make_workspace_engine(workspace_database_url(workspace))
        try:
            upgrade(engine)
            print(f"{workspace.slug}: upgraded")
        except Exception as exc:
            print(f"{workspace.slug}: FAILED {type(exc).__name__}", file=sys.stderr)
            return 1
        finally:
            engine.dispose()
            dispose_workspace(workspace.id)
    print(f"{len(workspaces)} workspace(s) at head")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.migrate")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stamp-existing", action="store_true", help="Stamp a pre-Alembic single database")
    group.add_argument("--single", action="store_true", help="Stamp if needed and upgrade CRM_DATABASE_URL")
    group.add_argument("--control", action="store_true", help="Upgrade the hosted control database")
    group.add_argument("--all-workspaces", action="store_true", help="Upgrade every active workspace")
    args = parser.parse_args(argv)
    if args.stamp_existing:
        from .db import engine

        print("stamped 0001_baseline" if stamp_existing(engine) else "nothing to stamp")
        return 0
    if args.single:
        from .db import engine

        prepare_single(engine)
        return 0
    if args.control:
        from .control import control_engine

        upgrade(control_engine(), "control")
        return 0
    return all_workspaces()


if __name__ == "__main__":
    raise SystemExit(main())
