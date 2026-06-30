from  .models import Revision
from .cpm import run_cpm

rev=Revision.objects.get(pk=1)
res=run_cpm(rev)

print(res.summary())