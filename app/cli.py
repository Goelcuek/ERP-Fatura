import os

import click

from .extensions import db
from .models import User


def register_cli(app):
    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--name", default="", help="Full name")
    @click.option("--admin", is_flag=True, help="Give admin rights")
    @click.password_option()
    def create_user(username, name, admin, password):
        """Create a user (or reset an existing user's password)."""
        u = User.query.filter_by(username=username.lower()).first() or User(username=username.lower())
        u.full_name = name or u.full_name or username
        if admin:
            u.role = "admin"
        u.active = True
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"User '{u.username}' saved.")

    @app.cli.command("backup")
    @click.option("--dest", default=None, help="Folder to write the backup to")
    def backup_cmd(dest):
        """Create a backup zip now."""
        from .services.backup import copy_to_extra, create_backup

        path = create_backup(app, label="cli", dest_dir=dest)
        copy_to_extra(path, app)
        click.echo(path)

    @app.cli.command("restore")
    @click.argument("zip_path", type=click.Path(exists=True))
    @click.confirmation_option(prompt="This replaces ALL current data. Continue?")
    def restore_cmd(zip_path):
        """Restore a backup zip (a safety backup is taken first)."""
        from .services.backup import restore

        manifest, safety = restore(zip_path, app)
        click.echo(f"Restored backup from {manifest.get('created_at')}. Previous data saved to {safety}")

    @app.cli.command("seed-demo")
    @click.option("--force", is_flag=True, help="Seed even if data exists")
    def seed_demo(force):
        """Fill the database with realistic demo data (demo / demo1234)."""
        from .demo import seed

        if User.query.count() and not force:
            raise click.ClickException("Database is not empty. Use --force to add demo data anyway.")
        seed()
        click.echo("Demo data created. Log in with demo / demo1234")

    @app.cli.command("info")
    def info():
        """Show where data and backups live."""
        from .services.backup import backup_dir

        click.echo(f"Data folder:   {app.config['DATA_DIR']}")
        click.echo(f"Database:      {os.path.join(app.config['DATA_DIR'], 'erp.db')}")
        click.echo(f"Backup folder: {backup_dir(app)}")
