import click


def confirm(msg, yes):
    return None if yes else click.confirm(msg, abort=True)
