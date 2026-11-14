"""Mission 5 : où passe le temps de B2b côté Redis ? (« combien de membres du serveur 118 sont en ligne »)

On découpe la lecture en étapes, et on compare avec le même calcul fait DANS Redis (petit script Lua),
qui ne renvoie qu'un nombre, comme PostgreSQL.

Usage : python detail_b2b.py [--n 300]   (lancer après load_nosql.py)
"""
import argparse
import statistics
import time

from common import redis_conn

SERVEUR = 118
LUA = """local n = 0
for _, u in ipairs(redis.call('SMEMBERS', KEYS[1])) do
  if redis.call('EXISTS', 'presence:' .. u) == 1 then n = n + 1 end
end
return n"""


def chrono(f, n, echauffement=20):
    for _ in range(echauffement):
        f()
    t = []
    for _ in range(n):
        t0 = time.perf_counter()
        f()
        t.append((time.perf_counter() - t0) * 1000)
    t.sort()
    return statistics.median(t), t[int(n * 0.95) - 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    n = ap.parse_args().n
    r = redis_conn()
    membres = r.smembers(f"membres:{SERVEUR}")
    cles = [f"presence:{m}" for m in membres]
    script = r.register_script(LUA)
    print(f"# membres du serveur {SERVEUR} : {len(membres)}")
    print("| Étape | p50 (ms) | p95 (ms) |\n|---|---:|---:|")
    for nom, f in (("SMEMBERS seul", lambda: r.smembers(f"membres:{SERVEUR}")),
                   (f"MGET seul ({len(cles)} clés)", lambda: r.mget(cles)),
                   ("script Lua côté serveur", lambda: script(keys=[f"membres:{SERVEUR}"]))):
        p50, p95 = chrono(f, n)
        print(f"| {nom} | {p50:.3f} | {p95:.3f} |")


if __name__ == "__main__":
    main()
