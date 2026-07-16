"""Console entry point. Subcommands (play/cli/replay) land at 0.1.0 build order step 5 — see ROADMAP.md."""

import click

from loopwall import __version__


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="loopwall")
@click.pass_context
def cli(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


if __name__ == "__main__":
    cli()
