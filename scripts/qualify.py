"""Network-enabled deployment qualification helper. Does not approve human gates."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent.parent

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'qualification-output');args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True,mode=0o700)
    tests=[('dependencies',[sys.executable,'-m','pip','check']),
           ('vulnerability-audit',[sys.executable,'-m','pip_audit','--format','json','--output',str(output/'pip-audit.json')]),
           ('all-tests',[sys.executable,'scripts/check_all.py','--origin'])]
    results=[]
    for name,command in tests:
        with (output/(name+'.log')).open('w') as log:
            try:
                result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=900)
                results.append({'check':name,'status':'passed' if result.returncode==0 else 'failed','exit_code':result.returncode})
            except (OSError,subprocess.TimeoutExpired) as exc:results.append({'check':name,'status':'blocked','error':str(exc)})
        print(name+': '+results[-1]['status'],flush=True)
    freeze=subprocess.run([sys.executable,'-m','pip','freeze','--all'],capture_output=True,text=True,timeout=60)
    (output/'resolved-environment.txt').write_text(freeze.stdout)
    report={'generated_at':time.time(),'checks':results,'approved_for_production':False,
            'notice':'Test and audit execution only. Still obtain documented operator sign-off for business acceptance, independent security, deployment capacity, real off-host recovery, alert delivery and storage encryption.'}
    (output/'automated-results.json').write_text(json.dumps(report,indent=2))
    return 0 if all(r['status']=='passed' for r in results) else 1

if __name__=='__main__':raise SystemExit(main())
