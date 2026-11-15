"""Fast source-level checks, independent of network and browser runtime."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / 'index.html'


def source():
    return HTML.read_text(encoding='utf-8')


def test_single_file_deployment_and_version():
    text = source()
    assert '<!DOCTYPE html>' in text or '<!doctype html>' in text
    assert "version: '1.1.0'" in text
    assert '<script>' in text
    assert 'const APP =' in text


def test_repository_link_and_source_features():
    text = source()
    assert text.count('https://github.com/theraghavraman/dataforge') >= 2
    for identifier in ['btnSampleMenu', 'studioMode', 'dialogOverlay', 'repoNav', 'projectInput']:
        assert identifier in text
    assert "ctx.vars.$$WF_ROWS='0'" in text
    assert "createWorkflow()" in text


def test_javascript_syntax():
    node = shutil.which('node')
    assert node is not None, 'Install Node.js 20+ to check inline JavaScript syntax.'
    scripts = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script\s*>', source(), flags=re.I | re.S)
    assert scripts, 'Expected inline JavaScript.'
    with tempfile.TemporaryDirectory() as tmp:
        for number, js in enumerate(scripts):
            path = Path(tmp) / f'inline-{number}.js'
            path.write_text(js, encoding='utf-8')
            subprocess.run([node, '--check', str(path)], check=True, capture_output=True, text=True)
