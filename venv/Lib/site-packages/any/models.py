import datetime
import fnmatch
import os.path
import uuid

from sqlalchemy import create_engine
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.orm import sessionmaker

import any.conf

Base = declarative_base()

def _get_most_recent_session(sessions):
    last_session = None
    last_created_at = datetime.datetime.min

    for session in sessions:
        last_command = session.commands.order_by(SessionCommand.created_at.desc()).first()
        last_snapshot = session.applied_snapshots.order_by(AppliedSnapshot.created_at.desc()).first()
        max_timestamp = max(
            last_command.created_at if last_command else datetime.datetime.min,
            last_snapshot.created_at if last_snapshot else datetime.datetime.min)
        if max_timestamp > last_created_at:
            last_created_at = max_timestamp
            last_session = session

    return last_session

class Project(Base):
    __tablename__ = 'project'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Snapshots created of this project.
    snapshots = relationship('Snapshot', back_populates='project', lazy='dynamic', cascade='all, delete-orphan')
    # Session created of this project.
    sessions = relationship('Session', lazy='dynamic', cascade='all, delete-orphan')

    def __init__(self, name):
        if name is None:
            self.name = str(uuid.uuid4())
        else:
            self.name = name

    def __repr__(self):
        return 'Project(name={} created_at={})'.format(self.name, self.created_at)

    def find_sessions(self, pattern):
        """Find active sessions in the project matching a pattern.

        If the pattern is None, the most recent session is returned
        (i.e. the one used last to execute a command or create a snapshot).

        Arguments:
            pattern (str): Pattern of the session name to match or None if the
                last active session in the whole project should be returned.

        Returns:
            Active sessions whose name matches the pattern.
        """
        sessions = self.sessions.filter(Session.active).all()
        if pattern is None:
            # If no pattern was specified, we want to return the default session.
            session = _get_most_recent_session(sessions)
            return [session]
        else:
            return [session for session in sessions if fnmatch.fnmatch(session.name, pattern)]

class Snapshot(Base):
    __tablename__ = 'snapshot'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('project.id'))
    # The project that this is a snapshot of.
    project = relationship('Project', back_populates='snapshots')
    parent_snapshot_id = Column(Integer, ForeignKey('snapshot.id'))
    name = Column(String)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    # The git HEAD at the time of the snapshot, if git is used.
    commit_hash = Column(String)
    # The diff compared to git HEAD at the time of the snapshot, if git is
    # used. This patch can be applied on top of `commit_hash` to rebuild the
    # state of the repo at the time of the snapshot.
    commit_patch_location = Column(String)
    # The snapshot previous to this one. For snapshots of the local project
    # directory, this is always the snapshot created immediately before.
    # TODO: For snapshots created from an active session, this should instead
    # be the last snapshot that was synced to that session.
    parent_snapshot = relationship('Snapshot', uselist=False)
    # List of input and output files saved in the snapshot. When creating a
    # session from this snapshot, files with sync=True will be synced with the
    # session.
    project_files = relationship('FileMetadata', cascade='all, delete-orphan')

    def __init__(self,
            project_id,
            parent_snapshot_id,
            name=None,
            description=None):
        self.project_id = project_id
        self.parent_snapshot_id = parent_snapshot_id
        if name is None:
            self.name = str(uuid.uuid4())
        else:
            self.name = name
        self.description = description

    def set_commit(self, commit_hash, commit_patch_location):
        self.commit_hash = commit_hash
        self.commit_patch_location = commit_patch_location

    def add_input_file(self, pathname, location):
        f = FileMetadata(pathname, location, sync=True)
        self.project_files.append(f)

    def add_output_file(self, pathname, location):
        f = FileMetadata(pathname, location, sync=False)
        self.project_files.append(f)

    def __repr__(self):
        return 'Snapshot(name={} created_at={})'.format(self.name, self.created_at)

class FileMetadata(Base):
    __tablename__ = 'file_metadata'
    id = Column(Integer, primary_key=True)
    snapshot_id = Column(Integer, ForeignKey('snapshot.id'))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    # When starting a session from the associated snapshot, whether to sync
    # this file or not. This should be set to False for output files, such as
    # application logs and checkpoints.
    sync = Column(Boolean)
    # The pathname, relative to the project.
    pathname = Column(String)
    # The absolute location where a copy of the file is stored. This may be
    # None if an output file specified by the user in the project.yaml was not
    # found.
    location = Column(String)

    # The snapshot that this file is a part of.
    snapshot = relationship('Snapshot')

    def __init__(self, pathname, location, sync):
        self.pathname = pathname
        self.location = location
        self.sync = sync


class Session(Base):
    __tablename__ = 'session'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    project_id = Column(Integer, ForeignKey('project.id'))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    # Snapshots that were applied to this session.
    applied_snapshots = relationship("AppliedSnapshot", cascade='all, delete-orphan', lazy="dynamic")
    # Raw shell commands that were run on this session.
    commands = relationship("SessionCommand", cascade='all, delete-orphan', lazy="dynamic")
    active = Column(Boolean)

    def __init__(self, project_id, name=None):
        self.project_id = project_id
        self.active = True
        if name is None:
            self.name = str(uuid.uuid4())
        else:
            self.name = name

    def __repr__(self):
        return 'Session(name={} created_at={})'.format(self.name, self.created_at)

    def apply_snapshot(self, snapshot):
        a = AppliedSnapshot()
        a.snapshot_id = snapshot.id
        self.applied_snapshots.append(a)

    def add_command(self, command):
        session_command = SessionCommand()
        session_command.session_id = self.id
        session_command.command = command
        self.commands.append(session_command)


class AppliedSnapshot(Base):
    __tablename__ = 'applied_snapshot'
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    # Session that this command was run during.
    session_id = Column(Integer, ForeignKey('session.id'))
    # Snapshot that was applied. This can be null if the snapshot is deleted.
    snapshot_id = Column(Integer, ForeignKey('snapshot.id'), nullable=True)
    snapshot = relationship("Snapshot", uselist=False)


class SessionCommand(Base):
    __tablename__ = 'session_command'
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    # Session that this command was run during.
    session_id = Column(Integer, ForeignKey('session.id'))
    # Raw shell command that was run.
    command = Column(String)

# Commands to create and connect to the database.
engine = create_engine(any.conf.DATABASE_URI)
# TODO: Move out this command to create the database! This is fine
# for testing with sqlite but will not work for production.
Base.metadata.create_all(engine)
DbSession = sessionmaker(bind=engine)
