from pathlib import Path
import subprocess

import pytest

from bioenv.cli import main
from bioenv.config import ConfigError, load_config
from bioenv.manager import BioenvError, check, install, plan, publish


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
    with pytest.raises(BioenvError, match='not a bioenv launcher'):
        plan(cfg, 'testtool')
    assert foreign.read_text() == 'not ours'


def test_launcher_marker_without_expected_shebang_is_not_owned(tmp_path):
    _, cfg = catalog(tmp_path)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    foreign = bin_dir / 'tool'
    foreign.write_text('unrelated\n# Managed by bioenv; tool=testtool\n', encoding='utf-8')
    with pytest.raises(BioenvError, match='not a bioenv launcher'):
        plan(cfg, 'testtool')
    assert foreign.read_text(encoding='utf-8').startswith('unrelated')


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


def test_install_dry_run_rejects_existing_prefix(tmp_path):
    _, cfg = catalog(tmp_path)
    prefix = tmp_path / 'envs' / 'testtool'
    prefix.mkdir(parents=True)
    with pytest.raises(BioenvError, match='already exists'):
        install(cfg, 'testtool', dry_run=True)


def test_install_dry_run_rejects_symlink_prefix(tmp_path):
    _, cfg = catalog(tmp_path)
    prefix = tmp_path / 'envs' / 'testtool'
    prefix.parent.mkdir(parents=True)
    target = tmp_path / 'other-env'
    target.mkdir()
    prefix.symlink_to(target)
    with pytest.raises(BioenvError, match='symlink'):
        install(cfg, 'testtool', dry_run=True)


def test_launcher_check_detects_stale_or_non_executable_file(tmp_path):
    _, cfg = catalog(tmp_path)
    env_bin = tmp_path / 'envs' / 'testtool' / 'bin'
    env_bin.mkdir(parents=True)
    for command in ('tool', 'tool-build'):
        exe = env_bin / command
        exe.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        exe.chmod(0o755)
    launchers = publish(cfg, 'testtool')
    assert all(ok for ok, _ in check(cfg, 'testtool'))
    launchers[0].write_text(launchers[0].read_text() + '# stale configuration\n', encoding='utf-8')
    results = check(cfg, 'testtool')
    assert not results[2][0]
    launchers[1].chmod(0o644)
    assert not check(cfg, 'testtool')[4][0]


def test_check_treats_missing_optional_database_as_ok_but_required_as_failure(tmp_path):
    path, _ = catalog(tmp_path)
    data = path.read_text(encoding='utf-8')
    data = data.replace('    environment:\n', '    databases:\n      - name: optional\n        path: ' + str(tmp_path / 'optional') + '\n        required: false\n      - name: required\n        path: ' + str(tmp_path / 'required') + '\n        required: true\n    environment:\n')
    path.write_text(data, encoding='utf-8')
    cfg = load_config(path)
    results = check(cfg, 'testtool')
    db_results = results[-2:]
    assert db_results[0][0]
    assert 'not installed' in db_results[0][1]
    assert not db_results[1][0]


def test_publish_rejects_launcher_directory_symlink(tmp_path):
    _, cfg = catalog(tmp_path)
    env_bin = tmp_path / 'envs' / 'testtool' / 'bin'
    env_bin.mkdir(parents=True)
    for command in ('tool', 'tool-build'):
        exe = env_bin / command
        exe.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        exe.chmod(0o755)
    real_bin = tmp_path / 'real-bin'
    real_bin.mkdir()
    (tmp_path / 'bin').symlink_to(real_bin, target_is_directory=True)
    with pytest.raises(BioenvError, match='symlink directory'):
        publish(cfg, 'testtool', dry_run=True)
    assert not list(real_bin.iterdir())


def test_wrapper_quotes_shell_values(tmp_path):
    _, cfg = catalog(tmp_path)
    dangerous = "value with spaces; $(touch SHOULD_NOT_EXIST) ' quoted"
    cfg['tools']['testtool']['environment']['DB_PATH'] = dangerous
    env_bin = tmp_path / 'envs' / 'testtool' / 'bin'
    env_bin.mkdir(parents=True)
    exe = env_bin / 'tool'
    exe.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    exe.chmod(0o755)
    cfg['tools']['testtool']['commands'] = ['tool']
    launcher = publish(cfg, 'testtool')[0]
    content = launcher.read_text(encoding='utf-8')
    assert "export DB_PATH='value with spaces; $(touch SHOULD_NOT_EXIST) '" in content
    assert 'run -p ' in content and ' "$@"' in content
    fake_micromamba = tmp_path / 'micromamba'
    fake_micromamba.write_text('#!/bin/sh\nprintf \'%s\\n\' "$DB_PATH" "$@"\n', encoding='utf-8')
    fake_micromamba.chmod(0o755)
    cfg['settings']['micromamba'] = str(fake_micromamba)
    publish(cfg, 'testtool')
    result = subprocess.run(
        [str(launcher), 'argument with spaces'], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    )
    assert result.stdout.splitlines() == [
        dangerous, 'run', '-p', str(tmp_path / 'envs' / 'testtool'), 'tool', 'argument with spaces'
    ]
    assert not (tmp_path / 'SHOULD_NOT_EXIST').exists()


def test_micromamba_failure_reports_partial_prefix(tmp_path, monkeypatch):
    _, cfg = catalog(tmp_path)

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(17, args[0])

    monkeypatch.setattr('bioenv.manager.subprocess.run', fail)
    with pytest.raises(BioenvError, match='exit status 17.*may be incomplete'):
        install(cfg, 'testtool')


def test_install_reports_recovery_after_launcher_publication_failure(tmp_path, monkeypatch):
    _, cfg = catalog(tmp_path)
    blocked_bin = tmp_path / 'bin'
    blocked_bin.write_text('not a directory', encoding='utf-8')

    def create_environment(command, **kwargs):
        prefix = Path(command[command.index('-p') + 1])
        env_bin = prefix / 'bin'
        env_bin.mkdir(parents=True)
        for name in ('tool', 'tool-build'):
            executable = env_bin / name
            executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
            executable.chmod(0o755)

    monkeypatch.setattr('bioenv.manager.subprocess.run', create_environment)
    with pytest.raises(BioenvError, match='Environment.*created.*bioenv publish testtool'):
        install(cfg, 'testtool')


def test_cli_dry_run_reports_mamba_root_and_rejects_existing_environment(tmp_path, capsys):
    path, _ = catalog(tmp_path)
    assert main(['--config', str(path), 'install', 'testtool', '--dry-run']) == 0
    output = capsys.readouterr().out
    assert 'MAMBA_ROOT_PREFIX=' in output
    assert 'Micromamba:' in output
    prefix = tmp_path / 'envs' / 'testtool'
    prefix.mkdir(parents=True)
    assert main(['--config', str(path), 'install', 'testtool', '--dry-run']) == 2
    assert 'already exists' in capsys.readouterr().err
