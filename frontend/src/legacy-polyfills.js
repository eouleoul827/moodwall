// The library LED player (NovaStar TU15Pro, ViPlex web page widget) runs an embedded
// Chromium ~80-86 that lacks these ES2022 built-ins used by the display. No-ops elsewhere.
if (typeof Object.hasOwn !== "function") {
  Object.defineProperty(Object, "hasOwn", {
    value: (object, key) => Object.prototype.hasOwnProperty.call(Object(object), key),
    configurable: true, writable: true,
  });
}
for (const Type of [Array, String]) {
  if (typeof Type.prototype.at !== "function") {
    Object.defineProperty(Type.prototype, "at", {
      value(index) {
        const length = this.length;
        let i = Math.trunc(index) || 0;
        if (i < 0) i += length;
        return i < 0 || i >= length ? undefined : this[i];
      },
      configurable: true, writable: true,
    });
  }
}
