"""Load the bundled synthetic CSV + portable mapping through the authenticated API."""
import argparse
import base64
import getpass
import json
from pathlib import Path
from urllib.parse import urlsplit
import httpx

ROOT=Path(__file__).resolve().parent.parent

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin',default='http://127.0.0.1:8080');parser.add_argument('--email',required=True)
    group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--workspace');group.add_argument('--create-workspace')
    args=parser.parse_args();parts=urlsplit(args.origin)
    if parts.scheme!='https' and not (parts.scheme=='http' and parts.hostname in ('127.0.0.1','localhost','::1')):parser.error('Use HTTPS, except on loopback.')
    if parts.path not in ('','/') or parts.username or parts.query or parts.fragment:parser.error('Use an origin without credentials or a path.')
    with httpx.Client(base_url=args.origin.rstrip('/'),timeout=30,trust_env=False) as client:
        def request(method,url,**kwargs):
            response=client.request(method,url,**kwargs);response.raise_for_status();return response.json()
        signed=request('POST','/api/auth/login',json={'email':args.email,'password':getpass.getpass('Password: '),'otp':getpass.getpass('Authenticator/recovery code (blank when not enabled): ')})
        client.headers['X-CSRF-Token']=signed['csrf']
        try:
            ws=args.workspace or request('POST','/api/workspaces',json={'name':args.create_workspace})['id'];root='/api/workspaces/'+ws
            source=request('POST',root+'/files',json={'name':'orders.csv','content_base64':base64.b64encode((ROOT/'examples/orders.csv').read_bytes()).decode()})
            project=request('POST',root+'/projects',json={'name':'Accepted revenue by region','document':json.loads((ROOT/'examples/portable-project.json').read_text())})
            print(json.dumps({'workspace_id':ws,'project_id':project['id'],'source_id':source['id'],'expected_result':[['North',130.0],['South',40.0]],'next':'Open Projects > Run pipeline. Choose orders.csv, compile, review and queue.'},indent=2))
        finally:request('POST','/api/auth/logout')

if __name__=='__main__':main()
