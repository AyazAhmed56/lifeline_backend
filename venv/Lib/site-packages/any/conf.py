import os


SNAPSHOT_DIRECTORY = '{}/.ray-snapshots/'.format(os.environ['HOME'])
DATABASE_DIR = os.path.dirname(os.path.realpath(__file__))
DATABASE_URI = 'sqlite:///{}/anyscale.db'.format(DATABASE_DIR)
