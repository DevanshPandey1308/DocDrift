import click
import typer

cli = typer.Typer()


@click.command()
@click.option("--click-count", help="count")
def click_command(click_count):
    return click_count


@cli.command()
def typer_command(limit: int = typer.Option(3, "--typer-limit")):
    return limit


computed_flag = "--computed"


@click.command()
@click.option(computed_flag)
def dynamic_command(value):
    return value


# @click.option("--comment-only")
unrelated_example = "@click.option('--string-only')"


class Unrelated:
    def command(self):
        return lambda function: function


unrelated = Unrelated()


@unrelated.command()
@click.option("--not-a-cli-command")
def ordinary_function(value):
    return value
