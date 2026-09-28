import sys, time, json, os
from pathlib import Path
from datetime import timedelta
ROOT=Path(os.environ.get('MARKETLENS_REVIEW_ROOT',str(Path(__file__).resolve().parent.parent/'work/source/chlansgus-claude-marketlens-investment-system-iyki44')))
sys.path.insert(0,str(ROOT/'backend'))
from tests.integration.test_service_api import make_service
from marketlens.infrastructure.db.models import RecommendationRow
from sqlalchemy import event
svc=make_service(universe=10)
_,_,rid=svc.analyze('NVDA')
with svc.sf() as s:
    row=s.get(RecommendationRow,rid)
    cols={c.name:getattr(row,c.name) for c in RecommendationRow.__table__.columns if c.name!='id'}
    snapshot_bytes=len(json.dumps(cols['inputs']))+len(json.dumps(cols['result']))+len(json.dumps(cols['model_config_snapshot']))
    for i in range(99):
        s.add(RecommendationRow(**(cols|{'as_of':row.as_of-timedelta(minutes=i+1)})))
    s.commit()
loaded=[]
def on_load(target,context): loaded.append(target.id)
event.listen(RecommendationRow,'load',on_load)
with svc.sf() as s:
    t=time.perf_counter()
    rows=svc.company_recommendations(s,'NVDA',limit=1)
    elapsed=time.perf_counter()-t
event.remove(RecommendationRow,'load',on_load)
print(json.dumps({'stored_recommendations':100,'requested_limit':1,'returned':len(rows),
 'orm_rows_loaded':len(loaded),'snapshot_bytes_per_row':snapshot_bytes,'elapsed_seconds':round(elapsed,4)},indent=2))
