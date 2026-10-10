// `crypto.randomUUID` is only exposed in a secure context (HTTPS or localhost).
// The managed testnet console is served over plain HTTP on a bare IP, so calling
// it directly throws `crypto.randomUUID is not a function` before the request is
// ever sent. `crypto.getRandomValues` has no secure-context requirement, so it is
// used as the fallback entropy source and only degrades to `Math.random` when the
// Web Crypto API is entirely absent.

const HEX = Array.from({ length: 256 }, (_, index) => index.toString(16).padStart(2, "0"));

function webCrypto(): Crypto | undefined {
  return typeof globalThis !== "undefined" ? (globalThis.crypto as Crypto | undefined) : undefined;
}

function randomBytes(length: number): Uint8Array {
  const bytes = new Uint8Array(length);
  const source = webCrypto();
  if (source && typeof source.getRandomValues === "function") {
    source.getRandomValues(bytes);
    return bytes;
  }
  for (let index = 0; index < length; index += 1) {
    bytes[index] = Math.floor(Math.random() * 256);
  }
  return bytes;
}

/** RFC 4122 version 4 UUID that also works outside a secure context. */
export function uuid(): string {
  const source = webCrypto();
  if (source && typeof source.randomUUID === "function") return source.randomUUID();
  const bytes = randomBytes(16);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => HEX[byte]);
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}
