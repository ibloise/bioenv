import shutil
import subprocess
from pathlib import Path

import pytest

from bioenv.cli import main
from bioenv.manager import BioenvError, publish, remove, remove_plan


def setup_catalog(tmp_path, commands=None):
    envs = tmp_path / 'envs'
    bindir = tmp_path / 'global-bin'
    micromamba = tmp_path / 'micromamba'
    micromamba.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    micromamba.chmod(0o755)
    config = {
        'settings': {
            'envs_root': str(envs), 'bin_dir': str(bindir),
            'micromamba': str(micromamba), 'mamba_root': str(tmp_path / 'mamba-root'),
        },
        'tools': {
            'spades': {
                'package': 'spades',
                'commands': commands or {'spades': 'spades.py', 'spades.py': 'spades.py'},
            },
        },
    }
    return config


def create_conda_env(config, tool='spades'):
    prefix = Path(config['settings']['envs_root']) / tool
    metadata = prefix / 'conda-meta'
    metadata.mkdir(parents=True)
    (metadata / 'history').write_text('', encoding='utf-8')
    executable = prefix / 'bin' / 'spades.py'
    executable.parent.mkdir()
    executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    executable.chmod(0o755)
    return prefix


def publish_launchers(config):
    return publish(config, 'spades')


def test_remove_complete_calls_micromamba_then_unlinks_all_alias_launchers(
    tmp_path, monkeypatch,
):
    config = setup_catalog(tmp_path)
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)
    observed = {}

    def micromamba(command, **kwargs):
        observed['command'] = command
        observed['env'] = kwargs['env']
        shutil.rmtree(prefix)

    monkeypatch.setattr('bioenv.manager.subprocess.run', micromamba)
    result = remove(config, 'spades')
    assert observed['command'] == [
        config['settings']['micromamba'], 'env', 'remove', '-p', str(prefix), '-y'
    ]
    assert observed['env']['MAMBA_ROOT_PREFIX'] == config['settings']['mamba_root']
    assert not prefix.exists()
    assert all(not launcher.exists() for launcher in launchers)
    assert result['environment_exists']


def test_remove_dry_run_is_read_only_and_reports_plan(tmp_path, monkeypatch):
    config = setup_catalog(tmp_path)
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)
    before = set(tmp_path.rglob('*'))
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run',
        lambda *args, **kwargs: pytest.fail('dry-run must not call micromamba'),
    )
    plan = remove(config, 'spades', dry_run=True)
    assert plan['command'][1:] == ['env', 'remove', '-p', str(prefix), '-y']
    assert [state for _, state in plan['launchers']] == ['managed', 'managed']
    assert set(tmp_path.rglob('*')) == before
    assert prefix.is_dir() and all(path.exists() for path in launchers)


def test_remove_launchers_only_keeps_environment_and_skips_micromamba(tmp_path, monkeypatch):
    config = setup_catalog(tmp_path)
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run',
        lambda *args, **kwargs: pytest.fail('launchers-only must not call micromamba'),
    )
    remove(config, 'spades', launchers_only=True)
    assert prefix.is_dir()
    assert all(not path.exists() for path in launchers)


def test_remove_recovers_launchers_after_environment_was_already_removed(tmp_path, monkeypatch):
    config = setup_catalog(tmp_path)
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)
    shutil.rmtree(prefix)
    plan = remove_plan(config, 'spades')
    assert not plan['environment_exists']
    assert plan['command'] is None
    assert [state for _, state in plan['launchers']] == ['managed', 'managed']
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run',
        lambda *args, **kwargs: pytest.fail('absent environments must not invoke micromamba'),
    )
    remove(config, 'spades', launchers_only=True)
    assert all(not launcher.exists() for launcher in launchers)


def test_remove_dry_run_reports_orphan_launchers_without_running_micromamba(
    tmp_path, monkeypatch, capsys,
):
    config = setup_catalog(tmp_path)
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)
    shutil.rmtree(prefix)
    config_path = _cli_config(config, tmp_path)
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run',
        lambda *args, **kwargs: pytest.fail('dry-run must not invoke micromamba'),
    )
    assert main(['--config', str(config_path), 'remove', 'spades', '--dry-run']) == 0
    output = capsys.readouterr().out
    assert 'already absent; no environment removal will be attempted' in output
    assert output.count('(managed)') == 2
    assert all(launcher.exists() for launcher in launchers)


def test_remove_repeated_after_complete_removal_is_noop(tmp_path, monkeypatch):
    config = setup_catalog(tmp_path, commands=['spades.py'])
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)

    def micromamba(command, **kwargs):
        shutil.rmtree(prefix)

    monkeypatch.setattr('bioenv.manager.subprocess.run', micromamba)
    remove(config, 'spades')
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run',
        lambda *args, **kwargs: pytest.fail('repeat removal must not invoke micromamba'),
    )
    result = remove(config, 'spades')
    assert not result['environment_exists']
    assert all(not launcher.exists() for launcher in launchers)


def test_remove_environment_never_deletes_declared_database(tmp_path, monkeypatch):
    config = setup_catalog(tmp_path, commands=['spades.py'])
    config['settings']['databases_root'] = str(tmp_path / 'databases')
    config['tools']['spades']['databases'] = [{
        'name': 'index', 'path': 'spades/index', 'required': True,
    }]
    prefix = create_conda_env(config)
    database = Path(config['settings']['databases_root']) / 'spades' / 'index'
    database.mkdir(parents=True)
    (database / 'keep').write_text('data', encoding='utf-8')

    def micromamba(command, **kwargs):
        shutil.rmtree(prefix)

    monkeypatch.setattr('bioenv.manager.subprocess.run', micromamba)
    remove(config, 'spades')
    assert (database / 'keep').read_text(encoding='utf-8') == 'data'


def test_remove_launchers_only_cli_with_yes(tmp_path, monkeypatch, capsys):
    config = setup_catalog(tmp_path, commands=['spades.py'])
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)
    config_path = _cli_config(config, tmp_path)
    monkeypatch.setattr('sys.stdin', type('Pipe', (), {'isatty': lambda self: False})())
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run',
        lambda *args, **kwargs: pytest.fail('launchers-only must not call micromamba'),
    )
    assert main([
        '--config', str(config_path), 'remove', 'spades', '--launchers-only', '--yes'
    ]) == 0
    assert 'environment was preserved' in capsys.readouterr().out
    assert prefix.is_dir() and not launchers[0].exists()


def test_remove_cli_yes_skips_prompt(tmp_path, monkeypatch, capsys):
    config = setup_catalog(tmp_path)
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)
    config_path = tmp_path / 'tools.yaml'
    config_path.write_text(
        f"settings:\n  envs_root: {config['settings']['envs_root']}\n"
        f"  bin_dir: {config['settings']['bin_dir']}\n"
        f"  micromamba: {config['settings']['micromamba']}\n"
        f"  mamba_root: {config['settings']['mamba_root']}\n"
        'tools:\n  spades:\n    package: spades\n    commands:\n'
        '      spades: spades.py\n      spades.py: spades.py\n', encoding='utf-8'
    )

    def micromamba(command, **kwargs):
        shutil.rmtree(prefix)

    monkeypatch.setattr('bioenv.manager.subprocess.run', micromamba)
    monkeypatch.setattr('builtins.input', lambda *args: pytest.fail('unexpected prompt'))
    assert main(['--config', str(config_path), 'remove', 'spades', '--yes']) == 0
    assert 'Removed environment' in capsys.readouterr().out
    assert all(not path.exists() for path in launchers)


def _cli_config(config, tmp_path):
    file = tmp_path / 'tools.yaml'
    file.write_text(
        f"settings:\n  envs_root: {config['settings']['envs_root']}\n"
        f"  bin_dir: {config['settings']['bin_dir']}\n"
        f"  micromamba: {config['settings']['micromamba']}\n"
        f"  mamba_root: {config['settings']['mamba_root']}\n"
        'tools:\n  spades:\n    package: spades\n    commands:\n'
        '      spades: spades.py\n      spades.py: spades.py\n', encoding='utf-8'
    )
    return file


def test_remove_interactive_cancel_does_not_change_resources(tmp_path, monkeypatch, capsys):
    config = setup_catalog(tmp_path)
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)
    config_path = _cli_config(config, tmp_path)

    class TTY:
        def isatty(self):
            return True

    monkeypatch.setattr('sys.stdin', TTY())
    monkeypatch.setattr('builtins.input', lambda prompt: 'n')
    assert main(['--config', str(config_path), 'remove', 'spades']) == 1
    assert 'cancelled' in capsys.readouterr().out.lower()
    assert prefix.is_dir() and all(path.exists() for path in launchers)


def test_remove_without_terminal_requires_yes(tmp_path, monkeypatch, capsys):
    config = setup_catalog(tmp_path)
    create_conda_env(config)
    publish_launchers(config)
    config_path = _cli_config(config, tmp_path)
    monkeypatch.setattr('sys.stdin', type('Pipe', (), {'isatty': lambda self: False})())
    assert main(['--config', str(config_path), 'remove', 'spades']) == 2
    assert '--yes' in capsys.readouterr().err


def test_remove_unknown_tool_and_invalid_environment_are_rejected(tmp_path):
    config = setup_catalog(tmp_path)
    with pytest.raises(BioenvError, match='Unknown tool'):
        remove_plan(config, 'missing')
    (Path(config['settings']['envs_root']) / 'spades').mkdir(parents=True)
    with pytest.raises(BioenvError, match='not a valid Conda environment'):
        remove_plan(config, 'spades')


def test_external_conda_environment_is_removable_from_catalog_authority(tmp_path, monkeypatch):
    config = setup_catalog(tmp_path, commands=['spades.py'])
    prefix = create_conda_env(config)
    called = []

    def micromamba(command, **kwargs):
        called.append(command)
        shutil.rmtree(prefix)

    monkeypatch.setattr('bioenv.manager.subprocess.run', micromamba)
    remove(config, 'spades')
    assert not prefix.exists()
    assert called


@pytest.mark.parametrize('commands', [['spades.py'], {'spades': 'spades.py', 'spades.py': 'spades.py'}])
def test_remove_handles_missing_launchers_and_yaml_command_shapes(tmp_path, monkeypatch, commands):
    config = setup_catalog(tmp_path, commands=commands)
    prefix = create_conda_env(config)
    observed = []

    def micromamba(command, **kwargs):
        observed.append(command)
        shutil.rmtree(prefix)

    monkeypatch.setattr('bioenv.manager.subprocess.run', micromamba)
    result = remove(config, 'spades')
    assert observed
    assert all(state == 'missing' for _, state in result['launchers'])


@pytest.mark.parametrize('owner,content', [
    ('foreign', '#!/bin/sh\nnot bioenv\n'),
    ('spades', '#!/bin/sh\n# Managed by bioenv; tool=spades\nmanual edit\n'),
    ('other', '#!/bin/sh\n# Managed by bioenv; tool=other\n'),
])
def test_launcher_conflicts_block_without_mutation(tmp_path, monkeypatch, owner, content):
    config = setup_catalog(tmp_path, commands=['spades.py'])
    prefix = create_conda_env(config)
    launcher = Path(config['settings']['bin_dir']) / 'spades.py'
    launcher.parent.mkdir()
    launcher.write_text(content, encoding='utf-8')
    launcher.chmod(0o755)
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run',
        lambda *args, **kwargs: pytest.fail('conflict must be detected before micromamba'),
    )
    with pytest.raises(BioenvError, match='Launcher conflict'):
        remove(config, 'spades')
    assert prefix.is_dir() and launcher.read_text(encoding='utf-8') == content


def test_symlink_launcher_and_symlink_environment_are_rejected(tmp_path):
    config = setup_catalog(tmp_path, commands=['spades.py'])
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)
    target = tmp_path / 'outside'
    target.write_text('keep', encoding='utf-8')
    launchers[0].unlink()
    launchers[0].symlink_to(target)
    assert remove_plan(config, 'spades')['blocked']
    assert prefix.is_dir() and target.read_text() == 'keep'
    launchers[0].unlink()
    prefix.rename(tmp_path / 'external-env')
    prefix.symlink_to(tmp_path / 'external-env', target_is_directory=True)
    with pytest.raises(BioenvError, match='symlink'):
        remove_plan(config, 'spades')


def test_micromamba_failure_keeps_environment_and_launchers(tmp_path, monkeypatch):
    config = setup_catalog(tmp_path)
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)
    monkeypatch.setattr(
        'bioenv.manager.subprocess.run',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(23, args[0])
        ),
    )
    with pytest.raises(BioenvError, match='exit status 23.*inspection'):
        remove(config, 'spades')
    assert prefix.is_dir() and all(path.exists() for path in launchers)


def test_micromamba_success_without_removing_prefix_keeps_launchers(tmp_path, monkeypatch):
    config = setup_catalog(tmp_path)
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)
    monkeypatch.setattr('bioenv.manager.subprocess.run', lambda *args, **kwargs: None)
    with pytest.raises(BioenvError, match='reported success but the environment still exists'):
        remove(config, 'spades')
    assert prefix.is_dir() and all(path.exists() for path in launchers)


def test_launcher_unlink_failure_reports_pending_resource_after_env_removal(
    tmp_path, monkeypatch,
):
    config = setup_catalog(tmp_path, commands=['spades.py'])
    prefix = create_conda_env(config)
    launchers = publish_launchers(config)

    def micromamba(command, **kwargs):
        shutil.rmtree(prefix)

    monkeypatch.setattr('bioenv.manager.subprocess.run', micromamba)
    monkeypatch.setattr(Path, 'unlink', lambda self, *a, **k: (_ for _ in ()).throw(
        PermissionError('denied')) if self == launchers[0] else Path.unlink(self, *a, **k))
    with pytest.raises(BioenvError, match='resources pending removal.*spades.py'):
        remove(config, 'spades')
    assert not prefix.exists() and launchers[0].exists()


def test_remove_of_absent_environment_does_not_delete_launcher_or_create_root(tmp_path):
    config = setup_catalog(tmp_path, commands=['spades.py'])
    bin_dir = Path(config['settings']['bin_dir'])
    bin_dir.mkdir()
    suspicious = bin_dir / 'spades.py'
    suspicious.write_text('unverified', encoding='utf-8')
    with pytest.raises(BioenvError, match='Launcher conflict'):
        remove(config, 'spades')
    assert suspicious.read_text() == 'unverified'
    assert not Path(config['settings']['envs_root']).exists()


def test_remove_cli_dry_run_lists_conflict_and_changes_nothing(tmp_path, capsys):
    config = setup_catalog(tmp_path, commands=['spades.py'])
    prefix = create_conda_env(config)
    launcher = Path(config['settings']['bin_dir']) / 'spades.py'
    launcher.parent.mkdir()
    launcher.write_text('unrelated', encoding='utf-8')
    config_path = _cli_config(config, tmp_path)
    assert main(['--config', str(config_path), 'remove', 'spades', '--dry-run']) == 2
    output = capsys.readouterr()
    assert 'conflict: not managed by this tool' in output.out
    assert 'blocked' in output.err.lower()
    assert prefix.is_dir() and launcher.read_text() == 'unrelated'


def test_remove_rejects_parent_components_and_critical_prefix(tmp_path):
    config = setup_catalog(tmp_path)
    config['settings']['envs_root'] = str(tmp_path / 'safe' / '..' / 'unsafe')
    with pytest.raises(BioenvError, match='unsafe environment root'):
        remove_plan(config, 'spades')
    config['settings']['envs_root'] = '/'
    config['tools']['etc'] = {'package': 'etc', 'commands': ['etc']}
    with pytest.raises(BioenvError, match='critical system directory'):
        remove_plan(config, 'etc')
