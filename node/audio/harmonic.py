"""Wzorzec detektora z aplikacji (Harmonic.kt). Użycie: python3 harmonic.py plik.wav ...
Tonalny grzebień harmonicznych: czy w oknie jest silnik o stałych obrotach (f0 40–250 Hz + harmoniczne)."""
import sys, numpy as np
import wave
def load(p):
    """WAV 16 kHz mono/stereo 16-bit → float32."""
    w=wave.open(p); x=np.frombuffer(w.readframes(w.getnframes()),np.int16).astype(np.float32)/32768
    if w.getnchannels()>1: x=x.reshape(-1,w.getnchannels()).mean(1)
    assert w.getframerate()==16000, 'potrzebne 16 kHz'
    return x
SR=16000
def frames(x, win=1.0, hop=0.5):
    n=int(win*SR); h=int(hop*SR); w=np.hanning(n)
    for s in range(0, len(x)-n, h):
        yield s/SR, np.abs(np.fft.rfft(x[s:s+n]*w))
def comb(spec, fres=1.0):
    # log-widmo wybielone (odjęte tło medianowe), potem suma na harmonicznych k*f0 (k=1..8)
    lg=np.log(spec+1e-9)
    from numpy.lib.stride_tricks import sliding_window_view
    k=31; pad=np.pad(lg,(k//2,k//2),mode='edge')
    bg=np.median(sliding_window_view(pad,k),axis=1)
    wh=np.clip(lg-bg,0,None)
    best=(0,0)
    for f0 in np.arange(40,250,0.5):
        idx=(np.arange(1,9)*f0/fres).round().astype(int)
        idx=idx[idx<len(wh)-1]
        v=np.mean([max(wh[i-1],wh[i],wh[i+1]) for i in idx])
        if v>best[0]: best=(v,f0)
    return best
TONAL=1.2
def fold(f):
    """f0 do oktawy 70–140 Hz (detektor czasem łapie 2·f0)."""
    while f>=140: f/=2
    return f
def presence(win):
    """Okna (siła, f0) → (ile trzyma wspólną złożoną wysokość ±4 %, ta wysokość)."""
    fs=sorted(fold(f) for s,f in win if s>TONAL and f>=60)
    if not fs: return 0,0.0
    m=fs[len(fs)//2]
    return sum(abs(x-m)/m<0.04 for x in fs), m
def track(rows, need=6, of=8):
    """Jak aplikacja: okna 1 s, wyzwolenie gdy ton obecny w ≥need z ostatnich `of` s.
    Zwraca (najlepsza obecność, jej f0, sekunda pierwszego wyzwolenia albo None)."""
    best=(0,0.0); trig=None
    for i in range(len(rows)):
        c,f=presence([(r[1],r[2]) for r in rows[max(0,i-of+1):i+1]])
        if c>best[0]: best=(c,f)
        if trig is None and c>=need and 80<=f<=135: trig=rows[i][0]
    return best[0],best[1],trig

C=343.0
class Track:
    """Śledzenie złapanego tonu (także poniżej 80 Hz) i rozpoznanie przelotu po efekcie Dopplera.
    Wzorzec dla Harmonic.Track w aplikacji."""
    CONT=1.1; STEP=0.04; GAP=6
    BACK=30
    def __init__(self, t, f, hist=()):
        """Start w chwili t przy wysokości f; `hist` = wcześniejsze okna (t, siła, f0) — tor
        doklejamy wstecz do BACK s, żeby nie zgubić nadlotu sprzed wyzwolenia."""
        self.pts=[(t,f)]; self.last=t
        for ht,hs,hf in sorted((h for h in hist if t-self.BACK<=h[0]<t), reverse=True):
            if hs<=self.CONT or hf<40: continue
            ff=fold(hf); ft,f1=self.pts[0]
            if abs(ff-f1)/f1<=min(0.12,self.STEP*max(1,ft-ht)): self.pts.insert(0,(ht,ff))
            elif ft-ht>=self.GAP: break
    def add(self, t, s, f):
        """Dołącza okno, jeśli pasuje wysokością do toru. Zwraca False, gdy tor się skończył."""
        if s>self.CONT and f>=40:
            ff=fold(f); lt,lf=self.pts[-1]
            if abs(ff-lf)/lf<=min(0.12,self.STEP*max(1,t-lt)):
                self.pts.append((t,ff)); self.last=t
        return t-self.last<self.GAP
    def flyby(self):
        """Najdłuższy gładki spadek tonu → dict z prędkością, f silnika i chwilą przelotu, albo None."""
        if len(self.pts)<8: return None
        ts=[p[0] for p in self.pts]; fs=[p[1] for p in self.pts]
        m=[float(np.median(fs[max(0,i-2):i+3])) for i in range(len(fs))]
        best=None; i=0
        while i<len(m)-1:
            j=i
            while j+1<len(m) and m[j+1]<=m[j]*1.01: j+=1
            if j>i and (best is None or m[i]/m[j]>m[best[0]]/m[best[1]]): best=(i,j)
            i=j+1
        if not best: return None
        i,j=best
        if m[i]/m[j]<1.05 or ts[j]-ts[i]<3: return None
        # ton nadlotu i odlotu = mediany całych odcinków przed i po spadku (odporne na wahania)
        hi=float(np.median(fs[:i+1])); lo=float(np.median(fs[j:])); r=hi/lo
        if r<1.05: return None
        f0=2*hi*lo/(hi+lo)
        cpa=next(ts[k] for k in range(i,j+1) if m[k]<=f0)
        return dict(f_hi=round(hi,1),f_lo=round(lo,1),f_engine=round(f0,1),
                    speed_kmh=round(C*(r-1)/(r+1)*3.6),t_cpa=cpa,sweep_s=ts[j]-ts[i],track_s=ts[-1]-ts[0])
if __name__=='__main__':
    for p in sys.argv[1:]:
        x=load(p); rows=[(t,)+comb(s) for t,s in frames(x)]
        a=np.array(rows); strong=a[:,1]>1.2
        # stabilność: jak bardzo złożone f0 skacze między kolejnymi silnymi oknami (% na sekundę)
        f=np.array([fold(v) for v in a[strong,2]]); jump=np.median(np.abs(np.diff(f))/f[:-1]*100/0.5) if len(f)>2 else float('nan')
        pc,pf,trig=track([(t,s,f0) for t,s,f0 in rows if abs(t-round(t))<1e-6])   # okna co 1 s jak w aplikacji
        tr=None; fb=None
        for t,st,f0 in rows:
            if abs(t-round(t))>1e-6: continue
            if tr is None:
                c,pf=presence([(r[1],r[2]) for r in rows if t-8<r[0]<=t and abs(r[0]-round(r[0]))<1e-6])
                if c>=6 and 80<=pf<=135: tr=Track(t,pf,[r for r in rows if r[0]<t and abs(r[0]-round(r[0]))<1e-6])
            elif not tr.add(t,st,f0): break
        fb=tr.flyby() if tr else None
        print(f"{p.split('/')[-2]}/{p.split('/')[-1][:15]} okien {len(a)} tonalnych {strong.mean()*100:4.0f}%  f0 med {np.median(f) if len(f) else 0:5.1f} Hz  zmiana f0 {jump:5.1f}%/s  siła med {np.median(a[:,1]):.2f}  obecność {pc}/8 @ {pf:5.1f} Hz  wyzwolenie {'—' if trig is None else f'{trig:.0f} s'}  przelot {fb if fb else '—'}")
