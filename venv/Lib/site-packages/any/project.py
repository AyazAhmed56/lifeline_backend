import os

import ray.projects

from any.models import Project


# Pathnames specific to Ray's project directory structure.
PROJECT_ID_BASENAME = 'project-id'
RAY_PROJECT_DIRECTORY = '.rayproject'


def get_project(db_session, project_dir):
    """
    Args:
        db_session: A connection to the database.
        project_dir: Project root directory.

    Returns:
        The Project metadata.

    Raises:
        ValueError: If the current project directory does not match the project
            metadata entry in the database.
    """
    project_id_filename = os.path.join(project_dir, RAY_PROJECT_DIRECTORY, PROJECT_ID_BASENAME)
    if not os.path.isfile(project_id_filename):
        raise ValueError("{} file does not exist.".format(project_id_filename))
    with open(project_id_filename, 'r') as f:
        project_id = f.read()
    try:
        project_id = int(project_id)
    except:
        raise ValueError("{} does not contain a valid project ID".format(project_id_filename))

    # Validate project ID against database.
    project = db_session.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise ValueError("{} does not contain a valid project ID. Have you added it to the database?".format(project_id_filename))

    # Validate project name from yaml against database.
    project_definition = ray.projects.ProjectDefinition(os.getcwd())
    project_name = project_definition.config.get("name", None)
    if project_name != project.name:
        raise ValueError("Project name {} does not match saved project name {}".format(project_name, project.name))

    return project


def list_projects(db_session):
    """List all projects registered with the database.

    Args:
        db_session: A connection to the database.

    Returns:
        A list of Project metadata entries for all registered projects.
    """
    return db_session.query(Project).all()
