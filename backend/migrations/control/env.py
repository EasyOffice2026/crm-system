from alembic import context

from app.control import ControlBase

target_metadata = ControlBase.metadata


def run() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("Run migrations through python -m app.migrate")
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


run()
