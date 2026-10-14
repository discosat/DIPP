import sys, multiprocessing as mp
sys.path.insert(0,"tools"); sys.path.insert(0,"analysis")
import policy_lab as L, lab_energy_deficit as E
V2=E.V2
def pols():
    return [L.Static("low"),L.Static("med"),L.Static("high"),L.Ladder(**V2,name="v2"),
            L.Ladder(**V2,energy_feas=True,name="E1"),L.Ladder(**V2,energy_time=True,name="ET"),
            L.Ladder(**V2,energy_feas=True,energy_time=True,name="E1+ET"),
            L.Ladder(**V2,energy_time=True,energy_future=True,name="ETF"),
            L.Ladder(**V2,energy_feas=True,energy_time=True,energy_future=True,name="E1+ETF"),
            L.Ladder(**V2,energy_feas=True,energy_time=True,energy_future=True,energy_service=True,name="all")]
E.policies=pols
J=[(20,dr,rg,c,sd) for rg in ("dd3.5","dd3.7","dd3.9","disco") for c in ("C1","C2","C3","C4") for dr in (30,300,1800,21600) for sd in (1,2)]
def lambda_run(ij):
    return ij[0], E.run(ij[1])
if __name__=="__main__":
    import time
    t0=time.time(); R=[None]*len(J)
    with mp.Pool(7) as p:
        for k,(i,r) in enumerate(p.imap_unordered(lambda_run, list(enumerate(J)))):
            R[i]=r
            print(f"  done {k+1}/{len(J)} {J[i]} {time.time()-t0:.0f}s", flush=True)
    from collections import defaultdict
    agg=defaultdict(list)
    for j,r in zip(J,R):
        b=max(r["U:static-"+e] for e in ("low","med","high"))
        for n in ("v2","E1","ET","E1+ET","ETF","E1+ETF","all"):
            agg[(j[2],j[1],n)].append(r["U:"+n]-b)
    for rg in ("dd3.5","dd3.7","dd3.9","disco"):
        for dr in (30,300,1800,21600):
            print(rg,dr," ".join(f"{n}:{min(agg[(rg,dr,n)]):+.3f}/{sum(agg[(rg,dr,n)])/len(agg[(rg,dr,n)]):+.3f}" for n in ("v2","E1","ET","E1+ET","ETF","E1+ETF","all")))
