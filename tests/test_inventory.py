import json
from pathlib import Path

import yaml

from bioenv.cli import main
from bioenv.config import load_config
from bioenv.manager import publish


def make_catalog(tmp_path, tools):
    config = {
        'settings': {
            'envs_root': str(tmp_path / 'envs'),
            'bin_dir': str(tmp_path / 'bin'),
            'micromamba': str(tmp_path / 'micromamba'),
            'mamba_root': str(tmp_path / 'mamba'),
            'databases_root': str(tmp_path / 'databases'),
        },
        'tools': tools,
    }
    path = tmp_path / 'tools.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    return path, load_config(path)


def tool_spec(name, commands=None, **extra):
    return {'package': name, 'commands': commands or [name], **extra}


def install_tool(config, tool, version='1.0', *, record=True):
    prefix = Path(config['settings']['envs_root']) / tool
    metadata = prefix / 'conda-meta'
    metadata.mkdir(parents=True)
    (metadata / 'history').write_text('', encoding='utf-8')
    spec = config['tools'][tool]
    if record:
        package_record = metadata / f'{spec["package"]}-{version}-0.json'
        package_record.write_text(
            json.dumps({'name': spec['package'], 'version': version}), encoding='utf-8'
        )
    env_bin = prefix / 'bin'
    env_bin.mkdir()
    commands = spec['commands']
    executables = commands.values() if isinstance(commands, dict) else commands
    for executable in set(executables):
        path = env_bin / executable
        path.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        path.chmod(0o755)
    return prefix


def test_list_table_shows_installed_version_and_all_three_statuses(tmp_path, capsys):
    path, config = make_catalog(tmp_path, {
        'bakta': tool_spec('bakta', ['bakta', 'bakta_db'], version='9.9'),
        'fastqc': tool_spec('fastqc'),
        'spades': tool_spec('spades'),
    })
    install_tool(config, 'bakta', '1.2.3')
    publish(config, 'bakta')
    install_tool(config, 'spades')
    # An executable without its published launcher makes the present environment incomplete.
    assert main(['--config', str(path), 'list']) == 0
    output = capsys.readouterr().out
    lines = output.splitlines()
    assert lines[0].startswith('TOOL')
    assert all(header in lines[0] for header in ('VERSION', 'STATUS', 'COMMANDS'))
    assert '─' in lines[1]
    starts = [lines[0].index(header) for header in ('TOOL', 'VERSION', 'STATUS', 'COMMANDS')]
    rows = {line[starts[0]:starts[1]].strip(): line for line in lines[2:]}
    assert rows['bakta'][starts[1]:starts[2]].strip() == '1.2.3'
    assert rows['bakta'][starts[2]:starts[3]].strip() == 'Installed'
    assert rows['bakta'].endswith('bakta, bakta_db')
    assert rows['fastqc'][starts[2]:starts[3]].strip() == 'Missing'
    assert rows['spades'][starts[2]:starts[3]].strip() == 'Incomplete'
    assert '9.9' not in rows['bakta']


def test_list_table_columns_are_aligned(tmp_path, capsys):
    path, _ = make_catalog(tmp_path, {
        'zeta': tool_spec('zeta'), 'alpha': tool_spec('alpha'),
    })
    main(['--config', str(path), 'list'])
    lines = capsys.readouterr().out.splitlines()
    starts = [lines[0].index(header) for header in ('TOOL', 'VERSION', 'STATUS', 'COMMANDS')]
    for line in lines[2:]:
        assert [line.index(value) for value in ('—', 'Missing')] == starts[1:3]


def test_list_simple_is_sorted_and_skips_filesystem_checks(tmp_path, monkeypatch, capsys):
    path, _ = make_catalog(tmp_path, {
        'zeta': tool_spec('zeta'), 'alpha': tool_spec('alpha'), 'beta': tool_spec('beta'),
    })
    def no_filesystem_check(*args):
        raise AssertionError('unexpected filesystem check')

    monkeypatch.setattr('bioenv.cli.installation_status', no_filesystem_check)
    monkeypatch.setattr('bioenv.cli.read_installed_version', no_filesystem_check)
    before = set(tmp_path.rglob('*'))
    assert main(['--config', str(path), 'list', '--simple']) == 0
    assert capsys.readouterr().out == 'alpha\nbeta\nzeta\n'
    assert set(tmp_path.rglob('*')) == before


def test_list_uses_dash_when_installed_package_version_is_unavailable(tmp_path, capsys):
    path, config = make_catalog(tmp_path, {'tool': tool_spec('tool', version='5.0')})
    install_tool(config, 'tool', record=False)
    publish(config, 'tool')
    main(['--config', str(path), 'list'])
    row = capsys.readouterr().out.splitlines()[2]
    assert '—' in row
    assert '5.0' not in row


def test_list_marks_incorrect_launcher_incomplete(tmp_path, capsys):
    path, config = make_catalog(tmp_path, {'tool': tool_spec('tool')})
    install_tool(config, 'tool')
    publish(config, 'tool')
    launcher = Path(config['settings']['bin_dir']) / 'tool'
    launcher.write_text('incorrect launcher', encoding='utf-8')

    assert main(['--config', str(path), 'list']) == 0
    row = capsys.readouterr().out.splitlines()[2]
    assert 'Incomplete' in row


def test_info_without_databases_shows_actual_and_requested_versions(tmp_path, capsys):
    path, config = make_catalog(tmp_path, {
        'tool': tool_spec('tool', version='2.0',
                          commands={'public': 'real-tool', 'alias': 'alias'}),
    })
    prefix = install_tool(config, 'tool', '1.5')
    publish(config, 'tool')
    before = set(tmp_path.rglob('*'))
    assert main(['--config', str(path), 'info', 'tool']) == 0
    output = capsys.readouterr().out
    assert 'Version:      1.5' in output
    assert 'Requested:    2.0' in output
    assert 'Status:       Installed' in output
    assert f'Environment:  {prefix}' in output
    assert 'public (executable: real-tool)' in output
    assert 'Databases:\n  None declared' in output
    assert set(tmp_path.rglob('*')) == before


def test_info_shows_database_status_and_default_environment(tmp_path, capsys):
    databases = [
        {'name': 'light', 'path': 'bakta/db-light', 'env': 'BAKTA_DB'},
        {'name': 'full', 'path': 'bakta/db', 'required': True},
        {'name': 'wrong-type', 'path': 'bakta/archive', 'type': 'file'},
    ]
    path, config = make_catalog(tmp_path, {
        'bakta': tool_spec('bakta', ['bakta'], version='2.0', databases=databases),
    })
    prefix = install_tool(config, 'bakta', '1.8')
    publish(config, 'bakta')
    light = Path(config['settings']['databases_root']) / 'bakta' / 'db-light'
    light.mkdir(parents=True)
    wrong_type = Path(config['settings']['databases_root']) / 'bakta' / 'archive'
    wrong_type.mkdir()
    before = set(tmp_path.rglob('*'))

    assert main(['--config', str(path), 'info', 'bakta']) == 0
    output = capsys.readouterr().out
    assert 'Version:      1.8' in output
    assert 'Requested:    2.0' in output
    assert 'Status:       Installed' in output
    assert f'Environment:  {prefix}' in output
    assert 'light' in output and 'Available' in output
    assert 'full' in output and 'Missing' in output
    assert 'wrong-type' in output and 'Invalid' in output
    assert 'Yes' in output and 'No' in output
    assert str(light) in output
    assert set(tmp_path.rglob('*')) == before


def test_info_database_default_is_unset_without_env_metadata(tmp_path, capsys):
    path, _config = make_catalog(tmp_path, {
        'tool': tool_spec('tool', databases=[{'name': 'db', 'path': 'tool/db'}]),
    })
    assert main(['--config', str(path), 'info', 'tool']) == 0
    output = capsys.readouterr().out
    assert 'db' in output and '—' in output
    assert 'Yes' not in output
