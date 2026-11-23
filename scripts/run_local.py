"""Start local API, worker and maintenance together; never a production supervisor."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parent.parent

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8080);args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('Port must be between 1024 and 65535.')
    env=os.environ.copy()
    if env.get('DATAFORGE_ENV','development')=='production':parser.error('Use the reviewed service supervisor for production, not this developer helper.')
    origin=env.setdefault('DATAFORGE_ORIGIN',f'http://127.0.0.1:{args.port}')
    if origin!=f'http://127.0.0.1:{args.port}':parser.error('Local origin must exactly match http://127.0.0.1:<port>.')
    env.setdefault('DATAFORGE_DATA_DIR',str(ROOT/'var'))
    # Initialization refuses to replace a key belonging to encrypted existing data.
    subprocess.run([sys.executable,'-m','app.cli','init'],cwd=ROOT,env=env,check=True)
    processes=[];stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    try:
        for command in (['serve','--port',str(args.port)],['worker'],['maintenance']):
            processes.append(subprocess.Popen([sys.executable,'-m','app.cli',*command],cwd=ROOT,env=env))
        print('Local services: '+origin+' | Ctrl+C stops all three. Provision an administrator separately.',flush=True)
        while not stopped:
            if any(p.poll() is not None for p in processes):raise RuntimeError('A service exited. Stopping the remaining local processes.')
            time.sleep(.2)
        return 0
    finally:
        for process in processes:
            if process.poll() is None:process.terminate()
        for process in processes:
            try:process.wait(timeout=40)
            except subprocess.TimeoutExpired:process.kill();process.wait()

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,RuntimeError,subprocess.SubprocessError) as exc:print(str(exc),file=sys.stderr);raise SystemExit(1)
