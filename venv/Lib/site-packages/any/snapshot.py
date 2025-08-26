import logging
import os
import shutil
import tempfile
import time

from git import Git
import ray.ray_constants

import any.conf
from any.models import (AppliedSnapshot,
        FileMetadata, Project,
        Snapshot, Session,)
from any.project import (get_project)
from any.util import confirm


logging.basicConfig(format=ray.ray_constants.LOGGER_FORMAT)
logger = logging.getLogger(__file__)

# ~/.ray-snapshots holds all files for snapshots taken locally.
COMMIT_PATCH_BASENAME = 'patch'

def get_snapshot_directory(project_id, snapshot_id):
    """Get the directory location for snapshot data files.

    Args:
        project_id: The ID of the project that this snapshot is of.
        snapshot_id: The ID of the snapshot.

    Returns:
        Directory location for snapshot data files.
    """
    snapshot_directory = os.path.join(
            any.conf.SNAPSHOT_DIRECTORY,
            "project-{}".format(project_id),
            "snapshot-{}".format(snapshot_id))
    return snapshot_directory

def create_snapshot(db_session, project_definition, yes, name=None, description=None, local=False):
    """Create a snapshot of a project.

    Args:
        db_session: A connection to the database.
        project_dir: Project root directory.
        yes: Don't ask for confirmation.
        name: An optional name to label the snapshot.
        local: Whether the snapshot should be of a live session or
            the local project directory state.
        description: An optional description of the snapshot.

    Raises:
        ValueError: If the current project directory does not match the project
            metadata entry in the database.
        Exception: If saving the snapshot files fails.
    """
    if not local:
        raise NotImplementedError("Snapshots of a live session not currently supported.")

    # Find and validate the current project ID.
    project_dir = project_definition.root
    project = get_project(db_session, project_dir)
    # TODO: For snapshots of a session, parent_snapshot should be set to the
    # last snapshot that was applied to the session.
    parent_snapshot = project.snapshots.order_by(Snapshot.created_at.desc()).first()
    parent_snapshot_id = parent_snapshot.id if parent_snapshot else None

    duplicate_snapshot = project.snapshots.filter(Snapshot.name == name).first()
    if duplicate_snapshot is not None:
        confirm("Snapshot with name {} already exists. Continue?".format(name), yes)

    # Create a snapshot entry.
    snapshot = Snapshot(
            project.id,
            parent_snapshot_id,
            name=name,
            description=description)
    db_session.add(snapshot)
    db_session.commit()
    # Create a snapshot directory for all saved files using the newly assigned
    # ID.
    snapshot_directory = get_snapshot_directory(project.id, snapshot.id)
    os.makedirs(snapshot_directory)

    # Get the output files to save with the snapshot.
    output_files = []
    missing_output_files = []
    for output_file in project_definition.config.get('output_files', []):
        if os.path.exists(os.path.join(project_dir, output_file)):
            output_files.append(output_file)
        else:
            missing_output_files.append(output_file)

    # Get the input files to save with the snapshot. By default, this is all
    # files in the project directory that are not tracked by git and are not
    # explicitly specified by the user to be an output file in project.yaml.
    input_files = []
    commit_hash = None
    commit_patch_location = None
    if project_definition.git_repo() is not None:
        # Get and save the current git repo state.
        git = Git('.')
        try:
            # Find all files in the project that are not already tracked by git.
            for untracked_file in git.ls_files(project_dir, exclude_standard=True, others=True, directory=True).split('\n'):
                # Save the pathname relative to the project so that we can later
                # recreate the directory structure inside the project.
                untracked_file = os.path.relpath(untracked_file, project_dir)
                input_files.append(untracked_file)
            commit_hash = git.rev_parse('HEAD')
            # Save the git diff, if any.
            diff = git.diff()
            if diff:
                commit_patch_location = os.path.join(snapshot_directory, COMMIT_PATCH_BASENAME)
                with open(commit_patch_location, 'w+') as f:
                    f.write(diff)
                    f.write('\n')
        except:
            pass
    else:
        for file in os.listdir(project_dir):
            input_files.append(file)
    input_files = [file for file in input_files if file not in output_files]

    # Confirm the files to save with the snapshot.
    if input_files or output_files or missing_output_files:
        print("Save the following files in the snapshot?")

        if input_files:
            print("Input files:")
            for file in input_files:
                print(" ", file)
        if output_files:
            print("Output files:")
            for file in output_files:
                print(" ", file)
        if missing_output_files:
            print("Missing output files:")
            for file in missing_output_files:
                print(" ", file)

        confirm("", yes)

    # Snapshot all found input and output files to disk.
    index = 0
    mappings = {}
    for i, project_file in enumerate(input_files + output_files):
        snapshot_file = os.path.join(snapshot_directory, str(i))
        try:
            try:
                shutil.copy2(os.path.join(project_dir, project_file), snapshot_file)
            except IsADirectoryError:
                shutil.copytree(os.path.join(project_dir, project_file), snapshot_file)
        except Exception as e:
            logger.warn("Failed to copy file %s for snapshot, aborting", project_file)
            shutil.rmtree(snapshot_directory)
            db_session.delete(snapshot)
            db_session.commit()
            raise e

        # Save the mapping from project file to copy on local disk.
        mappings[project_file] = snapshot_file

    # Save the snapshot data locations in the database.
    snapshot.set_commit(commit_hash, commit_patch_location)
    for file in input_files:
        snapshot.add_input_file(file, mappings[file])
    for file in output_files:
        snapshot.add_output_file(file, mappings[file])
    for file in missing_output_files:
        snapshot.add_output_file(file, None)
    db_session.commit()

    return snapshot


def delete_snapshot(db_session, project_dir, name, yes):
    """Delete the snapshot(s) with the given name.

    Delete the snapshot data from disk and the metadata from the database.

    Args:
        db_session: A connection to the database.
        project_dir: Project root directory.
        name: The name of the snapshot(s) to delete.
        yes: Don't ask for confirmation.

    Raises:
        ValueError: If the current project directory does not match the project
            metadata entry in the database.
    """
    # Find and validate the current project ID.
    project = get_project(db_session, project_dir)
    # Get the snapshots with the requested name.
    snapshots = project.snapshots.filter(Snapshot.name == name).all()
    if snapshots:
        snapshots_str = '\n'.join(str(snapshot) for snapshot in snapshots)
        confirm("Delete snapshots?\n{}".format(snapshots_str), yes)

        for snapshot in snapshots:
            # Log a warning if this snapshot was applied to any sessions. This
            # will delete the snapshot from the session's list of applied
            # snapshots.
            session_applications = db_session.query(AppliedSnapshot).filter(AppliedSnapshot.snapshot_id == snapshot.id).all()
            if len(session_applications) > 0:
                logger.warn("Deleted snapshot %s was applied to a session", snapshot)

            db_session.delete(snapshot)
            # Remove local snapshot data.
            snapshot_directory = get_snapshot_directory(project.id, snapshot.id)
            try:
                shutil.rmtree(snapshot_directory)
            except FileNotFoundError:
                logger.warn("Failed to remove snapshot directory %s because it does not exist",
                            snapshot_directory)
        db_session.commit()
    else:
        logger.warn("No snapshots found with name %s", name)

def list_snapshots(db_session, project_dir):
    """List all snapshots associated with the given project.

    Args:
        db_session: A connection to the database.
        project_dir: Project root directory.

    Returns:
        List of Snapshots for the current project.

    Raises:
        ValueError: If the current project directory does not match the project
            metadata entry in the database.
    """
    # Find and validate the current project ID.
    project = get_project(db_session, project_dir)
    return project.snapshots.all()
