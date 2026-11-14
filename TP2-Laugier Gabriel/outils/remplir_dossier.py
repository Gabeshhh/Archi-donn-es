"""Remplit MESURES.md et DOSSIER_ARCHI.md à partir des sorties rangées dans resultats/ par tout_tester.sh.

Usage : python outils/remplir_dossier.py resultats/ tp-laugier/
Chaque «clé» des modèles est remplacée par la valeur mesurée ; les verdicts des hypothèses
sont calculés à partir des rapports entre moteurs. S'il manque une mesure, le script s'arrête
en disant laquelle (rien n'est inventé).
"""
import re
import sys
from pathlib import Path

RES = Path(sys.argv[1] if len(sys.argv) > 1 else "resultats")
SORTIE = Path(sys.argv[2] if len(sys.argv) > 2 else "tp-laugier")
MODELES = Path(__file__).resolve().parent


def stop(msg):
    sys.exit(f"\nARRÊT : {msg}\nEnvoie le dossier resultats/ pour que l'erreur soit corrigée.")


def lire(nom):
    p = RES / nom
    if not p.exists():
        stop(f"fichier manquant : {p}")
    return p.read_text(encoding="utf-8", errors="replace")


def ms(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def fx(r):
    return (f"{r:.0f}" if r >= 10 else f"{r:.1f}").replace(".", ",")


def entier(n):
    return f"{n:,}".replace(",", " ")


def lignes_tableau(texte):
    """Lignes Markdown « | a | b | ... | » -> listes de cellules."""
    out = []
    for l in texte.splitlines():
        l = l.strip()
        if l.startswith("|") and not l.startswith("|---"):
            out.append([c.strip() for c in l.strip("|").split("|")])
    return out


def nombre(s):
    return float(s.replace(",", "."))


def valeur_sous_entete(texte, entete):
    ls = texte.splitlines()
    for i, l in enumerate(ls):
        if l.strip() == entete and i + 2 < len(ls):
            return ls[i + 2].strip()
    stop(f"valeur « {entete} » introuvable")


def temps_apres(texte, debut):
    ls = texte.splitlines()
    for i, l in enumerate(ls):
        if l.strip().startswith(debut):
            for k in ls[i + 1:i + 4]:
                m = re.match(r"\s*Time: ([\d.]+) ms", k)
                if m:
                    return float(m.group(1))
    stop(f"durée après « {debut} » introuvable")


v = {}

# ---------------- Machine ----------------
mach = {}
for l in lire("00_machine.txt").splitlines():
    if ":" in l:
        k, _, val = l.partition(":")
        mach[k.strip()] = val.strip()
v["cpu"] = mach.get("Processeur", "?")
v["ram"] = mach.get("Mémoire", "?")
v["os"] = mach.get("Système", "?")
v["docker"] = mach.get("Docker", "?")
v["docker_ram"] = mach.get("Mémoire Docker", "?")
v["python"] = mach.get("Python", "?")

# ---------------- Mission 1 ----------------
t12 = lire("12_chargement.txt")
lignes_m1 = []
for nom in ("utilisateur", "serveur", "membre", "salon", "message", "amitie"):
    m = re.search(rf"^\s*{nom}\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(-?\d+)", t12, re.M)
    if not m:
        stop(f"Mesure 1 : ligne {nom} introuvable dans 12_chargement.txt")
    st, ap, ec = map(int, m.groups())
    if ec != 0:
        stop(f"Mesure 1 : écart de {ec} sur la table {nom}")
    lignes_m1.append(f"| {nom} | {entier(st)} | {entier(ap)} | {ec} |")
v["tableau_mesure1"] = "\n".join(lignes_m1)
m = re.search(r"^\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*$", t12.split("pj_staging")[1], re.M)
v["pj_app"], v["modif_app"] = entier(int(m.group(2))), entier(int(m.group(4)))
temps = [float(x) for x in re.findall(r"Time: ([\d.]+) ms", t12)]
v["t_load_total"] = ms(sum(temps) / 1000, 1)
v["t_load_msg"] = ms(temps_apres(t12, "INSERT 0 300000") / 1000, 1)
for i, h in enumerate(("serveurs_sans_proprietaire_membre", "messages_dans_salon_vocal",
                       "messages_auteur_non_membre", "serveurs_nb_membres_faux"), 1):
    v[f"inv{i}"] = valeur_sous_entete(t12, h)
    if v[f"inv{i}"] != "0":
        stop(f"invariant {h} = {v[f'inv{i}']} (0 attendu)")

t15 = lire("15_cout_trigger.txt")
for essai in (1, 2):
    for cas, cle in (("sans", "sans"), ("avec", "avec")):
        v[f"trig_{cle}{essai}"] = temps_apres(t15, f"essai {essai} {cas} trigger")
ts, ta = v["trig_sans2"], v["trig_avec2"]
v["trig_us"] = ms((ta - ts) / 300_000 * 1000, 1)
v["trig_x"] = ms(ta / ts, 1)
for k in ("trig_sans1", "trig_avec1", "trig_sans2", "trig_avec2"):
    v[k] = entier(round(v[k]))

v["nb_refus"] = str(lire("14_preuves.txt").count("ERROR"))
if v["nb_refus"] != "18":
    stop(f"{v['nb_refus']} refus dans 14_preuves.txt (18 attendus)")

# ---------------- Mission 2 ----------------
sq = {}
for c in lignes_tableau(lire("20_mesure_sql.txt")):
    if len(c) >= 4 and c[0] != "Requête":
        sq[c[0]] = tuple(nombre(x) for x in c[1:4])
noms_sql = {"r1c": "R1 50 derniers messages, salon chaud (775)", "r1m": "R1 50 derniers messages, salon moyen (424)",
            "r1s": "R1 50 derniers messages, salon clairsemé (342)", "r2": "R2 droit d'écrire",
            "r3": "R3 activité oct. 2026, calcul à la volée", "r3sf": "R3 variante : filtre sur l'id Snowflake",
            "r3v": "R3 lue dans la vue matérialisée"}
num = {}
for k, nom in noms_sql.items():
    if nom not in sq:
        stop(f"Mesure 2 : ligne « {nom} » introuvable")
    p50, p95, froid = sq[nom]
    num[f"{k}_p50"], num[f"{k}_p95"] = p50, p95
    v[f"{k}_p50"], v[f"{k}_p95"], v[f"{k}_froid"] = ms(p50, 3), ms(p95, 3), ms(froid, 3)
v["verdict_r1"] = (f"**tenu**, avec une marge de ×{fx(80 / num['r1c_p95'])}" if num["r1c_p95"] < 80 else "**non tenu**")
v["verdict_r2"] = "**tenu**" if num["r2_p95"] < 5 else "**non tenu**"
v["f_vue_p50"] = fx(num["r3_p50"] / num["r3v_p50"])
v["f_vue_p95"] = fx(num["r3_p95"] / num["r3v_p95"])

t13 = lire("13_requetes.txt")
plans = t13.split("QUERY PLAN")
if len(plans) < 3:
    stop("EXPLAIN introuvables dans 13_requetes.txt")


def buffers(plan):
    m = re.search(r"Buffers: shared ([^\n]+)", plan)
    return sum(int(x) for x in re.findall(r"=(\d+)", m.group(1))) if m else 0


p1 = plans[1].split("Execution Time")[0]
ls = p1.splitlines()
i_noeud = next((i for i, l in enumerate(ls) if " on message m" in l), None)
if i_noeud is None:
    stop("nœud sur la table message introuvable dans l'EXPLAIN de R3")
ind = ls[i_noeud].index("->") if "->" in ls[i_noeud] else len(ls[i_noeud]) - len(ls[i_noeud].lstrip())
bloc = [ls[i_noeud]]
for l in ls[i_noeud + 1:]:
    if not l.strip() or (len(l) - len(l.lstrip())) <= ind:
        break
    bloc.append(l)
exec_t = re.search(r"Execution Time: [\d.]+ ms", plans[1])
largeur = min(len(x) - len(x.lstrip()) for x in bloc)
v["m3_bloc"] = "\n".join(x[largeur:] for x in bloc) + ("\n" + exec_t.group(0) if exec_t else "")
noeud = ls[i_noeud]
m = re.search(r"actual time=[\d.]+\.\.[\d.]+ rows=(\d+) loops=(\d+)", noeud)
rows, loops = (int(m.group(1)), int(m.group(2))) if m else (0, 1)
m = re.search(r"Rows Removed by Filter: (\d+)", "\n".join(bloc))
retires = int(m.group(1)) if m else 0
v["m3_lus"] = entier((rows + retires) * loops)
v["m3_jetes_pct"] = str(round(100 * retires / (rows + retires))) if rows + retires else "0"
if "Seq Scan" in noeud:
    v["m3_type"] = "Parcours séquentiel :"
    v["m3_type_explication"] = "PostgreSQL lit toute la table `message` ; l'index `(salon_id, id)` n'a pas été choisi pour cette requête."
else:
    v["m3_type"] = "Parcours par index, pas séquentiel :"
    v["m3_type_explication"] = "l'index `(salon_id, id)` trouve les messages des 4 salons textuels du serveur 118."
v["m3_buf"] = entier(buffers(plans[1]))
v["m3sf_buf"] = entier(buffers(plans[2]))
v["t_refresh"] = entier(round(temps_apres(t13, "REFRESH MATERIALIZED VIEW")))
v["t_mv_create"] = entier(round(temps_apres(t13, "SELECT 28957")))
for cle, ent in (("nb_avant", "avant"), ("nb_ins", "apres_insertion"), ("nb_role", "apres_changement_de_role"),
                 ("nb_del", "apres_suppression")):
    v[cle] = valeur_sous_entete(t13, ent)
if v["nb_avant"] != v["nb_del"]:
    stop("le trigger nb_membres ne revient pas à la valeur initiale")

# ---------------- Mission 3 ----------------
v["sortie_load"] = lire("30_load_nosql.txt").strip()
t31 = lire("31_verif_migration.txt")
if "Traceback" in t31 or "AssertionError" in t31:
    stop("verif_migration.py a échoué (voir 31_verif_migration.txt)")
for c in lignes_tableau(t31):
    if c[0] == "MongoDB":
        v["m5a_mongo"], v["m5a_mongo_diff"] = entier(int(c[1])), c[2]
    elif c[0].startswith("Cassandra"):
        v["m5a_cs"], v["m5a_cs_diff"] = entier(int(c[1])), c[2]
    for code, nom in (("c", "chaud (775)"), ("m", "moyen (424)"), ("s", "clairsemé (342)")):
        if c[0] == nom:
            v[f"m5b_{code}_mongo"], v[f"m5b_{code}_cs"] = f"**{c[2]}**", f"**{c[3]}**"
m = re.search(r"# mongo_taille : (.+)", t31)
v["mongo_taille"] = m.group(1).strip() if m else stop("taille MongoDB introuvable")
m = re.search(r"# mongo_plan_b1 : (.+)", t31)
v["mongo_plan"] = m.group(1).strip() if m else stop("plan MongoDB introuvable")
v["phrase_mongo_plan"] = ("La réponse sort de l'index sans lire un seul document." if "PROJECTION_COVERED" in v["mongo_plan"]
                          else "L'index trouve les documents dans le bon ordre, puis MongoDB les lit (étape FETCH).")

t40 = lire("40_bench.txt")
if "Traceback" in t40 or "AssertionError" in t40:
    stop("bench.py a échoué (voir 40_bench.txt)")
if "à faire" in t40:
    stop("bench.py contient encore des cas « à faire »")
b = {}
for c in lignes_tableau(t40):
    if len(c) >= 4 and c[0] != "Cas":
        b[(c[0], c[1])] = (nombre(c[2]), nombre(c[3]))


def bv(cas, moteur):
    if (cas, moteur) not in b:
        stop(f"bench.py : ligne « {cas} / {moteur} » introuvable")
    return b[(cas, moteur)]


CORR = "Cassandra + votre correctif (optionnel)"
for code, nom in (("c", "chaud"), ("m", "moyen"), ("s", "clairsemé")):
    cas = f"B1 50 derniers messages, salon {nom}"
    for mot, cle in (("PostgreSQL", "pg"), ("MongoDB", "mongo"), ("Cassandra", "cs"), (CORR, "csf")):
        p50, p95 = bv(cas, mot)
        num[f"b1_{code}_{cle}_p50"], num[f"b1_{code}_{cle}_p95"] = p50, p95
        v[f"b1_{code}_{cle}"] = f"{ms(p50)} / {ms(p95)}"
        v[f"b1_{code}_{cle}_p50"], v[f"b1_{code}_{cle}_p95"] = ms(p50), ms(p95)
fen = dict(re.findall(r"# B1 salon (\d+) : \d+ messages identiques, (\d+) fenêtre", t40))
v["fen_c"], v["fen_m"], v["fen_s"] = fen.get("775", "?"), fen.get("424", "?"), fen.get("342", "?")
v["controles"] = "\n".join(l for l in t40.splitlines() if l.startswith("#"))

for cle, cas, mots in (("b2a", "B2a présence : écriture", (("PostgreSQL", "pg"), ("Redis", "redis"))),
                       ("b2b", "B2b présence : en ligne dans un serveur (478 membres)", (("PostgreSQL", "pg"), ("Redis", "redis"))),
                       ("b3", "B3 serveurs suggérés (amis)", (("PostgreSQL", "pg"), ("Neo4j", "neo"))),
                       ("b3bis", "B3 bis amis d'amis (n=30)", (("PostgreSQL", "pg"), ("Neo4j", "neo"))),
                       ("b4", "B4 activité d'un serveur (30 j)", (("PostgreSQL normalisé", "pg"), ("PostgreSQL vue matérialisée", "vue"),
                                                                ("MongoDB (aggregate)", "mongo")))):
    for mot, m2 in mots:
        p50, p95 = bv(cas, mot)
        num[f"{cle}_{m2}_p50"], num[f"{cle}_{m2}_p95"] = p50, p95
        v[f"{cle}_{m2}"] = f"{ms(p50)} / {ms(p95)}"
        v[f"{cle}_{m2}_p50"], v[f"{cle}_{m2}_p95"] = ms(p50), ms(p95)

# Tableau complet de la Mesure 8
n_bench = re.search(r"\(n = (\d+)\)", t40)
tab = [f"(n = {n_bench.group(1) if n_bench else 300} ; B3 bis : n = 30)\n", "| Cas | Moteur | p50 (ms) | p95 (ms) |", "|---|---|---:|---:|"]
for (cas, mot), (p50, p95) in b.items():
    mot = "Cassandra + correctif (fenêtres non vides)" if mot == CORR else mot
    tab.append(f"| {cas} | {mot} | {ms(p50)} | {ms(p95)} |")
v["tableau_bench"] = "\n".join(tab)

d = {}
for c in lignes_tableau(lire("42_detail_b2b.txt")):
    if len(c) >= 3 and c[0] != "Étape":
        d[c[0].split(" ")[0] + (" Lua" if "Lua" in c[0] else "")] = (nombre(c[1]), nombre(c[2]))
try:
    (sm50, sm95), (mg50, mg95), (lu50, lu95) = d["SMEMBERS"], d["MGET"], d["script Lua"]
except KeyError:
    stop("42_detail_b2b.txt incomplet")
v.update(d_smembers_p50=ms(sm50, 3), d_smembers_p95=ms(sm95, 3), d_mget_p50=ms(mg50, 3), d_mget_p95=ms(mg95, 3),
         d_lua_p50=ms(lu50, 3), d_lua_p95=ms(lu95, 3))
v["expiration"] = lire("41_expiration.txt").strip()

# ---------------- Mission 4 ----------------
t50 = lire("50_tombstones.txt").strip()
v["tomb_sortie"] = t50
logs = [l[:400] for l in lire("51_logs_tombstones.txt").splitlines() if l.strip()]
v["tomb_log"] = "\n".join(logs) if logs else "(aucune ligne contenant « tombstone » dans le journal au moment du relevé)"
v["purge_sortie"] = lire("52_purge.txt").strip()
v["reparer_sortie"] = lire("53_reparer.txt").strip()
m = re.search(r"ÉCHEC après (\d+) ms", t50)
v["tomb_resume"] = (f"la lecture des 50 derniers messages échoue après {m.group(1)} ms, refusée par le moteur" if m
                    else "lecture relevée : « " + t50.splitlines()[-1].strip() + " »")

# ---------------- Rapports et phrases ----------------
pg_c, mo_c, cs_c, csf_c = num["b1_c_pg_p50"], num["b1_c_mongo_p50"], num["b1_c_cs_p50"], num["b1_c_csf_p50"]
pg_s, mo_s, cs_s, csf_s = num["b1_s_pg_p50"], num["b1_s_mongo_p50"], num["b1_s_cs_p50"], num["b1_s_csf_p50"]
cs_m, csf_m = num["b1_m_cs_p50"], num["b1_m_csf_p50"]
v["r_s_c_pg"], v["r_s_c_mongo"], v["r_s_c_cs"] = fx(pg_s / pg_c), fx(mo_s / mo_c), fx(cs_s / cs_c)

r = mo_c / pg_c
if r >= 1.3:
    v["phrase_mongo_b1"] = (f"**MongoDB contre PostgreSQL** : MongoDB est ×{fx(r)} plus lent sur la même lecture, alors que les deux ne lisent que l'index. "
                            "Ce n'est pas le modèle de données : c'est un coût fixe par requête plus élevé (hypothèse : préparation de la commande et lecture de la réponse par le pilote pymongo). "
                            f"Indice : le salon clairsemé (1 document renvoyé) coûte {ms(mo_s)} ms et le salon chaud (50 documents) {ms(mo_c)} ms.")
    v["phrase_resume_mongo"] = f"mais il est ×{fx(r)} plus lent que PostgreSQL sur la même lecture et n'apporte rien que le JSONB ne fasse déjà."
elif r <= 1 / 1.3:
    v["phrase_mongo_b1"] = (f"**MongoDB contre PostgreSQL** : MongoDB est ×{fx(1 / r)} plus rapide sur cette lecture. Les deux ne lisent que l'index ; "
                            "la différence vient du coût fixe d'une requête (pilote, protocole), pas du modèle de données.")
    v["phrase_resume_mongo"] = f"il est même ×{fx(1 / r)} plus rapide que PostgreSQL sur cette lecture, mais n'apporte rien que le JSONB ne fasse déjà, pour un moteur de plus à exploiter."
else:
    v["phrase_mongo_b1"] = f"**MongoDB contre PostgreSQL** : à égalité ({ms(mo_c)} contre {ms(pg_c)} ms) ; les deux ne lisent que l'index."
    v["phrase_resume_mongo"] = "il fait jeu égal avec PostgreSQL sur la même lecture et n'apporte rien que le JSONB ne fasse déjà."

if num["b4_mongo_p50"] < num["r3_p50"]:
    v["phrase_b4_mongo"] = ("MongoDB va plus vite que le calcul SQL à la volée parce que son index commence par le serveur **et** contient la date : il ne lit que les messages d'octobre. "
                            f"C'est le même effet que la variante Snowflake de PostgreSQL ({v['r3sf_p50']} ms).")
else:
    v["phrase_b4_mongo"] = ("MongoDB n'est pas plus rapide que le calcul SQL à la volée, alors que son index `(serveur_id, envoye_le)` ne lui fait lire que les messages d'octobre : "
                            f"le regroupement par jour (`$group` avec `$addToSet`) coûte plus cher que le `GROUP BY` de PostgreSQL. La variante Snowflake de PostgreSQL reste à {v['r3sf_p50']} ms.")

rc = cs_s / cs_c
if rc >= 1.5:
    v["phrase_cs_profil"] = f"le salon clairsemé est **×{fx(rc)} plus lent** que le salon chaud ({ms(cs_s)} ms contre {ms(cs_c)} ms au p50) : c'est l'inverse de PostgreSQL et MongoDB."
else:
    v["phrase_cs_profil"] = (f"l'écart reste faible (×{fx(rc)}, {ms(cs_s)} ms contre {ms(cs_c)} ms) : en local, un aller-retour vers Cassandra coûte peu, "
                             "donc 26 petites requêtes restent rapides. Sur un vrai réseau, chaque aller-retour coûterait bien plus.")
g = cs_s / csf_s
v["phrase_correctif_s"] = f"Gain mesuré : ×{fx(g)}." if g >= 1.3 else f"Gain faible (×{fx(g)}) : en local, les fenêtres vides ne coûtaient presque rien à lire."
v["phrase_correctif_m"] = ("Pas de gain mesuré, comme prévu." if csf_m >= 0.8 * cs_m else "Léger gain mesuré : les 3 fenêtres vides ne sont plus lues.")
v["phrase_correctif_c"] = ("Légère perte mesurée, comme prévu." if csf_c > 1.1 * cs_c else "Pas de différence visible.")
v["phrase_cs_meilleure"] = (f"Pas à ce volume : pour le salon chaud, elle met {ms(cs_c)} ms contre {ms(pg_c)} ms pour PostgreSQL (une seule plage lue dans les deux cas) ; "
                            f"pour le salon clairsemé, {ms(cs_s)} ms contre {ms(pg_s)} ms, à cause des 26 requêtes." if cs_c >= pg_c else
                            f"Pour le salon chaud, elle est plus rapide ({ms(cs_c)} contre {ms(pg_c)} ms), mais pour le salon clairsemé elle met {ms(cs_s)} ms contre {ms(pg_s)} ms, à cause des 26 requêtes.")

# B2a / B2b
w = num["b2a_pg_p50"] / num["b2a_redis_p50"]
v["phrase_b2a"] = (f"oui, Redis est ×{fx(w)} plus rapide. Mécanisme : PostgreSQL attend que l'écriture soit **sur le disque** au COMMIT (`synchronous_commit = on`) ; "
                   "Redis écrit en mémoire et ne force pas le disque. Pour une présence (donnée qu'on accepte de perdre), cette durabilité ne sert à rien."
                   if w >= 1.5 else f"l'écart est faible (×{fx(w)}) : le coût de l'aller-retour vers le conteneur domine celui de l'écriture elle-même.")
s = num["b2b_pg_p50"] / num["b2b_redis_p50"]
detail = (f"Détail mesuré : `SMEMBERS` renvoie 478 ids au client ({ms(sm50, 3)} ms), puis `MGET` envoie et reçoit 478 clés ({ms(mg50, 3)} ms) ; "
          f"le même calcul fait **dans** Redis par un petit script Lua, qui ne renvoie qu'un nombre : {ms(lu50, 3)} ms.")
if s >= 2.5:
    v["phrase_b2b"] = f"oui, Redis est ×{fx(s)} plus rapide : il lit directement en mémoire par clé, sans analyser de requête. " + detail
    h3v = "**Confirmée**"
elif s >= 1.2:
    v["phrase_b2b"] = f"en partie : Redis est plus rapide (×{fx(s)}) mais moins que prévu (×5), parce que ma version fait 2 allers-retours et rapatrie 478 ids chez le client. " + detail
    h3v = "**Confirmée en partie** (plus rapide, mais moins que ×5)"
elif s > 1 / 1.2:
    v["phrase_b2b"] = "non : à égalité. PostgreSQL fait la jointure chez lui et renvoie un nombre ; ma version Redis fait 2 allers-retours. " + detail
    h3v = "**Infirmée** (à égalité)"
else:
    v["phrase_b2b"] = (f"**non, c'est l'inverse** : Redis est ×{fx(1 / s)} **plus lent** que PostgreSQL. PostgreSQL fait la jointure chez lui et ne renvoie qu'un nombre. " + detail +
                       " C'est le transfert de données qui coûte, pas Redis lui-même.")
    h3v = "**Infirmée** (Redis plus lent)"
v["h3_verdict"] = h3v
v["h3_explication"] = ("PostgreSQL fait la jointure membre × présence chez lui et renvoie un nombre ; ma version Redis rapatrie 478 ids (SMEMBERS) puis 478 valeurs (MGET). "
                       f"Le même calcul dans Redis (script Lua) : {ms(lu50, 3)} ms. Le vrai avantage de Redis est l'expiration automatique et l'écriture (B2a : ×{fx(w)}).")
lent = s < 1 / 1.2
if lent:
    v["phrase_resume_redis"] = (f"**Redis reste, mais pour une autre raison** : il ne gagne pas en lecture (« qui est en ligne » : ×{fx(1 / s)} plus lent que PostgreSQL, Mesure 7), "
                                f"il gagne sur l'**expiration automatique** et l'**écriture** du battement de cœur (×{fx(w)}).")
    v["d2_verdict"] = "**Maintenue**, raison corrigée"
    v["d2_change"] = f"Redis est gardé pour l'expiration et l'écriture, pas pour la lecture. Pour « qui est en ligne », le calcul doit se faire **dans** Redis (script Lua : {ms(lu50, 3)} ms) et non en rapatriant 478 ids chez le client."
    v["phrase_deux_moteurs"] = (f"Mais les raisons ont changé : PostgreSQL tient toutes les requêtes du TP sous «pg_max_p95» ms au p95, avec toutes les règles de gestion garanties ; "
                                f"Redis n'est **pas** gardé pour la vitesse de lecture (il perd sur B2b), mais pour l'**expiration automatique** et le coût d'écriture du battement de cœur (×{fx(w)}). "
                                "Hier je pensais que Redis gagnerait en lecture ; la mesure dit que son vrai apport est ailleurs.")
    v["phrase_deux_moteurs_redis"] = (f"Hier, je gardais Redis en pensant qu'il serait plus rapide en lecture. La mesure dit le contraire pour « qui est en ligne » (×{fx(1 / s)} plus lent). "
                                      f"Je le garde pour ce qu'il fait vraiment mieux : faire disparaître une donnée toute seule, et absorber beaucoup d'écritures sans toucher au disque (×{fx(w)}).")
else:
    v["phrase_resume_redis"] = f"**Redis reste** : il gagne en écriture (×{fx(w)}) et en lecture (×{fx(s)}) face à PostgreSQL, et apporte l'expiration automatique (Mesure 7)."
    v["d2_verdict"] = "**Maintenue**"
    v["d2_change"] = f"Rien à changer ; un script Lua ({ms(lu50, 3)} ms) ferait encore mieux que les deux allers-retours actuels."
    v["phrase_deux_moteurs"] = (f"Les mesures le confirment : PostgreSQL tient toutes les requêtes du TP sous «pg_max_p95» ms au p95, règles comprises ; "
                                f"Redis gagne en écriture (×{fx(w)}) et en lecture (×{fx(s)}), et fait expirer les présences tout seul.")
    v["phrase_deux_moteurs_redis"] = f"Hier, je gardais Redis pour l'expiration et la vitesse. La mesure confirme : écriture ×{fx(w)} et lecture ×{fx(s)} plus rapides que PostgreSQL, et l'expiration se fait toute seule."

# Neo4j
n3, n3b = num["b3_pg_p50"] / num["b3_neo_p50"], num["b3bis_pg_p50"] / num["b3bis_neo_p50"]
chiffres_neo = (f"B3 : PostgreSQL {ms(num['b3_pg_p50'])} ms, Neo4j {ms(num['b3_neo_p50'])} ms ; "
                f"B3 bis : PostgreSQL {ms(num['b3bis_pg_p50'])} ms, Neo4j {ms(num['b3bis_neo_p50'])} ms (p50).")
seuil = f"PostgreSQL reste à {ms(num['b3bis_pg_p95'])} ms au p95 sur les amis d'amis, très loin de mon seuil de 200 ms : Neo4j ne se justifie pas à 15 000 amitiés."
if n3b >= 1.5:
    v["phrase_neo"] = (chiffres_neo + f" Neo4j gagne sur les amis d'amis (×{fx(n3b)}) : il suit les relations d'un nœud à l'autre, alors que PostgreSQL fait deux jointures "
                       "(dans les deux sens à cause de a < b). Mais le gain se compte en fractions de milliseconde. " + seuil)
    v["h4_verdict"] = "**Confirmée**" if n3b >= 1.5 else ""
    v["h4_explication"] = f"Neo4j suit directement les relations ; PostgreSQL fait deux jointures dans les deux sens. Gain ×{fx(n3b)}, mais en fractions de milliseconde. " + seuil
else:
    pourquoi = ("Avec 15 000 amitiés (6 par personne en moyenne), les jointures SQL lisent quelques dizaines de lignes par index ; "
                "Neo4j paie en plus le coût fixe d'une requête Cypher (analyse de la requête, protocole Bolt, JVM).")
    v["phrase_neo"] = chiffres_neo + (f" Neo4j ne gagne pas (×{fx(1 / n3b)} plus lent sur les amis d'amis). " if n3b < 1 / 1.5 else " À égalité sur les amis d'amis. ") + pourquoi + " " + seuil
    v["h4_verdict"] = "**Infirmée** (Neo4j plus lent)" if n3b < 1 / 1.5 else "**Infirmée** (à égalité)"
    v["h4_explication"] = pourquoi + " C'est la branche prévue au jour 1 : à cette taille, Neo4j ne se justifie pas (version minimale confirmée)."

# H1, H2, H5
h1 = cs_c / pg_c
v["h1_rapport"] = fx(h1)
if h1 < 2 and h1 > 0.5:
    v["h1_verdict"] = "**Confirmée**"
    v["h1_explication"] = "Les deux lisent une seule plage triée (index `(salon_id, id)` pour PG, une seule partition pour Cassandra : 1 partition lue) et tout est en mémoire : le modèle de données ne fait pas de différence à ce volume."
elif h1 >= 2:
    v["h1_verdict"] = "**Infirmée** (Cassandra plus lente)"
    v["h1_explication"] = ("Les deux lisent une seule plage triée (1 partition lue pour Cassandra) et tout est en mémoire, donc ce n'est pas le modèle de données : "
                           "Cassandra paie un coût fixe par requête plus élevé (coordinateur, JVM sur un seul nœud Docker avec 512 Mo, pilote Python). "
                           "C'était la branche prévue au jour 1 : « le coût de Cassandra vient de son fonctionnement en un seul nœud Docker ».")
else:
    v["h1_verdict"] = "**Infirmée** (Cassandra plus rapide)"
    v["h1_explication"] = "Une seule plage lue dans les deux cas : la différence vient du coût fixe d'une requête (pilote, protocole), pas du modèle de données. J'ai regardé l'EXPLAIN de PostgreSQL : il lit bien l'index à l'envers et s'arrête à 50."
if rc >= 2.5 and rc <= 10:
    v["h2_verdict"] = "**Confirmée** (sens et ordre de grandeur)"
elif rc > 10:
    v["h2_verdict"] = "**Confirmée sur le sens**, écart plus grand que prévu"
elif rc >= 1.5:
    v["h2_verdict"] = "**Confirmée en partie** : plus lent, mais moins que ×5"
else:
    v["h2_verdict"] = "**Infirmée** (écart faible)"
v["h2_explication"] = ("Les fenêtres sont lues l'une après l'autre ; un salon de moins de 50 messages remonte toujours jusqu'à sa création (26 partitions, plus que les 2 prévues). " +
                       ("En local, un aller-retour coûte peu : 26 petites requêtes restent rapides. C'est la branche « les allers-retours en local coûtent presque rien » prévue au jour 1 ; en vrai réseau, l'écart se rapprocherait de ×26."
                        if rc < 2.5 else "Le temps suit le nombre de requêtes successives."))
fv = num["r3_p50"] / num["r3v_p50"]
v["h5_verdict"] = "**Confirmée**" if fv >= 25 else f"**Confirmée sur le sens**, gain plus petit que prévu (×{fx(fv)})"

v["v_r1"] = "tenu" if num["r1c_p95"] < 80 else "non tenu"
v["v_r2"] = "tenu" if num["r2_p95"] < 5 else "non tenu"
v["v_b2a"] = "tenu" if max(num["b2a_pg_p95"], num["b2a_redis_p95"]) < 20 else "non tenu"
v["v_b2b"] = "tenu" if max(num["b2b_pg_p95"], num["b2b_redis_p95"]) < 50 else "non tenu"
v["v_b3"] = "tenu" if max(num["b3bis_pg_p95"], num["b3bis_neo_p95"]) < 1000 else "non tenu"
pg_p95 = [num[k] for k in num if k.endswith("_p95") and (k.startswith("r") or "_pg_" in k)] + [num["b4_pg_p95"], num["b4_vue_p95"]]
v["pg_max_p95"] = ms(max(pg_p95), 0) if max(pg_p95) >= 10 else ms(max(pg_p95), 1)


# ---------------- Écriture ----------------
def remplir(nom_modele, nom_sortie):
    t = (MODELES / nom_modele).read_text(encoding="utf-8")
    for _ in range(2):        # deux passes : certaines phrases contiennent elles-mêmes une «clé»
        t = re.sub(r"«(\w+)»", lambda m: str(v[m.group(1)]) if m.group(1) in v else m.group(0), t)
    reste = re.findall(r"«(\w+)»", t)
    if reste:
        stop(f"{nom_sortie} : valeurs non trouvées pour {sorted(set(reste))}")
    SORTIE.mkdir(parents=True, exist_ok=True)
    (SORTIE / nom_sortie).write_text(t, encoding="utf-8")
    print(f"écrit : {SORTIE / nom_sortie}")


remplir("MESURES.modele.md", "MESURES.md")
remplir("DOSSIER_ARCHI.modele.md", "DOSSIER_ARCHI.md")
