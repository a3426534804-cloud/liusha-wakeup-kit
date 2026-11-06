"""Read-only local dashboard. Never starts wakeup or keepalive jobs."""
import argparse
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from .usage import ledger
from .demo import create

WEB=Path(__file__).resolve().parent.parent/'web'
if not WEB.is_dir():WEB=Path(sys.prefix)/'share/liusha/web'


def run():
    parser=argparse.ArgumentParser()
    parser.add_argument('--demo',action='store_true')
    parser.add_argument('--data-dir',type=Path,default=Path('./var/demo'))
    parser.add_argument('--usage-log',type=Path)
    parser.add_argument('--runtime-state',type=Path)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8779)
    args=parser.parse_args()
    if args.demo:
        args.usage_log=create(args.data_dir)
        args.runtime_state=args.data_dir/'runtime.json'
    if not args.usage_log:
        parser.error('Choose --demo or --usage-log. Real log mode is read-only.')
    token=os.environ.get('LIUSHA_DASHBOARD_TOKEN','')
    if args.host not in ('127.0.0.1','localhost','::1') and not token:
        parser.error('Non-local bind requires LIUSHA_DASHBOARD_TOKEN and a TLS reverse proxy.')

    class Handler(BaseHTTPRequestHandler):
        def reply(self,body,status=200,content_type='application/json; charset=utf-8'):
            payload=body if isinstance(body,bytes) else json.dumps(body,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)

        def do_GET(self):
            url=urlsplit(self.path)
            if url.path.startswith('/api/'):
                supplied=self.headers.get('Authorization','')
                if token and not hmac.compare_digest(supplied,'Bearer '+token):
                    return self.reply({'error':'unauthorized'},401)
                if url.path=='/api/usage':
                    q=parse_qs(url.query)
                    period=q.get('period',['24h'])[0];source=q.get('source',['all'])[0]
                    if period not in ('today','24h','7d','all') or source not in ('all','chat','bubble','cc_wakeup','cc_wakeup_setup','cache_keepalive','other'):
                        return self.reply({'error':'invalid filter'},400)
                    try: offset=int(q.get('offset',['0'])[0])
                    except ValueError:return self.reply({'error':'invalid offset'},400)
                    return self.reply(ledger(args.usage_log,period=period,source=source,offset=offset))
                if url.path=='/api/runtime':
                    state={}
                    if args.runtime_state:
                        try: state=json.loads(args.runtime_state.read_text(encoding='utf-8'))
                        except (OSError,ValueError):pass
                    allowed=('status','checked_at','last_refresh','next_due','last_cache_read','last_cache_created','disabled')
                    return self.reply({'demo':args.demo,**{k:state.get(k) for k in allowed}})
                return self.reply({'error':'not found'},404)
            assets={'/':'index.html','/style.css':'style.css','/app.js':'app.js'}
            if url.path not in assets:return self.reply({'error':'not found'},404)
            name=assets[url.path];types={'html':'text/html','css':'text/css','js':'application/javascript'}
            return self.reply((WEB/name).read_bytes(),content_type=types[name.split('.')[-1]]+'; charset=utf-8')

        def do_POST(self):
            self.reply({'error':'read-only dashboard; no model execution endpoint'},405)

        def log_message(self,fmt,*args):
            # Do not put query strings, bearer tokens or local paths in logs.
            pass

    print(f'流沙 read-only dashboard: http://{args.host}:{args.port} | demo={args.demo}',flush=True)
    ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()


if __name__=='__main__':run()
