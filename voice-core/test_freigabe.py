"""Tests für die Regeln des Freigabe-Dienstes (infra/sokrates2/anker/freigabe.py, Konzept E.2, Schritt 1.4).

Laufen ohne Root, ohne Netz und ohne Datenbank: eigene ES256-Wegwerfschlüssel, selbst gestellte Uhr, Attrappe der Datenbank mit den
Regeln des Triggers aus Migration 0013. Der Rauchtest gegen die echte Datenbank folgt mit dem Prozess des Dienstes.

Ausführen:  uv run python -m pytest -q test_freigabe.py   (in voice-core/)
"""

import base64
import hashlib
import hmac
import json
import sys
import threading
import time
import unittest
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ANKER = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "anker"
MIGRATION = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "0013_release_freigaben.sql"
sys.path.insert(0, str(ANKER))

import freigabe as F  # noqa: E402

ISS = "https://192.168.178.136:55443/auth/v1"
AUD = "authenticated"
ADMIN = "123e4567-e89b-12d3-a456-426614174000"
ADMIN2 = "223e4567-e89b-12d3-a456-426614174000"
MITGLIED = "323e4567-e89b-12d3-a456-426614174000"
ADMIN3 = "623e4567-e89b-12d3-a456-426614174000"
ANFRAGE = "423e4567-e89b-12d3-a456-426614174000"
COMMIT = "a" * 40
TREE = "b" * 40
JETZT = 1_800_000_000.0

SCHLUESSEL = ec.generate_private_key(ec.SECP256R1())
FREMD = ec.generate_private_key(ec.SECP256R1())


def b64(daten: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(daten, separators=(",", ":")).encode()).rstrip(b"=").decode()


def schluessel(kid):
    if kid == "k1":
        return SCHLUESSEL.public_key()
    raise KeyError(kid)


def token(sub=ADMIN, alter=10, *, kid="k1", key=None, iss=ISS, aud=AUD, aal="aal2", amr=None, exp=3600):
    """alter: Sekunden seit dem Code (bezogen auf JETZT). exp und iat zählen in echter Zeit, damit PyJWT sie prüfen kann."""
    echt = int(time.time())
    claims = {"sub": sub, "iss": iss, "aud": aud, "aal": aal, "iat": echt, "exp": echt + exp,
              "amr": amr if amr is not None else [{"method": "password", "timestamp": int(JETZT) - 900},
                                                  {"method": "totp", "timestamp": JETZT - alter}]}
    return jwt.encode(claims, key or SCHLUESSEL, algorithm="ES256", headers={"kid": kid} if kid else None)


class Basis(unittest.TestCase):
    def setUp(self):
        self.db = F.AttrappeFreigabeDatenbank(uhr=lambda: JETZT)
        self.db.admins = {ADMIN, ADMIN2}
        self.db.anfragen[ANFRAGE] = {"status": "wartet_freigabe", "art": "normal", "v_commit": COMMIT, "tree_id": TREE,
                                     "angefordert_von": ADMIN, "wartet_bis": None}
        self.dienst = F.Freigabedienst(self.db, schluessel=schluessel, aussteller=ISS, zielgruppe=AUD, uhr=lambda: JETZT)

    def frei(self, tok=None, **kw):
        args = {"token": tok if tok is not None else token(), "anfrage": ANFRAGE, "commit_id": COMMIT, "tree_id": TREE}
        args.update(kw)
        return self.dienst.freigeben(**args)

    def lehnt_ab(self, code, http=None, **kw):
        with self.assertRaises(F.FreigabeFehler) as m:
            self.frei(**kw)
        self.assertEqual(m.exception.code, code)
        if http is not None:
            self.assertEqual(m.exception.http, http)
        return m.exception


class TokenTests(Basis):
    def test_gueltiges_token_gibt_frei(self):
        r = self.frei()
        self.assertTrue(r["ok"])
        self.assertEqual(r["gilt_s"], 3600)
        f = self.db.freigaben[0]
        self.assertEqual((f["anfrage"], f["freigeber"], f["commit_id"], f["tree_id"]), (ANFRAGE, ADMIN, COMMIT, TREE))
        self.assertEqual(f["aal2_zeit"], JETZT - 10)

    def test_falsche_signatur_wird_abgelehnt(self):
        self.lehnt_ab("token_ungueltig", 401, tok=token(key=FREMD))
        self.assertEqual(self.db.freigaben, [])

    def test_unbekannte_und_fehlende_kid(self):
        self.lehnt_ab("token_ungueltig", tok=token(kid="anderer"))
        self.lehnt_ab("token_ungueltig", tok=token(kid=None))

    def test_algorithmus_kommt_nie_aus_dem_token(self):
        # Schlüsselverwechslung: HS256 mit dem öffentlichen Schlüssel als Geheimnis
        pem = SCHLUESSEL.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        echt = int(time.time())
        claims = {"sub": ADMIN, "iss": ISS, "aud": AUD, "aal": "aal2", "iat": echt, "exp": echt + 600,
                  "amr": [{"method": "totp", "timestamp": int(JETZT)}]}
        # PyJWT verweigert beim Kodieren einen PEM-Schlüssel als HS256-Geheimnis: Das Token wird deshalb von Hand gebaut, wie es
        # ein Angreifer täte (Kopf alg=HS256, Signatur HMAC über den öffentlichen Schlüssel)
        kopf = b64({"alg": "HS256", "typ": "JWT", "kid": "k1"}) + "." + b64(claims)
        sig = base64.urlsafe_b64encode(hmac.new(pem, kopf.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        self.lehnt_ab("token_ungueltig", tok=kopf + "." + sig)
        ohne = jwt.encode({k: v for k, v in claims.items() if k != "amr"}, None, algorithm="none")
        self.lehnt_ab("token_ungueltig", tok=ohne)

    def test_aussteller_zielgruppe_und_ablauf(self):
        self.lehnt_ab("token_ungueltig", tok=token(iss="https://fremd.example/auth/v1"))
        self.lehnt_ab("token_ungueltig", tok=token(aud="anon"))
        self.lehnt_ab("token_ungueltig", tok=token(exp=-30))

    def test_pflichtfelder_und_form(self):
        self.lehnt_ab("token_ungueltig", tok=token(sub="kein-uuid"))
        for muell in ("", "a.b", "a.b.c", "x" * 20, None, 123, b"a.b.c", ["a"], "a." * 5000):
            with self.assertRaises(F.FreigabeFehler) as m:
                self.dienst.freigeben(token=muell, anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
            self.assertEqual(m.exception.code, "token_ungueltig", repr(muell)[:30])

    def test_fehlertexte_enthalten_nie_das_token(self):
        tok = token(key=FREMD)
        e = self.lehnt_ab("token_ungueltig", tok=tok)
        for teil in (tok, tok.split(".")[1], tok.split(".")[2]):
            self.assertNotIn(teil, str(e))
            self.assertNotIn(teil, repr(e.__dict__))


class Aal2Tests(Basis):
    def test_grenze_60_sekunden(self):
        self.assertTrue(self.frei(tok=token(alter=59))["ok"])
        self.lehnt_ab("aal2_alt", 403, tok=token(sub=ADMIN2, alter=61))

    def test_jungster_totp_eintrag_zaehlt(self):
        amr = [{"method": "totp", "timestamp": int(JETZT) - 4000}, {"method": "totp", "timestamp": int(JETZT) - 5}]
        self.assertTrue(self.frei(tok=token(amr=amr))["ok"])

    def test_nur_passwort_oder_aal1_reicht_nicht(self):
        self.lehnt_ab("aal2_fehlt", 403, tok=token(aal="aal1", amr=[{"method": "password", "timestamp": int(JETZT)}]))
        self.lehnt_ab("aal2_fehlt", tok=token(amr=[{"method": "password", "timestamp": int(JETZT)}]))
        self.lehnt_ab("aal2_fehlt", tok=token(amr=[]))

    def test_zeitstempel_aus_der_zukunft(self):
        self.assertTrue(self.frei(tok=token(alter=-4))["ok"])
        self.lehnt_ab("aal2_zukunft", tok=token(sub=ADMIN2, alter=-6))

    def test_muell_in_amr(self):
        for amr in ("totp", [{"method": "totp"}], [{"method": "totp", "timestamp": "jetzt"}], [{"method": "totp", "timestamp": True}],
                    [{"method": "totp", "timestamp": -5}], [None, 5, "x"], {"method": "totp", "timestamp": int(JETZT)}):
            self.lehnt_ab("aal2_fehlt", tok=token(amr=amr))


class FassungTests(Basis):
    def test_unbekannte_anfrage(self):
        self.lehnt_ab("anfrage_unbekannt", 404, anfrage="523e4567-e89b-12d3-a456-426614174000")

    def test_status_muss_wartet_freigabe_sein(self):
        for status in ("angefragt", "review", "pruefung3", "freigegeben", "veroeffentlicht", "abgebrochen"):
            self.db.anfragen[ANFRAGE]["status"] = status
            self.lehnt_ab("falscher_status", 409)
        self.assertEqual(self.db.freigaben, [])

    def test_commit_und_tree_muessen_zur_gesehenen_fassung_passen(self):
        self.lehnt_ab("fassung_passt_nicht", 409, commit_id="c" * 40)
        self.lehnt_ab("fassung_passt_nicht", tree_id="c" * 40)
        self.assertEqual(self.db.freigaben, [])

    def test_form_der_angaben(self):
        for kw in ({"anfrage": "x"}, {"anfrage": None}, {"commit_id": "xyz"}, {"commit_id": "A" * 40}, {"commit_id": "a" * 41},
                   {"tree_id": ""}, {"tree_id": 5}, {"quell_ip": "999.1.1.1"}, {"quell_ip": "keine ip"}):
            self.lehnt_ab("angaben", 400, **kw)

    def test_sha256_ids_werden_akzeptiert(self):
        self.db.anfragen[ANFRAGE].update(v_commit="d" * 64, tree_id="e" * 64)
        self.assertTrue(self.frei(commit_id="d" * 64, tree_id="e" * 64, quell_ip="192.168.178.20")["ok"])
        self.assertEqual(self.db.freigaben[0]["quell_ip"], "192.168.178.20")


class DatenbankTests(Basis):
    def test_nur_admins_geben_frei(self):
        self.lehnt_ab("keine_berechtigung", 403, tok=token(sub=MITGLIED))
        self.assertEqual(self.db.freigaben, [])

    def test_zweite_freigabe_derselben_person(self):
        self.frei()
        self.lehnt_ab("schon_freigegeben", 409)
        self.assertEqual(len(self.db.freigaben), 1)

    def test_zwei_admins_koennen_beide_freigeben(self):
        self.frei()
        self.frei(tok=token(sub=ADMIN2))
        self.assertEqual(len(self.db.freigaben), 2)

    def test_datenbank_ausfall_ist_503_und_schreibt_nichts(self):
        self.db.ausfall = True
        self.lehnt_ab("datenbank", 503)
        self.assertEqual(self.db.freigaben, [])

    def test_unbekannte_datenbankantwort_wird_zu_regel_ohne_servertext(self):
        class Komisch(F.AttrappeFreigabeDatenbank):
            def freigabe_schreiben(self, *a, **k):
                raise F.DatenbankAbgelehnt("XX000", "geheimer interner Satz mit Tabellenname release_freigaben")

        db = Komisch()
        db.admins = {ADMIN}
        db.anfragen[ANFRAGE] = dict(self.db.anfragen[ANFRAGE])
        d = F.Freigabedienst(db, schluessel=schluessel, aussteller=ISS, zielgruppe=AUD, uhr=lambda: JETZT)
        with self.assertRaises(F.FreigabeFehler) as m:
            d.freigeben(token=token(), anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
        self.assertEqual(m.exception.code, "regel")
        self.assertNotIn("geheimer", str(m.exception))
        self.assertNotIn("release_freigaben", str(m.exception))


class SonderTests(Basis):
    def setUp(self):
        super().setUp()
        self.db.anfragen[ANFRAGE].update(art="sonder", angefordert_von=ADMIN, wartet_bis=JETZT - 1)

    def test_beauftragter_darf_nicht_selbst_freigeben(self):
        self.lehnt_ab("keine_berechtigung", 403, tok=token(sub=ADMIN))
        self.assertEqual(self.db.freigaben, [])

    def test_anderer_admin_nach_der_wartezeit(self):
        r = self.frei(tok=token(sub=ADMIN2))
        self.assertEqual(r["gilt_s"], 86400)

    def test_wartezeit_noch_nicht_vorbei(self):
        self.db.anfragen[ANFRAGE]["wartet_bis"] = JETZT + 600
        self.lehnt_ab("wartezeit", 409, tok=token(sub=ADMIN2))
        self.db.anfragen[ANFRAGE]["wartet_bis"] = None
        self.lehnt_ab("wartezeit", 409, tok=token(sub=ADMIN2))

    def test_beauftragter_muss_ein_aktiver_admin_sein(self):
        self.db.anfragen[ANFRAGE]["angefordert_von"] = MITGLIED
        self.lehnt_ab("keine_berechtigung", tok=token(sub=ADMIN2))

    def test_zweiter_admin_satz_kommt_bei_zu_wenig_admins(self):
        # Beide Personen aktiv, aber die Zählung der Datenbank sieht unter zwei (Fehlerfall, Trigger sagt es ausdrücklich)
        class EinAdmin(F.AttrappeFreigabeDatenbank):
            def freigabe_schreiben(self, *a, **k):
                raise F.DatenbankAbgelehnt("42501", "release_freigaben: Braucht einen zweiten Admin (Sonderfreigabe gesperrt)")

        d = F.Freigabedienst(EinAdmin(), schluessel=schluessel, aussteller=ISS, zielgruppe=AUD, uhr=lambda: JETZT)
        d.db.admins = {ADMIN2}
        d.db.anfragen[ANFRAGE] = dict(self.db.anfragen[ANFRAGE])
        with self.assertRaises(F.FreigabeFehler) as m:
            d.freigeben(token=token(sub=ADMIN2), anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
        self.assertEqual((m.exception.code, m.exception.text), ("zweiter_admin", "Braucht einen zweiten Admin."))


class DrosselTests(Basis):
    def test_achte_versuch_geht_der_neunte_nicht(self):
        for _ in range(F.VERSUCHE_MAX):
            with self.assertRaises(F.FreigabeFehler):
                self.frei(tok=token(alter=500))                            # abgelehnte Versuche zählen mit
        self.lehnt_ab("zu_viele", 429)

    def test_fenster_laeuft_ab_und_profile_zaehlen_getrennt(self):
        t = [JETZT]
        self.db.uhr = lambda: t[0]                                         # auch die Datenbank geht mit der gestellten Uhr
        d = F.Freigabedienst(self.db, schluessel=schluessel, aussteller=ISS, zielgruppe=AUD, uhr=lambda: t[0], mono=lambda: t[0])
        for _ in range(F.VERSUCHE_MAX):
            with self.assertRaises(F.FreigabeFehler):
                d.freigeben(token=token(alter=500), anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
        with self.assertRaises(F.FreigabeFehler) as m:
            d.freigeben(token=token(), anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
        self.assertEqual(m.exception.code, "zu_viele")
        self.assertTrue(d.freigeben(token=token(sub=ADMIN2), anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)["ok"])
        t[0] = JETZT + F.VERSUCHE_FENSTER_S + 1
        spaeter = token(alter=-(F.VERSUCHE_FENSTER_S + 1) + 5)             # der Code ist 5 Sekunden alt, bezogen auf die neue Uhr
        self.assertTrue(d.freigeben(token=spaeter, anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)["ok"])

    def test_ungueltige_token_zaehlen_nicht_gegen_ein_profil(self):
        for _ in range(F.VERSUCHE_MAX + 3):
            with self.assertRaises(F.FreigabeFehler):
                self.frei(tok=token(key=FREMD))
        self.assertTrue(self.frei()["ok"])


class HaertungTests(Basis):
    """Lücken aus der Sicherheitsprüfung vom 1.11.2026."""

    def echtes_token(self, claims_aendern=None, kopf_aendern=None, key=None, alg="ES256"):
        echt = int(time.time())
        claims = {"sub": ADMIN, "iss": ISS, "aud": AUD, "aal": "aal2", "iat": echt, "exp": echt + 600,
                  "amr": [{"method": "totp", "timestamp": int(JETZT) - 5}]}
        claims = claims_aendern(claims) if claims_aendern else claims
        return jwt.encode(claims, key or SCHLUESSEL, algorithm=alg, headers=kopf_aendern or {"kid": "k1"})

    def test_jedes_pflichtfeld_fehlt_einzeln(self):
        for feld in ("exp", "iat", "aud", "iss", "sub"):
            tok = self.echtes_token(lambda c, f=feld: {k: v for k, v in c.items() if k != f})
            with self.assertRaises(F.FreigabeFehler, msg=feld) as m:
                self.dienst.freigeben(token=tok, anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
            self.assertEqual(m.exception.code, "token_ungueltig", feld)

    def test_aal_fehlt_im_token(self):
        tok = self.echtes_token(lambda c: {k: v for k, v in c.items() if k != "aal"})
        self.lehnt_ab("aal2_fehlt", tok=tok)

    def test_kopf_kid_kein_text_und_anderer_algorithmus(self):
        # PyJWT baut kein Token mit einer Zahl als kid: den Kopf von Hand austauschen (die Prüfung des Kopfes kommt vor der Signatur)
        teile = self.echtes_token().split(".")
        zahl = b64({"alg": "ES256", "typ": "JWT", "kid": 5}) + "." + teile[1] + "." + teile[2]
        self.lehnt_ab("token_ungueltig", tok=zahl)
        k384 = ec.generate_private_key(ec.SECP384R1())
        self.lehnt_ab("token_ungueltig", tok=self.echtes_token(kopf_aendern={"kid": "k1"}, key=k384, alg="ES384"))

    def test_tokenlaenge_grenze(self):
        # gültig signiert, aber zu lang: abgelehnt wegen der Länge, nicht wegen der Signatur
        lang = self.echtes_token(lambda c: {**c, "pad": "x" * (F.TOKEN_MAX_ZEICHEN + 100)})
        self.assertGreater(len(lang), F.TOKEN_MAX_ZEICHEN)
        self.lehnt_ab("token_ungueltig", tok=lang)
        kurz = self.echtes_token(lambda c: {**c, "pad": "x" * 100})
        self.assertLess(len(kurz), F.TOKEN_MAX_ZEICHEN)
        self.assertTrue(self.frei(tok=kurz)["ok"])

    def test_grenzfaelle_60_und_plus_5_sekunden(self):
        self.db.admins.add(ADMIN3)
        self.assertTrue(self.frei(tok=token(sub=ADMIN, alter=60))["ok"])           # genau 60 s: gilt (die Regel heißt "über 60")
        self.lehnt_ab("aal2_alt", tok=token(sub=ADMIN2, alter=60.001))
        self.assertTrue(self.frei(tok=token(sub=ADMIN3, alter=-5))["ok"])          # genau +5 s: gilt
        self.lehnt_ab("aal2_zukunft", tok=token(sub=ADMIN2, alter=-5.001))

    def test_gleitkomma_zeitstempel(self):
        amr = [{"method": "totp", "timestamp": JETZT - 12.5}]
        self.assertTrue(self.frei(tok=token(amr=amr))["ok"])

    def test_zukunftseintrag_neben_gueltigem_wird_abgelehnt(self):
        amr = [{"method": "totp", "timestamp": int(JETZT) - 5}, {"method": "totp", "timestamp": int(JETZT) + 600}]
        self.lehnt_ab("aal2_zukunft", tok=token(amr=amr))

    def test_quell_ip_nur_als_text_ohne_scope(self):
        for ip in (5, b"1.2.3.4", ["1.2.3.4"], "fe80::1%eth0", "1.2.3", "::g"):
            self.lehnt_ab("angaben", quell_ip=ip)
        self.assertTrue(self.frei(quell_ip="fd17:e98d:f6e2::1")["ok"])

    def test_unerwartete_ausnahmen_werden_neutral(self):
        class Platzt(F.AttrappeFreigabeDatenbank):
            def anfrage_lesen(self, anfrage):
                raise RuntimeError("SELECT * FROM release_freigaben WHERE geheim")

        db = Platzt()
        db.admins = {ADMIN}
        d = F.Freigabedienst(db, schluessel=schluessel, aussteller=ISS, zielgruppe=AUD, uhr=lambda: JETZT)
        with self.assertRaises(F.FreigabeFehler) as m:
            d.freigeben(token=token(), anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
        self.assertEqual(m.exception.code, "datenbank")
        self.assertNotIn("SELECT", str(m.exception))

        def kaputt(kid):
            raise OSError("Schlüsselsatz geheim.json nicht lesbar")

        d = F.Freigabedienst(self.db, schluessel=kaputt, aussteller=ISS, zielgruppe=AUD, uhr=lambda: JETZT)
        with self.assertRaises(F.FreigabeFehler) as m:
            d.freigeben(token=token(), anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
        self.assertEqual(m.exception.code, "token_ungueltig")
        self.assertNotIn("geheim", str(m.exception))

    def test_mitglied_erfaehrt_nichts_ueber_anfragen(self):
        # unbekannte Anfrage, falscher Status und falsche Fassung: für Nicht-Admins immer dieselbe Antwort
        fremd = "523e4567-e89b-12d3-a456-426614174000"
        self.db.anfragen[ANFRAGE]["status"] = "review"
        antworten = set()
        for kw in ({"anfrage": fremd}, {}, {"commit_id": "c" * 40}):
            with self.assertRaises(F.FreigabeFehler) as m:
                self.frei(tok=token(sub=MITGLIED), **kw)
            antworten.add((m.exception.code, m.exception.http))
        self.assertEqual(antworten, {("keine_berechtigung", 403)})

    def test_drosselung_gilt_unter_mehreren_threads_genau(self):
        ergebnisse = []

        def versuch():
            try:
                self.dienst.freigeben(token=token(alter=500), anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
            except F.FreigabeFehler as e:
                ergebnisse.append(e.code)

        threads = [threading.Thread(target=versuch) for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(ergebnisse.count("aal2_alt"), F.VERSUCHE_MAX)
        self.assertEqual(ergebnisse.count("zu_viele"), 40 - F.VERSUCHE_MAX)

    def test_zeitsprung_der_systemuhr_setzt_die_drosselung_nicht_zurueck(self):
        sprung = [JETZT]
        d = F.Freigabedienst(self.db, schluessel=schluessel, aussteller=ISS, zielgruppe=AUD, uhr=lambda: sprung[0], mono=lambda: 1000.0)
        for _ in range(F.VERSUCHE_MAX):
            with self.assertRaises(F.FreigabeFehler):
                d.freigeben(token=token(alter=500), anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
        sprung[0] = JETZT + 5 * F.VERSUCHE_FENSTER_S                              # die Systemuhr springt vor, die monotone Uhr nicht
        with self.assertRaises(F.FreigabeFehler) as m:
            d.freigeben(token=token(alter=-5 * F.VERSUCHE_FENSTER_S + 5), anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
        self.assertEqual(m.exception.code, "zu_viele")

    def test_speicher_der_drosselung_bleibt_begrenzt(self):
        t = [0.0]
        d = F.Freigabedienst(self.db, schluessel=schluessel, aussteller=ISS, zielgruppe=AUD, mono=lambda: t[0], uhr=lambda: JETZT)
        for i in range(1100):
            t[0] = float(i) * (F.VERSUCHE_FENSTER_S + 1)
            sub = f"{i:08x}-0000-4000-8000-000000000000"
            with self.assertRaises(F.FreigabeFehler):
                d.freigeben(token=token(sub=sub, alter=500), anfrage=ANFRAGE, commit_id=COMMIT, tree_id=TREE)
        self.assertLessEqual(len(d._versuche), 1025)


class AbbildungTests(unittest.TestCase):
    """Die Sätze der Trigger aus Migration 0013 müssen auf feste Codes abgebildet werden (ein Tippfehler im Stichwort fiele sonst
    still auf "regel")."""

    def test_saetze_der_migration_ergeben_die_erwarteten_codes(self):
        fall = [
            ("23514", "release_freigaben: Freigabe nur im Status wartet_freigabe (Anfrage ist review)", "falscher_status"),
            ("23514", "release_freigaben: Commit-ID oder Tree-ID passt nicht zur Anfrage", "fassung_passt_nicht"),
            ("42501", "release_freigaben: der Freigeber ist kein aktiver Admin", "keine_berechtigung"),
            ("23514", "release_freigaben: aal2_zeit liegt in der Zukunft", "aal2_zukunft"),
            ("23514", "release_freigaben: der zweite Faktor ist älter als 60 Sekunden", "aal2_alt"),
            ("42501", "release_freigaben: bei Art sonder darf der Beauftragte nicht selbst freigeben", "keine_berechtigung"),
            ("42501", "release_freigaben: Braucht einen zweiten Admin (Sonderfreigabe gesperrt)", "zweiter_admin"),
            ("23514", "release_freigaben: die Wartezeit der Sonderfreigabe ist noch nicht vorbei", "wartezeit"),
            ("23514", "release_freigaben: Freigabe nur mit gespeicherter Commit-ID und Tree-ID der Anfrage", "regel"),
            ("23505", "duplicate key value violates unique constraint release_freigaben_eine_je_person", "schon_freigegeben"),
            ("23503", "release_freigaben: Anfrage nicht gefunden", "anfrage_unbekannt"),
            ("XX000", "irgendwas", "regel"),
        ]
        sql = MIGRATION.read_text(encoding="utf-8")
        for sqlstate, satz, erwartet in fall:
            self.assertEqual(F.Freigabedienst._abbildung(F.DatenbankAbgelehnt(sqlstate, satz)).code, erwartet, satz)
            if satz.startswith("release_freigaben: ") and "(" not in satz and "nicht gefunden" not in satz:
                self.assertIn(satz.split(": ", 1)[1], sql, satz)


class MigrationTests(unittest.TestCase):
    """Die Regeln des Dienstes und der Attrappe dürfen von der Migration nicht abweichen."""

    def test_grenzen_stimmen_mit_dem_trigger_ueberein(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn(f"interval '{F.AAL2_MAX_ALTER_S} seconds'", sql)
        self.assertIn(f"interval '{F.AAL2_ZUKUNFT_S} seconds'", sql)
        self.assertIn("interval '60 minutes'", sql)
        self.assertIn("interval '24 hours'", sql)

    def test_saetze_der_trigger_stehen_in_migration_und_attrappe(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        code = Path(F.__file__).read_text(encoding="utf-8")
        for satz in ("Freigabe nur im Status wartet_freigabe", "Commit-ID oder Tree-ID passt nicht zur Anfrage", "kein aktiver Admin",
                     "aal2_zeit liegt in der Zukunft", "älter als 60 Sekunden", "darf der Beauftragte nicht selbst freigeben",
                     "Braucht einen zweiten Admin", "Wartezeit der Sonderfreigabe ist noch nicht vorbei"):
            self.assertIn(satz, sql, satz)
            self.assertIn(satz, code, satz)

    def test_modul_oeffnet_kein_netz_und_keine_datei(self):
        code = Path(F.__file__).read_text(encoding="utf-8")
        for verboten in ("import socket", "import subprocess", "import urllib", "import requests", "import httpx", "open(", "os.system"):
            self.assertNotIn(verboten, code, verboten)
        self.assertNotIn(chr(0x2014), code)
        self.assertNotIn(chr(0x2013), code)


if __name__ == "__main__":
    unittest.main()
