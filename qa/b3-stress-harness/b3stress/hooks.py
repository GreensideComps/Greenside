import json,sys,datetime
path,slot,t0=sys.argv[1],sys.argv[2],int(sys.argv[3])
t=open(path).read(); d=json.JSONDecoder(); i=0; out=[]
while True:
    j=t.find('{',i)
    if j<0: break
    try:
        o,e=d.raw_decode(t,j); i=e
        if not isinstance(o,dict) or 'eventTimestamp' not in o or o['eventTimestamp']<t0: continue
        req=(o.get('event') or {}).get('request') or {}; u=req.get('url') or ''
        if '/webhooks/' not in u: continue
        logs=[]
        for l in o.get('logs',[]):
            for m in l.get('message',[]):
                try: m=json.loads(m)
                except Exception: pass
                logs.append({k:m.get(k) for k in ('level','event','dry_run','topic','order_gid','webhook_id','reason')} if isinstance(m,dict) else m)
        h=req.get('headers',{})
        out.append({'slot':slot,'at':datetime.datetime.utcfromtimestamp(o['eventTimestamp']/1000).strftime('%H:%M:%S'),'path':u.split('.dev',1)[1],'status':((o['event'].get('response') or {}).get('status')),'outcome':o.get('outcome'),'version':(o.get('scriptVersion') or {}).get('id','')[:8],'topic':h.get('x-shopify-topic'),'shop':h.get('x-shopify-shop-domain'),'api_version':h.get('x-shopify-api-version'),'webhook_id':h.get('x-shopify-webhook-id'),'hmac_header':bool(h.get('x-shopify-hmac-sha256')),'exceptions':len(o.get('exceptions',[])),'logs':logs})
    except ValueError: i=j+1
for x in out: print(json.dumps(x))
