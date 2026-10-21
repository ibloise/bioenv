import pytest
import yaml

from bioenv.cli import main
from bioenv.config import ConfigError, load_config


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


def test_catalog_accepts_missing_and_multiple_categories(tmp_path):
    _, config = make_catalog(tmp_path, {
        'legacy': spec('legacy'),
        'assembler': spec('assembler', ['assembly', 'long-reads']),
    })
    assert 'categories' not in config['tools']['legacy']
    assert config['tools']['assembler']['categories'] == ['assembly', 'long-reads']


@pytest.mark.parametrize('categories', [
    'assembly', [None], [''], ['   '], ['assembly', 'assembly'],
])
def test_catalog_rejects_invalid_categories(tmp_path, categories):
    path = tmp_path / 'invalid.yaml'
    path.write_text(yaml.safe_dump({'tools': {'bad': {
        'package': 'bad', 'commands': ['bad'], 'categories': categories,
    }}}), encoding='utf-8')
    with pytest.raises(ConfigError, match='categories|duplicate category'):
        load_config(path)


def test_list_category_filters_exactly_and_simple_mode_stays_plain(tmp_path, capsys):
    path, _ = make_catalog(tmp_path, {
        'flye': spec('flye', ['assembly', 'long-reads']),
        'mafft': spec('mafft', ['alignment']),
        'unicycler': spec('unicycler', ['assembly']),
    })
    assert main(['--config', str(path), 'list', '--category', 'assembly', '--simple']) == 0
    assert capsys.readouterr().out == 'flye\nunicycler\n'
    assert main(['--config', str(path), 'list', '--category', 'Assembly', '--simple']) == 0
    assert capsys.readouterr().out == ''


def test_list_groups_alphabetically_and_repeats_multi_category_tools(tmp_path, capsys):
    path, _ = make_catalog(tmp_path, {
        'unicycler': spec('unicycler', ['assembly', 'hybrid-assembly']),
        'flye': spec('flye', ['assembly']),
        'mafft': spec('mafft', ['alignment']),
        'legacy': spec('legacy'),
    })
    assert main(['--config', str(path), 'list', '--group-by', 'category']) == 0
    output = capsys.readouterr().out
    assert output.index('ALIGNMENT') < output.index('ASSEMBLY')
    assert output.index('ASSEMBLY') < output.index('HYBRID-ASSEMBLY')
    assert output.index('HYBRID-ASSEMBLY') < output.index('UNCATEGORIZED')
    assert sum(line.startswith('unicycler ') for line in output.splitlines()) == 2
    assembly = output.split('ASSEMBLY\n', 1)[1].split('\n\n', 1)[0]
    assert assembly.index('flye') < assembly.index('unicycler')


def test_list_category_and_group_show_only_requested_group(tmp_path, capsys):
    path, _ = make_catalog(tmp_path, {
        'flye': spec('flye', ['assembly', 'long-reads']),
        'mafft': spec('mafft', ['alignment']),
    })
    assert main([
        '--config', str(path), 'list', '--category', 'assembly', '--group-by', 'category',
    ]) == 0
    output = capsys.readouterr().out
    assert 'ASSEMBLY' in output
    assert 'LONG-READS' not in output
    assert 'mafft' not in output


def test_categories_counts_declarations_once_per_category_and_info_lists_them(
    tmp_path, capsys
):
    path, _ = make_catalog(tmp_path, {
        'flye': spec('flye', ['assembly', 'long-reads']),
        'unicycler': spec('unicycler', ['assembly']),
        'mafft': spec('mafft', ['alignment']),
        'legacy': spec('legacy'),
    })
    assert main(['--config', str(path), 'categories']) == 0
    output = capsys.readouterr().out
    assert output.index('alignment') < output.index('assembly') < output.index('long-reads')
    assert '\nassembly    2\n' in output
    assert '\nalignment   1\n' in output
    assert '\nlong-reads  1\n' in output
    assert 'UNCATEGORIZED' not in output

    assert main(['--config', str(path), 'info', 'flye']) == 0
    assert 'Categories:   assembly, long-reads' in capsys.readouterr().out


def test_grouped_simple_list_has_no_duplicate_or_group_headers(tmp_path, capsys):
    path, _ = make_catalog(tmp_path, {
        'tool': spec('tool', ['a', 'b']),
        'other': spec('other', ['a']),
    })
    assert main([
        '--config', str(path), 'list', '--group-by', 'category', '--simple',
    ]) == 0
    assert capsys.readouterr().out == 'other\ntool\n'
