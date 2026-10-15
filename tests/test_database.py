import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

from bioenv.cli import main
from bioenv.config import load_config
from bioenv.manager import (
    BioenvError,
    check,
    check_database,
    database_install_plan,
    install_database,
    publish,
)
from bioenv.manager import (
    install as install_tool,
)


def make_config(tmp_path, *, database_path='bakta/full', install=True, required=True,
                database_env='BAKTA_DB', environment=None):
    envs = tmp_path / 'envs'
    databases_root = tmp_path / 'database root'
    config = {
        'settings': {
            'envs_root': str(envs),
            'bin_dir': str(tmp_path / 'bin'),
            'micromamba': str(tmp_path / 'micromamba'),
            'mamba_root': str(tmp_path / 'mamba root'),
            'databases_root': str(databases_root),
        },
        'tools': {
            'bakta': {
                'package': 'bakta',
                'commands': ['bakta', 'bakta_db'],
                'databases': [{
                    'name': 'full',
                    'path': database_path,
                    'required': required,
                    **({'env': database_env} if database_env else {}),
                    **({'install': [
                        'bakta_db', 'download', '--output', '{parent}',
                        '--database', '{path}', '--cache', '{root}/cache',
                    ]} if install else {}),
                }],
                **({'environment': environment} if environment is not None else {}),
            },
        },
    }
    config_path = tmp_path / 'tools.yaml'
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    return config_path, load_config(config_path)


def create_environment(config, *, installer=True):
    prefix = Path(config['settings']['envs_root']) / 'bakta'
    metadata = prefix / 'conda-meta'
    metadata.mkdir(parents=True)
    (metadata / 'history').write_text('', encoding='utf-8')
    bindir = prefix / 'bin'
    bindir.mkdir()
    for name in ('bakta', 'bakta_db'):
        executable = bindir / name
        executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        executable.chmod(0o755)
    if not installer:
        (bindir / 'bakta_db').unlink()
    return prefix


def test_relative_database_path_resolves_under_root(tmp_path):
    _, config = make_config(tmp_path)
    create_environment(config)
    assert database_install_plan(config, 'bakta', 'full')['path'] == (
        tmp_path / 'database root' / 'bakta' / 'full'
    )


def test_absolute_database_path_is_preserved_for_check_and_launcher(tmp_path):
    absolute = tmp_path / 'explicit database'
    _, config = make_config(tmp_path, database_path=str(absolute), install=False)
    create_environment(config)
    launcher = publish(config, 'bakta')[0]
    assert f'export BAKTA_DB={shlex.quote(str(absolute))}' in launcher.read_text()
    ok, label = check_database(config, 'bakta', 'full')
    assert not ok and str(absolute) in label


def test_launcher_exports_database_path_quoted_without_requiring_database(tmp_path):
    _, config = make_config(tmp_path, database_path='bakta/full data')
    expected_root = tmp_path / "database's $root"
    config['settings']['databases_root'] = str(expected_root)
    micromamba = tmp_path / 'micromamba'
    micromamba.write_text('#!/bin/sh\nprintf %s "$BAKTA_DB"\n', encoding='utf-8')
    micromamba.chmod(0o755)
    config['settings']['micromamba'] = str(micromamba)
    create_environment(config)
    launcher = publish(config, 'bakta')[0]
    content = launcher.read_text(encoding='utf-8')
    expected = str(expected_root / 'bakta' / 'full data')
    assert f'export BAKTA_DB={shlex.quote(expected)}' in content
    assert not Path(expected).exists()
    completed = subprocess.run([launcher], capture_output=True, text=True, check=True)
    assert completed.stdout == expected


def test_launcher_preserves_other_environment_variables(tmp_path):
    _, config = make_config(tmp_path, environment={'DATA_LABEL': 'value with spaces'})
    create_environment(config)
    content = publish(config, 'bakta')[0].read_text(encoding='utf-8')
    assert "export DATA_LABEL='value with spaces'" in content
    assert 'export BAKTA_DB=' in content


def test_relative_database_path_rejects_symlink_escape(tmp_path):
    _, config = make_config(tmp_path)
    create_environment(config)
    root = Path(config['settings']['databases_root'])
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'bakta').mkdir(parents=True)
    (root / 'bakta' / 'full').symlink_to(outside, target_is_directory=True)
    with pytest.raises(BioenvError, match='escapes settings.databases_root'):
        publish(config, 'bakta')
    with pytest.raises(BioenvError, match='escapes settings.databases_root'):
        database_install_plan(config, 'bakta', 'full')


def test_installer_command_uses_absolute_environment_executable_and_literal_substitutions(
    tmp_path, monkeypatch,
):
    _, config = make_config(tmp_path)
    prefix = create_environment(config)
    plan = database_install_plan(config, 'bakta', 'full')
    captured = {}
    destination = plan['path']

    def run(command, **kwargs):
        captured['command'] = command
        captured['env'] = kwargs['env']
        captured['kwargs'] = kwargs
        destination.mkdir(parents=True)

    monkeypatch.setattr('bioenv.manager.subprocess.run', run)
    result = install_database(config, 'bakta', 'full')
    assert captured['command'] == [
        config['settings']['micromamba'], 'run', '-p', str(prefix),
        str(prefix / 'bin' / 'bakta_db'), 'download', '--output',
        str(destination.parent), '--database', str(destination), '--cache',
        str(Path(config['settings']['databases_root']) / 'cache'),
    ]
    assert captured['kwargs']['check'] is True
    assert 'shell' not in captured['kwargs']
    assert captured['env']["MAMBA_ROOT_PREFIX"] == config['settings']['mamba_root']
    assert result['path'] == destination
    assert destination.is_dir()


def test_install_creates_missing_parent_directories(tmp_path, monkeypatch):
    _, config = make_config(tmp_path, database_path='new/nested/full')
    prefix = create_environment(config)
    destination = Path(config['settings']['databases_root']) / 'new' / 'nested' / 'full'
    observed = []

    def run(command, **kwargs):
        observed.append(command)
        destination.mkdir(parents=True)

    monkeypatch.setattr('bioenv.manager.subprocess.run', run)
    plan = install_database(config, 'bakta', 'full')
    assert destination.is_dir()
    assert plan['path'] == destination
    assert observed and str(prefix / 'bin' / 'bakta_db') in observed[0]


def test_tool_install_does_not_automatically_install_databases(tmp_path, monkeypatch):
    _, config = make_config(tmp_path)
    observed = []

    def create_environment_from_micromamba(command, **kwargs):
        observed.append(command)
        create_environment(config)

    monkeypatch.setattr('bioenv.manager.subprocess.run', create_environment_from_micromamba)
    install_tool(config, 'bakta')
    assert len(observed) == 1 and observed[0][1] == 'create'
    assert not (Path(config['settings']['databases_root']) / 'bakta' / 'full').exists()


def test_install_rejects_existing_database_without_running_micromamba(tmp_path, monkeypatch):
    _, config = make_config(tmp_path)
    create_environment(config)
    destination = Path(config['settings']['databases_root']) / 'bakta' / 'full'
    destination.mkdir(parents=True)
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run',
        lambda *args, **kwargs: pytest.fail('existing database must not be overwritten'),
    )
    with pytest.raises(BioenvError, match='already exists'):
        install_database(config, 'bakta', 'full')


def test_install_rejects_missing_install_procedure(tmp_path):
    _, config = make_config(tmp_path, install=False)
    create_environment(config)
    with pytest.raises(BioenvError, match='no declared install procedure'):
        database_install_plan(config, 'bakta', 'full')


def test_install_requires_environment_and_installer_executable(tmp_path):
    _, config = make_config(tmp_path)
    with pytest.raises(BioenvError, match='No valid Conda environment'):
        database_install_plan(config, 'bakta', 'full')
    create_environment(config, installer=False)
    with pytest.raises(BioenvError, match='installer executable is missing'):
        database_install_plan(config, 'bakta', 'full')


def test_install_command_failure_reports_partial_location_without_deleting(tmp_path, monkeypatch):
    _, config = make_config(tmp_path)
    create_environment(config)
    destination = Path(config['settings']['databases_root']) / 'bakta' / 'full'

    def failed_run(*args, **kwargs):
        destination.mkdir(parents=True)
        raise subprocess.CalledProcessError(17, args[0])

    monkeypatch.setattr('bioenv.manager.subprocess.run', failed_run)
    with pytest.raises(BioenvError, match='partial data may remain'):
        install_database(config, 'bakta', 'full')
    assert destination.is_dir()


def test_install_reports_success_without_expected_directory(tmp_path, monkeypatch):
    _, config = make_config(tmp_path)
    create_environment(config)
    monkeypatch.setattr('bioenv.manager.subprocess.run', lambda *args, **kwargs: None)
    with pytest.raises(BioenvError, match='expected accessible directory is missing'):
        install_database(config, 'bakta', 'full')


def test_install_dry_run_shows_command_and_directories_without_changes(
    tmp_path, monkeypatch, capsys,
):
    config_path, config = make_config(tmp_path, database_path='new/nested/full')
    prefix = create_environment(config)
    before = set(tmp_path.rglob('*'))
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run',
        lambda *args, **kwargs: pytest.fail('dry-run must not run micromamba'),
    )
    plan = install_database(config, 'bakta', 'full', dry_run=True)
    assert set(tmp_path.rglob('*')) == before
    assert plan['command'][3] == str(prefix)
    assert plan['directories'] == [
        Path(config['settings']['databases_root']),
        Path(config['settings']['databases_root']) / 'new',
        Path(config['settings']['databases_root']) / 'new' / 'nested',
    ]
    assert main([
        '--config', str(config_path), 'db', 'install', 'bakta', 'full', '--dry-run'
    ]) == 0
    output = capsys.readouterr().out
    assert f'Path: {plan["path"]}' in output
    assert 'Directories to create:' in output
    assert 'Micromamba: ' in output
    assert set(tmp_path.rglob('*')) == before


def test_managed_install_rejects_absolute_path_outside_databases_root(tmp_path):
    _, config = make_config(tmp_path, database_path=str(tmp_path / 'outside'))
    create_environment(config)
    with pytest.raises(BioenvError, match='must stay within settings.databases_root'):
        database_install_plan(config, 'bakta', 'full')


def test_managed_install_rejects_databases_root_as_destination(tmp_path):
    _, config = make_config(tmp_path)
    create_environment(config)
    config['tools']['bakta']['databases'][0]['path'] = config['settings']['databases_root']
    with pytest.raises(BioenvError, match='cannot use databases_root as the destination'):
        database_install_plan(config, 'bakta', 'full')


def test_optional_database_check_keeps_existing_semantics(tmp_path):
    _, config = make_config(tmp_path, install=False, required=False)
    create_environment(config)
    publish(config, 'bakta')
    ok, label = check_database(config, 'bakta', 'full')
    assert ok and 'not installed' in label
    assert all(ok for ok, _ in check(config, 'bakta'))


def test_required_database_check_fails_when_missing_and_passes_when_accessible(tmp_path):
    _, config = make_config(tmp_path, install=False, required=True)
    ok, label = check_database(config, 'bakta', 'full')
    assert not ok and 'not installed' in label
    destination = Path(config['settings']['databases_root']) / 'bakta' / 'full'
    destination.mkdir(parents=True)
    ok, label = check_database(config, 'bakta', 'full')
    assert ok and 'available' in label


def test_database_check_cli_reports_required_state(tmp_path, capsys):
    config_path, config = make_config(tmp_path, install=False, required=True)
    assert main(['--config', str(config_path), 'db', 'check', 'bakta', 'full']) == 1
    assert 'FAIL' in capsys.readouterr().out
    destination = Path(config['settings']['databases_root']) / 'bakta' / 'full'
    destination.mkdir(parents=True)
    assert main(['--config', str(config_path), 'db', 'check', 'bakta', 'full']) == 0
    assert 'OK' in capsys.readouterr().out


def test_absolute_legacy_database_path_needs_no_databases_root(tmp_path):
    config_path, _ = make_config(tmp_path, database_path=str(tmp_path / 'legacy'), install=False)
    raw_config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    raw_config['settings'].pop('databases_root')
    config_path.write_text(yaml.safe_dump(raw_config), encoding='utf-8')
    config = load_config(config_path)
    assert check_database(config, 'bakta', 'full')[0] is False
