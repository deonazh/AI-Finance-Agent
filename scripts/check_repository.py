"""Check committed-source hygiene and local documentation links without networking."""
from argparse import ArgumentParser
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.venv', 'venv', '__pycache__', 'outputs', 'data', 'uploads', 'originals', 'backups', 'dist'}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true', help='Check source/docs only; explicitly omit Git tracked-file checks.')
    args = parser.parse_args()
    errors = []
    git_status = 'not run (source-only)' if args.source_only else 'not run (no .git directory)'
    if (ROOT/'.git').exists() and not args.source_only:
        tracked = []
        try:
            result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, text=True, check=False)
        except OSError:
            errors.append('Git tracked-file check could not run. Install/configure Git, then retry.')
        else:
            if result.returncode:
                reason = ' Review the pending Xcode license in your local Apple tooling.' if 'license' in result.stderr.lower() else ''
                errors.append('Git tracked-file check failed.' + reason + ' Run git ls-files to diagnose; use --source-only only for a separately labeled source check.')
            else:
                tracked = [Path(name) for name in result.stdout.split('\0') if name]
                git_status = f'checked ({len(tracked)} tracked files)'
                if not tracked:
                    git_status += '; no tracked contents to verify yet'

        for path in tracked:
            private = (path.parts[0] in {'data', 'outputs', 'uploads', 'backups', '.venv'} and path.name != '.gitkeep') or path.suffix in {'.sqlite', '.sqlite3', '.db'} or '.sqlite3-' in path.name or path.name.startswith('fpa_plan') and path.suffix == '.json'
            secret = (path.name.startswith('.env') and path.name != '.env.example') or str(path) == '.streamlit/secrets.toml'
            if private or secret:
                errors.append(f'Private/generated file is tracked: {path}')
    sources = [p for p in ROOT.rglob('*') if p.is_file() and not any(part in SKIP for part in p.relative_to(ROOT).parts)]
    credential = re.compile(r'(?:sk-(?:proj-)?[A-Za-z0-9_-]{35,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})')
    for path in sources:
        if path.suffix not in {'.py', '.md', '.toml', '.yml', '.yaml', '.txt', '.example'}:
            continue
        text = path.read_text()
        if credential.search(text):
            errors.append(f'Possible credential in {path.relative_to(ROOT)} (value not printed)')
        if path.suffix == '.md':
            for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', text):
                parsed = urlsplit(target)
                if parsed.scheme or not parsed.path:
                    continue
                if not (path.parent/unquote(parsed.path)).exists():
                    errors.append(f'Broken local link in {path.relative_to(ROOT)}: {target}')
    if errors:
        raise SystemExit('\n'.join(errors))
    print('Local documentation links and basic source hygiene passed.')
    print('Git tracked-file check: ' + git_status + '.')


if __name__ == '__main__':
    main()
