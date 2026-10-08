"""KUAT identity ranks and concrete workspace capabilities."""
RANKS = [
 {'id':'Infinite','name':'最高','alias':'Infinite','color':'#253143','write':['world','entries','timeline','state','archive']},
 {'id':'Orange','name':'创始','alias':'Origin','color':'#d77e25','write':['world','entries','timeline','state','archive']},
 {'id':'White','name':'白羽','alias':'Shiro','color':'#ffffff','write':['world','entries','timeline','state','archive']},
 {'id':'Purple','name':'直属','alias':'Kuat','color':'#9469be','write':['world','entries','timeline','state','archive']},
 {'id':'Darkblue','name':'议会','alias':'Cabinet','color':'#354777','write':['world','entries','timeline','state','archive']},
 {'id':'Red','name':'红级','alias':'Alpha','color':'#c85c66','write':['world','entries','timeline','state']},
 {'id':'Blue','name':'蓝级','alias':'Beta','color':'#5686c4','write':['entries','timeline','state']},
 {'id':'Cyan','name':'青级','alias':'Tech','color':'#389ca6','write':['entries','timeline','state']},
 {'id':'Green','name':'绿级','alias':'Gamma','color':'#62a47b','write':[]},
 {'id':'Grey','name':'灰级','alias':'Omega','color':'#9098a5','write':[]},
]
BY_ID={r['id']:r for r in RANKS}
def permissions(principal):
 return {'write':BY_ID.get(principal.get('rank'),BY_ID['Grey'])['write'], 'issue_accounts':principal.get('role')=='owner'}
