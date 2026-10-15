from pathlib import Path
import subprocess
import threading

import pytest

from bioenv.cli import main
from bioenv.config import ConfigError, load_config
from bioenv.manager import (
    BioenvError, _environment_lock, check, install, plan, plan_update, publish, update,
)


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


def _installed_environment(tmp_path, version='1.0'):
    prefix = tmp_path / 'envs' / 'testtool'
    metadata = prefix / 'conda-meta'
    metadata.mkdir(parents=True)
    (metadata / 'history').write_text('', encoding='utf-8')
    (metadata / 'toolpkg-1.0-0.json').write_text(
        '{"name":"toolpkg","version":"' + version + '"}', encoding='utf-8'
    )
    env_bin = prefix / 'bin'
    env_bin.mkdir()
    for command in ('tool', 'tool-build'):
        executable = env_bin / command
        executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        executable.chmod(0o755)
    return prefix


def test_update_plan_uses_catalog_pin_and_latest_omits_it(tmp_path):
    _, cfg = catalog(tmp_path)
    cfg['tools']['testtool']['version'] = '2.5'
    declarative, _ = plan_update(cfg, 'testtool')
    latest, _ = plan_update(cfg, 'testtool', latest=True)
    assert declarative[1:3] == ['update', '-p']
    assert 'toolpkg=2.5' in declarative
    assert 'python=3.12' in declarative
    assert 'toolpkg' in latest and 'toolpkg=2.5' not in latest


def test_update_without_declared_version_requests_unpinned_package(tmp_path):
    _, cfg = catalog(tmp_path)
    command, _ = plan_update(cfg, 'testtool')
    assert 'toolpkg' in command
    assert not any(arg.startswith('toolpkg=') for arg in command)


@pytest.mark.parametrize('latest', [False, True])
def test_update_runs_in_place_and_syncs_launchers(tmp_path, monkeypatch, latest):
    _, cfg = catalog(tmp_path)
    prefix = _installed_environment(tmp_path)
    seen = {}

    def update_environment(command, **kwargs):
        seen['command'] = command
        seen['env'] = kwargs['env']
        for old_record in (prefix / 'conda-meta').glob('toolpkg-*.json'):
            old_record.unlink()
        record = prefix / 'conda-meta' / 'toolpkg-2.0-0.json'
        record.write_text('{"name":"toolpkg","version":"2.0"}', encoding='utf-8')

    monkeypatch.setattr('bioenv.manager.subprocess.run', update_environment)
    command, launchers, version = update(cfg, 'testtool', latest=latest)
    assert command == seen['command']
    assert seen['env']['MAMBA_ROOT_PREFIX'] == cfg['settings']['mamba_root']
    assert version == '2.0'
    assert len(launchers) == 2
    assert all(Path(path).is_file() for path in launchers)
    assert not any(arg == '--all' for arg in command)


def test_update_dry_run_simulates_solver_without_filesystem_changes(tmp_path, monkeypatch):
    _, cfg = catalog(tmp_path)
    prefix = _installed_environment(tmp_path)
    before = set(tmp_path.rglob('*'))
    calls = []
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run', lambda command, **kwargs: calls.append(command)
    )
    command, launchers, version = update(cfg, 'testtool', dry_run=True)
    after = set(tmp_path.rglob('*'))
    assert before == after
    assert calls[0][-1] == '--dry-run'
    assert '--dry-run' not in command
    assert version == '1.0'
    assert all(not Path(path).exists() for path in launchers)
    assert prefix.is_dir()


def test_update_cli_dry_run_reports_mode_and_solver_operation(tmp_path, monkeypatch, capsys):
    path, cfg = catalog(tmp_path)
    _installed_environment(tmp_path)
    cfg['tools']['testtool']['version'] = '2.5'
    path.write_text(path.read_text(encoding='utf-8').replace(
        '    commands: [tool, tool-build]',
        '    version: "2.5"\n    commands: [tool, tool-build]',
    ), encoding='utf-8')
    monkeypatch.setattr('bioenv.manager.subprocess.run', lambda *args, **kwargs: None)
    assert main(['--config', str(path), 'update', 'testtool', '--latest', '--dry-run']) == 0
    output = capsys.readouterr().out
    assert 'Mode: latest resolvable' in output
    assert 'Catalog version: ignored (--latest)' in output
    assert 'Micromamba solver simulation (--dry-run)' in output
    assert 'update -p' in output and 'toolpkg=2.5' not in output


def test_update_requires_existing_conda_environment_and_installed_package(tmp_path):
    _, cfg = catalog(tmp_path)
    with pytest.raises(BioenvError, match='use "bioenv install testtool"'):
        update(cfg, 'testtool', dry_run=True)
    prefix = tmp_path / 'envs' / 'testtool'
    prefix.mkdir(parents=True)
    with pytest.raises(BioenvError, match='not a valid Conda environment'):
        update(cfg, 'testtool', dry_run=True)


def test_update_requires_declared_package_in_environment(tmp_path):
    _, cfg = catalog(tmp_path)
    prefix = tmp_path / 'envs' / 'testtool'
    metadata = prefix / 'conda-meta'
    metadata.mkdir(parents=True)
    (metadata / 'history').write_text('', encoding='utf-8')
    with pytest.raises(BioenvError, match='toolpkg.*not installed'):
        update(cfg, 'testtool', dry_run=True)


def test_update_unknown_tool_is_reported(tmp_path):
    _, cfg = catalog(tmp_path)
    with pytest.raises(BioenvError, match='Unknown tool'):
        update(cfg, 'missing', dry_run=True)


def test_update_micromamba_failure_does_not_publish(tmp_path, monkeypatch):
    _, cfg = catalog(tmp_path)
    _installed_environment(tmp_path)
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run',
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(9, args[0])),
    )
    with pytest.raises(BioenvError, match='exit status 9'):
        update(cfg, 'testtool')
    assert not (tmp_path / 'bin').exists()


def test_update_verification_failure_does_not_publish(tmp_path, monkeypatch):
    _, cfg = catalog(tmp_path)
    _installed_environment(tmp_path)

    def remove_declared_executable(command, **kwargs):
        (tmp_path / 'envs' / 'testtool' / 'bin' / 'tool').unlink()

    monkeypatch.setattr('bioenv.manager.subprocess.run', remove_declared_executable)
    with pytest.raises(BioenvError, match='missing executable'):
        update(cfg, 'testtool')
    assert not (tmp_path / 'bin').exists()


def test_update_refuses_foreign_launcher(tmp_path, monkeypatch):
    _, cfg = catalog(tmp_path)
    _installed_environment(tmp_path)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    foreign = bin_dir / 'tool'
    foreign.write_text('foreign', encoding='utf-8')
    with pytest.raises(BioenvError, match='not a bioenv launcher'):
        update(cfg, 'testtool', dry_run=True)
    assert foreign.read_text(encoding='utf-8') == 'foreign'


def test_update_latest_does_not_report_success_when_solver_keeps_old_version(
    tmp_path, monkeypatch,
):
    _, cfg = catalog(tmp_path)
    _installed_environment(tmp_path)
    monkeypatch.setattr('bioenv.manager.subprocess.run', lambda *args, **kwargs: None)
    with pytest.raises(BioenvError, match='no newer resolvable version'):
        update(cfg, 'testtool', latest=True)
    assert not (tmp_path / 'bin').exists()


def test_environment_lock_serializes_threads(tmp_path):
    _, cfg = catalog(tmp_path)
    entered = threading.Event()
    acquired_second = threading.Event()

    def first():
        with _environment_lock(cfg, 'testtool'):
            entered.set()
            acquired_second.wait(timeout=2)

    def second():
        entered.wait(timeout=2)
        with _environment_lock(cfg, 'testtool'):
            acquired_second.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert entered.wait(timeout=2)
    assert not acquired_second.wait(timeout=0.05)
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    assert acquired_second.is_set()


def test_environment_lock_refuses_symlinked_root(tmp_path):
    _, cfg = catalog(tmp_path)
    real_root = tmp_path / 'real-envs'
    real_root.mkdir()
    (tmp_path / 'envs').symlink_to(real_root, target_is_directory=True)
    with pytest.raises(BioenvError, match='through a symlink'):
        with _environment_lock(cfg, 'testtool'):
            pytest.fail('lock must not be acquired through a symlinked environment root')
    assert not (real_root / '.bioenv-locks').exists()


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


def test_public_command_uses_absolute_environment_executable_and_avoids_path_recursion(tmp_path):
    _, cfg = catalog(tmp_path)
    env_bin = tmp_path / 'envs' / 'testtool' / 'bin'
    env_bin.mkdir(parents=True)
    executable = env_bin / 'tool'
    executable.write_text('#!/bin/sh\nprintf reached-env\n', encoding='utf-8')
    executable.chmod(0o755)
    build_executable = env_bin / 'tool-build'
    build_executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    build_executable.chmod(0o755)
    micromamba = tmp_path / 'micromamba'
    micromamba.write_text('#!/bin/sh\nshift 3\nexec "$@"\n', encoding='utf-8')
    micromamba.chmod(0o755)
    cfg['settings']['micromamba'] = str(micromamba)
    launcher = publish(cfg, 'testtool')[0]
    global_launcher = tmp_path / 'bin' / 'tool'
    assert launcher == global_launcher
    content = launcher.read_text(encoding='utf-8')
    assert f'{env_bin}/tool' in content
    assert f'run -p {tmp_path}/envs/testtool {env_bin}/tool "$@"' in content
    completed = subprocess.run([str(launcher)], env={'PATH': str(launcher.parent)},
                               capture_output=True, text=True, check=True)
    assert completed.stdout == 'reached-env'


def test_alias_publishes_public_name_but_executes_real_name(tmp_path):
    _, cfg = catalog(tmp_path)
    cfg['tools']['testtool']['commands'] = {'spades': 'spades.py', 'spades.py': 'spades.py'}
    env_bin = tmp_path / 'envs' / 'testtool' / 'bin'
    env_bin.mkdir(parents=True)
    executable = env_bin / 'spades.py'
    executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    executable.chmod(0o755)
    launchers = publish(cfg, 'testtool')
    assert [p.name for p in launchers] == ['spades', 'spades.py']
    assert str(executable) in launchers[0].read_text(encoding='utf-8')
    assert all(ok for ok, _ in check(cfg, 'testtool'))


def test_publish_rejects_missing_real_executable_without_creating_launcher(tmp_path):
    _, cfg = catalog(tmp_path)
    cfg['tools']['testtool']['commands'] = {'spades': 'spades.py'}
    (tmp_path / 'envs' / 'testtool' / 'bin').mkdir(parents=True)
    with pytest.raises(BioenvError, match='missing executable.*spades'):
        publish(cfg, 'testtool')
    assert not (tmp_path / 'bin').exists()


def test_non_executable_real_executable_is_rejected(tmp_path):
    _, cfg = catalog(tmp_path)
    env_bin = tmp_path / 'envs' / 'testtool' / 'bin'
    env_bin.mkdir(parents=True)
    (env_bin / 'tool').write_text('not executable', encoding='utf-8')
    with pytest.raises(BioenvError, match='missing executable'):
        publish(cfg, 'testtool')
    assert not (tmp_path / 'bin').exists()


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
        dangerous, 'run', '-p', str(tmp_path / 'envs' / 'testtool'),
        str(tmp_path / 'envs' / 'testtool' / 'bin' / 'tool'), 'argument with spaces'
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
