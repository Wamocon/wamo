"use client";

import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { WmcLogo } from "@/components/WmcLogo";
import { getSupabase } from "@/lib/supabase";
import { unverschluesselteDienste } from "@/lib/umgebung";

/**
 * Zugang zu WAMO: E-Mail und Passwort, danach der Code aus der Authenticator-App (aal2).
 * Beim ersten Mal richtet jedes Profil den zweiten Faktor per QR-Code ein. Ob das Konto ein aktives
 * WAMO-Profil hat, prüft der Voice-Core mit demselben Token selbst (voice-core/auth_gate.py);
 * die Oberfläche ist nur die Tür.
 */

// Build-Konstanten: auf Server und Client gleich, also ohne Hydration-Unterschied nutzbar
const CONFIGURED = Boolean(
  process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
);
const DEV = process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_WAMO_VORSCHAU === "1";
const VERSION = process.env.WAMO_VERSION ?? "0.1.0";

/** Was die Hauptansicht von der Anmeldung bekommt. */
export type Sitzung = { signOut: () => void; email: string | null };

type Step =
  | { kind: "laedt" }
  | { kind: "fehlt" }
  | { kind: "anmelden" }
  | { kind: "einrichten"; factorId: string; qr: string; secret: string }
  | { kind: "code"; factorId: string }
  | { kind: "fertig"; email: string | null };

/**
 * Supabase liefert den QR-Code als rohe, unkodierte SVG-Daten-URL mit Zeilenumbruch am Ende.
 * next/image bricht daran ab ("cannot end with a space or control character"), also sauber
 * kodiert neu aufbauen.
 */
export function qrDataUrl(raw: string) {
  const svg = raw.slice(raw.indexOf(",") + 1).trim();
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function CodeField() {
  return (
    <label>
      Sechsstelliger Code
      <input
        name="code"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9 ]{6,7}"
        maxLength={7}
        required
        autoFocus
      />
    </label>
  );
}

export function LoginGate({
  children,
  vorschau = false,
}: {
  children: (sitzung: Sitzung) => ReactNode;
  /** Nur Entwicklungsvorschau: zeigt das Anmeldeformular ohne Supabase-Verbindung. */
  vorschau?: boolean;
}) {
  const [step, setStep] = useState<Step>(
    vorschau ? { kind: "anmelden" } : CONFIGURED ? { kind: "laedt" } : { kind: "fehlt" },
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Welcher Schritt ist dran? Ohne Sitzung anmelden, mit aal1 Code abfragen oder einrichten.
  const nextStep = useCallback(async () => {
    const supabase = getSupabase();
    if (!supabase) return setStep({ kind: "fehlt" });
    const { data: sessionData } = await supabase.auth.getSession();
    if (!sessionData.session) return setStep({ kind: "anmelden" });

    const { data: aal, error: aalError } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
    if (aalError) {
      setError(aalError.message);
      return setStep({ kind: "anmelden" });
    }
    if (aal.currentLevel === "aal2") {
      return setStep({ kind: "fertig", email: sessionData.session.user.email ?? null });
    }

    const { data: factors, error: listError } = await supabase.auth.mfa.listFactors();
    if (listError) {
      setError(listError.message);
      return setStep({ kind: "anmelden" });
    }
    const verified = factors.totp[0];
    if (verified) return setStep({ kind: "code", factorId: verified.id });

    // Ersteinrichtung: liegengebliebene, nie bestätigte Faktoren zuerst entfernen
    for (const f of factors.all) {
      if (f.factor_type === "totp" && f.status === "unverified") {
        await supabase.auth.mfa.unenroll({ factorId: f.id });
      }
    }
    const { data: enrolled, error: enrollError } = await supabase.auth.mfa.enroll({
      factorType: "totp",
      issuer: "WAMO",
      friendlyName: "WAMO",
    });
    if (enrollError) {
      setError(enrollError.message);
      return setStep({ kind: "anmelden" });
    }
    setStep({
      kind: "einrichten",
      factorId: enrolled.id,
      qr: qrDataUrl(enrolled.totp.qr_code),
      secret: enrolled.totp.secret,
    });
  }, []);

  useEffect(() => {
    if (vorschau) return;
    const supabase = getSupabase();
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange((event) => {
      if (event === "SIGNED_OUT") setStep({ kind: "anmelden" });
      // Weitere Supabase-Aufrufe erst nach dem Rückruf, sonst blockiert sich supabase-js selbst
      if (event === "INITIAL_SESSION") {
        setTimeout(() => {
          nextStep().catch(() => {
            setError("Anmeldeserver nicht erreichbar. Bist du im Büronetz oder per WireGuard verbunden?");
            setStep({ kind: "anmelden" });
          });
        }, 0);
      }
    });
    return () => data.subscription.unsubscribe();
  }, [nextStep, vorschau]);

  const run = async (action: () => Promise<string | null>) => {
    setBusy(true);
    setError(null);
    try {
      const problem = await action();
      if (problem) setError(problem);
      else await nextStep();
    } catch {
      // z. B. Anmeldeserver nicht erreichbar (Netz, WireGuard aus)
      setError("Anmeldeserver nicht erreichbar. Bist du im Büronetz oder per WireGuard verbunden?");
    } finally {
      setBusy(false);
    }
  };

  const signIn = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const supabase = vorschau ? null : getSupabase();
    if (!supabase) {
      setError(vorschau ? "Vorschau ohne Verbindung: hier ist keine Anmeldung möglich." : "Anmeldung nicht eingerichtet.");
      return;
    }
    void run(async () => {
      const { error } = await supabase.auth.signInWithPassword({
        email: String(form.get("email")).trim(),
        password: String(form.get("password")),
      });
      if (!error) return null;
      return error.status === 400 ? "E-Mail oder Passwort stimmt nicht." : error.message;
    });
  };

  const verify = (factorId: string) => (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const code = String(new FormData(e.currentTarget).get("code")).replace(/\s/g, "");
    const supabase = getSupabase();
    if (!supabase) return;
    void run(async () => {
      const { error } = await supabase.auth.mfa.challengeAndVerify({ factorId, code });
      return error ? "Der Code passt nicht. Nimm den aktuellen aus der App." : null;
    });
  };

  // Lokal abmelden: Sitzung in diesem Browser beenden, auch wenn der Server gerade nicht erreichbar ist
  const signOut = useCallback(() => {
    const supabase = getSupabase();
    if (!supabase) return;
    supabase.auth.signOut({ scope: "local" }).catch(() => setStep({ kind: "anmelden" }));
  }, []);

  if (step.kind === "fertig") return <>{children({ signOut, email: step.email })}</>;

  return (
    <div className="gate">
      <header className="brandbar">
        <p>WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn</p>
        <WmcLogo gross />
      </header>
      <main className="gate-main">
        <section className="gate-card" aria-labelledby="gate-title">
          <header className="gate-head">
            <h1 className="gate-title" id="gate-title">
              WAMO
            </h1>
            <p className="gate-sub">Sprachassistent der WAMOCON GmbH</p>
          </header>

          {step.kind === "laedt" && <p className="gate-hint">Einen Moment …</p>}

          {step.kind === "fehlt" && (
            <>
              <p className="notice">
                Anmeldung nicht eingerichtet: NEXT_PUBLIC_SUPABASE_URL und
                NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY fehlen. Bitte in ui/.env.local eintragen.
              </p>
              {DEV && (
                <p className="gate-dev">
                  Nur Entwicklung: <Link href="/?vorschau=1">Vorschau der Hauptansicht</Link> ·{" "}
                  <Link href="/?vorschau=anmeldung">Vorschau der Anmeldung</Link>
                </p>
              )}
            </>
          )}

          {step.kind === "anmelden" && (
            <form className="gate-form" onSubmit={signIn}>
              <p className="gate-hint">
                Melde dich mit deinem WAMO-Profil an. Danach fragt WAMO den Code aus deiner
                Authenticator-App ab.
              </p>
              <label>
                E-Mail
                <input name="email" type="email" autoComplete="username" required autoFocus />
              </label>
              <label>
                Passwort
                <input name="password" type="password" autoComplete="current-password" required />
              </label>
              <button className="btn-primary btn-block" disabled={busy}>
                {busy ? "Prüfe …" : "Anmelden"}
              </button>
            </form>
          )}

          {step.kind === "einrichten" && (
            <form className="gate-form" onSubmit={verify(step.factorId)}>
              <p className="gate-hint">
                Einmalig: Scanne den QR-Code mit deiner Authenticator-App (z. B. Google
                Authenticator, Microsoft Authenticator, 1Password) und gib die sechs Ziffern ein.
              </p>
              <Image
                className="gate-qr"
                src={step.qr}
                alt="QR-Code für die Authenticator-App"
                width={200}
                height={200}
                unoptimized
              />
              <details className="gate-secret">
                <summary>QR-Code lässt sich nicht scannen?</summary>
                <code>{step.secret}</code>
              </details>
              <CodeField />
              <button className="btn-primary btn-block" disabled={busy}>
                {busy ? "Prüfe …" : "Bestätigen"}
              </button>
            </form>
          )}

          {step.kind === "code" && (
            <form className="gate-form" onSubmit={verify(step.factorId)}>
              <p className="gate-hint">Gib den aktuellen Code aus deiner Authenticator-App ein.</p>
              <CodeField />
              <button className="btn-primary btn-block" disabled={busy}>
                {busy ? "Prüfe …" : "Weiter"}
              </button>
            </form>
          )}

          {error && (
            <p className="notice" role="alert">
              {error}
            </p>
          )}

          {(step.kind === "einrichten" || step.kind === "code") && (
            <button type="button" className="gate-link" onClick={signOut}>
              Abmelden
            </button>
          )}
        </section>
      </main>
      {unverschluesselteDienste().length > 0 && (
        <p className="gate-unsicher" role="note">
          Hinweis: unverschlüsselt im LAN (
          {unverschluesselteDienste()
            .map((d) => d.name)
            .join(", ")}
          ). Nur im Büronetz oder über WireGuard nutzen.
        </p>
      )}
      <footer className="gate-foot">
        <span className="num">WAMO Version {VERSION}</span>
        <span>Zugang nur mit aktivem WAMO-Profil und zweitem Faktor</span>
      </footer>
    </div>
  );
}
