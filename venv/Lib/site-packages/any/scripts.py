import click
import fnmatch
import json
import jsonschema
import logging
import os
import sys

from ray.projects import ProjectDefinition
import ray.projects.scripts as ray_scripts
import ray.ray_constants
from ray.scripts.scripts import cli

import any
import any.conf
from any.models import (DbSession, Project, Session)
from any.session import (
    AnyscaleSessionRunner, list_sessions, sync_session)
from any.project import (PROJECT_ID_BASENAME, get_project, list_projects)


logging.basicConfig(format=ray.ray_constants.LOGGER_FORMAT)
logger = logging.getLogger(__file__)

db_session = DbSession()


def load_project_or_throw():
    # Validate the project file
    try:
        project = ray.projects.ProjectDefinition(os.getcwd())
        dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(dir, "anyscale_schema.json")) as f:
            schema = json.load(f)
        jsonschema.validate(instance=project.config, schema=schema)
    except (jsonschema.exceptions.ValidationError, ValueError) as e:
        raise click.ClickException(e)

    return project


@click.group(
    "snapshot", help="Commands for working with snapshot")
def snapshot_cli():
    pass


@ray_scripts.project_cli.command(
    name="validate",
    help="Validate current project spec")
@click.option(
    "--verbose", help="If set, print the validated file", is_flag=True)
def project_validate(verbose):
    project = load_project_or_throw()
    print("Project files validated!", file=sys.stderr)
    if verbose:
        print(project.config)


@ray_scripts.project_cli.command(
    name="create",
    help="Create a new project within current directory. If the project name "
         "is not provided, this will register the existing project.")
@click.argument("project_name", required=False)
@click.option(
    "--cluster-yaml",
    help="Path to autoscaler yaml. Created by default",
    default=None)
@click.option(
    "--requirements",
    help="Path to requirements.txt. Created by default",
    default=None)
@click.pass_context
def project_create(ctx, project_name, cluster_yaml, requirements):
    project_id_path = os.path.join(ray_scripts.PROJECT_DIR, PROJECT_ID_BASENAME)

    if project_name:
        # Call the normal `ray project create` command.
        ctx.forward(ray_scripts.create)

    project_definition = load_project_or_throw()
    project_name = project_definition.config["name"]
    # Add a description of the output_files parameter to the project yaml.
    if not "output_files" in project_definition.config:
        with open(ray_scripts.PROJECT_YAML, 'a') as f:
            f.write("\n".join([
                "",
                "# Pathnames for files and directories that should be saved in a snapshot but",
                "# that should not be synced with a session. Pathnames can be relative to the",
                "# project directory or absolute. Generally, this should be files that were",
                "# created by an active session, such as application checkpoints and logs.",
                "output_files: [",
                "  # For example, uncomment this to save the logs from the last ray job.",
                "  # \"/tmp/ray/session_latest\",",
                "]",
                ]))

    if os.path.exists(project_id_path):
        raise click.ClickException("This project has already been registered")

    # Add a local database entry for the new Project.
    project = Project(project_name)
    db_session.add(project)
    db_session.commit()
    with open(project_id_path, 'w+') as f:
        f.write(str(project.id))


@ray_scripts.project_cli.command(name="list", help="List all projects currently registered")
@click.pass_context
def project_list(ctx):
    projects = list_projects(db_session)
    print("Projects:")
    for project in projects:
        print(project)


@snapshot_cli.command(name="create", help="Create a snapshot of the current project")
@click.option(
    "--name",
    help="Name of the snapshot. If none provided, this will be randomly generated.",
    default=None)
@click.option(
    "--description",
    help="A description of the snapshot",
    default=None)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Don't ask for confirmation.")
def snapshot_create(name, description, yes):
    project_definition = load_project_or_throw()

    try:
        any.snapshot.create_snapshot(db_session, project_definition, yes, name=name, description=description, local=True)
    except click.Abort as e:
        raise e
    except Exception as e:
        # Creating a snapshot can fail if the project is not found or if some
        # files cannot be copied (e.g., due to permissions).
        raise click.ClickException(e)

@snapshot_cli.command(name="delete", help="Delete a snapshot of the current project with the given name")
@click.argument("name")
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Don't ask for confirmation.")
def snapshot_delete(name, yes):
    project_definition = load_project_or_throw()

    try:
        any.snapshot.delete_snapshot(db_session, project_definition.root, name, yes)
    except click.Abort as e:
        raise e
    except Exception as e:
        # Deleting a snapshot can fail if the project is not found.
        raise click.ClickException(e)


@snapshot_cli.command(name="list", help="List all snapshots of the current project")
def snapshot_list():
    project_definition = load_project_or_throw()

    try:
        snapshots = any.snapshot.list_snapshots(db_session, project_definition.root)
    except Exception as e:
        # Listing snapshots can fail if the project is not found.
        raise click.ClickException(e)

    if len(snapshots) == 0:
        print("No snapshots found.")
    else:
        print("Project snaphots:")
        for snapshot in snapshots:
            print(" {}".format(snapshot))


@ray_scripts.session_cli.command(
    name="start",
    context_settings=dict(ignore_unknown_options=True, ),
    help="Start a session based on current project config")
@click.argument("command", required=False)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--shell", help="If set, run the command as a raw shell command instead of looking up the command in the project.yaml.", is_flag=True)
@click.option(
    "--snapshot-name", help="If set, start the session from the given snapshot name.", default=None)
@click.option(
    "--name", help="A name to tag the session with.", default=None)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Don't ask for confirmation. Confirmation is needed when no snapshot name is provided.")
def session_start(command, args, shell, snapshot_name, name, yes):
    project_definition = load_project_or_throw()

    if not name:
        name = project_definition.config["name"]

    # Get the actual command to run. This also validates the command,
    # which should be done before the cluster is started.
    try:
        command, parsed_args, config = project_definition.get_command_info(
            command, args, shell, wildcards=True)
    except ValueError as e:
        raise click.ClickException(e)
    session_runs = ray_scripts.get_session_runs(name, command, parsed_args)

    if len(session_runs) > 1 and not config.get("tmux", False):
        logging.info("Using wildcards with tmux = False would not create "
                     "sessions in parallel, so we are overriding it with "
                     "tmux = True.")
        config["tmux"] = True

    project = get_project(db_session, os.getcwd())

    for run in session_runs:

        sessions = project.sessions.filter(Session.active).filter(Session.name == run["name"]).all()
        if len(sessions) == 0:
            session = Session(project.id, name=run["name"])
            db_session.add(session)
        else:
            raise click.ClickException("Session with name {} already exists".format(run["name"]))

        runner = AnyscaleSessionRunner(db_session, session)

        logger.info("[1/{}] Creating cluster".format(run["num_steps"]))
        runner.create_cluster()
        logger.info("[2/{}] Syncing the project".format(run["num_steps"]))
        runner.sync_files()
        logger.info("[3/{}] Setting up environment".format(run["num_steps"]))
        runner.setup_environment()

        # Sync the session with the given snapshot or from the current state if no
        # snapshot is specified.
        try:
            sync_session(db_session, snapshot_name, runner, yes)
        except click.Abort as e:
            raise e
        except Exception as e:
            raise click.ClickException(e)

        if command:
            # Run the actual command.
            logger.info("[4/4] Running command")
            runner.execute_command(run["command"], config, user_command=True)


@ray_scripts.session_cli.command(
    name="sync",
    help="Synchronize a session with a snapshot")
@click.option(
    "--snapshot-name", help="The snapshot the session should be synchronized with.", default=None)
@click.option(
    "--name", help="The name of the session to synchronize.", default=None)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Don't ask for confirmation. Confirmation is needed when no snapshot name is provided.")
def session_sync(snapshot_name, name, yes):
    project = get_project(db_session, os.getcwd())
    sessions = project.find_sessions(name)

    if len(sessions) == 0:
        raise click.ClickException("No active session matching pattern {} found".format(name))

    for session in sessions:
        runner = AnyscaleSessionRunner(db_session, session)

        try:
            sync_session(db_session, snapshot_name, runner, yes)
        except click.Abort as e:
            raise e
        except Exception as e:
            raise click.ClickException(e)


@ray_scripts.session_cli.command(
    name="execute",
    context_settings=dict(ignore_unknown_options=True, ),
    help="Execute a command in a session")
@click.argument("command", required=False)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--shell", help="If set, run the command as a raw shell command instead of looking up the command in the project.yaml.", is_flag=True)
@click.option(
    "--name", help="Name of the session to run this command on",
    default=None
)
def session_execute(command, args, shell, name):
    project_definition = load_project_or_throw()
    try:
        command, parsed_args, config = project_definition.get_command_info(
            command, args, shell, wildcards=False)
    except ValueError as e:
        raise click.ClickException(e)

    project = get_project(db_session, os.getcwd())
    sessions = project.find_sessions(name)

    if len(sessions) == 0:
        raise click.ClickException("No active session matching pattern {} found".format(name))

    command = ray_scripts.format_command(command, parsed_args)

    for session in sessions:
        runner = AnyscaleSessionRunner(db_session, session)
        runner.execute_command(command, user_command=True)


@ray_scripts.session_cli.command(name="stop", help="Stop a session based on current project config")
@click.option(
    "--name", help="Name of the session to stop",
    default=None
)
@click.pass_context
def session_stop(ctx, name):
    project = get_project(db_session, os.getcwd())
    sessions = project.find_sessions(name)
    if len(sessions) == 0:
        raise click.ClickException("No active session matching pattern {} found".format(name))

    for session in sessions:

        ctx.params['name'] = session.name
        ctx.forward(ray_scripts.stop)

        # Mark the session as stopped in the database.
        session.active = False
        db_session.commit()

@ray_scripts.session_cli.command(name="list", help="List all sessions.")
@click.option(
    "--name",
    help="Name of the session. If provided, this prints the snapshots that were applied and commands that ran for all sessions that match this name.",
    default=None)
def session_list(name):
    project_definition = load_project_or_throw()

    try:
        sessions = list_sessions(db_session, project_definition.root)
    except Exception as e:
        # Listing snapshots can fail if the project is not found.
        raise click.ClickException(e)

    if name is None:
        print("Project sessions:")
        for session in sessions:
            fmt = " {}"
            if session.active:
                fmt = "*{}"
            print(fmt.format(session))
    else:
        sessions = [session for session in sessions if session.name == name]
        for session in sessions:
            print()
            print("Snapshots applied to", session)
            for applied_snapshot in session.applied_snapshots:
                print(" {}: {}".format(
                    applied_snapshot.created_at,
                    applied_snapshot.snapshot,
                    ))
            print("Commands run during", session)
            for command in session.commands:
                print(" {}: '{}'".format(
                    command.created_at,
                    command.command,
                    ))


cli.add_command(ray_scripts.project_cli)
cli.add_command(ray_scripts.session_cli)
cli.add_command(snapshot_cli)


def main():
    return cli()

if __name__ == '__main__':
    main()
