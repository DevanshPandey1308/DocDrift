Machine-classified, NOT human labels, NOT ground truth. Development set only.

# Real-repository FALSE finding triage

Pattern-based classification of a pre-change `fp-sample` run across the existing 12-repository development set. This is an investigation aid only; it does not assert correctness or ground truth.

Total FALSE findings: 123 (file_path: 103, python_version: 20).

## build/output path reference — 3 (file_path)

- `gunicorn` `docs/README.md:19` — The rendered site is emitted into the `site/` directory. (checker: Referenced repository path does not exist.)
- `mkdocs` `docs/getting-started.md:165` — `site/` to your `.gitignore` file. (checker: Referenced repository path does not exist.)

## container/action/package-like identifier — 3 (file_path)

- `click` `docs/standalone-apps-nuitka.md:138` — - ``src/hello_cli``: the package to compile. Pass the directory that holds (checker: Referenced repository path does not exist.)
- `gunicorn` `docs/content/guides/docker.md:98` — - `ghcr.io/benoitc/gunicorn:24.1.0` - Specific version (checker: Referenced repository path does not exist.)

## feature/reference/library version note — 11 (python_version)

- `black` `docs/faq.md:92` — Black provides formatting and `--target-version` support for Python 3.3+. (checker: Documentation requires (3, 3); project metadata declares (3, 11).)
- `httpx` `docs/async.md:26` — Use [IPython](https://ipython.readthedocs.io/en/stable/) or Python 3.9+ with `python -m asyncio` to try this code interactively, as they support executing `async`/`await` expressio (checker: Documentation requires (3, 9); project metadata declares (3, 10).)

## historical/release-document context — 5 (python_version)

- `typer` `docs/release-notes.md:668` — **Note**: this is the last version to support Python 3.7, going forward Typer will require Python 3.8+. And soon Python 3.8 will also be dropped as [Python 3.8 reached it's end of  (checker: Documentation requires (3, 8); project metadata declares (3, 11).)
- `starlette` `docs/release-notes.md:1135` — * Improved detection of `async` endpoints wrapped in `functools.partial` on Python 3.8+ - (checker: Documentation requires (3, 8); project metadata declares (3, 11).)

## historical/release-document path reference — 18 (file_path)

- `mkdocs` `docs/about/release-notes.md:1597` — search index is now written to `search/search_index.json` instead of (checker: Referenced repository path does not exist.)
- `gunicorn` `docs/content/2015-news.md:106` — - add `web.py` framework example ([Issue #1117](https://github.com/benoitc/gunicorn/issues/1117)) (checker: Referenced repository path does not exist.)

## OTHER unresolved path reference — 52 (file_path)

- `click` `docs/entry-points.md:34` — Contents of ``hello.py``: (checker: Referenced repository path does not exist.)
- `starlette` `README.md:112` — Run the `app` application in `example.py`: (checker: Referenced repository path does not exist.)

## project-level version comparison candidate — 4 (python_version)

- `black` `docs/contributing/the_basics.md:9` — Development requires Python 3.13+; the latest version is preferred. You can use any (checker: Documentation requires (3, 13); project metadata declares (3, 11).)
- `httpx` `README.md:104` — HTTPX requires Python 3.9+. (checker: Documentation requires (3, 9); project metadata declares (3, 10).)

## reader-example/tutorial path reference — 18 (file_path)

- `typer` `docs/contributing.md:123` — And the same for the commands in your `demo.py` file: (checker: Referenced repository path does not exist.)
- `black` `docs/integrations/github_actions.md:15` — Create a file named `.github/workflows/black.yml` inside your repository with: (checker: Referenced repository path does not exist.)

## runtime/template/absolute-like path reference — 9 (file_path)

- `click` `docs/standalone-apps-briefcase.md:96` — ``python -m <package>``, so a ``__main__.py`` file **must** exist in the (checker: Referenced repository path does not exist.)
- `black` `docs/usage_and_configuration/the_basics.md:336` — For example, `^/foo\.py$` matches only `foo.py` in that root. If no `pyproject.toml`, (checker: Referenced repository path does not exist.)
