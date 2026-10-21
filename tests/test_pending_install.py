from pathlib import Path

import pytest
import yaml

from bioenv.cli import main
from bioenv.config import load_config
from bioenv.manager import BioenvError, publish


def make_catalog(tmp_path, tools):
    config = {
        'settings': {
            'envs_root': str(tmp_path / 'envs'),
            'bin_dir': str(tmp_path / 'bin'),
            'micromamba': str(tmp_path / 'micromamba'),
            'mamba_root': str(tmp_path / 'mamba'),
        },
        'tools': tools,
    }
    path = tmp_path / 'tools.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    return path, load_config(path)


def spec(name, categories=None):
    value = {'package': name, 'commands': [name]}
    if categories is not None:
        value['categories'] = categories
    return value


def create_installed(config, tool):
    prefix = Path(config['settings']['envs_root']) / tool
    executable = prefix / 'bin' / tool
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    executable.chmod(0o755)
    publish(config, tool)
    return prefix


def test_pending_install_is_alphabetical_and_skips_existing_environments(
    tmp_path, monkeypatch, capsys
):
    path, config = make_catalog(tmp_path, {
        'zeta': spec('zeta'), 'alpha': spec('alpha'), 'present': spec('present'),
        'incomplete': spec('incomplete'),
    })
    create_installed(config, 'present')
    (Path(config['settings']['envs_root']) / 'incomplete').mkdir(parents=True)
    calls = []

    def fake_install(received_config, tool, dry_run=False):
        calls.append(tool)
        Path(received_config['settings']['envs_root']).mkdir(parents=True, exist_ok=True)
        (Path(received_config['settings']['envs_root']) / tool).mkdir()

    monkeypatch.setattr('bioenv.cli.install', fake_install)
    assert main(['--config', str(path), 'install', '--pending']) == 0
    assert calls == ['alpha', 'zeta']
    output = capsys.readouterr().out
    assert '[1/2] alpha OK' in output
    assert '[2/2] zeta OK' in output
    assert 'Skipped:   2' in output
    assert 'Incomplete environments need administrative review: incomplete' in output


def test_pending_install_with_no_missing_tools_does_not_call_installer(
    tmp_path, monkeypatch, capsys
):
    path, config = make_catalog(tmp_path, {'present': spec('present')})
    create_installed(config, 'present')
    monkeypatch.setattr(
        'bioenv.cli.install', lambda *args, **kwargs: pytest.fail('unexpected install')
    )
    assert main(['--config', str(path), 'install', '--pending']) == 0
    assert capsys.readouterr().out.startswith('No pending tools.')


def test_pending_dry_run_shows_plan_and_has_no_side_effects(tmp_path, monkeypatch, capsys):
    path, config = make_catalog(tmp_path, {
        'a': spec('a'), 'b': spec('b'), 'present': spec('present'),
    })
    create_installed(config, 'present')
    before = set(tmp_path.rglob('*'))
    monkeypatch.setattr(
        'bioenv.cli.install', lambda *args, **kwargs: pytest.fail('dry-run installed a tool')
    )
    assert main(['--config', str(path), 'install', '--pending', '--dry-run']) == 0
    output = capsys.readouterr().out
    assert output.startswith('INSTALLATION PLAN')
    assert 'Pending installations: 2' in output
    assert 'Already present:      1' in output
    assert set(tmp_path.rglob('*')) == before


def test_pending_filter_applies_before_limit_and_reports_deferred(
    tmp_path, monkeypatch, capsys
):
    path, _config = make_catalog(tmp_path, {
        'zeta': spec('zeta', ['assembly']),
        'alpha': spec('alpha', ['assembly']),
        'beta': spec('beta', ['amr']),
    })
    calls = []

    def fake_install(received_config, tool, dry_run=False):
        calls.append(tool)
        Path(received_config['settings']['envs_root']).mkdir(parents=True, exist_ok=True)
        (Path(received_config['settings']['envs_root']) / tool).mkdir()

    monkeypatch.setattr('bioenv.cli.install', fake_install)
    assert main([
        '--config', str(path), 'install', '--pending', '--category', 'assembly', '--limit', '1',
    ]) == 0
    assert calls == ['alpha']
    output = capsys.readouterr().out
    assert 'Deferred:  1' in output
    assert 'Pending tools not processed by --limit: zeta' in output


def test_pending_dry_run_applies_limit_without_creating_anything(tmp_path, monkeypatch, capsys):
    path, _config = make_catalog(tmp_path, {
        'alpha': spec('alpha'), 'beta': spec('beta'),
    })
    before = set(tmp_path.rglob('*'))
    monkeypatch.setattr(
        'bioenv.cli.install', lambda *args, **kwargs: pytest.fail('dry-run installed a tool')
    )
    assert main([
        '--config', str(path), 'install', '--pending', '--dry-run', '--limit', '1',
    ]) == 0
    output = capsys.readouterr().out
    assert 'Pending installations: 1' in output
    assert 'Deferred by limit:     1' in output
    assert 'Defer' in output
    assert set(tmp_path.rglob('*')) == before


def test_pending_install_continues_after_tool_and_publication_failures(
    tmp_path, monkeypatch, capsys
):
    path, _config = make_catalog(tmp_path, {
        'alpha': spec('alpha'), 'beta': spec('beta'), 'gamma': spec('gamma'),
    })
    calls = []

    def fake_install(received_config, tool, dry_run=False):
        calls.append(tool)
        Path(received_config['settings']['envs_root']).mkdir(parents=True, exist_ok=True)
        (Path(received_config['settings']['envs_root']) / tool).mkdir()
        if tool == 'beta':
            raise BioenvError('environment created, but launcher publication failed')

    monkeypatch.setattr('bioenv.cli.install', fake_install)
    assert main(['--config', str(path), 'install', '--pending']) == 1
    assert calls == ['alpha', 'beta', 'gamma']
    captured = capsys.readouterr()
    output = captured.out
    errors = captured.err
    assert '[2/3] beta FAILED:' in output
    assert '[3/3] gamma OK' in output
    assert 'Installed: 2' in output and 'Failed:    1' in output
    assert 'beta: environment created, but launcher publication failed' in errors


def test_pending_rerun_does_not_reinstall_created_environments(tmp_path, monkeypatch, capsys):
    path, _config = make_catalog(tmp_path, {'tool': spec('tool')})
    calls = []

    def fake_install(received_config, tool, dry_run=False):
        calls.append(tool)
        Path(received_config['settings']['envs_root']).mkdir(parents=True, exist_ok=True)
        (Path(received_config['settings']['envs_root']) / tool).mkdir()

    monkeypatch.setattr('bioenv.cli.install', fake_install)
    assert main(['--config', str(path), 'install', '--pending']) == 0
    assert main(['--config', str(path), 'install', '--pending']) == 0
    assert calls == ['tool']
    assert 'No pending tools.' in capsys.readouterr().out


def test_pending_install_rejects_invalid_option_combinations(tmp_path, capsys):
    path, _ = make_catalog(tmp_path, {'tool': spec('tool')})
    assert main(['--config', str(path), 'install', '--category', 'assembly']) == 2
    assert '--category and --limit require --pending' in capsys.readouterr().err
    assert main(['--config', str(path), 'install', 'tool', '--pending']) == 2
    assert 'Do not combine TOOL with --pending' in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(['--config', str(path), 'install', '--pending', '--limit', '0'])
