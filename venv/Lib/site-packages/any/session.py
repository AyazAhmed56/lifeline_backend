import datetime
import click
import logging
import os.path
import tempfile
import time

import ray
import ray.projects.scripts as ray_scripts

from any.models import (AppliedSnapshot, Session, Snapshot)
from any.snapshot import (create_snapshot)
from any.project import (get_project)
from any.util import confirm


logging.basicConfig(format=ray.ray_constants.LOGGER_FORMAT)
logger = logging.getLogger(__file__)


class AnyscaleSessionRunner(ray_scripts.SessionRunner):

    def __init__(self, db_session, session):
        """Create a new AnyscaleSessionRunner.

        Args:
            db_session: A connection to the database.
            session: The session associated to this run.
        """
        super(AnyscaleSessionRunner, self).__init__()

        self.db_session = db_session
        self.session = session

        # TODO: We should check that the active session actually matches the
        # created cluster. If the user terminates the cluster out-of-band (through
        # `ray down` instead of `any session stop`), then we won't be able to
        # tell that the session is no longer active.

        self.session_name = self.session.name


    def execute_command(self, cmd, config={}, user_command=False):
        """Execute a shell command in the session and add it to the database.

        Args:
            cmd (str): Shell command to run in the session. It will be
                run in the working directory of the project.
            user_command (bool): If True, the command was run explicitly
                by the user.
        """
        if user_command:
            self.session.add_command(cmd)
            self.db_session.commit()
        return super(AnyscaleSessionRunner, self).execute_command(cmd, config)


def sync_session(db_session, snapshot_name, session_runner, yes):
    """Sync a live session with a snapshot with the given name.

    Args:
        db_session: A connection to the database.
        snapshot_name: The name of the snapshot with which to sync the session.
        session_runner: The runner of the session.
        yes: Don't ask for confirmation.

    Returns:
        The active Session metadata in the database.

    Raises:
        ValueError: If snapshot lookup does not succeed.
    """
    project_definition = session_runner.project_definition
    project = get_project(db_session, project_definition.root)
    # If no snapshot was provided, create a snapshot.
    if snapshot_name is None:
        confirm("No snapshot for the session specified. Create a snapshot?", yes)
        # TODO: Give the snapshot a name and description that includes this
        # session's name.
        snapshot = create_snapshot(db_session, project_definition, yes, local=True)
    else:
        snapshots = project.snapshots.filter(Snapshot.name == snapshot_name).all()
        if len(snapshots) == 0:
            raise ValueError("No snapshots found with name {}".format(snapshot_name))
        snapshot_idx = 0
        if len(snapshots) > 1:
            print("More than one snapshot found with name {}. Which do you want to use?".format(snapshot_name))
            for i, snapshot in enumerate(snapshots):
                print("{}. {}".format(i + 1, snapshot))
            snapshot_idx = click.prompt('Please enter a snapshot number from 1 to {}'.format(len(snapshots)), type=int)
            snapshot_idx -= 1
            if snapshot_idx < 0 or snapshot_idx > len(snapshots):
                raise ValueError("Snapshot index {} is out of range".format(snapshot_idx))
        snapshot = snapshots[snapshot_idx]
    logger.info("Using snapshot %s", snapshot)

    # Sync the git state.
    tmp = tempfile.TemporaryFile()
    if snapshot.commit_hash is not None:
        # Point the repo to the commit associated with the snapshot.
        session_runner.execute_command(
            "git reset && git checkout . && git checkout {} && git clean -fxd".format(snapshot.commit_hash))
        # Apply the git diff to the session's project directory, if any.
        if snapshot.commit_patch_location is not None:
            tmp_patch = "/tmp/ray_patch_{}".format(time.time())
            ray_scripts.rsync(
                project_definition.cluster_yaml(),
                source=snapshot.commit_patch_location,
                target=tmp_patch,
                override_cluster_name=session_runner.session_name,
                down=False,
            )
            session_runner.execute_command(
                    "git apply {}".format(tmp_patch))

    # NOTE: If the project is not using git, then we will sync all snapshot
    # files correctly, but there may be other files left in the project
    # directory. We can remove them with `rm -r *`, but this seems fishy.

    # Sync the files in the snapshot with the session.
    for project_file in snapshot.project_files:
        if not project_file.sync:
            continue
        source = project_file.location
        target = os.path.join(project_definition.working_directory(), project_file.pathname)
        if os.path.isdir(source):
            if not source.endswith("/"):
                source += "/"
            if not target.endswith("/"):
                target += "/"
        ray_scripts.rsync(
            project_definition.cluster_yaml(),
            source=source,
            target=target,
            override_cluster_name=session_runner.session_name,
            down=False,
        )

    session_runner.session.apply_snapshot(snapshot)
    db_session.commit()


def list_sessions(db_session, project_dir):
    """List all sessions, active or inactive.

    Args:
        db_session: A connection to the database.
        project_dir: The project root.

    Returns:
        A list of Session metadata entries for the current project.
    """
    # Find and validate the current project ID.
    project = get_project(db_session, project_dir)
    return project.sessions.order_by(Session.created_at.desc()).all()
