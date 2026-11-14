import Image from "next/image";

/**
 * WMC-Logo (WAMOCON, IT-Testmanagement) aus ui/public, rechts in der Kopfzeile wie in der WMC-Vorlage. Zwei Fassungen,
 * globals.css zeigt je Thema genau eine: im Hellmodus schwarz und rot auf weißer Platte (wmc-logo.png), im Dunkelmodus
 * weiß und rot ohne Platte (wmc-logo-dunkel.png, erzeugt mit scripts/wamo_bildmarken_erzeugen.py). Nur die helle Fassung
 * trägt den Alternativtext, die dunkle ist für Hilfstechnik verborgen. "gross" für die Anmeldeseite, sonst Kopfzeile.
 * Immer die große Datei: wmc-logo-klein.png ist im Branding-Paket oben knapp beschnitten.
 */
export function WmcLogo({ gross = false }: { gross?: boolean }) {
  const groesse = gross ? "120px" : "96px";
  const laden = gross ? "eager" : undefined;
  return (
    <span className="logo-plate">
      <Image
        className="logo-hell"
        src="/wmc-logo.png"
        alt="WAMOCON IT-Testmanagement"
        width={1615}
        height={634}
        sizes={groesse}
        loading={laden}
      />
      <Image
        className="logo-dunkel"
        src="/wmc-logo-dunkel.png"
        alt=""
        aria-hidden="true"
        width={1615}
        height={634}
        sizes={groesse}
        loading={laden}
      />
    </span>
  );
}
