import Image from "next/image";

/**
 * WMC-Logo (WAMOCON, IT-Testmanagement) aus ui/public, rechts in der Kopfzeile wie in der
 * WMC-Vorlage. Das Logo ist schwarz auf transparent: im dunklen Theme steht es auf einer weißen
 * Platte (--logo-plate). "gross" für die Anmeldeseite (Höhe 45 px), sonst Kopfzeile (34 px).
 * Immer die große Datei: wmc-logo-klein.png ist im Branding-Paket oben knapp beschnitten.
 */
export function WmcLogo({ gross = false }: { gross?: boolean }) {
  return (
    <span className="logo-plate">
      <Image
        src="/wmc-logo.png"
        alt="WAMOCON IT-Testmanagement"
        width={1615}
        height={634}
        sizes={gross ? "120px" : "96px"}
        loading={gross ? "eager" : undefined}
      />
    </span>
  );
}
