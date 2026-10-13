from pathlib import Path

import pytest

from bioenv.cli import main
from bioenv.config import ConfigError, load_config
from bioenv.manager import BioenvError, check, plan, publish


def catalog(tmp_path):
    envs = tmp_path / 'envs'
    bins = tmp_path / 'bin'
    path = tmp_path / 'tools.yaml'
    path.write_text(f'''settings:
  envs_root: {envs}
  bin_dir: {bins}
  micromamba: /usr/local/bin/micromamba
  mamba_root: {tmp_path}/root
tools:
  testtool:
    package: toolpkg
    python: "3.12"
    commands: [tool, tool-build]
    environment:
      DB_PATH: "{tmp_path}/data with spaces"
''', encoding='utf-8')
    return path, load_config(path)


def test_plan(tmp_path):
    _, cfg = catalog(tmp_path)
    command, launchers = plan(cfg, 'testtool')
    assert command[1] == 'create'
    assert 'python=3.12' in command
    assert 'toolpkg' in command
    assert launchers[-1].endswith('/tool-build')


def test_publish_existing_environment_and_check(tmp_path):
    path, cfg = catalog(tmp_path)
    env_bin = tmp_path / 'envs' / 'testtool' / 'bin'
    env_bin.mkdir(parents=True)
    for command in ('tool', 'tool-build'):
        exe = env_bin / command
        exe.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        exe.chmod(0o755)
    assert main(['--config', str(path), 'publish', 'testtool', '--dry-run']) == 0
    assert not (tmp_path / 'bin').exists()
    launchers = publish(cfg, 'testtool')
    assert len(launchers) == 2
    content = launchers[0].read_text()
    assert 'Managed by bioenv; tool=testtool' in content
    assert "'" in content  # space in DB path is shell-quoted
    assert '$@' in content
    assert all(ok for ok, _ in check(cfg, 'testtool'))


def test_foreign_command_is_not_overwritten(tmp_path):
    _, cfg = catalog(tmp_path)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    foreign = bin_dir / 'tool'
    foreign.write_text('not ours', encoding='utf-8')
    with pytest.raises(BioenvError, match='not owned'):
        plan(cfg, 'testtool')
    assert foreign.read_text() == 'not ours'


def test_invalid_command_name(tmp_path):
    path, _ = catalog(tmp_path)
    data = path.read_text().replace('tool-build', '../oops')
    path.write_text(data)
    with pytest.raises(ConfigError, match='commands'):
        load_config(path)


def test_colliding_catalog_commands(tmp_path):
    path, _ = catalog(tmp_path)
    with path.open('a') as f:
        f.write('  second:\n    package: other\n    commands: [tool]\n')
    with pytest.raises(ConfigError, match='both'):
        load_config(path)
