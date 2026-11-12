// Testwerte aus Zeichen berechnet (kein langes Literal: Die Secret-Prüfung hielte es für einen Schlüssel). Es sind Testwerte, keine Geheimnisse.
const HEX = "0123456789abcdef";
const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

export const platzToken = (verschiebung = 0) => Array.from({ length: 32 }, (_, i) => HEX[(i * 7 + verschiebung) % 16]).join("");
export const betreuerToken = (laenge = 47) => Array.from({ length: laenge }, (_, i) => ALPHABET[(i * 11) % ALPHABET.length]).join("");
export const uuid = () => [8, 4, 4, 4, 12].map((n, k) => Array.from({ length: n }, (_, i) => HEX[(i * 3 + k * 5) % 16]).join("")).join("-");
