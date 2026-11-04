/** UUID v4 for HTTPS and local HTTP development; always uses a secure RNG. */
export function secureUuid(source = globalThis.crypto) {
  if (typeof source?.randomUUID === "function") return source.randomUUID();
  // getRandomValues remains available on browsers' insecure HTTP contexts,
  // where randomUUID is absent. Never substitute Math.random for identity keys.
  if (typeof source?.getRandomValues !== "function") {
    throw Object.assign(new Error("Secure random number generation is unavailable"), {
      code: "SECURE_RANDOM_UNAVAILABLE",
    });
  }
  const bytes = new Uint8Array(16);
  source.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
