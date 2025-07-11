import os
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor


def main():
    if len(sys.argv) != 5:
        print(f"Usage: {sys.argv[0]} TEMPLATE DATA_DIR OUTPUT_DIR WORKERS", file=sys.stderr)
        sys.exit(1)

    template_path = sys.argv[1]
    data_dir = sys.argv[2]
    output_dir = sys.argv[3]
    workers = int(sys.argv[4])

    with open(template_path, 'r') as f:
        template = f.read()

    os.makedirs(output_dir, exist_ok=True)

    codex_bin = os.path.join(os.path.dirname(__file__), 'codex-x86_64-unknown-linux-musl')

    def run_on_file(path):
        with open(path, 'r') as f:
            data = f.read()
        prompt = template + "\n" + data
        output_path = os.path.join(output_dir, os.path.basename(path) + '-codex')
        cmd = [
            codex_bin,
            'exec',
            '--output-last-message', output_path,
            '--dangerously-bypass-approvals-and-sandbox',
            '--skip-git-repo-check',
            '-C', os.getcwd(),
        ]
        subprocess.run(cmd, input=prompt.encode(), check=True)

    files = [os.path.join(data_dir, f) for f in os.listdir(data_dir) if os.path.isfile(os.path.join(data_dir, f))]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(run_on_file, files))


if __name__ == '__main__':
    main()
