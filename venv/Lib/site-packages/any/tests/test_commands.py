from contextlib import contextmanager
import click
import click.testing
import os
import pytest
import shutil
import subprocess
import sys
import yaml

if sys.version_info >= (3, 3):
    from unittest.mock import patch, DEFAULT
else:
    from mock import patch, DEFAULT

TEST_DIR = os.path.dirname(os.path.abspath(__file__))

import any.conf
any.conf.SNAPSHOT_DIRECTORY = "{}/test-snapshots/".format(TEST_DIR)
any.conf.DATABASE_URI = "sqlite:///{}/test.db".format(TEST_DIR)
import any.scripts
from any.models import (AppliedSnapshot, Base, DbSession, FileMetadata,
        Session, Snapshot, Project)
import ray.projects.scripts

def check_ok(result):
    click_result, mock_calls = result
    assert click_result.exit_code == 0, click_result.stdout

def check_error(result):
    click_result, mock_calls = result
    assert click_result.exit_code != 0, click_result.stdout


@contextmanager
def _chdir_and_back(d):
    old_dir = os.getcwd()
    try:
        os.chdir(d)
        yield
    finally:
        os.chdir(old_dir)

def run_command(cwd, command):

    # Mock the `ray session stop` command.
    @any.scripts.cli.command()
    @click.option("--name", default=None)
    def mock_stop(name):
        return

    command = command.split(" ")
    with _chdir_and_back(cwd):
        runner = click.testing.CliRunner()
        with patch.multiple(
                    "any.scripts",
                    logger=DEFAULT,  # Suppress log output.
                ), patch.multiple(
                    "ray.projects.scripts",
                    stop=mock_stop,
                    rsync=DEFAULT,
                ) as cluster_mock_calls, patch.multiple(
                    "ray.projects.scripts.SessionRunner",
                    create_cluster=DEFAULT,
                    sync_files=DEFAULT,
                    setup_environment=DEFAULT,
                    execute_command=DEFAULT,
                ) as session_mock_calls:
                result = runner.invoke(any.scripts.cli, command)
    cluster_mock_calls.update(session_mock_calls)
    return result, cluster_mock_calls

@pytest.fixture
def setup_project_dir(request):
    # Params are the project directory and list of files to add to the project.
    param = getattr(request, "param", {})
    project_dir = os.path.join(TEST_DIR, param.pop("project_dir"))
    # Create a mock test project.
    os.mkdir(project_dir)

    yield project_dir

    # Clean up. Delete the project.
    shutil.rmtree(project_dir)
    try:
        shutil.rmtree(any.conf.SNAPSHOT_DIRECTORY)
    except:
        pass
    # Delete all rows in the test database.
    db_session = DbSession()
    for table in Base.metadata.sorted_tables:
        db_session.execute(table.delete())
    db_session.commit()

def add_project_files(project_dir, files):
    for filename, file_content in files.items():
        filename = os.path.join(project_dir, filename)
        with open(filename, "w+") as f:
            f.write(file_content)

def remove_project_files(project_dir, files):
    for filename in files:
        filename = os.path.join(project_dir, filename)
        os.remove(filename)


@pytest.mark.parametrize(
        "setup_project_dir", [{
                "project_dir": "test",
            }],
        indirect=True,
        )
def test_project_create(setup_project_dir):
    """Test basic project creation."""
    project_dir = setup_project_dir

    # Cannot create a project without a name.
    check_error(run_command(project_dir, "project create"))
    # Can create a project.
    check_ok(run_command(project_dir, "project create test"))
    # Cannot create a project if one already exists.
    check_error(run_command(project_dir, "project create test"))


@pytest.mark.parametrize(
        "setup_project_dir", [{
                "project_dir": "test",
            }],
        indirect=True,
        )
def test_snapshots(setup_project_dir):
    """Test basic snapshot creation, deletion, and listing."""
    db_session = DbSession()
    project_dir = setup_project_dir
    check_ok(run_command(project_dir, "project create test"))
    project = db_session.query(Project).one()

    # Check that there are no snapshots when we start.
    result = run_command(project_dir, "snapshot list")
    check_ok(result)
    assert len(result[0].stdout.strip().split("\n")) == 1

    # Test snapshot creation.
    snapshot_names = ["test_snapshot1", "test_snapshot2", "test_snapshot3"]
    input_filenames = []
    for name in snapshot_names:
        test_filename = "{}_file".format(name)
        add_project_files(project_dir, {
            test_filename: "test file content",
            })
        input_filenames.append(test_filename)
        check_ok(run_command(project_dir, "snapshot create --name {} -y".format(name)))

        # Get the latest snapshot.
        snapshot = project.snapshots.order_by(Snapshot.created_at.desc()).first()
        for filename in input_filenames:
            # Check that the latest snapshot includes all files so far.
            assert [project_file for project_file in snapshot.project_files if
                    project_file.pathname == test_filename], [project_file.pathname for project_file in snapshot.project_files]

    # The client should see the new snapshots now.
    result = run_command(project_dir, "snapshot list")
    for name in snapshot_names:
        assert name in result[0].stdout

    # Test snapshot deletion.
    while snapshot_names:
        name = snapshot_names.pop(-1)
        input_filenames.pop(-1)
        check_ok(run_command(project_dir, "snapshot delete {} -y".format(name)))

        # Get the latest snapshot.
        snapshot = project.snapshots.order_by(Snapshot.created_at.desc()).first()
        if snapshot is not None:
            for test_filename in input_filenames:
                # Check that the latest snapshot includes all files so far.
                assert [project_file for project_file in snapshot.project_files if
                        project_file.pathname == test_filename]

        # The client should not see the deleted snapshot.
        result, _ = run_command(project_dir, "snapshot list")
        assert name not in result.stdout
        for name in snapshot_names:
            assert name in result.stdout

    # There should be no more files now that all snapshots have been deleted.
    assert len(db_session.query(FileMetadata).all()) == 0


def check_snapshot_creation(project, project_dir, expected_inputs, expected_outputs):
    check_ok(run_command(project_dir, "snapshot create -y"))
    snapshot = project.snapshots.order_by(Snapshot.created_at.desc()).first()

    for project_file in snapshot.project_files:
        if project_file.sync:
            if project_file.pathname != ".rayproject":
                assert project_file.pathname in expected_inputs
                expected_inputs.remove(project_file.pathname)
        else:
            assert project_file.pathname in expected_outputs
            has_location = project_file.location is not None
            assert has_location == expected_outputs[project_file.pathname], project_file.pathname
            del expected_outputs[project_file.pathname]
    assert len(expected_inputs) == 0, expected_inputs
    assert len(expected_outputs) == 0, expected_outputs


@pytest.mark.parametrize(
        "setup_project_dir", [{
                "project_dir": "test",
            }],
        indirect=True,
        )
def test_snapshot_output(setup_project_dir):
    """Test that the correct input and output files get saved."""
    db_session = DbSession()
    project_dir = setup_project_dir
    check_ok(run_command(project_dir, "project create test"))
    project = db_session.query(Project).one()

    outputs = [
            os.path.join(TEST_DIR, "absolute_pathname_test1"),
            "relative_pathname_test1",
            os.path.join(TEST_DIR, "absolute_pathname_test2"),
            "relative_pathname_test2",
            ]

    # Create some files.
    add_project_files(project_dir, {
        output: "test file content" for output in outputs
        })
    # When no outputs are specified, only files inside the project directory
    # should be saved, as project inputs.
    expected_inputs = {output for output in outputs if "relative" in output}
    expected_outputs = {}
    check_snapshot_creation(project, project_dir, expected_inputs,
            expected_outputs)

    # Specify outputs in the project yaml.
    with open(os.path.join(project_dir, ray.projects.scripts.PROJECT_YAML), 'r') as f:
        config = yaml.load(f)
    config["output_files"] = outputs
    with open(os.path.join(project_dir, ray.projects.scripts.PROJECT_YAML), 'w') as f:
        f.write(yaml.dump(config))

    # When outputs are specified, all files should be saved, as project outputs
    # instead of project inputs.
    expected_inputs = {}
    expected_outputs = {
            output: True for output in outputs  # All specified outputs exist.
            }
    check_snapshot_creation(project, project_dir, expected_inputs,
            expected_outputs)

    # Remove some of the specified outputs.
    missing_outputs = outputs[:len(outputs) // 2]
    remove_project_files(project_dir, missing_outputs)
    expected_inputs = {}
    # When some of the specified inputs are missing, they should still be saved
    # in the snapshot metadata, but this time with no location.
    expected_outputs = {
            output: output not in missing_outputs for output in outputs
            }
    check_snapshot_creation(project, project_dir, expected_inputs,
            expected_outputs)

    # Remove all specified outputs.
    remove_project_files(project_dir, outputs[len(missing_outputs):])
    expected_inputs = {}
    # When all the specified inputs are missing, they should still be saved in
    # the snapshot metadata, but this time with no location.
    expected_outputs = {
            output: False for output in outputs  # All outputs are missing.
            }
    check_snapshot_creation(project, project_dir, expected_inputs,
            expected_outputs)


@pytest.mark.parametrize(
        "setup_project_dir", [{
                "project_dir": "test",
            }],
        indirect=True,
        )
def test_sync_session(setup_project_dir):
    """Test syncing a session from a snapshot."""
    db_session = DbSession()
    project_dir = setup_project_dir
    check_ok(run_command(project_dir, "project create test"))

    # Start a session.
    result = run_command(project_dir, "session start -y")
    check_ok(result)
    # Check that the session is started with the correct snapshot files.
    _, mock_calls = result
    for call in mock_calls["execute_command"].call_args_list:
        args, _ = call
        assert "git" not in args
    for call in mock_calls["rsync"].call_args_list:
        args, kwargs = call
        assert ".rayproject" in kwargs["target"]

    # Check that we created and applied a snapshot, since no snapshot name was
    # given.
    session = db_session.query(Session).one()
    assert len(session.applied_snapshots.all()) == 1

    snapshot_names = ["test_snapshot1", "test_snapshot2", "test_snapshot3"]
    input_filenames = []
    for name in snapshot_names:
        test_filename = "{}_file".format(name)
        add_project_files(project_dir, {
            test_filename: "test file content",
            })
        check_ok(run_command(project_dir, "snapshot create --name {} -y".format(name)))

    # Test syncing a session from an existing snapshot.
    for i, snapshot_name in enumerate(snapshot_names):
        result = run_command(project_dir, "session sync -y --snapshot-name {}".format(snapshot_name))
        check_ok(result)
        _, mock_calls = result
        for call in mock_calls["execute_command"].call_args_list:
            args, _ = call
            assert "git" not in args

        # Check that the correct snapshot files got rsynced.
        synced_filenames = []
        for call in mock_calls["rsync"].call_args_list:
            args, kwargs = call
            for filename in synced_filenames:
                if filename in kwargs["target"]:
                    synced_filenames.append(filename)
        assert set(synced_filenames) == set(input_filenames[:i+1])

        synced_snapshot = db_session.query(Snapshot).filter(Snapshot.name == snapshot_name).one()
        db_session.refresh(session)
        # We should have applied one snapshot on session creation and 1 more
        # for each additional session start command.
        assert len(session.applied_snapshots.all()) == i + 2
        # Check that we synced the correct snapshot.
        assert synced_snapshot in [a.snapshot for a in session.applied_snapshots]

@pytest.mark.parametrize(
        "setup_project_dir", [{
                "project_dir": "test",
            }],
        indirect=True,
        )
def test_sync_session_input_only(setup_project_dir):
    """Test that project outputs do not get synced to a session."""
    db_session = DbSession()
    project_dir = setup_project_dir
    check_ok(run_command(project_dir, "project create test"))
    project = db_session.query(Project).one()

    outputs = [
            os.path.join(TEST_DIR, "absolute_pathname_test1"),
            "relative_pathname_test1",
            ]
    inputs = ["relative_pathname_test2", "relative_pathname_test3"]

    # Create some files.
    add_project_files(project_dir, {
        file: "test file content" for file in outputs + inputs
        })
    # Specify outputs in the project yaml.
    with open(os.path.join(project_dir, ray.projects.scripts.PROJECT_YAML), 'r') as f:
        config = yaml.load(f)
    config["output_files"] = outputs
    with open(os.path.join(project_dir, ray.projects.scripts.PROJECT_YAML), 'w') as f:
        f.write(yaml.dump(config))

    snapshot_name = "snapshot"
    check_ok(run_command(project_dir, "snapshot create --name {} -y".format(snapshot_name)))
    result = run_command(project_dir, "session start -y --snapshot-name {}".format(snapshot_name))
    check_ok(result)
    _, mock_calls = result
    # Check that the inputs got rsynced, but not the outputs.
    synced_filenames = []
    for call in mock_calls["rsync"].call_args_list:
        args, kwargs = call
        for filename in inputs:
            if filename in kwargs["target"]:
                synced_filenames.append(filename)
        for filename in outputs:
            if filename in kwargs["target"]:
                assert False
    assert set(synced_filenames) == set(inputs)


@pytest.mark.parametrize(
        "setup_project_dir", [{
                "project_dir": "test",
            }],
        indirect=True,
        )
def test_session_start_stop(setup_project_dir):
    """Test basic session starting, stopping, and listing."""
    db_session = DbSession()
    project_dir = setup_project_dir
    check_ok(run_command(project_dir, "project create test"))

    # Check that there are no sessions when we start.
    result = run_command(project_dir, "session list")
    check_ok(result)
    assert len(result[0].stdout.strip().split("\n")) == 1

    # Check that the active session was created.
    check_ok(run_command(project_dir, "session start -y"))
    session = db_session.query(Session).one()
    assert session.active

    # Check that we can sync/start a second session with a different name.
    # However, it should not be possible to sync a session with a snapshot if
    # the session hasn't been started yet.
    check_ok(run_command(project_dir, "session start -y --name testsession"))

    # Check that we cannot start another session with the same name.
    check_error(run_command(project_dir, "session start -y --name testsession"))

    # Check that we can stop the currently active session (named test).
    check_ok(run_command(project_dir, "session stop"))
    db_session.refresh(session)
    assert session.active

    # Check that we can stop the first session we started.
    check_ok(run_command(project_dir, "session stop"))
    db_session.refresh(session)
    assert not session.active

    session_names = ["test_session1", "test_session2", "test_session3"]
    for name in session_names:
        check_ok(run_command(project_dir, "session start -y --name {}".format(name)))
        session = db_session.query(Session).filter(Session.active).order_by(Session.created_at.desc()).first()
        assert session.name == name

    for name in reversed(session_names):
        check_ok(run_command(project_dir, "session stop --name {}".format(name)))

    result = run_command(project_dir, "session list")
    check_ok(result)
    for name in session_names:
        assert name in result[0].stdout


@pytest.mark.parametrize(
        "setup_project_dir", [{
                "project_dir": "test",
            }],
        indirect=True,
        )
def test_multiple_sessions(setup_project_dir):
    """Test handling of multiple sessions."""
    db_session = DbSession()
    project_dir = setup_project_dir
    check_ok(run_command(project_dir, "project create test"))

    session_names = ["test_session1", "test_session2", "test_session3", "test_session4"]
    for name in session_names:
        check_ok(run_command(project_dir, "session start -y --name {}".format(name)))

    # Now touch the second session.
    check_ok(run_command(project_dir, "session execute --name test_session2 --shell uptime"))
    check_ok(run_command(project_dir, "session stop"))
    active_sessions = db_session.query(Session).filter(Session.active).all()
    assert set(session.name for session in active_sessions) == set(["test_session1", "test_session3", "test_session4"])

    # Touch the third session.
    check_ok(run_command(project_dir, "session execute --name test_session3 --shell uptime"))
    check_ok(run_command(project_dir, "session stop"))
    active_sessions = db_session.query(Session).filter(Session.active).all()
    assert set(session.name for session in active_sessions) == set(["test_session1", "test_session4"])

    # Stop the rest of the sessions.
    check_ok(run_command(project_dir, "session stop"))
    check_ok(run_command(project_dir, "session stop"))
    active_sessions = db_session.query(Session).filter(Session.active).all()
    assert len(active_sessions) == 0

@pytest.mark.parametrize(
        "setup_project_dir", [{
                "project_dir": "test",
            }],
        indirect=True,
        )
def test_session_patterns(setup_project_dir):
    """Test handling of patterns in session names."""
    db_session = DbSession()
    project_dir = setup_project_dir
    check_ok(run_command(project_dir, "project create test"))

    session_names = ["test_session1", "test_session2", "a_session", "b_session"]
    for name in session_names:
        check_ok(run_command(project_dir, "session start -y --name {}".format(name)))

    # Execute a command on test_session1 and test_session2.
    check_ok(run_command(project_dir, "session execute --name test_session* --shell testcommand"))
    for name in ["test_session1", "test_session2"]:
        session = db_session.query(Session).filter(Session.name == name).one()
        assert session.commands[-1].command == "testcommand"
    for name in ["a_session", "b_session"]:
        session = db_session.query(Session).filter(Session.name == name).one()
        assert len(session.commands.all()) == 0

    # Try to use a pattern that does not match any session.
    check_error(run_command(project_dir, "session execute --name session_name* --shell testcommand"))

    # Stop sessions a_session and b_session.
    check_ok(run_command(project_dir, "session stop --name ?_session"))
    active_sessions = db_session.query(Session).filter(Session.active).all()
    assert set(session.name for session in active_sessions) == set(["test_session1", "test_session2"])

    # Stop the rest.
    check_ok(run_command(project_dir, "session stop --name *"))
    active_sessions = db_session.query(Session).filter(Session.active).all()
    assert len(active_sessions) == 0

@pytest.mark.parametrize(
        "setup_project_dir", [{
                "project_dir": "test",
            }],
        indirect=True,
        )
def test_session_create_multiple(setup_project_dir):
    db_session = DbSession()
    project_dir = setup_project_dir
    check_ok(run_command(project_dir, "project create test"))
    project = db_session.query(Project).one()

    # Specify commands in the project yaml.
    with open(os.path.join(project_dir, ray.projects.scripts.PROJECT_YAML), 'r') as f:
        config = yaml.load(f)
    config["commands"] = [{
        "name": "testcommand",
        "command": "testcommand",
        "params": [{"name": "a", "choices": ["1", "2"]}]
    }]
    with open(os.path.join(project_dir, ray.projects.scripts.PROJECT_YAML), 'w') as f:
        f.write(yaml.dump(config))

    # Start two sessions, one for a = 1 and one for a = 2.
    check_ok(run_command(project_dir, "session start -y testcommand --a *"))

    # Check that these sessions have been started.
    sessions = db_session.query(Session).filter(Session.active).all()
    assert set(session.name for session in sessions) == set(["test-a-1", "test-a-2"])

    # Stop the sessions.
    check_ok(run_command(project_dir, "session stop --name *"))
