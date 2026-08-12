"""Tests for install.sh and the docker-compose template it renders.

The installer always produces a *fresh* cocoindex/ directory, so it always
starts with an empty cocoindex state store. Postgres data, however, lives in a
docker volume that outlives that directory. These tests pin the two invariants
that keep the pair consistent:

1. a fresh install resets the project's target schema before indexing, and
2. each project gets its own compose project and volume.
"""

import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
COMPOSE_TEMPLATE = REPO_ROOT / "templates" / "docker-compose.yml"
MAIN_TEMPLATE = REPO_ROOT / "templates" / "main.py"

# install.sh helpers that the functions under test call.
BASH_HELPERS = ["info", "warn", "error"]

# Upper bound on the line span of a single install.sh function.
MAX_FUNCTION_LINES = 80


def _extract_bash_function(name: str) -> str:
    """Return the source of one top-level function in install.sh.

    install.sh declares functions at column zero, either on one line
    (`info()  { ...; }`) or as a block closed by `}` at column zero.
    """
    assert name, "function name required"
    lines = INSTALL_SH.read_text().splitlines()
    opening = re.compile(rf"^{re.escape(name)}\(\)\s*\{{")
    for start, line in enumerate(lines):
        if not opening.match(line):
            continue
        if line.rstrip().endswith("}"):
            return line
        limit = min(start + MAX_FUNCTION_LINES, len(lines))
        for end in range(start + 1, limit):
            if lines[end] == "}":
                return "\n".join(lines[start : end + 1])
        raise AssertionError(f"unterminated {name}() in install.sh")
    raise AssertionError(f"{name}() not found in install.sh")


def _run_bash(functions: list[str], command: str, **kwargs) -> subprocess.CompletedProcess:
    """Run `command` against functions lifted out of install.sh."""
    assert functions, "at least one function required"
    assert command, "command required"
    parts = ["set -euo pipefail"]
    parts.extend(_extract_bash_function(name) for name in functions)
    parts.append(command)
    return subprocess.run(
        ["bash", "-c", "\n".join(parts)],
        capture_output=True,
        text=True,
        timeout=30,
        **kwargs,
    )


def _render(template: Path, project: str, port: int = 5434) -> str:
    """Apply the same placeholder substitution install.sh performs."""
    assert template.is_file(), f"missing template: {template}"
    assert project, "project required"
    content = template.read_text()
    return content.replace("{{PROJECT}}", project).replace("{{PORT}}", str(port))


def _compose_for(project: str) -> dict:
    parsed = yaml.safe_load(_render(COMPOSE_TEMPLATE, project))
    assert isinstance(parsed, dict), "compose template must parse to a mapping"
    return parsed


def _volume_names(compose: dict) -> set[str]:
    """Resolve declared volumes to the names docker actually creates.

    A volume with an explicit `name` uses it verbatim; otherwise compose
    prefixes the key with the project name.
    """
    project = compose.get("name", "")
    names = set()
    for key, spec in (compose.get("volumes") or {}).items():
        explicit = (spec or {}).get("name") if isinstance(spec, dict) else None
        names.add(explicit or f"{project}_{key}")
    assert names, "compose template declares no volumes"
    return names


class TestSchemaResetSql:
    def test_targets_the_project_schema(self):
        result = _run_bash(
            [*BASH_HELPERS, "schema_reset_sql"], 'schema_reset_sql "alpha"'
        )
        assert result.returncode == 0, result.stderr
        assert 'DROP SCHEMA IF EXISTS "alpha_cocoindex" CASCADE;' in result.stdout

    def test_suppresses_notices(self):
        """A fresh install has no schema to drop; psql should stay quiet."""
        result = _run_bash(
            [*BASH_HELPERS, "schema_reset_sql"], 'schema_reset_sql "alpha"'
        )
        assert result.returncode == 0, result.stderr
        assert "SET client_min_messages TO WARNING;" in result.stdout

    def test_schema_suffix_matches_main_template(self):
        """Guard against install.sh and main.py drifting apart."""
        assert (
            "f\"{CONFIG['project']}_cocoindex\"" in MAIN_TEMPLATE.read_text()
        ), "main.py no longer derives PG_SCHEMA_NAME as <project>_cocoindex"
        result = _run_bash(
            [*BASH_HELPERS, "schema_reset_sql"], 'schema_reset_sql "beta"'
        )
        assert result.returncode == 0, result.stderr
        assert '"beta_cocoindex"' in result.stdout

    @pytest.mark.parametrize("bad", ["alpha-beta", "Alpha", "", 'a"; DROP DATABASE x;'])
    def test_rejects_project_names_main_py_would_reject(self, bad):
        result = _run_bash(
            [*BASH_HELPERS, "schema_reset_sql"],
            f"schema_reset_sql {shlex.quote(bad)}",
        )
        assert result.returncode != 0, f"accepted invalid project name {bad!r}"


class TestInstallerOrdering:
    def test_schema_reset_runs_after_postgres_is_up_and_before_indexing(self):
        """The reset needs a live Postgres, and is pointless after indexing."""
        source = INSTALL_SH.read_text()
        ready = source.find('info "Postgres is ready."')
        reset = source.find('schema_reset_sql "$PROJECT_NAME"')
        index = source.find('info "Running initial index')
        assert ready != -1, "install.sh no longer waits for Postgres"
        assert reset != -1, "install.sh never resets the target schema"
        assert index != -1, "install.sh no longer runs the initial index"
        assert ready < reset < index, (
            f"reset must sit between Postgres readiness ({ready}) "
            f"and the initial index ({index}), got {reset}"
        )

    def test_schema_reset_runs_before_the_pgvector_extension(self):
        """CASCADE can drop a project-scoped `vector` extension.

        cocoindex installs pgvector into its own schema, where an unqualified
        `vector(N)` column type will not resolve. Resetting first means the
        installer's own CREATE EXTENSION always lands in `public`.
        """
        source = INSTALL_SH.read_text()
        reset = source.find('schema_reset_sql "$PROJECT_NAME"')
        extension = source.find("CREATE EXTENSION IF NOT EXISTS vector")
        assert reset != -1, "install.sh never resets the target schema"
        assert extension != -1, "install.sh no longer enables pgvector"
        assert reset < extension, (
            "the schema reset must precede CREATE EXTENSION, or a CASCADE drop "
            "can leave the database without a resolvable vector type"
        )


class TestRemoveConflictingContainer:
    @staticmethod
    def _docker_stub(tmp_path: Path, *, label: str, inspect_rc: int = 0) -> dict:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        log = tmp_path / "docker.log"
        stub = bin_dir / "docker"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
            'if [ "$1" = "inspect" ]; then\n'
            '    printf "%s\\n" "$DOCKER_LABEL"\n'
            '    exit "$DOCKER_INSPECT_RC"\n'
            "fi\n"
            "exit 0\n"
        )
        stub.chmod(0o755)
        import os

        env = dict(os.environ)
        env.update(
            PATH=f"{bin_dir}:{env['PATH']}",
            DOCKER_LOG=str(log),
            DOCKER_LABEL=label,
            DOCKER_INSPECT_RC=str(inspect_rc),
        )
        return {"env": env, "log": log}

    def test_removes_container_held_by_another_compose_project(self, tmp_path):
        stub = self._docker_stub(tmp_path, label="cocoindex")
        result = _run_bash(
            [*BASH_HELPERS, "remove_conflicting_container"],
            'remove_conflicting_container "alpha_cocoindex_postgres" "alpha_cocoindex"',
            env=stub["env"],
        )
        assert result.returncode == 0, result.stderr
        assert "rm -f alpha_cocoindex_postgres" in stub["log"].read_text()

    def test_keeps_container_already_in_this_project(self, tmp_path):
        stub = self._docker_stub(tmp_path, label="alpha_cocoindex")
        result = _run_bash(
            [*BASH_HELPERS, "remove_conflicting_container"],
            'remove_conflicting_container "alpha_cocoindex_postgres" "alpha_cocoindex"',
            env=stub["env"],
        )
        assert result.returncode == 0, result.stderr
        assert "rm -f" not in stub["log"].read_text()

    def test_succeeds_when_no_such_container_exists(self, tmp_path):
        stub = self._docker_stub(tmp_path, label="", inspect_rc=1)
        result = _run_bash(
            [*BASH_HELPERS, "remove_conflicting_container"],
            'remove_conflicting_container "alpha_cocoindex_postgres" "alpha_cocoindex"',
            env=stub["env"],
        )
        assert result.returncode == 0, result.stderr
        assert "rm -f" not in stub["log"].read_text()


class TestComposeTemplateIsolation:
    def test_declares_a_project_scoped_compose_project(self):
        assert _compose_for("alpha")["name"] == "alpha_cocoindex"

    def test_volume_is_project_scoped(self):
        assert _volume_names(_compose_for("alpha")) == {"alpha_cocoindex_data"}

    def test_two_projects_never_share_a_volume(self):
        alpha = _volume_names(_compose_for("alpha"))
        beta = _volume_names(_compose_for("beta"))
        assert not (alpha & beta), f"projects share volumes: {alpha & beta}"

    def test_container_name_stays_project_scoped(self):
        service = _compose_for("alpha")["services"]["cocoindex-postgres"]
        assert service["container_name"] == "alpha_cocoindex_postgres"
